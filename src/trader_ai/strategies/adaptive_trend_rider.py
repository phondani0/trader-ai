from __future__ import annotations

from collections import Counter
from collections import deque
from decimal import Decimal
from uuid import uuid4

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
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
from trader_ai.execution.instrumentation import execution_instrumentation
from trader_ai.execution.observer import ExecutionObserverMixin
from trader_ai.execution.recovery import StrategyPositionState
from trader_ai.strategies.bar_types import exit_management_bar_type


class _RuntimeSymbolContext:
    def __init__(
        self,
        *,
        parent: AdaptiveTrendRiderStrategy,
        config: AdaptiveTrendRiderConfig,
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
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.atr = AverageTrueRange(config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_window)
        self._atr_history: deque[float] = deque(maxlen=config.atr_ma_period)
        self._trend_bars: int = 0
        self._prev_trend: int = 0
        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._entry_atr: float = 0.0
        self._bars_seen: int = 0
        self._last_warmup_close_ns: int | None = None
        self._decision_counts: Counter[str] = Counter()
        self._last_decision_reason: str | None = None

    def seed_warmup(
        self,
        bars: list[Bar],
        *,
        last_replayed_close_ns: int | None = None,
    ) -> None:
        for bar in bars:
            self._handle_indicators(bar)
            self._bars_seen += 1
            self._volumes.append(bar.volume.as_double())
            if self.atr.initialized:
                self._atr_history.append(self.atr.value)
        self._last_warmup_close_ns = last_replayed_close_ns

    def on_bar(self, bar: Bar) -> None:
        if self._is_warmup_replayed_bar(bar):
            self._record_decision("warmup_replayed_bar")
            return

        self._handle_indicators(bar)
        self._bars_seen += 1
        self._volumes.append(bar.volume.as_double())
        if self.atr.initialized:
            self._atr_history.append(self.atr.value)

        if not self._indicators_ready():
            self._record_decision("warming_up")
            return

        self._update_trend_counter()

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        if self._position_side is not None:
            self._record_decision("position_open")
            self._manage_position(bar)
        elif self._cooldown_remaining > 0:
            self._record_decision("cooldown")
        else:
            self._evaluate_entry(bar)

    def on_exit_bar(self, bar: Bar) -> None:
        return

    def _handle_indicators(self, bar: Bar) -> None:
        self.fast_ema.handle_bar(bar)
        self.slow_ema.handle_bar(bar)
        self.rsi.handle_bar(bar)
        self.atr.handle_bar(bar)

    def _is_warmup_replayed_bar(self, bar: Bar) -> bool:
        return (
            self._last_warmup_close_ns is not None
            and bar.ts_event <= self._last_warmup_close_ns
        )

    def _indicators_ready(self) -> bool:
        return (
            self.fast_ema.initialized
            and self.slow_ema.initialized
            and self.rsi.initialized
            and self.atr.initialized
            and len(self._volumes) >= self.config.volume_window
            and len(self._atr_history) >= self.config.atr_ma_period
        )

    def _record_decision(self, reason: str) -> None:
        self._decision_counts[reason] += 1
        self._last_decision_reason = reason

    def _update_trend_counter(self) -> None:
        fast = self.fast_ema.value
        slow = self.slow_ema.value
        if fast > slow:
            current = 1
        elif fast < slow:
            current = -1
        else:
            current = 0

        if current == self._prev_trend and current != 0:
            self._trend_bars += 1
        else:
            self._trend_bars = 1 if current != 0 else 0
        self._prev_trend = current

    def _evaluate_entry(self, bar: Bar) -> None:
        if self._trend_bars < self.config.trend_confirmation_bars:
            self._record_decision("trend_not_confirmed")
            return

        if self._journal_blocks_entry():
            self._record_decision("discovery_gate")
            return

        current_atr = self.atr.value
        atr_ma = sum(self._atr_history) / len(self._atr_history)
        if current_atr < atr_ma * self.config.atr_volatility_gate:
            self._record_decision("atr_gate")
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        if bar.volume.as_double() < avg_volume * self.config.volume_spike_mult:
            self._record_decision("volume_gate")
            return

        close = bar.close.as_double()
        fast = self.fast_ema.value
        slow = self.slow_ema.value
        rsi = self.rsi.value

        ema_spread_pct = abs(fast - slow) / slow
        if ema_spread_pct < self.config.ema_spread_min:
            self._record_decision("ema_spread_gate")
            return

        if fast > slow and self.config.rsi_long_low <= rsi <= self.config.rsi_long_high:
            self._record_decision("entry_submitted")
            self._open_position(OrderSide.BUY, close, current_atr)
        elif fast < slow and self.config.rsi_short_low <= rsi <= self.config.rsi_short_high:
            self._record_decision("entry_submitted")
            self._open_position(OrderSide.SELL, close, current_atr)
        else:
            self._record_decision("rsi_gate")

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
            strategy="adaptive_trend_rider",
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
            strategy="adaptive_trend_rider",
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
            strategy="adaptive_trend_rider",
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
                strategy_name="adaptive_trend_rider",
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


class AdaptiveTrendRiderConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 8
    slow_ema_period: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    atr_ma_period: int = 50
    volume_window: int = 20
    volume_spike_mult: float = 1.8
    atr_volatility_gate: float = 0.8
    rsi_long_low: float = 0.40
    rsi_long_high: float = 0.60
    rsi_short_low: float = 0.40
    rsi_short_high: float = 0.60
    initial_stop_atr_mult: float = 3.0
    trailing_stop_atr_mult: float = 3.5
    trailing_activation_atr_mult: float = 4.0
    max_hold_bars: int = 288
    cooldown_bars: int = 8
    ema_spread_min: float = 0.002
    trend_confirm_bars: int = 3
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
    warmup_diagnostics: bool = True
    warmup_log_interval_bars: int = 10
    decision_diagnostics: bool = True
    decision_log_interval_bars: int = 10


class AdaptiveTrendRiderStrategy(ExecutionObserverMixin, Strategy):
    execution_strategy_name = "adaptive_trend_rider"

    def __init__(self, config: AdaptiveTrendRiderConfig):
        super().__init__(config)
        self.db = (
            DatabaseManager()
            if config.use_discovery_journal or config.discovery_gate in {"soft", "hard"}
            else None
        )
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.atr = AverageTrueRange(config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_window)
        self._atr_history: deque[float] = deque(maxlen=config.atr_ma_period)
        self._trend_bars: int = 0
        self._prev_trend: int = 0  # 1=long, -1=short, 0=none

        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._entry_atr: float = 0.0
        self._bars_seen: int = 0
        self._last_warmup_log_bar: int = 0
        self._last_warmup_close_ns: int | None = None
        self._decision_counts: Counter[str] = Counter()
        self._last_decision_reason: str | None = None
        self._last_decision_log_bar: int = 0
        self._runtime_contexts: dict[BarType, _RuntimeSymbolContext] = {}
        self._runtime_exit_contexts: dict[BarType, _RuntimeSymbolContext] = {}
        self._runtime_ccxt_symbols: set[str] = set()
        self._recovery_service = None
        self._blocked_symbols: set[str] = set()

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)
        self._exit_bar_type = exit_management_bar_type(self.config.bar_type)
        if self._exit_bar_type != self.config.bar_type:
            self.subscribe_bars(self._exit_bar_type)

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

        if self._is_warmup_replayed_bar(bar):
            self._record_decision("warmup_replayed_bar")
            return

        self._bars_seen += 1
        self._volumes.append(bar.volume.as_double())
        if self.atr.initialized:
            self._atr_history.append(self.atr.value)

        if not self._indicators_ready():
            self._log_warmup_status()
            self._record_decision("warming_up")
            return

        self._update_trend_counter()

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        if self._position_side is not None:
            self._record_decision("position_open")
            self._manage_position(bar)
        elif self._cooldown_remaining > 0:
            self._record_decision("cooldown")
        else:
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
            self.fast_ema.handle_bar(bar)
            self.slow_ema.handle_bar(bar)
            self.rsi.handle_bar(bar)
            self.atr.handle_bar(bar)
            self._bars_seen += 1
            self._volumes.append(bar.volume.as_double())
            if self.atr.initialized:
                self._atr_history.append(self.atr.value)
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

        context = _RuntimeSymbolContext(
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

    def _is_warmup_replayed_bar(self, bar: Bar) -> bool:
        return (
            self._last_warmup_close_ns is not None
            and bar.ts_event <= self._last_warmup_close_ns
        )

    def _indicators_ready(self) -> bool:
        return (
            self.fast_ema.initialized
            and self.slow_ema.initialized
            and self.rsi.initialized
            and self.atr.initialized
            and len(self._volumes) >= self.config.volume_window
            and len(self._atr_history) >= self.config.atr_ma_period
        )

    def _warmup_status(self) -> dict[str, int | bool]:
        return {
            "indicators_initialized": (
                self.fast_ema.initialized
                and self.slow_ema.initialized
                and self.rsi.initialized
                and self.atr.initialized
            ),
            "volume_bars": len(self._volumes),
            "volume_required": self.config.volume_window,
            "atr_history_bars": len(self._atr_history),
            "atr_history_required": self.config.atr_ma_period,
        }

    def _log_warmup_status(self) -> None:
        if not self.config.warmup_diagnostics:
            return
        if self._last_warmup_log_bar and (
            self._bars_seen - self._last_warmup_log_bar
            < self.config.warmup_log_interval_bars
        ):
            return

        status = self._warmup_status()
        self.log.info(
            "Warmup pending "
            f"{self.config.instrument_id}: "
            f"indicators_initialized={status['indicators_initialized']}, "
            f"volume={status['volume_bars']}/{status['volume_required']}, "
            f"atr_history={status['atr_history_bars']}/{status['atr_history_required']}"
        )
        self._last_warmup_log_bar = self._bars_seen

    def _record_decision(self, reason: str) -> None:
        self._decision_counts[reason] += 1
        self._last_decision_reason = reason
        self._log_decision_status()

    def _decision_status(self) -> dict[str, object]:
        return {
            "last_reason": self._last_decision_reason,
            "counts": dict(sorted(self._decision_counts.items())),
        }

    def _log_decision_status(self) -> None:
        if not self.config.decision_diagnostics:
            return
        if self._last_decision_log_bar and (
            self._bars_seen - self._last_decision_log_bar
            < self.config.decision_log_interval_bars
        ):
            return

        counts = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(self._decision_counts.items())
        )
        self.log.info(
            "Decision status "
            f"{self.config.instrument_id}: "
            f"last_reason={self._last_decision_reason}, "
            f"counts={counts or 'none'}"
        )
        self._last_decision_log_bar = self._bars_seen

    def _update_trend_counter(self):
        fast = self.fast_ema.value
        slow = self.slow_ema.value
        if fast > slow:
            current = 1
        elif fast < slow:
            current = -1
        else:
            current = 0

        if current == self._prev_trend and current != 0:
            self._trend_bars += 1
        else:
            self._trend_bars = 1 if current != 0 else 0
        self._prev_trend = current

    def _evaluate_entry(self, bar: Bar) -> None:
        if self._trend_bars < self.config.trend_confirm_bars:
            self._record_decision("trend_not_confirmed")
            return

        if self._journal_blocks_entry():
            self._record_decision("discovery_gate")
            return

        if self.config.ccxt_symbol in self._blocked_symbols:
            self._record_decision("recovery_blocked")
            return

        if self._recovery_service is not None and not self._recovery_service.is_entry_allowed(self.config.ccxt_symbol):
            self._record_decision("recovery_gate")
            return

        current_atr = self.atr.value
        atr_ma = sum(self._atr_history) / len(self._atr_history)
        if current_atr < atr_ma * self.config.atr_volatility_gate:
            self._record_decision("atr_gate")
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        if bar.volume.as_double() < avg_volume * self.config.volume_spike_mult:
            self._record_decision("volume_gate")
            return

        close = bar.close.as_double()
        fast = self.fast_ema.value
        slow = self.slow_ema.value
        rsi = self.rsi.value

        ema_spread_pct = abs(fast - slow) / slow
        if ema_spread_pct < self.config.ema_spread_min:
            self._record_decision("ema_spread_gate")
            return

        if fast > slow and self.config.rsi_long_low <= rsi <= self.config.rsi_long_high:
            self._record_decision("entry_submitted")
            self._open_position(OrderSide.BUY, close, current_atr)
        elif fast < slow and self.config.rsi_short_low <= rsi <= self.config.rsi_short_high:
            self._record_decision("entry_submitted")
            self._open_position(OrderSide.SELL, close, current_atr)
        else:
            self._record_decision("rsi_gate")

    def _open_position(self, side: OrderSide, price: float, current_atr: float):
        self._current_trade_id = f"T-{uuid4().hex}"
        execution_instrumentation.signal_generated(
            strategy="adaptive_trend_rider",
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

    def _journal_blocks_entry(self) -> bool:
        if not self.db or not self.config.ccxt_symbol:
            return False

        gate = self.config.discovery_gate
        if gate == "off" and not self.config.use_discovery_journal:
            return False

        pending = self.db.get_pending_discoveries()
        is_hot = any(p["symbol"] == self.config.ccxt_symbol for p in pending)
        if is_hot:
            return False

        if gate == "hard" or self.config.use_discovery_journal:
            return True

        return False

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

        # Check stop hit first (conservative: assume worst fill)
        if self._position_side == OrderSide.BUY and low <= self._stop_price:
            self._close_and_reset("stop_hit")
            return
        if self._position_side == OrderSide.SELL and high >= self._stop_price:
            self._close_and_reset("stop_hit")
            return

        # Max hold time exit
        if self._bars_in_position >= self.config.max_hold_bars:
            self._close_and_reset("max_hold")
            return

        # Activate trailing stop once profit reaches threshold
        activation_distance = self.config.trailing_activation_atr_mult * self._entry_atr
        if self._position_side == OrderSide.BUY:
            if self._best_price - self._entry_price >= activation_distance:
                self._trailing_active = True
        else:
            if self._entry_price - self._best_price >= activation_distance:
                self._trailing_active = True

        # Update trailing stop — use wider trail to let big moves breathe
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
            strategy="adaptive_trend_rider",
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
        self._best_price = state.best_price
        self._stop_price = state.stop_price
        self._entry_atr = state.entry_atr
        self._trailing_active = state.trailing_active
        self._bars_in_position = state.bars_in_position

        json_state = state.strategy_state_json or {}
        self._cooldown_remaining = json_state.get("cooldown_remaining", 0)
        self._trend_bars = json_state.get("trend_bars", 0)
        self._prev_trend = json_state.get("prev_trend", 0)

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
            strategy_name="adaptive_trend_rider",
            symbol=self.config.ccxt_symbol or str(self.config.instrument_id),
            instrument_id=str(self.config.instrument_id),
            position_id=trade_id,
            trade_id=trade_id,
            side=self._position_side.name if hasattr(self._position_side, "name") else str(self._position_side),
            quantity=0.0,
            entry_price=self._entry_price,
            entry_atr=self._entry_atr,
            stop_price=self._stop_price,
            best_price=self._best_price,
            trailing_active=self._trailing_active,
            bars_in_position=self._bars_in_position,
            strategy_state_json={
                "cooldown_remaining": self._cooldown_remaining,
                "trend_bars": self._trend_bars,
                "prev_trend": self._prev_trend,
            },
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
            strategy="adaptive_trend_rider",
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
                strategy_name="adaptive_trend_rider",
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
