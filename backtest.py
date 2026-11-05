import argparse
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from src.models import Candle, SwingLevel, SweepSignal
from src.strategy import find_swings, get_active_swings_near_price, check_candle_for_sweeps
from src.data import parse_rest_kline

def fetch_historical_candles(symbol: str, interval: str, total_limit: int = 1000) -> List[Candle]:
    """
    Downloads historical candles from Binance Futures REST API in batches up to 1500 per call.
    """
    symbol = symbol.upper()
    candles: List[Candle] = []
    end_time: Optional[int] = None
    remaining = total_limit

    print(f"[*] Downloading {total_limit} candles for {symbol} ({interval})...")

    while remaining > 0:
        batch_limit = min(remaining, 1000)
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={batch_limit}"
        if end_time:
            url += f"&endTime={end_time}"

        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if not isinstance(data, list) or len(data) == 0:
                break

            batch = [parse_rest_kline(row, is_closed=True) for row in data]
            candles = batch + candles
            remaining -= len(batch)
            end_time = batch[0].open_time - 1

            if len(batch) < batch_limit:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"[!] Network error downloading candles: {e}")
            break

    # Deduplicate by open_time and sort
    seen = set()
    unique_candles = []
    for c in candles:
        if c.open_time not in seen:
            seen.add(c.open_time)
            unique_candles.append(c)

    unique_candles.sort(key=lambda c: c.open_time)
    return unique_candles

def run_simulation(
    candles: List[Candle],
    target_rr: float = 2.0,
    taker_fee_pct: float = 0.05,
    slippage_pct: float = 0.02,
    vol_mult: float = 1.5,
    min_delta: float = 0.1,
    min_score: int = 4,
    cooldown_candles: int = 1
):
    total_bars = len(candles)
    if total_bars < 50:
        print("[!] Insufficient data for simulation.")
        return

    roundtrip_friction = (taker_fee_pct * 2 + slippage_pct * 2) / 100.0  # as decimal e.g. 0.0014

    print(f"[*] Simulating strategy over {total_bars} bars...")
    start_dt = datetime.fromtimestamp(candles[0].open_time / 1000).strftime('%Y-%m-%d %H:%M')
    end_dt = datetime.fromtimestamp(candles[-1].open_time / 1000).strftime('%Y-%m-%d %H:%M')
    print(f"    Range: {start_dt} -> {end_dt}")
    print(f"    Settings: Target R:R 1:{target_rr} | Taker Fee: {taker_fee_pct}% | Slippage: {slippage_pct}%")
    print(f"    Friction per roundtrip trade: {roundtrip_friction * 100:.2f}% of position notional")

    trades: List[Dict[str, Any]] = []
    cooldown_until_bar = 0

    for i in range(30, total_bars - 1):
        if i < cooldown_until_bar:
            continue

        eval_candle = candles[i]
        prior_closed = candles[:i]

        # Calculate historical swings strictly up to current bar (no lookahead!)
        swings = find_swings(prior_closed, left=3, right=3)
        active_highs, active_lows = get_active_swings_near_price(swings, eval_candle.close, max_candidates=5)

        signal = check_candle_for_sweeps(
            current_candle=eval_candle,
            prior_closed_candles=prior_closed,
            active_highs=active_highs,
            active_lows=active_lows,
            volume_multiplier=vol_mult,
            min_delta_ratio=min_delta,
            min_score=min_score
        )

        if not signal:
            continue

        cooldown_until_bar = i + cooldown_candles

        raw_entry = signal.candle_close
        if signal.direction == "long":
            entry = raw_entry * (1.0 + slippage_pct / 100.0)
            sl = signal.stop_loss
            raw_risk = entry - sl
            if raw_risk <= 0:
                continue
            tp = entry + (raw_risk * target_rr)
        else:
            entry = raw_entry * (1.0 - slippage_pct / 100.0)
            sl = signal.stop_loss
            raw_risk = sl - entry
            if raw_risk <= 0:
                continue
            tp = entry - (raw_risk * target_rr)

        # Simulate trade execution forward
        outcome = "OPEN"
        exit_bar = i
        pnl_r = 0.0

        for f_idx in range(i + 1, total_bars):
            f_candle = candles[f_idx]
            exit_bar = f_idx

            if signal.direction == "long":
                hit_sl = f_candle.low <= sl
                hit_tp = f_candle.high >= tp

                # If both touched in the same candle, conservatively assume Stop-Loss was hit first
                if hit_sl and hit_tp:
                    outcome = "LOSS"
                    exit_price = sl
                    break
                elif hit_sl:
                    outcome = "LOSS"
                    exit_price = sl
                    break
                elif hit_tp:
                    outcome = "WIN"
                    exit_price = tp
                    break
            else:
                hit_sl = f_candle.high >= sl
                hit_tp = f_candle.low <= tp

                if hit_sl and hit_tp:
                    outcome = "LOSS"
                    exit_price = sl
                    break
                elif hit_sl:
                    outcome = "LOSS"
                    exit_price = sl
                    break
                elif hit_tp:
                    outcome = "WIN"
                    exit_price = tp
                    break

        if outcome in ("WIN", "LOSS"):
            # Calculate net R after exchange taker fees and slippage
            friction_cost_currency = (entry + exit_price) * (taker_fee_pct / 100.0)
            friction_r = friction_cost_currency / raw_risk

            if outcome == "WIN":
                pnl_r = target_rr - friction_r
            else:
                pnl_r = -1.0 - friction_r

            trades.append({
                "bar": i,
                "time": datetime.fromtimestamp(eval_candle.open_time / 1000).strftime('%Y-%m-%d %H:%M'),
                "direction": signal.direction.upper(),
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "score": signal.score,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "friction_r": friction_r,
                "duration_bars": exit_bar - i
            })

    if not trades:
        print("[!] No trades generated with given parameters.")
        return

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    win_rate = (len(wins) / len(trades)) * 100.0

    net_r_total = sum(t["pnl_r"] for t in trades)
    expectancy_r = net_r_total / len(trades)

    gross_win_r = sum(t["pnl_r"] for t in wins)
    gross_loss_r = abs(sum(t["pnl_r"] for t in losses))
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 99.0

    # Max drawdown in R
    cumulative_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cumulative_r += t["pnl_r"]
        if cumulative_r > peak_r:
            peak_r = cumulative_r
        dd = peak_r - cumulative_r
        if dd > max_dd_r:
            max_dd_r = dd

    print("\n" + "=" * 65)
    print("                    BACKTEST PERFORMANCE REPORT")
    print("=" * 65)
    print(f"  Total Trades (Closed)   : {len(trades)}")
    print(f"  Win / Loss Count        : {len(wins)} Wins / {len(losses)} Losses")
    print(f"  Win Rate                : {win_rate:.1f}%")
    print(f"  Target R:R              : 1:{target_rr:.1f}")
    print(f"  Profit Factor           : {profit_factor:.2f}")
    print(f"  Expectancy (EV / trade) : {expectancy_r:+.2f} R")
    print(f"  Net Total Profit        : {net_r_total:+.2f} R  (after fees & slippage)")
    print(f"  Max Drawdown            : -{max_dd_r:.2f} R")
    print("=" * 65)

    print("\nRecent 8 Trades:")
    for t in trades[-8:]:
        icon = "✅ WIN " if t["outcome"] == "WIN" else "❌ LOSS"
        print(f"  {t['time']} | {t['direction']:<5} | Entry: {t['entry']:.2f} | SL: {t['sl']:.2f} | {icon} ({t['pnl_r']:+.2f}R, fee: -{t['friction_r']:.2f}R)")

def main():
    parser = argparse.ArgumentParser(description="Realistic Backtest for Liquidity Sweep Strategy")
    parser.add_argument("--symbol", "-s", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--interval", "-i", default="15m", help="Candle timeframe (default: 15m)")
    parser.add_argument("--limit", "-l", type=int, default=1000, help="Number of candles (default: 1000)")
    parser.add_argument("--rr", type=float, default=2.0, help="Risk:Reward ratio (default: 2.0)")
    parser.add_argument("--taker-fee", type=float, default=0.05, help="Taker fee %% per side (default: 0.05)")
    parser.add_argument("--slippage", type=float, default=0.02, help="Slippage %% per side (default: 0.02)")
    args = parser.parse_args()

    candles = fetch_historical_candles(args.symbol, args.interval, total_limit=args.limit)
    if candles:
        run_simulation(
            candles,
            target_rr=args.rr,
            taker_fee_pct=args.taker_fee,
            slippage_pct=args.slippage
        )

if __name__ == "__main__":
    main()
