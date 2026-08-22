#!/usr/bin/env bash
# Builds ONLY the guest rootfs (rootfs.ext4) and places it as a claude instance.
# No downloads (firecracker/vmlinux already sit in ../../firecracker/bin).
# Deliberately WITHOUT 'set -e' -> shows every step/error instead of aborting silently.
cd "$(dirname "$0")"
INST="${FC_DIR:-/home/ulrich/firecracker}"/instances/claude-rootfs.ext4
fail(){ echo "❌ ERROR in: $1"; exit 1; }

echo "== [1] provide bridge.py + web_bridge.py =="
# The sources used to live in neighbouring folders; today the files here are
# the source of truth. Only copy if the old sources still exist — otherwise use
# the local ones (and only complain if those are missing too).
[ -f ../claude-signal-bridge/bridge.py ] && cp ../claude-signal-bridge/bridge.py ./bridge.py
[ -f ../agent-web/web_bridge.py ] && cp ../agent-web/web_bridge.py ./web_bridge.py
[ -f ./bridge.py ] || fail "bridge.py fehlt"
[ -f ./web_bridge.py ] || fail "web_bridge.py fehlt"

echo "== [2] docker build (may take a while: node + claude-code) =="
docker build -f Dockerfile.rootfs -t claude-fc-rootfs . || fail "docker build"

echo "== [3] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create claude-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null

echo "== [4] insert login + workspace =="
mkdir -p rootfs/home/node/.claude rootfs/home/node/workspace
if [ -f "$HOME/.claude/.credentials.json" ]; then
  cp "$HOME/.claude/.credentials.json" rootfs/home/node/.claude/ && chmod 600 rootfs/home/node/.claude/.credentials.json
  echo "   login inserted (/home/node/.claude, uid 1000)."
else
  echo "   ⚠️ WARN: ~/.claude/.credentials.json missing -> guest without login!"
fi

echo "== [5] build ext4 image =="
SIZE_MB=${ROOTFS_MB:-3072}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail "truncate"
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d (e2fsprogs >=1.43 required)"
rm -rf rootfs

echo "== [6] place as claude instance =="
cp rootfs.ext4 "$INST" || fail "copy to instances/"
echo "✅ DONE:"; ls -lh "$INST"
