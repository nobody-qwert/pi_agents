"""Exact-route inference proxy contract tests."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from pytest import MonkeyPatch

from orchestrator.inference_proxy_main import InferenceProxyHandler
from orchestrator.lm_studio_relay_main import LmStudioRelayServer


class UpstreamHandler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []
    authorizations: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self._respond({"data": [{"id": "qwen3.6-27b"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self._respond({"choices": [{"message": {"content": "ok"}}]})

    def _respond(self, payload: dict[str, object]) -> None:
        type(self).paths.append(self.path)
        type(self).authorizations.append(self.headers.get("Authorization", ""))
        content = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_inference_proxy_allows_only_models_and_chat_routes(
    monkeypatch: MonkeyPatch,
) -> None:
    UpstreamHandler.paths = []
    UpstreamHandler.authorizations = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), InferenceProxyHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    upstream_thread.start()
    proxy_thread.start()
    upstream_port = upstream.server_address[1]
    proxy_port = proxy.server_address[1]
    monkeypatch.setenv("LM_STUDIO_UPSTREAM_URL", f"http://127.0.0.1:{upstream_port}/v1")
    monkeypatch.delenv("LM_STUDIO_UPSTREAM_SOCKET", raising=False)
    monkeypatch.setenv("LM_STUDIO_API_KEY", "private-test-key")
    monkeypatch.setenv("LM_STUDIO_MODEL_ID", "qwen3.6-27b")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/ready") as response:
            assert json.load(response) == {"status": "ready"}
        with urllib.request.urlopen(
            f"http://127.0.0.1:{proxy_port}/v1/models"
        ) as response:
            assert json.load(response)["data"][0]["id"] == "qwen3.6-27b"
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            data=b'{"model":"qwen3.6-27b","messages":[]}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            assert json.load(response)["choices"][0]["message"]["content"] == "ok"
        for path in ("/v1/config", "/v1/models?admin=true", "/api/v0/models"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}{path}")
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError("management route unexpectedly reached upstream")
        assert UpstreamHandler.paths == [
            "/v1/models",
            "/v1/models",
            "/v1/chat/completions",
        ]
        assert set(UpstreamHandler.authorizations) == {"Bearer private-test-key"}
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()
        proxy_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_inference_proxy_reaches_upstream_through_unix_relay(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    UpstreamHandler.paths = []
    UpstreamHandler.authorizations = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_port = upstream.server_address[1]
    socket_path = tmp_path / "lm-studio.sock"
    relay = LmStudioRelayServer(
        socket_path,
        upstream_host="127.0.0.1",
        upstream_port=upstream_port,
    )
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), InferenceProxyHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    upstream_thread.start()
    relay_thread.start()
    proxy_thread.start()
    proxy_port = proxy.server_address[1]
    monkeypatch.setenv("LM_STUDIO_UPSTREAM_SOCKET", str(socket_path))
    monkeypatch.setenv("LM_STUDIO_UPSTREAM_BASE_PATH", "/v1")
    monkeypatch.delenv("LM_STUDIO_UPSTREAM_URL", raising=False)
    monkeypatch.setenv("LM_STUDIO_API_KEY", "private-test-key")
    monkeypatch.setenv("LM_STUDIO_MODEL_ID", "qwen3.6-27b")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/ready") as response:
            assert json.load(response) == {"status": "ready"}
        with urllib.request.urlopen(
            f"http://127.0.0.1:{proxy_port}/v1/models"
        ) as response:
            assert json.load(response)["data"][0]["id"] == "qwen3.6-27b"
        assert UpstreamHandler.paths == ["/v1/models", "/v1/models"]
        assert set(UpstreamHandler.authorizations) == {"Bearer private-test-key"}
    finally:
        proxy.shutdown()
        relay.shutdown()
        upstream.shutdown()
        proxy.server_close()
        relay.server_close()
        upstream.server_close()
        proxy_thread.join(timeout=2)
        relay_thread.join(timeout=2)
        upstream_thread.join(timeout=2)


def test_lm_studio_relay_rejects_non_loopback_upstream(tmp_path: Path) -> None:
    try:
        LmStudioRelayServer(
            tmp_path / "lm-studio.sock",
            upstream_host="192.0.2.10",
            upstream_port=1234,
        )
    except ValueError as error:
        assert str(error) == "LM Studio relay upstream must be a loopback address"
    else:
        raise AssertionError("relay accepted a non-loopback upstream")


def test_lm_studio_relay_does_not_replace_regular_file(tmp_path: Path) -> None:
    socket_path = tmp_path / "lm-studio.sock"
    socket_path.write_text("do not replace")
    try:
        LmStudioRelayServer(
            socket_path,
            upstream_host="127.0.0.1",
            upstream_port=1234,
        )
    except ValueError as error:
        assert str(error) == "refusing to replace unsafe relay socket path"
    else:
        raise AssertionError("relay replaced a regular file")
    assert socket_path.read_text() == "do not replace"
