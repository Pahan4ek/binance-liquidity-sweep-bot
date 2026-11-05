import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict
import websocket

from src.config import BotConfig, load_config
from src.data import CandleBuffer, fetch_rest_klines, parse_ws_kline, fetch_current_open_interest
from src.models import Candle, SwingLevel, SweepSignal
from src.strategy import find_swings, get_active_swings_near_price, check_candle_for_sweeps
from src.notify import send_telegram_alert

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("sweep_bot")

class SweepBot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.buffer = CandleBuffer(max_size=cfg.limit + 50)
        self.swings: List[SwingLevel] = []
        self.signal_cooldown: Dict[str, float] = {}
        self.live_candle: Optional[Candle] = None
        self.last_oi: Optional[float] = None
        self.ws: Optional[websocket.WebSocketApp] = None
        self.running = False
        self._user_stopped = False

    def sync_history(self):
        logger.info("Loading %d historical klines for %s (%s)...", self.cfg.limit, self.cfg.symbol, self.cfg.interval)
        candles = fetch_rest_klines(self.cfg.symbol, self.cfg.interval, limit=self.cfg.limit)
        if not candles:
            logger.error("Failed to load historical klines. Check ticker or network.")
            return False

        self.buffer.set_batch(candles)
        self._recalculate_swings()
        logger.info("Loaded %d klines. Active swings: %d.", len(candles), len([s for s in self.swings if s.active]))
        return True

    def _recalculate_swings(self):
        closed = self.buffer.get_closed()
        self.swings = find_swings(closed, left=self.cfg.swing_left, right=self.cfg.swing_right)

    def _oi_poller(self):
        while self.running:
            oi = fetch_current_open_interest(self.cfg.symbol)
            if oi is not None:
                self.last_oi = oi
            time.sleep(self.cfg.oi_check_interval)

    def _klines_refresher(self):
        time.sleep(self.cfg.klines_refresh_interval)
        while self.running:
            candles = fetch_rest_klines(self.cfg.symbol, self.cfg.interval, limit=self.cfg.limit)
            if candles:
                self.buffer.set_batch(candles)
                self._recalculate_swings()
            time.sleep(self.cfg.klines_refresh_interval)

    def on_ws_open(self, ws):
        logger.info("WebSocket connected to %s@kline_%s", self.cfg.symbol, self.cfg.interval)
        # Re-sync klines on reconnection to fill any dropped gaps
        self.sync_history()

    def on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return

        k = data.get("k", {})
        if not k:
            return

        candle = parse_ws_kline(k)
        price = candle.close

        # Attach OI snapshots to candle
        if self.live_candle and self.live_candle.open_time == candle.open_time:
            candle.oi_open = self.live_candle.oi_open
        else:
            candle.oi_open = self.last_oi

        self.live_candle = candle
        self.buffer.upsert(candle)

        # Pulse info: nearest active levels
        active_h, active_l = get_active_swings_near_price(self.swings, price, max_candidates=1)
        h_str = f"{active_h[0].price:.4f} (+{((active_h[0].price/price)-1)*100:.2f}%)" if active_h else "None"
        l_str = f"{active_l[0].price:.4f} (-{((1-active_l[0].price/price)*100):.2f}%)" if active_l else "None"
        oi_str = f"{self.last_oi:,.0f}" if self.last_oi else "N/A"

        sys.stdout.write(
            f"\r[{datetime.now().strftime('%H:%M:%S')}] {self.cfg.symbol}: {price:.4f} "
            f"| High Near: {h_str} | Low Near: {l_str} | OI: {oi_str}    "
        )
        sys.stdout.flush()

        # Handle closed candle
        if candle.is_closed:
            print() # New line on candle close
            candle.oi_close = self.last_oi
            self.buffer.upsert(candle)
            self._recalculate_swings()

            # Run setup check
            closed_candles = self.buffer.get_closed()
            if len(closed_candles) >= 21:
                prior = closed_candles[:-1]
                active_highs, active_lows = get_active_swings_near_price(self.swings, candle.close, max_candidates=5)

                signal = check_candle_for_sweeps(
                    current_candle=candle,
                    prior_closed_candles=prior,
                    active_highs=active_highs,
                    active_lows=active_lows,
                    volume_multiplier=self.cfg.volume_multiplier,
                    min_delta_ratio=self.cfg.min_delta_ratio,
                    min_score=4
                )

                if signal:
                    self._handle_signal(signal)

    def _handle_signal(self, sig: SweepSignal):
        level_key = f"{sig.direction}_{sig.level_price:.6f}"
        now = time.time()
        last_fired = self.signal_cooldown.get(level_key, 0)

        if now - last_fired < self.cfg.cooldown_seconds:
            logger.info("Signal at level %s is in cooldown. Suppressed.", level_key)
            return

        self.signal_cooldown[level_key] = now

        dir_str = "SHORT ↓" if sig.direction == "short" else "LONG ↑"
        border = "═" * 55
        print(f"\n{border}")
        print(f"🔥 LIQUIDITY SWEEP SIGNAL: {dir_str} | {self.cfg.symbol} {self.cfg.interval}")
        print(f"{border}")
        print(f"  Swept Level : {sig.level_price:.4f}")
        print(f"  Entry Price : {sig.candle_close:.4f}")
        print(f"  Stop-Loss   : {sig.stop_loss:.4f}")
        print(f"  Score       : {sig.score}/6")
        print(f"  Confirmations:")
        for r in sig.reasons:
            print(f"    • {r}")
        print(f"{border}\n")

        # Telegram dispatch
        if self.cfg.telegram_enabled and sig.score >= self.cfg.telegram_min_score:
            send_telegram_alert(
                token=self.cfg.telegram_token,
                chat_id=self.cfg.telegram_chat_id,
                signal=sig,
                symbol=self.cfg.symbol,
                interval=self.cfg.interval
            )

    def on_ws_error(self, ws, error):
        logger.error("WebSocket encountered error: %s", error)

    def on_ws_close(self, ws, code, msg):
        logger.warning("WebSocket closed. Code: %s, Message: %s", code, msg)

    def stop(self):
        self._user_stopped = True
        self.running = False
        if self.ws:
            self.ws.close()

    def start(self):
        self.running = True

        def sig_handler(signum, frame):
            logger.info("Shutdown signal received. Stopping bot gracefully...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        if not self.sync_history():
            sys.exit(1)

        threading.Thread(target=self._oi_poller, daemon=True).start()
        threading.Thread(target=self._klines_refresher, daemon=True).start()

        stream_url = f"wss://fstream.binance.com/ws/{self.cfg.symbol.lower()}@kline_{self.cfg.interval}"
        logger.info("Connecting to Binance stream: %s", stream_url)

        self.ws = websocket.WebSocketApp(
            stream_url,
            on_open=self.on_ws_open,
            on_message=self.on_ws_message,
            on_error=self.on_ws_error,
            on_close=self.on_ws_close
        )

        try:
            # reconnect=5 enables automatic socket reconnection inside websocket-client
            self.ws.run_forever(ping_interval=20, ping_timeout=10, reconnect=5)
        except Exception as e:
            logger.error("Fatal loop exception: %s", e)
            sys.exit(1)

        if not self._user_stopped:
            # If run_forever exited on its own without user interrupt, exit with code 1 so watchdog detects it!
            logger.error("WebSocket run_forever exited unexpectedly.")
            sys.exit(1)

def run():
    cfg = load_config()
    bot = SweepBot(cfg)
    bot.start()

if __name__ == "__main__":
    run()
