from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners import live  # noqa: E402


class LiveRunnerTimerCallbackTest(unittest.TestCase):
    def test_discovery_timer_callback_accepts_nautilus_time_event(self):
        scanner = Mock()

        with patch.object(live, "_scanner", scanner):
            live._trigger_discovery_scan(object())

        scanner.scan_for_momentum.assert_called_once_with()

    def test_journal_timer_callback_accepts_nautilus_time_event(self):
        journal = Mock()

        with patch.object(live, "_journal", journal):
            live._trigger_journal_review(object())

        journal.update_performance.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
