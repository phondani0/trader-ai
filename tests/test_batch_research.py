from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners.batch_research import build_research_cases


class BatchResearchTest(unittest.TestCase):
    def test_build_research_cases_creates_cartesian_product(self):
        cases = build_research_cases(
            instruments=["synthetic_fx", "btcusdt_perp_binance"],
            strategies=["demo_ema_cross", "nautilus_ema_cross"],
            data_source="synthetic",
        )

        self.assertEqual(len(cases), 4)
        self.assertEqual(cases[0].instrument_name, "synthetic_fx")
        self.assertEqual(cases[-1].strategy_name, "nautilus_ema_cross")

    def test_build_research_cases_marks_strategy_config_usage(self):
        cases = build_research_cases(
            instruments=["btcusdt_perp_binance"],
            strategies=["volume_spike_trend"],
            data_source="ccxt",
            use_strategy_config=True,
            starting_balance="5000 USDT",
        )

        self.assertTrue(cases[0].use_strategy_config)
        self.assertEqual(cases[0].starting_balance, "5000 USDT")


if __name__ == "__main__":
    unittest.main()
