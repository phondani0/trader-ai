from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import call

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

from trader_ai.execution.exit_orders import EXIT_STOP_TAG
from trader_ai.execution.exit_orders import submit_pending_exit_orders
from trader_ai.execution.observer import ExecutionObserverMixin
from trader_ai.strategies.adaptive_trend_rider import AdaptiveTrendRiderConfig
from trader_ai.strategies.adaptive_trend_rider import _RuntimeSymbolContext
from trader_ai.strategies.macd_demo import DemoEMACrossConfig
from trader_ai.strategies.macd_demo import _RuntimeDemoEMAContext
from trader_ai.strategies.squeeze_breakout import SqueezeBreakoutConfig
from trader_ai.strategies.squeeze_breakout import _RuntimeSqueezeContext
from trader_ai.strategies.volume_spike_trend import VolumeSpikeTrendConfig
from trader_ai.strategies.volume_spike_trend import _RuntimeVolumeContext


class FakeInstrument:
    def make_qty(self, value):
        return Decimal(str(value))

    def make_price(self, value):
        return f"price:{value}"


class FakePrice:
    def __init__(self, value):
        self.value = value

    def as_double(self):
        return float(self.value)

    def __str__(self):
        return str(self.value)


class FakeCache:
    def __init__(self, instrument):
        self._instrument = instrument

    def instrument(self, instrument_id):
        self.instrument_id = instrument_id
        return self._instrument

    def orders_open(self, *, instrument_id=None):
        self.open_orders_instrument_id = instrument_id
        return []


class FakeParent:
    def __init__(self, instrument):
        self.cache = FakeCache(instrument)
        self.order_factory = Mock()
        self.submit_order = Mock()
        self.cancel_order = Mock()
        self.close_all_positions = Mock()
        self.order_factory.market.return_value = SimpleNamespace(client_order_id="O-entry")
        self.order_factory.stop_market.return_value = SimpleNamespace(client_order_id="O-stop")
        self.order_factory.limit.return_value = SimpleNamespace(client_order_id="O-tp")


class FakeRuntimeParent(ExecutionObserverMixin, FakeParent):
    execution_strategy_name = "adaptive_trend_rider"

    def __init__(self, instrument, *, instrument_id):
        super().__init__(instrument)
        self.config = SimpleNamespace(
            ccxt_symbol="ETH/USDT:USDT",
            instrument_id=instrument_id,
        )


@pytest.mark.parametrize(
    ("context_cls", "config_cls", "strategy_name"),
    [
        (_RuntimeSymbolContext, AdaptiveTrendRiderConfig, "adaptive_trend_rider"),
        (_RuntimeVolumeContext, VolumeSpikeTrendConfig, "volume_spike_trend"),
        (_RuntimeSqueezeContext, SqueezeBreakoutConfig, "squeeze_breakout"),
        (_RuntimeDemoEMAContext, DemoEMACrossConfig, "demo_ema_cross"),
    ],
)
def test_runtime_context_records_exit_orders_for_every_strategy(
    context_cls,
    config_cls,
    strategy_name,
):
    instrument = FakeInstrument()
    parent = FakeParent(instrument)
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = context_cls(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context._current_trade_id = "T-1"
    context._stop_price = 90.0

    context._submit_market(OrderSide.BUY, Decimal("100"))

    parent.order_factory.market.assert_called_once_with(
        instrument_id,
        OrderSide.BUY,
        Decimal("0.1"),
    )
    assert parent.submit_order.call_args_list == [call(parent.order_factory.market.return_value)]
    parent.order_factory.stop_market.assert_not_called()
    parent.order_factory.limit.assert_not_called()
    assert sorted(parent._exit_order_requests) == ["O-entry", "T-1"]
    assert parent._order_trade_ids == {"O-entry": "T-1"}

    submit_pending_exit_orders(owner=parent, trade_id="T-1")

    parent.order_factory.stop_market.assert_called_once_with(
        instrument_id,
        OrderSide.SELL,
        Decimal("0.1"),
        "price:90.0",
        reduce_only=True,
        tags=[
            EXIT_STOP_TAG,
            f"strategy={strategy_name}",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )
    parent.order_factory.limit.assert_not_called()
    assert parent.submit_order.call_args_list == [
        call(parent.order_factory.market.return_value),
        call(parent.order_factory.stop_market.return_value),
    ]
    assert parent._exit_order_requests == {}
    parent.close_all_positions.assert_not_called()


@pytest.mark.parametrize(
    ("context_cls", "config_cls", "strategy_name"),
    [
        (_RuntimeSymbolContext, AdaptiveTrendRiderConfig, "adaptive_trend_rider"),
        (_RuntimeVolumeContext, VolumeSpikeTrendConfig, "volume_spike_trend"),
        (_RuntimeSqueezeContext, SqueezeBreakoutConfig, "squeeze_breakout"),
        (_RuntimeDemoEMAContext, DemoEMACrossConfig, "demo_ema_cross"),
    ],
)
def test_runtime_context_trade_lifecycle_submits_protection_once_when_position_event_lacks_entry_order_id(
    context_cls,
    config_cls,
    strategy_name,
):
    instrument = FakeInstrument()
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    parent = FakeRuntimeParent(instrument, instrument_id=instrument_id)
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = context_cls(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context._current_trade_id = "T-1"
    context._stop_price = 90.0

    context._submit_market(OrderSide.BUY, Decimal("100"))
    parent.on_order_filled(
        SimpleNamespace(
            client_order_id="O-entry",
            venue_order_id="V-entry",
            order_side=OrderSide.BUY,
            position_id="ETHUSDT-LINEAR.BYBIT-EXTERNAL",
            last_qty=Decimal("0.1"),
            last_px=Decimal("100"),
        )
    )
    parent.on_position_opened(
        SimpleNamespace(
            order_side=OrderSide.BUY,
            position_id="ETHUSDT-LINEAR.BYBIT-EXTERNAL",
        )
    )

    parent.order_factory.stop_market.assert_called_once_with(
        instrument_id,
        OrderSide.SELL,
        Decimal("0.1"),
        "price:90.0",
        reduce_only=True,
        tags=[
            EXIT_STOP_TAG,
            f"strategy={strategy_name}",
            "symbol=ETH/USDT:USDT",
            "trade_id=T-1",
        ],
    )
    parent.order_factory.limit.assert_not_called()
    assert parent.submit_order.call_args_list == [
        call(parent.order_factory.market.return_value),
        call(parent.order_factory.stop_market.return_value),
    ]
    assert parent._exit_order_requests == {}
    parent.close_all_positions.assert_not_called()


@pytest.mark.parametrize(
    ("context_cls", "config_cls", "module_path"),
    [
        (
            _RuntimeSymbolContext,
            AdaptiveTrendRiderConfig,
            "trader_ai.strategies.adaptive_trend_rider.activate_profit_runner_if_ready",
        ),
        (
            _RuntimeVolumeContext,
            VolumeSpikeTrendConfig,
            "trader_ai.strategies.volume_spike_trend.activate_profit_runner_if_ready",
        ),
        (
            _RuntimeSqueezeContext,
            SqueezeBreakoutConfig,
            "trader_ai.strategies.squeeze_breakout.activate_profit_runner_if_ready",
        ),
        (
            _RuntimeDemoEMAContext,
            DemoEMACrossConfig,
            "trader_ai.strategies.macd_demo.activate_profit_runner_if_ready",
        ),
    ],
)
def test_runtime_context_checks_profit_runner_on_position_bar(
    monkeypatch,
    context_cls,
    config_cls,
    module_path,
):
    instrument = FakeInstrument()
    parent = FakeParent(instrument)
    activate_profit_runner = Mock(return_value=False)
    monkeypatch.setattr(module_path, activate_profit_runner)
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = context_cls(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context.atr = SimpleNamespace(value=1.0)
    context._current_trade_id = "T-1"
    context._position_side = OrderSide.BUY
    context._entry_price = 100.0
    context._best_price = 100.0
    context._stop_price = 90.0
    context._entry_atr = 1.0
    bar = SimpleNamespace(
        high=FakePrice("111"),
        low=FakePrice("100"),
        close=FakePrice("110"),
    )

    context._manage_position(bar)

    activate_profit_runner.assert_called_once_with(
        owner=parent,
        trade_id="T-1",
        latest_price=Decimal("110"),
        min_improvement_bps=50,
    )


@pytest.mark.parametrize(
    ("context_cls", "config_cls"),
    [
        (_RuntimeSymbolContext, AdaptiveTrendRiderConfig),
        (_RuntimeVolumeContext, VolumeSpikeTrendConfig),
        (_RuntimeSqueezeContext, SqueezeBreakoutConfig),
        (_RuntimeDemoEMAContext, DemoEMACrossConfig),
    ],
)
def test_runtime_context_exit_bar_does_not_close_on_intrabar_stop_wick(
    monkeypatch,
    context_cls,
    config_cls,
):
    instrument = FakeInstrument()
    parent = FakeParent(instrument)
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = context_cls(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context._position_side = OrderSide.BUY
    context._bars_in_position = 7
    context._entry_price = 100.0
    context._best_price = 100.0
    context._stop_price = 90.0
    context._entry_atr = 2.0
    context.atr = SimpleNamespace(value=2.0)
    manage_position = Mock(wraps=context._manage_position)
    monkeypatch.setattr(context, "_manage_position", manage_position)
    bar = SimpleNamespace(
        high=FakePrice("101"),
        low=FakePrice("89"),
        close=FakePrice("100"),
    )

    context.on_exit_bar(bar)

    manage_position.assert_not_called()
    assert context._bars_in_position == 7
    assert context._position_side == OrderSide.BUY
    parent.close_all_positions.assert_not_called()


@pytest.mark.parametrize(
    ("context_cls", "config_cls"),
    [
        (_RuntimeSymbolContext, AdaptiveTrendRiderConfig),
        (_RuntimeVolumeContext, VolumeSpikeTrendConfig),
        (_RuntimeSqueezeContext, SqueezeBreakoutConfig),
        (_RuntimeDemoEMAContext, DemoEMACrossConfig),
    ],
)
def test_runtime_context_primary_bar_closes_on_confirmed_stop_hit(
    context_cls,
    config_cls,
):
    instrument = FakeInstrument()
    parent = FakeParent(instrument)
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = context_cls(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context._position_side = OrderSide.BUY
    context._bars_in_position = 7
    context._entry_price = 100.0
    context._best_price = 100.0
    context._stop_price = 90.0
    context._entry_atr = 2.0
    context.atr = SimpleNamespace(value=2.0)
    bar = SimpleNamespace(
        high=FakePrice("101"),
        low=FakePrice("89"),
        close=FakePrice("100"),
    )

    context._manage_position(bar)

    parent.close_all_positions.assert_called_once_with(instrument_id)


def test_runtime_context_closes_position_when_exit_order_submission_fails():
    instrument = FakeInstrument()
    parent = FakeParent(instrument)
    parent.order_factory.stop_market.side_effect = RuntimeError("exchange rejected stop")
    instrument_id = InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT")
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
    config = AdaptiveTrendRiderConfig(
        instrument_id=instrument_id,
        bar_type=bar_type,
        trade_size=Decimal("1.0"),
        trade_notional_usdt=Decimal("10"),
    )
    context = _RuntimeSymbolContext(
        parent=parent,
        config=config,
        instrument=instrument,
        instrument_id=instrument_id,
        bar_type=bar_type,
        ccxt_symbol="ETH/USDT:USDT",
    )
    context._current_trade_id = "T-1"
    context._stop_price = 90.0

    context._submit_market(OrderSide.BUY, Decimal("100"))

    submit_pending_exit_orders(owner=parent, trade_id="T-1")

    assert parent.submit_order.call_args_list == [call(parent.order_factory.market.return_value)]
    parent.close_all_positions.assert_called_once_with(instrument_id)
