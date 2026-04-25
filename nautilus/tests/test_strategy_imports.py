from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.strategies.macd_demo import DemoEMACrossConfig, DemoEMACrossStrategy


class StrategyImportTest(unittest.TestCase):
    def test_demo_strategy_exports_expected_classes(self):
        self.assertEqual(DemoEMACrossConfig.__name__, "DemoEMACrossConfig")
        self.assertEqual(DemoEMACrossStrategy.__name__, "DemoEMACrossStrategy")


if __name__ == "__main__":
    unittest.main()
