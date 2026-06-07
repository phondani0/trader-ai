# Position Recovery And Persistence Design

## Goal

Make deploys, PM2 restarts, crashes, and reconnects safe while positions are open.

After startup, the bot must know whether each exchange position is:

- bot-managed and safely resumable
- externally changed but safely reconciled
- external/unmanaged and blocked from new automation
- stale in local DB and no longer open at the venue
- inconsistent enough to require operator action

The bot must never assume SQLite is truth for actual open exposure. Bybit/Nautilus is the truth for what exists. SQLite is the truth for how the strategy intended to manage that exposure.

## Sources And Research Notes

- Nautilus live reconciliation aligns local execution state with venue reports and can generate missing events from cached or external state. Its docs recommend persisting all execution events to the cache database to reduce reliance on venue history and support full recovery: <https://nautilustrader.io/docs/latest/concepts/live/>
- Nautilus cache can be configured with a database backend for persistence between restarts. The cache stores orders, positions, accounts, instruments, and market data, but it is not a full application database replacement: <https://nautilustrader.io/docs/latest/concepts/cache/>
- Nautilus cache docs distinguish cache-backed restart-survivable state from strategy variables. Strategy variables are suitable for temporary calculations, not durable recovery state.
- OpenTelemetry metrics should be aggregate measurements with bounded labels. High-cardinality IDs like raw order IDs and trade IDs belong in logs/traces, not Prometheus labels: <https://opentelemetry.io/docs/concepts/signals/metrics/>
- SQLite skill guidance used here: parameterized queries, WAL mode, transactions for multi-row recovery updates, explicit migrations, and DB-level constraints.
- Reliability/observability skill guidance used here: alert on actionable state transitions, separate SLIs from debug details, and avoid noisy alerts.

## Current Gap

The current code enables Nautilus execution reconciliation:

```python
LiveExecEngineConfig(reconciliation=True)
```

But the live strategies also keep management state in RAM:

```python
_position_side
_entry_price
_best_price
_stop_price
_trailing_active
_bars_in_position
_entry_atr
_current_trade_id
_order_trade_ids
```

On restart, those fields reset. Nautilus may still reconcile the actual exchange position, but the strategy does not yet rebuild its stop/trailing/metadata state.

The existing `managed_positions` table is currently too thin and unused by the live strategies. It is not a reliable open-position ledger yet.

## Design Principle

Use three layers with explicit ownership:

```text
Bybit / Nautilus cache = actual execution truth
SQLite strategy DB     = durable management intent and recovery metadata
Strategy RAM           = fast runtime mirror hydrated from exchange + SQLite
```

Exchange/Nautilus wins for:

- whether a position exists
- side
- current quantity
- average entry price
- open orders
- fill/partial-fill state
- realized/unrealized PnL
- liquidation price
- leverage and margin mode where available

SQLite wins for:

- strategy owner
- business `trade_id`
- original signal metadata
- entry reason
- stop/trailing state
- `entry_atr`
- `best_price`
- `stop_price`
- `trailing_active`
- `bars_in_position`
- discovery journal link
- recovery status
- adoption reason
- deployment/run ID

If these disagree, the bot must not silently continue. It must reconcile according to a deterministic policy.

## Nautilus Cache Persistence

Add `CacheConfig(database=DatabaseConfig(...))` to `TradingNodeConfig`.

Preferred production backend: Redis. Our installed Nautilus version exposes `DatabaseConfig(type="redis")` as the default cache database configuration. No SQLite cache backend is visible from the local Nautilus config API, so SQLite must remain the application strategy-state database rather than the Nautilus cache backend.

Required cache settings:

- `flush_on_start=False`
- `use_trader_prefix=True`
- `use_instance_id=False` unless we intentionally isolate runs
- `buffer_interval_ms` set to a small value such as `100` if supported
- do not purge open orders or open positions

The implementation plan must verify Redis connectivity and local/prod configuration before enabling Nautilus cache persistence.

## Application DB Schema

Replace or migrate `managed_positions` into a real strategy-state ledger.

### `strategy_positions`

One row per active or historical strategy-managed position.

Primary key:

- `strategy_name`
- `symbol`
- `position_id`

Columns:

```text
strategy_name TEXT NOT NULL
symbol TEXT NOT NULL
instrument_id TEXT NOT NULL
venue TEXT NOT NULL
position_id TEXT NOT NULL
trade_id TEXT NOT NULL
side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT', 'BUY', 'SELL'))
quantity REAL NOT NULL CHECK(quantity >= 0)
exchange_entry_price REAL NOT NULL CHECK(exchange_entry_price >= 0)
strategy_entry_price REAL CHECK(strategy_entry_price >= 0)
entry_atr REAL CHECK(entry_atr >= 0)
stop_price REAL CHECK(stop_price >= 0)
best_price REAL CHECK(best_price >= 0)
trailing_active INTEGER NOT NULL DEFAULT 0 CHECK(trailing_active IN (0, 1))
bars_in_position INTEGER NOT NULL DEFAULT 0 CHECK(bars_in_position >= 0)
entry_time TEXT
last_bar_time TEXT
last_exchange_sync_time TEXT
status TEXT NOT NULL CHECK(status IN (
  'opening',
  'open',
  'recovered',
  'adopted',
  'external',
  'blocked',
  'closing',
  'closed',
  'externally_closed',
  'reconciliation_failed'
))
entry_reason TEXT
exit_reason TEXT
recovery_reason TEXT
discovery_journal_id INTEGER
signal_payload_json TEXT
exchange_snapshot_json TEXT
strategy_state_json TEXT
original_run_id TEXT
last_run_id TEXT
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
closed_at TEXT
```

Indexes:

```sql
CREATE INDEX idx_strategy_positions_status
ON strategy_positions(status);

CREATE INDEX idx_strategy_positions_symbol_status
ON strategy_positions(symbol, status);

CREATE INDEX idx_strategy_positions_trade_id
ON strategy_positions(trade_id);

CREATE INDEX idx_strategy_positions_updated_at
ON strategy_positions(updated_at);
```

### `strategy_orders`

Order-to-trade correlation survives restart.

Primary key:

- `client_order_id`

Columns:

```text
client_order_id TEXT PRIMARY KEY
venue_order_id TEXT
position_id TEXT
trade_id TEXT NOT NULL
strategy_name TEXT NOT NULL
symbol TEXT NOT NULL
instrument_id TEXT NOT NULL
side TEXT NOT NULL
order_type TEXT
quantity REAL CHECK(quantity >= 0)
status TEXT NOT NULL
reduce_only INTEGER NOT NULL DEFAULT 0 CHECK(reduce_only IN (0, 1))
submitted_at TEXT
filled_at TEXT
closed_at TEXT
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
raw_event_json TEXT
```

Indexes:

```sql
CREATE INDEX idx_strategy_orders_trade_id
ON strategy_orders(trade_id);

CREATE INDEX idx_strategy_orders_position_id
ON strategy_orders(position_id);

CREATE INDEX idx_strategy_orders_symbol_status
ON strategy_orders(symbol, status);
```

### `reconciliation_runs`

One row per startup/reconnect reconciliation.

Columns:

```text
run_id TEXT PRIMARY KEY
started_at TEXT NOT NULL
finished_at TEXT
trigger TEXT NOT NULL CHECK(trigger IN ('startup', 'reconnect', 'manual', 'test'))
result TEXT NOT NULL CHECK(result IN ('running', 'clean', 'recovered', 'blocked', 'failed'))
exchange_open_count INTEGER NOT NULL DEFAULT 0
db_open_count INTEGER NOT NULL DEFAULT 0
recovered_count INTEGER NOT NULL DEFAULT 0
adopted_count INTEGER NOT NULL DEFAULT 0
externally_closed_count INTEGER NOT NULL DEFAULT 0
blocked_count INTEGER NOT NULL DEFAULT 0
failed_count INTEGER NOT NULL DEFAULT 0
details_json TEXT
```

## SQLite Operational Requirements

- Enable `PRAGMA journal_mode=WAL`.
- Enable `PRAGMA foreign_keys=ON`.
- Use parameterized queries only.
- Use transactions for reconciliation updates:
  - create reconciliation run
  - update stale DB rows
  - insert adopted/recovered rows
  - mark blocked symbols
  - finish reconciliation run
- Never delete recovery history during normal operation. Mark rows closed/stale instead.
- Keep `managed_positions` migration backward-compatible:
  - read existing rows
  - write migrated rows to `strategy_positions`
  - leave old table in place for one release, or mark deprecated

## Runtime State Hydration

Introduce a small recovery component, not logic scattered across strategies.

Recommended module:

```text
trader_ai/execution/recovery.py
```

Recommended classes:

```python
class ExchangePositionSnapshot: ...
class StrategyPositionState: ...
class PositionRecoveryDecision: ...
class PositionRecoveryService: ...
```

The service is responsible for:

- reading Nautilus cache/portfolio open positions after reconciliation
- reading SQLite strategy state
- comparing exchange snapshots to DB rows
- deciding recover/adopt/block/close-stale
- hydrating strategy runtime state through a narrow strategy interface

Strategies should expose an explicit interface:

```python
def recover_position_state(self, state: StrategyPositionState) -> None: ...
def block_symbol(self, symbol: str, reason: str) -> None: ...
def persist_position_state(self) -> StrategyPositionState | None: ...
```

Do not reach into strategy private fields from the recovery service. The strategy owns how its RAM fields map to persisted state.

## Startup Reconciliation Flow

1. Build `TradingNode`.
2. Configure Nautilus cache persistence.
3. Let Nautilus execution reconciliation run.
4. Read exchange/Nautilus open positions.
5. Read SQLite open strategy positions.
6. Start one `reconciliation_runs` row.
7. For each symbol/position, classify.
8. Hydrate or block strategies.
9. Emit metrics/logs/traces.
10. Finish reconciliation row.
11. Only then allow strategies to submit new entry orders.

New entries must be gated by a recovery barrier:

```text
if recovery_not_complete:
    block all new entries
```

Per-symbol gates:

```text
if symbol in blocked_symbols:
    block new entries for that symbol
```

## Reconciliation Decision Matrix

### Exchange Open + DB Open + Same Side

Action: recover.

- Exchange quantity and entry price overwrite runtime actuals.
- DB stop/trailing metadata hydrates strategy state.
- If quantity differs slightly, treat as partial close/reduce and update DB.
- Emit `position.recovered`.

### Exchange Open + DB Missing

Action: external position.

Policy:

- Default first release: block automation for that symbol.
- Future release: adopt only when the strategy can construct a conservative management state from exchange data.

Emit:

- `position.unmanaged_detected`
- `position.blocked`

### DB Open + Exchange Flat

Action: mark externally closed.

- Mark DB row `externally_closed`.
- Clear runtime state if present.
- Do not submit any close order.
- Emit `position.externally_closed`.

### Exchange Open + DB Open + Opposite Side

Action: fail closed.

- Mark DB row `reconciliation_failed`.
- Block symbol.
- Emit critical alert.
- Do not trade that symbol.

### Exchange Open + DB Open + Quantity Mismatch

Action:

- If same side and exchange quantity is lower: partial close/reduce; update DB quantity and continue.
- If exchange quantity is higher: external add; block or adopt based on configured policy.
- If mismatch exceeds threshold: block.

Emit:

- `position.quantity_mismatch`
- `position.partially_reconciled` or `position.blocked`

### Open Orders Exist During Restart

Action:

- Load from Nautilus cache/open orders.
- Match `client_order_id` or `venue_order_id` to `strategy_orders`.
- If unmatched and not clearly external, block symbol.
- Never cancel unknown reduce-only protection orders automatically in first release.

## Exchange Changes While Bot Is Down

The recovery flow must explicitly handle:

- position manually closed
- position partially closed
- position size increased manually
- position flipped manually
- reduce-only order filled
- stop/TP order canceled
- funding and fees changed
- liquidation or ADL event
- exchange reports incomplete history because lookback is too short

Policy:

- Exchange reality always wins for actual exposure.
- DB metadata is used only if it still matches a compatible exchange position.
- If exchange changed in a way the strategy cannot safely explain, block automation for that symbol.
- Operator must see the reason in Grafana and logs.

## Adoption Policy

First implementation ships recovery, persistence, hydration, and blocking directly. It does not need a feature flag because the system is still running in paper/demo mode.

Automatic adoption is a later implementation step, not a flag around the whole recovery system. When adoption is implemented, it should be safe by construction:

- same configured symbol
- no existing DB row for another open trade on that symbol
- exchange side/quantity valid
- current ATR available
- strategy can compute conservative stop
- strategy creates a new `trade_id`
- state marked `adopted`

Do not adopt if:

- side conflicts with strategy signal constraints
- liquidation price too close
- missing instrument metadata
- quantity exceeds configured max exposure
- unknown open orders exist

## Observability

### Logs

Every reconciliation decision emits one structured log through the existing logger.

Required log types:

```text
position.reconciliation.started
position.reconciliation.completed
position.recovered
position.adopted
position.unmanaged_detected
position.externally_closed
position.quantity_mismatch
position.side_mismatch
position.reconciliation_failed
position.recovery_blocked
position.state_persisted
position.state_persist_failed
```

Fields:

```text
run_id
reconciliation_run_id
strategy
symbol
instrument_id
position_id
trade_id
side
exchange_quantity
db_quantity
exchange_entry_price
db_entry_price
decision
reason
recovery_status
```

### Metrics

Counters:

```text
trader_reconciliation_runs_total{trigger,result}
trader_reconciliation_positions_total{decision,strategy,symbol}
trader_positions_recovered_total{strategy,symbol,result}
trader_positions_adopted_total{strategy,symbol,result}
trader_positions_unmanaged_total{symbol,reason}
trader_positions_externally_closed_total{strategy,symbol}
trader_position_recovery_blocks_total{symbol,reason}
trader_position_state_persist_total{strategy,symbol,result}
trader_position_state_hydration_total{strategy,symbol,result}
trader_order_correlation_recovered_total{strategy,symbol,result}
```

Gauges:

```text
trader_recovery_blocked_symbols{symbol,reason}
trader_exchange_open_positions{venue}
trader_db_open_positions{strategy}
trader_unmanaged_open_positions{venue}
```

Histograms:

```text
trader_reconciliation_duration_seconds{trigger,result}
trader_position_state_staleness_seconds{strategy,symbol}
```

Prometheus label rules:

- Do not label metrics by raw `trade_id`, `order_id`, or `position_id`.
- Use those IDs in logs and trace attributes only.
- Keep `reason` values from a small enum.

### Traces

Use one trace per reconciliation run:

```text
position.reconciliation
```

Child spans:

```text
exchange.positions.load
strategy.positions.load
position.compare
position.recover
position.adopt
position.block
position.mark_externally_closed
strategy.hydrate
```

Existing trade traces do not need to remain single traces across restart. Durable correlation is through:

- `trade_id`
- `position_id`
- `strategy`
- `symbol`
- `original_run_id`
- `recovery_run_id`

## Alerts

Critical alert:

- `trader_position_recovery_blocks_total` increases for `side_mismatch`, `unknown_external_position`, or `quantity_increased_external`
- `trader_unmanaged_open_positions > 0`
- `trader_reconciliation_runs_total{result="failed"}` increases

Warning alert:

- `trader_positions_externally_closed_total` increases
- `trader_position_state_staleness_seconds` exceeds two bar intervals
- `trader_position_state_persist_total{result="error"}` increases

Noisy/non-alert dashboard-only:

- successful recovery count
- normal partial reduction
- clean reconciliation runs

## UI / Grafana Panels

Add panels:

- Reconciliation runs by result
- Current blocked symbols
- Exchange open positions vs DB open positions
- Unmanaged positions
- Recovered/adopted/external-close counts
- Position state persistence errors
- Recovery duration p95
- Position state staleness p95

Loki drilldowns:

```logql
{service_name="trader-ai"} | recovery_status="blocked"
{service_name="trader-ai"} | log_type="position.unmanaged_detected"
{service_name="trader-ai"} | trade_id="<trade-id>"
```

## Safety Invariants

- No new entries before startup recovery completes.
- No new entries for blocked symbols.
- Unknown external positions are not auto-managed in first release.
- SQLite cannot override exchange side or quantity.
- DB open + exchange flat is not treated as still open.
- Exchange open + DB missing is not treated as safely managed.
- Reconciliation is idempotent: running it twice without exchange changes produces the same state.
- Recovery code never submits market orders in first release.
- Recovery code never cancels unknown orders in first release.

## Testing Strategy

Unit tests:

- DB migration creates `strategy_positions`, `strategy_orders`, `reconciliation_runs`.
- `upsert_strategy_position` is parameterized and idempotent.
- `mark_externally_closed` updates status without deleting rows.
- decision matrix returns expected action for each exchange/DB case.
- strategy hydration sets the same fields the strategy needs for management.

Integration tests:

- simulated open exchange position + matching DB row hydrates strategy state.
- simulated exchange flat + DB open marks externally closed.
- simulated exchange open + DB missing blocks symbol.
- simulated side mismatch blocks symbol.
- simulated partial close updates DB quantity.
- restart barrier blocks entries until recovery completes.
- observability emits metrics/logs/spans for recovery.

Operational smoke:

- start bot with no positions: clean reconciliation.
- start bot with a manually opened demo position: blocked unmanaged position.
- start bot with a bot-created demo position and persisted DB row: recovered position.

## Rollout Plan

### Release 1: Default-On Safety And Persistence

- Add recovery DB schema.
- Add reconciliation service.
- Block unmanaged/external positions.
- Persist strategy state on bot-originated positions.
- Enable the recovery barrier by default in paper/demo deployments.
- No automatic adoption yet.

### Release 2: Hydration

- Hydrate matching DB + exchange positions.
- Resume stop/trailing management after restart.
- Add integration smoke for restart-with-open-position.

### Release 3: Safe Adoption

- Adopt compatible external positions with conservative stop.
- Keep blocking as the fallback when adoption requirements are not met.

## Success Criteria

- Deploying while positions are open does not create duplicate entries.
- Restarting with bot-managed positions resumes management from persisted state.
- Restarting with manually opened positions blocks automation for those symbols.
- Manually closed positions are marked externally closed and not closed again.
- Grafana shows every recovery decision.
- Operator can answer: “what happened to this position during restart?” without SSH or PM2 logs.
