"""
Market-Hours-Aware Scheduler.

Manages the bot's daily lifecycle — wakes up before market open,
runs strategies during market hours, and shuts down after close.
Handles US market holidays and timezone conversions.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Callable, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.core.broker import AlpacaBroker


ET = pytz.timezone("US/Eastern")


class MarketScheduler:
    """
    Schedules bot tasks around US market hours.

    Lifecycle:
        08:00 ET  → Pre-market prep (fetch data, news)
        09:30 ET  → Market opens → start scanning
        09:30-10:00 ET → ORB window active
        10:00-15:30 ET → Prime trading hours
        15:30 ET  → Stop new entries
        15:50 ET  → Auto square-off all positions
        16:00 ET  → Market close → generate daily report
    """

    def __init__(self, broker: AlpacaBroker, config: dict):
        self.broker = broker
        self.config = config
        self.scheduler = BackgroundScheduler(timezone=ET)

        # Parse market hours from config
        hours = config.get("market_hours", {})
        self.pre_market_start = self._parse_time(hours.get("pre_market_start", "08:00"))
        self.market_open = self._parse_time(hours.get("market_open", "09:30"))
        self.orb_end = self._parse_time(hours.get("orb_window_end", "10:00"))
        self.stop_entries = self._parse_time(hours.get("stop_new_entries", "15:30"))
        self.auto_square_off = self._parse_time(hours.get("auto_square_off", "15:50"))
        self.market_close = self._parse_time(hours.get("market_close", "16:00"))

        # Callbacks
        self._callbacks: dict[str, Callable] = {}

    def register(self, event: str, callback: Callable):
        """
        Register a callback for a lifecycle event.

        Events:
            pre_market, market_open, orb_end, scan_tick,
            stop_entries, square_off, market_close
        """
        self._callbacks[event] = callback
        logger.debug(f"Registered callback for event: {event}")

    def start(self):
        """Start the scheduler with all registered callbacks."""

        # Pre-market preparation
        if "pre_market" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("pre_market"),
                CronTrigger(hour=self.pre_market_start.hour, minute=self.pre_market_start.minute,
                            day_of_week="mon-fri", timezone=ET),
                id="pre_market",
                name="Pre-Market Preparation",
            )

        # Market open
        if "market_open" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("market_open"),
                CronTrigger(hour=self.market_open.hour, minute=self.market_open.minute,
                            day_of_week="mon-fri", timezone=ET),
                id="market_open",
                name="Market Open",
            )

        # Periodic scan during market hours (every 1 minute during 9:30-15:30)
        if "scan_tick" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("scan_tick"),
                CronTrigger(minute="*/1",
                            hour=f"{self.market_open.hour}-{self.stop_entries.hour}",
                            day_of_week="mon-fri", timezone=ET),
                id="scan_tick",
                name="Strategy Scan Tick",
            )

        # ORB window end
        if "orb_end" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("orb_end"),
                CronTrigger(hour=self.orb_end.hour, minute=self.orb_end.minute,
                            day_of_week="mon-fri", timezone=ET),
                id="orb_end",
                name="ORB Window End",
            )

        # Stop new entries
        if "stop_entries" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("stop_entries"),
                CronTrigger(hour=self.stop_entries.hour, minute=self.stop_entries.minute,
                            day_of_week="mon-fri", timezone=ET),
                id="stop_entries",
                name="Stop New Entries",
            )

        # Auto square-off (close all positions)
        if "square_off" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("square_off"),
                CronTrigger(hour=self.auto_square_off.hour, minute=self.auto_square_off.minute,
                            day_of_week="mon-fri", timezone=ET),
                id="square_off",
                name="Auto Square-Off",
            )

        # End-of-day report
        if "market_close" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("market_close"),
                CronTrigger(hour=self.market_close.hour, minute=self.market_close.minute + 5,
                            day_of_week="mon-fri", timezone=ET),
                id="market_close",
                name="Market Close Report",
            )

        self.scheduler.start()
        logger.info("📅 Market scheduler started — waiting for market events")
        self._log_schedule()

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def is_market_hours(self) -> bool:
        """Check if we're currently within trading hours."""
        now_et = datetime.now(ET).time()
        return self.market_open <= now_et <= time(self.stop_entries.hour, self.stop_entries.minute)

    def is_orb_window(self) -> bool:
        """Check if we're in the Opening Range Breakout window."""
        now_et = datetime.now(ET).time()
        return self.market_open <= now_et <= self.orb_end

    def can_open_new_positions(self) -> bool:
        """Check if we can still open new positions."""
        now_et = datetime.now(ET).time()
        return self.market_open <= now_et <= time(self.stop_entries.hour, self.stop_entries.minute)

    def is_square_off_time(self) -> bool:
        """Check if it's time to close all positions."""
        now_et = datetime.now(ET).time()
        return now_et >= time(self.auto_square_off.hour, self.auto_square_off.minute)

    def time_to_close_minutes(self) -> float:
        """Minutes remaining until market close."""
        now_et = datetime.now(ET)
        close_time = now_et.replace(
            hour=self.market_close.hour,
            minute=self.market_close.minute,
            second=0,
        )
        delta = (close_time - now_et).total_seconds() / 60
        return max(0, delta)

    def _safe_run(self, event_name: str) -> Callable:
        """Wrap callback in error handling."""
        def wrapper():
            try:
                # Skip if market is closed (holiday handling via Alpaca clock)
                if event_name != "pre_market" and not self.broker.is_market_open():
                    if event_name == "scan_tick":
                        return  # Silent skip for ticks on holidays
                    logger.info(f"Market closed (holiday?) — skipping {event_name}")
                    return

                logger.info(f"⏰ Event triggered: {event_name}")
                self._callbacks[event_name]()
            except Exception as e:
                logger.error(f"Error in {event_name}: {e}")
        return wrapper

    def _log_schedule(self):
        """Log the scheduled jobs."""
        logger.info("Scheduled jobs:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  📌 {job.name} → {job.trigger}")

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse 'HH:MM' string to time object."""
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))
