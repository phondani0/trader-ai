from __future__ import annotations

import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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


class ObservabilityTest(unittest.TestCase):
    def tearDown(self) -> None:
        configure_observability(ObservabilitySettings(enabled=False))

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


if __name__ == "__main__":
    unittest.main()
