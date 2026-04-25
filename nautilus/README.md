# NautilusTrader Spike

This subproject is an isolated NautilusTrader experiment inside the main `trader-ai` repository.

It does not reuse the current bot runtime or backtest engine. The goal is to evaluate NautilusTrader on its own merits before any migration decision.

## Scope

Current spike goals:

- install NautilusTrader cleanly in an isolated subproject
- run a minimal synthetic-data backtest based on the official quickstart pattern
- keep strategy code and config separate from the current bot

## Setup

```bash
cd nautilus
uv sync
```

## Run

```bash
uv run nautilus-backtest
```

This runs a self-contained synthetic EUR/USD 1-minute EMA-cross demo backtest. No external market-data download is required for the first spike.
It also writes an interactive HTML tearsheet to:

```text
nautilus/output/demo_backtest_tearsheet.html
```

## Paper Trading

```bash
uv run nautilus-papertrade
```

Paper trading is intentionally not wired yet. This spike is backtest-first.

## Tests

```bash
uv run python -m unittest discover -s tests
```
