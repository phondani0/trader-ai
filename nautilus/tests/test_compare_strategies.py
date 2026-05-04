from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.runners.compare_strategies import build_compare_payload


class CompareStrategiesTest(unittest.TestCase):
    def test_build_compare_payload_keeps_single_instrument_and_many_strategies(self):
        payload = build_compare_payload(
            instrument_name="btcusdt_perp_binance",
            strategies=["nautilus_ema_cross", "volume_spike_trend"],
            data_source="ccxt",
        )

        self.assertEqual(payload["instrument_name"], "btcusdt_perp_binance")
        self.assertEqual(payload["strategies"], ["nautilus_ema_cross", "volume_spike_trend"])
        self.assertEqual(payload["data_source"], "ccxt")


if __name__ == "__main__":
    unittest.main()
