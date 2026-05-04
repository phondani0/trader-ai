# Nautilus-Only Pivot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy trader-ai runtime and leave NautilusTrader as the only supported system in the repository.

**Architecture:** Keep `nautilus/` as the only application. Root-level commands become thin wrappers that dispatch into `nautilus/`. Delete legacy runtime code, tests, and artifacts instead of preserving compatibility.

**Tech Stack:** Python, `uv`, NautilusTrader, unittest.

---

### Task 1: Lock the new root command contract with tests

**Files:**
- Create: `tests/test_nautilus_pivot.py`
- Create: `scripts/nautilus_dispatch.py`

- [ ] Add root wrapper tests for `start`, `backtest`, and `benchmark`.
- [ ] Verify they fail before implementation.
- [ ] Implement minimal dispatch helpers.
- [ ] Re-run the focused tests.

### Task 2: Remove legacy strategy adapter dependency

**Files:**
- Modify: `nautilus/tests/test_strategy_registry.py`
- Modify: `nautilus/src/nautilus_app/strategies/registry.py`
- Delete: `nautilus/src/nautilus_app/strategies/legacy_trader_ai.py`

- [ ] Change tests to require Nautilus-native strategies only.
- [ ] Verify failure.
- [ ] Remove the legacy adapter from the registry.
- [ ] Re-run Nautilus strategy registry tests.

### Task 3: Replace root entrypoints with Nautilus wrappers

**Files:**
- Modify: `pyproject.toml`
- Replace: `scripts/backtest.py`
- Replace: `scripts/benchmark.py`
- Replace: `scripts/trade.py`
- Replace: `scripts/adopt_position.py`
- Create: `scripts/start.py`
- Create: `scripts/papertrade.py`

- [ ] Point root scripts at wrapper modules only.
- [ ] Make unsupported legacy commands fail clearly or remove them from the root script surface.
- [ ] Re-run root wrapper tests.

### Task 4: Delete legacy runtime code and tests

**Files:**
- Delete directory: `src/`
- Delete obsolete tests under: `tests/` except pivot-wrapper coverage

- [ ] Remove the old runtime tree.
- [ ] Remove tests that only validate the deleted runtime.
- [ ] Keep only pivot-wrapper tests at root.

### Task 5: Purge legacy benchmark artifacts and docs

**Files:**
- Delete legacy artifacts under: `data/strategy_approvals*.json`
- Delete directories: `data/exports/`, `data/plots/`
- Modify: `README.md`
- Modify: `.github/workflows/deploy.yml`

- [ ] Remove old benchmark artifact files.
- [ ] Rewrite README around Nautilus only.
- [ ] Remove the old PM2 deploy path and keep Nautilus verification only.

### Task 6: Verify the pivot

**Files:**
- Verify: `tests/test_nautilus_pivot.py`
- Verify: `nautilus/tests/`

- [ ] Run root wrapper tests.
- [ ] Run Nautilus test suite.
- [ ] Run at least one root wrapper command and one direct Nautilus command.
