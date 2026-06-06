from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from trader_ai.execution.observer import ExecutionObserverMixin


class FakeStrategy(ExecutionObserverMixin):
    execution_strategy_name = "adaptive_trend_rider"

    def __init__(self) -> None:
        self.config = SimpleNamespace(ccxt_symbol="ETH/USDT:USDT")


def test_execution_instrumentation_observer_records_order_fill_event():
    strategy = FakeStrategy()
    event = SimpleNamespace(
        order_side="BUY",
        client_order_id="O-123",
        venue_order_id="V-123",
        last_qty=0.07,
        last_px=2140.5,
    )

    with patch("trader_ai.execution.observer.execution_instrumentation") as execution_instrumentation:
        strategy.on_order_filled(event)

    execution_instrumentation.order_filled.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="BUY",
        trade_id="O-123",
        order_id="V-123",
        quantity=0.07,
        fill_price=2140.5,
    )


def test_execution_instrumentation_observer_resolves_order_event_to_stored_trade_id():
    strategy = FakeStrategy()
    strategy._order_trade_ids = {"O-123": "T-generated"}
    event = SimpleNamespace(
        order_side="BUY",
        client_order_id="O-123",
        last_qty=0.07,
        last_px=2140.5,
    )

    with patch("trader_ai.execution.observer.execution_instrumentation") as execution_instrumentation:
        strategy.on_order_filled(event)

    assert execution_instrumentation.order_filled.call_args.kwargs["trade_id"] == "T-generated"


def test_execution_instrumentation_observer_records_order_rejection_event():
    strategy = FakeStrategy()
    event = SimpleNamespace(
        order_side="SELL",
        client_order_id="O-456",
        reason="insufficient_margin",
    )

    with patch("trader_ai.execution.observer.execution_instrumentation") as execution_instrumentation:
        strategy.on_order_rejected(event)

    execution_instrumentation.order_rejected.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="SELL",
        trade_id="O-456",
        order_id="O-456",
        reason="insufficient_margin",
    )


def test_execution_instrumentation_observer_records_order_denied_as_blocked_risk_check():
    strategy = FakeStrategy()
    event = SimpleNamespace(
        order_side="BUY",
        client_order_id="O-555",
        reason="risk_engine_denied",
    )

    with patch("trader_ai.execution.observer.execution_instrumentation") as execution_instrumentation:
        strategy.on_order_denied(event)

    execution_instrumentation.risk_checked.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        trade_id="O-555",
        result="blocked",
        reason="risk_engine_denied",
        side="BUY",
        order_id="O-555",
    )


def test_execution_instrumentation_observer_records_position_close_event():
    strategy = FakeStrategy()
    event = SimpleNamespace(
        side="BUY",
        client_order_id="O-789",
        position_id="P-789",
        realized_pnl=12.5,
    )

    with patch("trader_ai.execution.observer.execution_instrumentation") as execution_instrumentation:
        strategy.on_position_closed(event)

    execution_instrumentation.position_closed.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="BUY",
        trade_id="O-789",
        position_id="P-789",
        result="closed",
        pnl_usdt=12.5,
    )
