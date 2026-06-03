from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter

from trader_ai.observability.settings import ObservabilitySettings


class Tracer:
    def __init__(self) -> None:
        self._provider: TracerProvider | None = None
        self._tracer = None

    def configure(
        self,
        settings: ObservabilitySettings,
        *,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self._provider = None
        self._tracer = None
        if not settings.enabled:
            return

        exporter = span_exporter or OTLPSpanExporter(endpoint=settings.otlp_endpoint or None)
        processor = (
            SimpleSpanProcessor(exporter)
            if span_exporter is not None
            else BatchSpanProcessor(exporter)
        )
        self._provider = TracerProvider(resource=_resource(settings))
        self._provider.add_span_processor(processor)
        self._tracer = self._provider.get_tracer("trader_ai")

    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
    ):
        if self._tracer is None:
            return nullcontext()
        return self._tracer.start_as_current_span(name, attributes=attributes or {})


tracer = Tracer()


def _resource(settings: ObservabilitySettings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.environment,
            "trader.run_id": settings.run_id,
        }
    )
