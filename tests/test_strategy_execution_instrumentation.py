from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_FILES = [
    PROJECT_ROOT / "src" / "trader_ai" / "strategies" / "adaptive_trend_rider.py",
    PROJECT_ROOT / "src" / "trader_ai" / "strategies" / "volume_spike_trend.py",
    PROJECT_ROOT / "src" / "trader_ai" / "strategies" / "squeeze_breakout.py",
    PROJECT_ROOT / "src" / "trader_ai" / "strategies" / "macd_demo.py",
]


def test_every_strategy_records_market_order_submission_execution_instrumentation():
    missing = []
    for strategy_file in STRATEGY_FILES:
        source = strategy_file.read_text(encoding="utf-8")
        submit_methods = source.count("def _submit_market")
        instrumentation_calls = source.count("execution_instrumentation.order_submitted")
        if instrumentation_calls < submit_methods:
            missing.append(
                f"{strategy_file.relative_to(PROJECT_ROOT)} "
                f"has {instrumentation_calls} execution instrumentation calls for {submit_methods} submit methods"
            )

    assert not missing


def test_every_strategy_records_signal_generation_before_opening_position():
    missing = []
    for strategy_file in STRATEGY_FILES:
        source = strategy_file.read_text(encoding="utf-8")
        open_methods = source.count("def _open_position")
        signal_calls = source.count("execution_instrumentation.signal_generated")
        if signal_calls < open_methods:
            missing.append(
                f"{strategy_file.relative_to(PROJECT_ROOT)} "
                f"has {signal_calls} signal calls for {open_methods} open methods"
            )

    assert not missing


def test_every_strategy_records_internal_position_close_request_reason():
    missing = []
    for strategy_file in STRATEGY_FILES:
        source = strategy_file.read_text(encoding="utf-8")
        close_methods = source.count("def _close_and_reset")
        close_request_calls = source.count("execution_instrumentation.position_close_requested")
        if close_request_calls < close_methods:
            missing.append(
                f"{strategy_file.relative_to(PROJECT_ROOT)} "
                f"has {close_request_calls} close request instrumentation calls for {close_methods} close methods"
            )

    assert not missing
