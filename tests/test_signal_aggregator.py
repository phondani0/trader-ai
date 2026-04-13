from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.core.models import MarketRegime
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

    def generate_signal(self, symbol: str, df: pd.DataFrame, regime: MarketRegime):
        return None


class SignalAggregatorDiagnosticsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
