"""
Backtest of the liquidity sweep signal with fees, slippage and one position at a time.

    python backtest.py --symbol BTCUSDT --interval 15m --limit 1000            # recent candles via REST, no OI
    python backtest.py --symbols BTCUSDT,ETHUSDT --source vision --start 2026-05 --end 2026-10
"""
import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from src.history import (
    attach_open_interest,
    fetch_rest_history,
    load_vision_klines,
    load_vision_open_interest,
    month_range,
)
from src.models import Candle, SweepSignal
from src.strategy import SwingTracker, check_candle_for_sweeps, get_active_swings_near_price

WARMUP_BARS = 30  # enough for the 20-bar volume average and the first confirmed swings


@dataclass
class Costs:
    taker_fee_pct: float = 0.05  # per side
    slippage_pct: float = 0.02   # per side, applied against us on entry and exit


@dataclass
class Trade:
    bar: int
    exit_bar: int
    time: int
    direction: str
    score: int
    entry: float
    stop: float
    target: float
    exit: float
    outcome: str      # "WIN" or "LOSS"
    pnl_r: float      # net of fees and slippage, in units of initial risk
    cost_r: float     # fees + slippage, in R


def _fill(price: float, side: str, slippage_pct: float) -> float:
    """Market fill with slippage against us: buys fill higher, sells lower."""
    s = slippage_pct / 100.0
    return price * (1 + s) if side == "buy" else price * (1 - s)


def simulate_trade(candles: list[Candle], i: int, signal: SweepSignal, target_rr: float, costs: Costs) -> Trade | None:
    """
    Enters at the close of signal bar i and walks forward until stop or target.
    If one bar touches both, the stop wins (no intrabar data, so assume the worse case).
    Returns None when the trade never closes inside the data or the stop sits on the wrong side.
    """
    long = signal.direction == "long"
    entry = _fill(signal.candle_close, "buy" if long else "sell", costs.slippage_pct)
    stop = signal.stop_loss
    risk = entry - stop if long else stop - entry
    if risk <= 0:
        return None
    target = entry + risk * target_rr if long else entry - risk * target_rr

    for j in range(i + 1, len(candles)):
        c = candles[j]
        hit_stop = c.low <= stop if long else c.high >= stop
        hit_target = c.high >= target if long else c.low <= target
        if not (hit_stop or hit_target):
            continue
        outcome, level = ("LOSS", stop) if hit_stop else ("WIN", target)
        exit_price = _fill(level, "sell" if long else "buy", costs.slippage_pct)
        gross = exit_price - entry if long else entry - exit_price
        fees = (entry + exit_price) * costs.taker_fee_pct / 100.0
        ideal = (level - signal.candle_close) if long else (signal.candle_close - level)
        pnl_r = (gross - fees) / risk
        return Trade(bar=i, exit_bar=j, time=candles[i].open_time,
                     direction=signal.direction, score=signal.score, entry=entry, stop=stop, target=target,
                     exit=exit_price, outcome=outcome, pnl_r=pnl_r, cost_r=(ideal - gross + fees) / risk)
    return None


def run_backtest(candles: list[Candle], target_rr: float = 2.0, costs: Costs | None = None,
                 vol_mult: float = 1.5, min_delta: float = 0.1, min_score: int = 4,
                 swing_left: int = 3, swing_right: int = 3) -> tuple[list[Trade], int]:
    """
    Walks the candles once. Signals use only data up to the signal bar: swings come from the
    bars before it, the volume average from the 20 bars before it. While a trade is open new
    signals are ignored. Returns (closed trades, signals skipped because a trade was open).
    """
    costs = costs or Costs()
    tracker = SwingTracker(swing_left, swing_right)
    trades: list[Trade] = []
    busy_until = -1
    skipped = 0

    for i in range(WARMUP_BARS, len(candles) - 1):
        tracker.update(candles, i)
        bar = candles[i]
        highs, lows = get_active_swings_near_price(tracker.swings, bar.close, max_candidates=5)
        signal = check_candle_for_sweeps(bar, candles[i - 20:i], highs, lows, volume_multiplier=vol_mult,
                                         min_delta_ratio=min_delta, min_score=min_score)
        if not signal:
            continue
        if i <= busy_until:
            skipped += 1
            continue
        trade = simulate_trade(candles, i, signal, target_rr, costs)
        if trade:
            trades.append(trade)
            busy_until = trade.exit_bar
    return trades, skipped


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    wins = [t for t in trades if t.outcome == "WIN"]
    gross_win = sum(t.pnl_r for t in trades if t.pnl_r > 0)
    gross_loss = -sum(t.pnl_r for t in trades if t.pnl_r < 0)
    equity = peak = max_dd = 0.0
    for t in trades:
        equity += t.pnl_r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "net_r": equity,
        "expectancy_r": equity / len(trades),
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
        "max_dd_r": max_dd,
        "avg_cost_r": sum(t.cost_r for t in trades) / len(trades),
    }


def _fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def load(args, symbol: str) -> tuple[list[Candle], str]:
    if args.source == "rest":
        return fetch_rest_history(symbol, args.interval, total_limit=args.limit), "REST, no OI"
    months = month_range(args.start, args.end)
    candles = load_vision_klines(symbol, args.interval, months)
    note = "Binance Vision"
    if not args.no_oi and candles:
        first = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc).date()
        last = datetime.fromtimestamp(candles[-1].close_time / 1000, tz=timezone.utc).date()
        points = load_vision_open_interest(symbol, first, min(last, datetime.now(timezone.utc).date()))
        filled = attach_open_interest(candles, points)
        note += f", OI on {filled / len(candles) * 100:.0f}% of bars"
    return candles, note


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest for the liquidity sweep signal")
    p.add_argument("--symbol", "-s", default="BTCUSDT")
    p.add_argument("--symbols", help="comma-separated list, overrides --symbol")
    p.add_argument("--interval", "-i", default="15m")
    p.add_argument("--source", choices=["rest", "vision"], default="rest",
                   help="rest: latest candles from fapi, no open interest. vision: monthly archives "
                        "from data.binance.vision with 5-minute open interest")
    p.add_argument("--limit", "-l", type=int, default=1000, help="candles to load with --source rest")
    p.add_argument("--start", help="first month for --source vision, e.g. 2026-05")
    p.add_argument("--end", help="last month for --source vision, e.g. 2026-10")
    p.add_argument("--no-oi", action="store_true", help="skip open interest with --source vision")
    p.add_argument("--rr", type=float, default=2.0, help="reward:risk of the target (default 2.0)")
    p.add_argument("--min-score", type=int, default=4, help="minimum signal score out of 6 (default 4)")
    p.add_argument("--taker-fee", type=float, default=0.05, help="taker fee %% per side (default 0.05)")
    p.add_argument("--slippage", type=float, default=0.02, help="slippage %% per side (default 0.02)")
    p.add_argument("--markdown", action="store_true", help="print one markdown table row per symbol")
    args = p.parse_args()
    if args.source == "vision" and not (args.start and args.end):
        p.error("--source vision needs --start and --end")

    symbols = [s.strip().upper() for s in (args.symbols or args.symbol).split(",") if s.strip()]
    costs = Costs(args.taker_fee, args.slippage)
    if args.markdown:
        print("| Symbol | Bars | Trades | Win rate | Expectancy | Net | Profit factor | Max DD | Avg cost |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    for symbol in symbols:
        candles, note = load(args, symbol)
        if len(candles) < WARMUP_BARS + 2:
            print(f"[!] {symbol}: not enough data", file=sys.stderr)
            continue
        trades, skipped = run_backtest(candles, args.rr, costs, min_score=args.min_score)
        s = summarize(trades)
        if args.markdown:
            if s["trades"]:
                print(f"| {symbol} | {len(candles):,} | {s['trades']} | {s['win_rate']:.1f}% "
                      f"| {s['expectancy_r']:+.3f} R | {s['net_r']:+.1f} R | {s['profit_factor']:.2f} "
                      f"| {s['max_dd_r']:.1f} R | {s['avg_cost_r']:.2f} R |")
            else:
                print(f"| {symbol} | {len(candles):,} | 0 | – | – | – | – | – | – |")
            continue

        print(f"\n{symbol} {args.interval}: {_fmt_time(candles[0].open_time)} -> {_fmt_time(candles[-1].open_time)} "
              f"({len(candles):,} bars, {note})")
        print(f"Target 1:{args.rr} | min score {args.min_score}/6 | taker {args.taker_fee}% + slippage "
              f"{args.slippage}% per side | signals skipped while in a trade: {skipped}")
        if not s["trades"]:
            print("No trades.")
            continue
        print(f"  Trades          {s['trades']}")
        print(f"  Win rate        {s['win_rate']:.1f}%")
        print(f"  Expectancy      {s['expectancy_r']:+.3f} R per trade")
        print(f"  Net             {s['net_r']:+.2f} R")
        print(f"  Profit factor   {s['profit_factor']:.2f}")
        print(f"  Max drawdown    {s['max_dd_r']:.2f} R")
        print(f"  Avg cost        {s['avg_cost_r']:.3f} R per trade (fees + slippage)")
        for t in trades[-5:]:
            print(f"    {_fmt_time(t.time)} {t.direction:<5} score {t.score} entry {t.entry:.4f} "
                  f"stop {t.stop:.4f} -> {t.outcome} {t.pnl_r:+.2f} R")
    return 0


if __name__ == "__main__":
    sys.exit(main())
