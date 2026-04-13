"""
Trader AI — Autonomous Intraday Trading Bot
============================================

Main entry point. This is the "always-on worker" that:
    1. Resets daily state for the active broker mode
    2. Fetches data and detects market regime
    3. Runs strategies and generates signals
    4. Validates signals through risk management
    5. Executes trades via the configured broker
    6. Monitors positions and manages exits
    7. Squares off session-based positions when required
    8. Generates daily report

Usage:
    python -m src.main                    # Run the bot
    python -m src.main --mode paper       # Paper trading (default)
    python -m src.main --mode live        # LIVE trading (careful!)
    python -m src.main --once             # Run one scan cycle and exit
"""

from __future__ import annotations

import argparse
import json
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
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.macd_bollinger_inverse import MACDBollingerInverseStrategy
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
        self.journal = DiscoveryJournal(broker=self.broker, db=self.db, config=config)

        # Load watchlist
        self.watchlist = config.get(
            "active_watchlist",
            [
                "BTC/USDT:USDT",
                "ETH/USDT:USDT",
                "SOL/USDT:USDT",
                "DOGE/USDT:USDT",
                "LTC/USDT:USDT",
            ],
        )

        # Initialize capital
        try:
            capital = float(self.broker.get_equity())
        except Exception:
            capital = float(config.get("trading", {}).get("capital", 25000))

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
        self._bootstrapped = False

        logger.info(f"Capital: ${capital:,.2f}")
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
        logger.info(
            f"Strategies: {', '.join(s.name for s in self.strategies if s.enabled)}"
        )

    def _init_strategies(self, config: dict) -> list:
        """Initialize all strategy instances from config."""
        strategies = []
        approvals = self._load_strategy_approvals()

        strategy_map = {
            "macd_bollinger": MACDBollingerStrategy,
            "macd_bollinger_inverse": MACDBollingerInverseStrategy,
            "vwap_reversion": VWAPReversionStrategy,
            "supertrend": SupertrendStrategy,
            "orb": ORBStrategy,
            "rsi_divergence": RSIDivergenceStrategy,
            "ema_crossover": EMACrossoverStrategy,
            "donchian_breakout": DonchianBreakoutStrategy,
            "chart_patterns": ChartPatternStrategy,
        }

        for name, cls in strategy_map.items():
            params = config.get(name, {"enabled": True, "regime": ["all"]})
            strategy = cls(params=params)
            approval = approvals.get(name, {})
            deployable_regimes = approval.get("deployable_regimes")
            deployable_market_profiles = approval.get("deployable_market_profiles")
            if deployable_regimes is not None:
                strategy.set_benchmark_approved_regimes(deployable_regimes)
            if deployable_market_profiles is not None:
                strategy.set_benchmark_approved_market_profiles(
                    deployable_market_profiles
                )

            if approval and not approval.get("approved", False) and not deployable_regimes:
                strategy.enabled = False
                logger.warning(
                    f"  ⛔ Strategy gated by benchmark approval: {name} "
                    f"({approval.get('reason', 'not approved')})"
                )
            elif deployable_regimes:
                logger.info(
                    f"  🎯 Benchmark-approved regimes for {name}: "
                    f"{', '.join(deployable_regimes)}"
                )
                if deployable_market_profiles:
                    logger.info(
                        f"     Macro profiles: {', '.join(deployable_market_profiles)}"
                    )
            strategies.append(strategy)
            status = "✅" if strategy.enabled else "⬜"
            logger.info(f"  {status} Strategy: {name}")

        return strategies

    def _load_strategy_approvals(self) -> dict:
        """Load benchmark-derived strategy approvals when enabled."""
        benchmark_cfg = self.config.get("benchmarking", {})
        if not benchmark_cfg.get("enforce_approvals", True):
            return {}

        approvals_rel = benchmark_cfg.get("approvals_path", "data/strategy_approvals.json")
        approvals_path = PROJECT_ROOT / approvals_rel
        if not approvals_path.exists():
            logger.warning(
                f"Benchmark approvals file not found at {approvals_path}. "
                "Using config-level strategy enablement only."
            )
            return {}

        try:
            with open(approvals_path, "r") as f:
                payload = json.load(f)
            return payload.get("strategies", {})
        except Exception as e:
            logger.error(f"Failed to load strategy approvals: {e}")
            return {}

    # ─── Lifecycle Events ─────────────────────────────────────────

    def on_pre_market(self):
        """Run before market opens — prepare data and reset daily counters."""
        logger.info("═" * 40)
        logger.info(
            "🌅 Daily Reset & Preparation"
            if self.broker.is_24_7_market
            else "🌅 Pre-Market Preparation"
        )
        logger.info("═" * 40)

        # Reset daily state
        equity = self.broker.get_equity()
        self.risk_manager.reset_daily(current_equity=equity)
        self._orb_set = False
        if not self.broker.is_24_7_market:
            self._managed_positions.clear()

        # Reset ORB strategy
        for s in self.strategies:
            if isinstance(s, ORBStrategy):
                s.reset_daily()

        # Refresh dynamic discovery universe before the first data preload.
        self.market_scanner.scan_for_momentum()

        # Pre-fetch data
        bootstrap_universe = self.market_scanner.build_tradable_universe(self.watchlist)
        logger.info(f"Fetching data for {len(bootstrap_universe)} symbols...")
        self.data_feed.refresh_all(bootstrap_universe, strategies=self.strategies)

        # Account status
        buying_power = self.broker.get_buying_power()
        logger.info(
            f"Account equity: ${equity:,.2f} | Buying power: ${buying_power:,.2f}"
        )

        # Notify
        self.notifier.notify_startup(
            mode=self.mode.value,
            capital=equity,
            strategies=[s.name for s in self.strategies if s.enabled],
        )

    def on_market_open(self):
        """Market just opened — start scanning."""
        logger.info("🔔 Market OPEN — scanning begins")

    def on_orb_end(self):
        """ORB window (first 30 min) has closed — set ORB levels."""
        logger.info("⏰ ORB window closed — setting Opening Range levels")

        data = self.data_feed.refresh_all(self.watchlist, strategies=self.strategies)

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
        Main scan loop — runs on the configured scheduler cadence.
        """
        if not self._enforce_risk_guards():
            return

        try:
            positions = self.broker.get_positions()
            position_symbols = [position.symbol for position in positions]
            tradable_universe = self.market_scanner.build_tradable_universe(
                self.watchlist,
                persistent_symbols=[*position_symbols, *self._managed_positions.keys()],
            )

            # 1. Refresh data
            data = self.data_feed.refresh_all(
                tradable_universe,
                strategies=self.strategies,
            )

            if not data:
                return

            # 2. Check existing positions for exit conditions
            self._manage_positions(data, positions)

            if not self._enforce_risk_guards():
                return

            # 3. Check if we can open new positions
            if not self.scheduler.can_open_new_positions():
                return

            active_candidates = {
                candidate["symbol"]: candidate
                for candidate in self.market_scanner.get_active_candidates()
            }
            dynamic_universe_symbols = set(
                self.market_scanner.get_dynamic_universe_symbols()
            )
            signal_data = {
                symbol: symbol_data
                for symbol, symbol_data in data.items()
                if symbol in self.watchlist
                or symbol in active_candidates
                or symbol in dynamic_universe_symbols
            }

            # 4. Generate signals from the core watchlist plus approved discoveries
            signals = self.signal_aggregator.generate_signals(signal_data)

            if not signals:
                if active_candidates:
                    self._log_no_signal_context(active_candidates, signal_data)
                return

            # 5. Validate and execute signals through risk manager
            buying_power = self.broker.get_buying_power()

            for signal in signals:
                candidate_context = active_candidates.get(signal.symbol)
                if candidate_context:
                    signal.metadata["discovery_score"] = candidate_context["score"]
                    signal.metadata["discovery_change_pct"] = candidate_context["change"]
                    signal.metadata["scanner_profile"] = candidate_context.get(
                        "scanner_profile"
                    )
                    signal.metadata["discovery_direction"] = candidate_context.get(
                        "direction"
                    )
                elif signal.symbol in self.watchlist:
                    signal.metadata["core_watchlist"] = True
                elif signal.symbol in dynamic_universe_symbols:
                    signal.metadata["dynamic_universe"] = True

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
                    if not self._enforce_risk_guards():
                        return
                else:
                    logger.debug(f"Signal rejected for {signal.symbol}: {reason}")

        except Exception as e:
            logger.error(f"Error in scan tick: {e}")
            self.notifier.notify_error(f"Scan tick error: {str(e)}")

    def _log_no_signal_context(
        self,
        active_candidates: dict[str, dict],
        signal_data: dict[str, pd.DataFrame],
    ):
        """Explain why active discoveries did not convert into executable signals."""
        candidate_summaries: list[str] = []
        regime_detector = self.signal_aggregator.regime_detector

        for symbol, candidate in list(active_candidates.items())[:5]:
            df = signal_data.get(symbol)
            if df is None or df.empty:
                candidate_summaries.append(
                    f"{symbol}(score={candidate.get('score', 0.0):.1f}, no data)"
                )
                continue

            diagnosis = self.signal_aggregator.diagnose_symbol(symbol, df)
            regime = diagnosis["regime"]
            market_profile = diagnosis["market_profile"]
            eligible = diagnosis["eligible_strategies"]
            generated = diagnosis["generated_signals"]
            blocked = diagnosis["blocked_reasons"]
            if generated:
                outcome = f"signals={','.join(generated[:2])}"
            elif eligible:
                outcome = f"eligible={','.join(eligible[:2])}, no_entry"
            elif blocked:
                top_reason, names = next(iter(blocked.items()))
                outcome = f"blocked={top_reason}:{','.join(names[:2])}"
            else:
                outcome = "no_strategy_path"
            candidate_summaries.append(
                f"{symbol}(score={candidate.get('score', 0.0):.1f}, "
                f"profile={candidate.get('scanner_profile', '-')}, "
                f"regime={regime}, market={market_profile}, {outcome})"
            )

        logger.info(
            "No executable strategy setups this cycle. "
            f"Active discoveries: {', '.join(candidate_summaries)}"
        )

    def _execute_order(self, order):
        """Execute a validated order through the broker."""
        try:
            result = self.broker.place_order(order)

            if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                filled_entry_price = result.filled_price or order.signal.entry_price
                # Track the position
                self._managed_positions[order.symbol] = {
                    "order_id": result.id,
                    "signal": order.signal,
                    "entry_time": datetime.utcnow(),
                    "side": order.side,
                    "quantity": order.quantity,
                    "entry_price": filled_entry_price,
                }

                # Send notification
                self.notifier.notify_trade(
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=order.quantity,
                    price=filled_entry_price,
                    strategy=order.strategy_name,
                    stop_loss=order.signal.stop_loss,
                    take_profit=order.signal.take_profit,
                )

                # Place stop-loss order
                if self.broker.uses_exchange_managed_stops:
                    logger.info(
                        f"🛡️ Exchange-managed stop-loss attached for {order.symbol} "
                        f"at ${order.signal.stop_loss:.2f}"
                    )
                else:
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

    def _manage_positions(self, data: dict, positions=None):
        """
        Check existing positions for:
            - Take-profit targets
            - Trailing stops
            - Time-based exits
        """
        if positions is None:
            positions = self.broker.get_positions()

        self._reconcile_exchange_closed_positions(positions)

        for pos in positions:
            if pos.symbol not in self._managed_positions:
                continue

            managed = self._managed_positions[pos.symbol]
            signal = managed.get("signal")

            if not signal:
                continue

            # Get latest price
            current_price = self.data_feed.get_latest_price(
                pos.symbol,
                prefer_live=True,
            )
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

    def _reconcile_exchange_closed_positions(self, positions: list[Position]):
        """
        Record positions that disappeared on the exchange outside our direct close path.
        """
        active_symbols = {position.symbol for position in positions}
        stale_symbols = [
            symbol
            for symbol in list(self._managed_positions.keys())
            if symbol not in active_symbols
        ]

        for symbol in stale_symbols:
            self._record_exchange_side_close(symbol)

    def _record_exchange_side_close(self, symbol: str):
        managed = self._managed_positions.get(symbol)
        if not managed:
            return

        closed_orders = self.broker.fetch_recent_closed_orders(symbol=symbol, limit=10)
        closing_order = self._match_closing_order(managed, closed_orders)

        if not closing_order:
            logger.warning(
                f"Position disappeared on exchange for {symbol}, but no closing order "
                "was found yet. Keeping local tracking until a later scan."
            )
            return

        signal = managed.get("signal")
        entry_price = float(managed.get("entry_price") or getattr(signal, "entry_price", 0.0) or 0.0)
        exit_price = float(
            closing_order.get("average")
            or closing_order.get("price")
            or entry_price
            or 0.0
        )
        quantity = float(
            closing_order.get("filled")
            or closing_order.get("amount")
            or managed.get("quantity")
            or 0.0
        )
        exit_time = self._order_datetime(closing_order) or datetime.utcnow()
        reason = self._infer_exchange_close_reason(closing_order)

        trade = Trade(
            symbol=symbol,
            side=managed.get("side", Side.BUY),
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=managed.get("entry_time", datetime.utcnow()),
            exit_time=exit_time,
            strategy_name=getattr(signal, "strategy_name", "unknown"),
            reason_exit=reason,
        )
        trade.calculate_pnl()

        self.risk_manager.record_trade(trade)
        self.db.record_trade(trade)
        self.notifier.notify_trade_closed(
            symbol=trade.symbol,
            side=trade.side.value,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            reason=reason,
        )
        self._managed_positions.pop(symbol, None)
        logger.info(
            f"🔄 Reconciled exchange-side close for {symbol} at ${exit_price:.4f} "
            f"({reason})"
        )

    def _match_closing_order(self, managed: dict, closed_orders: list[dict]) -> dict | None:
        entry_time = managed.get("entry_time")
        expected_side = Side.SELL if managed.get("side") == Side.BUY else Side.BUY

        candidates: list[dict] = []
        for order in closed_orders:
            side = str(order.get("side", "")).lower()
            if side != expected_side.value:
                continue
            order_dt = self._order_datetime(order)
            if entry_time and order_dt and order_dt < entry_time:
                continue
            candidates.append(order)

        if not candidates:
            return None

        candidates.sort(
            key=lambda order: self._order_datetime(order) or datetime.min,
            reverse=True,
        )
        return candidates[0]

    @staticmethod
    def _order_datetime(order: dict) -> datetime | None:
        raw_dt = order.get("datetime")
        if raw_dt:
            try:
                return datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass

        timestamp = order.get("timestamp") or order.get("lastTradeTimestamp")
        if timestamp:
            try:
                return datetime.utcfromtimestamp(float(timestamp) / 1000.0)
            except (TypeError, ValueError, OSError):
                return None
        return None

    @staticmethod
    def _infer_exchange_close_reason(order: dict) -> str:
        info = order.get("info", {}) if isinstance(order.get("info"), dict) else {}
        stop_order_type = str(info.get("stopOrderType", "")).lower()
        create_type = str(info.get("createType", "")).lower()
        trigger_price = (
            order.get("triggerPrice")
            or order.get("stopLossPrice")
            or info.get("triggerPrice")
        )

        if "stoploss" in stop_order_type or "stoploss" in create_type:
            return "Exchange stop-loss triggered"
        if trigger_price:
            return "Exchange trigger order executed"
        if info.get("reduceOnly") or order.get("reduceOnly"):
            return "Exchange reduce-only close"
        return "Position closed on exchange"

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
            self.db.record_trade(trade)

            self.notifier.notify_trade_closed(
                symbol=trade.symbol,
                side=trade.side.value,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct,
                reason=reason,
            )

            # Remove from managed
            self._managed_positions.pop(position.symbol, None)
            self._enforce_risk_guards()

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
        logger.info(f"  Drawdown: {summary['drawdown_pct']:+.2f}%")

        self.notifier.notify_daily_summary(summary)
        logger.info(self.journal.generate_short_report())
        suggestions = self.journal.generate_parameter_suggestions()
        logger.info(self.journal.format_parameter_suggestions(suggestions))

        # Clear data cache
        self.data_feed.clear_cache()

    def on_position_check(self):
        """Monitor live positions between signal scans so exits are not delayed by higher timeframes."""
        try:
            positions = self.broker.get_positions()
            if not positions:
                return
            self._manage_positions({}, positions)
        except Exception as e:
            logger.error(f"Position monitor error: {e}")

    # ─── Main Run Loop ────────────────────────────────────────────

    def run(self):
        """Start the autonomous trading bot."""
        self._running = True

        # Register lifecycle events with scheduler
        self.scheduler.register("pre_market", self.on_pre_market)
        self.scheduler.register("market_open", self.on_market_open)
        self.scheduler.register("orb_end", self.on_orb_end)
        self.scheduler.register("scan_tick", self.on_scan_tick)
        self.scheduler.register("position_check", self.on_position_check)
        self.scheduler.register("discovery_scan", self.market_scanner.scan_for_momentum)
        self.scheduler.register("journal_review", self.journal.update_performance)
        self.scheduler.register("stop_entries", self.on_stop_entries)
        self.scheduler.register("square_off", self.on_square_off)
        self.scheduler.register("market_close", self.on_market_close)

        self._bootstrap_runtime()

        # Start scheduler
        self.scheduler.start()

        logger.info("🤖 Bot is running — press Ctrl+C to stop")
        if self.broker.is_24_7_market:
            logger.info("Market: 24/7 crypto")
            logger.info(
                f"Signal scans every {self.scheduler.scan_interval_minutes} minutes "
                f"with position checks every {self.scheduler.position_check_interval_minutes} minutes"
            )
        else:
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

        self._bootstrap_runtime()
        self.on_scan_tick()
        self.on_market_close()

    def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False

        # Square off if market is still open
        if self.broker.is_market_open():
            self.on_square_off()

        if self.scheduler.scheduler.running:
            self.scheduler.stop()
        logger.info("👋 Trader AI shutdown complete")

    def _bootstrap_runtime(self):
        """Prepare runtime state so scheduled and one-shot modes start the same way."""
        if self._bootstrapped:
            return

        self.on_pre_market()
        if self.config.get("discovery", {}).get("enabled", True):
            self.market_scanner.scan_for_momentum()
        self._enforce_risk_guards()
        self._bootstrapped = True

    def _enforce_risk_guards(self) -> bool:
        """Sync equity and escalate local risk kill-switches to the global execution kill-switch."""
        if self.kill_switch.is_active:
            return False

        try:
            equity = self.broker.get_equity()
            self.risk_manager.sync_equity(equity)
        except Exception as e:
            logger.error(f"Failed to sync account equity for risk guards: {e}")
            return not self.kill_switch.is_active

        if self.risk_manager.is_kill_switch_active and not self.kill_switch.is_active:
            self._engage_kill_switch(self.risk_manager.kill_switch_reason)

        return not self.kill_switch.is_active

    def _engage_kill_switch(self, reason: str):
        """Activate the execution kill-switch once and clear in-memory managed state."""
        if self.kill_switch.is_active:
            return

        self.kill_switch.activate(reason)
        self.notifier.notify_kill_switch(reason)
        self._managed_positions.clear()


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
