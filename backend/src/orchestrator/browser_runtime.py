"""Typed browser actions that can execute only through approved guest egress."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, model_validator

from orchestrator.domain.primitives import StrictDomainModel
from orchestrator.egress import ApprovedDestination, EgressPolicy

BrowserAction = Literal[
    "navigate", "snapshot", "click", "input", "screenshot", "console", "network"
]
_READ_ONLY_ACTIONS = frozenset({"snapshot", "screenshot", "console", "network"})
_MUTATING_ACTIONS = frozenset({"navigate", "click", "input"})


class BrowserRuntimeError(Exception):
    """A browser action was rejected before it reached the guest browser."""


class BrowserRequest(StrictDomainModel):
    action: BrowserAction
    url: str | None = Field(default=None, max_length=4096)
    selector: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=65_536)
    max_output_bytes: int = Field(default=65_536, ge=1, le=1_048_576)

    @model_validator(mode="after")
    def action_contract(self) -> BrowserRequest:
        if self.action == "navigate" and not self.url:
            raise ValueError("navigate requires url")
        if self.action in {"click", "input"} and not self.selector:
            raise ValueError("action requires selector")
        if self.action == "input" and self.text is None:
            raise ValueError("input requires text")
        return self


@dataclass(frozen=True, slots=True)
class BrowserResult:
    action: BrowserAction
    output: str
    output_sha256: str
    truncated: bool
    destination: ApprovedDestination | None


class GuestBrowserAdapter(Protocol):
    def execute(
        self, request: BrowserRequest, destination: ApprovedDestination | None
    ) -> str: ...


class BrowserRuntime:
    def __init__(self, adapter: GuestBrowserAdapter, egress: EgressPolicy) -> None:
        self._adapter = adapter
        self._egress = egress
        self._current_destination: ApprovedDestination | None = None

    def execute(self, *, role: str, request: BrowserRequest) -> BrowserResult:
        if request.action in _MUTATING_ACTIONS and role not in {
            "executor",
            "integrator",
        }:
            raise BrowserRuntimeError("browser_action_not_permitted")
        if request.action in _READ_ONLY_ACTIONS and role not in {
            "executor",
            "integrator",
            "local-verifier",
            "outcome-verifier",
        }:
            raise BrowserRuntimeError("browser_action_not_permitted")
        destination = self._current_destination
        if request.action == "navigate":
            destination = self._egress.authorize(request.url or "")
            self._current_destination = destination
        if destination is None:
            raise BrowserRuntimeError("browser_not_navigated")
        output = self._adapter.execute(request, destination)
        raw = output.encode("utf-8", errors="replace")
        bounded = raw[: request.max_output_bytes].decode("utf-8", errors="replace")
        return BrowserResult(
            action=request.action,
            output=bounded,
            output_sha256=hashlib.sha256(raw).hexdigest(),
            truncated=len(raw) > request.max_output_bytes,
            destination=destination,
        )
