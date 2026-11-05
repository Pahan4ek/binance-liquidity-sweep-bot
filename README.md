# ⚡ Binance Futures Liquidity Sweep & Order Flow Bot

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Binance Futures](https://img.shields.io/badge/Binance-USDT--M%20Futures-F3BA2F.svg)](https://www.binance.com)
[![WebSocket](https://img.shields.io/badge/Stream-Real--Time%20WebSocket-brightgreen.svg)]()

An institutional-grade algorithmic trading suite implementing **Smart Money Concepts (SMC)** on **Binance USDT-M Futures**. 

The bot identifies institutional stop hunts and liquidity sweeps in real time by combining **fractal swing levels**, **abnormal volume spikes**, **taker order flow delta**, and **open interest (OI) dynamics**. It includes a **24/7 supervisor watchdog with auto-recovery**, a **strategy backtester**, and optional **Telegram notifications**.

---

## 🧠 Strategy Architecture: Smart Money Liquidity Sweep

Markets are driven by liquidity pools resting above prior highs and below prior lows (stop-loss orders & breakout entries). Market makers frequently push price past these key levels to trigger stops, absorb liquidity, and immediately reverse.

```text
       SWEEP HIGH (Stop-Hunt) ──┐
                                ▲ (High > Swing Level)
                                │
   ─── SWING HIGH LEVEL ────────┼───────────────────
                                │
                                ▼ Candle Closes BELOW Level!
                                │
                                └──> BEARISH SWEEP SIGNAL (SHORT)
                                     + High Volume (x1.5+)
                                     + Negative Taker Delta (Aggressive Sellers)
                                     + Dropping Open Interest (Stops Cleared)
```

### Confluence Factors (Score up to 6/6):
1. **False Breakout**: The candle wick pierces the fractal level, but closes strictly back inside the range.
2. **Anomaly Volume**: Current volume exceeds the 20-period moving average by a configurable multiplier (e.g. `1.5x`).
3. **Cumulative Delta**: Evaluates aggressive taker buyer vs. seller pressure (`taker_buy_vol` vs `taker_sell_vol`). A bearish sweep requires negative delta; bullish requires positive delta.
4. **Open Interest (OI) Flush**: Falling OI during the sweep confirms stops were liquidated and positions were forced closed, validating the trap.
5. **Dynamic Risk Control**: Places stop-loss exactly at the candle's wick extreme, protecting capital with minimal invalidation distances.

---

## 🚀 Key Modules

* **`sweep_bot.py`**: Real-time bot connected to Binance Futures WebSocket stream (`@kline`). Features tick-by-tick price monitoring, dynamic distance calculations to nearest liquidity pools, and live score evaluation.
* **`watchdog.py`**: Production supervisor keeping the bot running 24/7. Automatically restarts upon network drops or unhandled exceptions, and supports `--auto-update` to automatically pull git changes and hot-restart without downtime.
* **`backtest.py`**: No-lookahead historical simulator. Fetches up to thousands of historical candles via REST, simulates real-time swing discovery, calculates win rate %, profit factor, and net R-unit returns.
* **`telegram` alerts**: Instantly dispatches Markdown cards with entry price, stop-loss, and confluence factors when a high-probability signal triggers.

---

## ⚡ Quick Start

### 1. Installation
```bash
git clone https://github.com/your-username/binance-liquidity-sweep-bot.git
cd binance-liquidity-sweep-bot
pip install -r requirements.txt
```

### 2. Run the Real-Time Bot
On Windows, simply double-click **`run.bat`**, or launch directly:
```bash
python sweep_bot.py --symbol BTCUSDT --interval 15m
```

### 3. Run with 24/7 Watchdog Supervisor
```bash
python watchdog.py --symbol ETHUSDT --interval 5m
```
To enable automated remote updates:
```bash
python watchdog.py --auto-update
```

### 4. Run Strategy Backtester
Simulate the strategy on historical data:
```bash
# Backtest BTCUSDT 15m over 1,000 candles with 1:2 Risk:Reward
python backtest.py --symbol BTCUSDT --interval 15m --limit 1000 --rr 2.0
```

Sample backtest report:
```text
============================================================
                    BACKTEST RESULTS
============================================================
  Total Setups Triggered : 18
  Closed Trades          : 18
  Wins                   : 8
  Losses                 : 10
  Win Rate               : 44.4%
  Profit Factor          : 1.60
  Net Return (R-units)   : +6.0 R
============================================================
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
  "klines_refresh_interval": 120,
  "telegram": {
    "enabled": false,
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "min_score": 4
  }
}
```

---

## 📱 Telegram Alerts Format

When a high-confluence setup is detected (Score >= 4/6), an alert is formatted and dispatched:

```text
🔥🔥 BEARISH LIQUIDITY SWEEP
• Pair: BTCUSDT (15m)
• Direction: SHORT ↓
• Level: 81240.5000
• Entry: 81090.2000
• Stop-Loss: 81380.0000
• Score: 6/6
• Confirmations:
  ✅ Anomaly Volume x2.1 (> 1.5x)
  ✅ Aggressive Sell Delta (-420, 18.2%)
  ✅ Open Interest Dropping (Stops Cleared)
```

---

## 🛡️ License

MIT License. See [LICENSE](LICENSE) for details.
