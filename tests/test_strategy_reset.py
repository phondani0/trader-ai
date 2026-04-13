from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.core.models import MarketRegime, Side
from src.strategies.bearish_breakdown_continuation import (
    BearishBreakdownContinuationStrategy,
)
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.volume_weighted_tsmom import VolumeWeightedTSMomentumStrategy


def _build_index(length: int) -> pd.DatetimeIndex:
    start = datetime.utcnow() - timedelta(hours=length)
    return pd.date_range(start=start, periods=length, freq="h")


def _build_donchian_breakout_frame() -> pd.DataFrame:
    closes = []
    for idx in range(90):
        if idx < 80:
            close = 100 + (idx * 0.12)
        elif idx < 89:
            close = 109.6 + ((idx - 80) * 0.03)
        else:
            close = 114.5
        closes.append(close)

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.35
    df["low"] = df["close"] - 0.35
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_60"] = df["close"].ewm(span=60, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 0.9
    df["adx"] = 28.0
    df["di_plus"] = 26.0
    df["di_minus"] = 14.0
    df["volume_ratio"] = 1.25
    return df


def _build_tsmom_breakout_frame() -> pd.DataFrame:
    closes = []
    for idx in range(95):
        if idx < 70:
            close = 100 + (idx * 0.18)
        elif idx < 88:
            close = 112.4 - ((idx - 70) * 0.12)
        elif idx < 94:
            close = 110.3 + ((idx - 88) * 0.18)
        else:
            close = 116.0
        closes.append(close)

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.4
    df["low"] = df["close"] - 0.4
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_60"] = df["close"].ewm(span=60, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.0
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    df["adx"] = 27.0
    df["di_plus"] = 24.0
    df["di_minus"] = 12.0
    df["volume_ratio"] = 1.3
    return df


def _build_donchian_long_continuation_frame() -> pd.DataFrame:
    closes = []
    for idx in range(120):
        if idx < 60:
            close = 100 + (idx * 1.0)
        elif idx < 90:
            close = 160 - ((idx - 60) * 0.35)
        elif idx < 110:
            close = 149.5 + ((idx - 90) * 0.55)
        else:
            close = 160.5 + ((idx - 110) * 0.15)
        closes.append(close)

    closes[-2] = 161.8
    closes[-1] = 162.6

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.7
    df["low"] = df["close"] - 0.7
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_60"] = df["close"].ewm(span=60, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.4
    df["adx"] = 30.0
    df["di_plus"] = 28.0
    df["di_minus"] = 12.0
    df["volume_ratio"] = 1.2
    return df


def _build_donchian_short_continuation_frame() -> pd.DataFrame:
    closes = []
    for idx in range(120):
        if idx < 60:
            close = 200 - (idx * 1.0)
        elif idx < 90:
            close = 140 + ((idx - 60) * 0.35)
        elif idx < 110:
            close = 150.5 - ((idx - 90) * 0.55)
        else:
            close = 139.5 - ((idx - 110) * 0.15)
        closes.append(close)

    closes[-2] = 138.2
    closes[-1] = 137.4

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.7
    df["low"] = df["close"] - 0.7
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_60"] = df["close"].ewm(span=60, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.4
    df["adx"] = 30.0
    df["di_plus"] = 12.0
    df["di_minus"] = 28.0
    df["volume_ratio"] = 1.2
    return df


class StrategyResetTest(unittest.TestCase):
    def test_bearish_breakdown_continuation_generates_short_signal(self):
        strategy = BearishBreakdownContinuationStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "breakdown_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "slope_lookback": 8,
                "momentum_lookback": 20,
                "rejection_lookback": 20,
                "continuation_touch_atr": 0.8,
                "continuation_trigger_bars": 3,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_donchian_short_continuation_frame(),
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)
        self.assertEqual(
            signal.strategy_name,
            "bearish_breakdown_continuation",
        )

    def test_bearish_breakdown_continuation_allows_one_missing_secondary_confirmation(self):
        strategy = BearishBreakdownContinuationStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "breakdown_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "slope_lookback": 8,
                "momentum_lookback": 20,
                "rejection_lookback": 20,
                "continuation_touch_atr": 0.8,
                "continuation_trigger_bars": 3,
            }
        )
        frame = _build_donchian_short_continuation_frame()
        frame["volume_ratio"] = 0.8

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            frame,
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)

    def test_donchian_ensemble_generates_long_signal(self):
        strategy = DonchianBreakoutStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_LOW_VOL.value],
                "breakout_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "momentum_lookback": 20,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_donchian_breakout_frame(),
            MarketRegime.TRENDING_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)
        self.assertEqual(signal.strategy_name, "donchian_breakout")

    def test_donchian_generates_long_continuation_signal_after_retest(self):
        strategy = DonchianBreakoutStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_LOW_VOL.value],
                "breakout_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "momentum_lookback": 20,
                "continuation_pullback_lookback": 20,
                "continuation_touch_atr": 0.8,
                "continuation_trigger_bars": 3,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_donchian_long_continuation_frame(),
            MarketRegime.TRENDING_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)
        self.assertIn("continuation", signal.reason.lower())

    def test_donchian_generates_short_continuation_signal_after_retest(self):
        strategy = DonchianBreakoutStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "breakout_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "momentum_lookback": 20,
                "continuation_pullback_lookback": 20,
                "continuation_touch_atr": 0.8,
                "continuation_trigger_bars": 3,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_donchian_short_continuation_frame(),
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)
        self.assertIn("continuation", signal.reason.lower())

    def test_donchian_short_path_can_use_asymmetric_ema_filters(self):
        strategy = DonchianBreakoutStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "breakout_periods": [20, 55],
                "min_confirmations": 2,
                "trend_ema_period": 200,
                "confirm_ema_period": 50,
                "short_trend_ema_period": 60,
                "short_confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "momentum_lookback": 20,
                "continuation_pullback_lookback": 20,
                "continuation_touch_atr": 0.8,
                "continuation_trigger_bars": 3,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_donchian_short_continuation_frame(),
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)

    def test_volume_weighted_tsmom_generates_long_signal(self):
        strategy = VolumeWeightedTSMomentumStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_LOW_VOL.value],
                "fast_momentum_bars": 12,
                "slow_momentum_bars": 48,
                "breakout_lookback": 20,
                "pullback_lookback": 10,
                "trend_ema_period": 60,
                "confirm_ema_period": 20,
                "atr_period": 14,
                "adx_period": 14,
                "volume_period": 20,
                "min_normalized_momentum": 8.0,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_tsmom_breakout_frame(),
            MarketRegime.TRENDING_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)
        self.assertEqual(signal.strategy_name, "volume_weighted_tsmom")


if __name__ == "__main__":
    unittest.main()
