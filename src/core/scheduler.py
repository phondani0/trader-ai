"""
Market-Hours-Aware Scheduler.

Manages the bot's daily lifecycle based on broker capabilities:
    - 24/7 markets (crypto): continuous scanning
    - Session-based markets (equities): NYSE/NASDAQ hours

Uses broker.is_24_7_market to decide — never checks asset_class directly.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.core.base_broker import BaseBroker
from src.core.timeframes import timeframe_to_minutes

ET = pytz.timezone("US/Eastern")

class MarketScheduler:
    """
    Schedules bot tasks around market hours.

    Automatically adapts based on broker capabilities:
        - 24/7 broker → config-driven scan loop aligned to candle closes
        - Session broker → NYSE market hours lifecycle
    """

    def __init__(self, broker: BaseBroker, config: dict, strategies: list | None = None):
        self.broker = broker
        self.config = config
        self.scheduler = BackgroundScheduler(timezone=ET)
        data_cfg = config.get("data", {})
        candle_interval = data_cfg.get("candle_interval", "5Min")
        enabled_strategies = [strategy for strategy in (strategies or []) if strategy.enabled]
        if enabled_strategies:
            self.scan_interval_minutes = min(
                timeframe_to_minutes(getattr(strategy, "timeframe", None) or candle_interval)
                for strategy in enabled_strategies
            )
        else:
            self.scan_interval_minutes = int(
                data_cfg.get(
                    "scan_interval_minutes",
                    timeframe_to_minutes(candle_interval),
                )
            )
        self.scan_buffer_minutes = int(data_cfg.get("scan_buffer_minutes", 1))
        self.position_check_interval_minutes = int(
            data_cfg.get("position_check_interval_minutes", 15)
        )

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
            pre_market, market_open, orb_end, scan_tick, position_check, discovery_scan,
            journal_review, stop_entries, square_off, market_close
        """
        self._callbacks[event] = callback
        logger.debug(f"Registered callback for event: {event}")

    def start(self):
        """Start the scheduler with all registered callbacks."""

        if self.broker.is_24_7_market:
            if "pre_market" in self._callbacks:
                self.scheduler.add_job(
                    self._safe_run("pre_market"),
                    CronTrigger(hour=0, minute=10, timezone=pytz.UTC),
                    id="pre_market",
                    name="Daily Reset (24/7)",
                )
            # 24/7 scan cadence derived from candle timeframe/config
            if "scan_tick" in self._callbacks:
                self.scheduler.add_job(
                    self._safe_run("scan_tick"),
                    self._build_24_7_scan_trigger(),
                    id="scan_tick",
                    name="Strategy Scan Tick (24/7)",
                )
            # Daily report at midnight UTC
            if "market_close" in self._callbacks:
                self.scheduler.add_job(
                    self._safe_run("market_close"),
                    CronTrigger(hour=0, minute=5, timezone=pytz.UTC),
                    id="market_close",
                    name="Daily Report (Midnight UTC)",
                )
            if "position_check" in self._callbacks:
                self.scheduler.add_job(
                    self._safe_run("position_check"),
                    IntervalTrigger(
                        minutes=self.position_check_interval_minutes,
                        timezone=pytz.UTC,
                    ),
                    id="position_check",
                    name="Position Monitor",
                )
            self.scheduler.start()

            # Journal Review (Performance Follow-up) - Global
            if "journal_review" in self._callbacks:
                self.scheduler.add_job(
                    self._safe_run("journal_review"),
                    CronTrigger(minute="*/30", timezone=pytz.UTC),
                    id="journal_review",
                    name="Discovery Journal Review",
                )

            # Discovery Scan
            discovery_cfg = self.config.get("discovery", {})
            if (
                discovery_cfg.get("enabled", True)
                and "discovery_scan" in self._callbacks
            ):
                interval = discovery_cfg.get("interval_minutes", 10)
                self.scheduler.add_job(
                    self._safe_run("discovery_scan"),
                    self._build_24_7_interval_trigger(
                        interval_minutes=int(interval),
                        buffer_minute=self.scan_buffer_minutes,
                    ),
                    id="discovery_scan",
                    name="Market Discovery Scanner",
                )
            logger.info(
                f"📅 Scheduler started — 24/7 market | scan every {self.scan_interval_minutes}m"
            )
            self._log_schedule()
            return

        # Session-based market hours
        if "pre_market" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("pre_market"),
                CronTrigger(
                    hour=self.pre_market_start.hour,
                    minute=self.pre_market_start.minute,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="pre_market",
                name="Pre-Market Preparation",
            )

        if "market_open" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("market_open"),
                CronTrigger(
                    hour=self.market_open.hour,
                    minute=self.market_open.minute,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="market_open",
                name="Market Open",
            )

        if "scan_tick" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("scan_tick"),
                CronTrigger(
                    minute="*/1",
                    hour=f"{self.market_open.hour}-{self.stop_entries.hour}",
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="scan_tick",
                name="Strategy Scan Tick",
            )

        if "orb_end" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("orb_end"),
                CronTrigger(
                    hour=self.orb_end.hour,
                    minute=self.orb_end.minute,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="orb_end",
                name="ORB Window End",
            )

        if "stop_entries" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("stop_entries"),
                CronTrigger(
                    hour=self.stop_entries.hour,
                    minute=self.stop_entries.minute,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="stop_entries",
                name="Stop New Entries",
            )

        if "square_off" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("square_off"),
                CronTrigger(
                    hour=self.auto_square_off.hour,
                    minute=self.auto_square_off.minute,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="square_off",
                name="Auto Square-Off",
            )

        if "market_close" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("market_close"),
                CronTrigger(
                    hour=self.market_close.hour,
                    minute=self.market_close.minute + 5,
                    day_of_week="mon-fri",
                    timezone=ET,
                ),
                id="market_close",
                name="Market Close Report",
            )

        self.scheduler.start()
        logger.info("📅 Scheduler started — session-based market hours")

        # Discovery Scan for Session Markets
        discovery_cfg = self.config.get("discovery", {})
        if discovery_cfg.get("enabled", True) and "discovery_scan" in self._callbacks:
            interval = discovery_cfg.get("interval_minutes", 10)
            if int(interval) < 60 and 60 % int(interval) == 0:
                self.scheduler.add_job(
                    self._safe_run("discovery_scan"),
                    CronTrigger(
                        minute=f"*/{interval}",
                        hour=f"{self.market_open.hour}-{self.stop_entries.hour}",
                        day_of_week="mon-fri",
                        timezone=ET,
                    ),
                    id="discovery_scan",
                    name="Market Discovery Scanner",
                )
            else:
                self.scheduler.add_job(
                    self._safe_run("discovery_scan"),
                    IntervalTrigger(minutes=int(interval), timezone=ET),
                    id="discovery_scan",
                    name="Market Discovery Scanner",
                )
        self._log_schedule()

        # Journal Review (Performance Follow-up) - Global
        if "journal_review" in self._callbacks:
            self.scheduler.add_job(
                self._safe_run("journal_review"),
                CronTrigger(minute="*/30", timezone=ET),
                id="journal_review",
                name="Discovery Journal Review",
            )

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def is_market_hours(self) -> bool:
        """Check if we're currently within trading hours."""
        if self.broker.is_24_7_market:
            return True
        now_et = datetime.now(ET).time()
        return (
            self.market_open
            <= now_et
            <= time(self.stop_entries.hour, self.stop_entries.minute)
        )

    def is_orb_window(self) -> bool:
        """Check if we're in the Opening Range Breakout window."""
        if self.broker.is_24_7_market:
            return False
        now_et = datetime.now(ET).time()
        return self.market_open <= now_et <= self.orb_end

    def can_open_new_positions(self) -> bool:
        """Check if we can still open new positions."""
        if self.broker.is_24_7_market:
            return True
        now_et = datetime.now(ET).time()
        return (
            self.market_open
            <= now_et
            <= time(self.stop_entries.hour, self.stop_entries.minute)
        )

    def is_square_off_time(self) -> bool:
        """Check if it's time to close all positions."""
        if self.broker.is_24_7_market:
            return False
        now_et = datetime.now(ET).time()
        return now_et >= time(self.auto_square_off.hour, self.auto_square_off.minute)

    def time_to_close_minutes(self) -> float:
        """Minutes remaining until market close."""
        if self.broker.is_24_7_market:
            now_utc = datetime.now(pytz.UTC)
            next_midnight = (now_utc + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return (next_midnight - now_utc).total_seconds() / 60

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
                if event_name != "pre_market" and not self.broker.is_market_open():
                    if event_name == "scan_tick":
                        return
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

    def _build_24_7_scan_trigger(self):
        return self._build_24_7_interval_trigger(
            interval_minutes=self.scan_interval_minutes,
            buffer_minute=self.scan_buffer_minutes,
        )

    @staticmethod
    def _build_24_7_interval_trigger(interval_minutes: int, buffer_minute: int = 0):
        interval = max(1, int(interval_minutes))
        buffer_minute = max(0, min(int(buffer_minute), 59))

        if interval < 60 and 60 % interval == 0:
            if interval == 1:
                return CronTrigger(minute="*", timezone=pytz.UTC)
            start_minute = buffer_minute % interval
            return CronTrigger(
                minute=f"{start_minute}-59/{interval}",
                timezone=pytz.UTC,
            )

        if interval < 1440 and interval % 60 == 0 and 1440 % interval == 0:
            hour_step = interval // 60
            hour_expr = "*" if hour_step == 1 else f"0-23/{hour_step}"
            return CronTrigger(
                hour=hour_expr,
                minute=buffer_minute,
                timezone=pytz.UTC,
            )

        if interval == 1440:
            return CronTrigger(hour=0, minute=buffer_minute, timezone=pytz.UTC)

        return IntervalTrigger(minutes=interval, timezone=pytz.UTC)

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse 'HH:MM' string to time object."""
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))
