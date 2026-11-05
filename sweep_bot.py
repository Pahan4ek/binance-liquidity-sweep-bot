import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

import pandas as pd
import requests
import websocket

try:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "symbol": "BTCUSDT",
    "interval": "15m",
    "limit": 150,
    "volume_multiplier": 1.5,
    "swing_left": 3,
    "swing_right": 3,
    "oi_check_interval": 30,
    "min_delta_ratio": 0.1,
    "klines_refresh_interval": 120,
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "min_score": 4
    }
}

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                for k, v in user_cfg.items():
                    if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
        except Exception as e:
            print(f"[!] Warning reading {CONFIG_FILE}: {e}. Using defaults.")
    return cfg

def parse_args():
    parser = argparse.ArgumentParser(description="Binance Futures Liquidity Sweep & Order Flow Bot")
    parser.add_argument("--symbol", "-s", type=str, help="Trading pair ticker (e.g. BTCUSDT, ETHUSDT, SOLUSDT)")
    parser.add_argument("--interval", "-i", type=str, help="Candle interval (e.g. 1m, 3m, 5m, 15m, 1h)")
    parser.add_argument("--vol-mult", type=float, help="Volume spike multiplier (e.g. 1.5)")
    parser.add_argument("--min-delta", type=float, help="Minimum delta/volume ratio for confirmation (e.g. 0.1)")
    parser.add_argument("--telegram-token", type=str, help="Telegram Bot Token for alerts")
    parser.add_argument("--chat-id", type=str, help="Telegram Chat ID for alerts")
    return parser.parse_args()

# Global state
cfg = load_config()
args = parse_args()

if args.symbol:
    cfg["symbol"] = args.symbol.upper()
if args.interval:
    cfg["interval"] = args.interval.lower()
if args.vol_mult:
    cfg["volume_multiplier"] = args.vol_mult
if args.min_delta:
    cfg["min_delta_ratio"] = args.min_delta
if args.telegram_token:
    cfg["telegram"]["bot_token"] = args.telegram_token
    cfg["telegram"]["enabled"] = True
if args.chat_id:
    cfg["telegram"]["chat_id"] = args.chat_id
    cfg["telegram"]["enabled"] = True

SYMBOL = cfg["symbol"].upper()
INTERVAL = cfg["interval"].lower()
LIMIT = int(cfg.get("limit", 150))
VOLUME_MULTIPLIER = float(cfg.get("volume_multiplier", 1.5))
SWING_LEFT = int(cfg.get("swing_left", 3))
SWING_RIGHT = int(cfg.get("swing_right", 3))
OI_CHECK_INTERVAL = int(cfg.get("oi_check_interval", 30))
MIN_DELTA_RATIO = float(cfg.get("min_delta_ratio", 0.1))
KLINES_REFRESH_INTERVAL = int(cfg.get("klines_refresh_interval", 120))
TG_CONFIG = cfg.get("telegram", {})

df = pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_vol'])
last_saved_oi: Optional[float] = None
oi_history: List[float] = []
current_swing_highs: List[float] = []
current_swing_lows: List[float] = []
signal_cooldown: Dict[str, float] = {}
live_candle: Dict[str, Any] = {}
df_lock = threading.Lock()

# ─────────────────────────────────────────────
# TELEGRAM ALERTS
# ─────────────────────────────────────────────

def send_telegram_alert(text: str):
    if not TG_CONFIG.get("enabled"):
        return
    token = TG_CONFIG.get("bot_token", "").strip()
    chat_id = TG_CONFIG.get("chat_id", "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"\n[!] Failed to send Telegram alert: {e}")

# ─────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────

def get_historical_klines(silent: bool = False) -> bool:
    global df
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}"
    try:
        response = requests.get(url, timeout=10).json()
        if not isinstance(response, list):
            if not silent:
                print(f"[!] Binance returned error: {response}")
            return False
    except Exception as e:
        if not silent:
            print(f"[!] Error fetching history: {e}")
        return False

    rows = []
    for k in response:
        rows.append({
            'time': pd.to_datetime(k[0], unit='ms'),
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'taker_buy_vol': float(k[9])
        })

    new_df = pd.DataFrame(rows)
    with df_lock:
        df = new_df

    update_swing_levels()
    return True

def klines_refresh_thread():
    time.sleep(KLINES_REFRESH_INTERVAL)
    while True:
        ok = get_historical_klines(silent=True)
        ts = datetime.now().strftime('%H:%M:%S')
        if ok:
            h = len(current_swing_highs)
            l = len(current_swing_lows)
            all_lvls = sorted(set(current_swing_highs + current_swing_lows))
            price = live_candle.get('close')
            if price and all_lvls:
                above = [v for v in all_lvls if v > price]
                below = [v for v in all_lvls if v < price]
                a_str = f"{min(above):.4f} (↑{((min(above)/price)-1)*100:.2f}%)" if above else "None"
                b_str = f"{max(below):.4f} (↓{((1-max(below)/price)*100):.2f}%)" if below else "None"
                print(f"\n[{ts}] ↻ Levels: {h} highs, {l} lows | Closest Above: {a_str} | Closest Below: {b_str}")
            else:
                print(f"\n[{ts}] ↻ Levels updated: {h} highs, {l} lows")
        else:
            print(f"\n[{ts}] [!] Failed to refresh historical klines")
        time.sleep(KLINES_REFRESH_INTERVAL)

def get_open_interest() -> Optional[float]:
    try:
        url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={SYMBOL}"
        r = requests.get(url, timeout=5).json()
        return float(r.get('openInterest', 0))
    except Exception:
        return None

def oi_polling_thread():
    global last_saved_oi, oi_history
    while True:
        oi = get_open_interest()
        if oi is not None:
            oi_history.append(oi)
            if len(oi_history) > 10:
                oi_history.pop(0)
            last_saved_oi = oi
        time.sleep(OI_CHECK_INTERVAL)

# ─────────────────────────────────────────────
# SWING LEVELS & LIQUIDITY POOLS
# ─────────────────────────────────────────────

def update_swing_levels():
    global current_swing_highs, current_swing_lows
    with df_lock:
        local_df = df.copy()

    if len(local_df) < SWING_LEFT + SWING_RIGHT + 2:
        return

    hist = local_df.iloc[:-1].reset_index(drop=True)
    highs, lows = [], []

    for i in range(SWING_LEFT, len(hist) - SWING_RIGHT):
        h = hist['high'].iloc[i]
        l = hist['low'].iloc[i]

        if all(hist['high'].iloc[i - j] < h for j in range(1, SWING_LEFT + 1)) and \
           all(hist['high'].iloc[i + j] < h for j in range(1, SWING_RIGHT + 1)):
            highs.append(round(h, 8))

        if all(hist['low'].iloc[i - j] > l for j in range(1, SWING_LEFT + 1)) and \
           all(hist['low'].iloc[i + j] > l for j in range(1, SWING_RIGHT + 1)):
            lows.append(round(l, 8))

    current_swing_highs = sorted(set(highs))
    current_swing_lows = sorted(set(lows))

def nearest_above(price: float) -> Optional[float]:
    all_levels = current_swing_highs + current_swing_lows
    candidates = [lvl for lvl in all_levels if lvl > price]
    return min(candidates) if candidates else None

def nearest_below(price: float) -> Optional[float]:
    all_levels = current_swing_highs + current_swing_lows
    candidates = [lvl for lvl in all_levels if lvl < price]
    return max(candidates) if candidates else None

def get_oi_trend() -> int:
    """
    Returns OI Trend:
      -1 = Falling (Stops flushed / liquidations executed)
       0 = Neutral
      +1 = Rising (New aggressive positioning)
    """
    if len(oi_history) < 3:
        return 0
    recent = oi_history[-3:]
    if recent[-1] < recent[0] * 0.999:
        return -1
    if recent[-1] > recent[0] * 1.001:
        return 1
    return 0

# ─────────────────────────────────────────────
# STRATEGY: LIQUIDITY SWEEP & ORDER FLOW
# ─────────────────────────────────────────────

def check_sweep_strategy(candle: dict):
    if len(df) < 20:
        return

    c_high = candle['high']
    c_low = candle['low']
    c_close = candle['close']
    c_vol = candle['volume']
    c_buy = candle['taker_buy_vol']
    c_sell = c_vol - c_buy
    delta = c_buy - c_sell

    with df_lock:
        avg_vol = df['volume'].iloc[-20:].mean()
    is_high_vol = c_vol > avg_vol * VOLUME_MULTIPLIER

    delta_ratio = abs(delta) / c_vol if c_vol > 0 else 0
    oi_trend = get_oi_trend()
    now_ts = datetime.now().strftime('%H:%M:%S')

    # BEARISH SWEEP (false breakout above Swing High -> Short)
    for level in current_swing_highs[-5:]:
        if c_high > level and c_close < level:
            key = f"bear_{level:.8f}"
            last_sig = signal_cooldown.get(key, 0)
            if time.time() - last_sig < 300:
                continue

            score = 0
            reasons = []

            if is_high_vol:
                score += 2
                reasons.append(f"✅ Anomaly Volume x{c_vol/avg_vol:.1f} (> {VOLUME_MULTIPLIER}x)")
            else:
                reasons.append(f"❌ Low Volume (x{c_vol/avg_vol:.1f})")

            if delta < 0 and delta_ratio >= MIN_DELTA_RATIO:
                score += 2
                reasons.append(f"✅ Aggressive Sell Delta ({delta:+.0f}, {delta_ratio*100:.1f}%)")
            elif delta < 0:
                score += 1
                reasons.append(f"⚠️ Mild Sell Delta ({delta:+.0f})")
            else:
                reasons.append(f"❌ Positive Delta (+{delta:.0f})")

            if oi_trend == -1:
                score += 2
                reasons.append("✅ Open Interest Dropping (Stops/Liquidations Cleared)")
            elif oi_trend == 0:
                score += 1
                reasons.append("⚠️ Open Interest Neutral")
            else:
                reasons.append("❌ Open Interest Rising")

            _print_and_alert_signal("BEARISH LIQUIDITY SWEEP", level, c_high, c_close,
                                    reasons, score, now_ts, direction="short")

            if score >= 4:
                signal_cooldown[key] = time.time()

    # BULLISH SWEEP (false breakout below Swing Low -> Long)
    for level in current_swing_lows[-5:]:
        if c_low < level and c_close > level:
            key = f"bull_{level:.8f}"
            last_sig = signal_cooldown.get(key, 0)
            if time.time() - last_sig < 300:
                continue

            score = 0
            reasons = []

            if is_high_vol:
                score += 2
                reasons.append(f"✅ Anomaly Volume x{c_vol/avg_vol:.1f} (> {VOLUME_MULTIPLIER}x)")
            else:
                reasons.append(f"❌ Low Volume (x{c_vol/avg_vol:.1f})")

            if delta > 0 and delta_ratio >= MIN_DELTA_RATIO:
                score += 2
                reasons.append(f"✅ Aggressive Buy Delta (+{delta:.0f}, {delta_ratio*100:.1f}%)")
            elif delta > 0:
                score += 1
                reasons.append(f"⚠️ Mild Buy Delta (+{delta:.0f})")
            else:
                reasons.append(f"❌ Negative Delta ({delta:+.0f})")

            if oi_trend == -1:
                score += 2
                reasons.append("✅ Open Interest Dropping (Shorts Squeezed/Cleared)")
            elif oi_trend == 0:
                score += 1
                reasons.append("⚠️ Open Interest Neutral")
            else:
                reasons.append("❌ Open Interest Rising")

            _print_and_alert_signal("BULLISH LIQUIDITY SWEEP", level, c_low, c_close,
                                    reasons, score, now_ts, direction="long")

            if score >= 4:
                signal_cooldown[key] = time.time()

def _print_and_alert_signal(title: str, level: float, extreme: float, close: float,
                            reasons: List[str], score: int, ts: str, direction: str):
    emoji_map = {6: "🔥🔥🔥", 5: "🔥🔥", 4: "🔥", 3: "⚠️ ", 2: "📌", 1: "📌", 0: "📌"}
    strength = emoji_map.get(score, "📌")

    print(f"\n{'─'*55}")
    print(f"[{ts}] {strength} {title} | {SYMBOL} {INTERVAL}")
    print(f"{'─'*55}")
    print(f"  Liquidity Level     : {level:.4f}")
    if direction == "short":
        print(f"  Sweep Peak (High)   : {extreme:.4f} (+{(extreme/level-1)*100:.2f}%)")
    else:
        print(f"  Sweep Base (Low)    : {extreme:.4f} (-{(1-extreme/level)*100:.2f}%)")
    print(f"  Candle Close        : {close:.4f}")
    print(f"  Confirmations:")
    for r in reasons:
        print(f"    {r}")
    print(f"  Total Confluence    : {score}/6")

    sl = extreme
    side_str = "SHORT ↓" if direction == "short" else "LONG  ↑"
    if score >= 5:
        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║  💰 HIGH PROBABILITY SIGNAL!         ║")
        print(f"  ║  Direction : {side_str:<23} ║")
        print(f"  ║  Entry     : {close:<23.4f} ║")
        print(f"  ║  Stop-Loss : {sl:<23.4f} ║")
        print(f"  ╚══════════════════════════════════════╝")
    elif score >= 4:
        print(f"\n  → Confirmed Setup (Score {score}/6) | SL: {sl:.4f}")
    else:
        print(f"\n  → Weak Setup (Score {score}/6) — Filtered")
    print(f"{'─'*55}\n")

    # Telegram notification
    min_tg_score = TG_CONFIG.get("min_score", 4)
    if score >= min_tg_score:
        tg_text = (
            f"*{strength} {title}*\n"
            f"• *Pair*: `{SYMBOL}` ({INTERVAL})\n"
            f"• *Direction*: *{side_str}*\n"
            f"• *Level*: `{level:.4f}`\n"
            f"• *Entry*: `{close:.4f}`\n"
            f"• *Stop-Loss*: `{sl:.4f}`\n"
            f"• *Score*: *{score}/6*\n"
            f"• *Confirmations*:\n" + "\n".join([f"  {r}" for r in reasons])
        )
        send_telegram_alert(tg_text)

# ─────────────────────────────────────────────
# WEBSOCKET STREAM
# ─────────────────────────────────────────────

def on_message(ws, message):
    global df, live_candle
    try:
        data = json.loads(message)
    except Exception:
        return

    k = data.get('k', {})
    is_closed = k.get('x', False)
    price = float(k.get('c', 0))

    live_candle = {
        'high': float(k.get('h', 0)),
        'low': float(k.get('l', 0)),
        'close': price,
        'volume': float(k.get('v', 0)),
        'taker_buy_vol': float(k.get('V', 0)),
    }

    nearest_h = nearest_above(price)
    nearest_l = nearest_below(price)
    oi_val = f"{last_saved_oi:,.0f}" if last_saved_oi else "—"

    h_str = f"{nearest_h:.4f} (↑{((nearest_h/price)-1)*100:.2f}%)" if nearest_h else "None"
    l_str = f"{nearest_l:.4f} (↓{((1-(nearest_l/price))*100):.2f}%)" if nearest_l else "None"

    print(
        f"\r[{datetime.now().strftime('%H:%M:%S')}] "
        f"{SYMBOL}: {price:.4f} "
        f"| Above: {h_str} "
        f"| Below: {l_str} "
        f"| OI: {oi_val}   ",
        end="", flush=True
    )

    if is_closed:
        print()
        candle = {
            'time': pd.to_datetime(k['T'], unit='ms'),
            'open': float(k['o']),
            'high': float(k['h']),
            'low': float(k['l']),
            'close': price,
            'volume': float(k['v']),
            'taker_buy_vol': float(k['V']),
        }

        with df_lock:
            new_row = pd.DataFrame([candle])
            df = pd.concat([df, new_row], ignore_index=True)
            if len(df) > LIMIT + 10:
                df = df.iloc[10:].reset_index(drop=True)

        update_swing_levels()
        check_sweep_strategy(candle)

def on_error(ws, error):
    print(f"\n[!] WebSocket Error: {error}")

def on_close(ws, code, msg):
    print(f"\n[*] WebSocket closed. Code: {code}")

def on_open(ws):
    print(f"[+] WebSocket connected: {SYMBOL} {INTERVAL}")

# ─────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────

def run_bot():
    print(f"\n{'='*60}")
    print(f"  Binance Futures Liquidity Sweep & Order Flow Bot")
    print(f"  Pair: {SYMBOL} | Timeframe: {INTERVAL} | Vol Spike: x{VOLUME_MULTIPLIER}")
    print(f"{'='*60}")
    print(f"[*] Preloading {LIMIT} historical klines...")

    if not get_historical_klines():
        print("[!] Failed to load history. Verify symbol and network connection.")
        sys.exit(1)

    print(f"[+] Loaded {len(df)} candles.")
    print(f"[+] Swing Highs: {len(current_swing_highs)} | Swing Lows: {len(current_swing_lows)}")

    all_lvls = sorted(set(current_swing_highs + current_swing_lows))
    if all_lvls:
        print(f"[+] Detected Levels ({len(all_lvls)}): ", end="")
        print("  ".join(f"{v:.4f}" for v in all_lvls[-6:]))

    # Background threads
    threading.Thread(target=oi_polling_thread, daemon=True).start()
    threading.Thread(target=klines_refresh_thread, daemon=True).start()

    time.sleep(1)
    print(f"\n[*] Monitoring live stream (Press Ctrl+C to stop)...\n")

    socket_url = f"wss://fstream.binance.com/ws/{SYMBOL.lower()}@kline_{INTERVAL}"
    ws = websocket.WebSocketApp(
        socket_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    try:
        ws.run_forever(ping_interval=20, ping_timeout=10)
    except KeyboardInterrupt:
        print("\n[*] Bot stopped by user.")

if __name__ == "__main__":
    run_bot()
