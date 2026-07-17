"""Narrow, run-scoped disposable-guest lifecycle boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class VmLifecycleError(Exception):
    """A lifecycle operation could not safely target the requested run."""


@dataclass(frozen=True, slots=True)
class GuestHandle:
    run_id: str
    guest_id: str
    overlay_id: str
    status: Literal["creating", "ready", "destroyed"]


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
