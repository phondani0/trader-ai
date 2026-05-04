from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli.backtest import build_parser


class BacktestCliTest(unittest.TestCase):
    def test_cli_parser_supports_strategy_data_and_output_overrides(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "--settings",
                "config/settings.yaml",
                "--output-dir",
                "output/custom",
                "--strategy",
                "nautilus_ema_cross",
                "--data-source",
                "csv",
                "--bars-csv",
                "data/bars.csv",
                "--instrument",
                "btcusdt_perp_binance",
                "--cache-dir",
                "data/cache",
                "--fast-ema-period",
                "8",
                "--slow-ema-period",
                "21",
            ]
        )

        self.assertEqual(args.settings, "config/settings.yaml")
        self.assertEqual(args.output_dir, "output/custom")
        self.assertEqual(args.strategy, "nautilus_ema_cross")
        self.assertEqual(args.data_source, "csv")
        self.assertEqual(args.bars_csv, "data/bars.csv")
        self.assertEqual(args.instrument, "btcusdt_perp_binance")
        self.assertEqual(args.cache_dir, "data/cache")
        self.assertEqual(args.fast_ema_period, 8)
        self.assertEqual(args.slow_ema_period, 21)


if __name__ == "__main__":
    unittest.main()
