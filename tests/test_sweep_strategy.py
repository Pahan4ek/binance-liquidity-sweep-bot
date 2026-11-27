import unittest

from src.models import Candle, SwingLevel
from src.strategy import check_candle_for_sweeps


def make_candle(open_time: int, high: float, low: float, close: float,
                volume: float = 100.0, taker_buy: float = 50.0) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + 60000,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_vol=taker_buy,
        is_closed=True
    )

class TestSweepStrategy(unittest.TestCase):
    def test_bearish_sweep_detection(self):
        prior = [make_candle(i * 60000, 105, 95, 100, volume=100.0) for i in range(25)]
        active_highs = [SwingLevel(time=1000, price=110.0, level_type="high", active=True)]

        current = Candle(
            open_time=26 * 60000,
            close_time=27 * 60000,
            open=106.0,
            high=112.0,      # Wick above 110
            low=105.0,
            close=108.0,     # Closes below 110 -> Bearish false breakout
            volume=250.0,    # 2.5x volume
            taker_buy_vol=50.0 # 50 buy vs 200 sell -> delta = -150
        )

        signal = check_candle_for_sweeps(
            current_candle=current,
            prior_closed_candles=prior,
            active_highs=active_highs,
            active_lows=[],
            volume_multiplier=1.5,
            min_delta_ratio=0.1,
            min_score=4
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "short")
        self.assertEqual(signal.level_price, 110.0)
        self.assertEqual(signal.extreme_price, 112.0)
        self.assertEqual(signal.stop_loss, 112.0)
        self.assertGreaterEqual(signal.score, 4)

    def test_true_breakout_not_detected_as_sweep(self):
        prior = [make_candle(i * 60000, 105, 95, 100, volume=100.0) for i in range(25)]
        active_highs = [SwingLevel(time=1000, price=110.0, level_type="high", active=True)]

        current = Candle(
            open_time=26 * 60000,
            close_time=27 * 60000,
            open=106.0,
            high=115.0,
            low=105.0,
            close=114.0,     # Closed ABOVE 110 (expansion, not sweep)
            volume=300.0,
            taker_buy_vol=250.0
        )

        signal = check_candle_for_sweeps(
            current_candle=current,
            prior_closed_candles=prior,
            active_highs=active_highs,
            active_lows=[],
            min_score=4
        )

        self.assertIsNone(signal)

if __name__ == "__main__":
    unittest.main()
