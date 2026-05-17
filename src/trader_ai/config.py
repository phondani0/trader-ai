from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class NautilusSettings:
    instrument_name: str
    strategy_name: str
    data_source: str
    venue: str
    bar_type: str
    starting_balance: str
    trade_size: str
    fast_ema_period: int
    slow_ema_period: int
    bars_count: int
    random_seed: int
    log_level: str
    ccxt_limit: int = 500
    startup_backfill_enabled: bool = True
    startup_backfill_bars: int = 120


@dataclass(frozen=True)
class InstrumentSpec:
    provider: str
    template: str
    venue: str
    bar_type: str
    asset_class: str
    symbol: str | None = None
    starting_balance: str | None = None
    trade_size: str | None = None
    ccxt_exchange: str | None = None
    ccxt_symbol: str | None = None
    ccxt_timeframe: str | None = None


@dataclass(frozen=True)
class StrategyExecutionConfig:
    enabled: bool
    instruments: list[str]
    discovery_gate: str
    params: dict[str, object]


def load_settings(path: str | Path) -> NautilusSettings:
    raw_path = Path(path)
    with raw_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    return NautilusSettings(
        instrument_name=str(payload.get("instrument_name", "synthetic_fx")),
        strategy_name=str(payload.get("strategy_name", "demo_ema_cross")),
        data_source=str(payload.get("data_source", "synthetic")),
        venue=str(payload["venue"]),
        bar_type=str(payload["bar_type"]),
        starting_balance=str(payload["starting_balance"]),
        trade_size=str(payload["trade_size"]),
        fast_ema_period=int(payload["fast_ema_period"]),
        slow_ema_period=int(payload["slow_ema_period"]),
        bars_count=int(payload["bars_count"]),
        random_seed=int(payload["random_seed"]),
        log_level=str(payload["log_level"]),
        ccxt_limit=int(payload.get("ccxt_limit", 500)),
        startup_backfill_enabled=bool(
            payload.get("startup_backfill_enabled", True)
        ),
        startup_backfill_bars=int(payload.get("startup_backfill_bars", 120)),
    )


def load_instruments(path: str | Path) -> dict[str, InstrumentSpec]:
    raw_path = Path(path)
    with raw_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    return {
        name: InstrumentSpec(
            provider=str(spec["provider"]),
            template=str(spec["template"]),
            venue=str(spec["venue"]),
            bar_type=str(spec["bar_type"]),
            asset_class=str(spec["asset_class"]),
            symbol=str(spec["symbol"]) if spec.get("symbol") is not None else None,
            starting_balance=(
                str(spec["starting_balance"])
                if spec.get("starting_balance") is not None
                else None
            ),
            trade_size=(
                str(spec["trade_size"]) if spec.get("trade_size") is not None else None
            ),
            ccxt_exchange=(
                str(spec["ccxt_exchange"])
                if spec.get("ccxt_exchange") is not None
                else None
            ),
            ccxt_symbol=(
                str(spec["ccxt_symbol"]) if spec.get("ccxt_symbol") is not None else None
            ),
            ccxt_timeframe=(
                str(spec["ccxt_timeframe"])
                if spec.get("ccxt_timeframe") is not None
                else None
            ),
        )
        for name, spec in payload.items()
    }


def load_strategy_configs(path: str | Path) -> dict[str, StrategyExecutionConfig]:
    raw_path = Path(path)
    with raw_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    configs: dict[str, StrategyExecutionConfig] = {}
    for name, spec in payload.items():
        discovery_gate = str(spec.get("discovery_gate", "off")).lower()
        if discovery_gate not in {"off", "soft", "hard"}:
            raise ValueError(
                f"Invalid discovery_gate for {name}: {discovery_gate!r}. "
                "Expected one of: off, soft, hard."
            )

        configs[str(name)] = StrategyExecutionConfig(
            enabled=bool(spec.get("enabled", False)),
            instruments=[str(item) for item in spec.get("instruments", [])],
            discovery_gate=discovery_gate,
            params=dict(spec.get("params", {})),
        )

    return configs
