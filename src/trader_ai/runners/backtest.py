from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re

import pandas as pd

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from trader_ai.config import InstrumentSpec
from trader_ai.config import NautilusSettings
from trader_ai.config import load_instruments
from trader_ai.config import load_settings
from trader_ai.data import generate_synthetic_bars
from trader_ai.data import load_ccxt_bars
from trader_ai.data import load_csv_bars
from trader_ai.strategies.registry import create_strategy


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
INSTRUMENTS_PATH = PROJECT_ROOT / "config" / "instruments.yaml"


@dataclass(frozen=True)
class BacktestComponents:
    venue: Venue
    instrument: object
    bar_type: BarType
    strategy_name: str
    strategy_config: object
    instrument_spec: InstrumentSpec
    strategy: object = None


def load_default_settings() -> NautilusSettings:
    return load_settings(PROJECT_ROOT / "config" / "settings.yaml")


def build_backtest_components(
    settings: NautilusSettings,
    *,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    fast_ema_period: int | None = None,
    slow_ema_period: int | None = None,
    is_live: bool = False,
) -> BacktestComponents:
    instruments = load_instruments(INSTRUMENTS_PATH)
    resolved_name = instrument_name or settings.instrument_name
    instrument_spec = instruments[resolved_name]
    venue = Venue(instrument_spec.venue)
    instrument = _build_instrument(instrument_spec, venue)
    bar_type = BarType.from_str(instrument_spec.bar_type)
    resolved_strategy_name = strategy_name or settings.strategy_name
    strategy = create_strategy(
        name=resolved_strategy_name,
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=instrument_spec.trade_size or settings.trade_size,
        fast_ema_period=fast_ema_period or settings.fast_ema_period,
        slow_ema_period=slow_ema_period or settings.slow_ema_period,
        use_discovery_journal=is_live,
        ccxt_symbol=instrument_spec.ccxt_symbol,
    )
    return BacktestComponents(
        venue=venue,
        instrument=instrument,
        bar_type=bar_type,
        strategy_name=resolved_strategy_name,
        strategy_config=strategy.config,
        instrument_spec=instrument_spec,
        strategy=strategy,
    )


def run_backtest(
    settings_path: str | Path | None = None,
    *,
    output_dir: str | Path | None = None,
    strategy_name: str | None = None,
    data_source: str | None = None,
    bars_csv: str | Path | None = None,
    instrument_name: str | None = None,
    ccxt_limit: int | None = None,
    cache_dir: str | Path | None = None,
    fast_ema_period: int | None = None,
    slow_ema_period: int | None = None,
) -> dict[str, object]:
    settings = (
        load_settings(settings_path)
        if settings_path is not None
        else load_default_settings()
    )
    resolved_output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    components = build_backtest_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
        fast_ema_period=fast_ema_period,
        slow_ema_period=slow_ema_period,
    )
    resolved_data_source = data_source or settings.data_source
    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    bars = _load_bars(
        data_source=resolved_data_source,
        settings=settings,
        components=components,
        bars_csv=bars_csv,
        instrument_name=instrument_name or settings.instrument_name,
        cache_dir=resolved_cache_dir,
        ccxt_limit=ccxt_limit or settings.ccxt_limit,
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=settings.log_level),
        ),
    )
    tearsheet_path = resolved_output_dir / "demo_backtest_tearsheet.html"
    summary_path = resolved_output_dir / "summary.json"
    positions_path = resolved_output_dir / "positions.csv"
    fills_path = resolved_output_dir / "fills.csv"
    account_path = resolved_output_dir / "account.csv"
    monthly_returns_path = resolved_output_dir / "monthly_returns.csv"

    try:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        engine.add_venue(
            venue=components.venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[
                _parse_balance(
                    components.instrument_spec.starting_balance
                    or settings.starting_balance
                )
            ],
            base_currency=_parse_balance_currency(
                components.instrument_spec.starting_balance or settings.starting_balance
            ),
            default_leverage=Decimal(1),
        )
        engine.add_instrument(components.instrument)
        engine.add_data(bars)
        engine.add_strategy(
            create_strategy(
                name=components.strategy_name,
                instrument_id=components.instrument.id,
                bar_type=components.bar_type,
                trade_size=components.instrument_spec.trade_size or settings.trade_size,
                fast_ema_period=fast_ema_period or settings.fast_ema_period,
                slow_ema_period=slow_ema_period or settings.slow_ema_period,
            )
        )
        engine.run()
        create_tearsheet(
            engine=engine,
            output_path=str(tearsheet_path),
            title="Trader AI Nautilus Demo Backtest",
        )

        positions = engine.trader.generate_positions_report()
        fills = engine.trader.generate_order_fills_report()
        account = engine.trader.generate_account_report(components.venue)
        positions.to_csv(positions_path, index=False)
        fills.to_csv(fills_path, index=False)
        account.to_csv(account_path, index=False)
        monthly_returns = _build_monthly_returns(positions, starting_balance=_parse_balance_amount(
            components.instrument_spec.starting_balance or settings.starting_balance
        ))
        monthly_returns.to_csv(monthly_returns_path, index=False)
        starting_balance = _parse_balance_amount(
            components.instrument_spec.starting_balance or settings.starting_balance
        )
        final_balance = float(account["total"].iloc[-1]) if len(account) else starting_balance
        net_pnl = final_balance - starting_balance
        return_pct = (net_pnl / starting_balance) * 100 if starting_balance else 0.0
        realized_pnl = _sum_realized_pnl(positions)

        summary = {
            "venue": str(components.venue),
            "instrument_id": str(components.instrument.id),
            "bars": len(bars),
            "positions": len(positions),
            "fills": len(fills),
            "account_rows": len(account),
            "tearsheet_path": str(tearsheet_path),
            "positions_path": str(positions_path),
            "fills_path": str(fills_path),
            "account_path": str(account_path),
            "monthly_returns_path": str(monthly_returns_path),
            "summary_path": str(summary_path),
            "strategy": components.strategy_name,
            "data_source": resolved_data_source,
            "cache_dir": str(resolved_cache_dir),
            "fast_ema_period": fast_ema_period or settings.fast_ema_period,
            "slow_ema_period": slow_ema_period or settings.slow_ema_period,
            "starting_balance": starting_balance,
            "final_balance": final_balance,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "realized_pnl_sum": realized_pnl,
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return summary
    finally:
        engine.dispose()


def _build_instrument(instrument_spec: InstrumentSpec, venue: Venue):
    template = getattr(TestInstrumentProvider, instrument_spec.template)
    if instrument_spec.template == "default_fx_ccy":
        return template(instrument_spec.symbol, venue=venue)
    return template()


def _load_bars(
    *,
    data_source: str,
    settings: NautilusSettings,
    components: BacktestComponents,
    bars_csv: str | Path | None,
    instrument_name: str,
    cache_dir: str | Path,
    ccxt_limit: int,
):
    if data_source == "synthetic":
        return generate_synthetic_bars(
            settings=settings,
            instrument=components.instrument,
            bar_type=components.bar_type,
        )
    if data_source == "csv":
        if bars_csv is None:
            raise ValueError("bars_csv is required for csv data source")
        return load_csv_bars(
            csv_path=bars_csv,
            instrument=components.instrument,
            bar_type=components.bar_type,
        )
    if data_source == "ccxt":
        return load_ccxt_bars(
            cache_dir=cache_dir,
            instrument_name=instrument_name,
            instrument_spec=components.instrument_spec,
            instrument=components.instrument,
            bar_type=components.bar_type,
            limit=ccxt_limit,
        )
    raise ValueError(f"Unsupported data source: {data_source}")


def _parse_balance(raw_balance: str) -> Money:
    amount_str, currency = raw_balance.split()
    return Money(Decimal(amount_str), Currency.from_str(currency))


def _parse_balance_currency(raw_balance: str):
    _, currency = raw_balance.split()
    return Currency.from_str(currency)


def _parse_balance_amount(raw_balance: str) -> float:
    amount_str, _ = raw_balance.split()
    return float(amount_str)


def _sum_realized_pnl(positions) -> float:
    if "realized_pnl" not in positions:
        return 0.0
    total = 0.0
    for value in positions["realized_pnl"].tolist():
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if match:
            total += float(match.group(0))
    return total


def _build_monthly_returns(positions, *, starting_balance: float) -> pd.DataFrame:
    if positions.empty or "ts_closed" not in positions or "realized_pnl" not in positions:
        return pd.DataFrame(columns=["month", "realized_pnl", "return_pct"])

    frame = positions.copy()
    frame["ts_closed"] = pd.to_datetime(frame["ts_closed"], utc=True, errors="coerce")
    frame["realized_pnl_value"] = frame["realized_pnl"].apply(
        lambda value: _extract_numeric(str(value))
    )
    frame = frame.dropna(subset=["ts_closed"])
    if frame.empty:
        return pd.DataFrame(columns=["month", "realized_pnl", "return_pct"])
    frame["closed_month"] = frame["ts_closed"].dt.tz_localize(None).dt.to_period("M")
    grouped = (
        frame.groupby("closed_month")["realized_pnl_value"].sum().reset_index()
    )
    grouped["month"] = grouped["closed_month"].astype(str)
    grouped["realized_pnl"] = grouped["realized_pnl_value"]
    grouped["return_pct"] = grouped["realized_pnl"] / starting_balance * 100 if starting_balance else 0.0
    return grouped[["month", "realized_pnl", "return_pct"]]


def _extract_numeric(value: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else 0.0
