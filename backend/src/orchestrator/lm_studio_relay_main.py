"""Loopback-only LM Studio TCP to Unix-domain-socket relay."""

from __future__ import annotations

import ipaddress
import os
import selectors
import socket
import socketserver
import stat
from contextlib import suppress
from pathlib import Path

_BUFFER_SIZE = 65_536


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, LmStudioRelayServer):
            raise RuntimeError("invalid relay server")
        client = self.request
        if not isinstance(client, socket.socket):
            raise RuntimeError("invalid relay connection")
        with socket.create_connection(
            (server.upstream_host, server.upstream_port), timeout=5
        ) as upstream:
            client.settimeout(None)
            upstream.settimeout(None)
            self._copy_bidirectionally(client, upstream)

    @staticmethod
    def _copy_bidirectionally(client: socket.socket, upstream: socket.socket) -> None:
        peers = {client: upstream, upstream: client}
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ)
        selector.register(upstream, selectors.EVENT_READ)
        try:
            while selector.get_map():
                for key, _ in selector.select():
                    source = key.fileobj
                    if not isinstance(source, socket.socket):
                        raise RuntimeError("invalid relay source")
                    content = source.recv(_BUFFER_SIZE)
                    if content:
                        peers[source].sendall(content)
                        continue
                    selector.unregister(source)
                    with suppress(OSError):
                        peers[source].shutdown(socket.SHUT_WR)
        finally:
            selector.close()


class LmStudioRelayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = True

    def __init__(
        self, socket_path: Path, *, upstream_host: str, upstream_port: int
    ) -> None:
        address = ipaddress.ip_address(upstream_host)
        if not address.is_loopback:
            raise ValueError("LM Studio relay upstream must be a loopback address")
        if not 1 <= upstream_port <= 65_535:
            raise ValueError("LM Studio relay upstream port is invalid")
        if not socket_path.is_absolute():
            raise ValueError("LM Studio relay socket path must be absolute")
        socket_path.parent.resolve(strict=True)
        self._prepare_socket_path(socket_path)
        self.socket_path = socket_path
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        super().__init__(os.fspath(socket_path), _RelayHandler)
        socket_path.chmod(0o660)

    @staticmethod
    def _prepare_socket_path(socket_path: Path) -> None:
        try:
            metadata = socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError("refusing to replace unsafe relay socket path")
        socket_path.unlink()

    def server_close(self) -> None:
        super().server_close()
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.getuid():
            self.socket_path.unlink()


def main() -> None:
    socket_path = Path(
        os.environ.get("LM_STUDIO_RELAY_SOCKET", "/run/lm-studio/lm-studio.sock")
    )
    upstream_host = os.environ.get("LM_STUDIO_RELAY_UPSTREAM_HOST", "127.0.0.1")
    upstream_port = int(os.environ.get("LM_STUDIO_RELAY_UPSTREAM_PORT", "1234"))
    with LmStudioRelayServer(
        socket_path, upstream_host=upstream_host, upstream_port=upstream_port
    ) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
