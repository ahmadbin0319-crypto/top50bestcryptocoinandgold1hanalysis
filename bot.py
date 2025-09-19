"""
pro_full_strategy_bot.py
Full 5-step Price-Action + Multi-TF SR scanner (Render-friendly, Bitget)

Usage:
 - Set TELEGRAM_TOKEN and CHAT_ID as environment variables (recommended).
 - Deploy to Render as a worker: `worker: python pro_full_strategy_bot.py`
"""

import os
import time
import math
import traceback
import ccxt
import pandas as pd
import requests
import pytz
from datetime import datetime

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# Use Bitget instead of Binance
EXCHANGE = ccxt.bitget({"enableRateLimit": True, "options": {"adjustForTimeDifference": True}})

# Top symbols (start with BTC) — extend up to ~50 as wanted
SYMBOLS = [
 "BTC/USDT","ETH/USDT","BNB/USDT","XRP/USDT","ADA/USDT","SOL/USDT","DOGE/USDT","DOT/USDT","MATIC/USDT",
 "AVAX/USDT","LTC/USDT","LINK/USDT","TRX/USDT","SHIB/USDT","UNI/USDT","ATOM/USDT","FIL/USDT","NEAR/USDT",
 "SAND/USDT","ALGO/USDT","FTM/USDT","AXS/USDT","GRT/USDT","VET/USDT","SUSHI/USDT","MANA/USDT","QNT/USDT",
 "RUNE/USDT","ENS/USDT","ZEC/USDT","BCH/USDT","CRV/USDT","SNX/USDT","ONE/USDT","STX/USDT","DYDX/USDT",
 "OCEAN/USDT","YFI/USDT","MKR/USDT","COMP/USDT","KSM/USDT","CHZ/USDT","BAT/USDT","CELR/USDT","ZIL/USDT",
 "PAXG/USDT","1INCH/USDT","LRC/USDT","ANKR/USDT"
]
# NOTE: Some exchanges (including Bitget) might not list XAU/USDT; skip if not available.
SYMBOLS.append("XAU/USDT")

# Timeframes
TF_1D = "1d"
TF_4H = "4h"
TF_1H = "1h"
TF_15M = "15m"

# Strategy parameters
SWING_DAYS = 3               # 2-3 day swings used
PIVOT_L = 3; PIVOT_R = 3     # pivot detection window
SR_CLUSTER_TOL = 0.006       # 0.6% cluster tolerance for SR
PROXIMITY_PCT = 0.01         # 1% proximity to SR to consider
SL_PCT = 2.5
TP_PCT = 8.5

# Timing
SLEEP_BETWEEN_SYMBOLS = 0.6
SLEEP_AFTER_SCAN = 15 * 60   # 15 minutes full-scans

# TZ for messages
TZ = pytz.timezone("Asia/Karachi")

# dedupe alerts
last_alerts = {}

# external indicator placeholders
USE_EXTERNAL = False
def fetch_external_indicator_signals(symbol):
    # optionally implement TradingView webhook file or API to read LuxAlgo/ChartPrime signals
    return {"lux": None, "chartprime": None}

# Try to load markets once (safe)
try:
    EXCHANGE.load_markets()
except Exception as e:
    print("[exchange] load_markets warning:", e)

# ------------------ UTILITIES ------------------
def human_now():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

def safe_fetch_ohlcv(symbol, timeframe, limit=500):
    """
    Use ccxt fetch_ohlcv via EXCHANGE. Return pandas DataFrame or None on error.
    """
    try:
        # ccxt expects market symbol like "BTC/USDT"
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("ts", inplace=True)
        return df
    except Exception as e:
        # common reasons: symbol not listed on exchange, network error, rate-limit
        print(f"[fetch_ohlcv] {symbol} {timeframe} error: {type(e).__name__} {e}")
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
    """
    Use ccxt fetch_order_book for Bitget. Return imbalance in [-1,1].
    """
    try:
        ob = EXCHANGE.fetch_order_book(symbol, limit=limit)
        bids = sum([float(b[1]) for b in ob.get("bids", [])])
        asks = sum([float(a[1]) for a in ob.get("asks", [])])
        if bids + asks == 0:
            return 0.0
        return (bids - asks) / (bids + asks)
    except Exception as e:
        print(f"[orderbook] {symbol} error: {type(e).__name__} {e}")
        return 0.0

# ------------------ CORE 5-STEP ANALYSIS ------------------
def analyze_symbol(symbol):
    try:
        # 1) fetch HTFs and entry TF
        df_1d = safe_fetch_ohlcv(symbol, TF_1D, limit=SWING_DAYS+10)
        df_4h = safe_fetch_ohlcv(symbol, TF_4H, limit=200)
        df_1h = safe_fetch_ohlcv(symbol, TF_1H, limit=200)
        df_15 = safe_fetch_ohlcv(symbol, TF_15M, limit=200)
        if df_15 is None or df_15.shape[0] < 3:
            return None

        entry_price = float(df_15['close'].iloc[-1])

        # 2) 2–3 day swing high/low from 1d
        swing_high = swing_low = None
        if df_1d is not None and df_1d.shape[0] >= 1:
            recent1d = df_1d.tail(SWING_DAYS)
            swing_high = float(recent1d['high'].max())
            swing_low = float(recent1d['low'].min())

        # 3) Support/Resistance from pivots (1d/4h/1h) and cluster into zones
        highs=[]; lows=[]
        for df in (df_1d, df_4h, df_1h):
            if df is None: continue
            h,l = pivot_levels_from_df(df)
            highs += h; lows += l
        res_levels = cluster_levels(highs)
        sup_levels = cluster_levels(lows)
        nearest_sup, sup_dist = find_nearest(sup_

