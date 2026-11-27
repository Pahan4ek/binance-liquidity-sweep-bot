"""
Historical data for the backtester.

Two sources:
- Binance Futures REST (/fapi/v1/klines): recent candles, no open interest.
- data.binance.vision: Binance's public dumps. Monthly kline archives plus daily "metrics"
  files with open interest every 5 minutes, so the backtest can score the OI factor the same
  way the live bot does. Files are cached under data/vision/.
"""
import csv
import io
import os
import time
import zipfile
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

from src.data import parse_rest_kline
from src.models import Candle

VISION = "https://data.binance.vision/data/futures/um"
REST = "https://fapi.binance.com/fapi/v1/klines"
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "vision")

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}


def fetch_rest_history(symbol: str, interval: str, total_limit: int = 1000) -> list[Candle]:
    """Downloads the latest `total_limit` closed-or-not candles from Binance Futures REST, oldest first."""
    symbol = symbol.upper()
    candles: list[Candle] = []
    end_time: int | None = None
    remaining = total_limit
    while remaining > 0:
        batch_limit = min(remaining, 1500)
        params = {"symbol": symbol, "interval": interval, "limit": batch_limit}
        if end_time:
            params["endTime"] = end_time
        resp = requests.get(REST, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            break
        batch = [parse_rest_kline(row, is_closed=True) for row in data]
        candles = batch + candles
        remaining -= len(batch)
        end_time = batch[0].open_time - 1
        if len(batch) < batch_limit:
            break
        time.sleep(0.15)
    return _dedup_sorted(candles)


def month_range(start: str, end: str) -> list[str]:
    """'2026-05', '2026-07' -> ['2026-05', '2026-06', '2026-07']"""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _download(url: str, path: str) -> str | None:
    if os.path.exists(path):
        return path
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(resp.content)
    os.replace(tmp, path)
    return path


def _read_zip_csv(path: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    # newer archives have a header row, older ones don't
    if rows and not rows[0][0].strip().lstrip("-").isdigit():
        rows = rows[1:]
    return rows


def load_vision_klines(symbol: str, interval: str, months: list[str], cache_dir: str = DEFAULT_CACHE) -> list[Candle]:
    symbol = symbol.upper()
    candles: list[Candle] = []
    for month in months:
        fname = f"{symbol}-{interval}-{month}.zip"
        url = f"{VISION}/monthly/klines/{symbol}/{interval}/{fname}"
        path = _download(url, os.path.join(cache_dir, "klines", symbol, interval, fname))
        if path is None:
            raise FileNotFoundError(f"No kline archive for {symbol} {interval} {month} on data.binance.vision")
        candles += [parse_rest_kline(row, is_closed=True) for row in _read_zip_csv(path)]
    return _dedup_sorted(candles)


def load_vision_open_interest(symbol: str, first_day: date, last_day: date,
                              cache_dir: str = DEFAULT_CACHE, workers: int = 8) -> list[tuple[int, float]]:
    """Open interest snapshots (ms timestamp, OI in base asset), every 5 minutes, sorted."""
    symbol = symbol.upper()
    days = [first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)]

    def one(day: date) -> list[tuple[int, float]]:
        fname = f"{symbol}-metrics-{day.isoformat()}.zip"
        path = _download(f"{VISION}/daily/metrics/{symbol}/{fname}", os.path.join(cache_dir, "metrics", symbol, fname))
        if path is None:
            return []
        out = []
        for row in _read_zip_csv(path):
            ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            out.append((int(ts.timestamp() * 1000), float(row[2])))
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        points = [p for chunk in pool.map(one, days) for p in chunk]
    points.sort()
    return points


def attach_open_interest(candles: list[Candle], points: list[tuple[int, float]], tolerance_ms: int = 600_000) -> int:
    """
    Sets oi_open / oi_close on each candle from the latest snapshot at or before its open and
    close. The live bot does the same with its 30-second OI poll. A snapshot older than
    `tolerance_ms` counts as missing, so gaps in the dumps leave the OI factor at 0 points
    instead of inventing a value. Returns how many candles got both values.
    """
    times = [t for t, _ in points]

    def at(ts: int) -> float | None:
        i = bisect_right(times, ts) - 1
        if i < 0 or ts - times[i] > tolerance_ms:
            return None
        return points[i][1]

    filled = 0
    for c in candles:
        c.oi_open = at(c.open_time)
        c.oi_close = at(c.close_time + 1)
        filled += c.oi_open is not None and c.oi_close is not None
    return filled


def _dedup_sorted(candles: list[Candle]) -> list[Candle]:
    by_time = {c.open_time: c for c in candles}
    return [by_time[t] for t in sorted(by_time)]
