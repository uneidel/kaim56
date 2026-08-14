#!/usr/bin/env bash
# Baut NUR das Gast-Rootfs (rootfs.ext4) und legt es als claude-Instanz ab.
# Keine Downloads (firecracker/vmlinux liegen schon in ../../firecracker/bin).
# Bewusst OHNE 'set -e' -> zeigt jeden Schritt/Fehler statt still abzubrechen.
cd "$(dirname "$0")"
INST=/home/ulrich/firecracker/instances/claude-rootfs.ext4
fail(){ echo "❌ FEHLER in: $1"; exit 1; }

echo "== [1] bridge.py + web_bridge.py kopieren =="
cp ../claude-signal-bridge/bridge.py ./bridge.py || fail "bridge.py kopieren"
cp ../agent-web/web_bridge.py ./web_bridge.py || fail "web_bridge.py kopieren"

echo "== [2] docker build (kann dauern: node + claude-code) =="
docker build -f Dockerfile.rootfs -t claude-fc-rootfs . || fail "docker build"

echo "== [3] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create claude-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null

echo "== [4] Anmeldung + Workspace einlegen =="
mkdir -p rootfs/home/node/.claude rootfs/home/node/workspace
if [ -f "$HOME/.claude/.credentials.json" ]; then
  cp "$HOME/.claude/.credentials.json" rootfs/home/node/.claude/ && chmod 600 rootfs/home/node/.claude/.credentials.json
  echo "   Anmeldung eingelegt (/home/node/.claude, uid 1000)."
else
  echo "   ⚠️ WARN: ~/.claude/.credentials.json fehlt -> Gast ohne Anmeldung!"
fi

echo "== [5] ext4-Image bauen =="
SIZE_MB=${ROOTFS_MB:-3072}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail "truncate"
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d (e2fsprogs >=1.43 nötig)"
rm -rf rootfs

echo "== [6] als claude-Instanz ablegen =="
cp rootfs.ext4 "$INST" || fail "kopieren nach instances/"
echo "✅ FERTIG:"; ls -lh "$INST"
