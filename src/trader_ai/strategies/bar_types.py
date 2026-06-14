from __future__ import annotations

from nautilus_trader.model.data import BarType


def exit_management_bar_type(bar_type: BarType) -> BarType:
    raw = str(bar_type)
    if "-1-MINUTE-" in raw:
        return bar_type
    return BarType.from_str(raw.replace("-15-MINUTE-", "-1-MINUTE-"))
