# Binance Futures liquidity sweep signals

[![CI](https://github.com/Pahan4ek/binance-liquidity-sweep-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Pahan4ek/binance-liquidity-sweep-bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A signal bot for Binance USDT-M futures. It watches the live kline stream and open interest, tracks unmitigated swing highs and lows, and flags candles that wick through a level and close back inside it (a sweep, or false breakout). Each sweep is scored on volume, taker delta and open interest, and strong ones go to Telegram. A backtester runs the same signal code over months of history with fees and slippage.

It only sends signals. It never places orders.

## The signal

```text
   wick above the level ──┐
                          │  high > swing high
   ── active swing high ──┼────────────────────
                          │  close < swing high
                          └─> short sweep, scored 0-6
```

A swing high is a fractal pivot: higher than the 3 bars on each side. It stays active until a later candle closes above it. Swing lows mirror that. A candle that wicks through an active level and closes back inside triggers a check. A candle that closes beyond the level is a breakout: it deactivates the level and gives no signal.

| Factor | Points |
|---|---|
| Volume vs the previous 20 closed bars | 2 if ≥ 1.5×, 1 if ≥ 1.1× |
| Taker delta in the signal direction | 2 if \|delta\| / volume ≥ 10%, 1 if smaller |
| Open interest fell during the candle | 2. Missing OI scores 0, never a free point |

Alerts need 4 of 6 by default. Stop is the sweep wick.

## Backtest results

15m candles, January to October 2026 (29,088 bars per symbol), open interest from Binance's public dumps, so the OI factor is scored the same way as live. Entry at the signal close, stop at the wick, target 2R. Taker fee 0.05% and slippage 0.02% on both entry and exit. One position at a time.

| Symbol | Trades | Win rate | Expectancy | Net | Profit factor | Max DD | Avg cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 797 | 28.2% | −0.795 R | −633.9 R | 0.33 | 635.0 R | 0.75 R |
| ETHUSDT | 812 | 30.0% | −0.580 R | −471.0 R | 0.45 | 473.5 R | 0.56 R |
| SOLUSDT | 820 | 32.1% | −0.506 R | −414.9 R | 0.51 | 421.8 R | 0.55 R |
| BNBUSDT | 868 | 29.3% | −0.820 R | −711.8 R | 0.33 | 711.8 R | 0.81 R |
| XRPUSDT | 821 | 33.9% | −0.465 R | −381.9 R | 0.53 | 383.7 R | 0.56 R |
| DOGEUSDT | 764 | 31.0% | −0.490 R | −374.2 R | 0.51 | 374.2 R | 0.49 R |

**It loses money, and the table shows why.** The same run with zero fees and zero slippage is roughly break-even: expectancy from −0.09 R (BTC) to +0.09 R (XRP), profit factor 0.87 to 1.13. The raw signal has no real edge at 1:2, and costs do the rest. A 15m sweep wick is small, so the stop often sits 0.1–0.2% from entry. A 0.14% round trip then eats half a risk unit or more on every trade (the Avg cost column).

On 1h candles the stops are wider and costs fall to 0.2–0.4 R, but the result is still negative on 5 of 6 symbols (expectancy −0.42 R to +0.01 R).

Reproduce:
```bash
python backtest.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT \
  --source vision --start 2026-01 --end 2026-10 --markdown
# add --taker-fee 0 --slippage 0 for the gross numbers, --interval 1h for the 1h run
```

What the backtester assumes:
- Signals use only data available at the signal close. Swings come from earlier bars, the volume average from the 20 bars before. A test checks this against a naive bar-by-bar rescan.
- If one bar touches both stop and target, it counts as a loss.
- New signals are ignored while a trade is open. The number skipped is printed.
- Trades still open when the data ends are dropped.

## Usage

```bash
pip install -r requirements.txt
cp config.example.json config.json     # optional, defaults work

python sweep_bot.py --symbol BTCUSDT --interval 15m     # live signals in the console
python watchdog.py --symbol BTCUSDT --interval 15m      # same, restarted on crash with backoff
python backtest.py --symbol BTCUSDT --limit 1500        # quick backtest on recent candles (REST, no OI)
```

Telegram alerts turn on when both env vars are set:
```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
```

`watchdog.py --auto-update` runs `git pull` on a timer and restarts the bot on new commits. Use it only on a branch you control, since it runs whatever lands there.

Docker:
```bash
docker build -t sweep-bot .
docker run -d --restart unless-stopped -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... sweep-bot
```

## Data sources

| Source | Used by | Open interest |
|---|---|---|
| `wss://fstream.binance.com` kline stream | live bot | polled from `/fapi/v1/openInterest` every 30 s |
| `/fapi/v1/klines` (`--source rest`) | live bot history, quick backtests | none, so the OI factor scores 0 |
| [data.binance.vision](https://data.binance.vision) (`--source vision`) | long backtests | 5-minute snapshots from the daily metrics files |

Vision archives are cached in `data/vision/`. The REST endpoints are geo-restricted in some countries; the archives are not.

## Layout

```text
src/strategy.py   swing detection (full scan for live, incremental SwingTracker for backtests), sweep scoring
src/data.py       kline parsing, thread-safe candle buffer, REST helpers
src/history.py    historical candles from REST or data.binance.vision, OI attachment
src/bot.py        WebSocket runner with reconnects and OI polling
src/notify.py     Telegram alerts
backtest.py       simulation, costs, stats, CLI
watchdog.py       restart supervisor
tests/            strategy, swing tracker equivalence, backtest mechanics, data parsing
```

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```

## License

MIT
