# Trader AI — Autonomous Crypto Trading System

A production-minded trading system for crypto markets built around CCXT, regime-aware strategies, discovery journaling, and friction-aware backtests.

The default product path is crypto perpetuals through CCXT. The Alpaca adapter remains in the repo as an optional experimental path, but it is not the primary product direction.

## Architecture

```text
Data Layer -> Intelligence Layer -> Strategy Layer -> Risk Layer -> Execution
   |              |                    |                |            |
   |         Discovery Scanner    Strategy Engine    Risk Manager   CCXT Exchange
   |         Regime Detector      Signal Aggregator  Kill Switch    Paper/Live
   |         Discovery Journal
   |
   -> OHLCV + indicators -> confluence -> validated orders -> journal + reports
```

## Current Trading Stack

| Strategy | Market Condition | Role | Default Status |
|---|---|---|---|
| VWAP Reversion | Sideways / calm | Mean reversion to fair value | Enabled |
| Supertrend | Trending | Momentum continuation | Enabled |
| RSI Divergence | Trend exhaustion | Reversal filter | Enabled |
| EMA Crossover | Strong trends | Trend-following confirmation | Enabled |
| MACD + Bollinger | Sideways / choppy | Experimental mean reversion | Enabled |
| ORB | Session opens | Session-based breakout logic | Disabled |
| Chart Patterns | Higher timeframe | Pattern breakout / reversal | Disabled |

## Intelligence Layer

- Discovery scanner now builds a dynamic liquid universe from exchange-wide tickers, then enriches shortlist symbols with 15m/1h momentum, relative volume, and breakout context.
- Discovery journal deduplicates repeated scans, persists momentum discoveries, and audits them at 1h, 4h, and 24h.
- The tradable universe is now the core watchlist plus a rotating dynamic universe plus recent high-score discovery candidates.
- Regime detector gates strategies by trend strength and volatility.
- Passive parameter suggestions are generated from recent discovery outcomes and completed trades.
- News sentiment code exists as future-facing scaffolding, not a live execution filter yet.

## Quick Start

### 1. Install dependencies

```bash
cd trader-ai
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Add exchange credentials for the default crypto path:

```dotenv
EXCHANGE_ID=bybit
EXCHANGE_API_KEY=your_key
EXCHANGE_SECRET_KEY=your_secret
TRADING_MODE=paper
```

### 3. Review config

- `config/settings.yaml` -> broker, capital, risk, database path
- `config/strategies.yaml` -> strategy parameters and enablement
- `config/watchlist.yaml` -> active crypto watchlist plus optional equity reference lists
- `config/regimes.yaml` -> curated historical windows for benchmarks

## Commands

Run commands with `uv run <command>`.

| Command | Purpose | Example |
|---|---|---|
| `start` | Launch the trading engine, scanner, and journal loop | `uv run start` |
| `benchmark` | Stress-test strategies across curated real-market windows; each strategy uses its own timeframe from `config/strategies.yaml` while the reference market-profile window uses `config/settings.yaml` | `uv run benchmark --strategy supertrend` |
| `backtest` | Run a single strategy or the live-style multi-strategy selector on a symbol/date window using strategy-configured timeframes from `config/strategies.yaml` | `uv run backtest --symbol BTC/USDT:USDT --from 2024-01-01 --to 2024-03-31` |
| `trade` | Manual override for connectivity tests or emergency exits | `uv run trade --symbol BTC/USDT:USDT --side buy --qty 0.01` |
| `adopt-position` | Adopt an already-open exchange position into managed bot state and attach/update exchange-side SL and TP protection | `uv run adopt-position --symbol BTC/USDT:USDT --strategy donchian_breakout --stop-loss 61250 --take-profit 64800 --mode live` |

## Runtime Cadence

For the default CCXT crypto path:

- Strategy scan cadence is derived from the smallest enabled strategy timeframe.
- Discovery scan runs every 10 minutes by default and can pull in non-watchlist symbols from the broader liquid universe.
- Discovery journal review runs every 30 minutes.
- Daily summary runs at `00:05 UTC`.
- Daily risk reset runs at `00:10 UTC`.
- Normal process restarts do not auto-square-off crypto positions; live exposure is recovered from the exchange and matched against persisted local strategy metadata.

For the optional Alpaca adapter:

- Session-based scheduling and square-off windows apply.
- ORB is only meaningful in that session-based mode.

## Risk Model

| Rule | Default |
|---|---|
| Max risk per trade | 2% of capital |
| Max daily loss kill-switch | 3% of capital |
| Max simultaneous positions | 5 |
| Max single-asset exposure | 20% of capital |
| Mandatory stop-loss | Yes |
| Minimum risk:reward | 1.5:1 |
| Max drawdown kill-switch | 20% from peak capital |
| Backtest fee baseline | 0.055% taker fee |
| Backtest slippage baseline | 0.1% |

Benchmark approvals can now block fragile strategies from live startup based on regime-aware ROI, expectancy, profit factor, trade count, and drawdown thresholds.

In live crypto mode, the exchange is treated as the source of truth for open exposure. SQLite persists the bot's managed-position metadata so take-profit logic and trade attribution can resume cleanly after restarts while still respecting manual exchange-side closes or size changes.

If you already have a legacy bot-opened position that predates managed-position recovery, use `uv run adopt-position ...` once to bring it under runtime management. New CCXT Bybit entries now place exchange-side stop-loss and take-profit protection alongside the entry order when the exchange supports attached TP/SL.

The benchmark output now includes:

- real window-level market profiles from fetched OHLCV data
- detected macro profile plus dominant execution regime
- curated benchmark intent for market profile and expected execution regimes
- market-profile capability summaries instead of only one global score
- deployable regime lists that the runtime can use to gate strategies

The discovery scanner now includes:

- exchange-wide liquid-universe ranking instead of only static watchlists
- dynamic monitoring symbols for breakout scouting
- intraday burst features from recent OHLCV
- richer discovery metadata persisted to SQLite for later learning

## Optional Alpaca Adapter

Alpaca is still supported in code, but it is no longer the repo's primary story.

Use it only if you explicitly want to experiment with:

- US equities
- Alpaca crypto spot
- session-based workflows

To switch, update `config/settings.yaml`:

```yaml
trading:
  asset_class: equity

broker:
  name: alpaca
```

Then provide `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env`.

## Notifications And Dashboard

- Telegram notifications are supported through `.env` credentials.
- A basic FastAPI dashboard scaffold exists at `src/dashboard/app.py`.

Start the dashboard scaffold with:

```bash
uv run uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8080
```

## Docker

```bash
docker build -t trader-ai .
docker run -d --name trader-ai --env-file .env -p 8080:8080 trader-ai
```

## Important Notes

- This is not financial advice.
- Start in paper or demo mode and observe behavior before considering live capital.
- Backtests are useful for filtering bad ideas, not proving future returns.
- `backtest --days N` now pages historical candles across multiple requests instead of silently truncating to a single 1000-bar fetch.
- `backtest --strategy all` now uses the same one-best-signal selector shape as live trading instead of opening parallel positions per strategy on the same symbol.
- Strategy timeframes are defined in `config/strategies.yaml` and are now the source of truth for live scans, selector backtests, and per-strategy benchmark runs.
- If you add a new external dependency or credential, keep `.env.example` and the README in sync.

## Project Structure

```text
trader-ai/
├── config/
│   ├── settings.yaml
│   ├── strategies.yaml
│   ├── watchlist.yaml
│   └── regimes.yaml
├── src/
│   ├── main.py
│   ├── core/
│   │   ├── broker.py          # Primary CCXT broker
│   │   ├── alpaca_broker.py   # Optional Alpaca adapter
│   │   ├── broker_factory.py
│   │   ├── data_feed.py
│   │   ├── scheduler.py
│   │   ├── database.py
│   │   └── backtest/
│   ├── intelligence/
│   │   ├── scanner.py
│   │   ├── journal.py
│   │   ├── regime_detector.py
│   │   ├── signal_aggregator.py
│   │   └── news_sentiment.py
│   ├── strategies/
│   ├── risk/
│   ├── notifications/
│   └── dashboard/
├── scripts/
│   ├── adopt_position.py
│   ├── backtest.py
│   ├── benchmark.py
│   └── trade.py
├── pyproject.toml
├── uv.lock
└── .env.example
```
