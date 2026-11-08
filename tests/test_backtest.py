import io
import random
import zipfile
from unittest import mock

import pytest

import backtest
from backtest import Costs, run_backtest, simulate_trade, summarize
from src.history import _read_zip_csv, attach_open_interest, month_range
from src.models import Candle, SweepSignal
from src.strategy import check_candle_for_sweeps, find_swings, get_active_swings_near_price


def bar(i, o, h, lo, c, vol=100.0, buy=50.0):
    return Candle(open_time=i * 60_000, close_time=i * 60_000 + 59_999, open=o, high=h, low=lo, close=c,
                  volume=vol, taker_buy_vol=buy)


def long_signal(close=100.0, stop=99.0):
    return SweepSignal(direction="long", time=0, candle_close=close, level_price=99.5, extreme_price=stop,
                       stop_loss=stop, score=4)


def test_bar_touching_stop_and_target_counts_as_loss():
    candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 103, 98, 100)]
    t = simulate_trade(candles, 0, long_signal(), target_rr=2.0, costs=Costs(0, 0))
    assert t.outcome == "LOSS"
    assert t.pnl_r == pytest.approx(-1.0)


def test_fees_are_charged_on_entry_and_exit_notional():
    # entry 100, stop 99, target 102. Fees 0.05% of (100 + 102) = 0.101 -> +1.899 R instead of +2
    candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 102.5, 99.5, 102)]
    t = simulate_trade(candles, 0, long_signal(), target_rr=2.0, costs=Costs(taker_fee_pct=0.05, slippage_pct=0))
    assert t.outcome == "WIN"
    assert t.pnl_r == pytest.approx(1.899)
    assert t.cost_r == pytest.approx(0.101)


def test_slippage_hits_both_entry_and_exit():
    candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 100.5, 98, 99)]
    t = simulate_trade(candles, 0, long_signal(), target_rr=2.0, costs=Costs(taker_fee_pct=0, slippage_pct=0.1))
    assert t.entry == pytest.approx(100.1)          # bought 0.1% above the close
    assert t.exit == pytest.approx(99 * 0.999)      # stop sold 0.1% below the level
    assert t.pnl_r == pytest.approx((99 * 0.999 - 100.1) / (100.1 - 99))
    assert t.pnl_r < -1.0


def test_short_mirror():
    sig = SweepSignal(direction="short", time=0, candle_close=100.0, level_price=100.5, extreme_price=101.0,
                      stop_loss=101.0, score=4)
    candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 100.2, 97.9, 98)]
    t = simulate_trade(candles, 0, sig, target_rr=2.0, costs=Costs(0, 0))
    assert (t.outcome, t.pnl_r) == ("WIN", pytest.approx(2.0))


def test_open_trade_at_end_of_data_is_dropped():
    candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 100.5, 99.5, 100)]
    assert simulate_trade(candles, 0, long_signal(), 2.0, Costs()) is None


def test_signals_are_ignored_while_a_trade_is_open():
    # flat market; fake signals on bars 31, 33 and 40. Stops get hit on bars 35 and 45.
    candles = [bar(i, 100, 100.5, 99.5, 100) for i in range(60)]
    candles[35] = bar(35, 100, 100.2, 98.5, 99)
    candles[45] = bar(45, 100, 100.2, 98.5, 99)
    fake = {31: long_signal(), 33: long_signal(), 40: long_signal()}
    def fake_check(b, *args, **kwargs):
        return fake.get(b.open_time // 60_000)

    with mock.patch.object(backtest, "check_candle_for_sweeps", side_effect=fake_check):
        trades, skipped = run_backtest(candles, 2.0, Costs(0, 0))
    assert [t.bar for t in trades] == [31, 40]
    assert trades[0].exit_bar == 35
    assert skipped == 1


def noisy_walk(n, seed):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        c = max(1.0, p + rnd.gauss(0, 1))
        v = rnd.uniform(50, 300)
        out.append(bar(i, p, max(p, c) + abs(rnd.gauss(0, 0.8)), min(p, c) - abs(rnd.gauss(0, 0.8)), c,
                       vol=v, buy=v * rnd.uniform(0.2, 0.8)))
        p = c
    return out


@pytest.mark.parametrize("seed", range(3))
def test_signals_match_a_full_rescan_without_lookahead(seed):
    """The incremental backtest must see exactly the signals a naive bar-by-bar rescan sees."""
    candles = noisy_walk(600, seed)
    seen = []

    def record(c, i, signal, rr, costs):
        seen.append((i, signal.direction, signal.level_price, signal.score))
        return None  # never "enter", so no signal gets skipped

    with mock.patch.object(backtest, "simulate_trade", side_effect=record):
        run_backtest(candles, min_score=2)

    expected = []
    for i in range(backtest.WARMUP_BARS, len(candles) - 1):
        prior = candles[:i]
        highs, lows = get_active_swings_near_price(find_swings(prior, 3, 3), candles[i].close, 5)
        s = check_candle_for_sweeps(candles[i], prior, highs, lows, 1.5, 0.1, 2)
        if s:
            expected.append((i, s.direction, s.level_price, s.score))
    assert seen == expected
    assert expected, "test data should produce some signals"


def test_summary_drawdown_and_profit_factor():
    mk = lambda r: backtest.Trade(0, 0, 0, "long", 4, 0, 0, 0, 0, "WIN" if r > 0 else "LOSS", r, 0.1)  # noqa: E731
    s = summarize([mk(2), mk(-1), mk(-1), mk(2), mk(-1)])
    assert s["trades"] == 5
    assert s["net_r"] == pytest.approx(1.0)
    assert s["max_dd_r"] == pytest.approx(2.0)
    assert s["profit_factor"] == pytest.approx(4 / 3)


def test_open_interest_uses_last_snapshot_within_tolerance():
    candles = [bar(i, 1, 1, 1, 1) for i in range(4)]   # 1-minute bars
    points = [(0, 10.0), (60_000, 11.0), (120_000, 12.0)]  # nothing after minute 2
    filled = attach_open_interest(candles, points, tolerance_ms=90_000)
    assert [(c.oi_open, c.oi_close) for c in candles] == [(10, 11), (11, 12), (12, 12), (12, None)]
    assert filled == 3
    assert candles[0].oi_delta == pytest.approx(1.0)


@pytest.mark.parametrize("header", [True, False])
def test_vision_csv_with_and_without_header(tmp_path, header):
    rows = "1,2,3\n4,5,6\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", ("open_time,open,high\n" if header else "") + rows)
    path = tmp_path / "x.zip"
    path.write_bytes(buf.getvalue())
    assert _read_zip_csv(str(path)) == [["1", "2", "3"], ["4", "5", "6"]]


def test_month_range_crosses_year():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
