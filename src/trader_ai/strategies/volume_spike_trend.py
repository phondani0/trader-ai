from __future__ import annotations

from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class VolumeSpikeTrendConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 8
    slow_ema_period: int = 21
    volume_window: int = 20
    volume_multiplier: float = 1.8


class VolumeSpikeTrendStrategy(Strategy):
    def __init__(self, config: VolumeSpikeTrendConfig):
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_window)

    def on_start(self):
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar):
        self._volumes.append(bar.volume.as_double())
        if not self.indicators_initialized() or len(self._volumes) < self.config.volume_window:
            return

        avg_volume = sum(self._volumes) / len(self._volumes)
        is_spike = bar.volume.as_double() >= avg_volume * self.config.volume_multiplier
        if not is_spike:
            return

        if self.fast_ema.value > self.slow_ema.value:
            if self.portfolio.is_flat(self.config.instrument_id):
                self._submit_market(OrderSide.BUY)
            elif self.portfolio.is_net_short(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
                self._submit_market(OrderSide.BUY)
        elif self.fast_ema.value < self.slow_ema.value:
            if self.portfolio.is_flat(self.config.instrument_id):
                self._submit_market(OrderSide.SELL)
            elif self.portfolio.is_net_long(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
                self._submit_market(OrderSide.SELL)

    def on_stop(self):
        self.close_all_positions(self.config.instrument_id)

    def _submit_market(self, side: OrderSide):
        instrument = self.cache.instrument(self.config.instrument_id)
        order = self.order_factory.market(
            self.config.instrument_id,
            side,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
