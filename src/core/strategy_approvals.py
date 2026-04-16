from __future__ import annotations


def get_global_strategy_approvals(payload: dict | None) -> dict:
    if not payload:
        return {}
    if "strategies" in payload and isinstance(payload["strategies"], dict):
        return payload["strategies"]
    return payload


def get_symbol_strategy_approvals(payload: dict | None, symbol: str) -> dict:
    if not payload:
        return {}
    symbols = payload.get("symbols")
    if isinstance(symbols, dict):
        symbol_payload = symbols.get(symbol, {})
        if isinstance(symbol_payload, dict):
            return symbol_payload.get("strategies", {}) or {}
        return {}
    return get_global_strategy_approvals(payload)

