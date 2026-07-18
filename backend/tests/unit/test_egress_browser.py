"""Egress and typed browser policy tests."""

from __future__ import annotations

import pytest

from orchestrator.browser_runtime import (
    BrowserRequest,
    BrowserRuntime,
    BrowserRuntimeError,
)
from orchestrator.egress import (
    ApprovedDestination,
    EgressDenied,
    EgressPolicy,
    issue_egress_token,
)


class Resolver:
    def __init__(self, records: dict[str, tuple[str, ...]]) -> None:
        self.records = records

    def resolve(self, hostname: str) -> tuple[str, ...]:
        return self.records[hostname]


class Browser:
    def execute(
        self, request: BrowserRequest, destination: ApprovedDestination | None
    ) -> str:
        assert destination is not None
        return f"{request.action}:{destination.hostname}:" + "x" * 100


def policy(records: dict[str, tuple[str, ...]] | None = None) -> EgressPolicy:
    return EgressPolicy(
        public_hosts=frozenset({"public.example"}),
        inference_host="inference.example",
        resolver=Resolver(
            records
            or {"public.example": ("8.8.8.8",), "inference.example": ("1.1.1.1",)}
        ),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://public.example:70000/",
        "http://private.example/",
        "http://inference.example/v1/files",
    ],
)
def test_egress_rejects_unsafe_hosts_and_management_routes(url: str) -> None:
    with pytest.raises((EgressDenied, ValueError)):
        policy().authorize(url)


def test_egress_blocks_private_resolution_and_dns_rebinding() -> None:
    with pytest.raises(EgressDenied, match="non_public_destination"):
        policy({"public.example": ("127.0.0.1",)}).authorize("https://public.example/")
    resolver = Resolver({"public.example": ("8.8.8.8",)})
    configured = EgressPolicy(
        public_hosts=frozenset({"public.example"}), resolver=resolver
    )
    approved = configured.authorize("https://public.example/")
    resolver.records["public.example"] = ("1.1.1.1",)
    with pytest.raises(EgressDenied, match="dns_rebinding_detected"):
        configured.validate_connection(approved, "8.8.8.8")


def test_egress_credentials_are_run_scoped_and_require_a_strong_secret() -> None:
    secret = "test-egress-secret-000000000000000000000000"
    first = issue_egress_token(secret, "run_0123456789abcdef01234567")
    assert len(first) == 64
    assert first == issue_egress_token(secret, "run_0123456789abcdef01234567")
    assert first != issue_egress_token(secret, "run_1123456789abcdef01234567")
    with pytest.raises(ValueError, match="invalid egress token input"):
        issue_egress_token("short", "run_0123456789abcdef01234567")


def test_browser_actions_are_role_scoped_and_bounded() -> None:
    runtime = BrowserRuntime(Browser(), policy())
    with pytest.raises(BrowserRuntimeError, match="browser_action_not_permitted"):
        runtime.execute(
            role="local-verifier",
            request=BrowserRequest(action="click", selector="#danger"),
        )
    navigated = runtime.execute(
        role="local-verifier",
        request=BrowserRequest(
            action="navigate", url="https://public.example/", max_output_bytes=12
        ),
    )
    assert navigated.truncated is True
    assert (
        runtime.execute(
            role="local-verifier", request=BrowserRequest(action="snapshot")
        ).destination
        is not None
    )
