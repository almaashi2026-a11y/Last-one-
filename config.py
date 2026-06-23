"""
config.py
All settings are read from Environment Variables (set them from the Render dashboard,
never write API keys directly here).
"""
import os

# ===== API Keys =====
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ===== Stage 1 filters (Yahoo - broad filtering) =====
PRICE_MIN = float(os.environ.get("PRICE_MIN", "0.20"))
PRICE_MAX = float(os.environ.get("PRICE_MAX", "10.0"))
STAGE1_MIN_RVOL = float(os.environ.get("STAGE1_MIN_RVOL", "1.5"))
STAGE1_MAX_CANDIDATES = int(os.environ.get("STAGE1_MAX_CANDIDATES", "45"))

# ===== Stage 2 filters (Finnhub - deep analysis) =====
STAGE2_MIN_RVOL = float(os.environ.get("STAGE2_MIN_RVOL", "2.0"))
VWAP_LOWER_BOUND_PCT = float(os.environ.get("VWAP_LOWER_BOUND_PCT", "-3.0"))   # -3%
VWAP_UPPER_BOUND_PCT = float(os.environ.get("VWAP_UPPER_BOUND_PCT", "0.5"))    # +0.5%
MIN_ACCUMULATION_SCORE = float(os.environ.get("MIN_ACCUMULATION_SCORE", "60"))

# ===== Scan cycle =====
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))  # every 5 minutes
FINNHUB_RATE_LIMIT_PER_MIN = int(os.environ.get("FINNHUB_RATE_LIMIT_PER_MIN", "55"))  # safety margin under 60

# ===== Telegram alert threshold =====
ALERT_MIN_SCORE = float(os.environ.get("ALERT_MIN_SCORE", "60"))

# ===== Quick check that core keys exist =====
def validate_config():
    missing = []
    if not FINNHUB_API_KEY:
        missing.append(
