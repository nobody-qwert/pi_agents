"""Guest-only typed tool policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.guest_runtime import (
    GuestRuntimeError,
    GuestToolRequest,
    GuestToolRuntime,
    LocalGuestRuntimeAdapter,
)


class Cancelled:
    def is_cancelled(self) -> bool:
        return True


def runtime(tmp_path: Path) -> tuple[GuestToolRuntime, Path]:
    workspace = "home/piagent/workspaces/run_example/project_example"
    root = tmp_path / workspace
    root.mkdir(parents=True)
    (root / "main.py").write_text("before\nneedle\n")
    return GuestToolRuntime(
        LocalGuestRuntimeAdapter(tmp_path), workspace_path=workspace
    ), root


def test_executor_can_mutate_guest_without_touching_the_source_fixture(
    tmp_path: Path,
) -> None:
    tool_runtime, guest_workspace = runtime(tmp_path)
    source = tmp_path / "source.py"
    source.write_text("source")

    result = tool_runtime.execute(
        role="executor",
        request=GuestToolRequest(
            tool="write", relative_path="new.txt", content="guest"
        ),
    )

    assert result.status == "completed"
    assert (guest_workspace / "new.txt").read_text() == "guest"
    assert source.read_text() == "source"


def test_verifier_is_read_only_and_escapes_are_rejected(tmp_path: Path) -> None:
    tool_runtime, _ = runtime(tmp_path)
    with pytest.raises(GuestRuntimeError, match="tool_not_permitted"):
        tool_runtime.execute(
            role="local-verifier",
            request=GuestToolRequest(
                tool="write", relative_path="blocked", content="no"
            ),
        )
    with pytest.raises(ValidationError, match="parent traversal"):
        GuestToolRequest(tool="read", relative_path="../source.py")


def test_command_policy_cancellation_and_bounded_output(tmp_path: Path) -> None:
    tool_runtime, _ = runtime(tmp_path)
    with pytest.raises(GuestRuntimeError, match="forbidden_command"):
        tool_runtime.execute(
            role="executor",
            request=GuestToolRequest(tool="bash", command=("sudo", "id")),
        )
    cancelled = tool_runtime.execute(
        role="executor",
        request=GuestToolRequest(tool="read", relative_path="main.py"),
        cancellation=Cancelled(),
    )
    assert cancelled.status == "cancelled"
    bounded = tool_runtime.execute(
        role="executor",
        request=GuestToolRequest(
            tool="bash",
            command=("python", "-c", "print('x' * 100)"),
            max_output_bytes=10,
        ),
    )
    assert bounded.truncated is True
    assert len(bounded.output.encode()) == 10
