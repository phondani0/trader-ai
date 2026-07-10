from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import call

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Price

from trader_ai.execution.exit_orders import ExitOrderRequest
from trader_ai.execution.exit_orders import activate_profit_runner_if_ready
from trader_ai.execution.exit_orders import calculate_take_profit_price
from trader_ai.execution.exit_orders import cancel_exit_orders
from trader_ai.execution.exit_orders import cancel_sibling_exit_order
from trader_ai.execution.exit_orders import record_exit_order_request
from trader_ai.execution.exit_orders import replace_exit_stop_if_improved
from trader_ai.execution.exit_orders import submit_pending_exit_orders


class FakeInstrument:
    def make_price(self, value):
        return f"price:{value}"


class FakeStringPrecisionInstrument:
    price_precision = "4"

    def make_price(self, value):
        raise TypeError("'<=' not supported between instances of 'str' and 'int'")


class FakeCache:
    def __init__(self, orders=None):
        self._orders = orders or []

    def orders_open(self, *, instrument_id=None):
        self.instrument_id = instrument_id
        return self._orders


class FakeOwner:
    def __init__(self, orders=None):
        self.cache = FakeCache(orders)
        self.order_factory = Mock()
        self.submit_order = Mock()
        self.cancel_order = Mock()
        self.close_all_positions = Mock()
        self.order_factory.stop_market.side_effect = lambda *args, **kwargs: SimpleNamespace(
            client_order_id=f"O-stop-{self.order_factory.stop_market.call_count}",
            order_type=SimpleNamespace(name="STOP_MARKET"),
            reduce_only=kwargs.get("reduce_only", False),
            tags=kwargs.get("tags", []),
        )
        self.order_factory.limit.side_effect = lambda *args, **kwargs: SimpleNamespace(
            client_order_id=f"O-tp-{self.order_factory.limit.call_count}",
            order_type=SimpleNamespace(name="LIMIT"),
            reduce_only=kwargs.get("reduce_only", False),
            tags=kwargs.get("tags", []),
        )


def _request(**overrides):
    payload = {
        "instrument": FakeInstrument(),
        "instrument_id": "ETHUSDT-LINEAR.BYBIT",
        "position_side": OrderSide.BUY,
        "quantity": "qty:0.1",
        "entry_price": Decimal("100"),
        "stop_price": Decimal("95"),
        "strategy_name": "adaptive_trend_rider",
        "symbol": "ETH/USDT:USDT",
        "trade_id": "T-1",
        "take_profit_order_enabled": False,
        "take_profit_r_multiple": Decimal("2.0"),
        "profit_runner_enabled": True,
        "profit_runner_activation_r_multiple": Decimal("2.0"),
        "profit_runner_stop_lock_r_multiple": Decimal("0.5"),
    }
    payload.update(overrides)
    return ExitOrderRequest(**payload)


def test_calculate_take_profit_price_uses_r_multiple():
    assert calculate_take_profit_price(
        position_side=OrderSide.BUY,
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        r_multiple=Decimal("2.0"),
    ) == Decimal("110.0")
    assert calculate_take_profit_price(
        position_side=OrderSide.SELL,
        entry_price=Decimal("100"),
        stop_price=Decimal("105"),
        r_multiple=Decimal("2.0"),
    ) == Decimal("90.0")


def test_submit_pending_exit_orders_submits_reduce_only_stop_and_take_profit():
    owner = FakeOwner()
    entry_order = SimpleNamespace(client_order_id="O-entry")
    record_exit_order_request(
        owner=owner,
        entry_order=entry_order,
        request=_request(take_profit_order_enabled=True),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles is not None
    owner.order_factory.stop_market.assert_called_once_with(
        "ETHUSDT-LINEAR.BYBIT",
        OrderSide.SELL,
        "qty:0.1",
        "price:95",
        reduce_only=True,
        tags=[
            "EXIT_STOP",
            "strategy=adaptive_trend_rider",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )
    owner.order_factory.limit.assert_called_once_with(
        "ETHUSDT-LINEAR.BYBIT",
        OrderSide.SELL,
        "qty:0.1",
        "price:110.0",
        post_only=False,
        reduce_only=True,
        tags=[
            "EXIT_TAKE_PROFIT",
            "strategy=adaptive_trend_rider",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )
    assert owner.submit_order.call_args_list == [
        call(handles.stop_order),
        call(handles.take_profit_order),
    ]
    assert owner._exit_order_trade_ids == {
        str(handles.stop_order.client_order_id): "T-1",
        str(handles.take_profit_order.client_order_id): "T-1",
    }


def test_submit_pending_exit_orders_does_not_submit_take_profit_order_by_default():
    owner = FakeOwner()
    entry_order = SimpleNamespace(client_order_id="O-entry")
    record_exit_order_request(owner=owner, entry_order=entry_order, request=_request())

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles is not None
    assert handles.stop_order is not None
    assert handles.take_profit_order is None
    owner.order_factory.stop_market.assert_called_once()
    owner.order_factory.limit.assert_not_called()
    owner.submit_order.assert_called_once_with(handles.stop_order)


def test_submit_pending_exit_orders_emits_exit_order_submitted_event(monkeypatch):
    owner = FakeOwner()
    exit_order_submitted = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.exit_order_submitted",
        exit_order_submitted,
    )
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    exit_order_submitted.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="SELL",
        trade_id="T-1",
        order_id=str(handles.stop_order.client_order_id),
        order_role="stop",
        quantity=0.0,
        price=95.0,
        result="submitted",
    )


def test_submit_pending_exit_orders_reuses_existing_tagged_orders():
    stop = SimpleNamespace(
        client_order_id="O-existing-stop",
        order_type=SimpleNamespace(name="STOP_MARKET"),
        reduce_only=True,
        tags=["EXIT_STOP", "trade_id=T-1"],
    )
    take_profit = SimpleNamespace(
        client_order_id="O-existing-tp",
        order_type=SimpleNamespace(name="LIMIT"),
        reduce_only=True,
        tags=["EXIT_TAKE_PROFIT", "trade_id=T-1"],
    )
    owner = FakeOwner([stop, take_profit])
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles.stop_order is stop
    assert handles.take_profit_order is take_profit
    owner.order_factory.stop_market.assert_not_called()
    owner.order_factory.limit.assert_not_called()
    owner.submit_order.assert_not_called()


def test_submit_pending_exit_orders_reuses_existing_nautilus_reduce_only_orders():
    stop = SimpleNamespace(
        client_order_id="O-existing-stop",
        order_type=SimpleNamespace(name="STOP_MARKET"),
        is_reduce_only=True,
        tags=["EXIT_STOP", "trade_id=T-1"],
    )
    take_profit = SimpleNamespace(
        client_order_id="O-existing-tp",
        order_type=SimpleNamespace(name="LIMIT"),
        is_reduce_only=True,
        tags=["EXIT_TAKE_PROFIT", "trade_id=T-1"],
    )
    owner = FakeOwner([stop, take_profit])
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles.stop_order is stop
    assert handles.take_profit_order is take_profit
    owner.order_factory.stop_market.assert_not_called()
    owner.order_factory.limit.assert_not_called()
    owner.submit_order.assert_not_called()


def test_submit_pending_exit_orders_logs_full_error_message_on_failure(monkeypatch):
    owner = FakeOwner()
    owner.order_factory.stop_market.side_effect = TypeError("invalid stop order call")
    exit_order_failed = Mock()
    runtime_error = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.exit_order_failed",
        exit_order_failed,
    )
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.runtime_error",
        runtime_error,
    )
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles is None
    owner.close_all_positions.assert_called_once_with("ETHUSDT-LINEAR.BYBIT")
    assert sorted(owner._exit_order_requests) == ["O-entry", "T-1"]
    assert exit_order_failed.call_args.kwargs["reason"] == "TypeError"
    assert exit_order_failed.call_args.kwargs["error"] == "invalid stop order call"
    assert runtime_error.call_args.kwargs["reason"] == "TypeError"
    assert runtime_error.call_args.kwargs["error"] == "invalid stop order call"


def test_submit_pending_exit_orders_handles_string_price_precision_from_live_instrument():
    owner = FakeOwner()
    entry_order = SimpleNamespace(client_order_id="O-entry")
    record_exit_order_request(
        owner=owner,
        entry_order=entry_order,
        request=_request(
            instrument=FakeStringPrecisionInstrument(),
            stop_price=Decimal("95.123456"),
        ),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert handles is not None
    stop_price = owner.order_factory.stop_market.call_args.args[3]
    assert isinstance(stop_price, Price)
    assert str(stop_price) == "95.1235"
    owner.close_all_positions.assert_not_called()


def test_submit_pending_exit_orders_can_skip_failure_callback_for_retryable_attempt():
    owner = FakeOwner()
    owner.order_factory.stop_market.side_effect = RuntimeError("position not ready")
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(),
    )

    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1", on_failure=None)

    assert handles is None
    owner.close_all_positions.assert_not_called()
    assert sorted(owner._exit_order_requests) == ["O-entry", "T-1"]


def test_replace_exit_stop_if_improved_submits_new_stop_then_cancels_old_stop():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")
    old_stop = handles.stop_order

    replaced = replace_exit_stop_if_improved(
        owner=owner,
        trade_id="T-1",
        stop_price=Decimal("97"),
        min_improvement_bps=10,
    )

    assert replaced is True
    assert owner.order_factory.stop_market.call_count == 2
    assert owner.submit_order.call_args_list[-1] == call(owner._exit_orders_by_trade_id["T-1"].stop_order)
    owner.cancel_order.assert_called_once_with(old_stop)
    assert str(old_stop.client_order_id) not in owner._exit_order_trade_ids
    assert str(owner._exit_orders_by_trade_id["T-1"].stop_order.client_order_id) in owner._exit_order_trade_ids


def test_replace_exit_stop_if_improved_emits_stop_replaced_event(monkeypatch):
    owner = FakeOwner()
    exit_stop_replaced = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.exit_stop_replaced",
        exit_stop_replaced,
    )
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    replaced = replace_exit_stop_if_improved(
        owner=owner,
        trade_id="T-1",
        stop_price=Decimal("97"),
        min_improvement_bps=10,
    )

    assert replaced is True
    exit_stop_replaced.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="SELL",
        trade_id="T-1",
        old_order_id=str(handles.stop_order.client_order_id),
        new_order_id=str(owner._exit_orders_by_trade_id["T-1"].stop_order.client_order_id),
        quantity=0.0,
        previous_stop_price=95.0,
        new_stop_price=97.0,
        result="replaced",
    )


def test_activate_profit_runner_moves_stop_to_locked_profit_after_activation_price():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(entry_price=Decimal("100"), stop_price=Decimal("95")),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")
    old_stop = handles.stop_order

    activated = activate_profit_runner_if_ready(
        owner=owner,
        trade_id="T-1",
        latest_price=Decimal("110"),
        min_improvement_bps=10,
    )

    assert activated is True
    assert owner.order_factory.stop_market.call_count == 2
    owner.order_factory.stop_market.assert_called_with(
        "ETHUSDT-LINEAR.BYBIT",
        OrderSide.SELL,
        "qty:0.1",
        "price:102.5",
        reduce_only=True,
        tags=[
            "EXIT_STOP",
            "strategy=adaptive_trend_rider",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )
    owner.cancel_order.assert_called_once_with(old_stop)
    assert owner._exit_orders_by_trade_id["T-1"].profit_runner_active is True


def test_activate_profit_runner_emits_activation_event(monkeypatch):
    owner = FakeOwner()
    profit_runner_activated = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.profit_runner_activated",
        profit_runner_activated,
    )
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(entry_price=Decimal("100"), stop_price=Decimal("95")),
    )
    submit_pending_exit_orders(owner=owner, trade_id="T-1")

    activated = activate_profit_runner_if_ready(
        owner=owner,
        trade_id="T-1",
        latest_price=Decimal("110"),
        min_improvement_bps=10,
    )

    assert activated is True
    profit_runner_activated.assert_called_once_with(
        strategy="adaptive_trend_rider",
        symbol="ETH/USDT:USDT",
        side="BUY",
        trade_id="T-1",
        latest_price=110.0,
        activation_price=110.0,
        locked_stop_price=102.5,
        result="activated",
    )


def test_activate_profit_runner_waits_until_activation_price_is_crossed():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(entry_price=Decimal("100"), stop_price=Decimal("95")),
    )
    submit_pending_exit_orders(owner=owner, trade_id="T-1")

    activated = activate_profit_runner_if_ready(
        owner=owner,
        trade_id="T-1",
        latest_price=Decimal("109.99"),
        min_improvement_bps=10,
    )

    assert activated is False
    assert owner.order_factory.stop_market.call_count == 1
    owner.cancel_order.assert_not_called()


def test_activate_profit_runner_supports_short_positions():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(
            position_side=OrderSide.SELL,
            entry_price=Decimal("100"),
            stop_price=Decimal("105"),
        ),
    )
    submit_pending_exit_orders(owner=owner, trade_id="T-1")

    activated = activate_profit_runner_if_ready(
        owner=owner,
        trade_id="T-1",
        latest_price=Decimal("90"),
        min_improvement_bps=10,
    )

    assert activated is True
    owner.order_factory.stop_market.assert_called_with(
        "ETHUSDT-LINEAR.BYBIT",
        OrderSide.BUY,
        "qty:0.1",
        "price:97.5",
        reduce_only=True,
        tags=[
            "EXIT_STOP",
            "strategy=adaptive_trend_rider",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )


def test_replace_exit_stop_if_improved_ignores_small_or_worse_moves():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )
    submit_pending_exit_orders(owner=owner, trade_id="T-1")

    assert replace_exit_stop_if_improved(
        owner=owner,
        trade_id="T-1",
        stop_price=Decimal("94"),
        min_improvement_bps=10,
    ) is False
    assert replace_exit_stop_if_improved(
        owner=owner,
        trade_id="T-1",
        stop_price=Decimal("95.005"),
        min_improvement_bps=10,
    ) is False
    assert owner.order_factory.stop_market.call_count == 1
    owner.cancel_order.assert_not_called()


def test_cancel_sibling_exit_order_cancels_stop_when_take_profit_fills():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    cancelled = cancel_sibling_exit_order(
        owner=owner,
        filled_order_id=str(handles.take_profit_order.client_order_id),
    )

    assert cancelled is True
    owner.cancel_order.assert_called_once_with(handles.stop_order)
    assert owner._exit_orders_by_trade_id == {}
    assert owner._exit_order_trade_ids == {}
    assert owner._exit_order_roles == {}


def test_cancel_exit_orders_cancels_both_exit_orders():
    owner = FakeOwner()
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    cancelled = cancel_exit_orders(owner=owner, trade_id="T-1")

    assert cancelled is True
    assert owner.cancel_order.call_args_list == [
        call(handles.stop_order),
        call(handles.take_profit_order),
    ]


def test_cancel_exit_orders_emits_canceled_events(monkeypatch):
    owner = FakeOwner()
    exit_order_canceled = Mock()
    monkeypatch.setattr(
        "trader_ai.execution.exit_orders.execution_instrumentation.exit_order_canceled",
        exit_order_canceled,
    )
    record_exit_order_request(
        owner=owner,
        entry_order=SimpleNamespace(client_order_id="O-entry"),
        request=_request(take_profit_order_enabled=True),
    )
    handles = submit_pending_exit_orders(owner=owner, trade_id="T-1")

    cancelled = cancel_exit_orders(owner=owner, trade_id="T-1")

    assert cancelled is True
    assert exit_order_canceled.call_args_list == [
        call(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="SELL",
            trade_id="T-1",
            order_id=str(handles.stop_order.client_order_id),
            order_role="stop",
            reason="trade_exit_cleanup",
            result="canceled",
        ),
        call(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="SELL",
            trade_id="T-1",
            order_id=str(handles.take_profit_order.client_order_id),
            order_role="take_profit",
            reason="trade_exit_cleanup",
            result="canceled",
        ),
    ]
