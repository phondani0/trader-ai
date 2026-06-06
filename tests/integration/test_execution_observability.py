from __future__ import annotations

from io import StringIO

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from trader_ai.execution.instrumentation import execution_instrumentation
from trader_ai.observability import ObservabilitySettings
from trader_ai.observability import configure_observability


def test_execution_observability_smoke_emits_metrics_logs_and_single_trace():
    output = StringIO()
    metric_reader = InMemoryMetricReader()
    span_exporter = InMemorySpanExporter()
    configure_observability(
        ObservabilitySettings(
            enabled=True,
            service_name="trader-ai-integration-test",
            environment="paper",
            log_json=True,
        ),
        metric_reader=metric_reader,
        span_exporter=span_exporter,
        stdout=output,
    )

    try:
        execution_instrumentation.signal_generated(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-integration",
            entry_price=2116.85,
        )
        execution_instrumentation.order_submitted(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-integration",
            order_id="O-integration",
            quantity=0.07,
            notional_usdt=148.18,
        )
        execution_instrumentation.order_filled(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-integration",
            order_id="O-integration",
            quantity=0.07,
            fill_price=2117.10,
        )
        execution_instrumentation.position_closed(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-integration",
            position_id="P-integration",
            result="closed",
            pnl_usdt=3.25,
            fees_usdt=0.16,
            slippage_bps=1.2,
            lifetime_seconds=900,
        )

        metric_names = {
            metric.name
            for resource_metric in metric_reader.get_metrics_data().resource_metrics
            for scope_metric in resource_metric.scope_metrics
            for metric in scope_metric.metrics
        }
        assert {
            "trader_signals_total",
            "trader_orders_total",
            "trader_order_fills_total",
            "trader_positions_closed_total",
            "trader_trade_pnl_usdt",
            "trader_trade_fees_usdt",
            "trader_trade_slippage_bps",
            "trader_trade_lifetime_seconds",
        } <= metric_names

        logs = output.getvalue()
        assert '"log_type":"trade.signal.generated"' in logs
        assert '"log_type":"trade.order.submitted"' in logs
        assert '"log_type":"trade.order.filled"' in logs
        assert '"log_type":"trade.position.closed"' in logs

        spans_by_name = {span.name: span for span in span_exporter.get_finished_spans()}
        root_span = spans_by_name["trade.execution"]
        child_spans = [
            spans_by_name["signal.generated"],
            spans_by_name["order.submit"],
            spans_by_name["order.fill"],
            spans_by_name["position.close"],
        ]
        assert {span.context.trace_id for span in child_spans} == {
            root_span.context.trace_id
        }
        assert {span.parent.span_id for span in child_spans} == {
            root_span.context.span_id
        }
    finally:
        execution_instrumentation.end_trade_execution(
            trade_id="T-integration",
            result="error",
        )
        configure_observability(ObservabilitySettings(enabled=False))
