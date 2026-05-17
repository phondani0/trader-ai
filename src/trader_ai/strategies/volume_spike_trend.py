from __future__ import annotations

from collections import deque
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


from trader_ai.core.database import DatabaseManager

class VolumeSpikeTrendConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 8
    slow_ema_period: int = 21
    volume_window: int = 20
    volume_multiplier: float = 3.0
    rsi_period: int = 14
    atr_period: int = 14
    rsi_long_low: float = 0.50
    rsi_long_high: float = 0.75
    rsi_short_low: float = 0.25
    rsi_short_high: float = 0.50
    initial_stop_atr_mult: float = 1.5
    trailing_stop_atr_mult: float = 2.0
    trailing_activation_atr_mult: float = 2.0
    max_hold_bars: int = 64
    cooldown_bars: int = 12
    use_discovery_journal: bool = False
    discovery_gate: str = "off"
    ccxt_symbol: str = ""
    warmup_diagnostics: bool = True
    warmup_log_interval_bars: int = 10


class VolumeSpikeTrendStrategy(Strategy):
    def __init__(self, config: VolumeSpikeTrendConfig):
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

        self._position_side: OrderSide | None = None
        self._entry_price: float = 0.0
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._entry_atr: float = 0.0
        self._trailing_active: bool = False
        self._bars_in_position: int = 0
        self._cooldown_remaining: int = 0
        self._bars_seen: int = 0
        self._last_warmup_log_bar: int = 0

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
        self._bars_seen += 1
        self._volumes.append(bar.volume.as_double())

        if not self._indicators_ready():
            self._log_warmup_status()
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

    def warmup_with_bars(self, bars: list[Bar]) -> None:
        for bar in bars:
            self.fast_ema.handle_bar(bar)
            self.slow_ema.handle_bar(bar)
            self.rsi.handle_bar(bar)
            self.atr.handle_bar(bar)
            self._bars_seen += 1
            self._volumes.append(bar.volume.as_double())

    def _indicators_ready(self) -> bool:
        return (
            self.fast_ema.initialized
            and self.slow_ema.initialized
            and self.rsi.initialized
            and self.atr.initialized
            and len(self._volumes) >= self.config.volume_window
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
            f"volume={status['volume_bars']}/{status['volume_required']}"
        )
        self._last_warmup_log_bar = self._bars_seen

    def _evaluate_entry(self, bar: Bar):
        if self._journal_blocks_entry():
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        is_spike = bar.volume.as_double() >= avg_volume * self.config.volume_multiplier
        if not is_spike:
            return

        fast = self.fast_ema.value
        slow = self.slow_ema.value
        rsi = self.rsi.value
        close = bar.close.as_double()
        current_atr = self.atr.value

        if fast > slow and self.config.rsi_long_low <= rsi <= self.config.rsi_long_high:
            self._open_position(OrderSide.BUY, close, current_atr)
        elif fast < slow and self.config.rsi_short_low <= rsi <= self.config.rsi_short_high:
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
