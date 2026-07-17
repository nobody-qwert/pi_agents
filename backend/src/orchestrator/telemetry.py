"""Safe, backend-agnostic telemetry envelopes and redaction policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

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
