from __future__ import annotations

import unittest
from unittest.mock import Mock

import pandas as pd

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


if __name__ == "__main__":
    unittest.main()
