"""Inference-only reverse proxy with an exact LM Studio route allowlist."""

from __future__ import annotations

import http.client
import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol, cast
from urllib.parse import urlsplit

_GET_ROUTES = frozenset({"/v1/models"})
_POST_ROUTES = frozenset({"/v1/chat/completions"})
_MAX_REQUEST_BYTES = 8_388_608
_MAX_RESPONSE_BYTES = 67_108_864


class _ResponseHeaders(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None: ...


class _UpstreamResponse(Protocol):
    status: int
    headers: _ResponseHeaders

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self._socket_path)
        self.sock = connection


def _socket_upstream_path(path: str) -> str:
    base_path = os.environ.get("LM_STUDIO_UPSTREAM_BASE_PATH", "/v1").rstrip("/")
    if not base_path.startswith("/") or "?" in base_path or "#" in base_path:
        raise ValueError("invalid LM_STUDIO_UPSTREAM_BASE_PATH")
    return base_path + path.removeprefix("/v1")


@contextmanager
def _open_upstream(
    method: str,
    path: str,
    *,
    content: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> Iterator[_UpstreamResponse]:
    socket_path = os.environ.get("LM_STUDIO_UPSTREAM_SOCKET")
    if socket_path:
        connection = _UnixHTTPConnection(socket_path, timeout=timeout)
        unix_response: http.client.HTTPResponse | None = None
        try:
            connection.request(
                method,
                _socket_upstream_path(path),
                body=content,
                headers=headers,
            )
            unix_response = connection.getresponse()
            yield cast(_UpstreamResponse, unix_response)
        finally:
            if unix_response is not None:
                unix_response.close()
            connection.close()
        return

    upstream = os.environ["LM_STUDIO_UPSTREAM_URL"].rstrip("/")
    request = urllib.request.Request(
        upstream + path.removeprefix("/v1"),
        data=content,
        method=method,
        headers=headers,
    )
    try:
        url_response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        url_response = error
    with url_response:
        yield cast(_UpstreamResponse, url_response)


class InferenceProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OrchestratorInferenceProxy/1"

    def do_GET(self) -> None:
        if self.path == "/live":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/ready":
            self._ready()
            return
        self._proxy("GET", _GET_ROUTES)

    def do_POST(self) -> None:
        self._proxy("POST", _POST_ROUTES)

    def _proxy(self, method: str, allowed: frozenset[str]) -> None:
        parsed = urlsplit(self.path)
        if parsed.path not in allowed or parsed.query or parsed.fragment:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "route_not_allowed"})
            return
        content = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= _MAX_REQUEST_BYTES:
                self._write_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "request_too_large"},
                )
                return
            content = self.rfile.read(length)
        try:
            with _open_upstream(
                method,
                parsed.path,
                content=content if method == "POST" else None,
                headers={
                    "Authorization": "Bearer " + os.environ["LM_STUDIO_API_KEY"],
                    "Content-Type": self.headers.get(
                        "Content-Type", "application/json"
                    ),
                    "Accept": self.headers.get("Accept", "application/json"),
                    "Connection": "close",
                },
                timeout=360,
            ) as response:
                self._write_upstream_response(response)
        except (OSError, http.client.HTTPException, urllib.error.URLError):
            self._write_json(
                HTTPStatus.BAD_GATEWAY, {"error": "inference_upstream_unavailable"}
            )

    def _write_upstream_response(self, response: _UpstreamResponse) -> None:
        length_header = response.headers.get("Content-Length")
        if length_header is not None and int(length_header) > _MAX_RESPONSE_BYTES:
            self._write_json(
                HTTPStatus.BAD_GATEWAY, {"error": "inference_response_too_large"}
            )
            return
        self.send_response(response.status)
        self.send_header(
            "Content-Type",
            response.headers.get("Content-Type", "application/json")
            or "application/json",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        sent = 0
        while chunk := response.read(65_536):
            sent += len(chunk)
            if sent > _MAX_RESPONSE_BYTES:
                self.close_connection = True
                return
            self.wfile.write(chunk)
            self.wfile.flush()
        self.close_connection = True

    def _ready(self) -> None:
        try:
            with _open_upstream(
                "GET",
                "/v1/models",
                content=None,
                headers={
                    "Authorization": "Bearer " + os.environ["LM_STUDIO_API_KEY"],
                    "Connection": "close",
                },
                timeout=5,
            ) as response:
                if not 200 <= response.status < 300:
                    raise ValueError("model endpoint returned an error")
                content = response.read(2_097_153)
            payload = json.loads(content)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("data"), list
            ):
                raise ValueError("invalid model response")
            identifiers = {
                str(item.get("id"))
                for item in payload.get("data", [])
                if isinstance(item, dict)
            }
        except (
            OSError,
            http.client.HTTPException,
            ValueError,
            urllib.error.URLError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "inference_upstream_unready"},
            )
            return
        if (
            len(content) > 2_097_152
            or os.environ["LM_STUDIO_MODEL_ID"] not in identifiers
        ):
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "configured_model_not_loaded"},
            )
            return
        self._write_json(HTTPStatus.OK, {"status": "ready"})

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)
        self.close_connection = True


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8050), InferenceProxyHandler).serve_forever()


if __name__ == "__main__":
    main()
