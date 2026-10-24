#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AlgoWave Scanner Pro - Multi-Asset Mean Reversion Engine
Platform: Pydroid 3 (Android Mobile Terminal)
Strategy: One-Shot Band Break Reversal (EMA 250 + Bollinger Bounds)
Author: @BenalgoDev
"""

import os
import sys
import time
import math
import datetime
import requests
import traceback

# ==================== CONFIGURATION ====================
# Safe configuration: Reads token from environment variable
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = "@vipsignalhup"

# Quantitative Model Parameters
LENGTH = 250                  # EMA 250 Trend Baseline
STD_DEV = 1.0                 # Bollinger Deviation Multiplier
TOLERANCE_PCT = 1.8           # Buy Zone Threshold above Green Band (%)
MAX_BELOW_BAND_PCT = 2.0      # Max penetration under Green Band (%)
MIN_PROFIT_PCT = 3.0          # Minimum Target Profit to EMA 250 (%)

# Speed and Intervals
CHECK_INTERVAL = 300          # 5 minutes sleep between full cycles
SCAN_DELAY = 0.01             # Fast loop delay for fluid terminal action
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# If True, triggers a verified visual alert during recording if market is quiet
DEMO_BURST_MODE = False

BINANCE_ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]

symbol_states = {}
pending_signals = []

# ==================== LOGGING & DIAGNOSTICS ====================
def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def test_connectivity():
    print("\n" + "=" * 55, flush=True)
    log("📡 [SYSTEM] INITIALIZING HIGH-SPEED MARKET SCANNER...")

    if not TELEGRAM_BOT_TOKEN:
        log("❌ TELEGRAM_BOT_TOKEN is not set.")
        log("💡 Please export TELEGRAM_BOT_TOKEN in your environment before running:")
        log("   export TELEGRAM_BOT_TOKEN=\"your_bot_token_here\"")
        print("=" * 55 + "\n", flush=True)
        sys.exit(1)

    binance_ok = False
    for ep in ["https://data-api.binance.vision/api/v3/ping", "https://api.binance.com/api/v3/ping"]:
        try:
            r = requests.get(ep, timeout=3)
            if r.status_code == 200:
                binance_ok = True
                log(f"⚡ Market Feed: ONLINE ({ep.split('/')[2]})")
                break
        except Exception:
            continue

    if not binance_ok:
        log("❌ Market Data Feed: OFFLINE / TIMEOUT")

    telegram_ok = False
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=4)
        if r.status_code == 200:
            data = r.json()
            if data.get("ok"):
                bot_user = data.get("result", {}).get("username", "Bot")
                telegram_ok = True
                log(f"🤖 Telegram Bridge: CONNECTED (@{bot_user})")
            else:
                log("⚠️ Telegram API: AUTH ERROR (Invalid Token)")
        else:
            log(f"⚠️ Telegram API: HTTP {r.status_code}")
    except Exception:
        log("⚠️ Telegram API: BLOCKED (Check VPN)")

    print("=" * 55 + "\n", flush=True)
    return binance_ok, telegram_ok

def format_price(val):
    if val is None:
        return "0.00"
    if val < 0.0001:
        return f"{val:.8f}"
    elif val < 1:
        return f"{val:.4f}"
    else:
        return f"{val:.2f}"

# ==================== TELEGRAM DISPATCH ====================
def get_chart_image(symbol, tf):
    tf_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    interval_code = tf_map.get(tf, "240")
    chart_url = (
        f"https://image.thum.io/get/width/1000/crop/680/noanimate/"
        f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}&interval={interval_code}"
    )
    try:
        r = requests.get(chart_url, timeout=7)
        if r.status_code == 200 and r.content and len(r.content) > 5000:
            return r.content
    except Exception:
        pass
    return None

def send_telegram_signal(caption, symbol, tf):
    photo = get_chart_image(symbol, tf)
    if photo:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            files = {'photo': (f'{symbol}_{tf}.png', photo, 'image/png')}
            payload = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
            r = requests.post(url, data=payload, files=files, timeout=12)
            if r.status_code == 200 and r.json().get("ok"):
                log(f"🚀 Telegram Dispatch: [CHART + SIGNAL] #{symbol} [{tf}] Sent!")
                return True
        except Exception:
            pass

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': caption,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        r = requests.post(url, data=payload, timeout=8)
        if r.status_code == 200 and r.json().get("ok"):
            log(f"⚡ Telegram Dispatch: [DIRECT TEXT ALERT] #{symbol} [{tf}] Sent!")
            return True
    except Exception:
        pass

    pending_signals.append({"caption": caption, "symbol": symbol, "tf": tf})
    log(f"📥 Signal #{symbol} saved in retry queue ({len(pending_signals)} items).")
    return False

def flush_pending():
    if not pending_signals:
        return
    log(f"🔄 Retrying {len(pending_signals)} queued signals...")
    sent = 0
    while pending_signals and sent < 3:
        item = pending_signals.pop(0)
        if send_telegram_signal(item["caption"], item["symbol"], item["tf"]):
            sent += 1
            time.sleep(0.5)
        else:
            pending_signals.insert(0, item)
            break

# ==================== DATA & CALCULATIONS ====================
def get_all_pairs():
    # 1. Fetch dynamically from Nobitex market stats (all USDT pairs)
    try:
        r = requests.get("https://apiv2.nobitex.ir/market/stats", timeout=5)
        if r.status_code == 200:
            stats = r.json().get('stats', {})
            pairs = [k.replace("-usdt", "").upper() + "USDT" for k in stats.keys() if k.endswith("-usdt")]
            if len(pairs) > 15:
                return sorted(list(set(pairs)))
    except Exception:
        pass

    # 2. Comprehensive Fallback Market List
    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
        "AVAXUSDT", "LINKUSDT", "DOTUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT",
        "APTUSDT", "FILUSDT", "ICPUSDT", "ARBUSDT", "OPUSDT", "FTMUSDT", "SUIUSDT",
        "TAOUSDT", "FETUSDT", "RENDERUSDT", "RAYUSDT", "LITUSDT", "ATUSDT", "GALAUSDT",
        "SANDUSDT", "MANAUSDT", "AXSUSDT", "PEPEUSDT", "ENAUSDT", "WLDUSDT", "COTIUSDT"
    ]

def get_klines(symbol, interval):
    for base in BINANCE_ENDPOINTS:
        url = f"{base}?symbol={symbol}&interval={interval}&limit=320"
        try:
            r = requests.get(url, timeout=3.5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) >= LENGTH + 15:
                    highs = [float(c[2]) for c in data]
                    lows = [float(c[3]) for c in data]
                    closes = [float(c[4]) for c in data]
                    return closes, highs, lows
        except Exception:
            continue
    return None, None, None

def calculate_ema_series(prices, span):
    alpha = 2.0 / (span + 1.0)
    ema = [None] * len(prices)
    seed = sum(prices[:span]) / span
    ema[span - 1] = seed
    for i in range(span, len(prices)):
        ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i - 1]
    return ema

# ==================== STRATEGY ENGINE ====================
def analyze_sequence_and_signal(symbol, tf, step_count=0):
    closes, highs, lows = get_klines(symbol, tf)
    if not closes or len(closes) < LENGTH + 15:
        return None

    c = closes[:-1]
    h = highs[:-1]
    l = lows[:-1]
    n = len(c)

    ema_series = calculate_ema_series(c, LENGTH)
    lookback = min(50, n - LENGTH)
    upper_bands = [None] * n
    lower_bands = [None] * n

    for i in range(n - lookback, n):
        window = c[i - LENGTH + 1 : i + 1]
        mean_val = sum(window) / LENGTH
        variance = sum((x - mean_val) ** 2 for x in window) / LENGTH
        dev = math.sqrt(variance) * STD_DEV
        upper_bands[i] = ema_series[i] + dev
        lower_bands[i] = ema_series[i] - dev

    cur_close = c[-1]
    cur_low = l[-1]
    cur_high = h[-1]
    cur_ema = ema_series[-1]
    cur_upper = upper_bands[-1]
    cur_lower = lower_bands[-1]

    if cur_ema is None or cur_upper is None or cur_lower is None or cur_lower <= 0:
        return None

    state_key = (symbol, tf)
    current_state = symbol_states.get(state_key, "idle")

    # Cycle Reset on Upper Band Cross
    if cur_high >= cur_upper:
        symbol_states[state_key] = "above_red"
        return None

    # Consumed when EMA 250 (Blue Line) Target is reached
    if current_state == "signalled":
        if cur_high >= cur_ema:
            symbol_states[state_key] = "consumed"
            log(f"  [{tf}] #{symbol} Hit TP (Blue Line EMA 250) -> Cycle Finished.")
        return None

    if current_state == "consumed":
        return None

    # Check Sequence: High > Red -> Drop Below Blue
    if current_state in ("idle", "above_red"):
        saw_above_red = False
        saw_below_blue = False
        
        scan_start = n - lookback
        for i in range(scan_start, n):
            if upper_bands[i] is None:
                continue
            if not saw_above_red:
                if h[i] >= upper_bands[i]:
                    saw_above_red = True
            else:
                if c[i] < ema_series[i]:
                    saw_below_blue = True
                    break

        if saw_above_red and saw_below_blue:
            current_state = "below_blue"
            symbol_states[state_key] = "below_blue"

    # Entry Trigger Conditions
    if current_state == "below_blue":
        if cur_close >= cur_ema:
            symbol_states[state_key] = "idle"
            return None

        min_allowed_price = cur_lower * (1.0 - MAX_BELOW_BAND_PCT / 100.0)
        max_allowed_price = cur_lower * (1.0 + TOLERANCE_PCT / 100.0)

        price_in_buy_zone = (cur_low <= max_allowed_price) and (cur_close >= min_allowed_price)
        profit_pct = ((cur_ema - cur_close) / cur_close) * 100.0

        trigger_fire = (price_in_buy_zone and profit_pct >= MIN_PROFIT_PCT)

        # Safety Demo trigger during video recording if enabled
        if DEMO_BURST_MODE and step_count == 12:
            trigger_fire = True
            profit_pct = max(3.45, profit_pct)

        if trigger_fire:
            symbol_states[state_key] = "signalled"

            entry = cur_close
            target = cur_ema if cur_ema > entry else entry * 1.042
            stop_loss = cur_lower * 0.985
            loss_pct = ((entry - stop_loss) / entry) * 100.0
            rr = (profit_pct / loss_pct) if loss_pct > 0 else 1.5

            if cur_low <= cur_lower:
                status_desc = "Price pierced Lower Green Band"
            else:
                status_desc = "Price testing Green Band Entry Zone"

            return {
                "symbol": symbol,
                "tf": tf,
                "entry": entry,
                "target": target,
                "stop_loss": stop_loss,
                "profit_pct": profit_pct,
                "loss_pct": loss_pct,
                "rr": rr,
                "basis": cur_ema,
                "lower_band": cur_lower,
                "upper_band": cur_upper,
                "desc": status_desc
            }

    return None

# ==================== MAIN SCANNER ENGINE ====================
def run_scanner():
    test_connectivity()

    pairs = get_all_pairs()
    log(f"Ready: Monitoring {len(pairs)} Total Market Assets across {TIMEFRAMES}")

    tf_tv_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    cycle = 0

    while True:
        cycle += 1
        cycle_start = time.time()
        flush_pending()

        total_checks = len(pairs) * len(TIMEFRAMES)
        current = 0
        found = 0

        log(f"🔄 Starting Full Sweep #{cycle} (Total checks: {total_checks})...")

        for s in pairs:
            for tf in TIMEFRAMES:
                current += 1
                log(f"🔍 [{current:03d}/{total_checks:03d}] Scanning {s:<10} [{tf:<3}] | Hits: {found}")

                res = analyze_sequence_and_signal(s, tf, step_count=current)
                if res:
                    found += 1
                    print("\n" + "🔥" * 28, flush=True)
                    log(f"🎯 [EXPLOSIVE SETUP FOUND] #{res['symbol']} on [{res['tf']}]")
                    log(f"Entry: {format_price(res['entry'])} | Target (Blue Line): {format_price(res['target'])} (+{res['profit_pct']:.2f}%)")
                    log(f"Green Band: {format_price(res['lower_band'])} | EMA 250: {format_price(res['basis'])}")
                    print("🔥" * 28 + "\n", flush=True)

                    tv_interval = tf_tv_map.get(res['tf'], '240')
                    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{res['symbol']}&interval={tv_interval}"

                    msg = (
                        f"🚨 <b>ALGOWAVE | STRATEGY ALERT</b> 🚨\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💎 <b>Pair:</b> #{res['symbol']}\n"
                        f"⏱ <b>Timeframe:</b> <code>{res['tf']}</code>\n"
                        f"🧭 <b>Setup:</b> <code>One-Shot Mean Reversion</code>\n"
                        f"💡 <b>Trigger:</b> {res['desc']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🟢 <b>Buy Zone (Green Band):</b> <code>{format_price(res['lower_band'])} USDT</code>\n"
                        f"💲 <b>Current Entry:</b> <code>{format_price(res['entry'])} USDT</code>\n"
                        f"🔵 <b>Target (EMA 250 / Blue):</b> <code>{format_price(res['target'])} USDT</code> (<b>+{res['profit_pct']:.2f}%</b>)\n"
                        f"🛑 <b>Stop Loss:</b> <code>{format_price(res['stop_loss'])} USDT</code> (-{res['loss_pct']:.2f}%)\n"
                        f"⚖️ <b>Risk / Reward:</b> <b>1 : {res['rr']:.2f}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ <i>Single-cycle signal: Once EMA 250 is reached, setup is consumed.</i>\n"
                        f"📈 <a href='{tv_link}'>Open Live Chart on TradingView</a>\n\n"
                        f"⚡ <b>Channel:</b> {TELEGRAM_CHAT_ID}\n"
                        f"🛠 <b>Engine Developed by:</b> @BenalgoDev"
                    )

                    send_telegram_signal(msg, res['symbol'], res['tf'])
                    time.sleep(1.0)

                time.sleep(SCAN_DELAY)

        elapsed = time.time() - cycle_start
        log(f"✅ Cycle #{cycle} completed in {elapsed:.1f}s. Total signals found: {found}")
        log(f"⏳ Sleeping {CHECK_INTERVAL // 60}m until next sweep...")
        time.sleep(CHECK_INTERVAL)

def main():
    while True:
        try:
            run_scanner()
        except Exception as e:
            log(f"⚠️ Engine exception caught: {e}")
            log("🔄 Restarting runner in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    main()
