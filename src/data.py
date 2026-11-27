import threading

import requests

from src.models import Candle


def parse_rest_kline(k: list, is_closed: bool = True) -> Candle:
    """
    Parses a Binance Futures REST kline array into a typed Candle.
    k[0]: open_time (ms)
    k[1]: open
    k[2]: high
    k[3]: low
    k[4]: close
    k[5]: volume (base)
    k[6]: close_time (ms)
    k[9]: taker_buy_vol (base)
    """
    return Candle(
        open_time=int(k[0]),
        close_time=int(k[6]),
        open=float(k[1]),
        high=float(k[2]),
        low=float(k[3]),
        close=float(k[4]),
        volume=float(k[5]),
        taker_buy_vol=float(k[9]),
        is_closed=is_closed
    )

def parse_ws_kline(k: dict) -> Candle:
    """
    Parses a Binance Futures WebSocket @kline event payload.
    k['t']: open_time (ms)
    k['T']: close_time (ms)
    k['o']: open
    k['h']: high
    k['l']: low
    k['c']: close
    k['v']: volume
    k['V']: taker_buy_vol
    k['x']: is_closed (bool)
    """
    return Candle(
        open_time=int(k['t']),
        close_time=int(k['T']),
        open=float(k['o']),
        high=float(k['h']),
        low=float(k['l']),
        close=float(k['c']),
        volume=float(k['v']),
        taker_buy_vol=float(k['V']),
        is_closed=bool(k['x'])
    )

class CandleBuffer:
    """
    Thread-safe buffer that upserts candles by open_time.
    Prevents duplicate bars between REST initial state and live WebSocket events.
    """
    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._candles: list[Candle] = []
        self._lock = threading.Lock()

    def upsert(self, candle: Candle):
        with self._lock:
            # Check if candle with same open_time already exists
            for idx in range(len(self._candles) - 1, -1, -1):
                if self._candles[idx].open_time == candle.open_time:
                    # Update in place
                    # Preserve existing OI tracking if not supplied
                    if candle.oi_open is None:
                        candle.oi_open = self._candles[idx].oi_open
                    if candle.oi_close is None:
                        candle.oi_close = self._candles[idx].oi_close
                    self._candles[idx] = candle
                    return

            # If not found, append and sort
            self._candles.append(candle)
            self._candles.sort(key=lambda c: c.open_time)
            if len(self._candles) > self.max_size:
                self._candles = self._candles[-self.max_size:]

    def get_all(self) -> list[Candle]:
        with self._lock:
            return list(self._candles)

    def get_closed(self) -> list[Candle]:
        with self._lock:
            return [c for c in self._candles if c.is_closed]

    def set_batch(self, candles: list[Candle]):
        with self._lock:
            unique_map: dict[int, Candle] = {}
            for c in candles:
                unique_map[c.open_time] = c
            sorted_list = sorted(unique_map.values(), key=lambda c: c.open_time)
            self._candles = sorted_list[-self.max_size:]

def fetch_rest_klines(symbol: str, interval: str, limit: int = 150) -> list[Candle]:
    """
    Fetches latest klines from Binance Futures REST API.
    Marks all except the last item as is_closed=True.
    """
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if not isinstance(data, list):
            return []

        candles = []
        total = len(data)
        for i, row in enumerate(data):
            is_closed = (i < total - 1)
            candles.append(parse_rest_kline(row, is_closed=is_closed))
        return candles
    except Exception as e:
        print(f"[!] Error loading REST klines: {e}")
        return []

def fetch_current_open_interest(symbol: str) -> float | None:
    url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol.upper()}"
    try:
        r = requests.get(url, timeout=5).json()
        return float(r.get("openInterest", 0))
    except Exception:
        return None
