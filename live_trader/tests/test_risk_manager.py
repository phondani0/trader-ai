from __future__ import annotations

import unittest
from datetime import datetime

from src.core.models import MarketRegime, Signal, SignalType, Side
from src.risk.manager import RiskManager


class RiskManagerTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "risk_management": {
                "max_risk_per_trade_pct": 2.0,
                "max_daily_loss_pct": 3.0,
                "max_drawdown_pct": 20.0,
                "max_open_positions": 5,
                "max_exposure_per_asset_pct": 20.0,
                "mandatory_stop_loss": True,
                "min_risk_reward_ratio": 1.5,
            }
        }
        self.manager = RiskManager(config=self.config, initial_capital=1000.0)

    def test_validate_signal_approves_valid_trade(self):
        signal = Signal(
            timestamp=datetime.utcnow(),
            symbol="BTC/USDT:USDT",
            strategy_name="test_strategy",
            signal_type=SignalType.BUY,
            side=Side.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            confidence=0.8,
            regime=MarketRegime.TRENDING_LOW_VOL,
        )

        approved, _, order = self.manager.validate_signal(
            signal=signal,
            current_positions=[],
            buying_power=500.0,
        )

        self.assertTrue(approved)
        self.assertIsNotNone(order)
        self.assertGreater(order.quantity, 0)

    def test_sync_equity_triggers_daily_loss_kill_switch(self):
        triggered, reason = self.manager.sync_equity(970.0)

        self.assertTrue(triggered)
        self.assertIn("daily loss", reason)
        self.assertTrue(self.manager.is_kill_switch_active)

    def test_sync_equity_triggers_drawdown_kill_switch(self):
        self.manager.max_daily_loss_pct = 30.0
        triggered, reason = self.manager.sync_equity(790.0)

        self.assertTrue(triggered)
        self.assertIn("drawdown", reason)
        self.assertTrue(self.manager.is_kill_switch_active)


if __name__ == "__main__":
    unittest.main()
