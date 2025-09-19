# pro_crypto_pro_scalper.py
"""
Pro Crypto Scalper (Render-friendly)
- Multi-timeframe SR (1d,4h,1h) + 2-3 day swing
- 15m price-action entry (engulfing/pin/breakout)
- SL = 2.5% / TP = 8.5% (approx RR 1:3.4)
- Single strong alert per scan (no spam)
- Telegram alerts (use env vars or hardcode)
"""

import os
import time
import math
import ccxt
import pandas as pd
from datetime import datetime, timezone
import pytz
import traceback
import requests

# ---------------- CONFIG ----------------
# Set via environment variables (recommended) or hardcode here:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# Exchange (ccxt) - Binance Spot
EXCHANGE = ccxt.binance({"enableRateLimit": True, "options": {"adjustForTimeDifference": True}})

# Symbols: top pairs + XAUUSD (Binance symbol for XAU is often XAUUSD or XAUUSDT - check your exchange)
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "SOL/USDT", "DOGE/USDT",
    "DOT/USDT", "MATIC/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "TRX/USDT", "SHIB/USDT",
    "UNI/USDT", "ATOM/USDT", "ALGO/USDT", "SAND/USDT", "FTM/USDT", "EGLD/USDT",
    # ... add up to ~50 as you like
]
# optional forex / commodity: if your exchange doesn't have XAU/USDT, remove or adapt
SYMBOLS.append("XAU/USDT")  # might need to change to correct symbol on Binance (check availability)

# timeframes
TF_HTF = {"1d": "1d", "4h": "4h", "1h": "1h"}
TF_ENTRY = "15m"

# swing lookback (days on 1d)
SWING_DAYS = 3

# risk percentages
SL_PERCENT = 2.5   # SL = entry - 2.5% (BUY) or +2.5% (SELL)
TP_PERCENT = 8.5   # TP = entry + 8.5% (BUY) or -8.5% (SELL)

# scan timing
SLEEP_BETWEEN_SYMBOLS = 0.6  # seconds (rate-limit friendly)
SLEEP_AFTER_SCAN = 15 * 60   # 15 minutes between full scans

# timezone for human timestamps
TZ = pytz.timezone("Asia/Karachi")

# duplicates control: store last alert per symbol+side for minute resolution
last_alerts = {}

# external indicator placeholders (LuxAlgo / ChartPrime)
USE_EXTERNAL_INDICATORS = False
def fetch_external_indicator_signals(symbol):
    # Implement your webhook/file parsing here if you have them
    return {"lux": None, "chartprime": None}

# ---------------- HELPERS ----------------
def fetch_ohlcv_ccxt(symbol, timeframe, limit=500):
    """Fetch OHLCV via ccxt and return DataFrame (indexed by timestamp)"""
    pair = symbol.replace("/", "")
    bars = EXCHANGE.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df

def pivot_levels_from_df(df, left=3, right=3):
    """Return pivot highs and lows as list of levels from df (simple local pivot)"""
    highs = []
    lows = []
    for i in range(left, len(df)-right):
        window = df.iloc[i-left:i+right+1]
        center_high = df['high'].iloc[i]
        center_low = df['low'].iloc[i]
        if center_high == window['high'].max():
            highs.append({"price": float(center_high), "index": df.index[i]})
        if center_low == window['low'].min():
            lows.append({"price": float(center_low), "index": df.index[i]})
    return highs, lows

def aggregate_levels(levels, tol=0.006):
    """Cluster nearby levels (tol=relative tolerance, e.g. 0.006 = 0.6%)"""
    if not levels:
        return []
    prices = sorted([l["price"] for l in levels])
    clusters = []
    cur = [prices[0]]
    for p in prices[1:]:
        if abs(p - cur[-1]) / cur[-1] <= tol:
            cur.append(p)
        else:
            clusters.append(sum(cur)/len(cur))
            cur = [p]
    clusters.append(sum(cur)/len(cur))
    return clusters

def find_nearest_level(levels, price):
    if not levels:
        return None, None
    arr = [(abs(price-l), l) for l in levels]
    arr.sort()
    dist, level = arr[0]
    return level, dist

def is_bullish_engulf(last, prev):
    return (last['close'] > last['open']) and (prev['close'] < prev['open']) and (last['open'] < prev['close']) and (last['close'] > prev['open'])

def is_bearish_engulf(last, prev):
    return (last['close'] < last['open']) and (prev['close'] > prev['open']) and (last['open'] > prev['close']) and (last['close'] < prev['open'])

def is_pin_bar(last):
    body = abs(last['close'] - last['open'])
    upper = last['high'] - max(last['open'], last['close'])
    lower = min(last['open'], last['close']) - last['low']
    # pin bar if wick >= 2*body on one side and body small
    if body == 0: return False
    if upper > 2*body and lower < body: return "bear"  # long upper wick
    if lower > 2*body and upper < body: return "bull"  # long lower wick
    return False

# ---------------- STRATEGY LOGIC ----------------
def analyze_symbol(symbol):
    try:
        # fetch HTF data
        df_1d = fetch_ohlcv_ccxt(symbol, TF_HTF['1d'], limit=SWING_DAYS+10)
        df_4h = fetch_ohlcv_ccxt(symbol, TF_HTF['4h'], limit=200)
        df_1h = fetch_ohlcv_ccxt(symbol, TF_HTF['1h'], limit=200)
        df_15 = fetch_ohlcv_ccxt(symbol, TF_ENTRY, limit=200)

        # basic checks
        if df_15 is None or df_15.empty:
            return None

        last_price = float(df_15['close'].iloc[-1])

        # 2-3 day swing high/low from 1d
        recent_1d = df_1d.tail(SWING_DAYS)
        swing_high = float(recent_1d['high'].max())
        swing_low = float(recent_1d['low'].min())

        # detect pivots and aggregate to SR levels
        highs_1d, lows_1d = pivot_levels_from_df(df_1d)
        highs_4h, lows_4h = pivot_levels_from_df(df_4h)
        highs_1h, lows_1h = pivot_levels_from_df(df_1h)

        res_levels = aggregate_levels([h['price'] for h in highs_1d + highs_4h + highs_1h])
        sup_levels = aggregate_levels([l['price'] for l in lows_1d + lows_4h + lows_1h])

        # find nearest SR
        nearest_sup, sup_dist = find_nearest_level(sup_levels, last_price)
        nearest_res, res_dist = find_nearest_level(res_levels, last_price)

        # price action on 15m
        last = df_15.iloc[-1]
        prev = df_15.iloc[-2] if len(df_15) >= 2 else df_15.iloc[-1]
        pa_signal = None
        pa_reason = ""

        # engulfing
        if is_bullish_engulf(last, prev):
            pa_signal = "LONG"
            pa_reason = "Bullish Engulfing"
        elif is_bearish_engulf(last, prev):
            pa_signal = "SHORT"
            pa_reason = "Bearish Engulfing"
        else:
            pin = is_pin_bar(last)
            if pin == "bull":
                pa_signal = "LONG"; pa_reason = "Bullish Pin Bar"
            elif pin == "bear":
                pa_signal = "SHORT"; pa_reason = "Bearish Pin Bar"
            else:
                # breakout relative to previous candle
                if last['close'] > prev['high']:
                    pa_signal = "LONG"; pa_reason = "Breakout Up"
                elif last['close'] < prev['low']:
                    pa_signal = "SHORT"; pa_reason = "Breakout Down"

        if pa_signal is None:
            return None

        # check HTF trend: basic EMA(50) on 1h to avoid trades against trend
        df_1h_close = df_1h['close']
        ema50_1h = df_1h_close.ewm(span=50, adjust=False).mean().iloc[-1]
        trend = "bull" if last_price > ema50_1h else "bear"

        # identification rules:
        # LONG: pa_signal LONG & nearest support close & HTF not bearish strong
        # SHORT: pa_signal SHORT & nearest resistance close & HTF not bullish strong
        # "close" defined as within 1% (tunable)
        proximity_pct = 0.01  # 1%

        candidate = None
        if pa_signal == "LONG" and nearest_sup:
            if last_price - nearest_sup <= abs(nearest_sup) * proximity_pct and trend != "bear":
                candidate = "LONG"
        if pa_signal == "SHORT" and nearest_res:
            if nearest_res - last_price <= abs(nearest_res) * proximity_pct and trend != "bull":
                candidate = "SHORT"

        # optionally incorporate external indicators
        ext = fetch_external_indicator_signals(symbol)
        # if ext signals contradict, skip (if using external indicators)
        if USE_EXTERNAL_INDICATORS:
            if ext.get("lux") and ext.get("lux") != candidate.lower():
                return None
            if ext.get("chartprime") and ext.get("chartprime") != candidate.lower():
                return None

        if not candidate:
            return None

        # Build trade details: SL/TP based on percent
        entry = last_price
        if candidate == "LONG":
            sl = round(entry * (1 - SL_PERCENT/100), 8)
            tp = round(entry * (1 + TP_PERCENT/100), 8)
        else:
            sl = round(entry * (1 + SL_PERCENT/100), 8)
            tp = round(entry * (1 - TP_PERCENT/100), 8)

        # Compose result
        res = {
            "symbol": symbol,
            "entry": entry,
            "side": candidate,
            "sl": sl,
            "tp": tp,
            "pa_reason": pa_reason,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "nearest_sup": nearest_sup,
            "nearest_res": nearest_res,
            "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        }
        return res

    except Exception as e:
        # attempt to notify error via Telegram (best-effort)
        try:
            send_telegram(f"⚠️ Error analyzing {symbol}: {e}\n{traceback.format_exc()}")
        except:
            pass
        return None

# ---------------- TELEGRAM ----------------
def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print("[send_telegram] error:", e)

def format_and_send_alert(r):
    if not r:
        return
    key = f"{r['symbol']}_{r['side']}"
    minute_key = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    # avoid duplicates in same minute
    if last_alerts.get(key) == minute_key:
        return
    last_alerts[key] = minute_key

    rr = TP_PERCENT / SL_PERCENT
    msg = (
        f"🚨 <b>{r['symbol']}</b> {r['side']} PRO SETUP\n"
        f"🕒 {r['time']}\n"
        f"📥 Entry: <b>{r['entry']:.8f}</b>\n"
        f"🛑 SL: <b>{r['sl']:.8f}</b> ({SL_PERCENT}%)\n"
        f"🎯 TP: <b>{r['tp']:.8f}</b> (+{TP_PERCENT}% )\n"
        f"⚖ R/R ≈ 1:{rr:.2f}\n"
        f"🔎 PA: {r['pa_reason']}\n"
        f"📈 2-3d Swing High: {r['swing_high']} | Swing Low: {r['swing_low']}\n"
        f"🧭 Nearest S: {r['nearest_sup']} | Nearest R: {r['nearest_res']}\n"
        f"\n✅ Only strong setups. Trade size & risk management is user's responsibility."
    )
    send_telegram(msg)
    print("[ALERT SENT]", r['symbol'], r['side'], f"Entry={r['entry']:.8f}")

# ---------------- MAIN LOOP ----------------
def run_loop(symbols=SYMBOLS):
    print("Pro Multi-TF Scaler started...", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
    while True:
        for s in symbols:
            try:
                result = analyze_symbol(s)
                if result:
                    format_and_send_alert(result)
            except Exception as e:
                # log and try to notify via telegram
                print("[main] error:", e)
                try:
                    send_telegram(f"⚠️ Runtime error in main loop: {e}")
                except:
                    pass
            time.sleep(SLEEP_BETWEEN_SYMBOLS)
        # sleep until next scan (default every 15 minutes)
        time.sleep(SLEEP_AFTER_SCAN)

# ---------------- ENTRY ----------------
if __name__ == "__main__":
    run_loop()
