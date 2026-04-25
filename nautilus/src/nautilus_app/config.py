from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class NautilusSettings:
    venue: str
    bar_type: str
    starting_balance: str
    trade_size: str
    fast_ema_period: int
    slow_ema_period: int
    bars_count: int
    random_seed: int
    log_level: str


def load_settings(path: str | Path) -> NautilusSettings:
    raw_path = Path(path)
    with raw_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    return NautilusSettings(
        venue=str(payload["venue"]),
        bar_type=str(payload["bar_type"]),
        starting_balance=str(payload["starting_balance"]),
        trade_size=str(payload["trade_size"]),
        fast_ema_period=int(payload["fast_ema_period"]),
        slow_ema_period=int(payload["slow_ema_period"]),
        bars_count=int(payload["bars_count"]),
        random_seed=int(payload["random_seed"]),
        log_level=str(payload["log_level"]),
    )
