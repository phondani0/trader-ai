from __future__ import annotations

from dataclasses import replace
from typing import Any

from trader_ai.execution.exit_orders import cancel_sibling_exit_order
from trader_ai.execution.exit_orders import submit_pending_exit_orders
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
        self._handle_order_rejected(event)

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
        cancel_sibling_exit_order(owner=self, filled_order_id=_order_id(event))
        self._submit_pending_exit_orders(event)

    def on_position_opened(self, event: Any) -> None:
        execution_instrumentation.position_opened(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            position_id=_position_id(event),
        )
        self._persist_recovery_state(event)
        self._submit_pending_exit_orders(event)

    def on_position_changed(self, event: Any) -> None:
        execution_instrumentation.position_updated(
            strategy=self._execution_strategy(),
            symbol=self._execution_symbol(),
            side=_event_side(event),
            trade_id=self._event_trade_id(event),
            position_id=_position_id(event),
        )
        self._persist_recovery_state(event)
        self._submit_pending_exit_orders(event)

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

    def _submit_pending_exit_orders(self, event: Any) -> None:
        submit_pending_exit_orders(owner=self, trade_id=self._event_trade_id(event))

    def _handle_order_rejected(self, event: Any) -> None:
        if self._event_trade_id(event) != str(getattr(self, "_current_trade_id", "")):
            return
        if getattr(self, "_position_side", None) is None:
            return

        self._discard_pending_exit_order_requests(self._event_trade_id(event))
        self._clear_position_state_after_failed_entry()

    def _handle_exit_order_failure(self) -> None:
        config = getattr(self, "config", None)
        instrument_id = getattr(config, "instrument_id", None)
        if instrument_id is not None:
            self.close_all_positions(instrument_id)
        self._clear_position_state_after_failed_entry()

    def _clear_position_state_after_failed_entry(self) -> None:
        reset_position_state = getattr(self, "_reset_position_state", None)
        if callable(reset_position_state):
            reset_position_state()

        config = getattr(self, "config", None)
        cooldown_bars = getattr(config, "cooldown_bars", None)
        if cooldown_bars is not None and hasattr(self, "_cooldown_remaining"):
            self._cooldown_remaining = cooldown_bars

    def _discard_pending_exit_order_requests(self, trade_id: str) -> None:
        requests = getattr(self, "_exit_order_requests", None)
        if not isinstance(requests, dict):
            return
        self._exit_order_requests = {
            key: request
            for key, request in requests.items()
            if getattr(request, "trade_id", None) != trade_id
        }

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
