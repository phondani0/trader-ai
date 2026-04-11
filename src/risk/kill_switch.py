"""
Kill Switch.

Emergency stop mechanism that can halt all trading activity.
Can be triggered by:
    - Daily loss exceeding threshold
    - Manual activation
    - Connection/API errors
    - Abnormal market conditions
"""

from __future__ import annotations

from datetime import datetime
from loguru import logger

from src.core.broker import AlpacaBroker


class KillSwitch:
    """
    Emergency stop — closes all positions and cancels all orders.
    """

    def __init__(self, broker: AlpacaBroker):
        self.broker = broker
        self._activated = False
        self._activation_time: datetime | None = None
        self._reason: str = ""

    def activate(self, reason: str = "Unknown"):
        """
        EMERGENCY: Cancel all orders and close all positions.
        """
        self._activated = True
        self._activation_time = datetime.utcnow()
        self._reason = reason

        logger.critical(f"🚨🛑 KILL SWITCH ACTIVATED: {reason}")
        logger.critical("Cancelling all open orders...")

        try:
            self.broker.cancel_all_orders()
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")

        logger.critical("Closing all positions...")

        try:
            self.broker.close_all_positions()
        except Exception as e:
            logger.error(f"Failed to close positions: {e}")

        logger.critical(
            f"🛑 Kill switch complete at {self._activation_time}. "
            f"Reason: {reason}. Bot will not place new trades."
        )

    def deactivate(self):
        """Deactivate kill switch (requires explicit action)."""
        self._activated = False
        self._reason = ""
        logger.warning("⚠️ Kill switch deactivated — trading can resume")

    @property
    def is_active(self) -> bool:
        return self._activated

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def activation_time(self) -> datetime | None:
        return self._activation_time
