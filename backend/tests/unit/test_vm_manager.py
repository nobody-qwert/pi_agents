"""Typed VM-manager API contract tests without claiming KVM integration."""

from fastapi.testclient import TestClient

from orchestrator.pi_rpc import PiRpcResult, PiToolEvent
from orchestrator.vm_manager import VmPreflight, create_vm_manager_app

TOKEN = "test-vm-manager-token-00000000"


class Adapter:
    def __init__(self) -> None:
        self.provisioned: list[tuple[str, str, str]] = []
        self.destroyed: list[tuple[str, str]] = []

    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        self.provisioned.append((run_id, guest_id, overlay_id))

    def probe_ready(self, guest_id: str) -> bool:
        return guest_id == "guest-example"

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        self.destroyed.append((guest_id, overlay_id))


def client_for(adapter: Adapter) -> TestClient:
    return TestClient(
        create_vm_manager_app(
            adapter,
            auth_token=TOKEN,
            preflight=lambda: VmPreflight(True, True, True, True, True, True),
        )
    )


def test_manager_requires_auth_and_exposes_typed_lifecycle_only() -> None:
    adapter = Adapter()
    client = client_for(adapter)
    payload = {"guest_id": "guest-example", "overlay_id": "overlay-example"}

    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 401
    created = client.post(
        "/v1/guests/run_example",
        json=payload,
        headers={"X-VM-Manager-Token": TOKEN},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "creating"
    ready = client.get(
        "/v1/guests/guest-example/ready",
        headers={"X-VM-Manager-Token": TOKEN},
    )
    assert ready.json() == {
        "schema_version": 1,
        "guest_id": "guest-example",
        "ready": True,
    }
    destroyed = client.request(
        "DELETE",
        "/v1/guests/guest-example",
        json=payload,
        headers={"X-VM-Manager-Token": TOKEN},
    )
    assert destroyed.status_code == 200
    assert adapter.provisioned == [
        ("run_example", "guest-example", "overlay-example")
    ]
    assert adapter.destroyed == [("guest-example", "overlay-example")]


def test_manager_rejects_cross_run_and_unknown_fields() -> None:
    client = client_for(Adapter())
    headers = {"X-VM-Manager-Token": TOKEN}
    mismatch = client.post(
        "/v1/guests/run_one",
        json={"guest_id": "guest-two", "overlay_id": "overlay-two"},
        headers=headers,
    )
    extra = client.post(
        "/v1/guests/run_example",
        json={
            "guest_id": "guest-example",
            "overlay_id": "overlay-example",
            "command": "id",
        },
        headers=headers,
    )
    assert mismatch.status_code == 409
    assert extra.status_code == 422


class AgentController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def invoke_agent(
        self, guest_id: str, guest_path: str, role: str, prompt: str
    ) -> PiRpcResult:
        self.calls.append((guest_id, guest_path, role, prompt))
        return PiRpcResult(
            text='{"kind":"verification_report"}',
            tool_events=(PiToolEvent("call_1", "pytest", "completed"),),
        )


def test_manager_exposes_role_scoped_agent_attempt_not_a_shell() -> None:
    adapter = Adapter()
    controller = AgentController()
    client = TestClient(
        create_vm_manager_app(
            adapter,
            auth_token=TOKEN,
            preflight=lambda: VmPreflight(True, True, True, True, True, True),
            workspace_controller=controller,  # type: ignore[arg-type]
        )
    )
    headers = {"X-VM-Manager-Token": TOKEN}

    response = client.post(
        "/v1/guests/guest-example/agent-attempts",
        json={
            "guest_path": "home/piagent/workspaces/run_example/project",
            "role": "local-verifier",
            "prompt": "verify the immutable packet",
        },
        headers=headers,
    )
    arbitrary = client.post(
        "/v1/guests/guest-example/agent-attempts",
        json={
            "guest_path": "home/piagent/workspaces/run_example/project",
            "role": "root",
            "prompt": "x",
            "command": "id",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["tool_events"] == [
        {
            "schema_version": 1,
            "tool_call_id": "call_1",
            "tool_name": "pytest",
            "status": "completed",
        }
    ]
    assert arbitrary.status_code == 422
    assert controller.calls == [
        (
            "guest-example",
            "home/piagent/workspaces/run_example/project",
            "local-verifier",
            "verify the immutable packet",
        )
    ]


class PreviewController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, str]] = []

    def preview_request(
        self, guest_id: str, port: int, method: str, target: str
    ) -> tuple[int, str, bytes]:
        self.calls.append((guest_id, port, method, target))
        return 200, "text/html; charset=utf-8", b"<h1>guest preview</h1>"


def test_manager_preview_fetch_is_typed_bounded_and_port_allowlisted() -> None:
    controller = PreviewController()
    api = TestClient(
        create_vm_manager_app(
            Adapter(),
            auth_token=TOKEN,
            preflight=lambda: VmPreflight(True, True, True, True, True, True),
            workspace_controller=controller,  # type: ignore[arg-type]
            preview_ports=(4173,),
        )
    )
    headers = {"X-VM-Manager-Token": TOKEN}

    response = api.post(
        "/v1/guests/guest-example/previews/fetch",
        json={"port": 4173, "method": "GET", "target": "/status?q=1"},
        headers=headers,
    )
    denied = api.post(
        "/v1/guests/guest-example/previews/fetch",
        json={"port": 22, "method": "GET", "target": "/"},
        headers=headers,
    )
    arbitrary = api.post(
        "/v1/guests/guest-example/previews/fetch",
        json={"port": 4173, "method": "POST", "target": "/", "headers": {}},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["content_base64"] == "PGgxPmd1ZXN0IHByZXZpZXc8L2gxPg=="
    assert denied.status_code == 403
    assert arbitrary.status_code == 422
    assert controller.calls == [("guest-example", 4173, "GET", "/status?q=1")]
