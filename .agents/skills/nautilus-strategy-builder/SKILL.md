---
name: nautilus-strategy-builder
description: >
  Build, register, backtest, and iteratively tune NautilusTrader strategies for
  crypto perpetuals on 15-minute bars.  The skill codifies the full feedback
  loop: scaffold → backtest → analyze → tune → validate.  Use when the user
  says "make a strategy", "build a new strategy", "add a strategy", or
  "create a trading strategy".
---

# NautilusTrader Strategy Builder

## Overview

This skill walks you through creating a new NautilusTrader strategy for the
`trader-ai` repo.  The codebase lives under `src/trader_ai/` and targets
**crypto perpetuals on 15-minute bars** via CCXT + NautilusTrader 1.226.

## Architecture Quick-Ref

```
src/trader_ai/
├── strategies/
│   ├── registry.py           # list_strategies() + create_strategy()
│   ├── adaptive_trend_rider.py
│   ├── volume_spike_trend.py
│   └── macd_demo.py
├── cli/
│   ├── backtest.py            # uv run backtest
│   └── benchmark.py           # uv run benchmark (DEFAULT_STRATEGIES list)
├── runners/
│   └── backtest.py            # run_backtest() engine wrapper
├── config.py                  # NautilusSettings, InstrumentSpec
└── data.py                    # CCXT data fetcher + cache
config/
├── settings.yaml              # ccxt_limit, ema periods, log level
└── instruments.yaml           # instrument definitions
tests/
├── test_strategy_registry.py  # list check + create check
└── test_cli_benchmark.py      # DEFAULT_STRATEGIES assertion
```

## Step 1 — Design the Strategy

Before writing code, answer:

1. **Market regime target**: Trending, ranging, or volatile?
2. **Entry logic**: What combination of indicators triggers a trade?
3. **Exit logic**: Fixed TP/SL? Trailing? Time-based?
4. **Filters**: What prevents the strategy from trading in the wrong regime?

### Indicator Availability (NautilusTrader 1.226)

All indicators live in `nautilus_trader.indicators`:

| Indicator | Import |
|---|---|
| EMA | `ExponentialMovingAverage` |
| SMA | `SimpleMovingAverage` |
| RSI | `RelativeStrengthIndex` |
| ATR | `AverageTrueRange` |
| Bollinger Bands | `BollingerBands` |
| Donchian Channel | `DonchianChannel` |
| MACD | `MovingAverageConvergenceDivergence` |
| Stochastics | `Stochastics` |
| Keltner Channel | `KeltnerChannel` |
| On Balance Volume | `OnBalanceVolume` |
| VWAP | `VolumeWeightedAveragePrice` |
| CCI | `CommodityChannelIndex` |
| Aroon | `AroonOscillator` |
| ROC | `RateOfChange` |
| Linear Regression | `LinearRegression` |

### Critical Pitfall: Indicator Scales

> **RSI returns 0.0–1.0, NOT 0–100.**
> A "50" RSI reading is `0.50` in NautilusTrader.
> Double-check every indicator's output range before setting thresholds.

### Order Factory Methods

```python
self.order_factory.market(instrument_id, side, quantity)
self.order_factory.limit(instrument_id, side, quantity, price)
self.order_factory.stop_market(instrument_id, side, quantity, trigger_price)
self.order_factory.bracket(...)  # entry + SL + TP
self.order_factory.trailing_stop_market(...)
```

## Step 2 — Scaffold the Strategy File

Create `src/trader_ai/strategies/<strategy_name>.py` using this template:

```python
from __future__ import annotations

from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import <YourIndicators>
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class <StrategyName>Config(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 8      # Required by registry
    slow_ema_period: int = 21     # Required by registry
    # ... strategy-specific params ...


class <StrategyName>Strategy(Strategy):
    def __init__(self, config: <StrategyName>Config):
        super().__init__(config)
        # Initialize indicators and state

    def on_start(self):
        # Register indicators for bar updates
        self.register_indicator_for_bars(self.config.bar_type, self.indicator)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
        # Core logic: check indicators, manage positions

    def on_stop(self):
        self.close_all_positions(self.config.instrument_id)

    def _submit_market(self, side: OrderSide):
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            self.config.instrument_id,
            side,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
```

> **Note**: `fast_ema_period` and `slow_ema_period` must exist in the config
> even if unused, because `registry.py:create_strategy()` passes them.

## Step 3 — Register the Strategy

### `src/trader_ai/strategies/registry.py`

1. Add imports at the top
2. Add the name to `list_strategies()` return list (alphabetical order)
3. Add an `if name == "your_strategy":` block in `create_strategy()`

### `src/trader_ai/cli/benchmark.py`

Add the strategy name to `DEFAULT_STRATEGIES` list.

## Step 4 — Run the Iterative Backtest Loop

This is the core feedback cycle.  Run it until the success criteria are met.

### 4a — First Run

```bash
uv run backtest --instrument btcusdt_perp_binance \
  --strategy <name> --data-source ccxt \
  --output-dir output/<name>_btc
```

If `ccxt_limit` in `config/settings.yaml` is < 10000, increase it.
The strategy needs 10,000+ bars (100+ days) for statistical significance.

### 4b — Analyze Results

Run this analysis script after each backtest:

```python
import pandas as pd, re

pos = pd.read_csv('output/<name>_btc/positions.csv')
def pnl(v):
    m = re.search(r'-?\d+(?:\.\d+)?', str(v))
    return float(m.group(0)) if m else 0.0

pos['pnl'] = pos['realized_pnl'].apply(pnl)
w = pos[pos['pnl'] > 0]
l = pos[pos['pnl'] < 0]
comm = pos['commissions'].apply(lambda x: pnl(str(x))).sum()

print(f'Trades: {len(pos)}')
print(f'Win Rate: {len(w)/len(pos)*100:.1f}%')
print(f'Avg Win: ${w["pnl"].mean():.2f}')
print(f'Avg Loss: ${l["pnl"].mean():.2f}')
print(f'R:R: {abs(w["pnl"].mean()/l["pnl"].mean()):.2f}')
print(f'Profit Factor: {w["pnl"].sum()/abs(l["pnl"].sum()):.2f}')
print(f'Net PnL: ${pos["pnl"].sum():.2f}')
print(f'Commissions: ${comm:.2f}')
print(f'Gross PnL: ${pos["pnl"].sum()+comm:.2f}')
```

### 4c — Diagnose & Tune

| Symptom | Diagnosis | Fix |
|---|---|---|
| 0 trades | Filters too tight or indicator scale wrong | Check RSI 0-1 scale, relax thresholds |
| 500+ trades | Overtrading, no selectivity | Add cooldown, raise volume/ATR gates |
| Win rate < 30% | Entering in wrong regime | Add trend/regime filter |
| R:R < 1.5 | Winners cut too early | Widen trailing stop activation |
| Negative gross PnL | No edge | Rethink entry logic fundamentally |
| Positive gross, negative net | Commission drag | Reduce trade count or increase selectivity |

### 4d — Repeat Until Success Criteria Met

**Minimum viable metrics:**
- Profit Factor > 1.2
- Win/Loss Ratio > 1.5
- Net PnL positive over 10,000+ bars
- Trades > 20 (statistically meaningful)

**Top-tier metrics (target):**
- Profit Factor > 1.5
- Win Rate > 40% with R:R > 2.0
- Max Drawdown < 15% of capital

## Step 5 — Cross-Asset Validation

Test on ETH to check robustness:

```bash
uv run backtest --instrument ethusdt_perp_binance \
  --strategy <name> --data-source ccxt \
  --output-dir output/<name>_eth
```

A strategy that works on BTC but loses on ETH is acceptable (BTC specialist).
A strategy that works on both is stronger.

## Step 6 — Update Tests

### `tests/test_strategy_registry.py`

Update the expected list in `test_registry_lists_expected_strategy_names`.

### `tests/test_cli_benchmark.py`

Update `DEFAULT_STRATEGIES` assertion in
`test_benchmark_parser_accepts_strategy_overrides`.

Run all tests:

```bash
uv run python -m unittest discover -s tests
```

## Step 7 — Commit

Use separate commits per logical change:

```bash
git add src/trader_ai/strategies/<name>.py
git commit -m "feat: add <name> strategy with <key_features>"

git add src/trader_ai/strategies/registry.py src/trader_ai/cli/benchmark.py
git commit -m "feat: register <name> in strategy registry and benchmark defaults"

git add tests/
git commit -m "test: update test expectations for <name>"
```

## Common Patterns

### Software Trailing Stop (Backtest-Compatible)

NautilusTrader's exchange-side trailing stops don't work in backtest mode.
Implement trailing stops in software:

```python
# In _manage_position():
activation_distance = self.config.trailing_activation_atr_mult * self._entry_atr
if self._position_side == OrderSide.BUY:
    if self._best_price - self._entry_price >= activation_distance:
        self._trailing_active = True

if self._trailing_active:
    trail_distance = self.config.trailing_stop_atr_mult * current_atr
    if self._position_side == OrderSide.BUY:
        new_stop = self._best_price - trail_distance
        self._stop_price = max(self._stop_price, new_stop)  # Only tighten, never widen
```

### Cooldown After Exit

Prevents chop re-entry:

```python
self._cooldown_remaining: int = 0

# In on_bar():
if self._cooldown_remaining > 0:
    self._cooldown_remaining -= 1

# In _close_and_reset():
self._cooldown_remaining = self.config.cooldown_bars
```

### Volume Filter with Deque

```python
self._volumes: deque[float] = deque(maxlen=config.volume_window)

# In on_bar():
self._volumes.append(bar.volume.as_double())
avg_volume = sum(self._volumes) / len(self._volumes)
is_spike = bar.volume.as_double() >= avg_volume * self.config.volume_multiplier
```
