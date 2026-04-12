from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.core.backtest.engine import BacktestEngine
from src.core.models import MarketRegime, SignalType, Side
from src.strategies.base import BaseStrategy
from tests.helpers import build_price_frame


class SingleEntryStrategy(BaseStrategy):
    def __init__(self, params: dict, *, take_profit: float = 105.0, stop_loss: float = 95.0):
        super().__init__(params)
        self._emitted = False
        self.take_profit = take_profit
        self.stop_loss = stop_loss

    @property
    def name(self) -> str:
        return "single_entry"

    def generate_signal(self, symbol, df, regime):
        if self._emitted:
            return None
        self._emitted = True
        return self._create_signal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            side=Side.BUY,
            entry_price=100.0,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            confidence=0.8,
            regime=regime,
            reason="test entry",
        )


class BacktestEngineTest(unittest.TestCase):
    def test_conservative_wick_logic_hits_stop_before_take_profit(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {"timestamp": base_time + timedelta(minutes=i), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000, "adx": 30, "atr_pct": 1}
                for i in range(3)
            ]
            + [
                {"timestamp": base_time + timedelta(minutes=3), "open": 100, "high": 106, "low": 94, "close": 100, "volume": 1000, "adx": 30, "atr_pct": 1}
            ]
        )
        strategy = SingleEntryStrategy({"enabled": True, "regime": ["all"]})
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)

        result = engine.run("BTC/USDT:USDT", df, strategy, min_lookback=2)

        self.assertEqual(len(result.trades), 1)
        self.assertLess(result.trades[0].pnl_pct, 0)

    def test_drawdown_kill_switch_stops_backtest(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {"timestamp": base_time + timedelta(minutes=i), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000, "adx": 30, "atr_pct": 1}
                for i in range(3)
            ]
            + [
                {"timestamp": base_time + timedelta(minutes=3), "open": 100, "high": 101, "low": 89, "close": 90, "volume": 1000, "adx": 30, "atr_pct": 1},
                {"timestamp": base_time + timedelta(minutes=4), "open": 90, "high": 91, "low": 89, "close": 90, "volume": 1000, "adx": 30, "atr_pct": 1},
            ]
        )
        strategy = SingleEntryStrategy({"enabled": True, "regime": ["all"]}, take_profit=120.0, stop_loss=90.0)
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0, max_drawdown_pct=5.0)

        result = engine.run("BTC/USDT:USDT", df, strategy, min_lookback=2)

        self.assertTrue(result.killed_by_drawdown)
        self.assertIn("drawdown", result.kill_switch_reason)


if __name__ == "__main__":
    unittest.main()
