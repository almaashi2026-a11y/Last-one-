"""
telegram_alerts.py
Sends Telegram alerts when an opportunity meets the entry criteria.
"""
import requests
import logging
from datetime import datetime
import config

logger = logging.getLogger("telegram_alerts")


def send_telegram_message(text: str) -> bool:
    """Sends a text message to Telegram. Returns True if sent successfully."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram bot token or chat id missing, skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        logger.error(f"Telegram send failed [{resp.status_code}]: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return False


def format_alert(candidate: dict) -> str:
    """Builds the alert text from candidate data."""
    symbol = candidate.get("symbol", "N/A")
    price = candidate.get("price", 0)
    score = candidate.get("accumulation_score", 0)
    rvol = candidate.get("rvol", 0)
    vwap_dev = candidate.get("vwap_deviation_pct", 0)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"🚨 <b>Institutional Accumulation Signal</b> 🚨\n\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Price:</b> ${price:.4f}\n"
        f"<b>Accumulation Score:</b> {score:.1f}/100\n"
        f"<b>RVOL:</b> {rvol:.2f}x\n"
        f"<b>VWAP Deviation:</b> {vwap_dev:+.2f}%\n"
        f"<b>Time:</b> {now}\n\n"
        f"⚠️ This is an automated alert only. Confirm with your own analysis before any entry decision."
    )


def send_candidate_alert(candidate: dict) -> bool:
    text = format_alert(candidate)
    return send_telegram_message(text)


def send_startup_message():
    send_telegram_message("✅ Scanner started successfully on Render and is ready to detect opportunities.")


def send_error_alert(error_text: str):
    send_telegram_message(f"⚠️ <b>Scanner Error:</b>\n{error_text}")
