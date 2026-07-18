"""Durable disposable-guest lifecycle and sanitized import integration proof."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text

from orchestrator.checkpoints import (
    LocalGuestCheckpointAdapter,
    PostgresCheckpointService,
)
from orchestrator.commands import CommandError, PostgresRunCommandService
from orchestrator.desktop_api import DesktopSessionAuthorizer, PostgresDesktopService
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.sse import PostgresEventStreamStore, SseEventService
from orchestrator.vm import PostgresVmLifecycleService
from orchestrator.workspace import (
    LocalGuestWorkspaceAdapter,
    PostgresWorkspaceImportStore,
    WorkspaceImportService,
)


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
        raise AssertionError("workspace setup does not invoke the model")


class RecordingVmAdapter:
    def __init__(self) -> None:
        self.provisioned: list[tuple[str, str, str]] = []
        self.destroyed: list[tuple[str, str]] = []

    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        self.provisioned.append((run_id, guest_id, overlay_id))

    def probe_ready(self, guest_id: str) -> bool:
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        self.destroyed.append((guest_id, overlay_id))


class NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


def test_lifecycle_and_import_survive_service_restarts(
    postgres_uow: PostgresUnitOfWork, tmp_path: Path
) -> None:
    source_root = tmp_path / "projects"
    source = source_root / "example"
    source.mkdir(parents=True)
    (source / "app.py").write_text("print('isolated')\n")
    (source / ".env").write_text("MUST_NOT_COPY=true\n")
    catalog = ProjectCatalog((source_root,))
    preview = catalog.discover()[0]
    command = PostgresRunCommandService(catalog, ReadyGateway(), postgres_uow).create(
        user_id="user_workspace",
        project_id=preview.project_id,
        message="Build inside a disposable guest",
        idempotency_key="workspace-run",
    )

    adapter = RecordingVmAdapter()
    lifecycle = PostgresVmLifecycleService(adapter, postgres_uow)
    creating = lifecycle.create(command.run_id)
    assert creating.status == "creating"
    assert len(adapter.provisioned) == 1

    restarted_lifecycle = PostgresVmLifecycleService(adapter, postgres_uow)
    assert restarted_lifecycle.create(command.run_id) == creating
    assert len(adapter.provisioned) == 1
    ready = restarted_lifecycle.probe(command.run_id)
    assert ready.status == "ready"

    workspace_id = restarted_lifecycle.workspace_id(command.run_id)
    guest_root = tmp_path / "guest"
    imports = WorkspaceImportService(
        catalog,
        restarted_lifecycle,
        LocalGuestWorkspaceAdapter(guest_root),
        PostgresWorkspaceImportStore(postgres_uow),
    )
    imported = imports.import_snapshot(
        workspace_id=workspace_id,
        run_id=command.run_id,
        project_id=preview.project_id,
        expected_source_fingerprint=preview.source_fingerprint,
    )
    destination = guest_root / ready.guest_id / imported.guest_path
    assert (destination / "app.py").read_text() == "print('isolated')\n"
    assert not (destination / ".env").exists()
    assert imported.baseline.commit_hash

    restarted_store = PostgresWorkspaceImportStore(postgres_uow)
    assert restarted_store.get(workspace_id) == imported
    with postgres_uow.transaction() as unit_of_work:
        row = unit_of_work.connection.execute(
            text(
                "SELECT lifecycle_status, payload ->> 'status' AS workspace_status, "
                "transfer.status AS transfer_status, transfer.completed_at "
                "FROM workspace_sessions JOIN workspace_transfers AS transfer "
                "USING (workspace_id) WHERE workspace_sessions.run_id = :run_id"
            ),
            {"run_id": command.run_id},
        ).one()
    assert row.lifecycle_status == "ready"
    assert row.workspace_status == "ready"
    assert row.transfer_status == "completed"
    assert row.completed_at is not None

    desktop = PostgresDesktopService(
        postgres_uow, "test-desktop-session-secret-000000000000"
    )
    grant = desktop.issue_session(
        run_id=command.run_id,
        user_id="user_workspace",
        idempotency_key="desktop-session",
    )
    replayed_grant = desktop.issue_session(
        run_id=command.run_id,
        user_id="user_workspace",
        idempotency_key="desktop-session",
    )
    assert replayed_grant == grant
    token = parse_qs(urlparse(str(grant["websocket_url"])).query)["token"][0]
    authorizer = DesktopSessionAuthorizer(postgres_uow)
    assert authorizer.consume(
        session_id=str(grant["session_id"]), token=token
    ) == command.run_id
    with pytest.raises(CommandError, match="desktop_session_not_authorized"):
        authorizer.consume(session_id=str(grant["session_id"]), token=token)

    assert desktop.change_owner(
        run_id=command.run_id,
        user_id="user_workspace",
        requested_owner="USER",
        idempotency_key="take-control",
    )["input_owner"] == "USER"
    checkpoints = PostgresCheckpointService(
        imports,
        LocalGuestCheckpointAdapter(guest_root),
        postgres_uow,
        NoopNotifier(),
    )
    checkpoint = checkpoints.create(
        workspace_id=workspace_id,
        checkpoint_id="checkpoint_user_workspace",
        kind="user_accepted",
        design_version=1,
    )
    assert checkpoint.kind == "user_accepted"
    assert desktop.change_owner(
        run_id=command.run_id,
        user_id="user_workspace",
        requested_owner="AGENT",
        idempotency_key="return-control",
    )["input_owner"] == "AGENT"
    replay = SseEventService(PostgresEventStreamStore(postgres_uow)).replay(
        run_id=command.run_id, user_id="user_workspace", after_sequence=0
    )
    assert [event.event_type for event in replay][-4:] == [
        "vm.input_owner_changed",
        "vm.input_owner_changed",
        "workspace.checkpointed",
        "vm.input_owner_changed",
    ]
    with pytest.raises(CommandError, match="workspace_not_ready"):
        desktop.issue_session(
            run_id=command.run_id,
            user_id="user_other",
            idempotency_key="cross-user",
        )

    assert restarted_lifecycle.destroy(command.run_id).status == "destroyed"
    assert restarted_lifecycle.destroy(command.run_id).status == "destroyed"
    assert len(adapter.destroyed) == 1
