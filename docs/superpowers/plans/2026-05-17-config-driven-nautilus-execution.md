# Config Driven Nautilus Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore config-driven multi-strategy, multi-instrument Nautilus demo/live execution while keeping scanner journal gating configurable.

**Architecture:** Keep `config/instruments.yaml` as the instrument catalog and add `config/strategies.yaml` as the enabled strategy execution matrix. The live runner builds one Nautilus strategy instance per enabled strategy/instrument pair, validates that all live instruments share one venue, and registers all instances on one `TradingNode`. Strategy journal gating changes from boolean hard-blocking to `off`, `soft`, or `hard`.

**Tech Stack:** Python 3.12, NautilusTrader, YAML config, `uv`, pytest, ruff.

---

### Task 1: Strategy Execution Config Loader

**Files:**
- Modify: `src/trader_ai/config.py`
- Create: `config/strategies.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `load_strategy_configs()` reads enabled strategies, instruments, `discovery_gate`, and params from `config/strategies.yaml`.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_config.py`
Expected: fails because `load_strategy_configs` does not exist.

- [ ] **Step 3: Implement config loader**

Add `StrategyExecutionConfig` and `load_strategy_configs(path)`.

- [ ] **Step 4: Add default strategy matrix**

Add `config/strategies.yaml` with `adaptive_trend_rider` enabled on XRP and BTC, `volume_spike_trend` disabled by default, and `discovery_gate: soft`.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_config.py`
Expected: passes.

### Task 2: Registry Params and Discovery Gate

**Files:**
- Modify: `src/trader_ai/strategies/registry.py`
- Modify: `src/trader_ai/strategies/adaptive_trend_rider.py`
- Modify: `src/trader_ai/strategies/volume_spike_trend.py`
- Test: `tests/test_strategy_registry.py`

- [ ] **Step 1: Write failing tests**

Add tests proving registry passes strategy-specific params and `discovery_gate` into strategy configs.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_strategy_registry.py`
Expected: fails because registry does not accept `params` or `discovery_gate`.

- [ ] **Step 3: Implement registry config merging**

Add `params: dict[str, object] | None = None` and `discovery_gate: str = "off"` to `create_strategy()`. Keep current explicit fast/slow arguments as fallback defaults.

- [ ] **Step 4: Implement soft/hard gate semantics**

`off`: ignore journal. `soft`: log journal status but do not block. `hard`: block when the current instrument symbol is not pending in the journal.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_strategy_registry.py`
Expected: passes.

### Task 3: Multi-Strategy Live Builder

**Files:**
- Modify: `src/trader_ai/runners/live.py`
- Test: `tests/test_live_runner.py`

- [ ] **Step 1: Write failing tests**

Add tests proving live execution config builds multiple strategy components from enabled strategy/instrument pairs and validates all instruments use one venue.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_live_runner.py`
Expected: fails because live runner only builds one strategy from `settings.yaml`.

- [ ] **Step 3: Implement multi-builder**

Load `config/strategies.yaml` by default. If CLI `--instrument` or `--strategy` is supplied, preserve current single-strategy override behavior. Otherwise build all enabled pairs.

- [ ] **Step 4: Register all strategies**

Call `node.trader.add_strategy(...)` for every built component.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_live_runner.py`
Expected: passes.

### Task 4: Full Verification

**Files:**
- No production files.

- [ ] **Step 1: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: ruff and pytest pass.

- [ ] **Step 2: Manual operational check**

After deploy, verify PM2 logs show multiple strategy registrations and bar subscriptions, with no timer callback `TypeError`.
