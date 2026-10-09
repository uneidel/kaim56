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
IMG="$FC_DIR/instances/$NAME-rootfs.ext4"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kaim56-$NAME-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cd "$HERE"
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

echo "== [1] docker build =="
docker build -f Dockerfile.rootfs -t "kaim56-$NAME-rootfs" .
docker run --rm "kaim56-$NAME-rootfs" repowise --version

echo "== [2] export + ext4 image (in a container) =="
mkrootfs "kaim56-$NAME-rootfs" "$WORK/rootfs.ext4" "${ROOTFS_MB:-3072}" || exit 1

echo "== [3] place atomically =="
# Never write INTO the target: a running VM holds it open as a block device.
mv -f "$WORK/rootfs.ext4" "$IMG.new" && mv -f "$IMG.new" "$IMG"
ls -la "$IMG"
echo "done: template '$NAME' -> $IMG (restart the indexer to pick it up)"
