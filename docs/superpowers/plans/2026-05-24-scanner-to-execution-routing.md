# Scanner To Execution Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow scanner discoveries such as NEAR, BILL, BSB, and GRASS to become executable strategy instruments through explicit gates instead of only being journaled.

**Architecture:** Add a small scanner execution router between `MarketScanner` and live Nautilus strategy construction. The router resolves CCXT symbols into supported Bybit instrument specs, applies liquidity/risk/slot gates, and produces strategy/instrument components. The first implementation must prove whether Nautilus can safely add strategies while the node is running; if not, it falls back to a bounded warmup-at-start executable universe.

**Tech Stack:** Python 3.12, NautilusTrader 1.226, CCXT, YAML config, pytest, ruff, pre-commit.

---

## File Structure

- Modify `config/strategies.yaml`: add scanner execution config under enabled strategies.
- Modify `src/trader_ai/config.py`: parse scanner execution config.
- Modify `src/trader_ai/runners/backtest.py`: support generic Bybit linear perpetual instrument construction from symbol metadata.
- Modify `src/trader_ai/runners/live.py`: build router, call scanner, create dynamic components, warm them, and register them.
- Create `src/trader_ai/runners/scanner_execution.py`: pure routing helpers with no Nautilus node side effects.
- Test `tests/test_scanner_execution.py`: router gates, symbol conversion, max slots, unsupported symbol rejection.
- Test `tests/test_live_runner.py`: live component matrix includes scanner candidates when configured.

---

## Task 1: Add Scanner Execution Config

**Files:**
- Modify: `config/strategies.yaml`
- Modify: `src/trader_ai/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Add this to `tests/test_config.py`:

```python
    def test_load_strategy_configs_reads_scanner_execution(self):
        strategies = load_strategy_configs(PROJECT_ROOT / "config" / "strategies.yaml")

        adaptive = strategies["adaptive_trend_rider"]

        self.assertTrue(adaptive.scanner_execution["enabled"])
        self.assertEqual(adaptive.scanner_execution["max_symbols"], 5)
        self.assertEqual(adaptive.scanner_execution["min_score"], 10.0)
        self.assertEqual(adaptive.scanner_execution["discovery_gate"], "hard")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_config.py::ConfigLoadingTest::test_load_strategy_configs_reads_scanner_execution
```

Expected: fail because `StrategyExecutionConfig` has no `scanner_execution`.

- [ ] **Step 3: Add config fields**

Update `StrategyExecutionConfig` in `src/trader_ai/config.py`:

```python
@dataclass(frozen=True)
class StrategyExecutionConfig:
    enabled: bool
    instruments: list[str]
    discovery_gate: str
    params: dict[str, object]
    scanner_execution: dict[str, object]
```

Update `load_strategy_configs()`:

```python
        scanner_execution = dict(spec.get("scanner_execution", {}))
        scanner_execution.setdefault("enabled", False)
        scanner_execution.setdefault("max_symbols", 0)
        scanner_execution.setdefault("min_score", 0.0)
        scanner_execution.setdefault("discovery_gate", discovery_gate)

        configs[str(name)] = StrategyExecutionConfig(
            enabled=bool(spec.get("enabled", False)),
            instruments=[str(item) for item in spec.get("instruments", [])],
            discovery_gate=discovery_gate,
            params=dict(spec.get("params", {})),
            scanner_execution=scanner_execution,
        )
```

Update `config/strategies.yaml`:

```yaml
adaptive_trend_rider:
  scanner_execution:
    enabled: true
    max_symbols: 5
    min_score: 10.0
    discovery_gate: hard
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_config.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add config/strategies.yaml src/trader_ai/config.py tests/test_config.py
git commit -m "feat: add scanner execution config"
```

---

## Task 2: Add Pure Scanner Execution Router

**Files:**
- Create: `src/trader_ai/runners/scanner_execution.py`
- Test: `tests/test_scanner_execution.py`

- [ ] **Step 1: Write failing router tests**

Create `tests/test_scanner_execution.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners.scanner_execution import ScannerExecutionConfig
from trader_ai.runners.scanner_execution import ccxt_symbol_to_bybit_instrument_name
from trader_ai.runners.scanner_execution import select_scanner_execution_symbols


class ScannerExecutionTest(unittest.TestCase):
    def test_ccxt_symbol_to_bybit_instrument_name(self):
        self.assertEqual(
            ccxt_symbol_to_bybit_instrument_name("NEAR/USDT:USDT"),
            "nearusdt_linear_bybit",
        )

    def test_select_scanner_execution_symbols_filters_and_limits(self):
        candidates = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0},
            {"symbol": "BTC/USDT:USDT", "score": 12.0},
            {"symbol": "XAU/USDT:USDT", "score": 4.0},
            {"symbol": "ETH/USDT", "score": 13.0},
        ]

        selected = select_scanner_execution_symbols(
            candidates,
            configured_symbols={"BTC/USDT:USDT"},
            config=ScannerExecutionConfig(max_symbols=1, min_score=10.0),
        )

        self.assertEqual(selected, ["NEAR/USDT:USDT"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_scanner_execution.py
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement router helpers**

Create `src/trader_ai/runners/scanner_execution.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScannerExecutionConfig:
    max_symbols: int
    min_score: float


def ccxt_symbol_to_bybit_instrument_name(symbol: str) -> str:
    base, rest = symbol.split("/", maxsplit=1)
    quote = rest.split(":", maxsplit=1)[0]
    return f"{base.lower()}{quote.lower()}_linear_bybit"


def select_scanner_execution_symbols(
    candidates: list[dict],
    *,
    configured_symbols: set[str],
    config: ScannerExecutionConfig,
) -> list[str]:
    selected: list[str] = []
    for candidate in sorted(candidates, key=lambda row: row.get("score", 0.0), reverse=True):
        symbol = str(candidate.get("symbol", ""))
        if not symbol.endswith("/USDT:USDT"):
            continue
        if symbol in configured_symbols:
            continue
        if float(candidate.get("score", 0.0) or 0.0) < config.min_score:
            continue
        selected.append(symbol)
        if len(selected) >= config.max_symbols:
            break
    return selected
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_scanner_execution.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/trader_ai/runners/scanner_execution.py tests/test_scanner_execution.py
git commit -m "feat: add scanner execution router"
```

---

## Task 3: Build Generic Bybit Linear Instruments

**Files:**
- Modify: `src/trader_ai/runners/backtest.py`
- Test: `tests/test_live_runner.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_live_runner.py`:

```python
    def test_build_backtest_components_supports_dynamic_bybit_linear_symbol(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        component = live.build_dynamic_bybit_component(
            settings,
            strategy_name="adaptive_trend_rider",
            ccxt_symbol="NEAR/USDT:USDT",
            strategy_params={"fast_ema_period": 10, "slow_ema_period": 20},
            discovery_gate="hard",
        )

        self.assertEqual(str(component.instrument.id), "NEARUSDT-LINEAR.BYBIT")
        self.assertEqual(component.instrument_spec.ccxt_symbol, "NEAR/USDT:USDT")
        self.assertEqual(component.strategy.config.discovery_gate, "hard")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_live_runner.py::LiveRunnerTimerCallbackTest::test_build_backtest_components_supports_dynamic_bybit_linear_symbol
```

Expected: fail because `build_dynamic_bybit_component` does not exist.

- [ ] **Step 3: Add generic builder**

Add to `src/trader_ai/runners/backtest.py`:

```python
def build_dynamic_bybit_linear_components(
    settings: NautilusSettings,
    *,
    ccxt_symbol: str,
    strategy_name: str,
    strategy_params: dict[str, object],
    discovery_gate: str,
) -> BacktestComponents:
    base = ccxt_symbol.split("/", maxsplit=1)[0].upper()
    raw_symbol = f"{base}USDT"
    instrument_spec = InstrumentSpec(
        provider="dynamic",
        template="dynamic_bybit_linear",
        venue="BYBIT",
        bar_type=f"{raw_symbol}-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL",
        asset_class="crypto_perpetual",
        starting_balance=settings.starting_balance,
        trade_size=settings.trade_size,
        ccxt_exchange="bybit",
        ccxt_symbol=ccxt_symbol,
        ccxt_timeframe="15m",
    )
    venue = Venue("BYBIT")
    instrument = _build_bybit_linear_perpetual(
        raw_symbol=raw_symbol,
        base_currency=Currency.from_str(base),
        venue=venue,
        price_precision=4,
        price_increment="0.0001",
        size_precision=2,
        size_increment="0.01",
        min_quantity="0.01",
    )
    bar_type = BarType.from_str(instrument_spec.bar_type)
    strategy = create_strategy(
        name=strategy_name,
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=instrument_spec.trade_size or settings.trade_size,
        fast_ema_period=settings.fast_ema_period,
        slow_ema_period=settings.slow_ema_period,
        discovery_gate=discovery_gate,
        ccxt_symbol=ccxt_symbol,
        params=strategy_params,
    )
    return BacktestComponents(
        venue=venue,
        instrument=instrument,
        bar_type=bar_type,
        instrument_name=f"{raw_symbol.lower()}_linear_bybit",
        strategy_name=strategy_name,
        strategy_config=strategy.config,
        instrument_spec=instrument_spec,
        strategy=strategy,
    )
```

If `Currency.from_str(base)` fails for unknown coins, replace `base_currency` with `Currency.from_str(base)` only after verifying Nautilus allows custom currency creation. If not, add an explicit dynamic currency factory helper and test it before using it here.

- [ ] **Step 4: Expose builder from live runner**

Add to `src/trader_ai/runners/live.py`:

```python
from trader_ai.runners.backtest import build_dynamic_bybit_linear_components


def build_dynamic_bybit_component(
    settings,
    *,
    strategy_name: str,
    ccxt_symbol: str,
    strategy_params: dict[str, object],
    discovery_gate: str,
):
    return build_dynamic_bybit_linear_components(
        settings,
        ccxt_symbol=ccxt_symbol,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        discovery_gate=discovery_gate,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_live_runner.py
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/trader_ai/runners/backtest.py src/trader_ai/runners/live.py tests/test_live_runner.py
git commit -m "feat: support dynamic bybit instruments"
```

---

## Task 4: Include Scanner Candidates At Live Startup

**Files:**
- Modify: `src/trader_ai/runners/live.py`
- Test: `tests/test_live_runner.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_live_runner.py`:

```python
    def test_build_live_components_includes_scanner_candidates(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        candidates = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0},
            {"symbol": "BTC/USDT:USDT", "score": 13.0},
        ]

        components = live.build_live_components(
            settings,
            scanner_candidates=candidates,
        )

        symbols = [component.instrument_spec.ccxt_symbol for component in components]
        self.assertIn("NEAR/USDT:USDT", symbols)
        self.assertEqual(symbols.count("BTC/USDT:USDT"), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_live_runner.py::LiveRunnerTimerCallbackTest::test_build_live_components_includes_scanner_candidates
```

Expected: fail because `scanner_candidates` is unsupported.

- [ ] **Step 3: Extend `build_live_components()`**

Modify signature in `src/trader_ai/runners/live.py`:

```python
def build_live_components(
    settings,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    strategies_path: str | Path | None = None,
    scanner_candidates: list[dict] | None = None,
) -> list[BacktestComponents]:
```

After static components are built, add:

```python
    configured_symbols = {
        component.instrument_spec.ccxt_symbol
        for component in components
        if component.instrument_spec.ccxt_symbol
    }

    for resolved_strategy_name, strategy_config in strategy_configs.items():
        scanner_cfg = strategy_config.scanner_execution
        if not bool(scanner_cfg.get("enabled", False)):
            continue
        selected_symbols = select_scanner_execution_symbols(
            scanner_candidates or [],
            configured_symbols=configured_symbols,
            config=ScannerExecutionConfig(
                max_symbols=int(scanner_cfg.get("max_symbols", 0)),
                min_score=float(scanner_cfg.get("min_score", 0.0)),
            ),
        )
        for symbol in selected_symbols:
            components.append(
                build_dynamic_bybit_component(
                    settings,
                    strategy_name=resolved_strategy_name,
                    ccxt_symbol=symbol,
                    strategy_params=strategy_config.params,
                    discovery_gate=str(scanner_cfg.get("discovery_gate", "hard")),
                )
            )
            configured_symbols.add(symbol)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_live_runner.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/trader_ai/runners/live.py tests/test_live_runner.py
git commit -m "feat: route scanner candidates at startup"
```

---

## Task 5: Runtime Dynamic Add Probe

**Files:**
- Create: `tests/test_nautilus_runtime_add_probe.py`
- Modify: `src/trader_ai/runners/live.py` only if the probe proves runtime add is safe.

- [ ] **Step 1: Add a probe test with explicit skip**

Create `tests/test_nautilus_runtime_add_probe.py`:

```python
from __future__ import annotations

import unittest


class NautilusRuntimeAddProbeTest(unittest.TestCase):
    @unittest.skip("Manual probe: verify Nautilus supports adding strategy/instrument after node.run starts.")
    def test_runtime_add_strategy_probe(self):
        self.fail("Run manually against a disposable demo node before enabling runtime scanner routing.")
```

- [ ] **Step 2: Manual engineering probe**

Run a disposable local script that:

```python
node = build_live_node(settings_path="config/settings.yaml")
component = build_dynamic_bybit_component(
    load_settings("config/settings.yaml"),
    strategy_name="adaptive_trend_rider",
    ccxt_symbol="NEAR/USDT:USDT",
    strategy_params={"fast_ema_period": 10, "slow_ema_period": 20},
    discovery_gate="hard",
)
node.trader.add_strategy(component.strategy)
```

Expected: either Nautilus accepts the strategy before `node.run()`, or rejects runtime mutation. Do not deploy runtime mutation until this is proven on demo.

- [ ] **Step 3: Choose runtime path**

If runtime mutation is safe, implement a `ScannerExecutionRouter` timer callback that:

```python
candidates = _scanner.get_active_candidates()
new_components = build_scanner_components(settings, candidates)
warmup_live_components(new_components, cache_dir=DEFAULT_CACHE_DIR, bars_limit=settings.warmup_bars)
for component in new_components:
    node.trader.add_strategy(component.strategy)
```

If runtime mutation is not safe, keep scanner execution as startup-only and use PM2 scheduled controlled restarts as the operational bridge until a safer dynamic architecture is proven.

- [ ] **Step 4: Commit probe**

```bash
git add tests/test_nautilus_runtime_add_probe.py
git commit -m "test: add runtime scanner routing probe"
```

---

## Task 6: Operational Verification

**Files:**
- Modify: `README.md` if live-run commands changed.
- No code changes if commands are unchanged.

- [ ] **Step 1: Local verification**

Run:

```bash
uv run pytest tests/test_scanner_execution.py tests/test_live_runner.py tests/test_warmup.py
uv run pre-commit run --all-files
```

Expected: all pass.

- [ ] **Step 2: Dry run component matrix**

Run:

```bash
uv run python -c "from trader_ai.config import load_settings; from trader_ai.runners.live import build_live_components; s=load_settings('config/settings.yaml'); c=build_live_components(s, scanner_candidates=[{'symbol':'NEAR/USDT:USDT','score':14.0}]); print([(x.strategy_name, x.instrument_spec.ccxt_symbol) for x in c])"
```

Expected output includes:

```python
('adaptive_trend_rider', 'NEAR/USDT:USDT')
```

- [ ] **Step 3: Server verification after deploy**

On VPS:

```bash
pm2 restart trader-ai
pm2 logs trader-ai --lines 200
```

Expected logs:

```text
Warmup replayed ... adaptive_trend_rider/nearusdt_linear_bybit; ready=True
Subscribed NEARUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL bars
Decision status NEARUSDT-LINEAR.BYBIT: ...
```

---

## Self-Review

- Spec coverage: plan covers config, routing, dynamic instrument creation, live startup inclusion, runtime mutation proof, tests, and PM2 verification.
- Placeholder scan: no TBD/TODO/fill-in placeholders remain.
- Type consistency: scanner candidates are `list[dict]`, selected symbols are CCXT symbols, generated instrument names use `<base>usdt_linear_bybit`, and dynamic components return existing `BacktestComponents`.
- Risk note: runtime scanner-to-execution must not mutate a running Nautilus node until the probe proves it is safe. The startup inclusion path is the safe first implementation.
