"""
Discovery Journal — Feedback Loop Logic.
Tracks the performance of coins found by the scanner over time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from loguru import logger

from src.core.database import DatabaseManager
from src.core.base_broker import BaseBroker


class DiscoveryJournal:
    """
    Manages the feedback loop for market discovery.
    Records snapshots and evaluates accuracy at 1h, 4h, and 24h intervals.
    """

    def __init__(self, broker: BaseBroker, db: DatabaseManager, config: dict | None = None):
        self.broker = broker
        self.db = db
        self.config = config or {}

    def record(self, discovery_data: Dict):
        """Record a new momentum discovery in the journal."""
        try:
            discovery_id, created = self.db.record_discovery(discovery_data)
            action = "created" if created else "refreshed"
            logger.info(
                f"📓 Journal entry {action} for {discovery_data['symbol']} (ID: {discovery_id})"
            )
        except Exception as e:
            logger.error(f"Failed to journal discovery: {e}")

    def update_performance(self):
        """
        Check pending discoveries and update their performance metrics
        if the time window has passed.
        """
        pending = self.db.get_pending_discoveries()
        if not pending:
            return

        logger.info(f"🧐 Reviewing {len(pending)} pending discovery journals...")
        now = datetime.utcnow()

        for entry in pending:
            symbol = entry["symbol"]
            discovery_time = self._parse_timestamp(entry["discovery_time"])

            # Fetch current price
            current_ticker = self.broker.fetch_ticker(symbol)
            current_price = current_ticker.get("last", 0.0)
            if not current_price:
                continue

            updates: Dict = {}
            elapsed = now - discovery_time

            # Check windows
            if not entry["price_1h"] and elapsed >= timedelta(hours=1):
                updates["price_1h"] = current_price
            
            if not entry["price_4h"] and elapsed >= timedelta(hours=4):
                updates["price_4h"] = current_price
            
            if not entry["price_24h"] and elapsed >= timedelta(hours=24):
                updates["price_24h"] = current_price
                updates["status"] = "completed"

            # Track Max High observed
            prev_max = entry.get("max_price", 0.0) or 0.0
            if current_price > prev_max:
                updates["max_price"] = current_price

            prev_min = entry.get("min_price", 0.0) or 0.0
            if prev_min == 0.0 or current_price < prev_min:
                updates["min_price"] = current_price

            if updates:
                self.db.update_discovery(entry["id"], updates)
                logger.debug(f"📝 Updated journal for {symbol}: {updates}")

    def generate_short_report(self) -> str:
        """Generate a summary of recent discovery performance."""
        recent = self.db.get_journal_summary(days=1)
        if not recent:
            return "No recent discoveries to report."

        total = len(recent)
        completed = [r for r in recent if r["status"] == "completed"]
        
        report = [
            "\n" + "═" * 60,
            "📊 DISCOVERY PERFORMANCE REPORT (24H)",
            "═" * 60,
            f"{'SYMBOL':<15} | {'ENTRY':<10} | {'MAX %':<10} | {'STATUS'}",
            "-" * 60
        ]

        for r in recent:
            max_p = r.get("max_price", r["entry_price"])
            max_move = ((max_p / r["entry_price"]) - 1) * 100
            
            status_emoji = "✅" if r["status"] == "completed" else "⏳"
            report.append(
                f"{r['symbol']:<15} | {r['entry_price']:<10.4f} | {max_move:<10.2f}% | {status_emoji} {r['status']}"
            )

        report.append("═" * 60 + "\n")
        return "\n".join(report)

    def generate_parameter_suggestions(self) -> list[dict]:
        """Generate passive tuning suggestions from recent journal and trade outcomes."""
        adapt_cfg = self.config.get("adaptation", {})
        if not adapt_cfg.get("enabled", True):
            return []

        lookback_days = adapt_cfg.get("suggestion_lookback_days", 14)
        min_completed = adapt_cfg.get("min_completed_discoveries", 10)
        min_recent_trades = adapt_cfg.get("min_recent_trades", 5)

        suggestions: list[dict] = []
        completed = self.db.get_completed_discoveries(days=lookback_days)
        recent_trades = self.db.get_recent_trades(days=lookback_days)

        if len(completed) >= min_completed:
            suggestions.extend(self._scanner_suggestions(completed))

        if len(recent_trades) >= min_recent_trades:
            suggestions.extend(self._stop_suggestions(recent_trades))

        if suggestions:
            self.db.record_parameter_suggestions(suggestions)

        return suggestions

    def format_parameter_suggestions(self, suggestions: list[dict]) -> str:
        """Render passive parameter suggestions for logs or CLI output."""
        if not suggestions:
            return "No passive parameter suggestions generated."

        lines = [
            "\n" + "═" * 60,
            "🧠 PASSIVE PARAMETER SUGGESTIONS",
            "═" * 60,
        ]
        for suggestion in suggestions:
            lines.append(
                f"{suggestion['parameter_name']}: {suggestion['current_value']} -> "
                f"{suggestion['suggested_value']} | conf={suggestion['confidence']:.0%}"
            )
            lines.append(suggestion["rationale"])
        lines.append("═" * 60 + "\n")
        return "\n".join(lines)

    def _scanner_suggestions(self, completed: list[dict]) -> list[dict]:
        discovery_cfg = self.config.get("discovery", {})
        current_min_change = float(discovery_cfg.get("min_price_change", 3.0))
        current_min_volume = float(discovery_cfg.get("min_24h_volume", 10_000_000))

        continuation_hits = 0
        excursions: list[float] = []

        for row in completed:
            entry_price = float(row.get("entry_price") or 0.0)
            change_pct = float(row.get("discovery_change_pct") or 0.0)
            price_24h = float(row.get("price_24h") or 0.0)
            max_price = float(row.get("max_price") or entry_price or 0.0)
            min_price = float(row.get("min_price") or entry_price or 0.0)

            if entry_price <= 0 or change_pct == 0 or price_24h <= 0:
                continue

            if change_pct > 0:
                continuation = price_24h > entry_price
                excursion = ((max_price / entry_price) - 1) * 100
            else:
                continuation = price_24h < entry_price
                excursion = ((entry_price / min_price) - 1) * 100 if min_price > 0 else 0.0

            continuation_hits += int(continuation)
            excursions.append(excursion)

        if not excursions:
            return []

        continuation_rate = continuation_hits / len(excursions)
        avg_excursion = sum(excursions) / len(excursions)
        suggestions: list[dict] = []

        if continuation_rate < 0.4:
            suggestions.append(
                {
                    "source": "journal",
                    "parameter_name": "discovery.min_price_change",
                    "current_value": current_min_change,
                    "suggested_value": round(current_min_change + 0.5, 2),
                    "confidence": 0.7,
                    "rationale": (
                        "Recent momentum discoveries showed weak 24h continuation. "
                        "Tighten burst quality by requiring a larger initial move."
                    ),
                    "metadata": {
                        "continuation_rate": continuation_rate,
                        "avg_excursion_pct": avg_excursion,
                    },
                }
            )
            suggestions.append(
                {
                    "source": "journal",
                    "parameter_name": "discovery.min_24h_volume",
                    "current_value": current_min_volume,
                    "suggested_value": round(current_min_volume * 1.2, 2),
                    "confidence": 0.65,
                    "rationale": (
                        "Weak follow-through suggests the scanner is still catching too much low-quality flow. "
                        "Raise the liquidity floor to bias toward stronger participation."
                    ),
                    "metadata": {
                        "continuation_rate": continuation_rate,
                        "avg_excursion_pct": avg_excursion,
                    },
                }
            )
        elif continuation_rate > 0.65 and avg_excursion > current_min_change:
            suggestions.append(
                {
                    "source": "journal",
                    "parameter_name": "discovery.min_price_change",
                    "current_value": current_min_change,
                    "suggested_value": round(max(1.5, current_min_change - 0.25), 2),
                    "confidence": 0.55,
                    "rationale": (
                        "Recent discoveries have strong follow-through, so the scanner may be able to admit "
                        "slightly earlier momentum without materially degrading quality."
                    ),
                    "metadata": {
                        "continuation_rate": continuation_rate,
                        "avg_excursion_pct": avg_excursion,
                    },
                }
            )

        return suggestions

    def _stop_suggestions(self, trades: list[dict]) -> list[dict]:
        strategy_configs = self.config
        stop_parameter_map = {
            "vwap_reversion": ("vwap_reversion.stop_loss_pct", strategy_configs.get("vwap_reversion", {}).get("stop_loss_pct")),
            "macd_bollinger": (
                "macd_bollinger.stop_loss_atr_multiplier",
                strategy_configs.get("macd_bollinger", {}).get("stop_loss_atr_multiplier"),
            ),
            "ema_crossover": (
                "ema_crossover.trailing_stop_atr",
                strategy_configs.get("ema_crossover", {}).get("trailing_stop_atr"),
            ),
            "chart_patterns": (
                "chart_patterns.stop_loss_pct",
                strategy_configs.get("chart_patterns", {}).get("stop_loss_pct"),
            ),
        }

        grouped: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            grouped[str(trade.get("strategy_name", ""))].append(trade)

        suggestions: list[dict] = []
        for strategy_name, strategy_trades in grouped.items():
            if strategy_name not in stop_parameter_map or len(strategy_trades) < 5:
                continue

            current_value = stop_parameter_map[strategy_name][1]
            if current_value is None:
                continue

            pnl_values = [float(trade.get("pnl_pct", 0.0) or 0.0) for trade in strategy_trades]
            winners = [p for p in pnl_values if p > 0]
            losers = [abs(p) for p in pnl_values if p < 0]
            if not losers:
                continue

            win_rate = len(winners) / len(pnl_values)
            avg_win = sum(winners) / len(winners) if winners else 0.0
            avg_loss = sum(losers) / len(losers)
            gross_profit = sum(winners)
            gross_loss = sum(losers)
            profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

            parameter_name, _ = stop_parameter_map[strategy_name]

            if profit_factor < 1.0 and avg_loss > max(avg_win, 0.1):
                suggestions.append(
                    {
                        "source": "trades",
                        "parameter_name": parameter_name,
                        "current_value": float(current_value),
                        "suggested_value": round(float(current_value) * 0.9, 3),
                        "confidence": 0.6,
                        "rationale": (
                            f"{strategy_name} is taking larger losses than its recent winners justify. "
                            "Tighten the stop distance slightly and re-evaluate."
                        ),
                        "metadata": {
                            "win_rate": win_rate,
                            "avg_win_pct": avg_win,
                            "avg_loss_pct": avg_loss,
                            "profit_factor": profit_factor,
                        },
                    }
                )
            elif profit_factor > 1.25 and win_rate > 0.5 and avg_win > avg_loss:
                suggestions.append(
                    {
                        "source": "trades",
                        "parameter_name": parameter_name,
                        "current_value": float(current_value),
                        "suggested_value": round(float(current_value) * 1.1, 3),
                        "confidence": 0.5,
                        "rationale": (
                            f"{strategy_name} is producing healthy trade economics. "
                            "A slightly wider stop may let winners breathe without auto-applying any change yet."
                        ),
                        "metadata": {
                            "win_rate": win_rate,
                            "avg_win_pct": avg_win,
                            "avg_loss_pct": avg_loss,
                            "profit_factor": profit_factor,
                        },
                    }
                )

        return suggestions

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return datetime.fromisoformat(value)
