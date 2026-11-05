import unittest
from src.models import Candle
from src.strategy import find_swings, get_active_swings_near_price, check_candle_for_sweeps

class TestBacktestMath(unittest.TestCase):
    def test_fee_impact_on_r_units(self):
        # Scenario: Entry 100, Stop Loss 99 -> Risk = 1.0 (1.0% of price)
        # Target TP = 102 -> Target R:R = 1:2.0
        # Taker fee = 0.05% per side -> roundtrip fee on notional ~ 0.10%
        # Friction in currency: (100 + 102) * 0.0005 = 0.101
        # Net profit in currency: (102 - 100) - 0.101 = 1.899
        # Net R: 1.899 / 1.0 = +1.899 R (instead of +2.0 R)
        entry = 100.0
        sl = 99.0
        tp = 102.0
        taker_fee_pct = 0.05
        raw_risk = entry - sl

        friction_cost = (entry + tp) * (taker_fee_pct / 100.0)
        net_profit = (tp - entry) - friction_cost
        net_r = net_profit / raw_risk

        self.assertAlmostEqual(net_r, 1.899, places=3)
        self.assertLess(net_r, 2.0)

    def test_conservative_same_candle_resolution(self):
        # If a single candle has low <= sl AND high >= tp,
        # conservative backtest must resolve as LOSS to prevent forward-looking bias
        entry = 100.0
        sl = 99.0
        tp = 102.0

        bar_low = 98.5  # pierced SL
        bar_high = 102.5 # pierced TP

        hit_sl = bar_low <= sl
        hit_tp = bar_high >= tp

        outcome = "LOSS" if (hit_sl and hit_tp) else ("WIN" if hit_tp else "LOSS")
        self.assertEqual(outcome, "LOSS")

if __name__ == "__main__":
    unittest.main()
