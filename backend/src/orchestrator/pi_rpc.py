"""Bounded client for Pi's JSONL RPC protocol.

The process itself is expected to run inside the disposable guest.  This
module deliberately knows nothing about host project paths or arbitrary shell
commands; callers provide a fixed argv assembled by the VM manager.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import Literal, cast

PiRole = Literal["executor", "local-verifier", "integrator", "outcome-verifier"]


class PiRpcError(RuntimeError):
    """The guest Pi process failed a bounded protocol operation."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PiToolEvent:
    tool_call_id: str
    tool_name: str
    status: Literal["started", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class PiRpcResult:
    text: str
    tool_events: tuple[PiToolEvent, ...]


def run_pi_rpc(
    argv: tuple[str, ...],
    *,
    prompt: str,
    timeout_seconds: int,
    max_text_bytes: int = 262_144,
    max_stream_bytes: int = 4_194_304,
    max_events: int = 256,
) -> PiRpcResult:
    """Run one prompt and return only the final assistant text and safe metadata.

    Pi accepts a prompt asynchronously.  ``agent_settled`` is the only event
    that proves retries, compaction, and queued continuations are finished, so
    the final-text query is not sent before that event.
    """

    if not argv or not prompt or len(prompt.encode("utf-8")) > 262_144:
        raise PiRpcError("invalid_pi_request")
    if not 5 <= timeout_seconds <= 900:
        raise PiRpcError("invalid_pi_timeout")
    if not 1 <= max_text_bytes <= 1_048_576:
        raise PiRpcError("invalid_pi_output_limit")

    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as error:
        raise PiRpcError("pi_runtime_unavailable", retryable=True) from error
    if process.stdin is None or process.stdout is None:
        _stop_process(process)
        raise PiRpcError("pi_runtime_unavailable", retryable=True)

    selector = selectors.DefaultSelector()
    buffer = bytearray()
    total_bytes = 0
    tool_events: list[PiToolEvent] = []
    prompt_accepted = False
    final_requested = False
    deadline = time.monotonic() + timeout_seconds
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        _send(
            process,
            {"id": "orchestrator-prompt", "type": "prompt", "message": prompt},
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PiRpcError("pi_runtime_timeout", retryable=True)
            ready = selector.select(min(remaining, 0.25))
            if not ready:
                if process.poll() is not None:
                    raise PiRpcError("pi_runtime_terminated", retryable=True)
                continue
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                raise PiRpcError("pi_runtime_terminated", retryable=True)
            total_bytes += len(chunk)
            if total_bytes > max_stream_bytes:
                raise PiRpcError("pi_rpc_stream_limit")
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                if line.endswith(b"\r"):
                    line = line[:-1]
                if not line or len(line) > 1_048_576:
                    raise PiRpcError("invalid_pi_rpc_record")
                record = _record(bytes(line))
                record_type = record.get("type")
                if record_type == "response":
                    command = record.get("command")
                    success = record.get("success")
                    if (
                        command == "prompt"
                        and record.get("id") == "orchestrator-prompt"
                    ):
                        if success is not True:
                            raise PiRpcError("pi_prompt_rejected")
                        prompt_accepted = True
                    elif (
                        command == "get_last_assistant_text"
                        and record.get("id") == "orchestrator-result"
                    ):
                        if success is not True:
                            raise PiRpcError("pi_result_unavailable")
                        data = record.get("data")
                        text = data.get("text") if isinstance(data, dict) else None
                        if not isinstance(text, str) or not text:
                            raise PiRpcError("pi_result_unavailable")
                        if len(text.encode("utf-8")) > max_text_bytes:
                            raise PiRpcError("pi_result_too_large")
                        return PiRpcResult(text=text, tool_events=tuple(tool_events))
                elif record_type == "agent_settled":
                    if not prompt_accepted:
                        raise PiRpcError("invalid_pi_rpc_sequence")
                    if not final_requested:
                        _send(
                            process,
                            {
                                "id": "orchestrator-result",
                                "type": "get_last_assistant_text",
                            },
                        )
                        final_requested = True
                elif record_type == "extension_ui_request":
                    request_id = record.get("id")
                    if isinstance(request_id, str):
                        _send(
                            process,
                            {
                                "type": "extension_ui_response",
                                "id": request_id,
                                "cancelled": True,
                            },
                        )
                elif record_type in {"tool_execution_start", "tool_execution_end"}:
                    event = _tool_event(record)
                    if event is not None:
                        if len(tool_events) >= max_events:
                            raise PiRpcError("pi_tool_event_limit")
                        tool_events.append(event)
    finally:
        selector.close()
        _stop_process(process)


def _record(line: bytes) -> dict[str, object]:
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PiRpcError("invalid_pi_rpc_record") from error
    if not isinstance(value, dict):
        raise PiRpcError("invalid_pi_rpc_record")
    return cast(dict[str, object], value)


def _tool_event(record: dict[str, object]) -> PiToolEvent | None:
    tool_call_id = record.get("toolCallId")
    tool_name = record.get("toolName")
    if not isinstance(tool_call_id, str) or not isinstance(tool_name, str):
        return None
    if len(tool_call_id) > 256 or len(tool_name) > 128:
        raise PiRpcError("invalid_pi_tool_event")
    if record.get("type") == "tool_execution_start":
        status: Literal["started", "completed", "failed"] = "started"
    else:
        status = "failed" if record.get("isError") is True else "completed"
    return PiToolEvent(tool_call_id, tool_name, status)


def _send(process: subprocess.Popen[bytes], value: dict[str, object]) -> None:
    if process.stdin is None:
        raise PiRpcError("pi_runtime_terminated", retryable=True)
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    try:
        process.stdin.write(payload + b"\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError) as error:
        raise PiRpcError("pi_runtime_terminated", retryable=True) from error


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if process.stdin is not None:
        process.stdin.close()
    if process.stdout is not None:
        process.stdout.close()
