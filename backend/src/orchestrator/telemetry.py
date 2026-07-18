"""Safe, backend-agnostic telemetry envelopes and redaction policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from threading import Lock
from typing import Protocol

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_SENSITIVE = re.compile(
    r"(?:api[-_]?key|authorization|bearer|cookie|password|secret|token|prompt|content|path)",
    re.IGNORECASE,
)
_SAFE_KEY = re.compile(
    r"^(?:run_id|stage|node_id|attempt_id|event_type|status|outcome|operation|error_code|retry_count|duration_ms|token_count|queue_wait_ms|approval_wait_ms)$"
)


class TelemetrySink(Protocol):
    def span(self, name: str, attributes: Mapping[str, str | int | bool]) -> None: ...
    def metric(
        self, name: str, value: float, attributes: Mapping[str, str]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SafeTelemetry:
    sink: TelemetrySink
    capture_content: bool = False

    def span(self, name: str, **attributes: str | int | bool) -> None:
        self.sink.span(name, self._attributes(attributes))

    def metric(self, name: str, value: float, **attributes: str) -> None:
        self.sink.metric(name, value, self._metric_attributes(attributes))

    def _attributes(
        self, attributes: Mapping[str, str | int | bool]
    ) -> dict[str, str | int | bool]:
        return {
            key: value
            for key, value in attributes.items()
            if _SAFE_KEY.fullmatch(key) and not _SENSITIVE.search(key)
        }

    def _metric_attributes(self, attributes: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in attributes.items()
            if key in {"stage", "status", "outcome", "operation", "error_code"}
            and not _SENSITIVE.search(key)
        }


class NoopTelemetrySink:
    def span(self, name: str, attributes: Mapping[str, str | int | bool]) -> None:
        del name, attributes

    def metric(self, name: str, value: float, attributes: Mapping[str, str]) -> None:
        del name, value, attributes


class OtelTelemetrySink:
    """OpenTelemetry sink retaining only attributes already accepted as safe."""

    def __init__(self, service_name: str, endpoint: str) -> None:
        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")
            )
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=endpoint.rstrip("/") + "/v1/metrics"
                    )
                )
            ],
        )
        self._tracer = tracer_provider.get_tracer("orchestrator")
        self._meter = meter_provider.get_meter("orchestrator")
        self._instruments: dict[str, Histogram] = {}
        self._lock = Lock()

    def span(self, name: str, attributes: Mapping[str, str | int | bool]) -> None:
        with self._tracer.start_as_current_span(name, attributes=dict(attributes)):
            pass

    def metric(
        self, name: str, value: float, attributes: Mapping[str, str]
    ) -> None:
        with self._lock:
            instrument = self._instruments.get(name)
            if instrument is None:
                instrument = self._meter.create_histogram(name)
                self._instruments[name] = instrument
        instrument.record(value, attributes=dict(attributes))


def configure_telemetry(service_name: str) -> SafeTelemetry:
    endpoint = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return SafeTelemetry(NoopTelemetrySink())
    capture = environ.get("OTEL_CONTENT_CAPTURE", "false").lower() == "true"
    return SafeTelemetry(OtelTelemetrySink(service_name, endpoint), capture)
