"""Authenticated default-deny HTTP CONNECT proxy for disposable guests."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import os
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from orchestrator.egress import (
    ApprovedDestination,
    EgressDenied,
    EgressPolicy,
    issue_egress_token,
)

_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_HEADER_LIMIT = 65_536


class SystemResolver:
    """Resolve all TCP addresses without retaining resolver state."""

    def resolve(self, hostname: str) -> tuple[str, ...]:
        values = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        return tuple(sorted({str(value[4][0]) for value in values}))


class EgressAuditStore:
    """Small durable projection containing no URL paths or content."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, pool_pre_ping=True)

    def close(self) -> None:
        self._engine.dispose()

    def reserve(
        self,
        *,
        request_id: str,
        run_id: str,
        hostname: str,
        port: int,
        scheme: str,
        budget_bytes: int,
    ) -> Literal["allowed", "budget_exhausted", "unknown_run"]:
        with self._engine.begin() as connection:
            found = connection.execute(
                text("SELECT run_id FROM runs WHERE run_id = :run_id FOR UPDATE"),
                {"run_id": run_id},
            ).scalar()
            if found is None:
                return "unknown_run"
            used = connection.execute(
                text(
                    "SELECT COALESCE(SUM(bytes_up + bytes_down), 0) "
                    "FROM egress_requests WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
            outcome: Literal["allowed", "budget_exhausted"] = (
                "allowed" if int(used) < budget_bytes else "budget_exhausted"
            )
            connection.execute(
                text(
                    "INSERT INTO egress_requests (request_id, run_id, requested_at, "
                    "hostname, port, scheme, decision, reason_code, resolved_ips) "
                    "VALUES (:request_id, :run_id, :now, :hostname, :port, :scheme, "
                    ":decision, :reason_code, '[]'::jsonb)"
                ),
                {
                    "request_id": request_id,
                    "run_id": run_id,
                    "now": datetime.now(UTC),
                    "hostname": hostname[:253],
                    "port": port,
                    "scheme": scheme,
                    "decision": "allowed" if outcome == "allowed" else "denied",
                    "reason_code": (
                        "budget_reserved"
                        if outcome == "allowed"
                        else "run_budget_exhausted"
                    ),
                },
            )
        return outcome

    def start(
        self,
        *,
        request_id: str,
        run_id: str,
        hostname: str,
        port: int,
        scheme: str,
        decision: Literal["allowed", "denied", "failed"],
        reason_code: str,
        resolved_ips: tuple[str, ...] = (),
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO egress_requests (request_id, run_id, requested_at, "
                    "hostname, port, scheme, decision, reason_code, resolved_ips) "
                    "VALUES (:request_id, :run_id, :now, :hostname, :port, :scheme, "
                    ":decision, :reason_code, CAST(:resolved_ips AS jsonb))"
                ),
                {
                    "request_id": request_id,
                    "run_id": run_id,
                    "now": datetime.now(UTC),
                    "hostname": hostname[:253],
                    "port": port,
                    "scheme": scheme,
                    "decision": decision,
                    "reason_code": reason_code,
                    "resolved_ips": json.dumps(resolved_ips),
                },
            )

    def classify(
        self,
        request_id: str,
        *,
        decision: Literal["allowed", "denied"],
        reason_code: str,
        hostname: str,
        port: int,
        scheme: str,
        resolved_ips: tuple[str, ...] = (),
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE egress_requests SET hostname = :hostname, port = :port, "
                    "scheme = :scheme, decision = :decision, reason_code = :reason_code, "
                    "resolved_ips = CAST(:resolved_ips AS jsonb) "
                    "WHERE request_id = :request_id"
                ),
                {
                    "request_id": request_id,
                    "hostname": hostname[:253],
                    "port": port,
                    "scheme": scheme,
                    "decision": decision,
                    "reason_code": reason_code,
                    "resolved_ips": json.dumps(resolved_ips),
                },
            )

    def finish(
        self,
        request_id: str,
        *,
        bytes_up: int,
        bytes_down: int,
        failed: bool,
        failure_reason: str = "connection_failed",
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE egress_requests SET completed_at = :now, "
                    "bytes_up = :bytes_up, bytes_down = :bytes_down, "
                    "decision = CASE WHEN :failed THEN 'failed' ELSE decision END, "
                    "reason_code = CASE WHEN :failed THEN :failure_reason "
                    "ELSE reason_code END WHERE request_id = :request_id"
                ),
                {
                    "request_id": request_id,
                    "now": datetime.now(UTC),
                    "bytes_up": bytes_up,
                    "bytes_down": bytes_down,
                    "failed": failed,
                    "failure_reason": failure_reason,
                },
            )


@dataclass(slots=True)
class _Transfer:
    up: int = 0
    down: int = 0
    failed: bool = False


class GuestEgressProxy:
    """One-request-per-connection proxy with a shared transfer budget."""

    def __init__(
        self,
        *,
        policy: EgressPolicy,
        audit: EgressAuditStore,
        auth_secret: str,
        connection_budget_bytes: int,
        run_budget_bytes: int,
        timeout_seconds: int,
    ) -> None:
        if len(auth_secret) < 32:
            raise ValueError("egress auth secret is too short")
        if connection_budget_bytes < 1 or run_budget_bytes < connection_budget_bytes:
            raise ValueError("egress transfer budgets are invalid")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("egress timeout is outside the allowed range")
        self._policy = policy
        self._audit = audit
        self._secret = auth_secret
        self._connection_budget = connection_budget_bytes
        self._run_budget = run_budget_bytes
        self._timeout = timeout_seconds

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_id = "egress_" + secrets.token_hex(16)
        run_id: str | None = None
        audited = False
        transfer = _Transfer()
        remote_writer: asyncio.StreamWriter | None = None
        policy_allowed = False
        failure_reason = "connection_failed"
        audit_hostname = "invalid"
        audit_port = 1
        audit_scheme = "http"
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if len(raw) > _HEADER_LIMIT:
                raise EgressDenied("request_headers_too_large")
            request_line, headers = self._parse_headers(raw)
            method, target, version = request_line.split(" ", 2)
            run_id = self._credentials(headers)
            audit_hostname, audit_port, audit_scheme = self._audit_target(
                method, target
            )
            reservation = await asyncio.to_thread(
                self._audit.reserve,
                request_id=request_id,
                run_id=run_id,
                hostname=audit_hostname,
                port=audit_port,
                scheme=audit_scheme,
                budget_bytes=self._run_budget,
            )
            if reservation == "unknown_run":
                raise EgressDenied("run_unknown_or_budget_exhausted")
            audited = True
            if reservation == "budget_exhausted":
                raise EgressDenied("run_budget_exhausted")
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise EgressDenied("unsupported_http_version")
            destination, scheme = self._destination(method, target)
            await asyncio.to_thread(
                self._audit.classify,
                request_id,
                decision="allowed",
                reason_code="policy_allowed",
                hostname=destination.hostname,
                port=destination.port,
                scheme=scheme,
                resolved_ips=destination.resolved_ips,
            )
            policy_allowed = True
            remote_reader, remote_writer = await self._connect(destination)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                remote_writer.write(
                    self._origin_request(method, target, version, headers, destination)
                )
                await remote_writer.drain()
            await self._relay(reader, writer, remote_reader, remote_writer, transfer)
        except (EgressDenied, ValueError, asyncio.IncompleteReadError) as error:
            code = error.code if isinstance(error, EgressDenied) else "invalid_request"
            if audited and not policy_allowed:
                await asyncio.to_thread(
                    self._audit.classify,
                    request_id,
                    decision="denied",
                    reason_code=code,
                    hostname=audit_hostname,
                    port=audit_port,
                    scheme=audit_scheme,
                )
            elif policy_allowed:
                transfer.failed = True
                failure_reason = code
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
                if policy_allowed
                else b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        except Exception:
            transfer.failed = True
            failure_reason = "proxy_internal_failure"
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            with contextlib.suppress(Exception):
                await writer.drain()
        finally:
            if remote_writer is not None:
                remote_writer.close()
                with contextlib.suppress(Exception):
                    await remote_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if audited:
                await asyncio.to_thread(
                    self._audit.finish,
                    request_id,
                    bytes_up=transfer.up,
                    bytes_down=transfer.down,
                    failed=transfer.failed,
                    failure_reason=failure_reason,
                )

    def _credentials(self, headers: dict[str, str]) -> str:
        value = headers.get("proxy-authorization", "")
        kind, _, encoded = value.partition(" ")
        if kind.lower() != "basic" or not encoded:
            raise EgressDenied("proxy_auth_required")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise EgressDenied("proxy_auth_invalid") from error
        run_id, separator, token = decoded.partition(":")
        try:
            expected = issue_egress_token(self._secret, run_id)
        except ValueError as error:
            raise EgressDenied("proxy_auth_invalid") from error
        if (
            not separator
            or _RUN_ID.fullmatch(run_id) is None
            or not hmac.compare_digest(expected, token)
        ):
            raise EgressDenied("proxy_auth_invalid")
        return run_id

    @staticmethod
    def _audit_target(method: str, target: str) -> tuple[str, int, str]:
        try:
            if method == "CONNECT":
                parsed = urlsplit(f"//{target}")
                return parsed.hostname or "invalid", parsed.port or 443, "https"
            parsed = urlsplit(target)
            return (
                parsed.hostname or "invalid",
                parsed.port or 80,
                parsed.scheme or "http",
            )
        except ValueError:
            return "invalid", 1, "http"

    @staticmethod
    def _parse_headers(raw: bytes) -> tuple[str, dict[str, str]]:
        try:
            lines = raw.decode("iso-8859-1").split("\r\n")
        except UnicodeDecodeError as error:
            raise EgressDenied("invalid_headers") from error
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            name, separator, value = line.partition(":")
            if not separator or not name or name.strip() != name:
                raise EgressDenied("invalid_headers")
            lower = name.lower()
            if lower in headers:
                raise EgressDenied("duplicate_header")
            headers[lower] = value.strip()
        return lines[0], headers

    def _destination(self, method: str, target: str) -> tuple[ApprovedDestination, str]:
        if method == "CONNECT":
            parsed = urlsplit(f"//{target}")
            if not parsed.hostname or parsed.username or parsed.password:
                raise EgressDenied("invalid_connect_target")
            port = parsed.port or 443
            if port != 443:
                raise EgressDenied("egress_port_not_allowed")
            return self._policy.authorize(f"https://{parsed.hostname}:{port}/"), "https"
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise EgressDenied("method_not_allowed")
        parsed = urlsplit(target)
        if parsed.scheme != "http" or (parsed.port or 80) != 80:
            raise EgressDenied("plain_proxy_target_invalid")
        return self._policy.authorize(target), "http"

    async def _connect(
        self, destination: ApprovedDestination
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        for address in destination.resolved_ips:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(address, destination.port), 10
                )
                peer = writer.get_extra_info("peername")
                connected = str(peer[0]) if isinstance(peer, tuple) else address
                self._policy.validate_connection(destination, connected)
                return reader, writer
            except (OSError, EgressDenied, TimeoutError):
                continue
        raise EgressDenied("destination_connection_failed")

    @staticmethod
    def _origin_request(
        method: str,
        target: str,
        version: str,
        headers: dict[str, str],
        destination: ApprovedDestination,
    ) -> bytes:
        parsed = urlsplit(target)
        origin = parsed.path or "/"
        if parsed.query:
            origin += "?" + parsed.query
        retained = [
            f"{name}: {value}"
            for name, value in headers.items()
            if name
            not in {"host", "proxy-authorization", "proxy-connection", "connection"}
        ]
        retained.extend((f"host: {destination.hostname}", "connection: close"))
        return (
            f"{method} {origin} {version}\r\n" + "\r\n".join(retained) + "\r\n\r\n"
        ).encode("iso-8859-1")

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
        transfer: _Transfer,
    ) -> None:
        async def copy(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
            direction: Literal["up", "down"],
        ) -> None:
            while chunk := await source.read(65_536):
                if transfer.up + transfer.down + len(chunk) > self._connection_budget:
                    raise EgressDenied("connection_budget_exhausted")
                if direction == "up":
                    transfer.up += len(chunk)
                else:
                    transfer.down += len(chunk)
                destination.write(chunk)
                await destination.drain()

        tasks = (
            asyncio.create_task(copy(client_reader, remote_writer, "up")),
            asyncio.create_task(copy(remote_reader, client_writer, "down")),
        )
        done, pending = await asyncio.wait(
            tasks, timeout=self._timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if not done:
            raise EgressDenied("connection_timeout")
        for task in done:
            task.result()


async def _health(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(reader.read(4096), 2)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b'Content-Length: 17\r\nConnection: close\r\n\r\n{"status":"ok"}'
        )
        await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _serve() -> None:
    public_hosts = frozenset(
        value.strip().lower()
        for value in os.environ.get("EGRESS_PUBLIC_HOSTS", "").split(",")
        if value.strip()
    )
    proxy = GuestEgressProxy(
        policy=EgressPolicy(public_hosts=public_hosts, resolver=SystemResolver()),
        audit=EgressAuditStore(os.environ["DATABASE_URL"]),
        auth_secret=os.environ["EGRESS_AUTH_SECRET"],
        connection_budget_bytes=int(
            os.environ.get("EGRESS_CONNECTION_BUDGET_BYTES", "52428800")
        ),
        run_budget_bytes=int(os.environ.get("EGRESS_RUN_BUDGET_BYTES", "209715200")),
        timeout_seconds=int(os.environ.get("EGRESS_TIMEOUT_SECONDS", "120")),
    )
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 8080)
    health = await asyncio.start_server(_health, "0.0.0.0", 8060)
    async with server, health:
        await asyncio.gather(server.serve_forever(), health.serve_forever())


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
