"""Typed, role-scoped operations rooted in a disposable guest workspace."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from orchestrator.domain.primitives import RelativePath, StrictDomainModel

GuestToolName = Literal["read", "write", "edit", "bash", "grep", "find", "ls"]
_MUTATING_TOOLS = frozenset({"write", "edit"})
_ROLE_TOOLS: dict[str, frozenset[GuestToolName]] = {
    "executor": frozenset({"read", "write", "edit", "bash", "grep", "find", "ls"}),
    "integrator": frozenset({"read", "write", "edit", "bash", "grep", "find", "ls"}),
    "local-verifier": frozenset({"read", "bash", "grep", "find", "ls"}),
    "outcome-verifier": frozenset({"read", "bash", "grep", "find", "ls"}),
}
_FORBIDDEN_COMMAND_TOKENS = frozenset(
    {"sudo", "su", "docker", "ssh", "scp", "rsync", "curl", "wget", "nc", "socat"}
)


class GuestRuntimeError(Exception):
    """A request was rejected or could not execute inside the guest."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


class GuestToolRequest(StrictDomainModel):
    """Parsed operation fields; free-form model output is never accepted here."""

    tool: GuestToolName
    relative_path: RelativePath = "."
    content: str | None = Field(default=None, max_length=262_144)
    old_text: str | None = Field(default=None, max_length=131_072)
    new_text: str | None = Field(default=None, max_length=131_072)
    query: str | None = Field(default=None, max_length=1024)
    command: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_output_bytes: int = Field(default=65_536, ge=1, le=1_048_576)

    @model_validator(mode="after")
    def matches_tool_contract(self) -> GuestToolRequest:
        if self.tool == "write" and self.content is None:
            raise ValueError("write requires content")
        if self.tool == "edit" and (self.old_text is None or self.new_text is None):
            raise ValueError("edit requires old_text and new_text")
        if self.tool in {"grep", "find"} and not self.query:
            raise ValueError(f"{self.tool} requires query")
        if self.tool == "bash" and not self.command:
            raise ValueError("bash requires an argv command")
        if self.tool != "bash" and self.command:
            raise ValueError("only bash accepts command")
        return self


@dataclass(frozen=True, slots=True)
class GuestToolResult:
    tool: GuestToolName
    status: Literal["completed", "rejected", "cancelled", "timed_out", "failed"]
    output: str
    output_sha256: str
    truncated: bool
    exit_code: int | None


class GuestAgentRuntime(Protocol):
    """Guest RPC/Pi SDK adapter; implementations may not accept host paths."""

    def execute(
        self, request: GuestToolRequest, *, workspace_path: str
    ) -> tuple[str, int | None]: ...


class GuestToolRuntime:
    """Policy gateway that validates every operation before guest dispatch."""

    def __init__(self, adapter: GuestAgentRuntime, *, workspace_path: str) -> None:
        self._adapter = adapter
        self._workspace_path = self._safe_workspace_path(workspace_path)

    def execute(
        self,
        *,
        role: str,
        request: GuestToolRequest,
        cancellation: CancellationToken | None = None,
    ) -> GuestToolResult:
        if cancellation is not None and cancellation.is_cancelled():
            return self._result(
                request.tool, "cancelled", "", None, request.max_output_bytes
            )
        self._authorize(role, request)
        try:
            output, exit_code = self._adapter.execute(
                request, workspace_path=self._workspace_path
            )
        except TimeoutError:
            return self._result(
                request.tool, "timed_out", "", None, request.max_output_bytes
            )
        except GuestRuntimeError:
            raise
        except Exception as error:
            raise GuestRuntimeError("guest_execution_failed", retryable=True) from error
        if cancellation is not None and cancellation.is_cancelled():
            return self._result(
                request.tool, "cancelled", "", exit_code, request.max_output_bytes
            )
        return self._result(
            request.tool, "completed", output, exit_code, request.max_output_bytes
        )

    def _authorize(self, role: str, request: GuestToolRequest) -> None:
        allowed = _ROLE_TOOLS.get(role, frozenset())
        if request.tool not in allowed:
            raise GuestRuntimeError("tool_not_permitted")
        self._safe_relative_path(request.relative_path)
        if request.tool == "bash":
            self._validate_command(request.command)

    @staticmethod
    def _validate_command(command: tuple[str, ...]) -> None:
        if not command or any(
            not argument or len(argument) > 4096 for argument in command
        ):
            raise GuestRuntimeError("invalid_command")
        if command[0] in _FORBIDDEN_COMMAND_TOKENS:
            raise GuestRuntimeError("forbidden_command")
        if any(
            any(token in argument for token in ("/dev/", "..", "~"))
            for argument in command
        ):
            raise GuestRuntimeError("unsafe_command_argument")

    @staticmethod
    def _safe_workspace_path(path: str) -> str:
        if not path.startswith("home/piagent/workspaces/") or ".." in Path(path).parts:
            raise ValueError("workspace_path must be a guest workspace path")
        return path

    @staticmethod
    def _safe_relative_path(path: str) -> None:
        if path.startswith("/") or ".." in Path(path).parts:
            raise GuestRuntimeError("workspace_path_escape")

    @staticmethod
    def _result(
        tool: GuestToolName,
        status: Literal["completed", "rejected", "cancelled", "timed_out", "failed"],
        output: str,
        exit_code: int | None,
        max_output_bytes: int,
    ) -> GuestToolResult:
        encoded = output.encode("utf-8", errors="replace")
        truncated = len(encoded) > max_output_bytes
        bounded = encoded[:max_output_bytes].decode("utf-8", errors="replace")
        return GuestToolResult(
            tool=tool,
            status=status,
            output=bounded,
            output_sha256=hashlib.sha256(encoded).hexdigest(),
            truncated=truncated,
            exit_code=exit_code,
        )


class LocalGuestRuntimeAdapter:
    """Disposable-fixture adapter; production uses an equivalent guest RPC port."""

    def __init__(self, guest_root: Path) -> None:
        self._guest_root = guest_root.resolve()

    def execute(
        self, request: GuestToolRequest, *, workspace_path: str
    ) -> tuple[str, int | None]:
        root = self._workspace_root(workspace_path)
        target = self._target(root, request.relative_path)
        if request.tool == "read":
            return target.read_text(encoding="utf-8"), 0
        if request.tool == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(request.content or "", encoding="utf-8")
            return "", 0
        if request.tool == "edit":
            source = target.read_text(encoding="utf-8")
            if source.count(request.old_text or "") != 1:
                raise GuestRuntimeError("edit_match_not_unique")
            target.write_text(
                source.replace(request.old_text or "", request.new_text or ""),
                encoding="utf-8",
            )
            return "", 0
        if request.tool == "ls":
            return "\n".join(sorted(path.name for path in target.iterdir())), 0
        if request.tool == "find":
            return "\n".join(
                path.relative_to(root).as_posix()
                for path in sorted(target.rglob(request.query or ""))
                if not path.is_symlink()
            ), 0
        if request.tool == "grep":
            matches: list[str] = []
            for path in sorted(target.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    for number, line in enumerate(
                        path.read_text(errors="replace").splitlines(), 1
                    ):
                        if (request.query or "") in line:
                            matches.append(f"{path.relative_to(root)}:{number}:{line}")
            return "\n".join(matches), 0
        try:
            completed = subprocess.run(
                request.command,
                cwd=target,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=request.timeout_seconds,
                text=True,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError from error
        return completed.stdout, completed.returncode

    def _workspace_root(self, workspace_path: str) -> Path:
        root = (self._guest_root / workspace_path).resolve()
        if not root.is_relative_to(self._guest_root) or not root.is_dir():
            raise GuestRuntimeError("unknown_guest_workspace")
        return root

    @staticmethod
    def _target(root: Path, relative_path: str) -> Path:
        target = (root / relative_path).resolve()
        if not target.is_relative_to(root):
            raise GuestRuntimeError("workspace_path_escape")
        return target
