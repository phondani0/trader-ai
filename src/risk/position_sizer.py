"""
Position Sizer.

Utility for calculating optimal position sizes using different methods.
"""

from __future__ import annotations



class PositionSizer:
    """
    Multiple position sizing methods:
        - Fixed fractional (risk-based) — default
        - Fixed dollar amount
        - Kelly Criterion (advanced)
    """

    @staticmethod
    def fixed_fractional(
        capital: float,
        risk_pct: float,
        entry_price: float,
        stop_loss: float,
    ) -> int:
        """
        Fixed-fractional sizing: risk a fixed % of capital per trade.

        Size = (Capital × Risk%) / |Entry - StopLoss|
        """
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share == 0 or entry_price == 0:
            return 0

        max_risk_amount = capital * (risk_pct / 100)
        shares = int(max_risk_amount / risk_per_share)
        return max(0, shares)

    @staticmethod
    def fixed_amount(
        amount: float,
        entry_price: float,
    ) -> int:
        """
        Fixed dollar amount sizing.

        Size = Amount / Entry Price
        """
        if entry_price == 0:
            return 0
        return max(0, int(amount / entry_price))

    @staticmethod
    def kelly_criterion(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        capital: float,
        entry_price: float,
        kelly_fraction: float = 0.25,  # Use quarter-Kelly for safety
    ) -> int:
        """
        Kelly Criterion: mathematically optimal position size.

        Kelly% = W - [(1 - W) / R]
        where W = win rate, R = avg win / avg loss

        We use fractional Kelly (default 25%) because full Kelly is too aggressive.
        """
        if avg_loss == 0 or entry_price == 0:
            return 0

        r = avg_win / abs(avg_loss)
        kelly_pct = win_rate - ((1 - win_rate) / r)

        # Apply fraction (quarter-Kelly by default)
        kelly_pct = max(0, kelly_pct * kelly_fraction)

        # Cap at 10% of capital
        kelly_pct = min(kelly_pct, 0.10)

        amount = capital * kelly_pct
        shares = int(amount / entry_price)

        return max(0, shares)
