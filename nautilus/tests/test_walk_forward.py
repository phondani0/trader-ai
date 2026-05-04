from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.runners.walk_forward import build_walk_forward_windows


class WalkForwardTest(unittest.TestCase):
    def test_build_walk_forward_windows_slides_by_step_size(self):
        timestamps = pd.date_range("2024-01-01", periods=12, freq="15min", tz="UTC")

        windows = build_walk_forward_windows(
            timestamps=timestamps,
            train_bars=6,
            test_bars=3,
            step_bars=3,
        )

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["train_start"], timestamps[0].isoformat())
        self.assertEqual(windows[0]["test_end"], timestamps[8].isoformat())
        self.assertEqual(windows[1]["train_start"], timestamps[3].isoformat())


if __name__ == "__main__":
    unittest.main()
