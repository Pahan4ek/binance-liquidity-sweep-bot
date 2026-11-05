import argparse
import sys
from src.config import load_config
from src.bot import SweepBot

def main():
    parser = argparse.ArgumentParser(description="Binance Futures Liquidity Sweep & Order Flow Bot")
    parser.add_argument("--symbol", "-s", type=str, help="Trading pair ticker (e.g. BTCUSDT, ETHUSDT)")
    parser.add_argument("--interval", "-i", type=str, help="Candle interval (e.g. 5m, 15m, 1h)")
    parser.add_argument("--vol-mult", type=float, help="Volume spike multiplier threshold")
    parser.add_argument("--min-delta", type=float, help="Minimum delta/volume ratio")
    args = parser.parse_args()

    cfg = load_config()
    if args.symbol:
        cfg.symbol = args.symbol.upper()
    if args.interval:
        cfg.interval = args.interval.lower()
    if args.vol_mult:
        cfg.volume_multiplier = args.vol_mult
    if args.min_delta:
        cfg.min_delta_ratio = args.min_delta

    bot = SweepBot(cfg)
    bot.start()

if __name__ == "__main__":
    main()
