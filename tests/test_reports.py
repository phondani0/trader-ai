from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.reports import build_leaderboard


class ReportsTest(unittest.TestCase):
    def test_build_leaderboard_sorts_by_return_then_fill_count(self):
        summaries = [
            {"strategy": "a", "instrument_id": "X", "return_pct": 1.5, "fills": 4},
            {"strategy": "b", "instrument_id": "Y", "return_pct": 3.1, "fills": 2},
            {"strategy": "c", "instrument_id": "Z", "return_pct": 3.1, "fills": 7},
        ]

        leaderboard = build_leaderboard(summaries)

        self.assertEqual(leaderboard[0]["strategy"], "c")
        self.assertEqual(leaderboard[1]["strategy"], "b")
        self.assertEqual(leaderboard[2]["strategy"], "a")


if __name__ == "__main__":
    unittest.main()
