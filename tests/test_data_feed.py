from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.core.data_feed import DataFeed
from src.core.models import MarketRegime
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from tests.helpers import StubBroker, build_price_frame


class PaginatedBroker(StubBroker):
    def __init__(self, symbol: str, bars: pd.DataFrame):
        super().__init__()
        self.symbol = symbol
        self.bars = bars
        self.calls: list[int | None] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 1000,
        since=None,
    ) -> pd.DataFrame:
        self.calls.append(since)
        if symbol != self.symbol:
            return pd.DataFrame()

        df = self.bars
        if since is not None:
            start_ts = pd.Timestamp(since, unit="ms")
            df = df[df.index >= start_ts]
        if limit:
            df = df.iloc[:limit]
        return df.copy()


class DataFeedTest(unittest.TestCase):
    def test_four_hour_timeframe_mapping(self):
        data_feed = DataFeed(
            broker=StubBroker(),
            config={"data": {"candle_interval": "4Hour", "lookback_days": 30}},
        )

        self.assertEqual(data_feed.timeframe, "4h")
        self.assertEqual(data_feed._timeframe_delta_ms, 240 * 60 * 1000)

    def test_get_data_paginates_beyond_one_thousand_bars(self):
        symbol = "BTC/USDT:USDT"
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5 * 1499)
        rows = []
        for idx in range(1500):
            ts = start_time + timedelta(minutes=5 * idx)
            close = 100 + (idx * 0.05)
            rows.append(
                {
                    "timestamp": ts,
                    "open": close - 0.2,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000 + idx,
                }
            )
        bars = build_price_frame(rows)
        broker = PaginatedBroker(symbol, bars)
        strategy = EMACrossoverStrategy(
            {
                "enabled": True,
                "regime": ["trending_low_vol"],
                "fast_ema": 20,
                "slow_ema": 50,
                "require_vwap_alignment": True,
            }
        )
        data_feed = DataFeed(
            broker=broker,
            config={
                "data": {
                    "candle_interval": "5Min",
                    "lookback_days": 10,
                    "max_bars_per_request": 1000,
                }
            },
        )

        df = data_feed.get_data(symbol, force_refresh=True, strategies=[strategy])

        self.assertEqual(len(df), 1500)
        self.assertIn("ema_20", df.columns)
        self.assertIn("ema_50", df.columns)
        self.assertIn("adx", df.columns)
        self.assertIn("atr", df.columns)
        self.assertIn("vwap", df.columns)
        self.assertFalse(df.index.duplicated().any())

    def test_strategy_respects_benchmark_approved_regimes(self):
        strategy = VWAPReversionStrategy(
            {
                "enabled": True,
                "regime": ["sideways_low_vol", "sideways_high_vol"],
            }
        )
        strategy.set_benchmark_approved_regimes([MarketRegime.SIDEWAYS_LOW_VOL.value])

        self.assertTrue(strategy.should_run(MarketRegime.SIDEWAYS_LOW_VOL))
        self.assertFalse(strategy.should_run(MarketRegime.SIDEWAYS_HIGH_VOL))

    def test_strategy_respects_benchmark_approved_market_profiles(self):
        strategy = VWAPReversionStrategy(
            {
                "enabled": True,
                "regime": ["sideways_low_vol", "sideways_high_vol"],
            }
        )
        strategy.set_benchmark_approved_market_profiles(["range"])

        self.assertTrue(strategy.allows_market_profile("range"))
        self.assertFalse(strategy.allows_market_profile("breakout"))

    def test_force_refresh_uses_cached_incremental_window(self):
        symbol = "BTC/USDT:USDT"
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5 * 1499)
        rows = []
        for idx in range(1500):
            ts = start_time + timedelta(minutes=5 * idx)
            close = 100 + (idx * 0.05)
            rows.append(
                {
                    "timestamp": ts,
                    "open": close - 0.2,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000 + idx,
                }
            )
        bars = build_price_frame(rows)
        broker = PaginatedBroker(symbol, bars)
        data_feed = DataFeed(
            broker=broker,
            config={
                "data": {
                    "candle_interval": "5Min",
                    "lookback_days": 10,
                    "max_bars_per_request": 1000,
                    "refresh_overlap_bars": 100,
                }
            },
        )

        first = data_feed.get_data(symbol, force_refresh=True, strategies=[])
        initial_call_count = len(broker.calls)

        extra_ts = broker.bars.index[-1] + timedelta(microseconds=1)
        broker.bars = pd.concat(
            [
                broker.bars,
                build_price_frame(
                    [
                        {
                            "timestamp": extra_ts,
                            "open": 200.0,
                            "high": 201.0,
                            "low": 199.5,
                            "close": 200.5,
                            "volume": 9_999,
                        }
                    ]
                ),
            ]
        ).sort_index()

        refreshed = data_feed.get_data(symbol, force_refresh=True, strategies=[])
        refresh_calls = broker.calls[initial_call_count:]

        self.assertEqual(len(first), 1500)
        self.assertGreaterEqual(len(refreshed), 1500)
        self.assertEqual(float(refreshed["close"].iloc[-1]), 200.5)
        self.assertTrue(refresh_calls)
        self.assertIsNotNone(refresh_calls[0])
        self.assertGreater(int(refresh_calls[0]), int(broker.calls[0]))
        self.assertLessEqual(len(refresh_calls), initial_call_count)


if __name__ == "__main__":
    unittest.main()
