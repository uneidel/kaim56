#!/usr/bin/env bash
# Sets up the NFSv4 server that shares /home/ulrich/agent live into the microVMs.
# Als ROOT ausfuehren:  sudo /home/ulrich/firecracker/setup-nfs-host.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo $0"; exit 1; }

AGENT_DIR="${AGENT_DIR:-$HOME/agent}"
POOL=172.30.0.0/16
UID_MAP=1000; GID_MAP=1000        # guest writes appear as this user (ulrich)

echo "[1/4] nfs-kernel-server installieren..."
if ! command -v exportfs >/dev/null; then
  # disable the dead /cdrom repo, otherwise it blocks apt entirely
  if grep -qs '^deb cdrom:' /etc/apt/sources.list; then
    sed -i.bak-claude '/^deb cdrom:/s/^/#/' /etc/apt/sources.list
    echo "  /cdrom-Repo auskommentiert (Backup: /etc/apt/sources.list.bak-claude)"
  fi
  apt-get update || echo "  apt update partially failed - trying install anyway"
  apt-get install -y --no-install-recommends nfs-kernel-server \
    || { echo "  ERROR: nfs-kernel-server not installable (check apt sources)."; exit 1; }
fi

echo "[2/4] Export folder..."
mkdir -p "$AGENT_DIR"
chown ${UID_MAP}:${GID_MAP} "$AGENT_DIR" || true

echo "[3/4] /etc/exports.d/agent.exports schreiben..."
mkdir -p /etc/exports.d
# fsid=0 => this folder is the NFSv4 root; the guest mounts "GW:/".
cat > /etc/exports.d/agent.exports <<EOF
$AGENT_DIR $POOL(rw,sync,no_subtree_check,all_squash,anonuid=${UID_MAP},anongid=${GID_MAP},fsid=0)
EOF
exportfs -ra
systemctl enable --now nfs-server 2>/dev/null || systemctl enable --now nfs-kernel-server

echo "[4/4] Firewall: NFS (2049) auf tap-Interfaces zulassen..."
if ! iptables -C INPUT -i 'fc+' -p tcp --dport 2049 -j ACCEPT 2>/dev/null; then
  iptables -A INPUT -i 'fc+' -p tcp --dport 2049 -j ACCEPT || true
fi

echo "FERTIG. Aktive Exports:"; exportfs -v
echo "The guest mounts automatically at boot (guest-init.sh):  <gateway>:/ -> /root/workspace"
