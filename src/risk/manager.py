"""
Risk Manager.

The final gatekeeper before any order is placed.
Validates every trade against account-wide risk rules.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from src.core.models import Signal, Order, Position, Side, OrderType, Trade


class RiskManager:
    """
    Enforces risk management rules:
        - Max risk per trade (% of capital)
        - Max daily loss (kill switch)
        - Max open positions
        - Max exposure per asset
        - Mandatory stop-loss
        - Position sizing based on ATR
    """

    def __init__(self, config: dict, initial_capital: float):
        risk_config = config.get("risk_management", {})

        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.day_start_capital = initial_capital

        self.max_risk_per_trade_pct = risk_config.get("max_risk_per_trade_pct", 2.0)
        self.max_daily_loss_pct = risk_config.get("max_daily_loss_pct", 3.0)
        self.max_drawdown_pct = risk_config.get("max_drawdown_pct", 20.0)
        self.max_open_positions = risk_config.get("max_open_positions", 5)
        self.max_exposure_per_asset_pct = risk_config.get(
            "max_exposure_per_asset_pct",
            risk_config.get("max_exposure_per_stock_pct", 20.0),
        )
        self.mandatory_stop_loss = risk_config.get("mandatory_stop_loss", True)
        self.min_rr_ratio = risk_config.get("min_risk_reward_ratio", 1.5)

        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_trades: list[Trade] = []
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._kill_switch_persistent = False

    def validate_signal(
        self,
        signal: Signal,
        current_positions: list[Position],
        buying_power: float,
    ) -> tuple[bool, str, Optional[Order]]:
        """
        Validate a signal against all risk rules.

        Returns:
            (approved, reason, order) — if approved, includes the sized Order
        """
        # Kill switch check
        if self._kill_switch_active:
            return False, f"🛑 KILL SWITCH ACTIVE — {self._kill_switch_reason}", None

        triggered, reason = self._evaluate_account_state()
        if triggered:
            return False, f"🛑 Kill switch: {reason}", None

        # Max positions check
        if len(current_positions) >= self.max_open_positions:
            return False, f"Max positions ({self.max_open_positions}) reached", None

        # Already in this asset?
        for pos in current_positions:
            if pos.symbol == signal.symbol:
                return False, f"Already in position for {signal.symbol}", None

        # Mandatory stop-loss
        if self.mandatory_stop_loss and signal.stop_loss == 0:
            return False, "Signal has no stop-loss (mandatory)", None

        # Calculate position size
        quantity = self._calculate_position_size(
            signal.entry_price, signal.stop_loss, buying_power
        )

        if quantity <= 0:
            return False, "Position size calculation returned 0 (insufficient capital or risk too high)", None

        # Max exposure per asset
        position_value = signal.entry_price * quantity
        max_exposure = self.current_capital * (self.max_exposure_per_asset_pct / 100)
        if position_value > max_exposure:
            quantity = round(max_exposure / signal.entry_price, 4)
            if quantity <= 0:
                return False, f"Exceeds max exposure per asset ({self.max_exposure_per_asset_pct}%)", None

        # Buying power check
        if position_value > buying_power:
            quantity = round(buying_power / signal.entry_price, 4)
            if quantity <= 0:
                return False, "Insufficient buying power", None

        # Risk:Reward check
        if signal.take_profit:
            risk = abs(signal.entry_price - signal.stop_loss)
            reward = abs(signal.take_profit - signal.entry_price)
            if risk > 0 and (reward / risk) < self.min_rr_ratio:
                return (
                    False,
                    f"R:R ratio {reward/risk:.1f} below minimum {self.min_rr_ratio}",
                    None,
                )

        # Build the order
        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            stop_price=signal.stop_loss,
            strategy_name=signal.strategy_name,
            signal=signal,
        )

        position_value = signal.entry_price * quantity
        risk_amount = abs(signal.entry_price - signal.stop_loss) * quantity
        risk_pct = (risk_amount / self.current_capital) * 100

        logger.info(
            f"✅ Risk approved: {signal.side.value} {quantity}x {signal.symbol} "
            f"| Value: ${position_value:.2f} | Risk: ${risk_amount:.2f} ({risk_pct:.1f}%)"
        )

        return True, "Approved", order

    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        buying_power: float,
    ) -> float:
        """
        Calculate position size using fixed-fractional (risk-based) sizing.

        Size = (Capital × Risk%) / (Entry - StopLoss)
        """
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share == 0:
            return 0

        max_risk_amount = self.current_capital * (self.max_risk_per_trade_pct / 100)
        shares = max_risk_amount / risk_per_share

        # Ensure we don't exceed buying power
        max_by_capital = buying_power / entry_price if entry_price > 0 else 0
        shares = min(shares, max_by_capital)

        # Round to 4 decimal places (works for fractional stocks & crypto)
        return round(max(0.0, shares), 4)

    def record_trade(self, trade: Trade):
        """Record a completed trade for daily tracking."""
        self.daily_trades.append(trade)
        self.daily_pnl += trade.pnl
        self.current_capital += trade.pnl
        self.peak_capital = max(self.peak_capital, self.current_capital)

        emoji = "💰" if trade.pnl >= 0 else "💸"
        logger.info(
            f"{emoji} Trade closed: {trade.side.value} {trade.quantity}x {trade.symbol} "
            f"| P&L: ${trade.pnl:.2f} ({trade.pnl_pct:.1f}%) "
            f"| Daily P&L: ${self.daily_pnl:.2f}"
        )
        self._evaluate_account_state()

    def reset_daily(self, current_equity: Optional[float] = None):
        """Reset daily counters at start of new trading day."""
        if current_equity is not None:
            self.current_capital = current_equity
            self.peak_capital = max(self.peak_capital, current_equity)
            self.day_start_capital = current_equity
        else:
            self.day_start_capital = self.current_capital

        self.daily_pnl = 0.0
        self.daily_trades.clear()

        if not self._kill_switch_persistent:
            self._kill_switch_active = False
            self._kill_switch_reason = ""

        logger.info("📊 Daily risk counters reset")

    def activate_kill_switch(self, reason: str = "Manual", persistent: bool = True):
        """Manually activate kill switch."""
        self._kill_switch_active = True
        self._kill_switch_reason = reason
        self._kill_switch_persistent = persistent
        logger.critical(f"🛑 KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self):
        """Deactivate kill switch (use with caution)."""
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._kill_switch_persistent = False
        logger.warning("⚠️ Kill switch deactivated")

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def kill_switch_reason(self) -> str:
        return self._kill_switch_reason

    def sync_equity(self, equity: float) -> tuple[bool, str]:
        """Sync the latest account equity and evaluate drawdown guards."""
        self.current_capital = equity
        self.peak_capital = max(self.peak_capital, equity)
        return self._evaluate_account_state()

    def get_daily_summary(self) -> dict:
        """Get current daily trading summary."""
        winning = [t for t in self.daily_trades if t.pnl > 0]
        losing = [t for t in self.daily_trades if t.pnl < 0]
        drawdown_pct = 0.0
        if self.peak_capital > 0:
            drawdown_pct = ((self.current_capital - self.peak_capital) / self.peak_capital) * 100

        return {
            "total_trades": len(self.daily_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / max(1, len(self.daily_trades)) * 100,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": (self.daily_pnl / self.initial_capital) * 100,
            "current_capital": self.current_capital,
            "kill_switch": self._kill_switch_active,
            "kill_switch_reason": self._kill_switch_reason,
            "largest_win": max((t.pnl for t in winning), default=0),
            "largest_loss": min((t.pnl for t in losing), default=0),
            "peak_capital": self.peak_capital,
            "drawdown_pct": drawdown_pct,
        }

    def _evaluate_account_state(self) -> tuple[bool, str]:
        if self._kill_switch_active:
            return True, self._kill_switch_reason

        max_daily_loss = self.day_start_capital * (self.max_daily_loss_pct / 100)
        daily_loss = max(0.0, self.day_start_capital - self.current_capital)
        if daily_loss >= max_daily_loss and self.current_capital < self.day_start_capital:
            reason = (
                f"daily loss ${daily_loss:.2f} exceeded {self.max_daily_loss_pct:.1f}% "
                f"of start-of-day capital"
            )
            self._kill_switch_active = True
            self._kill_switch_reason = reason
            self._kill_switch_persistent = False
            logger.critical(f"🛑 KILL SWITCH TRIGGERED: {reason}")
            return True, reason

        if self.peak_capital > 0:
            drawdown_pct = ((self.peak_capital - self.current_capital) / self.peak_capital) * 100
            if drawdown_pct >= self.max_drawdown_pct:
                reason = (
                    f"max drawdown {drawdown_pct:.2f}% exceeded {self.max_drawdown_pct:.1f}% "
                    f"from peak capital ${self.peak_capital:.2f}"
                )
                self._kill_switch_active = True
                self._kill_switch_reason = reason
                self._kill_switch_persistent = True
                logger.critical(f"🛑 KILL SWITCH TRIGGERED: {reason}")
                return True, reason

        return False, ""
