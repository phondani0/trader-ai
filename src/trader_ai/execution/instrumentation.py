from __future__ import annotations

from contextlib import AbstractContextManager
from contextlib import nullcontext
from typing import Any

from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode
from opentelemetry.trace import use_span

from trader_ai.observability.logging import logger
from trader_ai.observability.metrics import metrics
from trader_ai.observability.tracing import tracer


METRIC_LABEL_KEYS = frozenset(
    {
        "strategy",
        "symbol",
        "side",
        "result",
        "reason",
        "component",
        "order_role",
    }
)


class ExecutionInstrumentation:
    def __init__(self) -> None:
        self._trade_root_spans: dict[str, Any] = {}

    def start_trade_execution(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
    ) -> AbstractContextManager[Any]:
        if trade_id not in self._trade_root_spans:
            self._trade_root_spans[trade_id] = tracer.start_span(
                "trade.execution",
                attributes={
                    "strategy": strategy,
                    "symbol": symbol,
                    "side": side,
                    "trade_id": trade_id,
                },
            )
        return self._trade_root_span_context(trade_id)

    def end_trade_execution(
        self,
        *,
        trade_id: str,
        result: str = "closed",
        reason: str | None = None,
    ) -> None:
        span = self._trade_root_spans.pop(trade_id, None)
        if span is None:
            return

        status_code = StatusCode.OK if result in {"closed", "settled"} else StatusCode.ERROR
        if hasattr(span, "set_status"):
            span.set_status(Status(status_code, reason))
        if hasattr(span, "end"):
            span.end()

    def _trade_root_span_context(self, trade_id: str) -> AbstractContextManager[Any]:
        span = self._trade_root_spans.get(trade_id)
        if span is None:
            return nullcontext()
        return use_span(span, end_on_exit=False)

    def _start_trade_execution(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
    ) -> None:
        self.start_trade_execution(
            strategy=strategy,
            symbol=symbol,
            side=side,
            trade_id=trade_id,
        )

    def _end_trade_execution_when_terminal(
        self,
        *,
        trade_id: str,
        result: str,
        reason: str | None = None,
    ) -> None:
        if result in {"blocked", "rejected", "canceled", "closed", "settled", "error"}:
            self.end_trade_execution(trade_id=trade_id, result=result, reason=reason)

    def signal_generated(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        result: str = "generated",
        **fields: Any,
    ) -> None:
        self._start_trade_execution(
            strategy=strategy,
            symbol=symbol,
            side=side,
            trade_id=trade_id,
        )
        self._emit(
            message="Trade signal generated",
            span_name="signal.generated",
            log_type="trade.signal.generated",
            counter_name="trader_signals_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "result": result,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "result": result,
                **fields,
            },
        )

    def risk_checked(
        self,
        *,
        strategy: str,
        symbol: str,
        trade_id: str,
        result: str,
        reason: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade risk check completed",
            span_name="risk.check",
            log_type="trade.risk.checked",
            counter_name="trader_risk_checks_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "result": result,
                "reason": reason,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "trade_id": trade_id,
                "result": result,
                "reason": reason,
                **fields,
            },
        )
        self._end_trade_execution_when_terminal(
            trade_id=trade_id,
            result=result,
            reason=reason,
        )

    def order_submitted(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        quantity: float,
        notional_usdt: float | None = None,
        result: str = "submitted",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade order submitted",
            span_name="order.submit",
            log_type="trade.order.submitted",
            counter_name="trader_orders_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "result": result,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "quantity": quantity,
                "notional_usdt": notional_usdt,
                "result": result,
                **fields,
            },
        )

    def exit_order_submitted(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        order_role: str,
        quantity: float,
        price: float,
        result: str = "submitted",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Exit order submitted",
            span_name="exit.order.submit",
            log_type="exit.order.submitted",
            counter_name="trader_exit_orders_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "order_role": order_role,
                "result": result,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "order_role": order_role,
                "quantity": quantity,
                "price": price,
                "result": result,
                **fields,
            },
        )

    def exit_stop_replaced(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        old_order_id: str,
        new_order_id: str,
        quantity: float,
        previous_stop_price: float,
        new_stop_price: float,
        result: str = "replaced",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Exit stop replaced",
            span_name="exit.stop.replace",
            log_type="exit.stop.replaced",
            counter_name="trader_exit_stop_replacements_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "result": result,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "old_order_id": old_order_id,
                "new_order_id": new_order_id,
                "quantity": quantity,
                "previous_stop_price": previous_stop_price,
                "new_stop_price": new_stop_price,
                "result": result,
                **fields,
            },
        )

    def profit_runner_activated(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        latest_price: float,
        activation_price: float,
        locked_stop_price: float,
        result: str = "activated",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Profit runner activated",
            span_name="profit_runner.activate",
            log_type="profit_runner.activated",
            counter_name="trader_profit_runner_activations_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "result": result,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "latest_price": latest_price,
                "activation_price": activation_price,
                "locked_stop_price": locked_stop_price,
                "result": result,
                **fields,
            },
        )

    def exit_order_canceled(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        order_role: str,
        reason: str,
        result: str = "canceled",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Exit order canceled",
            span_name="exit.order.cancel",
            log_type="exit.order.canceled",
            counter_name="trader_exit_order_cancellations_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "order_role": order_role,
                "reason": reason,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "order_role": order_role,
                "reason": reason,
                "result": result,
                **fields,
            },
        )

    def exit_order_failed(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_role: str,
        reason: str,
        result: str = "error",
        **fields: Any,
    ) -> None:
        self._emit(
            message="Exit order failed",
            span_name="exit.order.error",
            log_type="exit.order.failed",
            counter_name="trader_exit_order_failures_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "order_role": order_role,
                "reason": reason,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_role": order_role,
                "reason": reason,
                "result": result,
                **fields,
            },
        )

    def order_accepted(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade order accepted",
            span_name="order.accepted",
            log_type="trade.order.accepted",
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "result": "accepted",
                **fields,
            },
        )

    def order_rejected(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        reason: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade order rejected",
            span_name="order.rejected",
            log_type="trade.order.rejected",
            counter_name="trader_order_rejections_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "reason": reason,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "reason": reason,
                "result": "rejected",
                **fields,
            },
        )
        self._end_trade_execution_when_terminal(
            trade_id=trade_id,
            result="rejected",
            reason=reason,
        )

    def order_canceled(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        reason: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade order canceled",
            span_name="order.canceled",
            log_type="trade.order.canceled",
            counter_name="trader_order_cancellations_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "reason": reason,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "reason": reason,
                "result": "canceled",
                **fields,
            },
        )
        self._end_trade_execution_when_terminal(
            trade_id=trade_id,
            result="canceled",
            reason=reason,
        )

    def order_filled(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        order_id: str,
        quantity: float,
        fill_price: float,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade order filled",
            span_name="order.fill",
            log_type="trade.order.filled",
            counter_name="trader_order_fills_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "order_id": order_id,
                "quantity": quantity,
                "fill_price": fill_price,
                "result": "filled",
                **fields,
            },
        )

    def position_opened(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        position_id: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade position opened",
            span_name="position.open",
            log_type="trade.position.opened",
            counter_name="trader_positions_open_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
            },
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "position_id": position_id,
                "result": "opened",
                **fields,
            },
        )

    def position_updated(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        position_id: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade position updated",
            span_name="position.update",
            log_type="trade.position.updated",
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "position_id": position_id,
                "result": "updated",
                **fields,
            },
        )

    def position_closed(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        position_id: str,
        result: str,
        pnl_usdt: float | None = None,
        pnl_pct: float | None = None,
        fees_usdt: float | None = None,
        slippage_bps: float | None = None,
        lifetime_seconds: float | None = None,
        **fields: Any,
    ) -> None:
        base_fields = {
            "strategy": strategy,
            "symbol": symbol,
            "side": side,
            "trade_id": trade_id,
            "position_id": position_id,
            "result": result,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "fees_usdt": fees_usdt,
            "slippage_bps": slippage_bps,
            "lifetime_seconds": lifetime_seconds,
            **fields,
        }
        self._emit(
            message="Trade position closed",
            span_name="position.close",
            log_type="trade.position.closed",
            counter_name="trader_positions_closed_total",
            counter_labels={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "result": result,
            },
            fields=base_fields,
        )
        if pnl_usdt is not None:
            metrics.record(
                "trader_trade_pnl_usdt",
                pnl_usdt,
                attributes={"strategy": strategy, "symbol": symbol, "result": result},
            )
        if fees_usdt is not None:
            metrics.record(
                "trader_trade_fees_usdt",
                fees_usdt,
                attributes={"strategy": strategy, "symbol": symbol},
            )
        if slippage_bps is not None:
            metrics.record(
                "trader_trade_slippage_bps",
                slippage_bps,
                attributes={"strategy": strategy, "symbol": symbol},
            )
        if lifetime_seconds is not None:
            metrics.record(
                "trader_trade_lifetime_seconds",
                lifetime_seconds,
                attributes={"strategy": strategy, "symbol": symbol, "result": result},
            )
        self._end_trade_execution_when_terminal(
            trade_id=trade_id,
            result=result,
        )

    def trade_settled(
        self,
        *,
        strategy: str,
        symbol: str,
        side: str,
        trade_id: str,
        result: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade settled",
            span_name="trade.settle",
            log_type="trade.settled",
            fields={
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "result": result,
                **fields,
            },
        )
        self._end_trade_execution_when_terminal(
            trade_id=trade_id,
            result=result,
        )

    def kill_switch_triggered(self, *, reason: str, **fields: Any) -> None:
        self._emit(
            message="Trade kill switch triggered",
            span_name="kill_switch.triggered",
            log_type="trade.kill_switch.triggered",
            counter_name="trader_kill_switch_events_total",
            counter_labels={"reason": reason},
            fields={"reason": reason, "result": "triggered", **fields},
        )

    def runtime_error(
        self,
        *,
        component: str,
        reason: str,
        **fields: Any,
    ) -> None:
        self._emit(
            message="Trade runtime error",
            span_name="runtime.error",
            log_type="trade.runtime.error",
            counter_name="trader_runtime_errors_total",
            counter_labels={"component": component, "reason": reason},
            fields={"component": component, "reason": reason, "result": "error", **fields},
        )

    def order_latency(
        self,
        *,
        strategy: str,
        symbol: str,
        result: str,
        seconds: float,
    ) -> None:
        metrics.record(
            "trader_order_latency_seconds",
            seconds,
            attributes={"strategy": strategy, "symbol": symbol, "result": result},
        )

    def signal_to_fill_latency(
        self,
        *,
        strategy: str,
        symbol: str,
        seconds: float,
    ) -> None:
        metrics.record(
            "trader_signal_to_fill_seconds",
            seconds,
            attributes={"strategy": strategy, "symbol": symbol},
        )

    def _emit(
        self,
        *,
        message: str,
        span_name: str,
        log_type: str,
        fields: dict[str, Any],
        counter_name: str | None = None,
        counter_labels: dict[str, str] | None = None,
    ) -> None:
        clean_fields = _clean_attributes(fields)
        logger.info(message, log_type=log_type, **clean_fields)
        if counter_name is not None:
            metrics.increment(
                counter_name,
                attributes=_metric_labels(counter_labels or clean_fields),
            )
        trade_id = str(clean_fields.get("trade_id", ""))
        with self._trade_root_span_context(trade_id):
            with tracer.start_as_current_span(span_name, attributes=clean_fields):
                pass


def _clean_attributes(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in fields.items()
        if value is not None and isinstance(value, str | bool | int | float)
    }


def _metric_labels(fields: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in fields.items()
        if key in METRIC_LABEL_KEYS and value is not None
    }


execution_instrumentation = ExecutionInstrumentation()
