from __future__ import annotations

from dataclasses import replace
from typing import Any

from trader_ai.execution.instrumentation import execution_instrumentation


class ExecutionObserverMixin:
    execution_strategy_name = "unknown"

    def on_order_accepted(self, event: Any) -> None:
        execution_instrumentation.order_accepted(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            order_id=_order_id(event),
        )

    def on_order_rejected(self, event: Any) -> None:
        execution_instrumentation.order_rejected(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            order_id=_order_id(event),
            reason=_event_reason(event),
        )

    def on_order_denied(self, event: Any) -> None:
        execution_instrumentation.risk_checked(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            trade_id=self._event_trade_id(event),
            result="blocked",
            reason=_event_reason(event),
            side=_event_side(event),
            order_id=_order_id(event),
        )

    def on_order_canceled(self, event: Any) -> None:
        execution_instrumentation.order_canceled(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            order_id=_order_id(event),
            reason=_event_reason(event),
        )

    def on_order_filled(self, event: Any) -> None:
        execution_instrumentation.order_filled(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            order_id=_order_id(event),
            quantity=_event_float(event, "last_qty"),
            fill_price=_event_float(event, "last_px"),
        )

    def on_position_opened(self, event: Any) -> None:
        execution_instrumentation.position_opened(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            position_id=_position_id(event),
        )
        self._persist_recovery_state(event)

    def on_position_changed(self, event: Any) -> None:
        execution_instrumentation.position_updated(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            position_id=_position_id(event),
        )
        self._persist_recovery_state(event)

    def on_position_closed(self, event: Any) -> None:
        execution_instrumentation.position_closed(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            position_id=_position_id(event),
            result="closed",
            pnl_usdt=_event_optional_float(event, "realized_pnl"),
        )
        self._persist_recovery_closed(event)

    def _persist_recovery_state(self, event: Any | None = None) -> None:
        recovery_service = getattr(self, "_recovery_service", None)
        if recovery_service is None:
            return
        
        persist_func = getattr(self, "persist_position_state", None)
        if callable(persist_func):
            state = persist_func()
            if state is not None:
                event_quantity = _event_position_quantity(event)
                if state.quantity <= 0 and event_quantity is not None:
                    state = replace(state, quantity=event_quantity)
                recovery_service.persist_state(state)

    def _persist_recovery_closed(self, event: Any) -> None:
        recovery_service = getattr(self, "_recovery_service", None)
        if recovery_service is None:
            return
            
        recovery_service.mark_closed(
            strategy_name=self._execution_strategy(),
            symbol=self._execution_symbol(),
            position_id=self._event_trade_id(event),
            exit_reason=_event_reason(event),
        )

    def _execution_strategy(self) -> str:
        return str(getattr(self, "execution_strategy_name", "unknown"))

    def _execution_symbol(self) -> str:
        config = getattr(self, "config", None)
        ccxt_symbol = getattr(config, "ccxt_symbol", None)
        if ccxt_symbol:
            return str(ccxt_symbol)
        instrument_id = getattr(config, "instrument_id", None)
        if instrument_id is not None:
            return str(instrument_id)
        return "unknown"

    def _event_trade_id(self, event: Any) -> str:
        trade_ids = getattr(self, "_order_trade_ids", {})
        for order_id in (
            _event_str(event, "client_order_id"),
            _event_str(event, "venue_order_id"),
        ):
            if order_id and order_id in trade_ids:
                return str(trade_ids[order_id])
        return _trade_id(event)


def _trade_id(event: Any) -> str:
    return _event_str(event, "client_order_id") or _position_id(event)


def _order_id(event: Any) -> str:
    return (
        _event_str(event, "venue_order_id")
        or _event_str(event, "client_order_id")
        or _position_id(event)
    )


def _position_id(event: Any) -> str:
    return _event_str(event, "position_id") or "unknown"


def _event_side(event: Any) -> str:
    return (
        _event_str(event, "order_side")
        or _event_str(event, "side")
        or "UNKNOWN"
    )


def _event_reason(event: Any) -> str:
    return _event_str(event, "reason") or "unknown"


def _event_str(event: Any, name: str) -> str:
    value = getattr(event, name, None)
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    if hasattr(value, "name"):
        return str(value.name)
    return str(value)


def _event_float(event: Any, name: str) -> float:
    value = _event_optional_float(event, name)
    return 0.0 if value is None else value


def _event_optional_float(event: Any, name: str) -> float | None:
    if event is None:
        return None
    value = getattr(event, name, None)
    if value is None:
        return None
    if hasattr(value, "as_double"):
        return float(value.as_double())
    if hasattr(value, "as_decimal"):
        return float(value.as_decimal())

    text = str(value).split(" ", maxsplit=1)[0].replace("_", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _event_position_quantity(event: Any | None) -> float | None:
    for name in ("quantity", "signed_qty", "last_qty"):
        value = _event_optional_float(event, name)
        if value is not None:
            return abs(value)
    return None
