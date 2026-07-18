#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
image_dir="$repo_root/vm/image"
output="$repo_root/vm/base/pi-base.qcow2"
ssh_public_key="$repo_root/vm/ssh/id_ed25519.pub"

: "${VM_BASE_IMAGE_URL:?Set VM_BASE_IMAGE_URL to a pinned Debian generic-cloud qcow2 URL}"
: "${VM_BASE_IMAGE_SHA256:?Set VM_BASE_IMAGE_SHA256 to the vendor-published SHA-256}"

if [[ ! "$VM_BASE_IMAGE_URL" =~ ^https:// ]] || [[ ! "$VM_BASE_IMAGE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "The base image must use HTTPS and a lowercase SHA-256 digest." >&2
  exit 2
fi
if [[ ! -f "$ssh_public_key" ]] || [[ -L "$ssh_public_key" ]]; then
  echo "Create vm/ssh/id_ed25519 and id_ed25519.pub before building." >&2
  exit 2
fi
for command_name in curl qemu-img virt-customize virt-cat sha256sum npm; do
  command -v "$command_name" >/dev/null || {
    echo "Missing build dependency: $command_name" >&2
    exit 2
  }
done

build_dir=$(mktemp -d)
cleanup() { rm -rf -- "$build_dir"; }
trap cleanup EXIT
download="$build_dir/vendor.qcow2"
candidate="$build_dir/pi-base.qcow2"

curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$download" "$VM_BASE_IMAGE_URL"
printf '%s  %s\n' "$VM_BASE_IMAGE_SHA256" "$download" | sha256sum --check --strict

npm install --package-lock-only --ignore-scripts --prefix "$image_dir"
qemu-img convert -f qcow2 -O qcow2 "$download" "$candidate"
qemu-img resize "$candidate" 40G

virt-customize -a "$candidate" \
  --install openssh-server,nodejs,npm,chromium,xserver-xorg,xfce4,lightdm,git,curl,ca-certificates \
  --run-command 'useradd --create-home --shell /bin/bash piagent || true' \
  --run-command 'gpasswd --delete piagent sudo >/dev/null 2>&1 || true' \
  --run-command 'userdel --remove debian >/dev/null 2>&1 || true' \
  --run-command 'install -d -o piagent -g piagent -m 0700 /home/piagent/.ssh /home/piagent/.pi/agent' \
  --run-command 'install -d -o root -g root -m 0755 /opt/orchestrator/pi /etc/lightdm/lightdm.conf.d /etc/ssh/sshd_config.d' \
  --copy-in "$ssh_public_key:/tmp" \
  --copy-in "$image_dir/browser-tools.ts:/opt/orchestrator/pi" \
  --copy-in "$image_dir/package.json:/opt/orchestrator/pi" \
  --copy-in "$image_dir/package-lock.json:/opt/orchestrator/pi" \
  --copy-in "$image_dir/models.json:/home/piagent/.pi/agent" \
  --copy-in "$image_dir/lightdm.conf:/etc/lightdm/lightdm.conf.d" \
  --copy-in "$image_dir/sshd-orchestrator.conf:/etc/ssh/sshd_config.d" \
  --run-command 'install -o piagent -g piagent -m 0600 /tmp/id_ed25519.pub /home/piagent/.ssh/authorized_keys' \
  --run-command 'rm -f /tmp/id_ed25519.pub' \
  --run-command 'chown -R piagent:piagent /home/piagent/.pi' \
  --run-command 'cd /opt/orchestrator/pi && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --omit=dev --ignore-scripts' \
  --run-command 'npm install --global --ignore-scripts @earendil-works/pi-coding-agent@0.80.7' \
  --run-command 'ssh-keygen -A' \
  --run-command 'systemctl enable ssh lightdm' \
  --run-command 'systemctl mask serial-getty@ttyS0.service' \
  --run-command 'apt-get clean && rm -rf /var/lib/apt/lists/* /root/.npm /root/.cache' \
  --run-command 'find /home/piagent -xdev -type f -name "*.log" -delete' \
  --run-command 'truncate -s 0 /etc/machine-id'

host_key=$(virt-cat -a "$candidate" /etc/ssh/ssh_host_ed25519_key.pub)
if [[ ! "$host_key" =~ ^ssh-ed25519[[:space:]] ]]; then
  echo "The sealed image did not produce an Ed25519 SSH host key." >&2
  exit 1
fi
printf '[127.0.0.1]:* %s\n' "$host_key" > "$build_dir/known_hosts"
chmod 0444 "$build_dir/known_hosts"
chmod 0444 "$candidate"
install -m 0444 "$candidate" "$output"
install -m 0444 "$build_dir/known_hosts" "$repo_root/vm/ssh/known_hosts"
sha256sum "$output" > "$repo_root/vm/base/pi-base.qcow2.sha256"

echo "Built sealed guest: $output"
echo "Pinned host key: $repo_root/vm/ssh/known_hosts"
