#!/usr/bin/env bash
# Baut NUR das Gast-Rootfs (rootfs.ext4) und legt es als claude-Instanz ab.
# Keine Downloads (firecracker/vmlinux liegen schon in ../../firecracker/bin).
# Bewusst OHNE 'set -e' -> zeigt jeden Schritt/Fehler statt still abzubrechen.
FC_DIR="${FC_DIR:-$(cd "$(dirname "$0")/.." && pwd)/firecracker}"   # the manager tree next to this folder
cd "$(dirname "$0")"
INST="${FC_DIR}"/instances/claude-rootfs.ext4
fail(){ echo "❌ FEHLER in: $1"; exit 1; }
# Docker image -> ext4 with the image's owners (root for the system, the agent
# user for its home): tar + mkfs.ext4 -d run as root in a throwaway container
# (no --privileged, mkfs works on a plain file). Unpacked by this non-root user
# instead, every file in the image was owned by uid 1000 — /usr, /etc, /app too.
mkrootfs() {  # <image> <absolute out.ext4> <size MB>
  local cid rc; cid=$(docker create "$1") || return 1
  docker export "$cid" | docker run -i --rm --network none -v "$(dirname "$2"):/out" \
      "${ROOTFS_TOOL_IMAGE:-debian:bookworm-slim}" sh -euc '
    mkdir /r; tar -C /r --numeric-owner --xattrs --xattrs-include=security.capability -xpf -
    rm -f "/out/$2"; truncate -s "$1M" "/out/$2"; mkfs.ext4 -F -q -d /r "/out/$2"; chown "$3" "/out/$2"' \
      sh "$3" "$(basename "$2")" "$(id -u):$(id -g)"
  rc=$?; docker rm "$cid" >/dev/null; return $rc
}

echo "== [1] bridge.py + web_bridge.py bereitstellen =="
# Die Quellen lagen frueher in Nachbarordnern; heute sind die hiesigen Dateien
# der Stand. Nur kopieren, wenn es die alten Quellen noch gibt — sonst die
# lokalen nehmen (und nur meckern, wenn auch die fehlen).
[ -f ../claude-signal-bridge/bridge.py ] && cp ../claude-signal-bridge/bridge.py ./bridge.py
[ -f ../agent-web/web_bridge.py ] && cp ../agent-web/web_bridge.py ./web_bridge.py
[ -f ./bridge.py ] || fail "bridge.py fehlt"
[ -f ./web_bridge.py ] || fail "web_bridge.py fehlt"

echo "== [2] docker build (kann dauern: node + claude-code) =="
docker build -f Dockerfile.rootfs -t claude-fc-rootfs . || fail "docker build"

echo "== [3] ext4-Image bauen (export + mkfs.ext4 -d im Container) =="
# Keine Anmeldung ins Image: guest-init holt das lebende Host-Credential beim
# Boot vom Manager, die Bruecke zieht es vor jedem Turn nach. Ein Token im
# Image waere nach Stunden tot und laege fuer immer in der Datei.
# /home/node/.claude und workspace legt das Dockerfile an (node:node).
mkrootfs claude-fc-rootfs "$PWD/rootfs.ext4" "${ROOTFS_MB:-3072}" || fail "mkfs.ext4 -d (im Container)"

echo "== [4] als claude-Instanz ablegen =="
# Never cp INTO the live image: a running microVM holds it open as its base, old
# and new blocks mix (ext4 checksum panic). Next to it, then an atomic rename.
cp rootfs.ext4 "$INST.new" || fail "kopieren nach instances/"
mv -f "$INST.new" "$INST" || fail "mv -> instances/"
echo "✅ FERTIG:"; ls -lh "$INST"
