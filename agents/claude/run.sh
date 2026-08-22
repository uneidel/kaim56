#!/usr/bin/env bash
# Starts the microVM. Needs access to /dev/kvm (root or the 'kvm' group) and tap0.
set -euo pipefail
cd "$(dirname "$0")"
SOCK=/tmp/fc-claude.sock
rm -f "$SOCK"
exec ./firecracker --api-sock "$SOCK" --config-file vmconfig.json
