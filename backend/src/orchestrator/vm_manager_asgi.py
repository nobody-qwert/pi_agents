"""Process entrypoint for the isolated VM-manager service."""

from __future__ import annotations

import os

from orchestrator.vm_manager import (
    QemuVmAdapter,
    SshGuestWorkspaceController,
    VmManagerConfig,
    create_vm_manager_app,
)

config = VmManagerConfig.from_environment()
adapter = QemuVmAdapter(config)
app = create_vm_manager_app(
    adapter,
    auth_token=os.environ["VM_MANAGER_AUTH_TOKEN"],
    workspace_controller=SshGuestWorkspaceController(adapter, config),
    preview_ports=config.preview_ports,
)
