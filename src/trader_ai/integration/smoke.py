from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from trader_ai.config import load_settings
from trader_ai.data import generate_synthetic_bars
from trader_ai.runners import live


DEFAULT_RUNTIME_CANDIDATES: dict[str, str] = {
    "adaptive_trend_rider": "FARTCOIN/USDT:USDT",
    "volume_spike_trend": "WLD/USDT:USDT",
    "squeeze_breakout": "RENDER/USDT:USDT",
    "demo_ema_cross": "SOL/USDT:USDT",
}


@dataclass(frozen=True)
class IntegrationSmokeReport:
    mode: str
    initial_components: list[tuple[str, str]]
    runtime_strategy_names: list[str]
    activated_symbols_by_strategy: dict[str, list[str]]
    failures: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_text(self) -> str:
        lines = [
            f"Integration smoke mode: {self.mode}",
            "Initial components:",
        ]
        lines.extend(
            f"- {strategy_name}: {symbol}"
            for strategy_name, symbol in self.initial_components
        )
        lines.append("Runtime strategies:")
        lines.extend(f"- {name}" for name in self.runtime_strategy_names)
        lines.append("Runtime activations:")
        for strategy_name in self.runtime_strategy_names:
            symbols = self.activated_symbols_by_strategy.get(strategy_name, [])
            rendered = ", ".join(symbols) if symbols else "none"
            lines.append(f"- {strategy_name}: {rendered}")
        if self.failures:
            lines.append("Failures:")
            lines.extend(f"- {failure}" for failure in self.failures)
        else:
            lines.append("Result: PASS")
        return "\n".join(lines)


class _RunningNode:
    is_running = True


def run_local_sim_runtime_smoke(
    *,
    settings_path: str | Path | None = None,
    runtime_candidates: dict[str, str] | None = None,
) -> IntegrationSmokeReport:
    settings = (
        load_settings(settings_path)
        if settings_path is not None
        else load_settings(live.PROJECT_ROOT / "config" / "settings.yaml")
    )
    components = live.build_live_components(settings)
    initial_components = [
        (component.strategy_name, component.instrument_spec.ccxt_symbol or "")
        for component in components
    ]
    runtime_strategies = {}
    for component in components:
        if callable(getattr(component.strategy, "activate_component", None)):
            _install_local_sim_subscription_recorder(component.strategy)
            runtime_strategies.setdefault(component.strategy_name, component.strategy)

    candidate_map = runtime_candidates or DEFAULT_RUNTIME_CANDIDATES
    active_symbols = live.component_ccxt_symbols(components)
    activated_by_strategy: dict[str, list[str]] = {}
    failures: list[str] = []

    for strategy_name in sorted(runtime_strategies):
        symbol = candidate_map.get(strategy_name)
        if symbol is None:
            failures.append(f"{strategy_name}: no runtime candidate configured")
            activated_by_strategy[strategy_name] = []
            continue

        activated = live.activate_runtime_scanner_symbols(
            settings=settings,
            scanner=None,
            node=_RunningNode(),
            active_symbols=active_symbols,
            candidates=[
                {
                    "symbol": symbol,
                    "score": 999.0,
                    "volume_24h": 1_000_000_000,
                }
            ],
            warmer=_synthetic_warmer(settings),
            runtime_strategies={strategy_name: runtime_strategies[strategy_name]},
        )
        activated_symbols = [
            component.instrument_spec.ccxt_symbol or "" for component in activated
        ]
        activated_by_strategy[strategy_name] = activated_symbols
        if symbol not in activated_symbols:
            failures.append(
                f"{strategy_name}: expected runtime activation for {symbol}, "
                f"got {activated_symbols or 'none'}"
            )

    return IntegrationSmokeReport(
        mode="local-sim",
        initial_components=initial_components,
        runtime_strategy_names=sorted(runtime_strategies),
        activated_symbols_by_strategy=activated_by_strategy,
        failures=failures,
    )


def build_bybit_demo_environment() -> dict[str, str]:
    trading_mode = os.environ.get("TRADING_MODE", "paper").lower()
    if trading_mode == "live":
        raise RuntimeError("integration-smoke refuses TRADING_MODE=live")

    api_key = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret = os.environ.get("EXCHANGE_SECRET_KEY", "")
    if not api_key or not api_secret:
        raise RuntimeError(
            "bybit-demo integration smoke requires EXCHANGE_API_KEY "
            "and EXCHANGE_SECRET_KEY"
        )

    return {
        **os.environ,
        "TRADING_MODE": "paper",
        "EXCHANGE_ID": "bybit",
        "EXCHANGE_API_KEY": api_key,
        "EXCHANGE_SECRET_KEY": api_secret,
    }


def _install_local_sim_subscription_recorder(strategy) -> None:
    subscriptions: list[str] = []

    def subscribe_bars(bar_type) -> None:
        subscriptions.append(str(bar_type))

    strategy.subscribe_bars = subscribe_bars
    strategy._integration_smoke_subscriptions = subscriptions


def _synthetic_warmer(settings):
    def warmer(
        *,
        instrument_name,
        instrument_spec,
        instrument,
        bar_type,
        limit,
        cache_dir,
    ):
        return generate_synthetic_bars(
            settings=settings,
            instrument=instrument,
            bar_type=bar_type,
        )[:limit]

    return warmer
