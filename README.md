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
uv run backtest --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/btc_backtest
uv run benchmark
uv run compare-strategies --instrument btcusdt_perp_binance --data-source ccxt --strategies volume_spike_trend nautilus_ema_cross demo_ema_cross --output-dir output/compare_btc
uv run integration-smoke --mode local-sim
uv run research --instruments btcusdt_perp_binance ethusdt_perp_binance --strategies volume_spike_trend nautilus_ema_cross --data-source ccxt --output-dir output/research
uv run parameter-sweep --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --fast-periods 5 8 10 --slow-periods 21 34 55 --output-dir output/sweep_btc
uv run walk-forward --bars-csv tests/fixtures/btcusdt_15m.csv --train-bars 256 --test-bars 128 --step-bars 128 --output-path output/walk_forward_windows.json
uv run papertrade --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/paper_bootstrap
uv run start --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/paper_bootstrap
```

Command roles:

- `backtest`: one strategy on one instrument
- `benchmark`: default comparison run for the core strategy set
- `compare-strategies`: compare multiple strategies on one instrument
- `integration-smoke`: CI-safe runtime scanner activation smoke test
- `research`: batch research across many instruments and strategies
- `parameter-sweep`: grid search one strategy over EMA periods
- `walk-forward`: generate rolling train/test windows from bar CSVs
- `papertrade` and `start`: generate the paper-trading bootstrap manifest and env template

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

## Strategy Set

Supported native strategies:

- `volume_spike_trend`
- `nautilus_ema_cross`
- `demo_ema_cross`

The core target lane is liquid crypto perpetuals on `15m` bars.

## Layout

```text
trader-ai/
├── config/
│   ├── instruments.yaml
│   └── settings.yaml
├── data/
│   └── cache/
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
