from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli.compare_strategies import build_parser


class CompareStrategiesCliTest(unittest.TestCase):
    def test_compare_parser_accepts_strategy_config_flag(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "--instrument",
                "btcusdt_perp_binance",
                "--data-source",
                "ccxt",
                "--strategies",
                "demo_ema_cross",
                "volume_spike_trend",
                "--use-strategy-config",
                "--starting-balance",
                "5000 USDT",
            ]
        )

        self.assertEqual(args.instrument, "btcusdt_perp_binance")
        self.assertEqual(args.data_source, "ccxt")
        self.assertEqual(args.strategies, ["demo_ema_cross", "volume_spike_trend"])
        self.assertTrue(args.use_strategy_config)
        self.assertEqual(args.starting_balance, "5000 USDT")


if __name__ == "__main__":
    unittest.main()
