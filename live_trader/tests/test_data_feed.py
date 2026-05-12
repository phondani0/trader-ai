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


class MultiTimeframeBroker(StubBroker):
    def __init__(self, bars_by_key: dict[tuple[str, str], pd.DataFrame]):
        super().__init__()
        self.bars_by_key = bars_by_key

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 1000,
        since=None,
    ) -> pd.DataFrame:
        frame = self.bars_by_key.get((symbol, timeframe))
        if frame is None:
            return pd.DataFrame()
        if since is not None:
            since_dt = pd.to_datetime(since, unit="ms")
            frame = frame.loc[frame.index >= since_dt]
        if limit and len(frame) > limit:
            frame = frame.iloc[:limit]
        return frame.copy()


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

    def test_refresh_for_strategies_returns_per_timeframe_frames(self):
        symbol = "BTC/USDT:USDT"
        base_time = datetime.utcnow() - timedelta(hours=5)
        rows_15m = []
        for idx in range(8):
            ts = base_time + timedelta(minutes=15 * idx)
            rows_15m.append(
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0 + idx,
                    "volume": 1_000 + idx,
                }
            )
        rows_1h = []
        for idx in range(4):
            ts = base_time + timedelta(hours=idx)
            rows_1h.append(
                {
                    "timestamp": ts,
                    "open": 200.0,
                    "high": 201.0,
                    "low": 199.0,
                    "close": 200.0 + idx,
                    "volume": 2_000 + idx,
                }
            )

        broker = MultiTimeframeBroker(
            {
                (symbol, "15Min"): build_price_frame(rows_15m),
                (symbol, "1Hour"): build_price_frame(rows_1h),
            }
        )
        data_feed = DataFeed(
            broker=broker,
            config={"data": {"candle_interval": "30Min", "lookback_days": 10}},
        )
        strategies = [
            EMACrossoverStrategy(
                {
                    "enabled": True,
                    "regime": ["trending_low_vol"],
                    "timeframe": "1Hour",
                    "fast_ema": 20,
                    "slow_ema": 50,
                    "require_vwap_alignment": False,
                }
            ),
            VWAPReversionStrategy(
                {
                    "enabled": True,
                    "regime": ["sideways_low_vol"],
                    "timeframe": "15Min",
                }
            ),
        ]

        data = data_feed.refresh_for_strategies([symbol], strategies=strategies)

        self.assertIn(symbol, data)
        self.assertEqual(set(data[symbol].keys()), {"15Min", "1Hour"})
        self.assertEqual(len(data[symbol]["15Min"]), 8)
        self.assertEqual(len(data[symbol]["1Hour"]), 4)


if __name__ == "__main__":
    unittest.main()
