"""Default-deny HTTP(S) destination and inference-route policy."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


class EgressDenied(Exception):
    """A URL, resolution, redirect, or connected address is outside policy."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HostResolver(Protocol):
    def resolve(self, hostname: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ApprovedDestination:
    url: str
    hostname: str
    port: int
    resolved_ips: tuple[str, ...]
    route_kind: str


class EgressPolicy:
    """Allows configured public HTTPS/HTTP hosts and two inference API routes."""

    def __init__(
        self,
        *,
        public_hosts: frozenset[str],
        resolver: HostResolver,
        inference_host: str | None = None,
    ) -> None:
        self._public_hosts = frozenset(
            host.lower().rstrip(".") for host in public_hosts
        )
        self._resolver = resolver
        self._inference_host = (
            inference_host.lower().rstrip(".") if inference_host else None
        )

    def authorize(self, url: str) -> ApprovedDestination:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EgressDenied("unsupported_url")
        if parsed.username or parsed.password or parsed.fragment:
            raise EgressDenied("unsafe_url_component")
        hostname = parsed.hostname.lower().rstrip(".")
        route_kind = self._route_kind(hostname, parsed.path)
        ips = self._resolve_public(hostname)
        return ApprovedDestination(
            url=url,
            hostname=hostname,
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            resolved_ips=ips,
            route_kind=route_kind,
        )

    def validate_connection(
        self, approved: ApprovedDestination, connected_ip: str
    ) -> None:
        self._validate_public_ip(connected_ip)
        current = self._resolve_public(approved.hostname)
        if connected_ip not in approved.resolved_ips or connected_ip not in current:
            raise EgressDenied("dns_rebinding_detected")

    def authorize_redirect(
        self, approved: ApprovedDestination, location: str
    ) -> ApprovedDestination:
        redirected = self.authorize(location)
        if redirected.route_kind != approved.route_kind:
            raise EgressDenied("redirect_route_kind_changed")
        return redirected

    def _route_kind(self, hostname: str, path: str) -> str:
        if hostname == self._inference_host:
            if path in {"/v1/models", "/v1/chat/completions"}:
                return "inference"
            raise EgressDenied("forbidden_inference_route")
        if hostname not in self._public_hosts:
            raise EgressDenied("host_not_allowlisted")
        return "public"

    def _resolve_public(self, hostname: str) -> tuple[str, ...]:
        if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(
            ".local"
        ):
            raise EgressDenied("host_not_public")
        try:
            addresses = self._resolver.resolve(hostname)
        except Exception as error:
            raise EgressDenied("dns_resolution_failed") from error
        if not addresses:
            raise EgressDenied("dns_resolution_empty")
        for address in addresses:
            self._validate_public_ip(address)
        return tuple(sorted(set(addresses)))

    @staticmethod
    def _validate_public_ip(address: str) -> None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise EgressDenied("invalid_resolved_address") from error
        if not ip.is_global:
            raise EgressDenied("non_public_destination")
