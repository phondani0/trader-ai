# Trader AI

This repository is now a `NautilusTrader`-first crypto research and paper-trading workspace.

The old custom runtime, benchmark engine, and legacy strategy stack are no longer the product direction. The root commands now dispatch into the isolated Nautilus subproject under [nautilus](/Users/ankit/Work/Projects/solo/trader-ai/nautilus).

## Quick Start

Install the root wrapper environment:

```bash
uv sync
```

Install the Nautilus subproject:

```bash
cd nautilus
uv sync
```

## Root Commands

Run these from the repo root:

```bash
uv run backtest --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt
uv run benchmark
uv run start
```

What they do:

- `backtest` runs `nautilus-backtest`
- `benchmark` runs `nautilus-compare-strategies`
- `start` runs `nautilus-papertrade`

The wrappers execute inside `nautilus/`, so the actual trading and research implementation lives there.

## Nautilus Subproject

The full app is in [nautilus/README.md](/Users/ankit/Work/Projects/solo/trader-ai/nautilus/README.md).

Key Nautilus commands:

```bash
cd nautilus
uv run nautilus-backtest
uv run nautilus-backtest --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/btc_spike
uv run nautilus-compare-strategies --instrument btcusdt_perp_binance --data-source ccxt --strategies volume_spike_trend nautilus_ema_cross demo_ema_cross --output-dir output/compare_btc
uv run nautilus-parameter-sweep --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --fast-periods 5 8 10 --slow-periods 21 34 55 --output-dir output/sweep_btc
uv run nautilus-papertrade --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/paper_bootstrap
uv run nautilus-walk-forward --bars-csv tests/fixtures/btcusdt_15m.csv --train-bars 256 --test-bars 128 --step-bars 128 --output-path output/walk_forward_windows.json
```

## Strategy Set

Supported Nautilus-native strategies:

- `volume_spike_trend`
- `nautilus_ema_cross`
- `demo_ema_cross`

The target operating lane is `15m` crypto research on liquid perpetuals.

## Verification

Root wrapper tests:

```bash
python3 -m unittest tests.test_nautilus_pivot
```

Nautilus tests:

```bash
cd nautilus
uv run python -m unittest discover -s tests
```
