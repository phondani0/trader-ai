from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class DemoEMACrossConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 10
    slow_ema_period: int = 21
    rsi_period: int = 14
    rsi_long_gate: float = 0.55
    rsi_short_gate: float = 0.45
    atr_period: int = 14
    atr_ma_period: int = 50
    atr_expansion_gate: float = 1.0
    initial_stop_atr_mult: float = 1.5
    trailing_stop_atr_mult: float = 2.0
    trailing_activation_atr_mult: float = 1.5
    max_hold_bars: int = 64
    cooldown_bars: int = 12


class DemoEMACrossStrategy(Strategy):
    """Upgraded Demo EMA Cross with RSI momentum confirmation and ATR trailing stops."""

    def __init__(self, config: DemoEMACrossConfig):
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.atr = AverageTrueRange(config.atr_period)
        from collections import deque
        self._atr_history: deque[float] = deque(maxlen=config.atr_ma_period)

        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._entry_atr: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
        if self.atr.initialized:
            self._atr_history.append(self.atr.value)

        if not self.indicators_initialized() or len(self._atr_history) < self.config.atr_ma_period:
            return

        fast = self.fast_ema.value
        slow = self.slow_ema.value

        # Update previous values BEFORE evaluating logic
        if self._prev_fast is None:
            self._prev_fast = fast
            self._prev_slow = slow
            return

        # We compute crossover here so it's always accurate
        crossed_up = self._prev_fast <= self._prev_slow and fast > slow
        crossed_down = self._prev_fast >= self._prev_slow and fast < slow

        self._prev_fast = fast
        self._prev_slow = slow

        current_atr = self.atr.value
        atr_ma = sum(self._atr_history) / len(self._atr_history)
        if current_atr < atr_ma * self.config.atr_expansion_gate:
            self._cooldown_remaining = self.config.cooldown_bars  # stay out of chop
            return

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        if self._position_side is not None:
            self._manage_position(bar)
        elif self._cooldown_remaining <= 0:
            self._evaluate_entry(bar, crossed_up, crossed_down)

    def on_stop(self):
        self.close_all_positions(self.config.instrument_id)
        self._reset_position_state()

    def _evaluate_entry(self, bar: Bar, crossed_up: bool, crossed_down: bool):
        rsi = self.rsi.value
        close = bar.close.as_double()
        current_atr = self.atr.value

        if crossed_up and rsi >= self.config.rsi_long_gate:
            self._open_position(OrderSide.BUY, close, current_atr)
        elif crossed_down and rsi <= self.config.rsi_short_gate:
            self._open_position(OrderSide.SELL, close, current_atr)

    def _open_position(self, side: OrderSide, price: float, current_atr: float):
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
        self._submit_market(side)

    def _manage_position(self, bar: Bar):
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
            self._close_and_reset()
            return
        if self._position_side == OrderSide.SELL and high >= self._stop_price:
            self._close_and_reset()
            return

        # Max hold
        if self._bars_in_position >= self.config.max_hold_bars:
            self._close_and_reset()
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

    def _close_and_reset(self):
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

    def _submit_market(self, side: OrderSide):
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            self.config.instrument_id,
            side,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
