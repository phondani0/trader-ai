from __future__ import annotations

from collections import deque
from decimal import Decimal
from uuid import uuid4

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import DonchianChannel
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from trader_ai.core.database import DatabaseManager
from trader_ai.execution.exit_orders import ExitOrderRequest
from trader_ai.execution.exit_orders import activate_profit_runner_if_ready
from trader_ai.execution.exit_orders import cancel_exit_orders
from trader_ai.execution.exit_orders import record_exit_order_request
from trader_ai.execution.exit_orders import replace_exit_stop_if_improved
from trader_ai.execution.exit_orders import submit_exit_orders_after_entry
from trader_ai.execution.instrumentation import execution_instrumentation
from trader_ai.execution.observer import ExecutionObserverMixin
from trader_ai.execution.recovery import StrategyPositionState
from trader_ai.strategies.bar_types import exit_management_bar_type


class SqueezeBreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 8
    slow_ema_period: int = 21
    dc_period: int = 20
    rsi_period: int = 14
    atr_period: int = 14
    atr_ma_period: int = 50
    volume_window: int = 20
    volume_spike_mult: float = 2.0
    atr_expansion_gate: float = 1.5
    rsi_momentum_low: float = 0.60
    rsi_momentum_high: float = 0.80
    rsi_short_low: float = 0.20
    rsi_short_high: float = 0.40
    initial_stop_atr_mult: float = 3.0
    trailing_stop_atr_mult: float = 3.5
    trailing_activation_atr_mult: float = 4.0
    max_hold_bars: int = 288
    cooldown_bars: int = 12
    squeeze_lookback: int = 10
    squeeze_threshold: float = 0.5
    use_discovery_journal: bool = False
    discovery_gate: str = "off"
    ccxt_symbol: str = ""
    trade_notional_usdt: Decimal = Decimal("0")
    take_profit_order_enabled: bool = False
    take_profit_r_multiple: Decimal = Decimal("2.0")
    profit_runner_enabled: bool = True
    profit_runner_activation_r_multiple: Decimal = Decimal("3.0")
    profit_runner_stop_lock_r_multiple: Decimal = Decimal("0.25")
    trailing_stop_enabled: bool = True
    trailing_replace_min_bps: int = 50


class _RuntimeSqueezeContext:
    def __init__(
        self,
        *,
        parent: SqueezeBreakoutStrategy,
        config: SqueezeBreakoutConfig,
        instrument,
        instrument_id: InstrumentId,
        bar_type: BarType,
        ccxt_symbol: str,
    ):
        self.parent = parent
        self.config = config
        self.instrument = instrument
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.ccxt_symbol = ccxt_symbol
        self.dc = DonchianChannel(config.dc_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.atr = AverageTrueRange(config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_window)
        self._atr_history: deque[float] = deque(maxlen=config.atr_ma_period)
        self._dc_widths: deque[float] = deque(maxlen=config.squeeze_lookback)
        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._position_quantity: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._entry_atr: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._last_warmup_close_ns: int | None = None

    def seed_warmup(
        self,
        bars: list[Bar],
        *,
        last_replayed_close_ns: int | None = None,
    ) -> None:
        for bar in bars:
            self._handle_indicators(bar)
            self._update_windows(bar)
        self._last_warmup_close_ns = last_replayed_close_ns

    def on_bar(self, bar: Bar) -> None:
        if (
            self._last_warmup_close_ns is not None
            and bar.ts_event <= self._last_warmup_close_ns
        ):
            return

        self._handle_indicators(bar)
        self._update_windows(bar)
        if not self._indicators_ready():
            return

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        if self._position_side is not None:
            self._manage_position(bar)
        elif self._cooldown_remaining <= 0:
            self._evaluate_entry(bar)

    def on_exit_bar(self, bar: Bar) -> None:
        return

    def _handle_indicators(self, bar: Bar) -> None:
        self.dc.handle_bar(bar)
        self.rsi.handle_bar(bar)
        self.atr.handle_bar(bar)

    def _update_windows(self, bar: Bar) -> None:
        self._volumes.append(bar.volume.as_double())
        if self.atr.initialized:
            self._atr_history.append(self.atr.value)
        if self.dc.initialized:
            self._dc_widths.append(self.dc.upper - self.dc.lower)

    def _indicators_ready(self) -> bool:
        return (
            self.dc.initialized
            and self.rsi.initialized
            and self.atr.initialized
            and len(self._volumes) >= self.config.volume_window
            and len(self._atr_history) >= self.config.atr_ma_period
            and len(self._dc_widths) >= self.config.squeeze_lookback
        )

    def _is_squeeze_breakout(self) -> bool:
        widths = list(self._dc_widths)
        recent_min = min(widths[:-1]) if len(widths) > 1 else widths[0]
        current = widths[-1]
        avg_width = sum(widths) / len(widths)
        return recent_min < avg_width * self.config.squeeze_threshold and current > recent_min

    def _evaluate_entry(self, bar: Bar) -> None:
        if self._position_side is not None:
            return

        if self._journal_blocks_entry():
            self._record_decision("discovery_gate")
            return

        if self.config.ccxt_symbol in self.parent._blocked_symbols:
            self._record_decision("recovery_blocked")
            return

        if self.parent._recovery_service is not None and not self.parent._recovery_service.is_entry_allowed(self.config.ccxt_symbol):
            self._record_decision("recovery_gate")
            return

        current_atr = self.atr.value
        atr_ma = sum(self._atr_history) / len(self._atr_history)
        if current_atr < atr_ma * self.config.atr_expansion_gate:
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        if bar.volume.as_double() < avg_volume * self.config.volume_spike_mult:
            return

        if not self._is_squeeze_breakout():
            return

        close = bar.close.as_double()
        high = bar.high.as_double()
        low = bar.low.as_double()
        rsi = self.rsi.value

        if high >= self.dc.upper and self.config.rsi_momentum_low <= rsi <= self.config.rsi_momentum_high:
            self._open_position(OrderSide.BUY, close, current_atr)
        elif low <= self.dc.lower and self.config.rsi_short_low <= rsi <= self.config.rsi_short_high:
            self._open_position(OrderSide.SELL, close, current_atr)

    def _journal_blocks_entry(self) -> bool:
        if not self.parent.db or not self.ccxt_symbol:
            return False

        gate = self.config.discovery_gate
        if gate == "off" and not self.config.use_discovery_journal:
            return False

        pending = self.parent.db.get_pending_discoveries()
        is_hot = any(p["symbol"] == self.ccxt_symbol for p in pending)
        if is_hot:
            return False

        return gate == "hard" or self.config.use_discovery_journal

    def _open_position(self, side: OrderSide, price: float, current_atr: float) -> None:
        self._current_trade_id = f"T-{uuid4().hex}"
        execution_instrumentation.signal_generated(
            strategy="squeeze_breakout",
            symbol=self.ccxt_symbol or str(self.instrument_id),
            side=side.name if hasattr(side, "name") else str(side),
            trade_id=self._current_trade_id,
            entry_price=price,
        )
        if side == OrderSide.BUY:
            self._stop_price = price - self.config.initial_stop_atr_mult * current_atr
        else:
            self._stop_price = price + self.config.initial_stop_atr_mult * current_atr

        self._position_side = side
        self._entry_price = price
        self._best_price = price
        self._entry_atr = current_atr
        self._trailing_active = False
        self._bars_in_position = 0
        self._submit_market(side, Decimal(str(price)))

    def _manage_position(self, bar: Bar, *, count_hold_bar: bool = True) -> None:
        if count_hold_bar:
            self._bars_in_position += 1
        high = bar.high.as_double()
        low = bar.low.as_double()
        current_atr = self.atr.value

        if self._position_side == OrderSide.BUY:
            self._best_price = max(self._best_price, high)
        else:
            self._best_price = min(self._best_price, low)

        if self._position_side == OrderSide.BUY and low <= self._stop_price:
            self._close_and_reset("stop_hit")
            return
        if self._position_side == OrderSide.SELL and high >= self._stop_price:
            self._close_and_reset("stop_hit")
            return

        if self._bars_in_position >= self.config.max_hold_bars:
            self._close_and_reset("max_hold")
            return

        activation_distance = self.config.trailing_activation_atr_mult * self._entry_atr
        if self._position_side == OrderSide.BUY:
            if self._best_price - self._entry_price >= activation_distance:
                self._trailing_active = True
        elif self._entry_price - self._best_price >= activation_distance:
            self._trailing_active = True

        if self._trailing_active:
            trail_distance = self.config.trailing_stop_atr_mult * current_atr
            if self._position_side == OrderSide.BUY:
                self._stop_price = max(self._stop_price, self._best_price - trail_distance)
            else:
                self._stop_price = min(self._stop_price, self._best_price + trail_distance)
            if self.config.trailing_stop_enabled:
                replace_exit_stop_if_improved(
                    owner=self.parent,
                    trade_id=getattr(self, "_current_trade_id", ""),
                    stop_price=Decimal(str(self._stop_price)),
                    min_improvement_bps=self.config.trailing_replace_min_bps,
                )
        activate_profit_runner_if_ready(
            owner=self.parent,
            trade_id=getattr(self, "_current_trade_id", ""),
            latest_price=Decimal(str(bar.close)),
            min_improvement_bps=self.config.trailing_replace_min_bps,
        )

    def _close_and_reset(self, reason: str) -> None:
        execution_instrumentation.position_close_requested(
            strategy="squeeze_breakout",
            symbol=self.ccxt_symbol or str(self.instrument_id),
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            trade_id=getattr(self, "_current_trade_id", ""),
            reason=reason,
            entry_price=self._entry_price,
            stop_price=self._stop_price,
            bars_in_position=self._bars_in_position,
        )
        cancel_exit_orders(
            owner=self.parent,
            trade_id=getattr(self, "_current_trade_id", ""),
        )
        self.parent.close_all_positions(self.instrument_id)
        self._reset_position_state()
        self._cooldown_remaining = self.config.cooldown_bars

    def _reset_position_state(self) -> None:
        self._position_side = None
        self._entry_price = 0.0
        self._best_price = 0.0
        self._stop_price = 0.0
        self._entry_atr = 0.0
        self._trailing_active = False
        self._bars_in_position = 0

    def _order_quantity(self, price: Decimal):
        instrument = self.parent.cache.instrument(self.instrument_id) or self.instrument
        if self.config.trade_notional_usdt > 0 and price > 0:
            return instrument.make_qty(self.config.trade_notional_usdt / price)
        return instrument.make_qty(self.config.trade_size)

    def _submit_market(self, side: OrderSide, price: Decimal) -> None:
        instrument = self.parent.cache.instrument(self.instrument_id) or self.instrument
        quantity = self._order_quantity(price)
        order = self.parent.order_factory.market(
            self.instrument_id,
            side,
            quantity,
        )
        trade_id = getattr(self, "_current_trade_id", str(order.client_order_id))
        self._order_trade_ids = getattr(self, "_order_trade_ids", {})
        self._order_trade_ids[str(order.client_order_id)] = trade_id
        parent_order_trade_ids = getattr(self.parent, "_order_trade_ids", {})
        parent_order_trade_ids[str(order.client_order_id)] = trade_id
        self.parent._order_trade_ids = parent_order_trade_ids
        execution_instrumentation.order_submitted(
            strategy="squeeze_breakout",
            symbol=self.ccxt_symbol or str(self.instrument_id),
            side=side.name if hasattr(side, "name") else str(side),
            trade_id=trade_id,
            order_id=str(order.client_order_id),
            quantity=float(str(quantity)),
            notional_usdt=float(price) * float(str(quantity)),
        )
        record_exit_order_request(
            owner=self.parent,
            entry_order=order,
            request=ExitOrderRequest(
                instrument=instrument,
                instrument_id=self.instrument_id,
                position_side=side,
                quantity=quantity,
                entry_price=price,
                stop_price=Decimal(str(self._stop_price)),
                strategy_name="squeeze_breakout",
                symbol=self.ccxt_symbol or str(self.instrument_id),
                trade_id=trade_id,
                take_profit_order_enabled=self.config.take_profit_order_enabled,
                take_profit_r_multiple=self.config.take_profit_r_multiple,
                profit_runner_enabled=self.config.profit_runner_enabled,
                profit_runner_activation_r_multiple=self.config.profit_runner_activation_r_multiple,
                profit_runner_stop_lock_r_multiple=self.config.profit_runner_stop_lock_r_multiple,
            ),
        )
        self.parent.submit_order(order)


class SqueezeBreakoutStrategy(ExecutionObserverMixin, Strategy):
    execution_strategy_name = "squeeze_breakout"

    """Donchian breakout strategy that enters on channel breaks after
    a volatility squeeze, with ATR-based trailing stops."""

    def __init__(self, config: SqueezeBreakoutConfig):
        super().__init__(config)
        self.db = (
            DatabaseManager()
            if config.use_discovery_journal or config.discovery_gate in {"soft", "hard"}
            else None
        )
        self.dc = DonchianChannel(config.dc_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.atr = AverageTrueRange(config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_window)
        self._atr_history: deque[float] = deque(maxlen=config.atr_ma_period)
        self._dc_widths: deque[float] = deque(maxlen=config.squeeze_lookback)

        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._entry_atr: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._last_warmup_close_ns: int | None = None
        self._runtime_contexts: dict[BarType, _RuntimeSqueezeContext] = {}
        self._runtime_exit_contexts: dict[BarType, _RuntimeSqueezeContext] = {}
        self._runtime_ccxt_symbols: set[str] = set()
        self._recovery_service = None
        self._blocked_symbols: set[str] = set()

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.dc)
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)
        self._exit_bar_type = exit_management_bar_type(self.config.bar_type)
        if self._exit_bar_type != self.config.bar_type:
            self.subscribe_bars(self._exit_bar_type)
        self._ensure_recovered_exit_orders()

    def on_bar(self, bar: Bar):
        if (
            bar.bar_type == getattr(self, "_exit_bar_type", None)
            and self._exit_bar_type != self.config.bar_type
        ):
            return

        runtime_exit_context = self._runtime_exit_contexts.get(bar.bar_type)
        if runtime_exit_context is not None:
            runtime_exit_context.on_exit_bar(bar)
            return

        runtime_context = self._runtime_contexts.get(bar.bar_type)
        if runtime_context is not None:
            runtime_context.on_bar(bar)
            return

        if (
            self._last_warmup_close_ns is not None
            and bar.ts_event <= self._last_warmup_close_ns
        ):
            return

        self._volumes.append(bar.volume.as_double())
        if self.atr.initialized:
            self._atr_history.append(self.atr.value)
        if self.dc.initialized:
            width = self.dc.upper - self.dc.lower
            self._dc_widths.append(width)

        if not self._indicators_ready():
            return

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        if self._position_side is not None:
            self._manage_position(bar)
        elif self._cooldown_remaining <= 0:
            self._evaluate_entry(bar)

    def on_stop(self):
        self.close_all_positions(self.config.instrument_id)
        for context in self._runtime_contexts.values():
            self.close_all_positions(context.instrument_id)
        self._reset_position_state()

    def seed_warmup(
        self,
        bars: list[Bar],
        *,
        last_replayed_close_ns: int | None = None,
    ) -> None:
        for bar in bars:
            self.dc.handle_bar(bar)
            self.rsi.handle_bar(bar)
            self.atr.handle_bar(bar)
            self._volumes.append(bar.volume.as_double())
            if self.atr.initialized:
                self._atr_history.append(self.atr.value)
            if self.dc.initialized:
                self._dc_widths.append(self.dc.upper - self.dc.lower)
        self._last_warmup_close_ns = last_replayed_close_ns

    def active_ccxt_symbols(self) -> set[str]:
        symbols = set(self._runtime_ccxt_symbols)
        if self.config.ccxt_symbol:
            symbols.add(self.config.ccxt_symbol)
        return symbols

    def activate_component(
        self,
        component,
        bars: list[Bar],
        *,
        last_replayed_close_ns: int | None = None,
    ) -> bool:
        ccxt_symbol = component.instrument_spec.ccxt_symbol
        if not ccxt_symbol or ccxt_symbol in self.active_ccxt_symbols():
            return False
        if component.bar_type in self._runtime_contexts:
            return False

        context = _RuntimeSqueezeContext(
            parent=self,
            config=component.strategy.config,
            instrument=component.instrument,
            instrument_id=component.instrument.id,
            bar_type=component.bar_type,
            ccxt_symbol=ccxt_symbol,
        )
        context.seed_warmup(
            bars,
            last_replayed_close_ns=last_replayed_close_ns,
        )
        if not context._indicators_ready():
            self.log.warning(
                "Runtime activation warmup failed "
                f"{ccxt_symbol}: bars={len(bars)} ready=False"
            )
            return False

        self._runtime_contexts[component.bar_type] = context
        runtime_exit_bar_type = exit_management_bar_type(component.bar_type)
        if runtime_exit_bar_type != component.bar_type:
            self._runtime_exit_contexts[runtime_exit_bar_type] = context
        self._runtime_ccxt_symbols.add(ccxt_symbol)
        self.subscribe_bars(component.bar_type)
        if runtime_exit_bar_type != component.bar_type:
            self.subscribe_bars(runtime_exit_bar_type)
        self.log.info(
            "Runtime symbol activated "
            f"{component.instrument.id}: bars={len(bars)} ready=True"
        )
        return True

    def _indicators_ready(self) -> bool:
        return (
            self.indicators_initialized()
            and len(self._volumes) >= self.config.volume_window
            and len(self._atr_history) >= self.config.atr_ma_period
            and len(self._dc_widths) >= self.config.squeeze_lookback
        )

    def _is_squeeze_breakout(self) -> bool:
        """Detect if the channel was recently squeezed (narrow) then expanding."""
        widths = list(self._dc_widths)
        recent_min = min(widths[:-1]) if len(widths) > 1 else widths[0]
        current = widths[-1]
        avg_width = sum(widths) / len(widths)
        return recent_min < avg_width * self.config.squeeze_threshold and current > recent_min

    def _evaluate_entry(self, bar: Bar):
        current_atr = self.atr.value
        atr_ma = sum(self._atr_history) / len(self._atr_history)
        if current_atr < atr_ma * self.config.atr_expansion_gate:
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        if bar.volume.as_double() < avg_volume * self.config.volume_spike_mult:
            return

        if not self._is_squeeze_breakout():
            return

        close = bar.close.as_double()
        high = bar.high.as_double()
        low = bar.low.as_double()
        rsi = self.rsi.value
        upper = self.dc.upper
        lower = self.dc.lower

        if high >= upper and self.config.rsi_momentum_low <= rsi <= self.config.rsi_momentum_high:
            self._open_position(OrderSide.BUY, close, current_atr)
        elif low <= lower and self.config.rsi_short_low <= rsi <= self.config.rsi_short_high:
            self._open_position(OrderSide.SELL, close, current_atr)

    def _open_position(self, side: OrderSide, price: float, current_atr: float):
        self._current_trade_id = f"T-{uuid4().hex}"
        execution_instrumentation.signal_generated(
            strategy="squeeze_breakout",
            symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
            side=side.name if hasattr(side, "name") else str(side),
            trade_id=self._current_trade_id,
            entry_price=price,
        )
        if side == OrderSide.BUY:
            self._stop_price = price - self.config.initial_stop_atr_mult * current_atr
        else:
            self._stop_price = price + self.config.initial_stop_atr_mult * current_atr

        self._position_side = side
        self._entry_price = price
        self._best_price = price
        self._entry_atr = current_atr
        self._trailing_active = False
        self._bars_in_position = 0
        self._submit_market(side, Decimal(str(price)))

    def _manage_position(self, bar: Bar, *, count_hold_bar: bool = True):
        if count_hold_bar:
            self._bars_in_position += 1
        high = bar.high.as_double()
        low = bar.low.as_double()
        current_atr = self.atr.value

        if self._position_side == OrderSide.BUY:
            self._best_price = max(self._best_price, high)
        else:
            self._best_price = min(self._best_price, low)

        # Stop check
        if self._position_side == OrderSide.BUY and low <= self._stop_price:
            self._close_and_reset("stop_hit")
            return
        if self._position_side == OrderSide.SELL and high >= self._stop_price:
            self._close_and_reset("stop_hit")
            return

        # Max hold
        if self._bars_in_position >= self.config.max_hold_bars:
            self._close_and_reset("max_hold")
            return

        # Trailing activation
        activation_distance = self.config.trailing_activation_atr_mult * self._entry_atr
        if self._position_side == OrderSide.BUY:
            if self._best_price - self._entry_price >= activation_distance:
                self._trailing_active = True
        else:
            if self._entry_price - self._best_price >= activation_distance:
                self._trailing_active = True

        # Trailing stop update
        if self._trailing_active:
            trail_distance = self.config.trailing_stop_atr_mult * current_atr
            if self._position_side == OrderSide.BUY:
                new_stop = self._best_price - trail_distance
                self._stop_price = max(self._stop_price, new_stop)
            else:
                new_stop = self._best_price + trail_distance
                self._stop_price = min(self._stop_price, new_stop)
            if self.config.trailing_stop_enabled:
                replace_exit_stop_if_improved(
                    owner=self,
                    trade_id=getattr(self, "_current_trade_id", ""),
                    stop_price=Decimal(str(self._stop_price)),
                    min_improvement_bps=self.config.trailing_replace_min_bps,
                )
        activate_profit_runner_if_ready(
            owner=self,
            trade_id=getattr(self, "_current_trade_id", ""),
            latest_price=Decimal(str(bar.close)),
            min_improvement_bps=self.config.trailing_replace_min_bps,
        )

    def _close_and_reset(self, reason: str):
        execution_instrumentation.position_close_requested(
            strategy="squeeze_breakout",
            symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            trade_id=getattr(self, "_current_trade_id", ""),
            reason=reason,
            entry_price=self._entry_price,
            stop_price=self._stop_price,
            bars_in_position=self._bars_in_position,
        )
        cancel_exit_orders(
            owner=self,
            trade_id=getattr(self, "_current_trade_id", ""),
        )
        self.close_all_positions(self.config.instrument_id)
        self._reset_position_state()
        self._cooldown_remaining = self.config.cooldown_bars

    def _reset_position_state(self):
        self._position_side = None
        self._entry_price = 0.0
        self._position_quantity = 0.0
        self._best_price = 0.0
        self._stop_price = 0.0
        self._entry_atr = 0.0
        self._trailing_active = False
        self._bars_in_position = 0

    def set_recovery_service(self, service) -> None:
        self._recovery_service = service

    def block_symbol(self, symbol: str, reason: str) -> None:
        self._blocked_symbols.add(symbol)

    def hydrate_position(self, state: StrategyPositionState) -> None:
        if state.strategy_name != self.execution_strategy_name:
            return

        side_str = state.side.upper()
        if side_str in ("BUY", "LONG"):
            self._position_side = OrderSide.BUY
        elif side_str in ("SELL", "SHORT"):
            self._position_side = OrderSide.SELL
        else:
            return

        self._current_trade_id = state.trade_id
        self._entry_price = state.entry_price
        self._position_quantity = state.quantity
        self._best_price = state.best_price
        self._stop_price = state.stop_price
        self._entry_atr = state.entry_atr
        self._trailing_active = state.trailing_active
        self._bars_in_position = state.bars_in_position

        json_state = state.strategy_state_json or {}
        self._cooldown_remaining = json_state.get("cooldown_remaining", 0)

        self.log.info(
            "Hydrated position state "
            f"strategy={self.execution_strategy_name} symbol={state.symbol} "
            f"trade_id={state.trade_id} side={side_str} "
            f"entry_price={self._entry_price} stop_price={self._stop_price}"
        )

    def persist_position_state(self) -> StrategyPositionState | None:
        if self._position_side is None:
            return None
        trade_id = getattr(self, "_current_trade_id", "unknown")
        return StrategyPositionState(
            strategy_name="squeeze_breakout",
            symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
            instrument_id=str(self.config.instrument_id),
            position_id=trade_id,
            trade_id=trade_id,
            side=self._position_side.name if hasattr(self._position_side, "name") else str(self._position_side),
            quantity=self._position_quantity,
            entry_price=self._entry_price,
            entry_atr=self._entry_atr,
            stop_price=self._stop_price,
            best_price=self._best_price,
            trailing_active=self._trailing_active,
            bars_in_position=self._bars_in_position,
            strategy_state_json={
                "cooldown_remaining": self._cooldown_remaining,
            },
        )

    def _ensure_recovered_exit_orders(self) -> None:
        if (
            self._position_side is None
            or self._position_quantity <= 0
            or self._entry_price <= 0
            or self._stop_price <= 0
        ):
            return
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = instrument.make_qty(Decimal(str(self._position_quantity)))
        submit_exit_orders_after_entry(
            owner=self,
            request=ExitOrderRequest(
                instrument=instrument,
                instrument_id=self.config.instrument_id,
                position_side=self._position_side,
                quantity=quantity,
                entry_price=Decimal(str(self._entry_price)),
                stop_price=Decimal(str(self._stop_price)),
                strategy_name=self.execution_strategy_name,
                symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
                trade_id=getattr(self, "_current_trade_id", "unknown"),
                take_profit_order_enabled=self.config.take_profit_order_enabled,
                take_profit_r_multiple=self.config.take_profit_r_multiple,
                profit_runner_enabled=self.config.profit_runner_enabled,
                profit_runner_activation_r_multiple=self.config.profit_runner_activation_r_multiple,
                profit_runner_stop_lock_r_multiple=self.config.profit_runner_stop_lock_r_multiple,
            ),
            on_failure=self._handle_exit_order_failure,
        )

    def _order_quantity(self, instrument, price: Decimal):
        if self.config.trade_notional_usdt > 0 and price > 0:
            return instrument.make_qty(self.config.trade_notional_usdt / price)
        return instrument.make_qty(self.config.trade_size)

    def _submit_market(self, side: OrderSide, price: Decimal):
        instrument = self.cache.instrument(self.config.instrument_id)
        quantity = self._order_quantity(instrument, price)
        order = self.order_factory.market(
            self.config.instrument_id,
            side,
            quantity,
        )
        trade_id = getattr(self, "_current_trade_id", str(order.client_order_id))
        self._order_trade_ids = getattr(self, "_order_trade_ids", {})
        self._order_trade_ids[str(order.client_order_id)] = trade_id
        execution_instrumentation.order_submitted(
            strategy="squeeze_breakout",
            symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
            side=side.name if hasattr(side, "name") else str(side),
            trade_id=trade_id,
            order_id=str(order.client_order_id),
            quantity=float(str(quantity)),
            notional_usdt=float(price) * float(str(quantity)),
        )
        record_exit_order_request(
            owner=self,
            entry_order=order,
            request=ExitOrderRequest(
                instrument=instrument,
                instrument_id=self.config.instrument_id,
                position_side=side,
                quantity=quantity,
                entry_price=price,
                stop_price=Decimal(str(self._stop_price)),
                strategy_name="squeeze_breakout",
                symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
                trade_id=trade_id,
                take_profit_order_enabled=self.config.take_profit_order_enabled,
                take_profit_r_multiple=self.config.take_profit_r_multiple,
                profit_runner_enabled=self.config.profit_runner_enabled,
                profit_runner_activation_r_multiple=self.config.profit_runner_activation_r_multiple,
                profit_runner_stop_lock_r_multiple=self.config.profit_runner_stop_lock_r_multiple,
            ),
        )
        self.submit_order(order)
