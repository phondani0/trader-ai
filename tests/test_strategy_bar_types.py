from __future__ import annotations

from nautilus_trader.model.data import BarType

from trader_ai.strategies.bar_types import exit_management_bar_type


def test_exit_management_bar_type_keeps_instrument_and_uses_one_minute():
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")

    result = exit_management_bar_type(bar_type)

    assert str(result) == "ETHUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL"


def test_exit_management_bar_type_returns_original_one_minute_bar_type():
    bar_type = BarType.from_str("ETHUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL")

    result = exit_management_bar_type(bar_type)

    assert result == bar_type
