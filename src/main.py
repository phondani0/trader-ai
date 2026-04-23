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

import pandas as pd
import yaml
from dotenv import load_dotenv
from loguru import logger

from src.core.broker_factory import BrokerFactory
from src.core.database import DatabaseManager
from src.core.data_feed import DataFeed
from src.core.models import (
    MarketRegime,
    OrderStatus,
    Position,
    Signal,
    SignalType,
    Side,
    Trade,
    TradingMode,
)
from src.core.strategy_approvals import get_global_strategy_approvals
from src.core.scheduler import MarketScheduler
from src.intelligence.journal import DiscoveryJournal
from src.intelligence.regime_detector import RegimeDetector
from src.intelligence.scanner import MarketScanner
from src.intelligence.signal_aggregator import SignalAggregator
from src.notifications.notifier import Notifier
from src.risk.kill_switch import KillSwitch
from src.risk.manager import RiskManager
from src.strategies.bearish_breakdown_continuation import (
    BearishBreakdownContinuationStrategy,
)
from src.strategies.chart_patterns import ChartPatternStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.macd_bollinger_inverse import MACDBollingerInverseStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.range_reversion import RangeReversionStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.volume_weighted_tsmom import VolumeWeightedTSMomentumStrategy
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

        self._ensure_strategy_approvals_ready()

        # Initialize Strategies
        strategy_configs = config
        self.strategies = self._init_strategies(strategy_configs)

        # Signal Aggregator
        self.signal_aggregator = SignalAggregator(
            strategies=self.strategies,
            regime_detector=self.regime_detector,
        )

        # Scheduler
        self.scheduler = MarketScheduler(
            broker=self.broker,
            config=config,
            strategies=self.strategies,
        )

        # Track active positions managed by us
        self._managed_positions: dict[
            str, dict
        ] = {}  # symbol → {signal, order_id, ...}
        self._foreign_exchange_state_detected = False
        self._foreign_exchange_state_signature: str | None = None
        self._bootstrapped = False

        logger.info(f"Capital: ${capital:,.2f}")
        logger.info(f"Watchlist: {', '.join(self.watchlist)}")
        logger.info(
            f"Strategies: {', '.join(s.name for s in self.strategies if s.enabled)}"
        )

    def _ensure_strategy_approvals_ready(self):
        benchmark_cfg = self.config.get("benchmarking", {})
        if not benchmark_cfg.get("enforce_approvals", True):
            return
        if not benchmark_cfg.get("refresh_on_start", False):
            return

        from scripts.benchmark import ensure_strategy_approvals, resolve_approvals_path

        approvals_path = resolve_approvals_path(self.config)
        logger.info(
            f"Checking benchmark approvals at startup: {approvals_path}"
        )
        refreshed = ensure_strategy_approvals(self.config)
        if refreshed:
            logger.info("Benchmark approvals refreshed on startup.")
        elif approvals_path.exists():
            logger.info("Using existing benchmark approvals.")

    def _persist_managed_position(self, symbol: str):
        managed = self._managed_positions.get(symbol)
        if not managed:
            return
        if not hasattr(self.db, "upsert_managed_position"):
            return

        payload = dict(managed)
        payload["symbol"] = symbol
        try:
            self.db.upsert_managed_position(payload)
        except Exception as e:
            logger.error(f"Failed to persist managed position for {symbol}: {e}")

    def _delete_persisted_managed_position(self, symbol: str):
        if not hasattr(self.db, "delete_managed_position"):
            return
        try:
            self.db.delete_managed_position(symbol)
        except Exception as e:
            logger.error(f"Failed to delete persisted managed position for {symbol}: {e}")

    @staticmethod
    def _parse_runtime_datetime(raw_dt) -> datetime | None:
        if not raw_dt:
            return None
        if isinstance(raw_dt, datetime):
            return raw_dt
        try:
            return datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    def _load_persisted_managed_positions(self) -> dict[str, dict]:
        persisted: dict[str, dict] = {}
        if not hasattr(self.db, "get_managed_positions"):
            return persisted
        try:
            rows = self.db.get_managed_positions()
        except Exception as e:
            logger.error(f"Failed to load persisted managed positions: {e}")
            return persisted

        for row in rows:
            signal = None
            signal_payload_json = row.get("signal_payload_json")
            if signal_payload_json:
                try:
                    signal = Signal.model_validate(json.loads(signal_payload_json))
                except Exception as e:
                    logger.error(
                        f"Failed to deserialize persisted signal for {row.get('symbol')}: {e}"
                    )

            side_raw = row.get("side", Side.BUY.value)
            try:
                side = Side(side_raw)
            except ValueError:
                side = Side.BUY

            persisted[row["symbol"]] = {
                "order_id": row.get("order_id"),
                "signal": signal,
                "entry_time": self._parse_runtime_datetime(row.get("entry_time"))
                or datetime.utcnow(),
                "side": side,
                "quantity": float(row.get("quantity") or 0.0),
                "entry_price": float(
                    row.get("entry_price")
                    or getattr(signal, "entry_price", 0.0)
                    or 0.0
                ),
            }
        return persisted

    @staticmethod
    def _exchange_order_id(order: dict) -> str | None:
        info = order.get("info", {}) if isinstance(order.get("info"), dict) else {}
        raw_order_id = (
            order.get("id")
            or order.get("orderId")
            or info.get("orderId")
            or info.get("order_id")
        )
        if raw_order_id in (None, ""):
            return None
        return str(raw_order_id)

    @staticmethod
    def _exchange_flag(order: dict, *keys: str) -> bool:
        info = order.get("info", {}) if isinstance(order.get("info"), dict) else {}
        for key in keys:
            for container in (order, info):
                value = container.get(key)
                if isinstance(value, bool):
                    if value:
                        return True
                    continue
                if value in (None, "", 0, "0", "false", "False"):
                    continue
                return True
        return False

    def _known_managed_order_ids(self) -> set[str]:
        order_ids: set[str] = set()
        for managed in self._managed_positions.values():
            order_id = managed.get("order_id")
            if order_id:
                order_ids.add(str(order_id))
        return order_ids

    def _is_expected_managed_open_order(self, order: dict) -> bool:
        symbol = str(order.get("symbol") or "")
        managed = self._managed_positions.get(symbol)
        if not managed:
            return False

        order_id = self._exchange_order_id(order)
        if order_id and order_id == str(managed.get("order_id") or ""):
            return True

        if self._exchange_flag(
            order,
            "reduceOnly",
            "closeOnTrigger",
            "triggerPrice",
            "stopLossPrice",
            "takeProfitPrice",
            "stopLoss",
            "takeProfit",
            "stopOrderType",
        ):
            return True

        return False

    def _describe_foreign_exchange_state(
        self,
        foreign_positions: list,
        foreign_orders: list[dict],
    ) -> tuple[str, str]:
        position_bits = [
            f"{position.symbol}:{position.side.value}:{float(position.quantity):g}"
            for position in sorted(foreign_positions, key=lambda item: item.symbol)
        ]
        order_bits = []
        for order in foreign_orders:
            order_bits.append(
                ":".join(
                    [
                        str(order.get("symbol") or "?"),
                        str(order.get("side") or "?"),
                        self._exchange_order_id(order) or "unknown-id",
                    ]
                )
            )

        signature = json.dumps(
            {
                "positions": position_bits,
                "orders": sorted(order_bits),
            },
            sort_keys=True,
        )

        details: list[str] = []
        if position_bits:
            details.append("positions=" + ", ".join(position_bits))
        if order_bits:
            details.append("open_orders=" + ", ".join(sorted(order_bits)))

        return signature, "; ".join(details)

    def _set_foreign_exchange_state(
        self,
        foreign_positions: list,
        foreign_orders: list[dict],
    ):
        if not foreign_positions and not foreign_orders:
            if getattr(self, "_foreign_exchange_state_detected", False):
                logger.info(
                    "Foreign exchange state cleared. New bot entries may resume."
                )
            self._foreign_exchange_state_detected = False
            self._foreign_exchange_state_signature = None
            return

        signature, details = self._describe_foreign_exchange_state(
            foreign_positions,
            foreign_orders,
        )
        already_reported = (
            getattr(self, "_foreign_exchange_state_detected", False)
            and getattr(self, "_foreign_exchange_state_signature", None) == signature
        )
        self._foreign_exchange_state_detected = True
        self._foreign_exchange_state_signature = signature

        if already_reported:
            return

        message = (
            "Foreign exchange state detected on the bot-only account. "
            "New bot entries are paused until the account is clean. "
            f"{details}"
        )
        logger.error(message)
        notifier = getattr(self, "notifier", None)
        if notifier and hasattr(notifier, "notify_error"):
            notifier.notify_error(message)

    def _reconcile_foreign_exchange_state(self, positions: list):
        open_orders = self.broker.fetch_open_orders(limit=50)
        managed_symbols = set(self._managed_positions.keys())
        known_order_ids = self._known_managed_order_ids()

        foreign_positions = [
            position for position in positions if position.symbol not in managed_symbols
        ]

        foreign_orders = []
        for order in open_orders:
            order_id = self._exchange_order_id(order)
            if order_id and order_id in known_order_ids:
                continue
            if self._is_expected_managed_open_order(order):
                continue
            foreign_orders.append(order)

        self._set_foreign_exchange_state(foreign_positions, foreign_orders)

    def _note_managed_position_drift(
        self,
        symbol: str,
        field_name: str,
        old_value,
        new_value,
    ):
        message = (
            f"Managed position for {symbol} drifted from bot intent on exchange "
            f"({field_name}: {old_value} -> {new_value}). "
            "Local tracking was updated to exchange reality."
        )
        logger.warning(message)
        notifier = getattr(self, "notifier", None)
        if notifier and hasattr(notifier, "notify_error"):
            notifier.notify_error(message)

    def _sync_managed_positions_with_exchange(self, positions):
        active_symbols = {position.symbol for position in positions}

        for position in positions:
            managed = self._managed_positions.get(position.symbol)
            if managed is None:
                continue

            changed = False
            if managed.get("side") != position.side:
                self._note_managed_position_drift(
                    position.symbol,
                    "side",
                    managed.get("side"),
                    position.side,
                )
                managed["side"] = position.side
                changed = True
            if float(managed.get("quantity") or 0.0) != float(position.quantity):
                self._note_managed_position_drift(
                    position.symbol,
                    "quantity",
                    managed.get("quantity"),
                    position.quantity,
                )
                managed["quantity"] = position.quantity
                changed = True
            if float(managed.get("entry_price") or 0.0) != float(position.entry_price):
                self._note_managed_position_drift(
                    position.symbol,
                    "entry_price",
                    managed.get("entry_price"),
                    position.entry_price,
                )
                managed["entry_price"] = position.entry_price
                changed = True

            if changed:
                self._persist_managed_position(position.symbol)

        stale_symbols = [
            symbol
            for symbol in list(self._managed_positions.keys())
            if symbol not in active_symbols
        ]
        for symbol in stale_symbols:
            self._record_exchange_side_close(symbol)

    def _rehydrate_managed_positions_from_exchange(self):
        positions = self.broker.get_positions()
        self._managed_positions = self._load_persisted_managed_positions()
        self._sync_managed_positions_with_exchange(positions)
        self._reconcile_foreign_exchange_state(positions)

        if self._managed_positions:
            logger.info(
                "Runtime position recovery: "
                + ", ".join(sorted(self._managed_positions.keys()))
            )

    def adopt_exchange_position(
        self,
        symbol: str,
        strategy_name: str,
        stop_loss: float,
        take_profit: float,
        reason: str = "Manual adoption of legacy exchange position",
    ) -> dict:
        position = self.broker.get_position(symbol)
        if position is None:
            raise ValueError(f"No open exchange position found for {symbol}")

        protection_synced = self.broker.set_position_protection(
            symbol=symbol,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_side=position.side,
        )
        if not protection_synced:
            raise RuntimeError(
                f"Failed to sync exchange TP/SL for adopted position {symbol}"
            )

        signal = Signal(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            strategy_name=strategy_name,
            signal_type=SignalType.BUY if position.side == Side.BUY else SignalType.SELL,
            side=position.side,
            entry_price=position.entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=1.0,
            regime=MarketRegime.UNKNOWN,
            reason=reason,
            metadata={
                "adopted": True,
                "adopted_at": datetime.utcnow().isoformat(timespec="seconds"),
                "source": "manual_adoption",
            },
        )

        managed = {
            "order_id": None,
            "signal": signal,
            "entry_time": datetime.utcnow(),
            "side": position.side,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
        }
        self._managed_positions[symbol] = managed
        self._persist_managed_position(symbol)
        self._reconcile_foreign_exchange_state(self.broker.get_positions())
        logger.info(
            f"Adopted existing exchange position for {symbol} into managed runtime state."
        )
        return managed

    def _init_strategies(self, config: dict) -> list:
        """Initialize all strategy instances from config."""
        strategies = []
        approvals_payload = self._load_strategy_approvals()
        approvals = get_global_strategy_approvals(approvals_payload)
        symbol_approvals = approvals_payload.get("symbols", {}) if approvals_payload else {}

        strategy_map = {
            "macd_bollinger": MACDBollingerStrategy,
            "macd_bollinger_inverse": MACDBollingerInverseStrategy,
            "vwap_reversion": VWAPReversionStrategy,
            "range_reversion": RangeReversionStrategy,
            "supertrend": SupertrendStrategy,
            "orb": ORBStrategy,
            "rsi_divergence": RSIDivergenceStrategy,
            "ema_crossover": EMACrossoverStrategy,
            "donchian_breakout": DonchianBreakoutStrategy,
            "bearish_breakdown_continuation": BearishBreakdownContinuationStrategy,
            "volume_weighted_tsmom": VolumeWeightedTSMomentumStrategy,
            "chart_patterns": ChartPatternStrategy,
        }

        for name, cls in strategy_map.items():
            params = config.get(name, {"enabled": True, "regime": ["all"]})
            strategy = cls(params=params)
            approved_symbols: list[str] = []
            for symbol, symbol_payload in symbol_approvals.items():
                symbol_strategy_approval = (
                    symbol_payload.get("strategies", {}).get(name, {})
                )
                deployable_regimes = symbol_strategy_approval.get("deployable_regimes")
                deployable_market_profiles = symbol_strategy_approval.get(
                    "deployable_market_profiles"
                )
                deployable_market_biases = symbol_strategy_approval.get(
                    "deployable_market_biases"
                )
                if deployable_regimes:
                    strategy.set_symbol_benchmark_approval(
                        symbol,
                        regimes=deployable_regimes,
                        market_profiles=deployable_market_profiles,
                        market_biases=deployable_market_biases,
                    )
                    approved_symbols.append(symbol)

            if approvals_payload and symbol_approvals and not approved_symbols:
                strategy.enabled = False
                logger.warning(
                    f"  ⛔ Strategy not approved for any benchmarked symbol: {name}"
                )
                strategies.append(strategy)
                status = "✅" if strategy.enabled else "⬜"
                logger.info(f"  {status} Strategy: {name}")
                continue

            if approvals and name not in approvals and not symbol_approvals:
                strategy.enabled = False
                logger.warning(
                    f"  ⛔ Strategy skipped until benchmarked: {name}"
                )
                strategies.append(strategy)
                status = "✅" if strategy.enabled else "⬜"
                logger.info(f"  {status} Strategy: {name}")
                continue

            approval = approvals.get(name, {})
            deployable_regimes = approval.get("deployable_regimes")
            deployable_market_profiles = approval.get("deployable_market_profiles")
            deployable_market_biases = approval.get("deployable_market_biases")
            if deployable_regimes is not None:
                strategy.set_benchmark_approved_regimes(deployable_regimes)
            if deployable_market_profiles is not None:
                strategy.set_benchmark_approved_market_profiles(
                    deployable_market_profiles
                )
            if deployable_market_biases is not None:
                strategy.set_benchmark_approved_market_biases(
                    deployable_market_biases
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
                if deployable_market_biases:
                    logger.info(
                        f"     Macro bias: {', '.join(deployable_market_biases)}"
                    )
            if approved_symbols:
                logger.info(
                    f"     Approved symbols: {', '.join(sorted(approved_symbols))}"
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
            return payload
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
        self.data_feed.refresh_for_strategies(
            bootstrap_universe,
            strategies=self.strategies,
        )

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

        orb_strategies = [s for s in self.strategies if isinstance(s, ORBStrategy)]
        data = self.data_feed.refresh_for_strategies(
            self.watchlist,
            strategies=orb_strategies,
        )

        for s in self.strategies:
            if isinstance(s, ORBStrategy):
                for symbol in self.watchlist:
                    symbol_frames = data.get(symbol, {})
                    strategy_df = symbol_frames.get(self.data_feed.strategy_timeframe(s))
                    if strategy_df is not None and not strategy_df.empty:
                        orb_minutes = s.params.get("orb_minutes", 30)
                        s.set_orb_range(symbol, strategy_df, orb_minutes)

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
            data = self.data_feed.refresh_for_strategies(
                tradable_universe,
                strategies=self.strategies,
            )

            if not data:
                return

            # 2. Check existing positions for exit conditions
            self._manage_positions(data, positions)

            if not self._enforce_risk_guards():
                return

            if getattr(self, "_foreign_exchange_state_detected", False):
                logger.warning(
                    "Foreign exchange state is present on the bot-only account. "
                    "Skipping new entries until it is cleared."
                )
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
            signals = self.signal_aggregator.generate_signals(
                signal_data,
                enforce_fresh_bars=True,
            )

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
        signal_data: dict[str, pd.DataFrame | dict[str, pd.DataFrame]],
    ):
        """Explain why active discoveries did not convert into executable signals."""
        candidate_summaries: list[str] = []

        for symbol, candidate in list(active_candidates.items())[:5]:
            symbol_data = signal_data.get(symbol)
            if symbol_data is None:
                candidate_summaries.append(
                    f"{symbol}(score={candidate.get('score', 0.0):.1f}, no data)"
                )
                continue

            primary_frame = self.data_feed.primary_frame(symbol_data)
            if primary_frame.empty:
                candidate_summaries.append(
                    f"{symbol}(score={candidate.get('score', 0.0):.1f}, no data)"
                )
                continue

            diagnosis = self.signal_aggregator.diagnose_symbol(symbol, symbol_data)
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
                self._persist_managed_position(order.symbol)

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

        self._sync_managed_positions_with_exchange(positions)
        self._reconcile_foreign_exchange_state(positions)

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

        entry_time = managed.get("entry_time")
        since_ms = None
        if isinstance(entry_time, datetime):
            since_ms = int(entry_time.timestamp() * 1000)

        closed_orders = self.broker.fetch_recent_closed_orders(symbol=symbol, limit=10)
        close_evidence = self.broker.fetch_recent_close_evidence(
            symbol=symbol,
            since=since_ms,
            limit=20,
        )
        closing_order = self._match_closing_order(
            managed,
            [*closed_orders, *close_evidence],
        )

        if not closing_order:
            missing_scans = int(managed.get("_missing_exchange_scans", 0)) + 1
            managed["_missing_exchange_scans"] = missing_scans
            if missing_scans >= 3:
                self._force_record_missing_exchange_close(symbol, managed)
                return
            logger.warning(
                f"Position disappeared on exchange for {symbol}, but no closing order "
                "was found yet. Keeping local tracking until a later scan."
            )
            return

        managed.pop("_missing_exchange_scans", None)

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
        self._delete_persisted_managed_position(symbol)
        logger.info(
            f"🔄 Reconciled exchange-side close for {symbol} at ${exit_price:.4f} "
            f"({reason})"
        )

    def _force_record_missing_exchange_close(self, symbol: str, managed: dict):
        signal = managed.get("signal")
        entry_price = float(
            managed.get("entry_price") or getattr(signal, "entry_price", 0.0) or 0.0
        )
        quantity = float(managed.get("quantity") or 0.0)
        exit_price = float(getattr(signal, "stop_loss", 0.0) or 0.0) or entry_price
        reason = "Exchange-side close inferred after repeated missing-position scans"
        if getattr(signal, "stop_loss", 0.0):
            reason = "Exchange-side stop-loss inferred after repeated missing-position scans"

        trade = Trade(
            symbol=symbol,
            side=managed.get("side", Side.BUY),
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=managed.get("entry_time", datetime.utcnow()),
            exit_time=datetime.utcnow(),
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
        self._delete_persisted_managed_position(symbol)
        logger.warning(
            f"🔄 Reconciled missing exchange position for {symbol} using "
            f"conservative inferred exit at ${exit_price:.4f} ({reason})"
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
        source = str(info.get("source", "")).lower()
        stop_order_type = str(info.get("stopOrderType", "")).lower()
        create_type = str(info.get("createType", "")).lower()
        trigger_price = (
            order.get("triggerPrice")
            or order.get("stopLossPrice")
            or info.get("triggerPrice")
        )

        if "stoploss" in stop_order_type or "stoploss" in create_type:
            return "Exchange stop-loss triggered"
        if source == "closed_pnl":
            return "Exchange-side close reconciled from closed P&L history"
        if source == "execution":
            return "Exchange-side close reconciled from execution history"
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
            self._delete_persisted_managed_position(position.symbol)
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
        if self.broker.is_market_open() and not self.broker.is_24_7_market:
            self.on_square_off()

        if self.scheduler.scheduler.running:
            self.scheduler.stop()
        logger.info("👋 Trader AI shutdown complete")

    def _bootstrap_runtime(self):
        """Prepare runtime state so scheduled and one-shot modes start the same way."""
        if self._bootstrapped:
            return

        self.on_pre_market()
        self._rehydrate_managed_positions_from_exchange()
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
