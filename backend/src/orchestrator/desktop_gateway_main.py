"""Authenticated WebSocket-to-VNC byte proxy for one disposable guest."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from orchestrator.commands import CommandError
from orchestrator.desktop_api import DesktopSessionAuthorizer
from orchestrator.persistence import PostgresUnitOfWork

_RUN_ID = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def build_app() -> FastAPI:
    unit_of_work = PostgresUnitOfWork(os.environ["DATABASE_URL"])
    authorizer = DesktopSessionAuthorizer(unit_of_work)
    vm_root = Path(os.environ.get("VM_DATA_ROOT", "/var/lib/orchestrator/vms")).resolve()
    app = FastAPI(title="Orchestrator Desktop Gateway", version="v1")

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.websocket("/ws/{session_id}")
    async def desktop(websocket: WebSocket, session_id: str, token: str) -> None:
        try:
            run_id = await asyncio.to_thread(
                authorizer.consume, session_id=session_id, token=token
            )
            socket_path = _vnc_path(vm_root, run_id)
            reader, writer = await asyncio.open_unix_connection(socket_path)
        except (CommandError, OSError, ValueError):
            await websocket.close(code=4401)
            return

        offered = websocket.headers.get("sec-websocket-protocol", "")
        subprotocol = "binary" if "binary" in offered.split(",") else None
        await websocket.accept(subprotocol=subprotocol)

        async def browser_to_guest() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if not isinstance(data, bytes):
                    raise ValueError("desktop gateway accepts binary frames only")
                writer.write(data)
                await writer.drain()

        async def guest_to_browser() -> None:
            while data := await reader.read(65_536):
                await websocket.send_bytes(data)

        tasks = {
            asyncio.create_task(browser_to_guest()),
            asyncio.create_task(guest_to_browser()),
        }
        try:
            _, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, WebSocketDisconnect, ValueError):
                    await task
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            with suppress(RuntimeError):
                await websocket.close()

    return app


def _vnc_path(vm_root: Path, run_id: str) -> str:
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("invalid run identity")
    guest_id = f"guest-{run_id.removeprefix('run_')}"
    path = (vm_root / guest_id / "vnc.sock").resolve()
    if not path.is_relative_to(vm_root) or path.is_symlink():
        raise ValueError("unsafe VNC socket path")
    return os.fspath(path)


app = build_app()
