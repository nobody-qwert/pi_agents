"""Standalone lease-owning runner process with a small health endpoint."""

from __future__ import annotations

import os
import signal
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType

from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import ArtifactPolicy
from orchestrator.checkpoints import PostgresCheckpointService
from orchestrator.graph import load_agent_registry
from orchestrator.guest_git import VmManagerGuestGitAdapter
from orchestrator.migrations import upgrade_database
from orchestrator.model_gateway import LmStudioGateway
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.runner.application_stages import ProductionPreDeliveryStagePort
from orchestrator.runner.coordinator import RunnerCoordinator
from orchestrator.runner.leases import PostgresRunLeaseQueue
from orchestrator.runner.service import RunnerService
from orchestrator.services.events import PostgresEventWakeupNotifier
from orchestrator.settings import load_settings
from orchestrator.telemetry import configure_telemetry
from orchestrator.vm import PostgresVmLifecycleService
from orchestrator.vm_manager import (
    VmManagerHttpAdapter,
    VmManagerPiRpcHttpAdapter,
    VmManagerWorkspaceHttpAdapter,
)
from orchestrator.workspace import (
    PostgresWorkspaceImportStore,
    WorkspaceImportService,
)


class _HealthHandler(BaseHTTPRequestHandler):
    ready: threading.Event

    def do_GET(self) -> None:
        if self.path == "/live" or (self.path == "/ready" and self.ready.is_set()):
            self.send_response(200)
        else:
            self.send_response(503 if self.path == "/ready" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = (
            b'{"status":"ready"}' if self.ready.is_set() else b'{"status":"starting"}'
        )
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
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
    catalog = ProjectCatalog(roots)
    gateway = LmStudioGateway(settings)
    registry = load_agent_registry(
        Path(os.environ.get("ORCHESTRATOR_CONFIG_ROOT", "/app/config"))
    )
    unit_of_work = PostgresUnitOfWork(settings.database_url)
    notifier = PostgresEventWakeupNotifier(settings.database_url)
    vm_client = VmManagerHttpAdapter(
        os.environ["VM_MANAGER_URL"], os.environ["VM_MANAGER_AUTH_TOKEN"]
    )
    lifecycle = PostgresVmLifecycleService(vm_client, unit_of_work)
    imports = WorkspaceImportService(
        catalog,
        lifecycle,
        VmManagerWorkspaceHttpAdapter(vm_client),
        PostgresWorkspaceImportStore(unit_of_work),
    )
    guest_git = VmManagerGuestGitAdapter(vm_client)
    checkpoints = PostgresCheckpointService(
        imports, guest_git, unit_of_work, notifier
    )
    metadata = PostgresArtifactMetadataRepository(settings.database_url)
    artifacts = ArtifactService(
        content_store=LocalVolumeArtifactStore(
            Path(os.environ.get("ARTIFACT_ROOT", "/var/lib/orchestrator/artifacts"))
        ),
        metadata_repository=metadata,
        policy=ArtifactPolicy(),
    )
    queue = PostgresRunLeaseQueue(
        settings.database_url,
        lease_duration=timedelta(
            seconds=int(os.environ.get("RUNNER_LEASE_SECONDS", "600"))
        ),
    )
    coordinator = RunnerCoordinator(unit_of_work, queue, notifier=notifier)
    telemetry = configure_telemetry("orchestrator-runner")
    stage_port = ProductionPreDeliveryStagePort(
        unit_of_work=unit_of_work,
        registry=registry,
        gateway=gateway,
        artifacts=artifacts,
        notifier=notifier,
        lifecycle=lifecycle,
        imports=imports,
        checkpoints=checkpoints,
        guest_outputs=guest_git,
        pi_port=VmManagerPiRpcHttpAdapter(
            vm_client,
            attempt_timeout_seconds=int(
                os.environ.get("PI_GUEST_CLIENT_TIMEOUT_SECONDS", "330")
            ),
        ),
        guest_model_id=settings.lm_studio_model_id,
        guest_ready_timeout_seconds=int(
            os.environ.get("PI_GUEST_READY_TIMEOUT_SECONDS", "45")
        ),
        telemetry=telemetry,
    )
    runner = RunnerService(
        database_url=settings.database_url,
        owner=os.environ.get("RUNNER_OWNER", "runner-compose-1"),
        lease_queue=queue,
        coordinator=coordinator,
        stage_port=stage_port,
    )
    stop = threading.Event()
    ready = threading.Event()
    _HealthHandler.ready = ready
    server = ThreadingHTTPServer(("0.0.0.0", 8020), _HealthHandler)
    health_thread = threading.Thread(target=server.serve_forever, daemon=True)
    health_thread.start()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        runner.initialize()
        ready.set()
        while not stop.is_set():
            started = time.monotonic()
            result = runner.run_next()
            duration_ms = int((time.monotonic() - started) * 1000)
            telemetry.span(
                "runner.poll",
                run_id=result.run_id or "run_none",
                outcome=result.outcome,
                duration_ms=duration_ms,
            )
            telemetry.metric(
                "runner.poll.duration",
                float(duration_ms),
                outcome=result.outcome,
            )
            if result.outcome == "unavailable":
                stop.wait(0.5)
    finally:
        ready.clear()
        server.shutdown()
        server.server_close()
        queue.close()
        metadata.close()
        unit_of_work.close()


if __name__ == "__main__":
    main()
