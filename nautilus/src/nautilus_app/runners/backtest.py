from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from nautilus_app.config import NautilusSettings
from nautilus_app.config import load_settings
from nautilus_app.strategies.macd_demo import DemoEMACrossConfig
from nautilus_app.strategies.macd_demo import DemoEMACrossStrategy


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"


@dataclass(frozen=True)
class DemoBacktestComponents:
    venue: Venue
    instrument: object
    bar_type: BarType
    strategy_config: DemoEMACrossConfig


def load_default_settings() -> NautilusSettings:
    return load_settings(PROJECT_ROOT / "config" / "settings.yaml")


def build_demo_components(settings: NautilusSettings) -> DemoBacktestComponents:
    venue = Venue(settings.venue)
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD", venue=venue)
    bar_type = BarType.from_str(settings.bar_type)
    strategy_config = DemoEMACrossConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=Decimal(settings.trade_size),
        fast_ema_period=settings.fast_ema_period,
        slow_ema_period=settings.slow_ema_period,
    )
    return DemoBacktestComponents(
        venue=venue,
        instrument=instrument,
        bar_type=bar_type,
        strategy_config=strategy_config,
    )


def generate_synthetic_bars(
    *,
    settings: NautilusSettings,
    instrument,
    bar_type: BarType,
):
    rng = np.random.default_rng(settings.random_seed)
    n = settings.bars_count
    price = 1.10 + np.cumsum(rng.normal(0, 0.0002, n))
    spread = np.abs(rng.normal(0, 0.0003, n))

    bars_df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread,
            "low": price - spread,
            "close": price + rng.normal(0, 0.00005, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    bars_df["high"] = bars_df[["open", "high", "close"]].max(axis=1)
    bars_df["low"] = bars_df[["open", "low", "close"]].min(axis=1)

    return BarDataWrangler(bar_type, instrument).process(bars_df)


def run_backtest(settings_path: str | Path | None = None) -> dict[str, object]:
    settings = (
        load_settings(settings_path)
        if settings_path is not None
        else load_default_settings()
    )
    components = build_demo_components(settings)
    bars = generate_synthetic_bars(
        settings=settings,
        instrument=components.instrument,
        bar_type=components.bar_type,
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=settings.log_level),
        ),
    )
    tearsheet_path = OUTPUT_DIR / "demo_backtest_tearsheet.html"

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        engine.add_venue(
            venue=components.venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[_parse_balance(settings.starting_balance)],
            base_currency=USD,
            default_leverage=Decimal(1),
        )
        engine.add_instrument(components.instrument)
        engine.add_data(bars)
        engine.add_strategy(DemoEMACrossStrategy(components.strategy_config))
        engine.run()
        create_tearsheet(
            engine=engine,
            output_path=str(tearsheet_path),
            title="Trader AI Nautilus Demo Backtest",
        )

        positions = engine.trader.generate_positions_report()
        fills = engine.trader.generate_order_fills_report()
        account = engine.trader.generate_account_report(components.venue)

        return {
            "venue": str(components.venue),
            "instrument_id": str(components.instrument.id),
            "bars": len(bars),
            "positions": len(positions),
            "fills": len(fills),
            "account_rows": len(account),
            "tearsheet_path": str(tearsheet_path),
        }
    finally:
        engine.dispose()


def _parse_balance(raw_balance: str) -> Money:
    amount_str, currency = raw_balance.split()
    if currency != "USD":
        raise ValueError(f"Unsupported balance currency: {currency}")
    return Money(Decimal(amount_str), USD)
