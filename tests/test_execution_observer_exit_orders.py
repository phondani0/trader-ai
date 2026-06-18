from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from nautilus_trader.model.enums import OrderSide

from trader_ai.execution.observer import ExecutionObserverMixin


class FakeExecutionObserver(ExecutionObserverMixin):
    execution_strategy_name = "adaptive_trend_rider"


def test_position_opened_submits_pending_exit_orders(monkeypatch):
    observer = FakeExecutionObserver()
    observer._order_trade_ids = {"O-entry": "T-1"}
    position_opened = Mock()
    submit_pending = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.observer.execution_instrumentation.position_opened",
        position_opened,
    )
    monkeypatch.setattr(
        "trader_ai.execution.observer.submit_pending_exit_orders",
        submit_pending,
    )

    observer.on_position_opened(
        SimpleNamespace(
            client_order_id="O-entry",
            order_side=OrderSide.BUY,
            position_id="P-1",
        )
    )

    submit_pending.assert_called_once_with(owner=observer, trade_id="T-1")


def test_position_changed_submits_pending_exit_orders(monkeypatch):
    observer = FakeExecutionObserver()
    observer._order_trade_ids = {"O-entry": "T-1"}
    position_updated = Mock()
    submit_pending = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.observer.execution_instrumentation.position_updated",
        position_updated,
    )
    monkeypatch.setattr(
        "trader_ai.execution.observer.submit_pending_exit_orders",
        submit_pending,
    )

    observer.on_position_changed(
        SimpleNamespace(
            client_order_id="O-entry",
            order_side=OrderSide.BUY,
            position_id="P-1",
        )
    )

    submit_pending.assert_called_once_with(owner=observer, trade_id="T-1")


def test_entry_order_fill_submits_pending_exit_orders(monkeypatch):
    observer = FakeExecutionObserver()
    observer._order_trade_ids = {"O-entry": "T-1"}
    order_filled = Mock()
    submit_pending = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.observer.execution_instrumentation.order_filled",
        order_filled,
    )
    monkeypatch.setattr(
        "trader_ai.execution.observer.submit_pending_exit_orders",
        submit_pending,
    )

    observer.on_order_filled(
        SimpleNamespace(
            client_order_id="O-entry",
            venue_order_id="V-entry",
            order_side=OrderSide.BUY,
            position_id="P-1",
            last_qty=1,
            last_px=100,
        )
    )

    submit_pending.assert_called_once_with(owner=observer, trade_id="T-1")
