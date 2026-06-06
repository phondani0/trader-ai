# Live Execution Instrumentation Design

## Goal

Instrument the live trading process so every meaningful trading transition is visible in Grafana through Prometheus metrics, Loki logs, and Tempo traces.

The target is the live bot path only. Backtests do not need this execution instrumentation in the first implementation.

## Non-Goals

- Do not use SQLite as part of the observability pipeline.
- Do not infer order activity from logs after the fact.
- Do not add fake metrics that are not emitted from real execution paths.
- Do not expose Grafana publicly.
- Do not add auto-tuning decisions in this step.

SQLite can remain an audit or strategy-memory store, but Grafana execution observability must come from OpenTelemetry signals.

## Design Decision

Use one trace per trade execution, with child spans for the concrete events inside that execution.

This keeps the causal story intact:

```text
signal -> risk check -> order submit -> accept/reject -> fill -> position open/update -> close -> settle
```

Prometheus handles counters, rates, latency, and distributions. Loki stores searchable structured event logs. Tempo shows the timeline of one trade.

## Event Model

Every execution event uses the same base identifiers:

- `event_name`
- `strategy`
- `symbol`
- `side`
- `trade_id`
- `order_id`
- `position_id`
- `result`
- `reason`
- `venue`
- `environment`

Numeric fields are added where relevant:

- `quantity`
- `notional_usdt`
- `entry_price`
- `exit_price`
- `fill_price`
- `pnl_usdt`
- `pnl_pct`
- `fees_usdt`
- `slippage_bps`
- `latency_ms`

The required event names are:

- `signal.generated`
- `risk.check`
- `risk.blocked`
- `order.submit`
- `order.accepted`
- `order.rejected`
- `order.canceled`
- `order.fill`
- `position.open`
- `position.update`
- `order.close.submit`
- `position.close`
- `trade.settle`
- `kill_switch.triggered`
- `runtime.error`

## Logs

Each execution event emits exactly one structured log through the existing project logger.

The log must include `log_type` and the shared identifiers. Example log types:

- `trade.signal.generated`
- `trade.risk.checked`
- `trade.order.submitted`
- `trade.order.filled`
- `trade.position.opened`
- `trade.position.closed`
- `trade.settled`

Logs are for event history and debugging context. They should carry enough fields to search by `trade_id`, `order_id`, `strategy`, or `symbol` in Loki.

## Metrics

Metrics are emitted from the same execution instrumentation helper that writes logs and spans.

Required counters:

- `trader_signals_total{strategy,symbol,side,result}`
- `trader_risk_checks_total{strategy,symbol,result,reason}`
- `trader_orders_total{strategy,symbol,side,result}`
- `trader_order_fills_total{strategy,symbol,side}`
- `trader_order_rejections_total{strategy,symbol,reason}`
- `trader_order_cancellations_total{strategy,symbol,reason}`
- `trader_positions_open_total{strategy,symbol,side}`
- `trader_positions_closed_total{strategy,symbol,side,result}`
- `trader_kill_switch_events_total{reason}`
- `trader_runtime_errors_total{component,reason}`

Required histograms:

- `trader_order_latency_seconds{strategy,symbol,result}`
- `trader_signal_to_fill_seconds{strategy,symbol}`
- `trader_trade_lifetime_seconds{strategy,symbol,result}`
- `trader_trade_pnl_usdt{strategy,symbol,result}`
- `trader_trade_slippage_bps{strategy,symbol}`
- `trader_trade_fees_usdt{strategy,symbol}`

Prometheus should not store high-cardinality values such as raw `order_id`, `trade_id`, or `position_id` as metric labels. Those belong in logs and trace attributes.

## Traces

The root trace is `trade.execution`.

Child spans:

- `signal.generated`
- `risk.check`
- `order.submit`
- `order.accepted`
- `order.rejected`
- `order.fill`
- `position.open`
- `position.update`
- `order.close.submit`
- `position.close`
- `trade.settle`

Trace attributes include the shared identifiers. Raw IDs are allowed as trace attributes because traces are sampled and used for debugging, unlike Prometheus labels.

Root trace status:

- `OK` when the trade settles normally
- `ERROR` when the trade execution ends in rejection, runtime error, or forced kill-switch closure

## Correlation

The system must generate or preserve a `trade_id` at signal time and carry it through every later event.

If the venue order ID is not available at submit time, the client order ID becomes the temporary `order_id`; once the venue order ID is known, both IDs are logged and attached to spans.

This gives three reliable lookup paths:

- Grafana metric spike -> Loki query by `strategy` and `symbol`
- Loki event -> Tempo trace by `trade_id`
- Tempo span -> Loki logs by `trade_id` or `order_id`

## Grafana Panels

Add dashboard panels for:

- Orders submitted by strategy and result
- Order fills by strategy and symbol
- Rejections by reason
- Open position events by strategy
- Closed position events by strategy
- PnL distribution by strategy
- Signal-to-fill latency
- Order submit-to-fill latency
- Kill switch events
- Runtime errors

Existing scanner, journal, warmup, and runtime activation panels stay. They are health metrics, while this spec adds execution and trading outcome metrics.

## Implementation Shape

Add a small execution-domain instrumentation module instead of spreading metric names across call sites.

Proposed module:

```text
trader_ai/execution/instrumentation.py
```

Responsibilities:

- validate event names
- normalize attributes
- call the existing observability logger with structured fields
- increment or record metrics through the existing metrics wrapper
- create spans through the existing tracing wrapper
- avoid high-cardinality Prometheus labels

Call sites should say what happened, not hand-code the entire observability payload.

Example call shape:

```python
execution_instrumentation.order_submitted(
    strategy=strategy_name,
    symbol=symbol,
    side=side,
    trade_id=trade_id,
    order_id=client_order_id,
    quantity=quantity,
    notional_usdt=notional_usdt,
)
```

## Live Hook Points

The implementation must hook the live process at these boundaries:

- signal generation
- risk approval or risk block
- market order submit
- exchange accept or rejection
- fill or partial fill
- position open
- position update
- close order submit
- position close
- realized trade settlement
- kill switch trigger
- runtime exception

The live code path is in this repository, so the execution hooks and dashboard contract must be kept together here.

## EDOT Skill Notes

The EDOT Python skill recommends auto-instrumentation for Python services that do not already have an OpenTelemetry agent.

This project already has explicit OpenTelemetry setup for logs, metrics, and traces, so the first implementation should extend the existing SDK wrapper instead of replacing it with `opentelemetry-instrument`.

If the live VPS bot does not already initialize OpenTelemetry, then it should either:

- reuse this repo's explicit observability wrapper, or
- adopt EDOT auto-instrumentation consistently at process startup

The live process must not run two competing OpenTelemetry initializers at the same time.

## Testing

Unit tests:

- execution instrumentation helper emits the expected metric names
- high-cardinality IDs are excluded from metric labels
- structured logs require `log_type`
- trace spans include `trade_id`, `strategy`, and `symbol`

Integration tests:

- fake execution instrumentation sequence from signal to settle emits all required events
- rejected order emits rejection metric and error span
- kill-switch path emits kill-switch metric and log
- Grafana dashboard JSON references the new metric names

Smoke test:

- run a synthetic paper execution sequence locally
- verify Prometheus receives counters
- verify Loki receives logs
- verify Tempo receives a `trade.execution` trace

## Rollout

1. Add execution instrumentation helper.
2. Add unit tests for logs, metrics, and spans.
3. Instrument live signal, risk, order, fill, position, close, and settle paths.
4. Add Grafana panels.
5. Run local smoke with the observability stack.
6. Deploy and verify a paper-mode order execution in Grafana.

## Success Criteria

For any live trade, the operator can answer:

- why the signal happened
- whether risk allowed or blocked it
- when the order was submitted
- whether the venue accepted, rejected, or filled it
- when the position opened
- when and why it closed
- realized PnL, fees, and slippage
- which strategy and symbol were responsible

The answer must be visible through Grafana without reading PM2 logs directly.
