from __future__ import annotations

import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.observability import ObservabilitySettings  # noqa: E402
from trader_ai.observability import configure_observability  # noqa: E402
from trader_ai.observability import logger  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
