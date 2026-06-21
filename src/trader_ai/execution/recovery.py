"""
Position Recovery Service.

Reconciles exchange positions with SQLite strategy state on startup/restart.
Implements the decision matrix from the position-recovery-persistence-design spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from trader_ai.core.database import DatabaseManager
from trader_ai.observability.logging import logger
from trader_ai.observability.metrics import metrics
from trader_ai.observability.tracing import tracer


@dataclass(frozen=True)
class ExchangePositionSnapshot:
    instrument_id: str
    symbol: str
    side: str
    quantity: float
    avg_entry_price: float
    unrealized_pnl: float | None = None


@dataclass(frozen=True)
class StrategyPositionState:
    strategy_name: str
    symbol: str
    instrument_id: str
    position_id: str
    trade_id: str
    side: str
    quantity: float
    entry_price: float
    entry_atr: float | None = None
    stop_price: float | None = None
    best_price: float | None = None
    trailing_active: bool = False
    bars_in_position: int = 0
    strategy_state_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReconciliationDecision:
    symbol: str
    action: str
    reason: str
    exchange_snapshot: ExchangePositionSnapshot | None = None
    db_state: StrategyPositionState | None = None


def strategy_position_state_from_row(row: dict[str, Any]) -> StrategyPositionState:
    """Convert a raw SQLite strategy_positions row into recovery state."""
    strategy_state_json = row.get("strategy_state_json")
    if isinstance(strategy_state_json, str) and strategy_state_json:
        strategy_state_json = json.loads(strategy_state_json)
    elif not isinstance(strategy_state_json, dict):
        strategy_state_json = None

    return StrategyPositionState(
        strategy_name=str(row["strategy_name"]),
        symbol=str(row["symbol"]),
        instrument_id=str(row["instrument_id"]),
        position_id=str(row["position_id"]),
        trade_id=str(row["trade_id"]),
        side=str(row["side"]),
        quantity=float(row.get("quantity") or 0.0),
        entry_price=float(
            row.get("strategy_entry_price")
            if row.get("strategy_entry_price") is not None
            else row.get("exchange_entry_price") or 0.0
        ),
        entry_atr=(
            float(row["entry_atr"]) if row.get("entry_atr") is not None else None
        ),
        stop_price=(
            float(row["stop_price"]) if row.get("stop_price") is not None else None
        ),
        best_price=(
            float(row["best_price"]) if row.get("best_price") is not None else None
        ),
        trailing_active=bool(row.get("trailing_active")),
        bars_in_position=int(row.get("bars_in_position") or 0),
        strategy_state_json=strategy_state_json,
    )


_SIDE_ALIASES: dict[str, str] = {
    "BUY": "LONG",
    "SELL": "SHORT",
    "LONG": "LONG",
    "SHORT": "SHORT",
}


def _normalize_side(side: str) -> str:
    return _SIDE_ALIASES.get(side.upper(), side.upper())


def _has_valid_strategy_state(state: StrategyPositionState) -> bool:
    return (
        state.quantity > 0
        and state.entry_price > 0
        and state.entry_atr is not None
        and state.entry_atr > 0
        and state.stop_price is not None
        and state.stop_price > 0
        and state.best_price is not None
        and state.best_price > 0
    )


class PositionRecoveryService:
    """Reconciles exchange reality with SQLite strategy state."""

    def __init__(self, db: DatabaseManager, run_id: str) -> None:
        self.db = db
        self.run_id = run_id
        self.blocked_symbols: set[str] = set()
        self.recovery_complete: bool = False
        self._decisions: list[ReconciliationDecision] = []

    def reconcile(
        self,
        exchange_positions: list[ExchangePositionSnapshot],
        db_positions: list[StrategyPositionState],
        *,
        trigger: str = "startup",
    ) -> list[ReconciliationDecision]:
        with tracer.start_as_current_span(
            "position.reconciliation",
            attributes={"run_id": self.run_id, "trigger": trigger},
        ):
            self.db.create_reconciliation_run(self.run_id, trigger)

            logger.info(
                "Position reconciliation started",
                log_type="position.reconciliation.started",
                run_id=self.run_id,
                trigger=trigger,
                exchange_count=len(exchange_positions),
                db_count=len(db_positions),
            )

            exchange_by_symbol: dict[str, ExchangePositionSnapshot] = {
                pos.symbol: pos for pos in exchange_positions
            }
            db_by_symbol: dict[str, StrategyPositionState] = {
                pos.symbol: pos for pos in db_positions
            }

            all_symbols = set(exchange_by_symbol.keys()) | set(db_by_symbol.keys())
            decisions: list[ReconciliationDecision] = []
            counts: dict[str, int] = {
                "exchange_open": len(exchange_positions),
                "db_open": len(db_positions),
                "recovered": 0,
                "adopted": 0,
                "externally_closed": 0,
                "blocked": 0,
                "failed": 0,
            }

            for symbol in sorted(all_symbols):
                exchange_pos = exchange_by_symbol.get(symbol)
                db_pos = db_by_symbol.get(symbol)
                decision = self._classify(exchange_pos, db_pos)
                decisions.append(decision)
                self._apply_decision(decision, counts)

            result = self._determine_run_result(counts)
            self.db.finish_reconciliation_run(self.run_id, result, counts)
            self._decisions = decisions
            self.recovery_complete = True

            logger.info(
                "Position reconciliation completed",
                log_type="position.reconciliation.completed",
                run_id=self.run_id,
                trigger=trigger,
                result=result,
                **counts,
            )
            metrics.increment(
                "trader_reconciliation_runs_total",
                attributes={"trigger": trigger, "result": result},
            )

            return decisions

    def is_entry_allowed(self, symbol: str) -> bool:
        return self.recovery_complete and symbol not in self.blocked_symbols

    def persist_state(self, state: StrategyPositionState) -> None:
        try:
            self.db.upsert_strategy_position(
                {
                    "strategy_name": state.strategy_name,
                    "symbol": state.symbol,
                    "position_id": state.position_id,
                    "instrument_id": state.instrument_id,
                    "trade_id": state.trade_id,
                    "side": state.side,
                    "quantity": state.quantity,
                    "exchange_entry_price": state.entry_price,
                    "strategy_entry_price": state.entry_price,
                    "entry_atr": state.entry_atr,
                    "stop_price": state.stop_price,
                    "best_price": state.best_price,
                    "trailing_active": state.trailing_active,
                    "bars_in_position": state.bars_in_position,
                    "status": "open",
                    "strategy_state_json": state.strategy_state_json,
                    "last_run_id": self.run_id,
                    "original_run_id": self.run_id,
                }
            )
            metrics.increment(
                "trader_position_state_persist_total",
                attributes={
                    "strategy": state.strategy_name,
                    "symbol": state.symbol,
                    "result": "success",
                },
            )
        except Exception as exc:
            logger.warning(
                "Position state persistence failed",
                log_type="position.state_persist_failed",
                strategy=state.strategy_name,
                symbol=state.symbol,
                error=str(exc),
            )
            metrics.increment(
                "trader_position_state_persist_total",
                attributes={
                    "strategy": state.strategy_name,
                    "symbol": state.symbol,
                    "result": "error",
                },
            )

    def mark_closed(
        self,
        strategy_name: str,
        symbol: str,
        position_id: str,
        exit_reason: str,
    ) -> None:
        try:
            self.db.mark_position_closed(
                strategy_name, symbol, position_id, exit_reason
            )
        except Exception as exc:
            logger.warning(
                "Position close persistence failed",
                log_type="position.state_persist_failed",
                strategy=strategy_name,
                symbol=symbol,
                error=str(exc),
            )

    def _classify(
        self,
        exchange_pos: ExchangePositionSnapshot | None,
        db_pos: StrategyPositionState | None,
    ) -> ReconciliationDecision:
        if exchange_pos is not None and db_pos is not None:
            return self._classify_both_open(exchange_pos, db_pos)

        if exchange_pos is not None and db_pos is None:
            return ReconciliationDecision(
                symbol=exchange_pos.symbol,
                action="block",
                reason="unmanaged_exchange_position",
                exchange_snapshot=exchange_pos,
            )

        if exchange_pos is None and db_pos is not None:
            return ReconciliationDecision(
                symbol=db_pos.symbol,
                action="externally_closed",
                reason="exchange_flat_db_open",
                db_state=db_pos,
            )

        return ReconciliationDecision(
            symbol="unknown",
            action="block",
            reason="both_missing",
        )

    def _classify_both_open(
        self,
        exchange_pos: ExchangePositionSnapshot,
        db_pos: StrategyPositionState,
    ) -> ReconciliationDecision:
        if not _has_valid_strategy_state(db_pos):
            return ReconciliationDecision(
                symbol=exchange_pos.symbol,
                action="block",
                reason="invalid_strategy_state",
                exchange_snapshot=exchange_pos,
                db_state=db_pos,
            )

        exchange_side = _normalize_side(exchange_pos.side)
        db_side = _normalize_side(db_pos.side)

        if exchange_side != db_side:
            return ReconciliationDecision(
                symbol=exchange_pos.symbol,
                action="fail",
                reason="side_mismatch",
                exchange_snapshot=exchange_pos,
                db_state=db_pos,
            )

        if exchange_pos.quantity > db_pos.quantity * 1.1:
            return ReconciliationDecision(
                symbol=exchange_pos.symbol,
                action="block",
                reason="quantity_increased_external",
                exchange_snapshot=exchange_pos,
                db_state=db_pos,
            )

        recovered_state = replace(
            db_pos,
            quantity=exchange_pos.quantity,
            entry_price=exchange_pos.avg_entry_price or db_pos.entry_price,
        )

        return ReconciliationDecision(
            symbol=exchange_pos.symbol,
            action="recover",
            reason="matching_position",
            exchange_snapshot=exchange_pos,
            db_state=recovered_state,
        )

    def _apply_decision(
        self,
        decision: ReconciliationDecision,
        counts: dict[str, int],
    ) -> None:
        action = decision.action
        symbol = decision.symbol

        log_fields: dict[str, Any] = {
            "run_id": self.run_id,
            "symbol": symbol,
            "action": action,
            "reason": decision.reason,
        }
        if decision.db_state is not None:
            log_fields["strategy"] = decision.db_state.strategy_name
            log_fields["trade_id"] = decision.db_state.trade_id
            log_fields["position_id"] = decision.db_state.position_id
        if decision.exchange_snapshot is not None:
            log_fields["exchange_side"] = decision.exchange_snapshot.side
            log_fields["exchange_quantity"] = decision.exchange_snapshot.quantity

        if action == "recover":
            counts["recovered"] += 1
            logger.info(
                "Position recovered",
                log_type="position.recovered",
                **log_fields,
            )

        elif action == "externally_closed":
            counts["externally_closed"] += 1
            if decision.db_state is not None:
                self.db.mark_position_externally_closed(
                    decision.db_state.strategy_name,
                    decision.db_state.symbol,
                    decision.db_state.position_id,
                )
            logger.info(
                "Position externally closed",
                log_type="position.externally_closed",
                **log_fields,
            )

        elif action == "block":
            counts["blocked"] += 1
            self.blocked_symbols.add(symbol)
            logger.warning(
                "Position blocked",
                log_type="position.recovery_blocked",
                **log_fields,
            )
            metrics.increment(
                "trader_position_recovery_blocks_total",
                attributes={"symbol": symbol, "reason": decision.reason},
            )

        elif action == "fail":
            counts["failed"] += 1
            self.blocked_symbols.add(symbol)
            if decision.db_state is not None:
                self.db.upsert_strategy_position(
                    {
                        "strategy_name": decision.db_state.strategy_name,
                        "symbol": decision.db_state.symbol,
                        "position_id": decision.db_state.position_id,
                        "instrument_id": decision.db_state.instrument_id,
                        "trade_id": decision.db_state.trade_id,
                        "side": decision.db_state.side,
                        "quantity": decision.db_state.quantity,
                        "exchange_entry_price": decision.db_state.entry_price,
                        "status": "reconciliation_failed",
                        "last_run_id": self.run_id,
                        "original_run_id": self.run_id,
                    }
                )
            logger.error(
                "Position reconciliation failed",
                log_type="position.reconciliation_failed",
                **log_fields,
            )
            metrics.increment(
                "trader_position_recovery_blocks_total",
                attributes={"symbol": symbol, "reason": decision.reason},
            )

        metrics.increment(
            "trader_reconciliation_positions_total",
            attributes={
                "decision": action,
                "strategy": log_fields.get("strategy", "unknown"),
                "symbol": symbol,
            },
        )

    @staticmethod
    def _determine_run_result(counts: dict[str, int]) -> str:
        if counts.get("failed", 0) > 0:
            return "failed"
        if counts.get("blocked", 0) > 0:
            return "blocked"
        if counts.get("recovered", 0) > 0:
            return "recovered"
        return "clean"
