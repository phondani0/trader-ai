from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.runners.parameter_sweep import build_parameter_grid


class ParameterSweepTest(unittest.TestCase):
    def test_build_parameter_grid_keeps_only_fast_below_slow(self):
        grid = build_parameter_grid(
            fast_periods=[5, 8, 21],
            slow_periods=[8, 21, 34],
        )

        self.assertEqual(grid[0], {"fast_ema_period": 5, "slow_ema_period": 8})
        self.assertNotIn({"fast_ema_period": 21, "slow_ema_period": 8}, grid)
        self.assertEqual(len(grid), 6)


if __name__ == "__main__":
    unittest.main()
