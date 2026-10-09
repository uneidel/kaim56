#!/usr/bin/env bash
# Build the indexer rootfs -> <manager>/instances/indexer-rootfs.ext4.
#   FC_DIR     the manager tree (default: ../../manager next to this folder in
#              the repo, or $HOME/firecracker for the live tree)
#   ROOTFS_MB  image size (default 3072; repowise and its deps are ~1.3 GB)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="$(basename "$HERE")"
if [ -z "${FC_DIR:-}" ]; then
  for d in "$HERE/../../manager" "$HOME/firecracker"; do
    [ -d "$d/instances" ] && FC_DIR="$(cd "$d" && pwd)" && break
  done
fi
[ -n "${FC_DIR:-}" ] || { echo "FC_DIR: no manager tree found (set FC_DIR)"; exit 1; }
mkdir -p "$FC_DIR/instances"
export PATH="$PATH:/usr/sbin:/sbin"      # mkfs.ext4
IMG="$FC_DIR/instances/$NAME-rootfs.ext4"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kaim56-$NAME-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cd "$HERE"

echo "== [1] docker build =="
docker build -f Dockerfile.rootfs -t "kaim56-$NAME-rootfs" .
docker run --rm "kaim56-$NAME-rootfs" repowise --version

echo "== [2] export =="
mkdir -p "$WORK/rootfs"
CID=$(docker create "kaim56-$NAME-rootfs")
docker export "$CID" | tar -C "$WORK/rootfs" -xf -
docker rm "$CID" >/dev/null

echo "== [3] ext4 image =="
truncate -s "${ROOTFS_MB:-3072}M" "$WORK/rootfs.ext4"
mkfs.ext4 -F -q -d "$WORK/rootfs" "$WORK/rootfs.ext4"

echo "== [4] place atomically =="
# Never write INTO the target: a running VM holds it open as a block device.
mv -f "$WORK/rootfs.ext4" "$IMG.new" && mv -f "$IMG.new" "$IMG"
ls -la "$IMG"
echo "done: template '$NAME' -> $IMG (restart the indexer to pick it up)"
