"""Typed HTTP and QEMU/KVM adapters for the trusted VM-manager boundary."""

from __future__ import annotations

import base64
import fcntl
import hmac
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import ConfigDict, Field, model_validator

from orchestrator.domain.primitives import StrictDomainModel
from orchestrator.egress import issue_egress_token
from orchestrator.pi_rpc import PiRole, PiRpcError, PiRpcResult, PiToolEvent, run_pi_rpc
from orchestrator.projects import ProjectFile
from orchestrator.vm import GuestHandle, VmAdapter, VmLifecycleError
from orchestrator.workspace import GuestBaseline, WorkspaceImportError

_RUN_ID = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_GUEST_ID = re.compile(r"^guest-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_OVERLAY_ID = re.compile(r"^overlay-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PI_CONFIG_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_PI_ROLE_TOOLS: dict[PiRole, tuple[str, ...]] = {
    "executor": (
        "read",
        "write",
        "edit",
        "bash",
        "grep",
        "find",
        "ls",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_input",
        "browser_screenshot",
        "browser_console",
        "browser_network",
    ),
    "local-verifier": (
        "read",
        "bash",
        "grep",
        "find",
        "ls",
        "browser_navigate",
        "browser_snapshot",
        "browser_screenshot",
        "browser_console",
        "browser_network",
    ),
    "integrator": (
        "read",
        "write",
        "edit",
        "bash",
        "grep",
        "find",
        "ls",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_input",
        "browser_screenshot",
        "browser_console",
        "browser_network",
    ),
    "outcome-verifier": (
        "read",
        "bash",
        "grep",
        "find",
        "ls",
        "browser_navigate",
        "browser_snapshot",
        "browser_screenshot",
        "browser_console",
        "browser_network",
    ),
}


class _BinaryReader(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


@dataclass(frozen=True, slots=True)
class VmManagerConfig:
    base_image: Path
    vm_root: Path
    ssh_private_key: Path = Path("/var/lib/orchestrator/ssh/id_ed25519")
    ssh_known_hosts: Path = Path("/var/lib/orchestrator/ssh/known_hosts")
    qemu_system_binary: str = "qemu-system-x86_64"
    qemu_img_binary: str = "qemu-img"
    memory_mb: int = 4096
    cpu_count: int = 4
    operation_timeout_seconds: int = 30
    pi_binary: str = "pi"
    pi_provider: str = "lm-studio"
    pi_model: str = "qwen3.6-27b"
    pi_attempt_timeout_seconds: int = 300
    pi_browser_extension: str = "/opt/orchestrator/pi/browser-tools.ts"
    egress_proxy_host: str = "egress-proxy"
    egress_proxy_port: int = 8080
    inference_proxy_host: str = "inference-proxy"
    inference_proxy_port: int = 8050
    preview_ports: tuple[int, ...] = (3000, 4173, 5173, 8000, 8080)
    egress_auth_secret: str = "test-egress-auth-secret-000000000000000000000"

    @classmethod
    def from_environment(cls) -> VmManagerConfig:
        base_image = Path(os.environ["PI_VM_BASE_IMAGE"])
        vm_root = Path(os.environ["PI_VM_ROOT"])
        if not base_image.is_absolute() or not vm_root.is_absolute():
            raise ValueError("VM manager paths must be absolute")
        resolved_root = vm_root.resolve()
        if resolved_root in {Path("/"), Path.home().resolve()}:
            raise ValueError("PI_VM_ROOT is too broad")
        return cls(
            base_image=base_image.resolve(),
            vm_root=resolved_root,
            ssh_private_key=Path(
                os.environ.get(
                    "PI_VM_SSH_PRIVATE_KEY", "/var/lib/orchestrator/ssh/id_ed25519"
                )
            ).resolve(),
            ssh_known_hosts=Path(
                os.environ.get(
                    "PI_VM_SSH_KNOWN_HOSTS", "/var/lib/orchestrator/ssh/known_hosts"
                )
            ).resolve(),
            qemu_system_binary=os.environ.get(
                "QEMU_SYSTEM_BINARY", "qemu-system-x86_64"
            ),
            qemu_img_binary=os.environ.get("QEMU_IMG_BINARY", "qemu-img"),
            memory_mb=int(os.environ.get("PI_VM_MEMORY_MB", "4096")),
            cpu_count=int(os.environ.get("PI_VM_CPU_COUNT", "4")),
            operation_timeout_seconds=int(
                os.environ.get("PI_VM_OPERATION_TIMEOUT_SECONDS", "30")
            ),
            pi_binary=os.environ.get("PI_GUEST_BINARY", "pi"),
            pi_provider=os.environ.get("PI_GUEST_PROVIDER", "lm-studio"),
            pi_model=os.environ.get("PI_GUEST_MODEL", "qwen3.6-27b"),
            pi_attempt_timeout_seconds=int(
                os.environ.get("PI_GUEST_ATTEMPT_TIMEOUT_SECONDS", "300")
            ),
            pi_browser_extension=os.environ.get(
                "PI_BROWSER_EXTENSION", "/opt/orchestrator/pi/browser-tools.ts"
            ),
            egress_proxy_host=cls._proxy_host(
                os.environ.get("EGRESS_PROXY_HOST", "egress-proxy")
            ),
            egress_proxy_port=cls._proxy_port(
                os.environ.get("EGRESS_PROXY_PORT", "8080")
            ),
            inference_proxy_host=cls._proxy_host(
                os.environ.get("INFERENCE_PROXY_HOST", "inference-proxy")
            ),
            inference_proxy_port=cls._proxy_port(
                os.environ.get("INFERENCE_PROXY_PORT", "8050")
            ),
            preview_ports=cls._preview_ports(
                os.environ.get("PI_PREVIEW_PORTS", "3000,4173,5173,8000,8080")
            ),
            egress_auth_secret=os.environ["EGRESS_AUTH_SECRET"],
        )

    @staticmethod
    def _proxy_host(value: str) -> str:
        if (
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value)
            is None
        ):
            raise ValueError("invalid proxy host")
        return value

    @staticmethod
    def _proxy_port(value: str) -> int:
        port = int(value)
        if not 1 <= port <= 65_535:
            raise ValueError("invalid proxy port")
        return port

    @classmethod
    def _preview_ports(cls, value: str) -> tuple[int, ...]:
        ports = tuple(dict.fromkeys(cls._proxy_port(item.strip()) for item in value.split(",") if item.strip()))
        if not ports or len(ports) > 16:
            raise ValueError("PI_PREVIEW_PORTS must contain 1-16 ports")
        return ports


@dataclass(frozen=True, slots=True)
class VmPreflight:
    ready: bool
    kvm_available: bool
    base_image_available: bool
    qemu_system_available: bool
    qemu_img_available: bool
    vm_root_writable: bool
    guest_ssh_key_available: bool = False
    guest_known_hosts_available: bool = False


@dataclass(frozen=True, slots=True)
class _VmMetadata:
    run_id: str
    guest_id: str
    overlay_id: str
    overlay_path: str
    qmp_path: str
    pid_path: str
    log_path: str
    ssh_port: int
    vnc_path: str


class QemuVmAdapter(VmAdapter):
    """Service-owned QEMU operations derived only from validated identifiers."""

    def __init__(self, config: VmManagerConfig) -> None:
        if config.memory_mb < 512 or config.memory_mb > 131_072:
            raise ValueError("PI_VM_MEMORY_MB is outside the allowed range")
        if config.cpu_count < 1 or config.cpu_count > 64:
            raise ValueError("PI_VM_CPU_COUNT is outside the allowed range")
        if not 5 <= config.operation_timeout_seconds <= 300:
            raise ValueError("PI_VM_OPERATION_TIMEOUT_SECONDS is outside range")
        self._config = config
        self._config.vm_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def preflight(self) -> VmPreflight:
        kvm = Path("/dev/kvm")
        checks = {
            "kvm_available": kvm.is_char_device() and os.access(kvm, os.R_OK | os.W_OK),
            "base_image_available": self._config.base_image.is_file()
            and not self._config.base_image.is_symlink(),
            "qemu_system_available": shutil.which(self._config.qemu_system_binary)
            is not None,
            "qemu_img_available": shutil.which(self._config.qemu_img_binary)
            is not None,
            "vm_root_writable": self._config.vm_root.is_dir()
            and os.access(self._config.vm_root, os.R_OK | os.W_OK | os.X_OK),
            "guest_ssh_key_available": self._config.ssh_private_key.is_file()
            and not self._config.ssh_private_key.is_symlink(),
            "guest_known_hosts_available": self._config.ssh_known_hosts.is_file()
            and not self._config.ssh_known_hosts.is_symlink(),
        }
        return VmPreflight(ready=all(checks.values()), **checks)

    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        self._validate_binding(run_id, guest_id, overlay_id)
        self._require_preflight()
        with self._guest_lock(guest_id):
            metadata = self._metadata(run_id, guest_id, overlay_id)
            existing = self._read_metadata(guest_id)
            if existing is not None:
                if existing != metadata:
                    raise VmLifecycleError("vm_identity_conflict")
                if self._is_owned_process_live(existing):
                    return
                self._remove_stale_runtime(existing)
            overlay_path = Path(metadata.overlay_path)
            if overlay_path.exists():
                raise VmLifecycleError("stale_overlay_requires_cleanup")
            self._run(
                (
                    self._config.qemu_img_binary,
                    "create",
                    "-f",
                    "qcow2",
                    "-F",
                    "qcow2",
                    "-b",
                    os.fspath(self._config.base_image),
                    os.fspath(overlay_path),
                ),
                "overlay_create_failed",
            )
            try:
                self._run(self._qemu_argv(metadata), "qemu_start_failed")
                self._write_metadata(metadata)
                if not self._is_owned_process_live(metadata):
                    raise VmLifecycleError("qemu_start_failed")
            except Exception:
                with suppress(OSError):
                    overlay_path.unlink()
                self._remove_stale_runtime(metadata)
                raise

    def probe_ready(self, guest_id: str) -> bool:
        self._validate_guest_id(guest_id)
        with self._guest_lock(guest_id):
            metadata = self._read_metadata(guest_id)
            if metadata is None or not self._is_owned_process_live(metadata):
                return False
            if self._qmp_command(metadata, "query-status") is None:
                return False
            try:
                with socket.create_connection(
                    ("127.0.0.1", metadata.ssh_port), timeout=1.0
                ):
                    return True
            except OSError:
                return False

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        self._validate_destroy_binding(guest_id, overlay_id)
        with self._guest_lock(guest_id):
            metadata = self._read_metadata(guest_id)
            if metadata is not None and metadata.overlay_id != overlay_id:
                raise VmLifecycleError("vm_identity_conflict")
            if metadata is None:
                metadata = self._metadata(
                    f"run_{guest_id.removeprefix('guest-')}", guest_id, overlay_id
                )
            if self._is_owned_process_live(metadata):
                self._qmp_command(metadata, "quit")
                self._wait_stopped(metadata, 5.0)
            if self._is_owned_process_live(metadata):
                self._signal_owned(metadata, signal.SIGTERM)
                self._wait_stopped(metadata, 5.0)
            if self._is_owned_process_live(metadata):
                self._signal_owned(metadata, signal.SIGKILL)
                self._wait_stopped(metadata, 2.0)
            if self._is_owned_process_live(metadata):
                raise VmLifecycleError("qemu_destroy_failed")
            overlay = Path(metadata.overlay_path)
            if overlay.exists():
                if overlay.is_symlink() or not overlay.is_file():
                    raise VmLifecycleError("unsafe_overlay_target")
                overlay.unlink()
            self._remove_stale_runtime(metadata)

    def ssh_endpoint(self, guest_id: str) -> tuple[str, int]:
        """Return only the manager-local endpoint for a live owned guest."""
        self._validate_guest_id(guest_id)
        with self._guest_lock(guest_id):
            metadata = self._read_metadata(guest_id)
            if metadata is None or not self._is_owned_process_live(metadata):
                raise VmLifecycleError("guest_not_live")
            return "127.0.0.1", metadata.ssh_port

    def _require_preflight(self) -> None:
        if not self.preflight().ready:
            raise VmLifecycleError("kvm_preflight_failed")

    def _metadata(self, run_id: str, guest_id: str, overlay_id: str) -> _VmMetadata:
        guest_dir = self._guest_dir(guest_id)
        guest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        port = 20_000 + int(sha256(run_id.encode()).hexdigest()[:8], 16) % 20_000
        return _VmMetadata(
            run_id=run_id,
            guest_id=guest_id,
            overlay_id=overlay_id,
            overlay_path=os.fspath(guest_dir / f"{overlay_id}.qcow2"),
            qmp_path=os.fspath(guest_dir / "qmp.sock"),
            pid_path=os.fspath(guest_dir / "qemu.pid"),
            log_path=os.fspath(guest_dir / "qemu.log"),
            ssh_port=port,
            vnc_path=os.fspath(guest_dir / "vnc.sock"),
        )

    def _qemu_argv(self, metadata: _VmMetadata) -> tuple[str, ...]:
        network = (
            "user,id=net0,restrict=on,"
            f"hostfwd=tcp:127.0.0.1:{metadata.ssh_port}-:22,"
            "guestfwd=tcp:10.0.2.100:3128-"
            f"tcp:{self._config.egress_proxy_host}:{self._config.egress_proxy_port},"
            "guestfwd=tcp:10.0.2.101:1234-"
            f"tcp:{self._config.inference_proxy_host}:"
            f"{self._config.inference_proxy_port}"
        )
        return (
            self._config.qemu_system_binary,
            "-machine",
            "q35,accel=kvm",
            "-cpu",
            "host",
            "-smp",
            str(self._config.cpu_count),
            "-m",
            str(self._config.memory_mb),
            "-drive",
            f"file={metadata.overlay_path},format=qcow2,if=virtio,cache=none",
            "-netdev",
            network,
            "-device",
            "virtio-net-pci,netdev=net0",
            "-qmp",
            f"unix:{metadata.qmp_path},server=on,wait=off",
            "-vnc",
            f"unix:{metadata.vnc_path},share=ignore",
            "-monitor",
            "none",
            "-daemonize",
            "-pidfile",
            metadata.pid_path,
            "-D",
            metadata.log_path,
            "-no-reboot",
            "-sandbox",
            "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
        )

    def _run(self, argv: tuple[str, ...], error_code: str) -> None:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self._config.operation_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise VmLifecycleError(error_code) from error
        if completed.returncode != 0:
            raise VmLifecycleError(error_code)

    def _qmp_command(
        self, metadata: _VmMetadata, command: Literal["query-status", "quit"]
    ) -> dict[str, object] | None:
        qmp_path = Path(metadata.qmp_path)
        if not qmp_path.exists() or qmp_path.is_symlink():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2.0)
                connection.connect(metadata.qmp_path)
                stream = connection.makefile("rwb", buffering=0)
                self._read_qmp(stream)
                stream.write(b'{"execute":"qmp_capabilities"}\r\n')
                self._read_qmp(stream)
                stream.write(
                    json.dumps({"execute": command}, separators=(",", ":")).encode()
                    + b"\r\n"
                )
                return self._read_qmp(stream)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _read_qmp(stream: _BinaryReader) -> dict[str, object]:
        line = stream.readline(65_537)
        if not line or len(line) > 65_536:
            raise ValueError("invalid QMP response")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid QMP response")
        return cast(dict[str, object], value)

    def _is_owned_process_live(self, metadata: _VmMetadata) -> bool:
        pid = self._read_pid(metadata)
        if pid is None:
            return False
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            return False
        expected_binary = Path(self._config.qemu_system_binary).name.encode()
        return (
            bool(command)
            and Path(os.fsdecode(command[0])).name.encode() == expected_binary
            and any(metadata.overlay_path.encode() in argument for argument in command)
        )

    def _read_pid(self, metadata: _VmMetadata) -> int | None:
        path = Path(metadata.pid_path)
        try:
            if path.is_symlink() or not path.is_file():
                return None
            value = int(path.read_text(encoding="ascii").strip())
            if value <= 1:
                return None
            os.kill(value, 0)
            return value
        except (OSError, ValueError):
            return None

    def _signal_owned(
        self, metadata: _VmMetadata, requested_signal: signal.Signals
    ) -> None:
        pid = self._read_pid(metadata)
        if pid is None or not self._is_owned_process_live(metadata):
            return
        os.kill(pid, requested_signal)

    def _wait_stopped(self, metadata: _VmMetadata, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self._is_owned_process_live(metadata) and time.monotonic() < deadline:
            time.sleep(0.05)

    def _remove_stale_runtime(self, metadata: _VmMetadata) -> None:
        for path in (
            Path(metadata.qmp_path),
            Path(metadata.vnc_path),
            Path(metadata.pid_path),
            self._guest_dir(metadata.guest_id) / "metadata.json",
        ):
            with suppress(FileNotFoundError):
                if path.is_symlink():
                    raise VmLifecycleError("unsafe_vm_runtime_target")
                path.unlink()

    def _write_metadata(self, metadata: _VmMetadata) -> None:
        destination = self._guest_dir(metadata.guest_id) / "metadata.json"
        temporary = destination.with_suffix(".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
                json.dump(asdict(metadata), output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)

    def _read_metadata(self, guest_id: str) -> _VmMetadata | None:
        path = self._guest_dir(guest_id) / "metadata.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise VmLifecycleError("unsafe_vm_metadata_target")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "run_id",
                "guest_id",
                "overlay_id",
                "overlay_path",
                "qmp_path",
                "pid_path",
                "log_path",
                "ssh_port",
                "vnc_path",
            }:
                raise ValueError
            metadata = _VmMetadata(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise VmLifecycleError("invalid_vm_metadata") from error
        expected = self._metadata(
            metadata.run_id, metadata.guest_id, metadata.overlay_id
        )
        if metadata != expected or metadata.guest_id != guest_id:
            raise VmLifecycleError("vm_metadata_binding_mismatch")
        return metadata

    @contextmanager
    def _guest_lock(self, guest_id: str) -> Iterator[None]:
        guest_dir = self._guest_dir(guest_id)
        guest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = guest_dir / "operation.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _guest_dir(self, guest_id: str) -> Path:
        self._validate_guest_id(guest_id)
        destination = (self._config.vm_root / guest_id).resolve()
        if not destination.is_relative_to(self._config.vm_root):
            raise VmLifecycleError("vm_path_escape")
        return destination

    @staticmethod
    def _validate_guest_id(guest_id: str) -> None:
        if _GUEST_ID.fullmatch(guest_id) is None:
            raise VmLifecycleError("invalid_guest_id")

    @staticmethod
    def _validate_destroy_binding(guest_id: str, overlay_id: str) -> None:
        if (
            _GUEST_ID.fullmatch(guest_id) is None
            or _OVERLAY_ID.fullmatch(overlay_id) is None
            or guest_id.removeprefix("guest-") != overlay_id.removeprefix("overlay-")
        ):
            raise VmLifecycleError("invalid_vm_binding")

    @staticmethod
    def _validate_binding(run_id: str, guest_id: str, overlay_id: str) -> None:
        suffix = run_id.removeprefix("run_")
        if (
            _RUN_ID.fullmatch(run_id) is None
            or guest_id != f"guest-{suffix}"
            or overlay_id != f"overlay-{suffix}"
        ):
            raise VmLifecycleError("invalid_vm_binding")


class ManagerWorkspaceController(Protocol):
    def prepare(self, guest_id: str, guest_path: str) -> None: ...

    def write_file(self, guest_id: str, guest_path: str, file: ProjectFile) -> None: ...

    def create_baseline(self, guest_id: str, guest_path: str) -> GuestBaseline: ...

    def cleanup(self, guest_id: str, guest_path: str) -> None: ...

    def checkpoint(
        self, guest_id: str, guest_path: str, checkpoint_id: str
    ) -> GuestBaseline: ...

    def verify(
        self, guest_id: str, guest_path: str, baseline: GuestBaseline
    ) -> bool: ...

    def restore(
        self, guest_id: str, guest_path: str, baseline: GuestBaseline
    ) -> None: ...

    def diff_paths(
        self, guest_id: str, guest_path: str, baseline: str, target: str
    ) -> tuple[tuple[str, str], ...]: ...

    def export_patch(
        self, guest_id: str, guest_path: str, baseline: str, target: str
    ) -> bytes: ...

    def invoke_agent(
        self, guest_id: str, guest_path: str, role: PiRole, prompt: str
    ) -> PiRpcResult: ...

    def preview_request(
        self, guest_id: str, port: int, method: Literal["GET", "HEAD"], target: str
    ) -> tuple[int, str, bytes]: ...


class SshGuestWorkspaceController:
    """Fixed SSH operations against the guest; no caller supplies a command."""

    def __init__(self, vm_adapter: QemuVmAdapter, config: VmManagerConfig) -> None:
        if not 5 <= config.pi_attempt_timeout_seconds <= 900:
            raise ValueError("PI_GUEST_ATTEMPT_TIMEOUT_SECONDS is outside range")
        if any(
            _PI_CONFIG_VALUE.fullmatch(value) is None
            for value in (config.pi_binary, config.pi_provider, config.pi_model)
        ):
            raise ValueError("Pi guest configuration contains an unsafe value")
        self._vm_adapter = vm_adapter
        self._config = config

    def prepare(self, guest_id: str, guest_path: str) -> None:
        target = self._guest_path(guest_path)
        quoted = shlex.quote(target)
        self._ssh(
            guest_id,
            f"set -eu; test ! -L {quoted}; mkdir -p -- {quoted}; chmod 700 -- {quoted}",
        )

    def write_file(self, guest_id: str, guest_path: str, file: ProjectFile) -> None:
        if len(file.content) > 16 * 1024 * 1024:
            raise WorkspaceImportError("guest_file_too_large")
        target = self._file_path(guest_path, file.relative_path)
        quoted_target = shlex.quote(target)
        existing = (
            self._ssh(
                guest_id,
                f"set -eu; if test -f {quoted_target}; then "
                f"sha256sum -- {quoted_target} | cut -d' ' -f1; fi",
            )
            .decode("ascii")
            .strip()
        )
        if existing:
            if existing == file.sha256:
                return
            raise WorkspaceImportError("guest_import_conflict")
        parent = shlex.quote(os.fspath(Path(target).parent))
        temporary = shlex.quote(f"{target}.orchestrator-{file.sha256[:16]}.tmp")
        mode = "700" if file.executable else "600"
        self._ssh(
            guest_id,
            f"set -eu; mkdir -p -- {parent}; umask 077; "
            f"cat > {temporary}; test \"$(sha256sum -- {temporary} | cut -d' ' -f1)\" "
            f"= {shlex.quote(file.sha256)}; chmod {mode} -- {temporary}; "
            f"mv -- {temporary} {quoted_target}",
            input_bytes=file.content,
        )

    def create_baseline(self, guest_id: str, guest_path: str) -> GuestBaseline:
        target = shlex.quote(self._guest_path(guest_path))
        output = (
            self._ssh(
                guest_id,
                f"set -eu; cd -- {target}; "
                "if test ! -d .git; then git init --quiet; "
                "git config user.name orchestrator-service; "
                "git config user.email service@orchestrator.invalid; "
                "git add --all; git commit --quiet -m 'orchestrator baseline'; fi; "
                'test -z "$(git status --porcelain)"; '
                "git rev-parse HEAD; git rev-parse 'HEAD^{tree}'",
            )
            .decode("ascii")
            .splitlines()
        )
        if len(output) != 2 or any(
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None
            for value in output
        ):
            raise WorkspaceImportError("guest_git_baseline_failed")
        return GuestBaseline(commit_hash=output[0], tree_hash=output[1])

    def cleanup(self, guest_id: str, guest_path: str) -> None:
        target = shlex.quote(self._guest_path(guest_path))
        self._ssh(guest_id, f"set -eu; rm -rf -- {target}")

    def checkpoint(
        self, guest_id: str, guest_path: str, checkpoint_id: str
    ) -> GuestBaseline:
        if (
            re.fullmatch(r"checkpoint_[A-Za-z0-9][A-Za-z0-9_-]{0,127}", checkpoint_id)
            is None
        ):
            raise WorkspaceImportError("invalid_checkpoint_id")
        target = shlex.quote(self._guest_path(guest_path))
        message = shlex.quote(checkpoint_id)
        output = (
            self._ssh(
                guest_id,
                f"set -eu; cd -- {target}; git add --all; "
                f"git commit --quiet --allow-empty -m {message}; "
                "git rev-parse HEAD; git rev-parse 'HEAD^{tree}'",
            )
            .decode("ascii")
            .splitlines()
        )
        return self._baseline_output(output)

    def verify(self, guest_id: str, guest_path: str, baseline: GuestBaseline) -> bool:
        self._validate_git_hash(baseline.commit_hash)
        self._validate_git_hash(baseline.tree_hash)
        target = shlex.quote(self._guest_path(guest_path))
        commit = shlex.quote(baseline.commit_hash)
        expected_tree = shlex.quote(baseline.tree_hash)
        try:
            self._ssh(
                guest_id,
                f"set -eu; cd -- {target}; git cat-file -e {commit}^{{commit}}; "
                f'test "$(git rev-parse {commit}^{{tree}})" = {expected_tree}',
            )
        except WorkspaceImportError:
            return False
        return True

    def restore(self, guest_id: str, guest_path: str, baseline: GuestBaseline) -> None:
        self._validate_git_hash(baseline.commit_hash)
        target = shlex.quote(self._guest_path(guest_path))
        commit = shlex.quote(baseline.commit_hash)
        self._ssh(
            guest_id,
            f"set -eu; cd -- {target}; git reset --hard {commit}; git clean -fd",
        )

    def diff_paths(
        self, guest_id: str, guest_path: str, baseline: str, target: str
    ) -> tuple[tuple[str, str], ...]:
        self._validate_git_hash(baseline)
        self._validate_git_hash(target)
        guest_path_value = shlex.quote(self._guest_path(guest_path))
        output = self._ssh(
            guest_id,
            f"set -eu; cd -- {guest_path_value}; git diff --name-status --no-renames "
            f"{shlex.quote(baseline)} {shlex.quote(target)}",
        ).decode("utf-8")
        changed: list[tuple[str, str]] = []
        for line in output.splitlines():
            status_value, separator, path = line.partition("\t")
            if (
                not separator
                or not path
                or path.startswith("/")
                or ".." in Path(path).parts
                or len(path) > 1024
            ):
                raise WorkspaceImportError("invalid_guest_diff")
            changed.append((status_value, path))
        return tuple(changed)

    def export_patch(
        self, guest_id: str, guest_path: str, baseline: str, target: str
    ) -> bytes:
        self._validate_git_hash(baseline)
        self._validate_git_hash(target)
        guest_path_value = shlex.quote(self._guest_path(guest_path))
        return self._ssh(
            guest_id,
            f"set -eu; cd -- {guest_path_value}; "
            "git diff --binary --full-index --no-renames "
            f"{shlex.quote(baseline)} {shlex.quote(target)}",
            max_output_bytes=10_485_760,
        )

    def invoke_agent(
        self, guest_id: str, guest_path: str, role: PiRole, prompt: str
    ) -> PiRpcResult:
        """Run one role-pinned Pi RPC session in the guest workspace."""
        target = self._guest_path(guest_path)
        tools = _PI_ROLE_TOOLS[role]
        run_id = f"run_{guest_id.removeprefix('guest-')}"
        egress_token = issue_egress_token(self._config.egress_auth_secret, run_id)
        remote_argv = (
            "env",
            "PI_OFFLINE=1",
            "PI_TELEMETRY=0",
            f"ORCHESTRATOR_RUN_ID={run_id}",
            "ORCHESTRATOR_EGRESS_PROXY=http://10.0.2.100:3128",
            f"ORCHESTRATOR_EGRESS_TOKEN={egress_token}",
            self._config.pi_binary,
            "--mode",
            "rpc",
            "--no-session",
            "--provider",
            self._config.pi_provider,
            "--model",
            self._config.pi_model,
            "--tools",
            ",".join(tools),
            "--no-extensions",
            "--extension",
            self._config.pi_browser_extension,
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
        )
        remote_command = f"cd -- {shlex.quote(target)} && exec " + " ".join(
            shlex.quote(argument) for argument in remote_argv
        )
        return run_pi_rpc(
            self._ssh_argv(guest_id, remote_command),
            prompt=prompt,
            timeout_seconds=self._config.pi_attempt_timeout_seconds,
        )

    def preview_request(
        self, guest_id: str, port: int, method: Literal["GET", "HEAD"], target: str
    ) -> tuple[int, str, bytes]:
        """Fetch one bounded HTTP response through the guest SSH control channel."""
        if port not in self._config.preview_ports:
            raise WorkspaceImportError("preview_port_denied")
        if (
            method not in {"GET", "HEAD"}
            or not target.startswith("/")
            or "\r" in target
            or "\n" in target
            or len(target) > 4096
        ):
            raise WorkspaceImportError("invalid_preview_request")
        url = f"http://127.0.0.1:{port}{target}"
        command = (
            "exec curl --noproxy '*' --max-time 4 --max-filesize 5242880 "
            "--silent --show-error --include --http1.1 "
            f"--request {method} -- {shlex.quote(url)} | head -c 5250000"
        )
        raw = self._ssh(guest_id, command, max_output_bytes=5_250_000)
        header, separator, body = raw.partition(b"\r\n\r\n")
        if not separator:
            raise WorkspaceImportError("invalid_preview_response")
        lines = header.split(b"\r\n")
        try:
            status_code = int(lines[0].split(b" ", 2)[1])
        except (IndexError, ValueError) as error:
            raise WorkspaceImportError("invalid_preview_response") from error
        content_type = "application/octet-stream"
        for line in lines[1:]:
            name, colon, value = line.partition(b":")
            if colon and name.lower() == b"content-type":
                content_type = value.strip().decode("ascii", errors="replace")[:256]
        if not 100 <= status_code <= 599:
            raise WorkspaceImportError("invalid_preview_response")
        return status_code, content_type, b"" if method == "HEAD" else body

    def _ssh(
        self,
        guest_id: str,
        remote_command: str,
        *,
        input_bytes: bytes | None = None,
        max_output_bytes: int = 65_536,
    ) -> bytes:
        if not 1 <= max_output_bytes <= 10_485_760:
            raise WorkspaceImportError("invalid_guest_output_limit")
        argv = self._ssh_argv(guest_id, remote_command)
        try:
            completed = subprocess.run(
                argv,
                input=input_bytes,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._config.operation_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkspaceImportError("guest_control_unavailable") from error
        if completed.returncode != 0 or len(completed.stdout) > max_output_bytes:
            raise WorkspaceImportError("guest_operation_failed")
        return completed.stdout

    def _ssh_argv(self, guest_id: str, remote_command: str) -> tuple[str, ...]:
        host, port = self._vm_adapter.ssh_endpoint(guest_id)
        return (
            "ssh",
            "-T",
            "-p",
            str(port),
            "-i",
            os.fspath(self._config.ssh_private_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self._config.ssh_known_hosts}",
            "-o",
            "ConnectTimeout=5",
            f"piagent@{host}",
            remote_command,
        )

    @staticmethod
    def _guest_path(guest_path: str) -> str:
        parts = Path(guest_path).parts
        if (
            not guest_path.startswith("home/piagent/workspaces/")
            or ".." in parts
            or len(guest_path) > 1024
        ):
            raise WorkspaceImportError("invalid_guest_path")
        return f"/{guest_path}"

    @classmethod
    def _file_path(cls, guest_path: str, relative_path: str) -> str:
        if (
            not relative_path
            or relative_path.startswith("/")
            or ".." in Path(relative_path).parts
            or len(relative_path) > 1024
        ):
            raise WorkspaceImportError("invalid_guest_path")
        root = cls._guest_path(guest_path)
        target = os.fspath(Path(root) / relative_path)
        if not Path(target).is_relative_to(root):
            raise WorkspaceImportError("guest_path_escape")
        return target

    @staticmethod
    def _validate_git_hash(value: str) -> None:
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
            raise WorkspaceImportError("invalid_git_object")

    @classmethod
    def _baseline_output(cls, output: list[str]) -> GuestBaseline:
        if len(output) != 2:
            raise WorkspaceImportError("guest_git_operation_failed")
        cls._validate_git_hash(output[0])
        cls._validate_git_hash(output[1])
        return GuestBaseline(commit_hash=output[0], tree_hash=output[1])


class VmManagerRequest(StrictDomainModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    guest_id: str = Field(pattern=_GUEST_ID.pattern, max_length=134)
    overlay_id: str = Field(pattern=_OVERLAY_ID.pattern, max_length=136)


class VmManagerResponse(StrictDomainModel):
    run_id: str
    guest_id: str
    overlay_id: str
    status: Literal["creating", "ready", "destroyed"]


class VmReadyResponse(StrictDomainModel):
    guest_id: str
    ready: bool


class VmPreflightResponse(StrictDomainModel):
    ready: bool
    kvm_available: bool
    base_image_available: bool
    qemu_system_available: bool
    qemu_img_available: bool
    vm_root_writable: bool
    guest_ssh_key_available: bool
    guest_known_hosts_available: bool


class WorkspacePathRequest(StrictDomainModel):
    guest_path: str = Field(min_length=1, max_length=1024)


class WorkspaceFileRequest(WorkspacePathRequest):
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str = Field(max_length=22_369_624)
    executable: bool

    @model_validator(mode="after")
    def content_matches_hash(self) -> WorkspaceFileRequest:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except ValueError as error:
            raise ValueError("content_base64 is invalid") from error
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("file exceeds guest transfer limit")
        if sha256(content).hexdigest() != self.sha256:
            raise ValueError("content hash does not match")
        return self

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class WorkspaceOperationResponse(StrictDomainModel):
    guest_id: str
    status: Literal["prepared", "written", "cleaned"]


class WorkspaceBaselineResponse(StrictDomainModel):
    guest_id: str
    commit_hash: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    tree_hash: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorkspaceCheckpointRequest(WorkspacePathRequest):
    checkpoint_id: str = Field(pattern=r"^checkpoint_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WorkspaceGitObjectRequest(WorkspacePathRequest):
    commit_hash: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    tree_hash: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorkspaceVerifyResponse(StrictDomainModel):
    guest_id: str
    valid: bool


class WorkspaceDiffRequest(WorkspacePathRequest):
    baseline_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    target_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorkspaceChangedPath(StrictDomainModel):
    status: str = Field(min_length=1, max_length=16)
    path: str = Field(min_length=1, max_length=1024)


class WorkspaceDiffResponse(StrictDomainModel):
    guest_id: str
    changed_paths: tuple[WorkspaceChangedPath, ...]


class WorkspacePatchResponse(StrictDomainModel):
    guest_id: str
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_base64: str = Field(max_length=13_981_016)


class GuestAgentRequest(WorkspacePathRequest):
    role: PiRole
    prompt: str = Field(min_length=1, max_length=262_144)


class GuestAgentToolEvent(StrictDomainModel):
    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    status: Literal["started", "completed", "failed"]


class GuestAgentResponse(StrictDomainModel):
    guest_id: str
    role: PiRole
    text: str = Field(min_length=1, max_length=262_144)
    tool_events: tuple[GuestAgentToolEvent, ...]


class GuestPreviewRequest(StrictDomainModel):
    port: int = Field(ge=1, le=65_535)
    method: Literal["GET", "HEAD"]
    target: str = Field(min_length=1, max_length=4096)


class GuestPreviewResponse(StrictDomainModel):
    guest_id: str
    status_code: int = Field(ge=100, le=599)
    content_type: str = Field(min_length=1, max_length=256)
    content_base64: str = Field(max_length=7_000_000)


def create_vm_manager_app(
    adapter: QemuVmAdapter | VmAdapter,
    *,
    auth_token: str,
    preflight: Callable[[], VmPreflight] | None = None,
    workspace_controller: ManagerWorkspaceController | None = None,
    preview_ports: tuple[int, ...] = (3000, 4173, 5173, 8000, 8080),
) -> FastAPI:
    if len(auth_token) < 24:
        raise ValueError("VM manager token must contain at least 24 characters")
    app = FastAPI(title="Orchestrator VM Manager", version="v1")

    def authorize(
        supplied: Annotated[str | None, Header(alias="X-VM-Manager-Token")] = None,
    ) -> None:
        if supplied is None or not hmac.compare_digest(supplied, auth_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get(
        "/ready", response_model=VmPreflightResponse, dependencies=[Depends(authorize)]
    )
    def ready() -> VmPreflightResponse:
        if preflight is not None:
            result = preflight()
        elif isinstance(adapter, QemuVmAdapter):
            result = adapter.preflight()
        else:
            result = VmPreflight(True, True, True, True, True, True)
        return VmPreflightResponse(**asdict(result))

    @app.post(
        "/v1/guests/{run_id}",
        response_model=VmManagerResponse,
        dependencies=[Depends(authorize)],
    )
    def provision(
        run_id: str,
        request: VmManagerRequest,
    ) -> VmManagerResponse:
        try:
            QemuVmAdapter._validate_binding(
                run_id, request.guest_id, request.overlay_id
            )
            adapter.provision(run_id, request.guest_id, request.overlay_id)
        except VmLifecycleError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        return VmManagerResponse(
            run_id=run_id,
            guest_id=request.guest_id,
            overlay_id=request.overlay_id,
            status="creating",
        )

    @app.get(
        "/v1/guests/{guest_id}/ready",
        response_model=VmReadyResponse,
        dependencies=[Depends(authorize)],
    )
    def probe(guest_id: str) -> VmReadyResponse:
        try:
            QemuVmAdapter._validate_guest_id(guest_id)
            is_ready = adapter.probe_ready(guest_id)
        except VmLifecycleError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        return VmReadyResponse(guest_id=guest_id, ready=is_ready)

    @app.delete(
        "/v1/guests/{guest_id}",
        response_model=VmManagerResponse,
        dependencies=[Depends(authorize)],
    )
    def destroy(
        guest_id: str,
        request: VmManagerRequest,
    ) -> VmManagerResponse:
        if request.guest_id != guest_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "vm_identity_conflict")
        try:
            QemuVmAdapter._validate_destroy_binding(guest_id, request.overlay_id)
            adapter.destroy(guest_id, request.overlay_id)
        except VmLifecycleError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        return VmManagerResponse(
            run_id=f"run_{guest_id.removeprefix('guest-')}",
            guest_id=guest_id,
            overlay_id=request.overlay_id,
            status="destroyed",
        )

    if workspace_controller is not None:
        @app.post(
            "/v1/guests/{guest_id}/previews/fetch",
            response_model=GuestPreviewResponse,
            dependencies=[Depends(authorize)],
        )
        def fetch_preview(
            guest_id: str, request: GuestPreviewRequest
        ) -> GuestPreviewResponse:
            if request.port not in preview_ports:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "preview_port_denied")
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                code, media_type, content = workspace_controller.preview_request(
                    guest_id, request.port, request.method, request.target
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return GuestPreviewResponse(
                guest_id=guest_id,
                status_code=code,
                content_type=media_type,
                content_base64=base64.b64encode(content).decode("ascii"),
            )


        @app.post(
            "/v1/guests/{guest_id}/workspace/prepare",
            response_model=WorkspaceOperationResponse,
            dependencies=[Depends(authorize)],
        )
        def prepare_workspace(
            guest_id: str, request: WorkspacePathRequest
        ) -> WorkspaceOperationResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                workspace_controller.prepare(guest_id, request.guest_path)
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceOperationResponse(guest_id=guest_id, status="prepared")

        @app.put(
            "/v1/guests/{guest_id}/workspace/file",
            response_model=WorkspaceOperationResponse,
            dependencies=[Depends(authorize)],
        )
        def write_workspace_file(
            guest_id: str, request: WorkspaceFileRequest
        ) -> WorkspaceOperationResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                workspace_controller.write_file(
                    guest_id,
                    request.guest_path,
                    ProjectFile(
                        relative_path=request.relative_path,
                        content=request.content(),
                        sha256=request.sha256,
                        executable=request.executable,
                    ),
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceOperationResponse(guest_id=guest_id, status="written")

        @app.post(
            "/v1/guests/{guest_id}/workspace/baseline",
            response_model=WorkspaceBaselineResponse,
            dependencies=[Depends(authorize)],
        )
        def baseline_workspace(
            guest_id: str, request: WorkspacePathRequest
        ) -> WorkspaceBaselineResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                baseline = workspace_controller.create_baseline(
                    guest_id, request.guest_path
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceBaselineResponse(
                guest_id=guest_id,
                commit_hash=baseline.commit_hash,
                tree_hash=baseline.tree_hash,
            )

        @app.delete(
            "/v1/guests/{guest_id}/workspace",
            response_model=WorkspaceOperationResponse,
            dependencies=[Depends(authorize)],
        )
        def cleanup_workspace(
            guest_id: str, request: WorkspacePathRequest
        ) -> WorkspaceOperationResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                workspace_controller.cleanup(guest_id, request.guest_path)
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceOperationResponse(guest_id=guest_id, status="cleaned")

        @app.post(
            "/v1/guests/{guest_id}/workspace/checkpoints",
            response_model=WorkspaceBaselineResponse,
            dependencies=[Depends(authorize)],
        )
        def create_checkpoint(
            guest_id: str, request: WorkspaceCheckpointRequest
        ) -> WorkspaceBaselineResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                baseline = workspace_controller.checkpoint(
                    guest_id, request.guest_path, request.checkpoint_id
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceBaselineResponse(
                guest_id=guest_id,
                commit_hash=baseline.commit_hash,
                tree_hash=baseline.tree_hash,
            )

        @app.post(
            "/v1/guests/{guest_id}/workspace/checkpoints/verify",
            response_model=WorkspaceVerifyResponse,
            dependencies=[Depends(authorize)],
        )
        def verify_checkpoint(
            guest_id: str, request: WorkspaceGitObjectRequest
        ) -> WorkspaceVerifyResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                valid = workspace_controller.verify(
                    guest_id,
                    request.guest_path,
                    GuestBaseline(request.commit_hash, request.tree_hash),
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceVerifyResponse(guest_id=guest_id, valid=valid)

        @app.post(
            "/v1/guests/{guest_id}/workspace/checkpoints/restore",
            response_model=WorkspaceOperationResponse,
            dependencies=[Depends(authorize)],
        )
        def restore_checkpoint(
            guest_id: str, request: WorkspaceGitObjectRequest
        ) -> WorkspaceOperationResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                workspace_controller.restore(
                    guest_id,
                    request.guest_path,
                    GuestBaseline(request.commit_hash, request.tree_hash),
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceOperationResponse(guest_id=guest_id, status="prepared")

        @app.post(
            "/v1/guests/{guest_id}/workspace/diff",
            response_model=WorkspaceDiffResponse,
            dependencies=[Depends(authorize)],
        )
        def diff_workspace(
            guest_id: str, request: WorkspaceDiffRequest
        ) -> WorkspaceDiffResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                changed = workspace_controller.diff_paths(
                    guest_id,
                    request.guest_path,
                    request.baseline_commit,
                    request.target_commit,
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspaceDiffResponse(
                guest_id=guest_id,
                changed_paths=tuple(
                    WorkspaceChangedPath(status=status_value, path=path)
                    for status_value, path in changed
                ),
            )

        @app.post(
            "/v1/guests/{guest_id}/workspace/patch",
            response_model=WorkspacePatchResponse,
            dependencies=[Depends(authorize)],
        )
        def export_workspace_patch(
            guest_id: str, request: WorkspaceDiffRequest
        ) -> WorkspacePatchResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                patch = workspace_controller.export_patch(
                    guest_id,
                    request.guest_path,
                    request.baseline_commit,
                    request.target_commit,
                )
            except (VmLifecycleError, WorkspaceImportError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return WorkspacePatchResponse(
                guest_id=guest_id,
                patch_sha256=sha256(patch).hexdigest(),
                patch_base64=base64.b64encode(patch).decode("ascii"),
            )

        @app.post(
            "/v1/guests/{guest_id}/agent-attempts",
            response_model=GuestAgentResponse,
            dependencies=[Depends(authorize)],
        )
        def invoke_guest_agent(
            guest_id: str, request: GuestAgentRequest
        ) -> GuestAgentResponse:
            try:
                QemuVmAdapter._validate_guest_id(guest_id)
                result = workspace_controller.invoke_agent(
                    guest_id, request.guest_path, request.role, request.prompt
                )
            except (VmLifecycleError, WorkspaceImportError, PiRpcError) as error:
                code = error.code if isinstance(error, PiRpcError) else str(error)
                response_status = (
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if isinstance(error, PiRpcError) and error.retryable
                    else status.HTTP_409_CONFLICT
                )
                raise HTTPException(response_status, code) from error
            return GuestAgentResponse(
                guest_id=guest_id,
                role=request.role,
                text=result.text,
                tool_events=tuple(
                    GuestAgentToolEvent(
                        tool_call_id=event.tool_call_id,
                        tool_name=event.tool_name,
                        status=event.status,
                    )
                    for event in result.tool_events
                ),
            )

    return app


class VmManagerHttpAdapter(VmAdapter):
    """Bounded typed client; it cannot send arbitrary manager operations."""

    def __init__(
        self, base_url: str, auth_token: str, *, timeout_seconds: int = 35
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("VM manager URL must be HTTP(S)")
        if len(auth_token) < 24:
            raise ValueError("VM manager token must contain at least 24 characters")
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds

    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        self._request(
            "POST",
            f"/v1/guests/{run_id}",
            {"guest_id": guest_id, "overlay_id": overlay_id},
        )

    def probe_ready(self, guest_id: str) -> bool:
        response = self._request("GET", f"/v1/guests/{guest_id}/ready", None)
        value = response.get("ready")
        if not isinstance(value, bool):
            raise VmLifecycleError("invalid_vm_manager_response")
        return value

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        self._request(
            "DELETE",
            f"/v1/guests/{guest_id}",
            {"guest_id": guest_id, "overlay_id": overlay_id},
        )

    def readiness(self) -> VmPreflightResponse:
        return VmPreflightResponse.model_validate(self._request("GET", "/ready", None))

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        *,
        timeout_seconds: int | None = None,
        max_response_bytes: int = 1_048_576,
    ) -> dict[str, object]:
        if not 1_024 <= max_response_bytes <= 16_777_216:
            raise ValueError("VM manager response limit is outside range")
        body = (
            json.dumps(payload, separators=(",", ":")).encode()
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-VM-Manager-Token": self._auth_token,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds or self._timeout_seconds
            ) as response:
                if response.status < 200 or response.status >= 300:
                    raise VmLifecycleError("vm_manager_request_failed")
                raw = response.read(max_response_bytes + 1)
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as error:
            raise VmLifecycleError("vm_manager_unavailable") from error
        if len(raw) > max_response_bytes:
            raise VmLifecycleError("invalid_vm_manager_response")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VmLifecycleError("invalid_vm_manager_response") from error
        if not isinstance(value, dict):
            raise VmLifecycleError("invalid_vm_manager_response")
        return cast(dict[str, object], value)


class VmManagerWorkspaceHttpAdapter:
    """Guest workspace port implemented only through typed manager endpoints."""

    def __init__(self, client: VmManagerHttpAdapter) -> None:
        self._client = client

    def prepare(self, guest: GuestHandle, guest_path: str) -> None:
        self._client._request(
            "POST",
            f"/v1/guests/{guest.guest_id}/workspace/prepare",
            {"guest_path": guest_path},
        )

    def write_file(
        self, guest: GuestHandle, guest_path: str, file: ProjectFile
    ) -> None:
        self._client._request(
            "PUT",
            f"/v1/guests/{guest.guest_id}/workspace/file",
            {
                "guest_path": guest_path,
                "relative_path": file.relative_path,
                "sha256": file.sha256,
                "content_base64": base64.b64encode(file.content).decode("ascii"),
                "executable": file.executable,
            },
        )

    def create_baseline(self, guest: GuestHandle, guest_path: str) -> GuestBaseline:
        response = self._client._request(
            "POST",
            f"/v1/guests/{guest.guest_id}/workspace/baseline",
            {"guest_path": guest_path},
        )
        commit_hash = response.get("commit_hash")
        tree_hash = response.get("tree_hash")
        if not isinstance(commit_hash, str) or not isinstance(tree_hash, str):
            raise WorkspaceImportError("invalid_vm_manager_response")
        return GuestBaseline(commit_hash=commit_hash, tree_hash=tree_hash)

    def cleanup(self, guest: GuestHandle, guest_path: str) -> None:
        self._client._request(
            "DELETE",
            f"/v1/guests/{guest.guest_id}/workspace",
            {"guest_path": guest_path},
        )


class VmManagerPreviewHttpAdapter:
    """Bounded application-preview reads through the manager SSH channel."""

    def __init__(self, client: VmManagerHttpAdapter) -> None:
        self._client = client

    def fetch(
        self,
        guest: GuestHandle,
        port: int,
        method: Literal["GET", "HEAD"],
        target: str,
    ) -> tuple[int, str, bytes]:
        response = self._client._request(
            "POST",
            f"/v1/guests/{guest.guest_id}/previews/fetch",
            {"port": port, "method": method, "target": target},
            max_response_bytes=7_100_000,
        )
        try:
            parsed = GuestPreviewResponse.model_validate(response)
            content = base64.b64decode(parsed.content_base64, validate=True)
        except ValueError as error:
            raise VmLifecycleError("invalid_vm_manager_response") from error
        if len(content) > 5_242_880:
            raise VmLifecycleError("invalid_vm_manager_response")
        return parsed.status_code, parsed.content_type, content


class VmManagerPiRpcHttpAdapter:
    """Runner-side typed Pi invocation port for a specific live guest."""

    def __init__(
        self, client: VmManagerHttpAdapter, *, attempt_timeout_seconds: int = 330
    ) -> None:
        if not 10 <= attempt_timeout_seconds <= 930:
            raise ValueError("Pi attempt client timeout is outside range")
        self._client = client
        self._attempt_timeout_seconds = attempt_timeout_seconds

    def invoke(
        self, *, guest: GuestHandle, guest_path: str, role: PiRole, prompt: str
    ) -> PiRpcResult:
        response = self._client._request(
            "POST",
            f"/v1/guests/{guest.guest_id}/agent-attempts",
            {"guest_path": guest_path, "role": role, "prompt": prompt},
            timeout_seconds=self._attempt_timeout_seconds,
        )
        try:
            parsed = GuestAgentResponse.model_validate(response)
        except ValueError as error:
            raise PiRpcError("invalid_vm_manager_response") from error
        return PiRpcResult(
            text=parsed.text,
            tool_events=tuple(
                PiToolEvent(event.tool_call_id, event.tool_name, event.status)
                for event in parsed.tool_events
            ),
        )
