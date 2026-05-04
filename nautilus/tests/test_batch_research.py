from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.runners.batch_research import build_research_cases


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


if __name__ == "__main__":
    unittest.main()
