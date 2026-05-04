# Nautilus Subproject Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated NautilusTrader subproject inside this repo that can install NautilusTrader, download sample data, and run a minimal backtest without touching the current bot runtime.

**Architecture:** Create a root-level `nautilus/` Python subproject with its own `pyproject.toml`, config, scripts, and tests. Use a simple Nautilus-native demo strategy and official sample-data flow first, so the spike proves the framework and wiring before any serious strategy migration.

**Tech Stack:** Python 3.12+, `uv`, NautilusTrader, PyYAML, unittest.

---

### Task 1: Scaffold the isolated Nautilus subproject

**Files:**
- Create: `nautilus/pyproject.toml`
- Create: `nautilus/README.md`
- Create: `nautilus/src/nautilus_app/__init__.py`
- Create: `nautilus/src/nautilus_app/strategies/__init__.py`
- Create: `nautilus/src/nautilus_app/runners/__init__.py`
- Create: `nautilus/src/nautilus_app/adapters/__init__.py`
- Create: `nautilus/config/settings.yaml`
- Create: `nautilus/config/instruments.yaml`
- Create: `nautilus/tests/__init__.py`

- [ ] Add an isolated subproject with its own package metadata and dependencies.
- [ ] Add minimal config files for backtest defaults.
- [ ] Add package directories so scripts can import `nautilus_app`.

### Task 2: Add a minimal Nautilus strategy and config loader with tests first

**Files:**
- Create: `nautilus/tests/test_config.py`
- Create: `nautilus/tests/test_strategy_imports.py`
- Create: `nautilus/src/nautilus_app/config.py`
- Create: `nautilus/src/nautilus_app/strategies/macd_demo.py`

- [ ] Write tests that verify config loading and strategy import surfaces.
- [ ] Run the tests and confirm they fail before implementation.
- [ ] Implement minimal config loading and a simple Nautilus-native demo strategy.
- [ ] Re-run the focused tests and confirm they pass.

### Task 3: Add sample-data bootstrap and backtest runner

**Files:**
- Create: `nautilus/scripts/download_sample_data.py`
- Create: `nautilus/scripts/backtest.py`
- Create: `nautilus/src/nautilus_app/runners/backtest.py`
- Create: `nautilus/tests/test_backtest_runner.py`

- [ ] Write a failing test that checks the backtest runner builds a config from local settings.
- [ ] Run the test and confirm it fails first.
- [ ] Implement a sample-data bootstrap script and a minimal backtest runner based on NautilusTrader’s high-level `BacktestNode` flow.
- [ ] Re-run the focused tests and confirm they pass.

### Task 4: Install NautilusTrader and verify the spike end to end

**Files:**
- Modify: `nautilus/README.md`
- Create: `nautilus/scripts/papertrade.py`

- [ ] Install dependencies for the `nautilus/` subproject with `uv`.
- [ ] Add a paper-trading entry stub that clearly states the spike is backtest-first.
- [ ] Run unit tests for the subproject.
- [ ] Run the sample-data bootstrap.
- [ ] Run the Nautilus backtest command and capture the actual outcome.
- [ ] Update the README with exact setup and run commands that match the final implementation.
