# NautilusTrader App

This subproject is the only active trading and research engine in the repository.

It is intentionally isolated from the repo root so strategy work, reports, and framework dependencies stay inside one clear boundary.

## Setup

```bash
cd nautilus
uv sync
```

## Default Modes

- synthetic demo backtest for fast local verification
- `15m` crypto research via CCXT for real market work
- paper-trading bootstrap generation for deployment planning

## Commands

### Backtest

```bash
uv run nautilus-backtest
uv run nautilus-backtest --instrument btcusdt_perp_binance --strategy volume_spike_trend --data-source ccxt --output-dir output/btc_spike
```

### Compare Strategies

```bash
uv run nautilus-compare-strategies \
  --instrument btcusdt_perp_binance \
  --data-source ccxt \
  --strategies volume_spike_trend nautilus_ema_cross demo_ema_cross \
  --output-dir output/compare_btc
```

### Parameter Sweep

```bash
uv run nautilus-parameter-sweep \
  --instrument btcusdt_perp_binance \
  --strategy volume_spike_trend \
  --data-source ccxt \
  --fast-periods 5 8 10 \
  --slow-periods 21 34 55 \
  --output-dir output/sweep_btc
```

### Batch Research

```bash
uv run nautilus-batch-research \
  --instruments btcusdt_perp_binance ethusdt_perp_binance \
  --strategies volume_spike_trend nautilus_ema_cross \
  --data-source ccxt \
  --output-dir output/batch_crypto
```

### Walk-Forward Windows

```bash
uv run nautilus-walk-forward \
  --bars-csv tests/fixtures/btcusdt_15m.csv \
  --train-bars 256 \
  --test-bars 128 \
  --step-bars 128 \
  --output-path output/walk_forward_windows.json
```

### Paper Bootstrap

```bash
uv run nautilus-papertrade \
  --instrument btcusdt_perp_binance \
  --strategy volume_spike_trend \
  --data-source ccxt \
  --output-dir output/paper_bootstrap
```

This generates deployment metadata and a runtime env template. It is not yet a continuous live loop.

## Outputs

Backtests and research runs emit:

- interactive HTML tearsheets
- `summary.json`
- `monthly_returns.csv`
- `positions.csv`
- `fills.csv`
- `account.csv`
- leaderboard files for compare and sweep runs

## Current Strategy Set

- `volume_spike_trend`
- `nautilus_ema_cross`
- `demo_ema_cross`

## Tests

```bash
uv run python -m unittest discover -s tests
```
