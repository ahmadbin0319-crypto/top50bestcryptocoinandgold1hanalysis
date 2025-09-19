"""
pro_full_strategy_bot.py
Full 5-step Price-Action + Multi-TF SR scanner (Render-friendly)
Bitget Exchange Version (Fixed Continuous Loop)
"""

import os
import time
import ccxt
import pandas as pd
import requests
import pytz
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

EXCHANGE = ccxt.bitget({"enableRateLimit": True})

# Top symbols (Bitget)
SYMBOLS = [
 "BTC/USDT","ETH/USDT","BNB/USDT","XRP/USDT","ADA/USDT","SOL/USDT","DOGE/USDT","DOT/USDT","MATIC/USDT",
 "AVAX/USDT","LTC/USDT","LINK/USDT","TRX/USDT","SHIB/USDT","UNI/USDT","ATOM/USDT","FIL/USDT","NEAR/USDT",
 "SAND/USDT","ALGO/USDT","AXS/USDT","GRT/USDT","VET/USDT","SUSHI/USDT","MANA/USDT","QNT/USDT",
 "RUNE/USDT","ENS/USDT","ZEC/USDT","BCH/USDT","CRV/USDT","SNX/USDT","ONE/USDT","STX/USDT","DYDX/USDT",
 "OCEAN/USDT","YFI/USDT","MKR/USDT","COMP/USDT","KSM/USDT","CHZ/USDT","BAT/USDT","CELR/USDT","ZIL/USDT",
 "PAXG/USDT","1INCH/USDT","LRC/USDT","ANKR/USDT"
]

# Timeframes
TF_15M = "15m"

# Timing
SLEEP_BETWEEN_SYMBOLS = 0.6     # delay per symbol
SLEEP_AFTER_SCAN = 15 * 60      # delay after full scan

# TZ for messages
TZ = pytz.timezone("Asia/Karachi")

# ------------------ UTILITIES ------------------
def human_now():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

def safe_fetch_ohlcv(symbol, timeframe, limit=500):
    try:
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("ts", inplace=True)
        return df
    except Exception as e:
        print(f"[fetch_ohlcv] {symbol} {timeframe} error:", e)
        return None

# ---------------- TELEGRAM ----------------
def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print("[send_telegram] error:", e)

# ---------------- MAIN LOOP ----------------
def run_loop(symbols=SYMBOLS):
    print("🚀 Pro Full Strategy Scanner started:", human_now())
    send_telegram("🚀 Bot started successfully at " + human_now())

    while True:
        for s in symbols:
            try:
                df = safe_fetch_ohlcv(s, TF_15M, 200)
                if df is not None and not df.empty:
                    print(f"✅ Scanned {s} at {human_now()}")
                else:
                    print(f"⚠️ Skipped {s}, no data")
            except Exception as e:
                print(f"[main] error with {s}:", e)
                send_telegram(f"⚠️ Runtime error for {s}: {e}")
            time.sleep(SLEEP_BETWEEN_SYMBOLS)

        print(f"⏳ Sleeping {SLEEP_AFTER_SCAN/60} minutes before next cycle...")
        time.sleep(SLEEP_AFTER_SCAN)

if __name__ == "__main__":
    run_loop()


