from collections.abc import Mapping
from dataclasses import dataclass, field

from orchestrator.telemetry import SafeTelemetry


@dataclass
class Sink:
    spans: list[dict[str, object]] = field(default_factory=list)
    metrics: list[dict[str, object]] = field(default_factory=list)

    def span(self, name: str, attributes: Mapping[str, str | int | bool]) -> None:
        self.spans.append({"name": name, **attributes})

    def metric(self, name: str, value: float, attributes: Mapping[str, str]) -> None:
        self.metrics.append({"name": name, "value": value, **attributes})


def test_telemetry_drops_content_credentials_paths_and_unbounded_labels() -> None:
    sink = Sink()
    telemetry = SafeTelemetry(sink)
    telemetry.span(
        "runner.stage",
        run_id="run_safe",
        stage="EXECUTE",
        duration_ms=12,
        prompt="hidden thought",
        authorization="Bearer hidden",
        path="/host/project",
        arbitrary_label="high-cardinality",
    )
    telemetry.metric(
        "orchestrator.stage.duration",
        12,
        stage="EXECUTE",
        status="passed",
        run_id="run_safe",
        token="hidden",
    )
    assert sink.spans == [
        {
            "name": "runner.stage",
            "run_id": "run_safe",
            "stage": "EXECUTE",
            "duration_ms": 12,
        }
    ]
    assert sink.metrics == [
        {
            "name": "orchestrator.stage.duration",
            "value": 12,
            "stage": "EXECUTE",
            "status": "passed",
        }
    ]
