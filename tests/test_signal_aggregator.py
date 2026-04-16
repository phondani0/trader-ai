from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.core.models import MarketBias, MarketRegime, RegimeState, SignalType, Side, StrategyType
from src.intelligence.regime_detector import RegimeDetector
from src.intelligence.signal_aggregator import SignalAggregator
from src.strategies.base import BaseStrategy


def _build_regime_frame() -> pd.DataFrame:
    start = datetime.utcnow() - timedelta(minutes=5 * 39)
    rows = []
    for idx in range(40):
        ts = start + timedelta(minutes=5 * idx)
        close = 100 + (idx * 0.2)
        rows.append(
            {
                "timestamp": ts,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1_000 + idx,
                "adx": 30.0,
                "atr_pct": 0.25 if idx < 39 else 0.12,
            }
        )
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.set_index("timestamp")


class NoEntryStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "supertrend"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    def generate_signal(self, symbol: str, df: pd.DataFrame, regime: MarketRegime):
        return None


class RecordingSignalStrategy(BaseStrategy):
    def __init__(self, params: dict, *, name: str, confidence: float):
        super().__init__(params)
        self._name = name
        self._confidence = confidence
        self.seen_lengths: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    def generate_signal(self, symbol: str, df: pd.DataFrame, regime: MarketRegime):
        self.seen_lengths.append(len(df))
        return self._create_signal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            side=Side.BUY,
            entry_price=float(df["close"].iloc[-1]),
            stop_loss=float(df["close"].iloc[-1]) - 1.0,
            take_profit=float(df["close"].iloc[-1]) + 2.0,
            confidence=self._confidence,
            regime=regime,
            reason=f"{self._name} fired",
        )


class AllowAllTestRegimeDetector(RegimeDetector):
    def detect(self, df):
        return RegimeState(
            regime=MarketRegime.TRENDING_LOW_VOL,
            bias=MarketBias.BULLISH,
        )

    def get_allowed_strategies(self, regime):
        return ["range_reversion", "donchian_breakout"]


class SignalAggregatorDiagnosticsTest(unittest.TestCase):
    def test_regime_matrix_allows_episodic_strategies(self):
        detector = RegimeDetector()

        trending_high = detector.get_allowed_strategies(MarketRegime.TRENDING_HIGH_VOL)
        trending_low = detector.get_allowed_strategies(MarketRegime.TRENDING_LOW_VOL)
        sideways_high = detector.get_allowed_strategies(MarketRegime.SIDEWAYS_HIGH_VOL)
        sideways_low = detector.get_allowed_strategies(MarketRegime.SIDEWAYS_LOW_VOL)

        self.assertIn("episodic_pivot", trending_high)
        self.assertIn("episodic_pivot_short", trending_high)
        self.assertIn("episodic_pivot", trending_low)
        self.assertIn("episodic_pivot_short", trending_low)
        self.assertIn("episodic_pivot", sideways_high)
        self.assertIn("episodic_pivot_short", sideways_high)
        self.assertIn("episodic_pivot", sideways_low)
        self.assertIn("episodic_pivot_short", sideways_low)

    def test_market_profile_window_scales_with_bar_interval(self):
        strategy = NoEntryStrategy({"enabled": True, "regime": ["all"]})
        aggregator = SignalAggregator(
            strategies=[strategy],
            regime_detector=RegimeDetector(),
        )

        start = datetime.utcnow() - timedelta(minutes=30 * 2999)
        rows = []
        for idx in range(3000):
            ts = start + timedelta(minutes=30 * idx)
            rows.append(
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000,
                }
            )
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)

        profile_df = aggregator.market_profile_window(df)

        self.assertEqual(len(profile_df), 2160)

    def test_diagnose_symbol_reports_market_profile_gate(self):
        strategy = NoEntryStrategy(
            {"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]}
        )
        strategy.set_benchmark_approved_market_profiles(["trend"])
        aggregator = SignalAggregator(
            strategies=[strategy],
            regime_detector=RegimeDetector(),
        )

        diagnosis = aggregator.diagnose_symbol(
            "BTC/USDT:USDT",
            _build_regime_frame(),
            market_profile="range",
        )

        self.assertEqual(diagnosis["status"], "fully_gated")
        self.assertEqual(diagnosis["regime"], MarketRegime.TRENDING_LOW_VOL.value)
        self.assertIn("market_profile_mismatch", diagnosis["blocked_reasons"])

    def test_diagnose_symbol_reports_market_bias_gate(self):
        strategy = NoEntryStrategy(
            {"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]}
        )
        strategy.set_benchmark_approved_market_profiles(["trend"])
        strategy.set_benchmark_approved_market_biases(["bullish"])
        aggregator = SignalAggregator(
            strategies=[strategy],
            regime_detector=RegimeDetector(),
        )

        diagnosis = aggregator.diagnose_symbol(
            "BTC/USDT:USDT",
            _build_regime_frame(),
            market_profile="trend",
            market_bias="bearish",
        )

        self.assertEqual(diagnosis["status"], "fully_gated")
        self.assertIn("market_bias_mismatch", diagnosis["blocked_reasons"])

    def test_diagnose_symbol_reports_eligible_no_entry(self):
        strategy = NoEntryStrategy(
            {"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]}
        )
        strategy.set_benchmark_approved_market_profiles(["trend"])
        aggregator = SignalAggregator(
            strategies=[strategy],
            regime_detector=RegimeDetector(),
        )

        diagnosis = aggregator.diagnose_symbol(
            "BTC/USDT:USDT",
            _build_regime_frame(),
            market_profile="trend",
        )

        self.assertEqual(diagnosis["status"], "no_entry")
        self.assertEqual(diagnosis["eligible_strategies"], ["supertrend"])
        self.assertEqual(diagnosis["generated_signals"], [])

    def test_diagnose_symbol_respects_symbol_specific_approvals(self):
        strategy = NoEntryStrategy(
            {"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]}
        )
        strategy.set_symbol_benchmark_approval(
            "BTC/USDT:USDT",
            regimes=["trending_low_vol"],
            market_profiles=["trend"],
            market_biases=["bullish"],
        )
        aggregator = SignalAggregator(
            strategies=[strategy],
            regime_detector=RegimeDetector(),
        )

        diagnosis = aggregator.diagnose_symbol(
            "ETH/USDT:USDT",
            _build_regime_frame(),
            market_profile="trend",
            market_bias="bullish",
        )

        self.assertEqual(diagnosis["status"], "fully_gated")
        self.assertIn("benchmark_regime_mismatch", diagnosis["blocked_reasons"])

    def test_generate_signals_routes_each_strategy_to_its_configured_timeframe(self):
        base_time = datetime.utcnow() - timedelta(hours=40)

        rows_15m = []
        for idx in range(40):
            ts = base_time + timedelta(minutes=15 * idx)
            rows_15m.append(
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0 + idx,
                    "volume": 1_000 + idx,
                    "adx": 30.0,
                    "atr_pct": 0.1,
                }
            )

        rows_1h = []
        for idx in range(35):
            ts = base_time + timedelta(hours=idx)
            rows_1h.append(
                {
                    "timestamp": ts,
                    "open": 200.0,
                    "high": 201.0,
                    "low": 199.0,
                    "close": 200.0 + idx,
                    "volume": 2_000 + idx,
                    "adx": 30.0,
                    "atr_pct": 0.1,
                }
            )

        strategy_15m = RecordingSignalStrategy(
            {"enabled": True, "regime": ["all"], "timeframe": "15Min"},
            name="range_reversion",
            confidence=0.6,
        )
        strategy_1h = RecordingSignalStrategy(
            {"enabled": True, "regime": ["all"], "timeframe": "1Hour"},
            name="donchian_breakout",
            confidence=0.9,
        )

        aggregator = SignalAggregator(
            strategies=[strategy_15m, strategy_1h],
            regime_detector=AllowAllTestRegimeDetector(),
        )

        signals = aggregator.generate_signals(
            {
                "BTC/USDT:USDT": {
                    "15Min": pd.DataFrame(rows_15m).assign(
                        timestamp=lambda df: pd.to_datetime(df["timestamp"])
                    ).set_index("timestamp"),
                    "1Hour": pd.DataFrame(rows_1h).assign(
                        timestamp=lambda df: pd.to_datetime(df["timestamp"])
                    ).set_index("timestamp"),
                }
            },
            log_signals=False,
            market_profiles={"BTC/USDT:USDT": "trend"},
            market_biases={"BTC/USDT:USDT": "bullish"},
        )

        self.assertEqual(strategy_15m.seen_lengths, [40])
        self.assertEqual(strategy_1h.seen_lengths, [35])
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].strategy_name, "donchian_breakout")


if __name__ == "__main__":
    unittest.main()
