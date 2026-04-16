from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd
import yaml

from src.core.models import MarketRegime, Side
from src.strategies.bearish_breakdown_continuation import (
    BearishBreakdownContinuationStrategy,
)
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.episodic_pivot import EpisodicPivotStrategy
from src.strategies.episodic_pivot_short import EpisodicPivotShortStrategy
from src.strategies.range_reversion import RangeReversionStrategy
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


def _build_tsmom_research_breakout_frame() -> pd.DataFrame:
    closes = []
    for idx in range(260):
        if idx < 170:
            close = 100 + (idx * 0.22)
        elif idx < 230:
            close = 137.4 - ((idx - 170) * 0.18)
        elif idx < 258:
            close = 126.6 + ((idx - 230) * 0.32)
        elif idx == 258:
            close = 137.8
        else:
            close = 141.2
        closes.append(close)

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.45
    df["low"] = df["close"] - 0.45
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.15
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    df["adx"] = 27.0
    df["di_plus"] = 25.0
    df["di_minus"] = 12.0
    df["volume_ratio"] = 1.28
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


def _build_range_reversion_long_frame() -> pd.DataFrame:
    closes = [100.0 + ((idx % 6) - 3) * 0.18 for idx in range(100)]
    closes[-3] = 96.4
    closes[-2] = 95.2
    closes[-1] = 96.9

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.45
    df["low"] = df["close"] - 0.45
    df.loc[df.index[-2], "low"] = 94.8
    df["atr"] = 1.1
    df["adx"] = 16.0
    df["vwap"] = 100.0
    df["rsi_14"] = 45.0
    df.loc[df.index[-2], "rsi_14"] = 27.0
    df.loc[df.index[-1], "rsi_14"] = 35.0
    df["bb_lower"] = 96.1
    df["bb_mid"] = 100.0
    df["bb_upper"] = 103.9
    df["bb_bandwidth"] = 0.078
    df["bb_pct"] = 0.35
    return df


def _build_range_reversion_short_frame() -> pd.DataFrame:
    closes = [100.0 + ((idx % 6) - 3) * 0.18 for idx in range(100)]
    closes[-3] = 103.6
    closes[-2] = 104.8
    closes[-1] = 103.1

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.45
    df["low"] = df["close"] - 0.45
    df.loc[df.index[-2], "high"] = 105.2
    df["atr"] = 1.1
    df["adx"] = 16.0
    df["vwap"] = 100.0
    df["rsi_14"] = 55.0
    df.loc[df.index[-2], "rsi_14"] = 73.0
    df.loc[df.index[-1], "rsi_14"] = 64.0
    df["bb_lower"] = 96.1
    df["bb_mid"] = 100.0
    df["bb_upper"] = 103.9
    df["bb_bandwidth"] = 0.078
    df["bb_pct"] = 0.65
    return df


def _build_episodic_pivot_long_frame() -> pd.DataFrame:
    closes = [100.0 + (idx * 0.12) for idx in range(220)]
    closes[-1] = closes[-2] + 1.4

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.25
    df["low"] = df["close"] - 0.25
    df.loc[df.index[-1], "high"] = df["close"].iloc[-1] + 0.45
    df.loc[df.index[-1], "low"] = df["close"].iloc[-1] - 0.20
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.0
    df["adx"] = 26.0
    df["volume_ratio"] = 1.0
    df["volume_burst_ratio"] = 1.0
    df.loc[df.index[-1], "volume_burst_ratio"] = 2.6
    return df


def _build_episodic_pivot_short_frame() -> pd.DataFrame:
    closes = [200.0 - (idx * 0.12) for idx in range(220)]
    closes[-1] = closes[-2] - 1.4

    df = pd.DataFrame(index=_build_index(len(closes)))
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"] + 0.25
    df["low"] = df["close"] - 0.25
    df.loc[df.index[-1], "high"] = df["close"].iloc[-1] + 0.20
    df.loc[df.index[-1], "low"] = df["close"].iloc[-1] - 0.45
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["atr"] = 1.0
    df["adx"] = 28.0
    df["volume_ratio"] = 1.0
    df["volume_burst_ratio"] = 1.0
    df.loc[df.index[-1], "volume_burst_ratio"] = 2.7
    return df


class StrategyResetTest(unittest.TestCase):
    def test_episodic_pivot_long_signal_uses_configured_structure_stop(self):
        df = _build_episodic_pivot_long_frame()
        strategy = EpisodicPivotStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "vol_multiplier": 2.0,
                "stop_buffer_atr": 0.1,
                "tp_rr_ratio": 3.0,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            df,
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)
        expected_stop = float(df.iloc[-1]["low"] - (df.iloc[-1]["atr"] * 0.1))
        self.assertAlmostEqual(signal.stop_loss, expected_stop, places=6)
        self.assertEqual(signal.metadata["adx"], 26.0)

    def test_episodic_pivot_short_signal_uses_configured_structure_stop(self):
        df = _build_episodic_pivot_short_frame()
        strategy = EpisodicPivotShortStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.TRENDING_HIGH_VOL.value],
                "vol_multiplier": 2.0,
                "stop_buffer_atr": 0.1,
                "tp_rr_ratio": 3.0,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            df,
            MarketRegime.TRENDING_HIGH_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)
        expected_stop = float(df.iloc[-1]["high"] + (df.iloc[-1]["atr"] * 0.1))
        self.assertAlmostEqual(signal.stop_loss, expected_stop, places=6)

    def test_volume_weighted_tsmom_config_generates_long_signal(self):
        with open("config/strategies.yaml", "r") as fh:
            config = yaml.safe_load(fh)

        strategy = VolumeWeightedTSMomentumStrategy(config["volume_weighted_tsmom"])

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_tsmom_research_breakout_frame(),
            MarketRegime.TRENDING_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)

    def test_range_reversion_generates_long_signal(self):
        strategy = RangeReversionStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.SIDEWAYS_LOW_VOL.value],
                "bb_period": 20,
                "bb_std_dev": 2.0,
                "rsi_period": 14,
                "adx_period": 14,
                "atr_period": 14,
                "adx_max": 23,
                "rsi_oversold": 38,
                "rsi_overbought": 62,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_range_reversion_long_frame(),
            MarketRegime.SIDEWAYS_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)

    def test_range_reversion_generates_short_signal(self):
        strategy = RangeReversionStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.SIDEWAYS_LOW_VOL.value],
                "bb_period": 20,
                "bb_std_dev": 2.0,
                "rsi_period": 14,
                "adx_period": 14,
                "atr_period": 14,
                "adx_max": 23,
                "rsi_oversold": 38,
                "rsi_overbought": 62,
            }
        )

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            _build_range_reversion_short_frame(),
            MarketRegime.SIDEWAYS_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SELL)

    def test_range_reversion_accepts_same_bar_touch_reclaim(self):
        strategy = RangeReversionStrategy(
            {
                "enabled": True,
                "regime": [MarketRegime.SIDEWAYS_LOW_VOL.value],
                "bb_period": 20,
                "bb_std_dev": 2.0,
                "rsi_period": 14,
                "adx_period": 14,
                "atr_period": 14,
                "adx_max": 23,
                "rsi_oversold": 38,
                "rsi_overbought": 62,
            }
        )

        df = _build_range_reversion_long_frame()
        # Previous bar no longer touches lower band; current bar still sweeps and reclaims.
        df.loc[df.index[-2], "low"] = 96.7
        df.loc[df.index[-1], "low"] = 94.9

        signal = strategy.generate_signal(
            "BTC/USDT:USDT",
            df,
            MarketRegime.SIDEWAYS_LOW_VOL,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.BUY)

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
