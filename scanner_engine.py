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
        for line in lines[1:-1]:  # skip header and footer lines
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
