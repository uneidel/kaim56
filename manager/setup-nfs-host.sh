#!/usr/bin/env bash
# Richtet den NFSv4-Server ein, der /home/ulrich/agent live in die microVMs teilt.
# Als ROOT ausfuehren:  sudo /home/ulrich/firecracker/setup-nfs-host.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen: sudo $0"; exit 1; }

AGENT_DIR="${AGENT_DIR:-$HOME/agent}"
POOL=172.30.0.0/16
UID_MAP=1000; GID_MAP=1000        # Gast-Writes erscheinen als dieser User (ulrich)

echo "[1/4] nfs-kernel-server installieren..."
if ! command -v exportfs >/dev/null; then
  # Totes /cdrom-Repo deaktivieren, sonst blockiert es apt komplett
  if grep -qs '^deb cdrom:' /etc/apt/sources.list; then
    sed -i.bak-claude '/^deb cdrom:/s/^/#/' /etc/apt/sources.list
    echo "  /cdrom-Repo auskommentiert (Backup: /etc/apt/sources.list.bak-claude)"
  fi
  apt-get update || echo "  apt update teilweise fehlgeschlagen – versuche Install trotzdem"
  apt-get install -y --no-install-recommends nfs-kernel-server \
    || { echo "  FEHLER: nfs-kernel-server nicht installierbar (apt-Quellen prüfen)."; exit 1; }
fi

echo "[2/4] Export-Ordner..."
mkdir -p "$AGENT_DIR"
chown ${UID_MAP}:${GID_MAP} "$AGENT_DIR" || true

echo "[3/4] /etc/exports.d/agent.exports schreiben..."
mkdir -p /etc/exports.d
# fsid=0 => dieser Ordner ist die NFSv4-Wurzel; Gast mountet "GW:/".
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
echo "Gast mountet automatisch beim Boot (guest-init.sh):  <gateway>:/ -> /root/workspace"
