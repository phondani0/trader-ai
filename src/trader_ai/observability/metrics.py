from __future__ import annotations

from typing import Any

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from trader_ai.observability.settings import ObservabilitySettings


class Metrics:
    def __init__(self) -> None:
        self._settings = ObservabilitySettings()
        self._meter_provider: MeterProvider | None = None
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

    def configure(
        self,
        settings: ObservabilitySettings,
        *,
        metric_reader: MetricReader | None = None,
    ) -> None:
        self._settings = settings
        self._counters.clear()
        self._histograms.clear()
        self._meter_provider = None

        if not settings.enabled:
            return

        reader = metric_reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=settings.otlp_endpoint or None),
        )
        self._meter_provider = MeterProvider(
            metric_readers=[reader],
            resource=_resource(settings),
        )

    def increment(
        self,
        name: str,
        value: int = 1,
        *,
        attributes: dict[str, str] | None = None,
    ) -> None:
        if self._meter_provider is None:
            return
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter_provider.get_meter("trader_ai").create_counter(name)
            self._counters[name] = counter
        counter.add(value, attributes or {})

    def record(
        self,
        name: str,
        value: float,
        *,
        attributes: dict[str, str] | None = None,
    ) -> None:
        if self._meter_provider is None:
            return
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self._meter_provider.get_meter("trader_ai").create_histogram(name)
            self._histograms[name] = histogram
        histogram.record(value, attributes or {})


metrics = Metrics()


def _resource(settings: ObservabilitySettings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.environment,
            "trader.run_id": settings.run_id,
        }
    )
