"""
scanner_engine.py
Core engine: Stage 1 broad filtering via Yahoo Finance (unofficial), Stage 2 deep analysis via Finnhub.
"""
import time
import logging
import requests
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config

logger = logging.getLogger("scanner_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ===================== Rate limiter for Finnhub =====================
class RateLimiter:
    """Ensures we never exceed the per-minute request limit (Finnhub free tier = 60/min)."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.lock = threading.Lock()
        self.timestamps = []

    def wait_if_needed(self):
        with self.lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            if len(self.timestamps) >= self.max_per_minute:
                sleep_time = 60 - (now - self.timestamps[0]) + 0.1
                if sleep_time > 0:
                    time.sleep(sleep_time)
                now = time.time()
                self.timestamps = [t for t in self.timestamps if now - t < 60]
            self.timestamps.append(time.time())


finnhub_limiter = RateLimiter(config.FINNHUB_RATE_LIMIT_PER_MIN)


# ===================== Stage 1: Yahoo Finance (broad filtering) =====================
def get_nasdaq_symbol_universe() -> list:
    """
    Fetches the NASDAQ symbol list from the official nasdaqlisted.txt file
    (updated daily by nasdaqtrader.com). More stable than manual lists.
    """
    url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        symbols = []
        for line in lines[1:-1]:
            parts = line.split("|")
            if len(parts) > 1:
                symbol = parts[0].strip()
                test_issue = parts[3].strip() if len(parts) > 3 else "N"
                if symbol and test_issue != "Y" and "." not in symbol and "$" not in symbol:
                    symbols.append(symbol)
        logger.info(f"Loaded {len(symbols)} NASDAQ symbols.")
        return symbols
    except Exception as e:
        logger.error(f"Failed to load symbol list: {e}")
        return []


def fetch_yahoo_quote(symbol: str) -> dict:
    """Fetches quick data from the Yahoo Finance unofficial endpoint."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": "1d", "range": "5d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        result = result[0]
        meta = result.get("meta", {})
        quote = result.get("indicators", {}).get("quote", [{}])[0]

        volumes = [v for v in quote.get("volume", []) if v is not None]
        closes = [c for c in quote.get("close", []) if c is not None]
        if not volumes or not closes:
            return None

        current_price = meta.get("regularMarketPrice")
        current_volume = volumes[-1] if volumes else 0
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else current_volume

        if not current_price or avg_volume == 0:
            return None

        rvol = current_volume / avg_volume if avg_volume > 0 else 0

        return {
            "symbol": symbol,
            "price": current_price,
            "rvol_approx": rvol,
            "avg_volume": avg_volume,
        }
    except Exception:
        return None


def stage1_filter(symbols: list) -> list:
    """Broad fast filtering: price within range + approximate RVOL above minimum."""
    candidates = []
    logger.info(f"Starting Stage 1: scanning {len(symbols)} symbols via Yahoo Finance...")

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_yahoo_quote, sym): sym for sym in symbols}
        for future in as_completed(futures):
            data = future.result()
            if data is None:
                continue
            if config.PRICE_MIN <= data["price"] <= config.PRICE_MAX:
                if data["rvol_approx"] >= config.STAGE1_MIN_RVOL:
                    candidates.append(data)

    candidates.sort(key=lambda x: x["rvol_approx"], reverse=True)
    top_candidates = candidates[: config.STAGE1_MAX_CANDIDATES]
    logger.info(f"Stage 1 done: {len(candidates)} matched filter, took top {len(top_candidates)}.")
    return top_candidates


# ===================== Stage 2: Finnhub (deep analysis) =====================
def fetch_finnhub_candles(symbol: str, resolution: str = "5", count: int = 78) -> dict:
    """Fetches 5-minute candles from Finnhub (78 candles approx one full trading day)."""
    finnhub_limiter.wait_if_needed()
    url = "https://finnhub.io/api/v1/stock/candle"
    now_ts = int(time.time())
    from_ts = now_ts - (count * int(resolution) * 60)
    params = {
        "symbol": symbol,
        "resolution": resolution,
        "from": from_ts,
        "to": now_ts,
        "token": config.FINNHUB_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("s") != "ok":
            return None
        return data
    except Exception as e:
        logger.warning(f"Finnhub candle fetch failed for {symbol}: {e}")
        return None


def calculate_vwap(highs, lows, closes, volumes) -> float:
    """Calculates cumulative session VWAP."""
    cum_pv = 0.0
    cum_vol = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        typical_price = (h + l + c) / 3
        cum_pv += typical_price * v
        cum_vol += v
    if cum_vol == 0:
        return closes[-1] if closes else 0
    return cum_pv / cum_vol


def calculate_accumulation_score(closes, volumes) -> float:
    """
    Calculates an institutional accumulation score 0-100 based on:
    - Ratio of up candles (40 points)
    - Volume skew towards up candles vs down candles (40 points)
    - Tightness of trading range = consolidation (20 points)
    """
    if len(closes) < 10 or len(volumes) < 10:
        return 0.0

    n = len(closes)
    up_candles = 0
    up_volume = 0.0
    down_volume = 0.0

    for i in range(1, n):
        if closes[i] >= closes[i - 1]:
            up_candles += 1
            up_volume += volumes[i]
        else:
            down_volume += volumes[i]

    up_ratio = up_candles / (n - 1) if n > 1 else 0
    score_breadth = up_ratio * 40

    total_vol = up_volume + down_volume
    vol_skew = (up_volume / total_vol) if total_vol > 0 else 0.5
    score_volume = vol_skew * 40

    price_range = max(closes) - min(closes)
    avg_price = sum(closes) / len(closes)
    range_pct = (price_range / avg_price) * 100 if avg_price > 0 else 100
    tightness_score = max(0, 20 - (range_pct / 15 * 20))

    total_score = score_breadth + score_volume + tightness_score
    return round(min(100.0, max(0.0, total_score)), 2)


def analyze_candidate(candidate: dict) -> dict:
    """Deep analysis of a single candidate via Finnhub data: VWAP, precise RVOL, accumulation score."""
    symbol = candidate["symbol"]
    data = fetch_finnhub_candles(symbol)
    if not data:
        return None

    closes = data.get("c", [])
    highs = data.get("h", [])
    lows = data.get("l", [])
    volumes = data.get("v", [])

    if len(closes) < 10:
        return None

    current_price = closes[-1]
    vwap = calculate_vwap(highs, lows, closes, volumes)
    vwap_deviation_pct = ((current_price - vwap) / vwap) * 100 if vwap > 0 else 0

    current_volume = volumes[-1] if volumes else 0
    avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else current_volume
    rvol = current_volume / avg_volume if avg_volume > 0 else 0

    accumulation_score = calculate_accumulation_score(closes, volumes)

    meets_entry_criteria = (
        accumulation_score > config.MIN_ACCUMULATION_SCORE
        and rvol > config.STAGE2_MIN_RVOL
        and config.VWAP_LOWER_BOUND_PCT <= vwap_deviation_pct <= config.VWAP_UPPER_BOUND_PCT
    )

    return {
        "symbol": symbol,
        "price": current_price,
        "vwap": round(vwap, 4),
        "vwap_deviation_pct": round(vwap_deviation_pct, 2),
        "rvol": round(rvol, 2),
        "accumulation_score": accumulation_score,
        "meets_entry_criteria": meets_entry_criteria,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def stage2_analysis(candidates: list) -> list:
    """Parallel deep analysis of all Stage 1 candidates via Finnhub, respecting the rate limit."""
    logger.info(f"Starting Stage 2: deep analysis of {len(candidates)} candidates via Finnhub...")
    results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_candidate, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    results.sort(key=lambda x: x["accumulation_score"], reverse=True)
    logger.info(f"Stage 2 done: {len(results)} results analyzed successfully.")
    return results


# ===================== Full scan cycle =====================
def run_full_scan() -> dict:
    """Runs a full scan cycle: load universe -> Stage1 -> Stage2. Returns dict with results and stats."""
    scan_start = time.time()
    symbols = get_nasdaq_symbol_universe()
    if not symbols:
        return {"error": "Failed to load NASDAQ symbol list", "results": [], "stats": {}}

    stage1_candidates = stage1_filter(symbols)
    stage2_results = stage2_analysis(stage1_candidates) if stage1_candidates else []

    scan_duration = round(time.time() - scan_start, 1)
    entry_signals = [r for r in stage2_results if r["meets_entry_criteria"]]

    stats = {
        "total_universe": len(symbols),
        "stage1_candidates": len(stage1_candidates),
        "stage2_analyzed": len(stage2_results),
        "entry_signals": len(entry_signals),
        "scan_duration_sec": scan_duration,
        "last_scan_time": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Full scan cycle done in {scan_duration}s. Entry signals: {len(entry_signals)}")

    return {"error": None, "results": stage2_results, "entry_signals": entry_signals, "stats": stats}
