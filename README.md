# 🤖 Trader AI — Autonomous Intraday Trading Bot

A production-ready, autonomous intraday trading bot for **US stock markets** using **Alpaca** (commission-free trading). Features multi-strategy engine with market regime detection, adaptive strategy selection, and comprehensive risk management.

## 🏗️ Architecture

```
Data Layer → Intelligence Layer → Strategy Layer → Risk Layer → Execution
   │              │                    │                │            │
   │         Regime Detector     7 Strategies      Risk Manager   Alpaca API
   │         News Sentiment      Signal Aggregator  Kill Switch   Paper/Live
   │
   └── Alpaca WebSocket + REST → OHLCV + Indicators
```

## 📊 Strategies

| # | Strategy | Market Condition | Method |
|---|---------|-----------------|--------|
| 1 | **MACD + Bollinger Bands** | Sideways/Range | Mean reversion on band touch + MACD cross |
| 2 | **VWAP Mean Reversion** | Range-bound | Trade deviation from VWAP + RSI |
| 3 | **Supertrend Momentum** | Trending | Supertrend flip + EMA + volume confirmation |
| 4 | **Opening Range Breakout** | First 30 min | Break above/below ORB with VWAP filter |
| 5 | **RSI Divergence** | Trend exhaustion | Price/RSI divergence → reversal |
| 6 | **EMA Crossover** | Strong trends | 9/21 EMA cross with ADX filter |
| 7 | **Chart Patterns** | All conditions | Triangle, ABCD, Double Top/Bottom |

## 🧠 Market Regime Detection

The bot automatically detects market conditions and **only activates strategies suited for the current regime**:

| Regime | Active Strategies |
|--------|------------------|
| 📈 Trending + Calm | Supertrend, EMA Crossover, RSI Divergence, ORB |
| 📈⚡ Trending + Volatile | Supertrend, ORB (wider stops) |
| ↔️ Ranging + Calm | MACD+BB, VWAP Reversion, ORB |
| ↔️⚡ Choppy (DANGER) | ORB only (or sit out) |

## 🚀 Quick Start

### 1. Get Alpaca API Keys
1. Go to [https://app.alpaca.markets](https://app.alpaca.markets)
2. Sign up for a free account
3. Get your **API Key** and **Secret Key** from the dashboard
4. Paper trading is enabled by default (free, no risk)

### 2. Setup
```bash
# Clone and enter the project
cd trader-ai

# Install uv (modern Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies (auto-creates .venv)
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your Alpaca API keys
```

### 3. Configure
- **`config/settings.yaml`** — Main settings (capital, risk limits, market hours)
- **`config/strategies.yaml`** — Strategy parameters (tune without code changes)
- **`config/watchlist.yaml`** — Stocks to monitor

### 4. Run

```bash
# Start bot in Paper trading mode (default, safe)
uv run start

# Run a single scan cycle (testing)
uv run start --once

# Backtest strategies
uv run backtest --symbol BTC/USD --days 30

# Manual CLI Trading Override (Injects live trades to Alpaca bypassing strategies)
uv run trade --symbol BTC/USD --side buy --qty 0.01

# Manual CLI Trading (Full Round-Trip Buy then Sell Test)
uv run trade --symbol BTC/USD --side buy --qty 0.01 --round-trip

# Manual CLI Trading (Emergency Liquidation)
uv run trade --close-all

# Execute Live Trading Bot (requires explicit terminal confirmation)
uv run start --mode live
```

## 🛡️ Risk Management

| Rule | Default |
|------|---------|
| Max risk per trade | 2% of capital |
| Max daily loss (kill switch) | 3% of capital |
| Max simultaneous positions | 5 |
| Max single-stock exposure | 20% of capital |
| Mandatory stop-loss | Every trade |
| Auto square-off | 3:50 PM ET daily |
| Min risk:reward ratio | 1.5:1 |

## 📱 Notifications (Telegram)

Set up a Telegram bot to receive real-time alerts:

1. Message [@BotFather](https://t.me/botfather) on Telegram → `/newbot`
2. Get your bot token
3. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
4. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

## 📊 Dashboard

```bash
# Start the monitoring dashboard
uv run uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8080
```
Open [http://localhost:8080](http://localhost:8080) for real-time monitoring.

## 🐳 Docker Deployment

```bash
# Build
docker build -t trader-ai .

# Run (paper mode)
docker run -d \
  --name trader-ai \
  --env-file .env \
  -p 8080:8080 \
  trader-ai
```

## ⏰ Daily Lifecycle

| Time (ET) | Event |
|-----------|-------|
| 8:00 AM | Bot wakes up, fetches pre-market data |
| 9:30 AM | Market opens → scanning begins |
| 9:30-10:00 AM | ORB window (Opening Range Breakout) |
| 10:00 AM-3:30 PM | All strategies active (regime-dependent) |
| 3:30 PM | Stop new entries |
| 3:50 PM | Auto square-off all positions |
| 4:00 PM | Market close → daily P&L report |

## ⚠️ Important Disclaimers

- **This is NOT financial advice.** Algorithmic trading involves significant risk.
- **Always start with paper trading.** Run for 30+ days before considering live.
- **Past performance ≠ future results.** Backtest results may not replicate live.
- **You are responsible** for all trades placed by this bot.
- **PDT Rule:** US pattern day trader rule requires $25,000+ for unlimited day trades.

## 📁 Project Structure

```
trader-ai/
├── config/                    # YAML configuration
│   ├── settings.yaml          # Main settings
│   ├── strategies.yaml        # Strategy parameters
│   └── watchlist.yaml         # Stock universe
├── src/
│   ├── main.py               # Entry point (autonomous worker)
│   ├── core/
│   │   ├── broker.py          # Alpaca API wrapper
│   │   ├── data_feed.py       # Market data + indicators
│   │   ├── scheduler.py       # Market-hours scheduler
│   │   └── models.py          # Data models
│   ├── strategies/
│   │   ├── base.py            # Abstract base strategy
│   │   ├── macd_bollinger.py  # Strategy 1
│   │   ├── vwap_reversion.py  # Strategy 2
│   │   ├── supertrend.py      # Strategy 3
│   │   ├── orb.py             # Strategy 4
│   │   ├── rsi_divergence.py  # Strategy 5
│   │   ├── ema_crossover.py   # Strategy 6
│   │   └── chart_patterns.py  # Strategy 7
│   ├── intelligence/
│   │   ├── regime_detector.py # Market regime detection
│   │   ├── signal_aggregator.py # Multi-strategy orchestrator
│   │   └── news_sentiment.py  # FinBERT NLP (optional)
│   ├── risk/
│   │   ├── manager.py         # Risk management
│   │   ├── position_sizer.py  # Position sizing
│   │   └── kill_switch.py     # Emergency stop
│   ├── notifications/
│   │   └── notifier.py        # Telegram alerts
│   └── dashboard/
│       └── app.py             # Web dashboard (FastAPI)
├── scripts/
│   └── backtest.py            # Backtesting runner
├── pyproject.toml              # Dependencies & project config
├── uv.lock                     # Lockfile (reproducible builds)
├── Dockerfile
└── .env.example
```
