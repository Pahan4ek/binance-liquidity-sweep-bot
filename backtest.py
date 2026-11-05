import argparse
import sys
import time
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
import requests

try:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def fetch_klines(symbol: str, interval: str, total_limit: int = 1000) -> pd.DataFrame:
    """
    Fetches historical klines from Binance Futures API (chunked up to total_limit).
    """
    symbol = symbol.upper()
    all_rows = []
    end_time = None
    remaining = total_limit

    print(f"[*] Downloading {total_limit} candles for {symbol} ({interval})...")

    while remaining > 0:
        limit = min(remaining, 500)
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        if end_time:
            url += f"&endTime={end_time}"

        try:
            r = requests.get(url, timeout=10).json()
            if not isinstance(r, list) or len(r) == 0:
                break

            for k in r:
                all_rows.append({
                    'time': pd.to_datetime(k[0], unit='ms'),
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5]),
                    'taker_buy_vol': float(k[9])
                })

            remaining -= len(r)
            end_time = int(r[0][0]) - 1
            if len(r) < limit:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"[!] Error fetching historical data: {e}")
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows).sort_values('time').drop_duplicates('time').reset_index(drop=True)
    return df

def run_backtest(df: pd.DataFrame, vol_mult: float = 1.5, min_delta_ratio: float = 0.1,
                 swing_left: int = 3, swing_right: int = 3, target_rr: float = 2.0, min_score: int = 4):
    if len(df) < 50:
        print("[!] Not enough data to backtest.")
        return

    print(f"[*] Running simulation over {len(df)} candles...")
    print(f"    Date Range: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    print(f"    Parameters: Vol Spike x{vol_mult} | Min Delta: {min_delta_ratio*100:.0f}% | Target R:R: 1:{target_rr} | Min Score: {min_score}")

    trades = []
    signal_cooldown = {}

    for i in range(50, len(df) - 10):
        current_candle = df.iloc[i]
        c_time = current_candle['time']
        c_high = current_candle['high']
        c_low = current_candle['low']
        c_close = current_candle['close']
        c_vol = current_candle['volume']
        c_buy = current_candle['taker_buy_vol']
        c_sell = c_vol - c_buy
        delta = c_buy - c_sell
        delta_ratio = abs(delta) / c_vol if c_vol > 0 else 0

        # Historical window strictly up to current candle (no lookahead!)
        hist = df.iloc[:i].reset_index(drop=True)
        avg_vol = hist['volume'].iloc[-20:].mean()
        is_high_vol = c_vol > avg_vol * vol_mult

        # Swing levels
        highs, lows = [], []
        for idx in range(swing_left, len(hist) - swing_right):
            h = hist['high'].iloc[idx]
            l = hist['low'].iloc[idx]
            if all(hist['high'].iloc[idx - j] < h for j in range(1, swing_left + 1)) and \
               all(hist['high'].iloc[idx + j] < h for j in range(1, swing_right + 1)):
                highs.append(h)
            if all(hist['low'].iloc[idx - j] > l for j in range(1, swing_left + 1)) and \
               all(hist['low'].iloc[idx + j] > l for j in range(1, swing_right + 1)):
                lows.append(l)

        recent_highs = sorted(set(highs))[-5:]
        recent_lows = sorted(set(lows))[-5:]

        # Check Bearish Sweep
        for level in recent_highs:
            if c_high > level and c_close < level:
                if c_time.timestamp() - signal_cooldown.get(f"bear_{level}", 0) < 1800:
                    continue

                score = 0
                if is_high_vol: score += 2
                if delta < 0 and delta_ratio >= min_delta_ratio: score += 2
                elif delta < 0: score += 1

                if score >= min_score:
                    signal_cooldown[f"bear_{level}"] = c_time.timestamp()
                    entry = c_close
                    sl = c_high
                    risk = sl - entry
                    if risk <= 0:
                        continue
                    tp = entry - (risk * target_rr)

                    # Simulate forward
                    outcome = "OPEN"
                    pnl_r = 0.0
                    exit_idx = i
                    for future_idx in range(i + 1, len(df)):
                        f_candle = df.iloc[future_idx]
                        if f_candle['high'] >= sl:
                            outcome = "LOSS"
                            pnl_r = -1.0
                            exit_idx = future_idx
                            break
                        elif f_candle['low'] <= tp:
                            outcome = "WIN"
                            pnl_r = target_rr
                            exit_idx = future_idx
                            break

                    trades.append({
                        "type": "SHORT",
                        "time": c_time,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "score": score,
                        "outcome": outcome,
                        "pnl_r": pnl_r,
                        "duration_candles": exit_idx - i
                    })
                    break

        # Check Bullish Sweep
        for level in recent_lows:
            if c_low < level and c_close > level:
                if c_time.timestamp() - signal_cooldown.get(f"bull_{level}", 0) < 1800:
                    continue

                score = 0
                if is_high_vol: score += 2
                if delta > 0 and delta_ratio >= min_delta_ratio: score += 2
                elif delta > 0: score += 1

                if score >= min_score:
                    signal_cooldown[f"bull_{level}"] = c_time.timestamp()
                    entry = c_close
                    sl = c_low
                    risk = entry - sl
                    if risk <= 0:
                        continue
                    tp = entry + (risk * target_rr)

                    # Simulate forward
                    outcome = "OPEN"
                    pnl_r = 0.0
                    exit_idx = i
                    for future_idx in range(i + 1, len(df)):
                        f_candle = df.iloc[future_idx]
                        if f_candle['low'] <= sl:
                            outcome = "LOSS"
                            pnl_r = -1.0
                            exit_idx = future_idx
                            break
                        elif f_candle['high'] >= tp:
                            outcome = "WIN"
                            pnl_r = target_rr
                            exit_idx = future_idx
                            break

                    trades.append({
                        "type": "LONG",
                        "time": c_time,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "score": score,
                        "outcome": outcome,
                        "pnl_r": pnl_r,
                        "duration_candles": exit_idx - i
                    })
                    break

    if not trades:
        print("\n[!] No trade setups triggered with current parameters.")
        return

    closed_trades = [t for t in trades if t["outcome"] in ("WIN", "LOSS")]
    wins = [t for t in closed_trades if t["outcome"] == "WIN"]
    losses = [t for t in closed_trades if t["outcome"] == "LOSS"]

    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
    total_r = sum(t["pnl_r"] for t in closed_trades)
    gross_profit = len(wins) * target_rr
    gross_loss = len(losses) * 1.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    print("\n" + "=" * 60)
    print("                    BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Total Setups Triggered : {len(trades)}")
    print(f"  Closed Trades          : {len(closed_trades)}")
    print(f"  Wins                   : {len(wins)}")
    print(f"  Losses                 : {len(losses)}")
    print(f"  Win Rate               : {win_rate:.1f}%")
    print(f"  Profit Factor          : {profit_factor:.2f}")
    print(f"  Net Return (R-units)   : {total_r:+.1f} R")
    print("=" * 60)

    print("\nRecent 10 Triggered Trades:")
    for t in trades[-10:]:
        status = "✅ WIN " if t["outcome"] == "WIN" else ("❌ LOSS" if t["outcome"] == "LOSS" else "⏳ OPEN")
        print(f"  {t['time']} | {t['type']:<5} | Entry: {t['entry']:.4f} | SL: {t['sl']:.4f} | TP: {t['tp']:.4f} | {status} ({t['pnl_r']:+.1f}R)")

def main():
    parser = argparse.ArgumentParser(description="Backtester for Liquidity Sweep Strategy")
    parser.add_argument("--symbol", "-s", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--interval", "-i", default="15m", help="Candle timeframe (default: 15m)")
    parser.add_argument("--limit", "-l", type=int, default=1000, help="Total historical candles (default: 1000)")
    parser.add_argument("--vol-mult", type=float, default=1.5, help="Volume spike threshold")
    parser.add_argument("--rr", type=float, default=2.0, help="Target Risk:Reward ratio (default: 2.0)")
    parser.add_argument("--min-score", type=int, default=4, help="Minimum confluence score to trigger trade")
    args = parser.parse_args()

    df = fetch_klines(args.symbol, args.interval, total_limit=args.limit)
    if not df.empty:
        run_backtest(df, vol_mult=args.vol_mult, target_rr=args.rr, min_score=args.min_score)
    else:
        print("[!] No klines received.")

if __name__ == "__main__":
    main()
