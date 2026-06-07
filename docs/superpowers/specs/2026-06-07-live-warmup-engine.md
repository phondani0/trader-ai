# Live Warmup Engine

> **Status:** Implemented and live  
> **Components:** `live.py`, `macd_demo.py`, `volume_spike_trend.py`, `config.py`

## Problem

Indicators (EMA-21, RSI-14, ATR-14, ATR MA-50) need N historical bars before they produce meaningful values. When the live node boots and connects to the exchange WebSocket, all indicators start at zero. Without warmup the bot would either trade on garbage signals or sit idle for 12+ hours waiting for enough 15m bars to arrive organically.

## Solution

Pre-load historical bars via CCXT REST and replay them through each strategy's indicators before the WebSocket stream begins.

## Architecture

```
build_live_node()
  └─ warmup_live_components()            # startup path
       ├─ load_ccxt_bars()               # CCXT REST, disk-cached
       ├─ _closed_warmup_bars()          # drop incomplete candle
       ├─ strategy.seed_warmup(bars)     # replay through indicators
       └─ _indicators_ready()            # readiness check

activate_runtime_scanner_symbols()       # runtime path
  └─ strategy.activate_component()
       ├─ context.seed_warmup(bars)
       └─ context._indicators_ready()    # reject if not ready
```

## Two Warmup Paths

### Startup Warmup

Triggered once during `build_live_node()` when `settings.warmup_enabled` is `true`. For every configured component:

1. Fetch `warmup_bars` (default 120) of history via `load_ccxt_bars()`
2. Filter out the incomplete candle via `_closed_warmup_bars()` — any bar whose close time is in the future is dropped
3. Call `strategy.seed_warmup(bars, last_replayed_close_ns=...)` which feeds bars through all indicators without triggering trades
4. Check `_indicators_ready()` and log readiness
5. Emit `trader_warmups_total` metric with `success` / `skipped` / `error` tags

### Runtime Scanner Warmup

When the `MarketScanner` discovers a new symbol mid-session (e.g., `SOL/USDT:USDT`):

1. `activate_runtime_scanner_symbols()` builds a dynamic component for the symbol
2. Fetches historical bars via CCXT REST
3. Creates a new `_RuntimeVolumeContext` or `_RuntimeDemoEMAContext`
4. Calls `context.seed_warmup(bars)` to warm indicators
5. If `_indicators_ready()` returns `False` → activation is **rejected**
6. If ready → subscribes to the bar type and begins trading

## Duplicate-Bar Guard

After warmup, the WebSocket often replays the same recent bars that were fetched via REST. The guard in each strategy's `on_bar()` prevents double-processing:

```python
def _is_warmup_replayed_bar(self, bar: Bar) -> bool:
    return (
        self._last_warmup_close_ns is not None
        and bar.ts_event <= self._last_warmup_close_ns
    )
```

This prevents indicators from being fed the same bar twice (which would skew EMA/RSI values) and prevents phantom trades on already-seen data.

## Strategy Implementations

### `seed_warmup()` in `demo_ema_cross`

Feeds EMA (fast + slow), RSI, ATR, fills the ATR moving-average history deque, and seeds `_prev_fast`/`_prev_slow` for crossover detection on the very first live bar.

### `seed_warmup()` in `volume_spike_trend`

Same indicator seeding, plus fills the `_volumes` deque (needed for volume-spike detection: "is current volume ≥ 3× the 20-bar average?").

## Configuration

```yaml
# config/settings.yaml
warmup_enabled: true   # master on/off switch
warmup_bars: 120       # 120 × 15m = 30 hours of history
```

120 bars is sufficient for the slowest indicator chain: ATR (14 bars) + ATR MA (50 bars) = 64 minimum, with a healthy margin.

## Observability

| Signal | Type | Emitted when |
|---|---|---|
| `warmup.completed` | Structured log | Per-component warmup succeeds |
| `warmup.skipped` | Structured log | Strategy has no `seed_warmup()` method |
| `warmup.failed` | Structured log | Exception during fetch or replay |
| `trader_warmups_total` | OTel counter | Every warmup attempt, tagged `result=success\|skipped\|error` |
| Warmup diagnostics | Structured log | `volume_spike_trend` logs indicator readiness every N bars via `_log_warmup_status()` |
