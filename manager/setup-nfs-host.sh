#!/usr/bin/env bash
# NFS server for the microVMs. Run as root:  sudo <manager-dir>/setup-nfs-host.sh
#
# The manager exports every instance's workspace ($AGENT_DIR/<name>) and
# host folders to that VM's address alone, squashed to the system user
# kaim56-guest, and writes /etc/exports.d/fc-<name>.exports itself. This
# script only provides what it cannot: the server package, the base folder,
# the service (the firewall rules are the manager's) — and it retires the old pool-wide root
# export from earlier versions.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo $0"; exit 1; }

FC_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="${AGENT_DIR:-$(dirname "$FC_DIR")/agent}"

echo "[1/3] nfs-kernel-server..."
if ! command -v exportfs >/dev/null; then
  if grep -qs '^deb cdrom:' /etc/apt/sources.list; then
    sed -i.bak-kaim56 '/^deb cdrom:/s/^/#/' /etc/apt/sources.list
  fi
  apt-get update || echo "  apt update partially failed - trying install anyway"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nfs-kernel-server \
    || { echo "  ERROR: nfs-kernel-server not installable (check apt sources)."; exit 1; }
fi

echo "[2/3] Base folder $AGENT_DIR ..."
mkdir -p "$AGENT_DIR"
if id kaim56-guest >/dev/null 2>&1; then
  chown "kaim56-guest:$(stat -c %G "$FC_DIR")" "$AGENT_DIR"      # the manager repeats this at start
fi
chmod 755 "$AGENT_DIR"

echo "[3/3] Exports: per instance, written by the manager ..."
mkdir -p /etc/exports.d
if [ -f /etc/exports.d/agent.exports ]; then
  mv -f /etc/exports.d/agent.exports /etc/exports.d/agent.exports.bak
  echo "  old pool-wide export retired (agent.exports -> .bak)"
fi
systemctl enable --now nfs-server 2>/dev/null || systemctl enable --now nfs-kernel-server
exportfs -ra

echo "DONE. Exports now (one block per running instance appears after its start):"
exportfs -v || true
