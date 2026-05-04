from __future__ import annotations

import json
from pathlib import Path

from trader_ai.config import load_settings
from trader_ai.runners.backtest import build_backtest_components


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"


def build_papertrade_plan(
    *,
    settings_path: str | Path | None,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    data_source: str | None = None,
) -> dict[str, object]:
    settings = (
        load_settings(settings_path)
        if settings_path is not None
        else load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    )
    components = build_backtest_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
    )
    resolved_data_source = data_source or settings.data_source
    return {
        "mode": "papertrade-bootstrap",
        "instrument_name": instrument_name or settings.instrument_name,
        "instrument_id": str(components.instrument.id),
        "strategy": components.strategy_name,
        "data_source": resolved_data_source,
        "venue": str(components.venue),
        "bar_type": str(components.bar_type),
        "ccxt_exchange": components.instrument_spec.ccxt_exchange,
        "ccxt_symbol": components.instrument_spec.ccxt_symbol,
        "ccxt_timeframe": components.instrument_spec.ccxt_timeframe,
    }


def write_papertrade_plan(
    *,
    settings_path: str | Path | None,
    output_dir: str | Path | None = None,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    data_source: str | None = None,
) -> dict[str, object]:
    resolved_output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_papertrade_plan(
        settings_path=settings_path,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
        data_source=data_source,
    )
    plan_path = resolved_output_dir / "papertrade_bootstrap.json"
    template_path = resolved_output_dir / "papertrade_runtime.env"
    plan["plan_path"] = str(plan_path)
    plan["runtime_template_path"] = str(template_path)
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    template_path.write_text(build_runtime_template(), encoding="utf-8")
    return plan


def build_runtime_template() -> str:
    return "\n".join(
        [
            "TRADING_MODE=paper",
            "EXCHANGE_ID=bybit",
            "EXCHANGE_API_KEY=your_key_here",
            "EXCHANGE_SECRET_KEY=your_secret_here",
            "NAUTILUS_VENUE=BYBIT",
            "NAUTILUS_ACCOUNT_TYPE=MARGIN",
            "",
        ]
    )
