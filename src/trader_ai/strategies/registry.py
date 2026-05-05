from __future__ import annotations

from decimal import Decimal

from nautilus_trader.examples.strategies.ema_cross import EMACross
from nautilus_trader.examples.strategies.ema_cross import EMACrossConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from trader_ai.strategies.adaptive_trend_rider import AdaptiveTrendRiderConfig
from trader_ai.strategies.adaptive_trend_rider import AdaptiveTrendRiderStrategy
from trader_ai.strategies.macd_demo import DemoEMACrossConfig
from trader_ai.strategies.macd_demo import DemoEMACrossStrategy
from trader_ai.strategies.volume_spike_trend import VolumeSpikeTrendConfig
from trader_ai.strategies.volume_spike_trend import VolumeSpikeTrendStrategy


def list_strategies() -> list[str]:
    return ["adaptive_trend_rider", "demo_ema_cross", "nautilus_ema_cross", "volume_spike_trend"]


def create_strategy(
    *,
    name: str,
    instrument_id,
    bar_type,
    trade_size,
    fast_ema_period: int,
    slow_ema_period: int,
):
    if isinstance(instrument_id, str):
        instrument_id = InstrumentId.from_str(instrument_id)
    if isinstance(bar_type, str):
        bar_type = BarType.from_str(bar_type)
    if not isinstance(trade_size, Decimal):
        trade_size = Decimal(str(trade_size))

    if name == "adaptive_trend_rider":
        config = AdaptiveTrendRiderConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=fast_ema_period,
            slow_ema_period=slow_ema_period,
        )
        return AdaptiveTrendRiderStrategy(config)

    if name == "demo_ema_cross":
        config = DemoEMACrossConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=fast_ema_period,
            slow_ema_period=slow_ema_period,
        )
        return DemoEMACrossStrategy(config)

    if name == "nautilus_ema_cross":
        config = EMACrossConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=fast_ema_period,
            slow_ema_period=slow_ema_period,
        )
        return EMACross(config)

    if name == "volume_spike_trend":
        config = VolumeSpikeTrendConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=fast_ema_period,
            slow_ema_period=slow_ema_period,
        )
        return VolumeSpikeTrendStrategy(config)

    raise ValueError(f"Unknown strategy: {name}")
