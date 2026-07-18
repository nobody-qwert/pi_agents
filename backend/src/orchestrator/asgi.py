"""Container entrypoint that composes only configured trusted boundaries."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import text

from orchestrator.api import ApiServices, create_app
from orchestrator.approvals import PostgresApprovalService
from orchestrator.artifact_api import PostgresArtifactApiService
from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import ArtifactPolicy
from orchestrator.checkpoints import PostgresCheckpointService
from orchestrator.commands import PostgresRunCommandService
from orchestrator.conversations import PostgresConversationService
from orchestrator.desktop_api import PostgresDesktopService
from orchestrator.graph import load_agent_registry
from orchestrator.guest_git import VmManagerGuestGitAdapter
from orchestrator.migrations import upgrade_database
from orchestrator.model_gateway import LmStudioGateway
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_manager import PromotionManagerHttpAdapter
from orchestrator.run_queries import PostgresRunQueryService
from orchestrator.services.events import PostgresEventWakeupNotifier
from orchestrator.settings import load_settings
from orchestrator.sse import PostgresEventStreamStore, SseEventService
from orchestrator.telemetry import configure_telemetry
from orchestrator.vm import PostgresVmLifecycleService
from orchestrator.vm_manager import (
    VmManagerHttpAdapter,
    VmManagerPreviewHttpAdapter,
    VmManagerWorkspaceHttpAdapter,
)
from orchestrator.workspace import (
    PostgresWorkspaceImportStore,
    WorkspaceImportService,
)
from orchestrator.workspace_api import PostgresWorkspaceApiService


def build_app() -> FastAPI:
    settings = load_settings()
    alembic_config = Path(
        os.environ.get(
            "ORCHESTRATOR_ALEMBIC_CONFIG",
            Path(__file__).resolve().parents[2] / "alembic.ini",
        )
    )
    upgrade_database(settings.database_url, alembic_config)
    roots = tuple(
        Path(value).resolve()
        for value in os.environ["PROJECT_ROOTS"].split(":")
        if value
    )
    projects = ProjectCatalog(roots)
    gateway = LmStudioGateway(settings)
    registry = load_agent_registry(
        Path(os.environ.get("ORCHESTRATOR_CONFIG_ROOT", "/app/config"))
    )
    unit_of_work = PostgresUnitOfWork(settings.database_url)
    notifier = PostgresEventWakeupNotifier(settings.database_url)
    commands = PostgresRunCommandService(
        projects, gateway, unit_of_work, notifier=notifier
    )
    vm_client = VmManagerHttpAdapter(
        os.environ["VM_MANAGER_URL"], os.environ["VM_MANAGER_AUTH_TOKEN"]
    )
    lifecycle = PostgresVmLifecycleService(vm_client, unit_of_work)
    import_store = PostgresWorkspaceImportStore(unit_of_work)
    imports = WorkspaceImportService(
        projects,
        lifecycle,
        VmManagerWorkspaceHttpAdapter(vm_client),
        import_store,
    )
    guest_git = VmManagerGuestGitAdapter(vm_client)
    checkpoints = PostgresCheckpointService(
        imports,
        guest_git,
        unit_of_work,
        notifier,
    )
    workspace_api = PostgresWorkspaceApiService(
        unit_of_work,
        lifecycle,
        imports,
        import_store,
        checkpoints,
        VmManagerPreviewHttpAdapter(vm_client),
        tuple(
            int(value.strip())
            for value in os.environ.get(
                "PI_PREVIEW_PORTS", "3000,4173,5173,8000,8080"
            ).split(",")
            if value.strip()
        ),
        os.environ["DESKTOP_SESSION_SECRET"],
        guest_git,
    )
    artifact_service = ArtifactService(
        content_store=LocalVolumeArtifactStore(Path(os.environ["ARTIFACT_ROOT"])),
        metadata_repository=PostgresArtifactMetadataRepository(settings.database_url),
        policy=ArtifactPolicy(),
    )

    def database_ready() -> bool:
        try:
            with unit_of_work.transaction() as transaction:
                transaction.connection.execute(text("SELECT 1")).scalar_one()
            return True
        except Exception:
            return False

    return create_app(
        ApiServices(
            registry,
            projects,
            gateway,
            commands,
            SseEventService(PostgresEventStreamStore(unit_of_work)),
            database_ready,
            workspace_api,
            PostgresRunQueryService(unit_of_work),
            configure_telemetry("orchestrator-api"),
            PostgresApprovalService(unit_of_work, notifier),
            PostgresDesktopService(
                unit_of_work,
                os.environ["DESKTOP_SESSION_SECRET"],
                notifier=notifier,
            ),
            PromotionManagerHttpAdapter(
                os.environ["PROMOTION_MANAGER_URL"],
                os.environ["PROMOTION_MANAGER_AUTH_TOKEN"],
            ),
            PostgresConversationService(unit_of_work, commands),
            PostgresArtifactApiService(unit_of_work, artifact_service),
        )
    )


app = build_app()
