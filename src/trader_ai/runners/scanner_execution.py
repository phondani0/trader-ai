from __future__ import annotations

from dataclasses import dataclass
from typing import Any


NON_CRYPTO_SYMBOL_TYPES = {"commodity", "stock"}


@dataclass(frozen=True)
class ScannerExecutionConfig:
    enabled: bool = False
    max_symbols: int = 0
    min_score: float = 0.0
    min_24h_volume: float = 0.0
    blacklist: set[str] | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> ScannerExecutionConfig:
        return cls(
            enabled=bool(payload.get("enabled", False)),
            max_symbols=int(payload.get("max_symbols", 0)),
            min_score=float(payload.get("min_score", 0.0)),
            min_24h_volume=float(payload.get("min_24h_volume", 0.0)),
            blacklist={str(symbol) for symbol in payload.get("blacklist", [])},
        )


def ccxt_symbol_to_bybit_instrument_name(symbol: str) -> str:
    base, rest = symbol.split("/", maxsplit=1)
    quote = rest.split(":", maxsplit=1)[0]
    return f"{base.lower()}{quote.lower()}_linear_bybit"


def select_scanner_execution_symbols(
    candidates: list[dict[str, Any]],
    *,
    configured_symbols: set[str],
    config: ScannerExecutionConfig,
) -> list[str]:
    if not config.enabled or config.max_symbols <= 0:
        return []

    blacklist = config.blacklist or set()
    selected: list[str] = []
    for candidate in sorted(
        candidates,
        key=lambda row: float(row.get("score", 0.0) or 0.0),
        reverse=True,
    ):
        symbol = str(candidate.get("symbol", ""))
        if not symbol.endswith("/USDT:USDT"):
            continue
        if _is_non_crypto_market(candidate):
            continue
        if symbol in configured_symbols or symbol in blacklist:
            continue
        if float(candidate.get("score", 0.0) or 0.0) < config.min_score:
            continue
        if float(candidate.get("volume_24h", 0.0) or 0.0) < config.min_24h_volume:
            continue

        selected.append(symbol)
        if len(selected) >= config.max_symbols:
            break

    return selected


def _is_non_crypto_market(candidate: dict[str, Any]) -> bool:
    symbol_type = str(candidate.get("symbol_type") or "").lower()
    return symbol_type in NON_CRYPTO_SYMBOL_TYPES
