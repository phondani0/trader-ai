from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli.benchmark import DEFAULT_DATA_SOURCE
from trader_ai.cli.benchmark import DEFAULT_INSTRUMENT
from trader_ai.cli.benchmark import DEFAULT_STRATEGIES
from trader_ai.cli.benchmark import build_parser


class BenchmarkCliTest(unittest.TestCase):
    def test_benchmark_parser_uses_project_defaults(self):
        parser = build_parser()

        args = parser.parse_args([])

        self.assertEqual(args.instrument, DEFAULT_INSTRUMENT)
        self.assertEqual(args.data_source, DEFAULT_DATA_SOURCE)
        self.assertIsNone(args.strategies)

    def test_benchmark_parser_accepts_strategy_overrides(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "--instrument",
                "ethusdt_perp_binance",
                "--data-source",
                "synthetic",
                "--strategies",
                "demo_ema_cross",
                "volume_spike_trend",
            ]
        )

        self.assertEqual(args.instrument, "ethusdt_perp_binance")
        self.assertEqual(args.data_source, "synthetic")
        self.assertEqual(args.strategies, ["demo_ema_cross", "volume_spike_trend"])
        self.assertEqual(
            DEFAULT_STRATEGIES,
            ["adaptive_trend_rider", "squeeze_breakout", "volume_spike_trend", "nautilus_ema_cross", "demo_ema_cross"],
        )


if __name__ == "__main__":
    unittest.main()
