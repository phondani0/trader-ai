from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.config import load_instruments
from nautilus_app.config import load_settings
from nautilus_app.runners.backtest import build_backtest_components
from nautilus_app.data import build_ccxt_cache_path
from nautilus_app.data import build_ccxt_batch_sizes
from nautilus_app.data import load_csv_bars


class DataSourcesTest(unittest.TestCase):
    def test_instrument_catalog_contains_crypto_perp_entry(self):
        instruments = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")

        self.assertIn("btcusdt_perp_binance", instruments)
        self.assertEqual(instruments["btcusdt_perp_binance"].provider, "test")
        self.assertEqual(instruments["btcusdt_perp_binance"].template, "btcusdt_perp_binance")

    def test_csv_loader_reads_fixture_bars(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = build_backtest_components(
            settings,
            instrument_name="btcusdt_perp_binance",
        )

        bars = load_csv_bars(
            csv_path=PROJECT_ROOT / "tests" / "fixtures" / "btcusdt_15m.csv",
            instrument=components.instrument,
            bar_type=components.bar_type,
        )

        self.assertEqual(len(bars), 6)
        self.assertEqual(str(bars[0].bar_type), "BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL")

    def test_ccxt_cache_path_includes_exchange_symbol_and_limit(self):
        instruments = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
        cache_path = build_ccxt_cache_path(
            cache_dir=PROJECT_ROOT / "output",
            instrument_name="btcusdt_perp_binance",
            instrument_spec=instruments["btcusdt_perp_binance"],
            limit=250,
        )

        self.assertTrue(str(cache_path).endswith("btcusdt_perp_binance_binanceusdm_15m_250.csv"))

    def test_ccxt_batch_sizes_paginate_large_history_requests(self):
        self.assertEqual(build_ccxt_batch_sizes(limit=4000, batch_size=1000), [1000, 1000, 1000, 1000])
        self.assertEqual(build_ccxt_batch_sizes(limit=2500, batch_size=1000), [1000, 1000, 500])


if __name__ == "__main__":
    unittest.main()
