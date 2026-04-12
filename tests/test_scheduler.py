from __future__ import annotations

import unittest

from src.core.scheduler import MarketScheduler
from tests.helpers import StubBroker


class SchedulerTest(unittest.TestCase):
    def test_24_7_scheduler_registers_daily_reset_job(self):
        broker = StubBroker()
        scheduler = MarketScheduler(
            broker=broker,
            config={"discovery": {"enabled": True, "interval_minutes": 10}},
        )
        scheduler.register("pre_market", lambda: None)
        scheduler.register("scan_tick", lambda: None)
        scheduler.register("market_close", lambda: None)
        scheduler.register("discovery_scan", lambda: None)
        scheduler.register("journal_review", lambda: None)

        scheduler.start()
        try:
            job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
            self.assertIn("pre_market", job_ids)
            self.assertIn("scan_tick", job_ids)
            self.assertIn("market_close", job_ids)
            self.assertIn("discovery_scan", job_ids)
            self.assertIn("journal_review", job_ids)
        finally:
            scheduler.stop()


if __name__ == "__main__":
    unittest.main()
