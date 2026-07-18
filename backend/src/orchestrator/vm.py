"""Narrow, run-scoped disposable-guest lifecycle boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, cast

from sqlalchemy import text

from orchestrator.domain.authoritative import WorkspaceRecord
from orchestrator.domain.primitives import RecordMetadata
from orchestrator.persistence import DuplicateRecordError, PostgresUnitOfWork


class VmLifecycleError(Exception):
    """A lifecycle operation could not safely target the requested run."""


@dataclass(frozen=True, slots=True)
class GuestHandle:
    run_id: str
    guest_id: str
    overlay_id: str
    status: Literal["creating", "ready", "failed", "destroying", "destroyed"]


LifecycleStatus = Literal["creating", "ready", "failed", "destroying", "destroyed"]


@dataclass(frozen=True, slots=True)
class _LifecycleRow:
    workspace_id: str
    run_id: str
    guest_identity: str
    overlay_id: str
    lifecycle_status: LifecycleStatus
    last_error_code: str | None
    provisioned_at: datetime | None


class VmAdapter(Protocol):
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None: ...

    def probe_ready(self, guest_id: str) -> bool: ...

    def destroy(self, guest_id: str, overlay_id: str) -> None: ...


class VmLifecycleService:
    """Derives every VM identity from a run ID; it never exposes a host shell."""

    def __init__(self, adapter: VmAdapter) -> None:
        self._adapter = adapter
        self._guests: dict[str, GuestHandle] = {}

    def create(self, run_id: str) -> GuestHandle:
        if not run_id.startswith("run_"):
            raise VmLifecycleError("invalid_run_id")
        existing = self._guests.get(run_id)
        if existing is not None:
            return existing
        suffix = run_id.removeprefix("run_")
        handle = GuestHandle(run_id, f"guest-{suffix}", f"overlay-{suffix}", "creating")
        self._adapter.provision(run_id, handle.guest_id, handle.overlay_id)
        self._guests[run_id] = handle
        return handle

    def probe(self, run_id: str) -> GuestHandle:
        handle = self._require_live(run_id)
        if self._adapter.probe_ready(handle.guest_id):
            handle = GuestHandle(
                handle.run_id, handle.guest_id, handle.overlay_id, "ready"
            )
            self._guests[run_id] = handle
        return handle

    def destroy(self, run_id: str) -> GuestHandle:
        handle = self._guests.get(run_id)
        if handle is None:
            raise VmLifecycleError("unknown_run")
        if handle.status != "destroyed":
            self._adapter.destroy(handle.guest_id, handle.overlay_id)
            handle = GuestHandle(
                handle.run_id, handle.guest_id, handle.overlay_id, "destroyed"
            )
            self._guests[run_id] = handle
        return handle

    def _require_live(self, run_id: str) -> GuestHandle:
        handle = self._guests.get(run_id)
        if handle is None or handle.status == "destroyed":
            raise VmLifecycleError("guest_not_live")
        return handle


class PostgresVmLifecycleService:
    """Restart-safe lifecycle owner backed by one locked workspace row per run.

    The external adapter is deliberately invoked while the owning row is locked.
    Lifecycle calls have bounded adapter timeouts, and serialization is more
    important here than connection throughput: a destroy must never race a late
    provision or readiness probe for the same guest.
    """

    def __init__(self, adapter: VmAdapter, unit_of_work: PostgresUnitOfWork) -> None:
        self._adapter = adapter
        self._unit_of_work = unit_of_work

    def create(self, run_id: str) -> GuestHandle:
        self._validate_run_id(run_id)
        failure: tuple[VmLifecycleError, Exception] | None = None
        with self._unit_of_work.transaction() as unit_of_work:
            row = self._locked_row(unit_of_work, run_id)
            if row is None:
                row = self._create_workspace_row(unit_of_work, run_id)
            status = str(row.lifecycle_status)
            if status == "destroyed":
                raise VmLifecycleError("guest_destroyed")
            if status == "ready":
                return self._handle(row)
            if status == "creating" and row.provisioned_at is not None:
                return self._handle(row)
            if status in {"failed", "destroying"}:
                self._set_lifecycle(
                    unit_of_work,
                    run_id,
                    status="creating",
                    last_error_code=None,
                )
                row = self._locked_row(unit_of_work, run_id)
                assert row is not None
            try:
                self._adapter.provision(
                    run_id, str(row.guest_identity), str(row.overlay_id)
                )
            except Exception as error:
                self._set_lifecycle(
                    unit_of_work,
                    run_id,
                    status="failed",
                    last_error_code="vm_provision_failed",
                )
                failure = (VmLifecycleError("vm_provision_failed"), error)
            else:
                self._set_lifecycle(
                    unit_of_work,
                    run_id,
                    status="creating",
                    last_error_code=None,
                    provisioned=True,
                )
            row = self._locked_row(unit_of_work, run_id)
            assert row is not None
            handle = self._handle(row)
        if failure is not None:
            public_error, cause = failure
            raise public_error from cause
        return handle

    def probe(self, run_id: str) -> GuestHandle:
        self._validate_run_id(run_id)
        failure: tuple[VmLifecycleError, Exception] | None = None
        with self._unit_of_work.transaction() as unit_of_work:
            row = self._locked_row(unit_of_work, run_id)
            if row is None or str(row.lifecycle_status) == "destroyed":
                raise VmLifecycleError("guest_not_live")
            if str(row.lifecycle_status) == "failed":
                raise VmLifecycleError(str(row.last_error_code or "vm_lifecycle_failed"))
            if str(row.lifecycle_status) == "destroying":
                raise VmLifecycleError("guest_destroying")
            if str(row.lifecycle_status) != "ready":
                try:
                    ready = self._adapter.probe_ready(str(row.guest_identity))
                except Exception as error:
                    self._set_lifecycle(
                        unit_of_work,
                        run_id,
                        status="failed",
                        last_error_code="vm_probe_failed",
                    )
                    failure = (VmLifecycleError("vm_probe_failed"), error)
                    ready = False
                if ready:
                    self._set_lifecycle(
                        unit_of_work,
                        run_id,
                        status="ready",
                        last_error_code=None,
                        ready=True,
                    )
            row = self._locked_row(unit_of_work, run_id)
            assert row is not None
            handle = self._handle(row)
        if failure is not None:
            public_error, cause = failure
            raise public_error from cause
        return handle

    def destroy(self, run_id: str) -> GuestHandle:
        self._validate_run_id(run_id)
        failure: tuple[VmLifecycleError, Exception] | None = None
        with self._unit_of_work.transaction() as unit_of_work:
            row = self._locked_row(unit_of_work, run_id)
            if row is None:
                raise VmLifecycleError("unknown_run")
            if str(row.lifecycle_status) == "destroyed":
                return self._handle(row)
            self._set_lifecycle(
                unit_of_work,
                run_id,
                status="destroying",
                last_error_code=None,
            )
            try:
                self._adapter.destroy(str(row.guest_identity), str(row.overlay_id))
            except Exception as error:
                self._set_lifecycle(
                    unit_of_work,
                    run_id,
                    status="failed",
                    last_error_code="vm_destroy_failed",
                )
                failure = (VmLifecycleError("vm_destroy_failed"), error)
            else:
                current = unit_of_work.workspace_sessions.get(str(row.workspace_id))
                if current is None:
                    raise VmLifecycleError("unknown_workspace")
                now = datetime.now(UTC)
                unit_of_work.workspace_sessions.compare_and_swap(
                    current.model_copy(
                        update={
                            "status": "destroyed",
                            "metadata": current.metadata.model_copy(
                                update={
                                    "record_version": current.metadata.record_version + 1,
                                    "updated_at": now,
                                    "idempotency_key": f"vm:destroy:{run_id}",
                                }
                            ),
                        }
                    ),
                    expected_record_version=current.metadata.record_version,
                )
                self._set_lifecycle(
                    unit_of_work,
                    run_id,
                    status="destroyed",
                    last_error_code=None,
                    destroyed=True,
                )
            row = self._locked_row(unit_of_work, run_id)
            assert row is not None
            handle = self._handle(row)
        if failure is not None:
            public_error, cause = failure
            raise public_error from cause
        return handle

    def get(self, run_id: str) -> GuestHandle:
        self._validate_run_id(run_id)
        with self._unit_of_work.transaction() as unit_of_work:
            row = self._row(unit_of_work, run_id)
            if row is None:
                raise VmLifecycleError("unknown_run")
            return self._handle(row)

    @staticmethod
    def workspace_id(run_id: str) -> str:
        PostgresVmLifecycleService._validate_run_id(run_id)
        return f"workspace_{sha256(run_id.encode()).hexdigest()[:24]}"

    def _create_workspace_row(
        self, unit_of_work: PostgresUnitOfWork, run_id: str
    ) -> _LifecycleRow:
        source = unit_of_work.connection.execute(
            text(
                "SELECT project_id, source_fingerprint, payload -> 'metadata' ->> "
                "'trace_id' AS trace_id FROM runs WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).mappings().one_or_none()
        if (
            source is None
            or source.project_id is None
            or source.source_fingerprint is None
        ):
            raise VmLifecycleError("run_has_no_selected_source")
        suffix = run_id.removeprefix("run_")
        workspace_id = self.workspace_id(run_id)
        guest_id = f"guest-{suffix}"
        overlay_id = f"overlay-{suffix}"
        guest_path = f"home/piagent/workspaces/{run_id}/{source.project_id}"
        now = datetime.now(UTC)
        record = WorkspaceRecord(
            metadata=RecordMetadata(
                record_version=1,
                created_at=now,
                updated_at=now,
                idempotency_key=f"vm:create:{run_id}",
                trace_id=source.trace_id,
            ),
            workspace_id=workspace_id,
            run_id=run_id,
            selected_source=str(source.project_id),
            source_fingerprint=str(source.source_fingerprint),
            guest_identity=guest_id,
            guest_path=guest_path,
            status="selected",
        )
        try:
            unit_of_work.workspace_sessions.add(record)
        except DuplicateRecordError:
            row = self._locked_row(unit_of_work, run_id)
            if row is None:
                raise VmLifecycleError("workspace_identity_conflict") from None
            return row
        unit_of_work.connection.execute(
            text(
                "UPDATE workspace_sessions SET overlay_id = :overlay_id, "
                "lifecycle_status = 'creating', last_error_code = NULL, "
                "record_version = record_version + 1, updated_at = :now, "
                "payload = jsonb_set(payload, '{metadata}', "
                "(payload -> 'metadata') || jsonb_build_object("
                "'record_version', record_version + 1, 'updated_at', :now), true) "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id, "overlay_id": overlay_id, "now": now},
        )
        row = self._locked_row(unit_of_work, run_id)
        assert row is not None
        return row

    @staticmethod
    def _set_lifecycle(
        unit_of_work: PostgresUnitOfWork,
        run_id: str,
        *,
        status: str,
        last_error_code: str | None,
        ready: bool = False,
        destroyed: bool = False,
        provisioned: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        unit_of_work.connection.execute(
            text(
                "UPDATE workspace_sessions SET lifecycle_status = :status, "
                "last_error_code = :last_error_code, "
                "provisioned_at = CASE WHEN :provisioned "
                "THEN COALESCE(provisioned_at, :now) ELSE provisioned_at END, "
                "ready_at = CASE WHEN :ready THEN COALESCE(ready_at, :now) ELSE ready_at END, "
                "destroyed_at = CASE WHEN :destroyed THEN :now ELSE destroyed_at END, "
                "record_version = record_version + 1, updated_at = :now, "
                "payload = jsonb_set(payload, '{metadata}', "
                "(payload -> 'metadata') || jsonb_build_object("
                "'record_version', record_version + 1, 'updated_at', :now), true) "
                "WHERE run_id = :run_id"
            ),
            {
                "run_id": run_id,
                "status": status,
                "last_error_code": last_error_code,
                "ready": ready,
                "destroyed": destroyed,
                "provisioned": provisioned,
                "now": now,
            },
        )

    @staticmethod
    def _row(
        unit_of_work: PostgresUnitOfWork, run_id: str
    ) -> _LifecycleRow | None:
        row = unit_of_work.connection.execute(
            text(
                "SELECT workspace_id, run_id, guest_identity, overlay_id, "
                "lifecycle_status, last_error_code, provisioned_at "
                "FROM workspace_sessions "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).mappings().one_or_none()
        return PostgresVmLifecycleService._decode_row(row)

    @staticmethod
    def _locked_row(
        unit_of_work: PostgresUnitOfWork, run_id: str
    ) -> _LifecycleRow | None:
        row = unit_of_work.connection.execute(
            text(
                "SELECT workspace_id, run_id, guest_identity, overlay_id, "
                "lifecycle_status, last_error_code, provisioned_at "
                "FROM workspace_sessions "
                "WHERE run_id = :run_id FOR UPDATE"
            ),
            {"run_id": run_id},
        ).mappings().one_or_none()
        return PostgresVmLifecycleService._decode_row(row)

    @staticmethod
    def _decode_row(row: object | None) -> _LifecycleRow | None:
        if row is None:
            return None
        values = cast(dict[str, object], row)
        status = str(values["lifecycle_status"])
        if status not in {"creating", "ready", "failed", "destroying", "destroyed"}:
            raise VmLifecycleError("invalid_lifecycle_state")
        return _LifecycleRow(
            workspace_id=str(values["workspace_id"]),
            run_id=str(values["run_id"]),
            guest_identity=str(values["guest_identity"]),
            overlay_id=str(values["overlay_id"]),
            lifecycle_status=cast(LifecycleStatus, status),
            last_error_code=(
                str(values["last_error_code"])
                if values["last_error_code"] is not None
                else None
            ),
            provisioned_at=cast(datetime | None, values["provisioned_at"]),
        )

    @staticmethod
    def _handle(row: _LifecycleRow) -> GuestHandle:
        return GuestHandle(
            run_id=row.run_id,
            guest_id=row.guest_identity,
            overlay_id=row.overlay_id,
            status=row.lifecycle_status,
        )

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not run_id.startswith("run_") or len(run_id) > 128:
            raise VmLifecycleError("invalid_run_id")
