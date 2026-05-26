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
from trader_ai.strategies.squeeze_breakout import SqueezeBreakoutConfig
from trader_ai.strategies.squeeze_breakout import SqueezeBreakoutStrategy
from trader_ai.strategies.volume_spike_trend import VolumeSpikeTrendConfig
from trader_ai.strategies.volume_spike_trend import VolumeSpikeTrendStrategy


def list_strategies() -> list[str]:
    return [
        "adaptive_trend_rider",
        "demo_ema_cross",
        "nautilus_ema_cross",
        "squeeze_breakout",
        "volume_spike_trend",
    ]


def create_strategy(
    *,
    name: str,
    instrument_id,
    bar_type,
    trade_size,
    fast_ema_period: int,
    slow_ema_period: int,
    use_discovery_journal: bool = False,
    discovery_gate: str = "off",
    ccxt_symbol: str = "",
    params: dict[str, object] | None = None,
):
    if isinstance(instrument_id, str):
        instrument_id = InstrumentId.from_str(instrument_id)
    if isinstance(bar_type, str):
        bar_type = BarType.from_str(bar_type)
    if not isinstance(trade_size, Decimal):
        trade_size = Decimal(str(trade_size))
    strategy_params = dict(params or {})
    if "trade_notional_usdt" in strategy_params:
        strategy_params["trade_notional_usdt"] = Decimal(
            str(strategy_params["trade_notional_usdt"])
        )

    if name == "adaptive_trend_rider":
        config = AdaptiveTrendRiderConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=int(
                strategy_params.pop("fast_ema_period", fast_ema_period)
            ),
            slow_ema_period=int(
                strategy_params.pop("slow_ema_period", slow_ema_period)
            ),
            use_discovery_journal=use_discovery_journal,
            discovery_gate=discovery_gate,
            ccxt_symbol=ccxt_symbol,
            **strategy_params,
        )
        return AdaptiveTrendRiderStrategy(config)

    if name == "demo_ema_cross":
        config = DemoEMACrossConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=int(
                strategy_params.pop("fast_ema_period", fast_ema_period)
            ),
            slow_ema_period=int(
                strategy_params.pop("slow_ema_period", slow_ema_period)
            ),
            use_discovery_journal=use_discovery_journal,
            discovery_gate=discovery_gate,
            ccxt_symbol=ccxt_symbol,
            **strategy_params,
        )
        return DemoEMACrossStrategy(config)

    if name == "squeeze_breakout":
        config = SqueezeBreakoutConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=int(
                strategy_params.pop("fast_ema_period", fast_ema_period)
            ),
            slow_ema_period=int(
                strategy_params.pop("slow_ema_period", slow_ema_period)
            ),
            use_discovery_journal=use_discovery_journal,
            discovery_gate=discovery_gate,
            ccxt_symbol=ccxt_symbol,
            **strategy_params,
        )
        return SqueezeBreakoutStrategy(config)

    if name == "nautilus_ema_cross":
        config = EMACrossConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=int(
                strategy_params.pop("fast_ema_period", fast_ema_period)
            ),
            slow_ema_period=int(
                strategy_params.pop("slow_ema_period", slow_ema_period)
            ),
            **strategy_params,
        )
        return EMACross(config)

    if name == "volume_spike_trend":
        config = VolumeSpikeTrendConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema_period=int(
                strategy_params.pop("fast_ema_period", fast_ema_period)
            ),
            slow_ema_period=int(
                strategy_params.pop("slow_ema_period", slow_ema_period)
            ),
            use_discovery_journal=use_discovery_journal,
            discovery_gate=discovery_gate,
            ccxt_symbol=ccxt_symbol,
            **strategy_params,
        )
        return VolumeSpikeTrendStrategy(config)

    raise ValueError(f"Unknown strategy: {name}")
