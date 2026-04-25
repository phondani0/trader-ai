from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.config import load_settings


class ConfigLoadingTest(unittest.TestCase):
    def test_load_settings_reads_expected_defaults(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        self.assertEqual(settings.venue, "SIM")
        self.assertEqual(settings.bar_type, "EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        self.assertEqual(settings.trade_size, "100000")
        self.assertEqual(settings.fast_ema_period, 10)
        self.assertEqual(settings.slow_ema_period, 20)
        self.assertEqual(settings.bars_count, 10000)


if __name__ == "__main__":
    unittest.main()
