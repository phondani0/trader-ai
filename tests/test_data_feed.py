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

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 1000,
        since=None,
    ) -> pd.DataFrame:
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


if __name__ == "__main__":
    unittest.main()
