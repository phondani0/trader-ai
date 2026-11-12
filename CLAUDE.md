# Trader AI - Engineering Standards & Architecture Guide

Autonomous algorithmic crypto trading system built on NautilusTrader and CCXT.

---

## 🏗️ System Architecture: One Codebase, Multiple Runtimes

This project uses **one codebase** for research, paper trading, and live trading. The same repository is used locally and on the Ubuntu VPS.

### Local Research & Backtesting
- **Runtime:** `uv run backtest`, `uv run benchmark`, `uv run papertrade`, `uv run integration-smoke`
- **Engine:** NautilusTrader (institutional-grade event-driven backtester/live engine)
- **Purpose:** Strategy research, backtesting, benchmarking, integration testing, and paper trading
- **CI/CD:** GitHub Actions (`.github/workflows/deploy.yml`) runs tests on every push to `main`.
- **Strategies:** `adaptive_trend_rider`, `squeeze_breakout`, `volume_spike_trend`, `demo_ema_cross`

### VPS Live Runtime
- **Runtime:** PM2 running this same repo, typically through `uv run start`
- **Engine:** NautilusTrader live runner in `src/trader_ai/runners/live.py`
- **Purpose:** Live/paper trading on Bybit perpetual futures using the same strategy and scanner code
- **Deployment:** Manual SSH + git pull on the server, then PM2 restart (`scripts/deploy_remote.sh`).

> [!IMPORTANT]
> When investigating PM2 logs, runtime scanner behavior, unexecuted trades, or kill-switch behavior, assume it is this same repo running on the VPS. Debug local source first, then use runtime telemetry as evidence.

---

## 🚀 Trading Philosophy & Architecture

### The "Sudden Momentum" Philosophy
Crypto moves on "Sudden Momentum" driven by liquidity shifts, news, and social sentiment. The system's job is to:
1. **Scan for Sudden Bursts:** Identify sudden liquidity and price movements across the exchange universe.
2. **Filter for Quality:** Use multi-pillar verification (regime detection, volatility gates, volume spikes) to eliminate noise.
3. **Learn and Adapt:** Record predictions in the SQLite Discovery Journal and verify outcomes across 1h, 4h, and 24h horizons.

### Feedback Loop Architecture
1. **L1 (Observational):** Discovery Journal. Every momentum signal is persisted to SQLite and audited at 1h, 4h, and 24h intervals.
2. **L2 (Parametric):** Passive Parameter Drift. Auto-tune `min_price_change` or stop targets based on empirical journal data.
3. **L3 (Autonomous):** Structural Evolution. Adaptive regime detection adjusting strategy allocation dynamically.

---

## 🏛 Architectural Decision Records (ADR)

- **Package Manager:** `uv`. Fast, deterministic environments. Do not use `pip`, `poetry`, or `conda`.
- **Market Abstraction:** `CCXT`. Universal exchange coverage (Bybit, Binance).
- **Persistence:** `SQLite`. All signals, journal entries, and trade recovery history live in `data/trader.db`.
- **Backtesting:** Modular and regime-aware. Always stress-test against specific historical windows (Trending vs. Choppy).

---

## ⚖️ The Friction Manifesto
**All simulations and backtests must adhere to strict real-world friction:**
1. **Strict Trading Costs:** Always include 0.055% Taker Fees and 0.1% Slippage as a baseline.
2. **Conservative Wick Logic:** If a candle touches both SL and TP, the engine assumes the **Stop Loss hit first**.
3. **Capital Protection:** Non-negotiable daily loss limits and a **20% Maximum Drawdown** kill-switch implemented at the architectural level.

---

## 📊 Strategy Status (Binance 15m BTCUSDT-PERP Benchmarks)

- **🏆 `demo_ema_cross` (Momentum EMA):** Best performer. 19.3% return, PF 1.77, 49% WR, R:R 1.87. ATR trailing stops + RSI + ATR expansion gate.
- **✅ `volume_spike_trend`:** Solid. 11.1% return, PF 1.34, 44% WR, R:R 1.73. High-conviction volume spike (3x avg) + RSI bounds + ATR trailing stops.
- **✅ `adaptive_trend_rider`:** Steady. 6.0% return. Conservative and robust.
- **⚠️ `squeeze_breakout`:** Highly selective. 4.3% return (20 fills in 156 days).
- **❌ `nautilus_ema_cross` (legacy):** Defunct (-7.6% return, flip-flop with no stops). Do NOT activate.

---

## 🛠 Engineering & Safety Standards

### Strict Schema & API Discipline
- Never apply "be liberal in what you accept" fallbacks (e.g. dual field aliases in payloads).
- Enforce strict, fail-fast schema contracts at boundaries with early validation.
- Fix calling clients directly when payloads mismatch. Never write masking server fallbacks.

### Code Quality & DX
- **Quality over Quantity:** Prefer one high-fidelity filter over 10 noisy strategies.
- **Typing:** Enforce strict typing (`-> dict`, `-> list`) and `Pydantic` models everywhere.
- **Documentation Sync:** Every CLI command change in `pyproject.toml` MUST be reflected in `README.md`.
- **Proactivity:** Evaluate decisions for risk. If a proposed change increases financial risk (e.g. removing stops), push back and provide safe alternatives.
- **Minimalist Code:** Avoid noisy comments. Code should be self-documenting; use comments only for complex algorithmic logic.

### Observability Gate (Production Requirement)
Every live-trading behavior change MUST update structured logs, Prometheus metrics, and OpenTelemetry traces:
- Normal `logger.info(...)`-style structured fields through the observability layer.
- Stable fields: `log_type`, `strategy`, `symbol`, `trade_id`, `order_id`, `result`, `reason`.
- Prometheus metrics must use bounded labels.
- Grafana must always be able to answer: *"did this lifecycle event happen, for which symbol/strategy/trade, and with what result?"*

### Exit Lifecycle Signals
Non-optional observability events:
- Entry submission & fill
- Exit-order submission & fill
- Stop replacement & trailing updates
- Profit-runner activation
- Exit-order cancellation
- Order rejection / failure
- Position open & position close

### Protection Orders Are P0
- Every live/paper position opened by the bot MUST have a visible exchange-side reduce-only stop/trigger order.
- Never rely solely on in-memory stop checks or periodic bar loops.
- Verify post-deploy safety:
  ```bash
  uv run analyze-bybit-orders --since-hours 72 --limit 200 --fail-unprotected-positions
  ```
  Any non-zero exit code indicates an unprotected position failure.

### Lifecycle Tests Must Simulate Real Events
- Tests must simulate the full production event chain:
  `entry submitted` -> `OrderFilled with client_order_id` -> `PositionOpened/PositionChanged` -> `exactly one protective reduce-only stop submitted`.
- Cover both startup/preloaded strategies and runtime scanner contexts.

### Restart Recovery Protection
- Nautilus reconciliation and SQLite hydration restore strategy memory.
- `strategy.on_start()` must ensure exchange-side protection exists for all recovered positions.
- If an exchange position was partially closed externally while offline, recovery must hydrate the reconciled exchange quantity so the protective order size matches.

### Bybit Diagnostics
- CCXT unified calls may fail on Bybit demo (`Demo trading are not supported`); diagnostics bypass unified methods and call Bybit v5 read-only endpoints directly.
- Always query conditional stop orders with `orderFilter=StopOrder`, not just normal active orders.

### Scanner Universe Preloading & Filtering
- Dynamic runtime scanners must enable symbol preloading (`preload_runtime_symbols: true`) to warm indicators before bar streaming.
- Enforce explicit token blacklists (`blacklist`) to prevent onboarding non-crypto assets or illiquid tickers.

### Order Failure State Sanitization
- Whenever exchange order submissions fail or reject, execution observers MUST explicitly clear stale strategy in-memory state. Never leave pending execution states wedged.

### Exchange-Side Exits & Throttling
- Delegate exit execution to exchange-side reduce-only protection orders via `ExitOrderManager`.
- Throttle trailing stop updates by minimum basis-point thresholds (`trailing_replace_min_bps`) to prevent rate-limiting churn.

### Commit Gate
- Always ask the user for permission before running `git commit`. Never auto-commit.
