"""
app.py
Main dashboard (Streamlit). Runs the scanner in a background thread continuously,
displays results live, and sends Telegram alerts when entry signals are found.
"""
import threading
import time
import logging
from datetime import datetime, timezone

import streamlit as st
import pandas as pd

import config
import scanner_engine
import telegram_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("app")

st.set_page_config(
    page_title="NASDAQ Penny Stock Scanner",
    page_icon="📈",
    layout="wide",
)


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.results = []
        self.entry_signals = []
        self.stats = {}
        self.error = None
        self.is_running = False
        self.alerted_symbols = set()

    def update(self, scan_output: dict):
        with self.lock:
            self.error = scan_output.get("error")
            self.results = scan_output.get("results", [])
            self.entry_signals = scan_output.get("entry_signals", [])
            self.stats = scan_output.get("stats", {})

    def get_snapshot(self):
        with self.lock:
            return {
                "error": self.error,
                "results": list(self.results),
                "entry_signals": list(self.entry_signals),
                "stats": dict(self.stats),
            }


@st.cache_resource
def get_shared_state():
    return SharedState()


def background_scan_loop(state: SharedState):
    """Runs in an infinite loop on a separate thread: scan -> update state -> alert -> sleep -> repeat."""
    state.is_running = True
    telegram_alerts.send_startup_message()

    while True:
        try:
            scan_output = scanner_engine.run_full_scan()
            state.update(scan_output)

            if scan_output.get("error"):
                telegram_alerts.send_error_alert(scan_output["error"])
            else:
                current_alert_symbols = set()
                for candidate in scan_output.get("entry_signals", []):
                    symbol = candidate["symbol"]
                    current_alert_symbols.add(symbol)
                    if symbol not in state.alerted_symbols:
                        telegram_alerts.send_candidate_alert(candidate)
                        state.alerted_symbols.add(symbol)
                state.alerted_symbols = state.alerted_symbols.intersection(current_alert_symbols)

        except Exception as e:
            logger.exception("Unexpected error in scan loop")
            telegram_alerts.send_error_alert(f"Unexpected error: {e}")

        time.sleep(config.SCAN_INTERVAL_SECONDS)


@st.cache_resource
def start_background_thread(_state: SharedState):
    """Starts the scan thread only once (cache_resource ensures no duplication across reruns)."""
    thread = threading.Thread(target=background_scan_loop, args=(_state,), daemon=True)
    thread.start()
    return thread


shared_state = get_shared_state()
start_background_thread(shared_state)

missing_keys = config.validate_config()

st.title("📈 NASDAQ Penny Stock Scanner")
st.caption("Detecting institutional accumulation signals before VWAP breakout — continuous auto-update")

if missing_keys:
    st.error(
        f"⚠️ Missing environment variables: {', '.join(missing_keys)}. "
        "Add them from Render → Environment, then redeploy the service."
    )

snapshot = shared_state.get_snapshot()

if snapshot["error"]:
    st.error(f"Error in last scan cycle: {snapshot['error']}")

stats = snapshot["stats"]
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Universe", stats.get("total_universe", "—"))
col2.metric("Stage 1 Candidates", stats.get("stage1_candidates", "—"))
col3.metric("Stage 2 Analyzed", stats.get("stage2_analyzed", "—"))
col4.metric("Entry Signals", stats.get("entry_signals", "—"))
col5.metric("Last Scan Duration (s)", stats.get("scan_duration_sec", "—"))

last_scan = stats.get("last_scan_time")
if last_scan:
    try:
        dt = datetime.fromisoformat(last_scan)
        st.caption(f"Last update: {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    except Exception:
        pass

st.divider()

st.subheader("🎯 Active Entry Signals")
entry_signals = snapshot["entry_signals"]

if entry_signals:
    df_entries = pd.DataFrame(entry_signals)
    df_entries = df_entries[[
        "symbol", "price", "accumulation_score", "rvol", "vwap", "vwap_deviation_pct"
    ]]
    df_entries.columns = ["Symbol", "Price", "Accumulation Score", "RVOL", "VWAP", "VWAP Deviation %"]
    st.dataframe(df_entries, use_container_width=True, hide_index=True)
else:
    st.info("No entry signals matching the criteria right now. The scanner is running in the background and will update automatically.")

st.divider()

st.subheader("📊 All Analyzed Candidates (Stage 2)")
all_results = snapshot["results"]

if all_results:
    df_all = pd.DataFrame(all_results)
    df_all = df_all[[
        "symbol", "price", "accumulation_score", "rvol", "vwap",
        "vwap_deviation_pct", "meets_entry_criteria"
    ]]
    df_all.columns = [
        "Symbol", "Price", "Accumulation Score", "RVOL", "VWAP",
        "VWAP Deviation %", "Meets Entry Criteria"
    ]
    st.dataframe(df_all, use_container_width=True, hide_index=True)
else:
    st.info("Running first scan cycle... results may take a minute to appear.")

st.divider()
st.caption(
    f"⚙️ Scan cycle every {config.SCAN_INTERVAL_SECONDS}s | "
    f"Entry criteria: Score > {config.MIN_ACCUMULATION_SCORE} | "
    f"RVOL > {config.STAGE2_MIN_RVOL}x | "
    f"VWAP between {config.VWAP_LOWER_BOUND_PCT}% and {config.VWAP_UPPER_BOUND_PCT}%"
)

time.sleep(30)
st.rerun()
