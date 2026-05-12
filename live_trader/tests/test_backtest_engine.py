from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.core.backtest.engine import BacktestEngine
from src.core.models import MarketBias, MarketRegime, RegimeState, SignalType, Side, StrategyType
from src.intelligence.regime_detector import RegimeDetector
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

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

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


class CompetingStrategy(SingleEntryStrategy):
    def __init__(self, params: dict, *, name: str, confidence: float):
        super().__init__(params, take_profit=105.0, stop_loss=95.0)
        self._name = name
        self.confidence = confidence

    @property
    def name(self) -> str:
        return self._name

    def generate_signal(self, symbol, df, regime):
        signal = super().generate_signal(symbol, df, regime)
        if signal:
            signal.strategy_name = self.name
            signal.confidence = self.confidence
        return signal


class NoSignalStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "no_signal"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.UNKNOWN

    def generate_signal(self, symbol, df, regime):
        return None


class TimeframeTimedEntryStrategy(BaseStrategy):
    def __init__(
        self,
        params: dict,
        *,
        name: str,
        entry_time: datetime,
        side: Side = Side.BUY,
    ):
        super().__init__(params)
        self._name = name
        self.entry_time = entry_time
        self.side = side
        self._emitted = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    def generate_signal(self, symbol, df, regime):
        if self._emitted or df.index[-1] != self.entry_time:
            return None
        self._emitted = True
        price = float(df["close"].iloc[-1])
        if self.side == Side.BUY:
            stop_loss = price - 1.0
            take_profit = price + 1.0
        else:
            stop_loss = price + 1.0
            take_profit = price - 1.0
        return self._create_signal(
            symbol=symbol,
            signal_type=SignalType.BUY if self.side == Side.BUY else SignalType.SELL,
            side=self.side,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=0.7,
            regime=regime,
            reason=f"{self._name} timed entry",
        )


class CountingRegimeDetector(RegimeDetector):
    def __init__(self):
        super().__init__()
        self.profile_window_calls = 0

    def detect(self, df):
        return RegimeState(
            regime=MarketRegime.TRENDING_HIGH_VOL,
            bias=MarketBias.BEARISH,
        )

    def profile_window(self, df):
        self.profile_window_calls += 1
        return {
            "detected_market_profile": "breakout",
            "market_bias": "bearish",
        }

    def get_allowed_strategies(self, regime):
        return ["no_signal"]


class SelectorAllowAllRegimeDetector(RegimeDetector):
    def detect(self, df):
        return RegimeState(
            regime=MarketRegime.TRENDING_LOW_VOL,
            bias=MarketBias.BULLISH,
        )

    def get_allowed_strategies(self, regime):
        return ["range_reversion", "donchian_breakout"]


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

    def test_run_closes_trade_after_max_hold_bars(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=15 * i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1000,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i in range(8)
            ]
        )
        strategy = SingleEntryStrategy(
            {
                "enabled": True,
                "regime": ["all"],
                "timeframe": "15Min",
                "max_hold_bars": 2,
            },
            take_profit=130.0,
            stop_loss=80.0,
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)

        result = engine.run("BTC/USDT:USDT", df, strategy, min_lookback=2)

        self.assertEqual(len(result.trades), 1)
        self.assertIn("Max hold", result.trades[0].reason)

    def test_run_closes_losing_trade_after_stale_exit_bars(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=15 * i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": close,
                    "volume": 1000,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i, close in enumerate([100, 100, 100, 99, 98, 98, 98])
            ]
        )
        strategy = SingleEntryStrategy(
            {
                "enabled": True,
                "regime": ["all"],
                "timeframe": "15Min",
                "stale_exit_bars": 2,
                "stale_exit_min_pnl_pct": 0.0,
            },
            take_profit=130.0,
            stop_loss=80.0,
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)

        result = engine.run("BTC/USDT:USDT", df, strategy, min_lookback=2)

        self.assertEqual(len(result.trades), 1)
        self.assertLess(result.trades[0].pnl_pct, 0)
        self.assertIn("Stale trade", result.trades[0].reason)

    def test_selector_mode_uses_one_best_signal_across_strategies(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1000,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i in range(30)
            ]
            + [
                {
                    "timestamp": base_time + timedelta(minutes=30),
                    "open": 100,
                    "high": 106,
                    "low": 99,
                    "close": 105,
                    "volume": 1000,
                    "adx": 30,
                    "atr_pct": 1,
                }
            ]
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)

        result = engine.run_selector(
            "BTC/USDT:USDT",
            df,
            [
                CompetingStrategy(
                    {"enabled": True, "regime": ["all"]},
                    name="ema_crossover",
                    confidence=0.55,
                ),
                CompetingStrategy(
                    {"enabled": True, "regime": ["all"]},
                    name="supertrend",
                    confidence=0.8,
                ),
            ],
            min_lookback=29,
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].strategy, "supertrend")

    def test_no_trade_backtest_preserves_initial_equity(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1000,
                    "adx": 20,
                    "atr_pct": 1,
                }
                for i in range(40)
            ]
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0, initial_capital=25000.0)

        result = engine.run("BTC/USDT:USDT", df, NoSignalStrategy({"enabled": True, "regime": ["all"]}))

        self.assertEqual(len(result.trades), 0)
        self.assertEqual(result.final_equity, 25000.0)

    def test_selector_caches_market_profile_and_bias_between_refreshes(self):
        base_time = datetime(2024, 1, 1)
        df = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=30 * i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100 + (i % 3),
                    "volume": 1000,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i in range(75)
            ]
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)
        engine.regime_detector = CountingRegimeDetector()

        engine.run_selector(
            "BTC/USDT:USDT",
            df,
            [NoSignalStrategy({"enabled": True, "regime": ["all"]})],
            min_lookback=50,
        )

        self.assertEqual(engine.regime_detector.profile_window_calls, 3)

    def test_selector_supports_mixed_strategy_timeframes(self):
        base_time = datetime(2024, 1, 1)
        df_15m = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(minutes=15 * i),
                    "open": 100 + i,
                    "high": 102 + i,
                    "low": 99 + i,
                    "close": 100 + i,
                    "volume": 1000 + i,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i in range(40)
            ]
        )
        df_1h = build_price_frame(
            [
                {
                    "timestamp": base_time + timedelta(hours=i),
                    "open": 200 + i,
                    "high": 203 + i,
                    "low": 199 + i,
                    "close": 200 + i,
                    "volume": 2000 + i,
                    "adx": 30,
                    "atr_pct": 1,
                }
                for i in range(35)
            ]
        )
        engine = BacktestEngine(fee_pct=0.0, slippage_pct=0.0)
        engine.regime_detector = SelectorAllowAllRegimeDetector()

        result = engine.run_selector(
            "BTC/USDT:USDT",
            {"15Min": df_15m, "1Hour": df_1h},
            [
                TimeframeTimedEntryStrategy(
                    {"enabled": True, "regime": ["all"], "timeframe": "15Min"},
                    name="range_reversion",
                    entry_time=df_15m.index[30],
                ),
                TimeframeTimedEntryStrategy(
                    {"enabled": True, "regime": ["all"], "timeframe": "1Hour"},
                    name="donchian_breakout",
                    entry_time=df_1h.index[31],
                ),
            ],
            min_lookback=30,
        )

        self.assertEqual(len(result.trades), 2)
        self.assertEqual(
            [trade.strategy for trade in result.trades],
            ["range_reversion", "donchian_breakout"],
        )


if __name__ == "__main__":
    unittest.main()
