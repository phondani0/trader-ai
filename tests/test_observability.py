from __future__ import annotations

import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.observability import ObservabilitySettings  # noqa: E402
from trader_ai.observability import configure_observability  # noqa: E402
from trader_ai.observability import logger  # noqa: E402
from trader_ai.observability import metrics  # noqa: E402
from trader_ai.observability import tracer  # noqa: E402
from trader_ai.execution.instrumentation import execution_instrumentation  # noqa: E402


class ObservabilityTest(unittest.TestCase):
    def tearDown(self) -> None:
        for trade_id in list(execution_instrumentation._trade_root_spans):
            execution_instrumentation.end_trade_execution(trade_id=trade_id, result="error")
        configure_observability(ObservabilitySettings(enabled=False))

    def test_settings_defaults_enable_local_collector_export(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = ObservabilitySettings.from_env()

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.service_name, "trader-ai")
        self.assertEqual(settings.environment, "paper")
        self.assertEqual(settings.otlp_endpoint, "http://localhost:4317")
        self.assertTrue(settings.log_json)

    def test_settings_can_disable_observability_with_environment(self):
        with patch.dict(
            os.environ,
            {
                "OBSERVABILITY_ENABLED": "false",
            },
            clear=True,
        ):
            settings = ObservabilitySettings.from_env()

        self.assertFalse(settings.enabled)

    def test_settings_loads_environment_defaults(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_SERVICE_NAME": "trader-ai-test",
                "TRADING_ENV": "test",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317",
                "OBSERVABILITY_ENABLED": "true",
            },
            clear=False,
        ):
            settings = ObservabilitySettings.from_env()

        self.assertEqual(settings.service_name, "trader-ai-test")
        self.assertEqual(settings.environment, "test")
        self.assertEqual(settings.otlp_endpoint, "http://collector:4317")
        self.assertTrue(settings.enabled)

    def test_logger_info_writes_json_with_log_type_and_fields(self):
        output = StringIO()
        configure_observability(
            ObservabilitySettings(
                enabled=False,
                service_name="trader-ai-test",
                environment="test",
                run_id="run-123",
                log_json=True,
            ),
            stdout=output,
        )

        logger.info(
            "Runtime scanner activated",
            log_type="runtime.activation",
            strategy="adaptive_trend_rider",
            symbol="WLD/USDT:USDT",
            activation_count=1,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["message"], "Runtime scanner activated")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["log_type"], "runtime.activation")
        self.assertEqual(payload["service"], "trader-ai-test")
        self.assertEqual(payload["env"], "test")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["strategy"], "adaptive_trend_rider")
        self.assertEqual(payload["symbol"], "WLD/USDT:USDT")
        self.assertEqual(payload["activation_count"], 1)

    def test_logger_info_exports_same_payload_to_opentelemetry_logs(self):
        output = StringIO()
        log_exporter = InMemoryLogRecordExporter()
        configure_observability(
            ObservabilitySettings(
                enabled=True,
                service_name="trader-ai-test",
                environment="test",
                run_id="run-123",
                log_json=True,
            ),
            log_exporter=log_exporter,
            stdout=output,
        )

        logger.info(
            "Runtime scanner activated",
            log_type="runtime.activation",
            strategy="adaptive_trend_rider",
            symbol="WLD/USDT:USDT",
        )

        stdout_payload = json.loads(output.getvalue())
        exported = log_exporter.get_finished_logs()
        self.assertEqual(stdout_payload["log_type"], "runtime.activation")
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0].log_record.body, "Runtime scanner activated")
        self.assertEqual(exported[0].log_record.severity_text, "INFO")
        self.assertEqual(
            exported[0].log_record.attributes,
            {
                "log_type": "runtime.activation",
                "service": "trader-ai-test",
                "env": "test",
                "run_id": "run-123",
                "strategy": "adaptive_trend_rider",
                "symbol": "WLD/USDT:USDT",
            },
        )

    def test_logger_requires_log_type_for_structured_fields(self):
        output = StringIO()
        configure_observability(
            ObservabilitySettings(enabled=False, log_json=True),
            stdout=output,
        )

        with self.assertRaisesRegex(ValueError, "log_type"):
            logger.info("Missing log type", strategy="adaptive_trend_rider")

    def test_metrics_increment_exports_counter_with_attributes(self):
        metric_reader = InMemoryMetricReader()
        configure_observability(
            ObservabilitySettings(enabled=True),
            metric_reader=metric_reader,
        )

        metrics.increment(
            "trader_runtime_activations_total",
            attributes={
                "strategy": "adaptive_trend_rider",
                "symbol": "WLD/USDT:USDT",
            },
        )

        metric_data = metric_reader.get_metrics_data()
        resource_metrics = metric_data.resource_metrics
        data_points = resource_metrics[0].scope_metrics[0].metrics[0].data.data_points
        self.assertEqual(
            resource_metrics[0].scope_metrics[0].metrics[0].name,
            "trader_runtime_activations_total",
        )
        self.assertEqual(data_points[0].value, 1)
        self.assertEqual(
            dict(data_points[0].attributes),
            {
                "strategy": "adaptive_trend_rider",
                "symbol": "WLD/USDT:USDT",
            },
        )

    def test_metrics_record_exports_histogram_with_attributes(self):
        metric_reader = InMemoryMetricReader()
        configure_observability(
            ObservabilitySettings(enabled=True),
            metric_reader=metric_reader,
        )

        metrics.record(
            "trader_scanner_duration_seconds",
            1.25,
            attributes={"result": "success"},
        )

        metric_data = metric_reader.get_metrics_data()
        data = metric_data.resource_metrics[0].scope_metrics[0].metrics[0]
        data_points = data.data.data_points
        self.assertEqual(data.name, "trader_scanner_duration_seconds")
        self.assertEqual(data_points[0].count, 1)
        self.assertEqual(data_points[0].sum, 1.25)
        self.assertEqual(dict(data_points[0].attributes), {"result": "success"})

    def test_tracer_exports_span_with_attributes(self):
        span_exporter = InMemorySpanExporter()
        configure_observability(
            ObservabilitySettings(enabled=True),
            span_exporter=span_exporter,
        )

        with tracer.start_as_current_span(
            "runtime.activation",
            attributes={
                "strategy": "adaptive_trend_rider",
                "symbol": "WLD/USDT:USDT",
            },
        ):
            pass

        spans = span_exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "runtime.activation")
        self.assertEqual(
            spans[0].attributes,
            {
                "strategy": "adaptive_trend_rider",
                "symbol": "WLD/USDT:USDT",
            },
        )

    def test_execution_instrumentation_order_submit_emits_log_metric_and_span(self):
        output = StringIO()
        metric_reader = InMemoryMetricReader()
        span_exporter = InMemorySpanExporter()
        configure_observability(
            ObservabilitySettings(
                enabled=True,
                service_name="trader-ai-test",
                environment="paper",
                run_id="run-123",
                log_json=True,
            ),
            metric_reader=metric_reader,
            span_exporter=span_exporter,
            stdout=output,
        )

        execution_instrumentation.order_submitted(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-123",
            order_id="O-123",
            quantity=0.07,
            notional_usdt=150.0,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["log_type"], "trade.order.submitted")
        self.assertEqual(payload["trade_id"], "T-123")
        self.assertEqual(payload["order_id"], "O-123")
        self.assertEqual(payload["strategy"], "adaptive_trend_rider")
        self.assertEqual(payload["symbol"], "ETH/USDT:USDT")

        metric = self._metric_by_name(metric_reader, "trader_orders_total")
        data_point = metric.data.data_points[0]
        self.assertEqual(data_point.value, 1)
        self.assertEqual(
            dict(data_point.attributes),
            {
                "strategy": "adaptive_trend_rider",
                "symbol": "ETH/USDT:USDT",
                "side": "BUY",
                "result": "submitted",
            },
        )

        spans = span_exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "order.submit")
        self.assertEqual(spans[0].attributes["trade_id"], "T-123")
        self.assertEqual(spans[0].attributes["order_id"], "O-123")
        self.assertEqual(spans[0].attributes["notional_usdt"], 150.0)

    def test_execution_instrumentation_rejection_keeps_raw_ids_out_of_metric_labels(self):
        output = StringIO()
        metric_reader = InMemoryMetricReader()
        configure_observability(
            ObservabilitySettings(enabled=True, log_json=True),
            metric_reader=metric_reader,
            stdout=output,
        )

        execution_instrumentation.order_rejected(
            strategy="squeeze_breakout",
            symbol="SOL/USDT:USDT",
            side="SELL",
            trade_id="T-raw-456",
            order_id="O-raw-456",
            reason="insufficient_margin",
        )

        metric = self._metric_by_name(metric_reader, "trader_order_rejections_total")
        labels = dict(metric.data.data_points[0].attributes)

        self.assertEqual(
            labels,
            {
                "strategy": "squeeze_breakout",
                "symbol": "SOL/USDT:USDT",
                "reason": "insufficient_margin",
            },
        )
        self.assertNotIn("trade_id", labels)
        self.assertNotIn("order_id", labels)
        self.assertEqual(json.loads(output.getvalue())["order_id"], "O-raw-456")

    def test_execution_instrumentation_keeps_execution_spans_in_one_trace(self):
        span_exporter = InMemorySpanExporter()
        configure_observability(
            ObservabilitySettings(enabled=True, log_json=True),
            span_exporter=span_exporter,
            stdout=StringIO(),
        )

        execution_instrumentation.signal_generated(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-trace",
        )
        execution_instrumentation.order_submitted(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-trace",
            order_id="O-trace",
            quantity=0.07,
        )
        execution_instrumentation.position_closed(
            strategy="adaptive_trend_rider",
            symbol="ETH/USDT:USDT",
            side="BUY",
            trade_id="T-trace",
            position_id="P-trace",
            result="closed",
            pnl_usdt=4.2,
        )

        spans_by_name = {span.name: span for span in span_exporter.get_finished_spans()}
        root_span = spans_by_name["trade.execution"]
        child_spans = [
            spans_by_name["signal.generated"],
            spans_by_name["order.submit"],
            spans_by_name["position.close"],
        ]

        self.assertEqual(root_span.attributes["trade_id"], "T-trace")
        self.assertEqual(
            {span.context.trace_id for span in child_spans},
            {root_span.context.trace_id},
        )
        self.assertEqual(
            {span.parent.span_id for span in child_spans},
            {root_span.context.span_id},
        )

    @staticmethod
    def _metric_by_name(metric_reader: InMemoryMetricReader, name: str):
        metric_data = metric_reader.get_metrics_data()
        for resource_metric in metric_data.resource_metrics:
            for scope_metric in resource_metric.scope_metrics:
                for metric in scope_metric.metrics:
                    if metric.name == name:
                        return metric
        raise AssertionError(f"Metric {name} not exported")


if __name__ == "__main__":
    unittest.main()
