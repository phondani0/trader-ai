# Trader AI

`Trader AI` is now a single-project `NautilusTrader` workspace for `15m` crypto research and paper-trading workflows.

The old custom runtime and benchmark stack are gone. The repo now has one application package under [src/trader_ai](/Users/ankit/Work/Projects/solo/trader-ai/src/trader_ai), one config directory under [config](/Users/ankit/Work/Projects/solo/trader-ai/config), one cache directory under [data/cache](/Users/ankit/Work/Projects/solo/trader-ai/data/cache), and one test suite under [tests](/Users/ankit/Work/Projects/solo/trader-ai/tests).

## Setup

```bash
uv sync
```

## Commands

Run everything from the repo root.

```bash
uv run analyze-bybit-orders --since-hours 72
uv run analyze-bybit-orders --since-hours 72 --fail-unprotected-positions
uv run backtest --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --use-strategy-config --starting-balance "5000 USDT" --output-dir output/btc_backtest
uv run benchmark --use-strategy-config --starting-balance "5000 USDT"
uv run compare-strategies --instrument btcusdt_perp_binance --data-source ccxt --strategies volume_spike_trend nautilus_ema_cross demo_ema_cross --use-strategy-config --starting-balance "5000 USDT" --output-dir output/compare_btc
uv run integration-smoke --mode local-sim
uv run research --instruments btcusdt_perp_binance ethusdt_perp_binance --strategies volume_spike_trend nautilus_ema_cross --data-source ccxt --use-strategy-config --starting-balance "5000 USDT" --output-dir output/research
uv run parameter-sweep --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --fast-periods 5 8 10 --slow-periods 21 34 55 --output-dir output/sweep_btc
uv run walk-forward --bars-csv tests/fixtures/btcusdt_15m.csv --train-bars 256 --test-bars 128 --step-bars 128 --output-path output/walk_forward_windows.json
uv run papertrade --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/paper_bootstrap
uv run start --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/paper_bootstrap
```

Command roles:

- `analyze-bybit-orders`: read-only Bybit positions/orders/fills/closed-PnL diagnostics from `.env`
- `backtest`: one strategy on one instrument
- `benchmark`: default comparison run for the core strategy set
- `compare-strategies`: compare multiple strategies on one instrument
- `integration-smoke`: CI-safe runtime scanner activation smoke test
- `research`: batch research across many instruments and strategies
- `parameter-sweep`: grid search one strategy over EMA periods
- `walk-forward`: generate rolling train/test windows from bar CSVs
- `papertrade` and `start`: generate the paper-trading bootstrap manifest and env template

Research commands use strategy class defaults unless `--use-strategy-config` is provided.
Use `--use-strategy-config` for production-faithful research because it loads strategy params
from `config/strategies.yaml`, including notional sizing, stop/trailing settings, and
profit-runner settings.
Use `--starting-balance "5000 USDT"` to model the current intended account size.

For Bybit diagnostics, `analyze-bybit-orders` is read-only and loads `.env`.
Paper mode defaults to Bybit demo REST. Use `--account-mode testnet` only for testnet keys.
Use `--fail-unprotected-positions` after deploys to fail fast when any open position lacks a visible reduce-only stop/trigger order.

Recommended post-deploy safety check:

```bash
uv run analyze-bybit-orders --since-hours 72 --limit 200 --fail-unprotected-positions
```

Exit code `2` means at least one open position is unprotected. Stop trading or close/reconcile that position before trusting the account state.

## Integration Smoke

Use the local simulator smoke after runtime activation or scanner changes:

```bash
uv run integration-smoke --mode local-sim
```

This uses deterministic bars and fake scanner candidates. It does not connect to Bybit/Binance and does not require API keys.

The manual Bybit demo preflight is safety-gated and always forces paper/demo semantics:

```bash
uv run integration-smoke --mode bybit-demo --duration 120
```

It refuses `TRADING_MODE=live` and refuses to run without demo API credentials. CI should use local-sim mode only.

## Observability

The live runner can emit structured JSON logs plus OpenTelemetry metrics and traces. The local OSS stack is:

- OpenTelemetry Collector
- Prometheus
- Loki
- Tempo
- Grafana

Start the stack:

```bash
docker compose up -d
```

Grafana is bound to `127.0.0.1:3300` on the host with `admin` / `admin`.
It is not exposed publicly by Compose.
Override the host port only when needed:

```bash
GRAFANA_PORT=3000 docker compose up -d
```

Minimum remote access path:

```bash
bash scripts/expose_grafana_tailscale.sh
```

That uses Tailscale Services to publish Grafana as `svc:trader-ai-grafana` over HTTPS without opening a public port. If you prefer a one-off tunnel, use SSH port forwarding to `127.0.0.1:3300` on the VPS.

Run the app. Observability is enabled by default and exports logs, metrics, and traces to `http://localhost:4317`:

```bash
uv run start
```

Default values:

- `OBSERVABILITY_ENABLED=true`
- `OTEL_SERVICE_NAME=trader-ai`
- `TRADING_ENV=paper`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`
- `LOG_JSON=true`

Local retention defaults are intentionally bounded for a small VPS:

- Prometheus metrics: `15d`, capped at `4GB`
- Loki logs: `14d`
- Tempo traces: `3d`

For PM2 on the VPS, no `.env` update is needed when the Collector runs on the same host. Set `OBSERVABILITY_ENABLED=false` only if you need to temporarily disable telemetry.

## Strategy Set

Supported native strategies:

- `volume_spike_trend`
- `nautilus_ema_cross`
- `demo_ema_cross`

The core target lane is liquid crypto perpetuals on `15m` bars.

## Live Warmup Engine

Indicators (EMA, RSI, ATR) need dozens of historical bars before they output meaningful values. The warmup engine pre-loads history via CCXT REST and replays it through each strategy's indicators **before** the WebSocket stream begins — so the bot is ready to trade on bar one.

**Two warmup paths:**

| Path | When | What happens |
|---|---|---|
| **Startup warmup** | Node boot | `warmup_live_components()` fetches `warmup_bars` of history for every configured instrument and calls `strategy.seed_warmup()` |
| **Runtime warmup** | Scanner discovers a new symbol mid-session | `activate_component()` fetches history, warms a new context, and **rejects** the symbol if indicators aren't ready |

**Safety guards:**

- **Incomplete candle filter** — the still-forming bar is dropped to avoid look-ahead bias
- **Duplicate-bar guard** — `_last_warmup_close_ns` prevents the WebSocket from re-processing bars already replayed during warmup

**Configuration** (`config/settings.yaml`):

```yaml
warmup_enabled: true   # master on/off switch
warmup_bars: 120       # 120 × 15m = 30h of history
```

## Position Recovery Engine

The bot features a Restart-Safe Position Recovery system that protects capital during service interruptions, VPS reboots, and network disconnects.

When the live node starts, it reconciles the exchange reality (Open Positions via CCXT) with its SQLite intent (`strategy_positions`).

**The Three Pillars of Recovery:**
- 🛡️ **Safety Barrier:** Reconciles positions before booting. Mismatches in size/side explicitly block the symbol from new entries to avoid overlapping trades.
- 💧 **State Hydration:** If an open position matches SQLite perfectly, the strategy resumes management. It restores entry prices, trade IDs, active trailing stops, and internal cooldown/trend bars natively.
- 🚧 **Unmanaged Position Guard:** If an open exchange position has no matching SQLite record, the symbol is blocked from new entries and logged for review. The bot will not automatically manage or close positions it cannot prove it owns.

*The trading gate will refuse all new entries until the `PositionRecoveryService` completes its audit on startup.*

Current safety boundary:

- New entries submit protection after the entry `OrderFilled` event, because this event reliably carries the original entry `client_order_id`.
- `PositionOpened`/`PositionChanged` events are fallback signals only; they may identify the position but not the entry order.
- Existing positions opened before this protection flow may remain unprotected and must be checked with `analyze-bybit-orders --fail-unprotected-positions`.
- Hydrated/recovered positions restore strategy memory and submit/ensure exchange-side reduce-only stop protection on strategy start.
- If an exchange position was partially closed while the bot was offline, recovery hydrates the reconciled exchange quantity so the protective order size matches the actual open position.

The required lifecycle test shape for exit-order changes is:

```text
entry order submitted
entry OrderFilled with client_order_id
reduce-only stop/trigger order submitted
PositionOpened/PositionChanged may arrive with only position_id
no duplicate protection order is created
restart recovery hydrates exchange quantity
strategy on_start ensures recovered-position protection
```

Do not accept a change that only tests the helper creating stop orders; it must simulate this lifecycle for every affected strategy/runtime path.

## Layout

```text
trader-ai/
├── config/
│   ├── instruments.yaml
│   └── settings.yaml
├── data/
│   └── cache/
├── observability/
│   ├── grafana/
│   ├── loki.yaml
│   ├── otel-collector.yaml
│   ├── prometheus.yml
│   └── tempo.yaml
├── src/
│   └── trader_ai/
│       ├── cli/
│       ├── runners/
│       ├── strategies/
│       ├── config.py
│       ├── data.py
│       └── reports.py
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## Outputs

Backtests and research runs emit:

- interactive HTML tearsheets
- `summary.json`
- `monthly_returns.csv`
- `positions.csv`
- `fills.csv`
- `account.csv`
- leaderboard files for compare and sweep runs

## Verification

```bash
uv run python -m unittest discover -s tests
```
