from __future__ import annotations

import unittest

import pandas as pd

from src.intelligence.regime_detector import RegimeDetector


class RegimeDetectorTest(unittest.TestCase):
    def test_profile_window_handles_indicator_columns_with_no_valid_observations(self):
        detector = RegimeDetector()
        df = pd.DataFrame(
            {
                "close": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "adx": [float("nan"), float("nan"), float("nan")],
                "atr_pct": [float("nan"), float("nan"), float("nan")],
            }
        )

        profile = detector.profile_window(df)

        self.assertEqual(profile["bars"], 3)
        self.assertEqual(profile["dominant_regime"], "unknown")
        self.assertEqual(profile["regime_mix_pct"], {})
        self.assertEqual(profile["execution_regime_mix_pct"], {})


if __name__ == "__main__":
    unittest.main()
