import random

import pytest

from src.models import Candle
from src.strategy import SwingTracker, find_swings


def random_walk(n: int, seed: int) -> list[Candle]:
    rnd = random.Random(seed)
    price, out = 100.0, []
    for i in range(n):
        o = price
        c = max(1.0, o + rnd.gauss(0, 1))
        h = max(o, c) + abs(rnd.gauss(0, 0.6))
        lo = min(o, c) - abs(rnd.gauss(0, 0.6))
        # coarse rounding produces equal highs/lows, which exercises the strict comparisons
        out.append(Candle(open_time=i * 60_000, close_time=i * 60_000 + 59_999, open=round(o, 1), high=round(h, 1),
                          low=round(lo, 1), close=round(c, 1), volume=100.0, taker_buy_vol=50.0))
        price = c
    return out


def snapshot(swings):
    return [(s.bar_index, s.level_type, s.price, s.active, s.mitigated_at) for s in swings]


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("left,right", [(3, 3), (2, 5), (5, 2)])
def test_tracker_matches_full_rescan_at_every_bar(seed, left, right):
    candles = random_walk(400, seed)
    tracker = SwingTracker(left, right)
    for n in range(len(candles) + 1):
        tracker.update(candles, n)
        assert snapshot(tracker.swings) == snapshot(find_swings(candles[:n], left, right)), f"diverged at n={n}"


def test_update_is_idempotent_and_can_skip_ahead():
    candles = random_walk(300, 42)
    stepwise = SwingTracker()
    for n in range(0, 301, 7):
        stepwise.update(candles, n)
    stepwise.update(candles, 300)
    stepwise.update(candles, 300)
    assert snapshot(stepwise.swings) == snapshot(find_swings(candles, 3, 3))
