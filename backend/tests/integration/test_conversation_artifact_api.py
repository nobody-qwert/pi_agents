"""Durable conversation continuation and owned artifact API integration proof."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from orchestrator.api import ApiServices, create_app
from orchestrator.artifact_api import PostgresArtifactApiService
from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import (
    ArtifactPolicy,
    ArtifactPublishRequest,
    ArtifactScope,
)
from orchestrator.commands import PostgresRunCommandService
from orchestrator.conversations import PostgresConversationService
from orchestrator.domain import ArtifactRecord, AuthenticatedActor, RecordMetadata
from orchestrator.graph import load_agent_registry
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog


class ReadyGateway:
    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status="ready", configured_model_id="qwen3.6-27b")

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        raise AssertionError("API commands do not invoke the model")


def _client(
    database_url: str, tmp_path: Path
) -> tuple[TestClient, PostgresUnitOfWork, PostgresArtifactMetadataRepository, str]:
    project_root = tmp_path / "projects"
    project = project_root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("conversation API project")
    projects = ProjectCatalog((project_root,))
    project_id = projects.discover()[0].project_id
    unit_of_work = PostgresUnitOfWork(database_url)
    commands = PostgresRunCommandService(projects, ReadyGateway(), unit_of_work)
    conversations = PostgresConversationService(unit_of_work, commands)
    metadata = PostgresArtifactMetadataRepository(database_url)
    artifacts = ArtifactService(
        content_store=LocalVolumeArtifactStore(tmp_path / "artifacts"),
        metadata_repository=metadata,
        policy=ArtifactPolicy(max_content_bytes=1_048_576),
    )
    app = create_app(
        ApiServices(
            registry=load_agent_registry(Path(__file__).parents[3] / "config"),
            projects=projects,
            gateway=ReadyGateway(),
            commands=commands,
            conversations=conversations,
            artifact_reader=PostgresArtifactApiService(unit_of_work, artifacts),
        )
    )
    return TestClient(app), unit_of_work, metadata, project_id


def test_conversation_continuation_and_owned_artifact_reads(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    api, unit_of_work, metadata, project_id = _client(
        migrated_postgres_database, tmp_path
    )
    owner = {"X-Dev-User": "user_conversation_api"}
    other = {"X-Dev-User": "user_other_api"}
    try:
        assert api.get("/api/v1/conversations").status_code == 401
        created = api.post(
            "/api/v1/conversations",
            headers={**owner, "Idempotency-Key": "new-thread"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation_id"]
        assert (
            api.post(
                "/api/v1/conversations",
                headers={**owner, "Idempotency-Key": "new-thread"},
            ).json()["conversation_id"]
            == conversation_id
        )

        ordinary = api.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={**owner, "Idempotency-Key": "context-message"},
            json={"content": "Keep this durable context."},
        )
        assert ordinary.status_code == 202
        assert ordinary.json()["message"]["sequence"] == 1
        assert (
            api.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={**owner, "Idempotency-Key": "context-message"},
                json={"content": "Keep this durable context."},
            ).json()
            == ordinary.json()
        )
        conflict = api.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={**owner, "Idempotency-Key": "context-message"},
            json={"content": "A conflicting retry."},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"

        run_message = api.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={**owner, "Idempotency-Key": "start-run"},
            json={"content": "Implement the next change.", "project_id": project_id},
        )
        assert run_message.status_code == 202
        run_id = run_message.json()["run_id"]
        thread = api.get(
            f"/api/v1/conversations/{conversation_id}", headers=owner
        ).json()
        assert [message["sequence"] for message in thread["messages"]] == [1, 2]
        assert thread["run_ids"] == [run_id]
        assert api.get(
            f"/api/v1/conversations/{conversation_id}", headers=other
        ).status_code == 404

        content = b"# Validated result\n\nNo internal storage locator is exposed.\n"
        published = ArtifactService(
            content_store=LocalVolumeArtifactStore(tmp_path / "artifacts"),
            metadata_repository=metadata,
            policy=ArtifactPolicy(max_content_bytes=1_048_576),
        ).publish(
            ArtifactPublishRequest(
                artifact_id="art_conversation_api",
                scope=ArtifactScope(
                    tenant_id="tenant_local",
                    run_id=run_id,
                    allowed_roles=("operator",),
                ),
                media_type="text/markdown",
                expected_version=0,
            ),
            content,
        )
        now = datetime.now(UTC)
        actor = AuthenticatedActor(
            actor_id="service_artifact_api",
            kind="service",
            role="artifact-publisher",
            authenticated_at=now,
            authentication_context="integration-test",
        )
        with unit_of_work.transaction() as transaction:
            transaction.artifacts.add(
                ArtifactRecord(
                    artifact_id=published.artifact_id,
                    run_id=run_id,
                    logical_name="validated-result",
                    version=published.version,
                    media_type=published.media_type,
                    storage_locator=published.storage_key,
                    sha256=published.content_sha256,
                    producer=actor,
                    access_policy=("operator",),
                    metadata=RecordMetadata(
                        record_version=1,
                        created_at=now,
                        updated_at=now,
                        idempotency_key="artifact-api-record",
                        trace_id="0123456789abcdef0123456789abcdef",
                    ),
                )
            )

        preview = api.get("/api/v1/artifacts/art_conversation_api", headers=owner)
        assert preview.status_code == 200
        assert preview.json()["preview"] == content.decode()
        assert "storage_locator" not in preview.text
        assert "storage_key" not in preview.text
        download = api.get(
            "/api/v1/artifacts/art_conversation_api?download=true", headers=owner
        )
        assert download.status_code == 200
        assert download.content == content
        assert download.headers["content-type"].startswith("text/markdown")
        assert api.get(
            "/api/v1/artifacts/art_conversation_api", headers=other
        ).status_code == 404
    finally:
        metadata.close()
        unit_of_work.close()
