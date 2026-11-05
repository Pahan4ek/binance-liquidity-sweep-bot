import unittest
from src.models import Candle, SwingLevel
from src.strategy import find_swings, get_active_swings_near_price

def make_candle(open_time: int, high: float, low: float, close: float, volume: float = 100.0) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + 60000,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_vol=volume / 2,
        is_closed=True
    )

class TestSwings(unittest.TestCase):
    def test_find_swings_fractal(self):
        prices = [
            (10, 8, 9),
            (12, 9, 11),
            (14, 10, 13),
            (20, 12, 18),  # Index 3: Peak high = 20
            (15, 10, 12),
            (13, 8, 10),
            (11, 7, 8),
        ]
        candles = [make_candle(i * 60000, h, l, c) for i, (h, l, c) in enumerate(prices)]

        swings = find_swings(candles, left=2, right=2)
        highs = [s for s in swings if s.level_type == "high"]

        self.assertEqual(len(highs), 1)
        self.assertEqual(highs[0].price, 20.0)
        self.assertTrue(highs[0].active)

    def test_swing_mitigation_on_close(self):
        prices = [
            (10, 8, 9),
            (12, 9, 11),
            (14, 10, 13),
            (20, 12, 18),  # Swing high = 20
            (15, 10, 12),
            (13, 8, 10),
            (23, 15, 22),  # Closes at 22 > 20 -> mitigates swing high
        ]
        candles = [make_candle(i * 60000, h, l, c) for i, (h, l, c) in enumerate(prices)]

        swings = find_swings(candles, left=2, right=2)
        highs = [s for s in swings if s.level_type == "high"]

        self.assertEqual(len(highs), 1)
        self.assertFalse(highs[0].active)

    def test_active_swings_sorted_by_proximity(self):
        swings = [
            SwingLevel(time=1, price=100.0, level_type="high", active=True),
            SwingLevel(time=2, price=150.0, level_type="high", active=True),
            SwingLevel(time=3, price=110.0, level_type="high", active=True),
            SwingLevel(time=4, price=90.0, level_type="low", active=True),
            SwingLevel(time=5, price=70.0, level_type="low", active=True),
        ]

        highs, lows = get_active_swings_near_price(swings, price=105.0)

        # Closest high above 105 is 110.0, then 150.0
        self.assertEqual(highs[0].price, 110.0)
        self.assertEqual(highs[1].price, 150.0)

        # Closest low below 105 is 90.0, then 70.0
        self.assertEqual(lows[0].price, 90.0)
        self.assertEqual(lows[1].price, 70.0)

if __name__ == "__main__":
    unittest.main()
