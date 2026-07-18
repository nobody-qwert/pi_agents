# Disposable VM base image

Build the sealed, immutable guest with `scripts/build-vm-image.sh`. Supply an
HTTPS URL for a pinned Debian generic-cloud qcow2 image and its vendor-published
SHA-256 in `VM_BASE_IMAGE_URL` and `VM_BASE_IMAGE_SHA256`. The build installs the
pinned Pi runtime, Chromium/Playwright extension, non-root desktop, strict SSH
policy, and inference configuration, then writes `pi-base.qcow2`, its checksum,
and the pinned guest host key. Generated image and key material are intentionally
not committed.
