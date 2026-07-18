"""Production composition for the isolated writable promotion service."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import ArtifactPolicy
from orchestrator.guest_git import VmManagerGuestGitAdapter
from orchestrator.migrations import upgrade_database
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_manager import create_promotion_manager_app
from orchestrator.promotion_service import PostgresPromotionService
from orchestrator.services.events import PostgresEventWakeupNotifier
from orchestrator.settings import load_settings
from orchestrator.vm_manager import VmManagerHttpAdapter
from orchestrator.workspace import PostgresWorkspaceImportStore


def build_app() -> FastAPI:
    settings = load_settings()
    upgrade_database(
        settings.database_url,
        Path(os.environ.get("ORCHESTRATOR_ALEMBIC_CONFIG", "/app/backend/alembic.ini")),
    )
    roots = tuple(
        Path(value).resolve()
        for value in os.environ["PROJECT_ROOTS"].split(":")
        if value
    )
    unit_of_work = PostgresUnitOfWork(settings.database_url)
    metadata = PostgresArtifactMetadataRepository(settings.database_url)
    vm_client = VmManagerHttpAdapter(
        os.environ["VM_MANAGER_URL"], os.environ["VM_MANAGER_AUTH_TOKEN"]
    )
    service = PostgresPromotionService(
        unit_of_work=unit_of_work,
        catalog=ProjectCatalog(roots),
        imports=PostgresWorkspaceImportStore(unit_of_work),
        guest_git=VmManagerGuestGitAdapter(vm_client),
        artifacts=ArtifactService(
            content_store=LocalVolumeArtifactStore(Path(os.environ["ARTIFACT_ROOT"])),
            metadata_repository=metadata,
            policy=ArtifactPolicy(),
        ),
        review_root=Path(os.environ["PROMOTION_REVIEW_ROOT"]),
        worktree_root=Path(os.environ["PROMOTION_WORKTREE_ROOT"]),
        confirmation_secret=os.environ["PROMOTION_CONFIRMATION_SECRET"],
        notifier=PostgresEventWakeupNotifier(settings.database_url),
    )
    return create_promotion_manager_app(
        service, auth_token=os.environ["PROMOTION_MANAGER_AUTH_TOKEN"]
    )


app = build_app()
