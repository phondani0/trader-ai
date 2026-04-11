"""
Notification Service.

Sends real-time alerts via Telegram (and extensible to Discord, email, etc.).
"""

from __future__ import annotations

import os
import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger


class Notifier:
    """
    Sends trading notifications via Telegram.

    Alerts on:
        - Trade executions (buy/sell)
        - Kill switch activation
        - Daily P&L summary
        - System errors
    """

    def __init__(self, config: dict):
        self.enabled = config.get("notifications", {}).get("enabled", False)
        self.telegram_enabled = config.get("notifications", {}).get("telegram", False)

        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        self._bot = None

        if self.telegram_enabled and self.bot_token and self.chat_id:
            try:
                from telegram import Bot
                self._bot = Bot(token=self.bot_token)
                logger.info("📱 Telegram notifications enabled")
            except ImportError:
                logger.warning("python-telegram-bot not installed — notifications disabled")
            except Exception as e:
                logger.warning(f"Telegram setup failed: {e}")
        else:
            logger.info("Notifications disabled (no Telegram config)")

    def notify_trade(self, symbol: str, side: str, quantity: int, price: float,
                     strategy: str, stop_loss: float, take_profit: Optional[float] = None):
        """Alert on trade execution."""
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        msg = (
            f"{emoji} *TRADE EXECUTED*\n"
            f"• {side.upper()} {quantity}x *{symbol}*\n"
            f"• Price: ${price:.2f}\n"
            f"• Strategy: {strategy}\n"
            f"• Stop Loss: ${stop_loss:.2f}\n"
        )
        if take_profit:
            msg += f"• Take Profit: ${take_profit:.2f}\n"
        msg += f"• Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"

        self._send(msg)

    def notify_trade_closed(self, symbol: str, side: str, pnl: float, pnl_pct: float, reason: str):
        """Alert on trade closure."""
        emoji = "💰" if pnl >= 0 else "💸"
        msg = (
            f"{emoji} *TRADE CLOSED*\n"
            f"• {symbol} ({side})\n"
            f"• P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"• Reason: {reason}\n"
            f"• Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )
        self._send(msg)

    def notify_kill_switch(self, reason: str):
        """CRITICAL: Kill switch activated."""
        msg = (
            f"🚨🛑 *KILL SWITCH ACTIVATED*\n\n"
            f"• Reason: {reason}\n"
            f"• All orders cancelled\n"
            f"• All positions closed\n"
            f"• No new trades until reset\n"
            f"• Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )
        self._send(msg)

    def notify_daily_summary(self, summary: dict):
        """Send end-of-day P&L report."""
        pnl = summary.get("daily_pnl", 0)
        emoji = "📈" if pnl >= 0 else "📉"
        win_rate = summary.get("win_rate", 0)

        msg = (
            f"{emoji} *DAILY SUMMARY*\n\n"
            f"• Total Trades: {summary.get('total_trades', 0)}\n"
            f"• Winning: {summary.get('winning_trades', 0)} | "
            f"Losing: {summary.get('losing_trades', 0)}\n"
            f"• Win Rate: {win_rate:.0f}%\n"
            f"• Daily P&L: ${pnl:+.2f} ({summary.get('daily_pnl_pct', 0):+.1f}%)\n"
            f"• Capital: ${summary.get('current_capital', 0):,.2f}\n"
            f"• Largest Win: ${summary.get('largest_win', 0):+.2f}\n"
            f"• Largest Loss: ${summary.get('largest_loss', 0):+.2f}\n"
            f"• Date: {datetime.utcnow().strftime('%Y-%m-%d')}"
        )
        self._send(msg)

    def notify_error(self, error: str):
        """Alert on system error."""
        msg = (
            f"⚠️ *SYSTEM ERROR*\n\n"
            f"• {error}\n"
            f"• Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )
        self._send(msg)

    def notify_startup(self, mode: str, capital: float, strategies: list[str]):
        """Alert on bot startup."""
        msg = (
            f"🤖 *TRADER AI STARTED*\n\n"
            f"• Mode: {'📄 PAPER' if mode == 'paper' else '🔴 LIVE'}\n"
            f"• Capital: ${capital:,.2f}\n"
            f"• Strategies: {', '.join(strategies)}\n"
            f"• Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )
        self._send(msg)

    def _send(self, message: str):
        """Send message via all enabled channels."""
        if not self.enabled:
            return

        # Log to console regardless
        logger.info(f"📱 Notification: {message[:100]}...")

        # Send via Telegram
        if self._bot and self.chat_id:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    self._bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode="Markdown",
                    )
                )
                loop.close()
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")
