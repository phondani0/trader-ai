"""
Trader AI — Autonomous Intraday Trading Bot
============================================

Main entry point. This is the "always-on worker" that:
    1. Wakes up before market open
    2. Fetches data and detects market regime
    3. Runs strategies and generates signals
    4. Validates signals through risk management
    5. Executes trades via the configured broker
    6. Monitors positions and manages exits
    7. Squares off at end of day
    8. Generates daily report

Usage:
    python -m src.main                    # Run the bot
    python -m src.main --mode paper       # Paper trading (default)
    python -m src.main --mode live        # LIVE trading (careful!)
    python -m src.main --once             # Run one scan cycle and exit
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from src.core.broker_factory import BrokerFactory
from src.core.database import DatabaseManager
from src.core.data_feed import DataFeed
from src.core.models import (
    OrderStatus,
    Side,
    Trade,
    TradingMode,
)
from src.core.scheduler import MarketScheduler
from src.intelligence.journal import DiscoveryJournal
from src.intelligence.regime_detector import RegimeDetector
from src.intelligence.scanner import MarketScanner
from src.intelligence.signal_aggregator import SignalAggregator
from src.notifications.notifier import Notifier
from src.risk.kill_switch import KillSwitch
from src.risk.manager import RiskManager
from src.strategies.chart_patterns import ChartPatternStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy

# ─── Configuration ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def load_config() -> dict:
    """Load all YAML config files."""
    settings = {}
    for config_file in ["settings.yaml", "strategies.yaml", "watchlist.yaml"]:
        path = CONFIG_DIR / config_file
        if path.exists():
            with open(path) as f:
                settings.update(yaml.safe_load(f) or {})
    return settings


def setup_logging(config: dict):
    """Configure loguru logging."""
    log_config = config.get("logging", {})
    log_file = PROJECT_ROOT / log_config.get("file", "logs/trader.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()  # Remove default handler

    # Console handler
    if log_config.get("console", True):
        logger.add(
            sys.stderr,
            level=log_config.get("level", "INFO"),
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> — <level>{message}</level>",
            colorize=True,
        )

    # File handler
    logger.add(
        str(log_file),
        level="DEBUG",
        rotation=f"{log_config.get('max_size_mb', 50)} MB",
        retention=log_config.get("backup_count", 5),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} — {message}",
    )


# ─── Trading Engine ──────────────────────────────────────────────────


class TradingEngine:
    """
    The autonomous trading engine that orchestrates everything.
    """

    def __init__(self, config: dict, mode: TradingMode = TradingMode.PAPER):
        self.config = config
        self.mode = mode
        self._running = False
        self._orb_set = False

        logger.info("═" * 60)
        logger.info("🤖 TRADER AI — Autonomous Intraday Trading Bot")
        logger.info(
            f"   Mode: {'📄 PAPER' if mode == TradingMode.PAPER else '🔴 LIVE TRADING'}"
        )
        logger.info("═" * 60)

        if mode == TradingMode.LIVE:
            logger.warning("⚠️  LIVE TRADING MODE — Real money is at risk!")

        # ─── Initialize Components ────────────────────────────────
        self.broker = BrokerFactory.create(config, mode)
        self.data_feed = DataFeed(broker=self.broker, config=config)
        self.regime_detector = RegimeDetector(config=config)
        self.kill_switch = KillSwitch(broker=self.broker)

        # Database & Journal (L1 Feedback Loop)
        self.db = DatabaseManager(
            db_path=config.get("data", {}).get("database_path", "data/trader.db")
        )
        self.journal = DiscoveryJournal(broker=self.broker, db=self.db)

        # Load watchlist
        self.watchlist = config.get(
            "active_watchlist",
            [
                "AAPL",
                "MSFT",
                "NVDA",
                "TSLA",
                "AMD",
                "META",
                "SPY",
                "QQQ",
                "AMZN",
                "GOOGL",
            ],
        )

        # Initialize capital
        account = self.broker.get_account()
        capital = float(
            account.get("equity", config.get("trading", {}).get("capital", 25000))
        )

        # Risk Manager
        self.risk_manager = RiskManager(config=config, initial_capital=capital)

        # Notifications
        self.notifier = Notifier(config=config)

        # Discovery Scanner
        self.market_scanner = MarketScanner(
            broker=self.broker, config=config, journal=self.journal
        )

        # Initialize Strategies
        strategy_configs = config
        self.strategies = self._init_strategies(strategy_configs)

        # Signal Aggregator
        self.signal_aggregator = SignalAggregator(
            strategies=self.strategies,
            regime_detector=self.regime_detector,
        )

        # Scheduler
        self.scheduler = MarketScheduler(broker=self.broker, config=config)

        # Track active positions managed by us
        self._managed_positions: dict[
            str, dict
        ] = {}  # symbol → {signal, order_id, ...}

        logger.info(f"Capital: ${capital:,.2f}")
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
        logger.info(
            f"Strategies: {', '.join(s.name for s in self.strategies if s.enabled)}"
        )

    def _init_strategies(self, config: dict) -> list:
        """Initialize all strategy instances from config."""
        strategies = []

        strategy_map = {
            "macd_bollinger": MACDBollingerStrategy,
            "vwap_reversion": VWAPReversionStrategy,
            "supertrend": SupertrendStrategy,
            "orb": ORBStrategy,
            "rsi_divergence": RSIDivergenceStrategy,
            "ema_crossover": EMACrossoverStrategy,
            "chart_patterns": ChartPatternStrategy,
        }

        for name, cls in strategy_map.items():
            params = config.get(name, {"enabled": True, "regime": ["all"]})
            strategy = cls(params=params)
            strategies.append(strategy)
            status = "✅" if strategy.enabled else "⬜"
            logger.info(f"  {status} Strategy: {name}")

        return strategies

    # ─── Lifecycle Events ─────────────────────────────────────────

    def on_pre_market(self):
        """Run before market opens — prepare data and reset daily counters."""
        logger.info("═" * 40)
        logger.info("🌅 Pre-Market Preparation")
        logger.info("═" * 40)

        # Reset daily state
        self.risk_manager.reset_daily()
        self._orb_set = False
        self._managed_positions.clear()

        # Reset ORB strategy
        for s in self.strategies:
            if isinstance(s, ORBStrategy):
                s.reset_daily()

        # Pre-fetch data
        logger.info(f"Fetching data for {len(self.watchlist)} symbols...")
        self.data_feed.refresh_all(self.watchlist)

        # Account status
        account = self.broker.get_account()
        logger.info(
            f"Account equity: ${account['equity']:,.2f} | Buying power: ${account['buying_power']:,.2f}"
        )

        # Notify
        self.notifier.notify_startup(
            mode=self.mode.value,
            capital=account["equity"],
            strategies=[s.name for s in self.strategies if s.enabled],
        )

    def on_market_open(self):
        """Market just opened — start scanning."""
        logger.info("🔔 Market OPEN — scanning begins")

    def on_orb_end(self):
        """ORB window (first 30 min) has closed — set ORB levels."""
        logger.info("⏰ ORB window closed — setting Opening Range levels")

        data = self.data_feed.refresh_all(self.watchlist)

        for s in self.strategies:
            if isinstance(s, ORBStrategy):
                for symbol in self.watchlist:
                    if symbol in data and not data[symbol].empty:
                        orb_minutes = s.params.get("orb_minutes", 30)
                        s.set_orb_range(symbol, data[symbol], orb_minutes)

                        if symbol in s._orb_set and s._orb_set[symbol]:
                            logger.info(
                                f"  ORB {symbol}: High={s._orb_high[symbol]:.2f} "
                                f"Low={s._orb_low[symbol]:.2f}"
                            )

        self._orb_set = True

    def on_scan_tick(self):
        """
        Main scan loop — runs every minute during market hours.
        This is where the magic happens.
        """
        if self.kill_switch.is_active:
            return

        if self.risk_manager.is_kill_switch_active:
            return

        try:
            # 1. Refresh data
            data = self.data_feed.refresh_all(self.watchlist)

            if not data:
                return

            # 2. Check existing positions for exit conditions
            self._manage_positions(data)

            # 3. Check if we can open new positions
            if not self.scheduler.can_open_new_positions():
                return

            # 4. Generate signals from all strategies
            signals = self.signal_aggregator.generate_signals(data)

            if not signals:
                return

            # 5. Validate and execute signals through risk manager
            positions = self.broker.get_positions()
            buying_power = self.broker.get_buying_power()

            for signal in signals:
                approved, reason, order = self.risk_manager.validate_signal(
                    signal=signal,
                    current_positions=positions,
                    buying_power=buying_power,
                )

                if approved and order:
                    self._execute_order(order)
                    # Update positions list so we don't exceed limits
                    positions = self.broker.get_positions()
                    buying_power = self.broker.get_buying_power()
                else:
                    logger.debug(f"Signal rejected for {signal.symbol}: {reason}")

        except Exception as e:
            logger.error(f"Error in scan tick: {e}")
            self.notifier.notify_error(f"Scan tick error: {str(e)}")

    def _execute_order(self, order):
        """Execute a validated order through the broker."""
        try:
            result = self.broker.place_order(order)

            if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                # Track the position
                self._managed_positions[order.symbol] = {
                    "order_id": result.id,
                    "signal": order.signal,
                    "entry_time": datetime.utcnow(),
                    "side": order.side,
                }

                # Send notification
                self.notifier.notify_trade(
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=order.quantity,
                    price=result.filled_price or order.signal.entry_price,
                    strategy=order.strategy_name,
                    stop_loss=order.signal.stop_loss,
                    take_profit=order.signal.take_profit,
                )

                # Place stop-loss order
                self._place_stop_loss(order)

        except Exception as e:
            logger.error(f"Order execution failed for {order.symbol}: {e}")
            self.notifier.notify_error(f"Order failed: {order.symbol} — {str(e)}")

    def _place_stop_loss(self, order):
        """Place a paired stop-loss order for protection."""
        if not order.signal or order.signal.stop_loss == 0:
            return

        try:
            sl_side = Side.SELL if order.side == Side.BUY else Side.BUY

            sl_order = self.broker.create_stop_loss_order(
                symbol=order.symbol,
                side=sl_side,
                quantity=order.quantity,
                stop_price=order.signal.stop_loss,
                strategy_name=f"{order.strategy_name}_stoploss",
            )

            self.broker.place_order(sl_order)
            logger.info(
                f"🛡️ Stop-loss placed for {order.symbol} at ${order.signal.stop_loss:.2f}"
            )

        except Exception as e:
            logger.error(f"Failed to place stop-loss for {order.symbol}: {e}")

    def _manage_positions(self, data: dict):
        """
        Check existing positions for:
            - Take-profit targets
            - Trailing stops
            - Time-based exits
        """
        positions = self.broker.get_positions()

        for pos in positions:
            if pos.symbol not in self._managed_positions:
                continue

            managed = self._managed_positions[pos.symbol]
            signal = managed.get("signal")

            if not signal:
                continue

            # Get latest price
            current_price = self.data_feed.get_latest_price(pos.symbol)
            if not current_price:
                continue

            pos.update_pnl(current_price)

            # Take-profit check
            if signal.take_profit:
                hit_tp = (
                    pos.side == Side.BUY and current_price >= signal.take_profit
                ) or (pos.side == Side.SELL and current_price <= signal.take_profit)

                if hit_tp:
                    logger.info(
                        f"🎯 Take-profit hit for {pos.symbol} at ${current_price:.2f}"
                    )
                    self._close_position(pos, "Take-profit target reached")

    def _close_position(self, position, reason: str):
        """Close a position and record the trade."""
        try:
            self.broker.close_position(position.symbol)

            trade = Trade(
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=position.current_price,
                entry_time=self._managed_positions.get(position.symbol, {}).get(
                    "entry_time", datetime.utcnow()
                ),
                exit_time=datetime.utcnow(),
                strategy_name=self._managed_positions.get(position.symbol, {})
                .get("signal", {})
                .strategy_name
                if self._managed_positions.get(position.symbol, {}).get("signal")
                else "unknown",
                reason_exit=reason,
            )
            trade.calculate_pnl()

            self.risk_manager.record_trade(trade)

            self.notifier.notify_trade_closed(
                symbol=trade.symbol,
                side=trade.side.value,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct,
                reason=reason,
            )

            # Remove from managed
            self._managed_positions.pop(position.symbol, None)

        except Exception as e:
            logger.error(f"Failed to close position {position.symbol}: {e}")

    def on_stop_entries(self):
        """3:30 PM ET — stop opening new positions."""
        logger.info("⏰ 3:30 PM — No more new entries allowed")

    def on_square_off(self):
        """3:50 PM ET — close ALL positions (end-of-day square-off)."""
        logger.info("═" * 40)
        logger.info("🔔 AUTO SQUARE-OFF — Closing all positions")
        logger.info("═" * 40)

        try:
            # Cancel all open orders first
            self.broker.cancel_all_orders()

            # Close all positions
            positions = self.broker.get_positions()
            for pos in positions:
                self._close_position(pos, "End-of-day auto square-off")

            self.broker.close_all_positions()
            logger.info("All positions squared off")

        except Exception as e:
            logger.error(f"Square-off error: {e}")
            # Emergency: Try force close
            try:
                self.broker.close_all_positions()
            except Exception:
                self.notifier.notify_error(f"CRITICAL: Square-off failed: {str(e)}")

    def on_market_close(self):
        """Market closed — generate daily report."""
        logger.info("═" * 40)
        logger.info("🔔 Market CLOSED — Daily Report")
        logger.info("═" * 40)

        summary = self.risk_manager.get_daily_summary()

        logger.info(f"  Total Trades: {summary['total_trades']}")
        logger.info(f"  Win Rate: {summary['win_rate']:.0f}%")
        logger.info(
            f"  Daily P&L: ${summary['daily_pnl']:+.2f} ({summary['daily_pnl_pct']:+.1f}%)"
        )
        logger.info(f"  Capital: ${summary['current_capital']:,.2f}")

        self.notifier.notify_daily_summary(summary)

        # Clear data cache
        self.data_feed.clear_cache()

    # ─── Main Run Loop ────────────────────────────────────────────

    def run(self):
        """Start the autonomous trading bot."""
        self._running = True

        # Register lifecycle events with scheduler
        self.scheduler.register("pre_market", self.on_pre_market)
        self.scheduler.register("market_open", self.on_market_open)
        self.scheduler.register("orb_end", self.on_orb_end)
        self.scheduler.register("scan_tick", self.on_scan_tick)
        self.scheduler.register("discovery_scan", self.market_scanner.scan_for_momentum)
        self.scheduler.register("journal_review", self.journal.update_performance)
        self.scheduler.register("stop_entries", self.on_stop_entries)
        self.scheduler.register("square_off", self.on_square_off)
        self.scheduler.register("market_close", self.on_market_close)

        # Start scheduler
        self.scheduler.start()

        logger.info("🤖 Bot is running — press Ctrl+C to stop")
        logger.info("Market hours: 9:30 AM - 4:00 PM ET")
        logger.info("Scanning every 1 minute during market hours")

        # Keep the main thread alive
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def run_once(self):
        """Run a single scan cycle (useful for testing / debugging)."""
        logger.info("Running single scan cycle...")

        self.on_pre_market()
        self.on_scan_tick()

        summary = self.risk_manager.get_daily_summary()
        logger.info(f"Scan complete — {summary['total_trades']} trades generated")

    def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False

        # Square off if market is still open
        if self.broker.is_market_open():
            self.on_square_off()

        self.scheduler.stop()
        logger.info("👋 Trader AI shutdown complete")


# ─── Entry Point ──────────────────────────────────────────────────────


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Trader AI — Autonomous Intraday Trading Bot"
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle and exit",
    )
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Override mode from env if set
    env_mode = os.getenv("TRADING_MODE", args.mode)
    mode = TradingMode.LIVE if env_mode == "live" else TradingMode.PAPER

    # Safety check for live mode
    if mode == TradingMode.LIVE:
        confirm = input(
            "\n⚠️  You are about to start LIVE trading with REAL MONEY.\n"
            "Type 'I UNDERSTAND THE RISKS' to proceed: "
        )
        if confirm != "I UNDERSTAND THE RISKS":
            print("Aborted.")
            sys.exit(0)

    # Load configuration
    config = load_config()
    setup_logging(config)

    # Create directories
    (PROJECT_ROOT / "data").mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    # Initialize and run
    engine = TradingEngine(config=config, mode=mode)

    # Handle signals for graceful shutdown
    def signal_handler(sig, frame):
        engine.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.once:
        engine.run_once()
    else:
        engine.run()


if __name__ == "__main__":
    main()
