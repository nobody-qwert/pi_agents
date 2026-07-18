# Guest SSH control material

Place the VM-manager-only private key at `id_ed25519` and the pinned guest host
key file at `known_hosts` before starting the KVM profile. These files are
mounted read-only into the VM manager and are ignored by Git. The sealed guest
must authorize the matching public key for the non-root `piagent` account.
