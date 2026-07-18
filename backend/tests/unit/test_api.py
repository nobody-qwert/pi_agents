"""API factory contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient

from orchestrator.api import ApiServices, create_app
from orchestrator.graph import load_agent_registry
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.projects import ProjectCatalog


class Gateway:
    def __init__(
        self,
        status: Literal[
            "ready", "unreachable", "malformed", "model_unavailable"
        ] = "ready",
    ) -> None:
        self.status = status

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status=self.status, configured_model_id="qwen3.6-27b")

    def complete(
        self, request: ModelRequest, *, cancellation: CancellationToken | None = None
    ) -> ModelResponse:
        raise AssertionError("not used by read-only API")


def client(
    tmp_path: Path,
    status: Literal["ready", "unreachable", "malformed", "model_unavailable"] = "ready",
) -> TestClient:
    root = tmp_path / "projects"
    project = root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("project")
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    return TestClient(
        create_app(ApiServices(registry, ProjectCatalog((root,)), Gateway(status)))
    )


def test_health_readiness_identity_and_safe_project_projections(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.get("/health").json() == {"status": "ok"}
    assert api.get("/ready").json()["status"] == "ready"
    unauthorized = api.get("/api/v1/projects")
    assert unauthorized.status_code == 401
    assert "request_id" in unauthorized.json()
    listed = api.get("/api/v1/projects", headers={"X-Dev-User": "user_example"})
    assert listed.status_code == 200
    project = listed.json()["projects"][0]
    assert project["project_id"].startswith("project_")
    assert "source" not in project
    assert (
        api.get(
            "/api/v1/system/graph", headers={"X-Dev-User": "user_example"}
        ).status_code
        == 200
    )


def test_readiness_returns_safe_unavailable_error(tmp_path: Path) -> None:
    response = client(tmp_path, "model_unavailable").get("/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "model_model_unavailable"


def test_readiness_checks_database_before_model(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    api = TestClient(
        create_app(
            ApiServices(
                registry,
                ProjectCatalog((root,)),
                Gateway(),
                database_ready=lambda: False,
            )
        )
    )

    response = api.get("/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
