from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from src.core.models import Position, Side
from src.main import TradingEngine


class _DataFeedStub:
    @staticmethod
    def primary_frame(data):
        if isinstance(data, pd.DataFrame):
            return data
        for frame in data.values():
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                return frame
        return pd.DataFrame()


class MainNoSignalContextTest(unittest.TestCase):
    def test_log_no_signal_context_accepts_multi_timeframe_symbol_data(self):
        engine = TradingEngine.__new__(TradingEngine)
        engine.data_feed = _DataFeedStub()
        engine.signal_aggregator = Mock()
        engine.signal_aggregator.diagnose_symbol.return_value = {
            "regime": "trending_low_vol",
            "market_profile": "trend",
            "eligible_strategies": ["donchian_breakout"],
            "generated_signals": [],
            "blocked_reasons": {},
        }

        frame = pd.DataFrame(
            [
                {"close": 1.0},
                {"close": 1.1},
            ]
        )
        signal_data = {
            "CORE/USDT:USDT": {
                "5Min": frame,
                "15Min": pd.DataFrame(),
            }
        }
        active_candidates = {
            "CORE/USDT:USDT": {
                "symbol": "CORE/USDT:USDT",
                "score": 9.5,
                "scanner_profile": "breakout",
            }
        }

        engine._log_no_signal_context(active_candidates, signal_data)

        engine.signal_aggregator.diagnose_symbol.assert_called_once_with(
            "CORE/USDT:USDT",
            signal_data["CORE/USDT:USDT"],
        )

    def test_manage_positions_closes_trade_after_max_hold_bars(self):
        engine = TradingEngine.__new__(TradingEngine)
        engine.data_feed = Mock()
        engine.data_feed.get_latest_price.return_value = 100.0
        engine._sync_managed_positions_with_exchange = lambda positions: None
        engine._reconcile_foreign_exchange_state = lambda positions: None
        engine._close_position = Mock()
        engine._managed_positions = {
            "BTC/USDT:USDT": {
                "signal": SimpleNamespace(take_profit=120.0, strategy_name="supertrend"),
                "entry_time": datetime.utcnow() - timedelta(minutes=15 * 7),
                "timeframe_minutes": 15,
                "max_hold_bars": 6,
                "stale_exit_bars": 0,
                "stale_exit_min_pnl_pct": 0.0,
            }
        }

        position = Position(
            symbol="BTC/USDT:USDT",
            side=Side.BUY,
            quantity=1.0,
            entry_price=100.0,
            current_price=100.0,
        )

        engine._manage_positions({}, positions=[position])

        engine._close_position.assert_called_once()
        _, reason = engine._close_position.call_args[0]
        self.assertIn("max hold", reason.lower())

    def test_manage_positions_closes_losing_trade_after_stale_bars(self):
        engine = TradingEngine.__new__(TradingEngine)
        engine.data_feed = Mock()
        engine.data_feed.get_latest_price.return_value = 98.0
        engine._sync_managed_positions_with_exchange = lambda positions: None
        engine._reconcile_foreign_exchange_state = lambda positions: None
        engine._close_position = Mock()
        engine._managed_positions = {
            "BTC/USDT:USDT": {
                "signal": SimpleNamespace(take_profit=120.0, strategy_name="donchian_breakout"),
                "entry_time": datetime.utcnow() - timedelta(minutes=15 * 5),
                "timeframe_minutes": 15,
                "max_hold_bars": 0,
                "stale_exit_bars": 4,
                "stale_exit_min_pnl_pct": 0.0,
            }
        }

        position = Position(
            symbol="BTC/USDT:USDT",
            side=Side.BUY,
            quantity=1.0,
            entry_price=100.0,
            current_price=100.0,
        )

        engine._manage_positions({}, positions=[position])

        engine._close_position.assert_called_once()
        _, reason = engine._close_position.call_args[0]
        self.assertIn("stale", reason.lower())


if __name__ == "__main__":
    unittest.main()
