"""Protocol tests for the bounded Pi JSONL client."""

from __future__ import annotations

import sys

import pytest

from orchestrator.pi_rpc import PiRpcError, run_pi_rpc


def test_pi_rpc_waits_for_settled_and_returns_safe_tool_metadata() -> None:
    script = r'''
import json, sys
prompt = json.loads(sys.stdin.readline())
assert prompt["type"] == "prompt"
print(json.dumps({"id": prompt["id"], "type": "response", "command": "prompt", "success": True}), flush=True)
print(json.dumps({"type": "tool_execution_start", "toolCallId": "call_1", "toolName": "read", "args": {"path": "secret"}}), flush=True)
print(json.dumps({"type": "tool_execution_end", "toolCallId": "call_1", "toolName": "read", "result": {"content": "not returned"}, "isError": False}), flush=True)
print(json.dumps({"type": "agent_settled"}), flush=True)
result = json.loads(sys.stdin.readline())
assert result["type"] == "get_last_assistant_text"
print(json.dumps({"id": result["id"], "type": "response", "command": "get_last_assistant_text", "success": True, "data": {"text": "{\"kind\":\"work_report\"}"}}), flush=True)
'''

    result = run_pi_rpc(
        (sys.executable, "-u", "-c", script),
        prompt="implement the packet",
        timeout_seconds=5,
    )

    assert result.text == '{"kind":"work_report"}'
    assert [(event.tool_name, event.status) for event in result.tool_events] == [
        ("read", "started"),
        ("read", "completed"),
    ]


def test_pi_rpc_rejects_malformed_or_oversized_results() -> None:
    malformed = "import sys; sys.stdin.readline(); print('not-json', flush=True)"
    with pytest.raises(PiRpcError, match="invalid_pi_rpc_record"):
        run_pi_rpc(
            (sys.executable, "-u", "-c", malformed),
            prompt="x",
            timeout_seconds=5,
        )

    oversized = r'''
import json, sys
p = json.loads(sys.stdin.readline())
print(json.dumps({"id": p["id"], "type": "response", "command": "prompt", "success": True}), flush=True)
print(json.dumps({"type": "agent_settled"}), flush=True)
r = json.loads(sys.stdin.readline())
print(json.dumps({"id": r["id"], "type": "response", "command": "get_last_assistant_text", "success": True, "data": {"text": "too large"}}), flush=True)
'''
    with pytest.raises(PiRpcError, match="pi_result_too_large"):
        run_pi_rpc(
            (sys.executable, "-u", "-c", oversized),
            prompt="x",
            timeout_seconds=5,
            max_text_bytes=2,
        )
