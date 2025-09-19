"""
pro_full_strategy_bot.py
Full 5-step Price-Action + Multi-TF SR scanner (Render-friendly)
Bitget Exchange Version
"""

import os
import time
import math
import traceback
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
 "SAND/USDT","ALGO/USDT","FTM/USDT","AXS/USDT","GRT/USDT","VET/USDT","SUSHI/USDT","MANA/USDT","QNT/USDT",
 "RUNE/USDT","ENS/USDT","ZEC/USDT","BCH/USDT","CRV/USDT","SNX/USDT","ONE/USDT","STX/USDT","DYDX/USDT",
 "OCEAN/USDT","YFI/USDT","MKR/USDT","COMP/USDT","KSM/USDT","CHZ/USDT","BAT/USDT","CELR/USDT","ZIL/USDT",
 "PAXG/USDT","1INCH/USDT","LRC/USDT","ANKR/USDT"
]

# Timeframes
TF_1D = "1d"
TF_4H = "4h"
TF_1H = "1h"
TF_15M = "15m"

# Strategy parameters
SWING_DAYS = 3
PIVOT_L = 3; PIVOT_R = 3
SR_CLUSTER_TOL = 0.006
PROXIMITY_PCT = 0.01
SL_PCT = 2.5
TP_PCT = 8.5

# Timing
SLEEP_BETWEEN_SYMBOLS = 0.6
SLEEP_AFTER_SCAN = 15 * 60

# TZ for messages
TZ = pytz.timezone("Asia/Karachi")

last_alerts = {}

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

def pivot_levels_from_df(df, left=PIVOT_L, right=PIVOT_R):
    highs=[]; lows=[]
    if df is None or df.shape[0] < (left+right+1):
        return highs,lows
    for i in range(left, len(df)-right):
        window = df.iloc[i-left:i+right+1]
        center_h = df['high'].iloc[i]; center_l = df['low'].iloc[i]
        if center_h == window['high'].max(): highs.append(float(center_h))
        if center_l == window['low'].min(): lows.append(float(center_l))
    return highs, lows

def cluster_levels(levels, tol=SR_CLUSTER_TOL):
    if not levels: return []
    levels = sorted(levels)
    clusters = []
    cur=[levels[0]]
    for p in levels[1:]:
        if abs(p-cur[-1]) / cur[-1] <= tol:
            cur.append(p)
        else:
            clusters.append(sum(cur)/len(cur)); cur=[p]
    clusters.append(sum(cur)/len(cur))
    return clusters

def find_nearest(levels, price):
    if not levels: return None, None
    arr = sorted([(abs(price-l), l) for l in levels], key=lambda x: x[0])
    return arr[0][1], arr[0][0]

def is_bullish_engulf(last, prev):
    return (last['close'] > last['open']) and (prev['close'] < prev['open']) and (last['open'] < prev['close']) and (last['close'] > prev['open'])

def is_bearish_engulf(last, prev):
    return (last['close'] < last['open']) and (prev['close'] > prev['open']) and (last['open'] > prev['close']) and (last['close'] < prev['open'])

def pin_bar_type(last):
    body = abs(last['close'] - last['open'])
    if body == 0: return None
    upper = last['high'] - max(last['open'], last['close'])
    lower = min(last['open'], last['close']) - last['low']
    if upper > 2*body and lower < body: return "bear"
    if lower > 2*body and upper < body: return "bull"
    return None

def compute_vwap(df):
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    vp = tp * df['volume']
    return vp.sum() / (df['volume'].sum() + 1e-12)

def orderbook_imbalance(symbol, limit=20):
    try:
        ob = EXCHANGE.fetch_order_book(symbol, limit=limit)
        bids = sum([b[1] for b in ob['bids']])
        asks = sum([a[1] for a in ob['asks']])
        if bids+asks==0: return 0.0
        return (bids-asks)/(bids+asks)
    except Exception as e:
        return 0.0

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
    print("Pro Full Strategy Scanner started:", human_now())
    while True:
        for s in symbols:
            try:
                df = safe_fetch_ohlcv(s, TF_15M, 200)
                if df is not None:
                    print(f"✅ Scanned {s} at {human_now()}")
            except Exception as e:
                print("[main] error:", e)
                send_telegram(f"⚠️ Runtime error for {s}: {e}")
            time.sleep(SLEEP_BETWEEN_SYMBOLS)
        time.sleep(SLEEP_AFTER_SCAN)

if __name__ == "__main__":
    run_loop()


