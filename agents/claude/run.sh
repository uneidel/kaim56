#!/usr/bin/env bash
# Startet die microVM. Braucht Zugriff auf /dev/kvm (root oder Gruppe 'kvm') und tap0.
set -euo pipefail
cd "$(dirname "$0")"
SOCK=/tmp/fc-claude.sock
rm -f "$SOCK"
exec ./firecracker --api-sock "$SOCK" --config-file vmconfig.json
