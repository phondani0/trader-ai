from __future__ import annotations

from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import DonchianChannel
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


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
    initial_stop_atr_mult: float = 1.5
    trailing_stop_atr_mult: float = 2.0
    trailing_activation_atr_mult: float = 2.5
    max_hold_bars: int = 128
    cooldown_bars: int = 12
    squeeze_lookback: int = 10
    squeeze_threshold: float = 0.5


class SqueezeBreakoutStrategy(Strategy):
    """Donchian breakout strategy that enters on channel breaks after
    a volatility squeeze, with ATR-based trailing stops."""

    def __init__(self, config: SqueezeBreakoutConfig):
        super().__init__(config)
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

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.dc)
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
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
        self._reset_position_state()

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
