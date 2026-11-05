# Binance Futures Liquidity Sweep & Order Flow Signal Engine

[![CI](https://github.com/Pahan4ek/binance-liquidity-sweep-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Pahan4ek/binance-liquidity-sweep-bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Binance Futures](https://img.shields.io/badge/Binance-USDT--M%20Futures-F3BA2F.svg)](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)

An algorithmic signal and monitoring engine implementing **Smart Money Concepts (SMC)** on **Binance USDT-M Futures**.

The engine monitors live kline and open interest streams, detects unmitigated fractal liquidity pools, and identifies institutional stop-runs / false breakouts backed by taker order flow volume and delta confirmation.

> **Disclaimer**: This tool is an analytical **signal and alert engine only**. It does **NOT** place live exchange orders or manage capital. Always conduct your own risk management.

---

## 🧠 Strategy Mechanics

Markets consistently target resting liquidity pools (clusters of stop-loss and breakout orders) above previous swing highs and below swing lows. Market makers frequently push price past these key levels to trigger stops, absorb liquidity, and immediately reverse back inside the range.

```text
       SWEEP HIGH (Stop-Hunt) ──┐
                                ▲ (Candle High > Swing Level)
                                │
   ─── UNMITIGATED SWING HIGH ──┼───────────────────
                                │
                                ▼ Candle Closes strictly BELOW Level!
                                │
                                └──> BEARISH SWEEP SIGNAL (SHORT)
                                     • Anomaly Volume (>= 1.5x 20-period SMA)
                                     • Negative Taker Delta (Aggressive Sellers)
                                     • Dropping Open Interest (Stops Flushed)
```

### Setup Confluence Scoring (Max 6 points)

1. **Trigger Condition (Mandatory)**: 
   * **Bearish**: Candle wick pierces above an active swing high, but closes back *below* the level.
   * **Bullish**: Candle wick pierces below an active swing low, but closes back *above* the level.
   * *True breakouts (candle closes beyond the level) invalidate the swing level and are rejected.*
2. **Volume Anomaly (+1 to +2 pts)**:
   * Volume $\ge 1.5\times$ prior 20 closed bars moving average: **+2 pts**
   * Volume $\ge 1.1\times$ prior average: **+1 pt**
3. **Taker Order Flow Delta (+1 to +2 pts)**:
   * Agrees with direction and $|\text{Delta}| / \text{Volume} \ge 10\%$: **+2 pts**
   * Agrees with direction (mild delta): **+1 pt**
4. **Open Interest Dynamics (+0 to +2 pts)**:
   * Net drop in OI across the candle: **+2 pts** (indicates stop flushes and forced position liquidations).
   * Missing / flat OI receives **0 pts** (no free score for missing data).

---

## 📁 Modular Architecture

```text
binance-liquidity-sweep-bot/
├── .github/workflows/ci.yml   # Multi-version CI testing (3.10-3.12) & linting
├── src/
│   ├── config.py             # Typed BotConfig with deepcopy & env overrides
│   ├── models.py             # Typed Candle, SwingLevel, and SweepSignal dataclasses
│   ├── strategy.py           # Pure logic: find_swings, mitigation, check_candle_for_sweeps
│   ├── data.py               # REST client, WebSocket parser, thread-safe CandleBuffer
│   ├── notify.py             # HTML Telegram alert dispatcher with response validation
│   └── bot.py                # Real-time WebSocket runner with automatic reconnection
├── tests/
│   ├── test_swings.py        # Fractal detection & level mitigation unit tests
│   ├── test_sweep_strategy.py# False breakout vs true breakout classification tests
│   └── test_backtest_math.py # Same-candle resolution & taker fee math tests
├── sweep_bot.py              # CLI entrypoint for real-time monitoring
├── backtest.py               # Historical strategy simulation with realistic fees & slippage
├── watchdog.py               # 24/7 supervisor with crash-loop backoff & git auto-updater
├── Dockerfile                # Production container deployment
├── requirements.txt          # Production dependencies
└── config.example.json       # Clean configuration template
```

---

## ⚡ Quick Start

### 1. Installation
```bash
git clone https://github.com/Pahan4ek/binance-liquidity-sweep-bot.git
cd binance-liquidity-sweep-bot
pip install -r requirements.txt
```

### 2. Run the Real-Time Signal Engine
```bash
python sweep_bot.py --symbol BTCUSDT --interval 15m
```

### 3. Run with 24/7 Watchdog Supervisor
The watchdog monitors process health, handles unexpected socket drops, and automatically restarts the engine:
```bash
python watchdog.py --symbol BTCUSDT --interval 15m
```

To enable background auto-pulling on incoming git commits:
```bash
python watchdog.py --auto-update
```

### 4. Run Strategy Backtester
Unlike naive backtesters, `backtest.py` accounts for:
* **Exchange Taker Fees** ($0.05\%$ per side = $0.10\%$ roundtrip).
* **Slippage** ($0.02\%$ per side).
* **Conservative Intra-Bar Resolution**: If a bar touches both Stop-Loss and Take-Profit, Stop-Loss is assumed first to eliminate optimistic bias.

```bash
python backtest.py --symbol BTCUSDT --interval 15m --limit 1000 --rr 2.0
```

---

## ⚙️ Configuration (`config.json`)

Copy `config.example.json` to `config.json`:

```json
{
  "symbol": "BTCUSDT",
  "interval": "15m",
  "limit": 150,
  "volume_multiplier": 1.5,
  "swing_left": 3,
  "swing_right": 3,
  "oi_check_interval": 30,
  "min_delta_ratio": 0.1,
  "cooldown_seconds": 900,
  "telegram": {
    "enabled": false,
    "bot_token": "",
    "chat_id": "",
    "min_score": 4
  },
  "backtest": {
    "taker_fee_pct": 0.05,
    "maker_fee_pct": 0.02,
    "slippage_pct": 0.02,
    "target_rr": 2.0
  }
}
```

*Telegram credentials can also be passed via environment variables:*
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

---

## 🐳 Docker Deployment

Run with Docker Compose or standalone:
```bash
docker build -t sweep-bot .
docker run -d --restart unless-stopped --name sweep-bot sweep-bot
```

---

## 🧪 Testing

Run the automated test suite:
```bash
python -m unittest discover tests
```
Or with `pytest`:
```bash
pytest tests/ -v
```

---

## 🛡️ License

MIT License. See [LICENSE](LICENSE) for details.
