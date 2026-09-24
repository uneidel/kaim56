#!/usr/bin/env bash
# Build this agent's rootfs -> <manager>/instances/<folder>-rootfs.ext4.
# The image name follows the FOLDER name, so a copy of this folder under a
# new name builds its own image; template.json's "rootfs" must say the same.
#
#   FC_DIR   the manager tree (default: ../../manager next to this folder in
#            the repo, or $HOME/firecracker for the live tree)
#   ROOTFS_MB  image size (default 1024)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="$(basename "$HERE")"
if [ -z "${FC_DIR:-}" ]; then
  for d in "$HERE/../../manager" "$HOME/firecracker"; do
    [ -d "$d/instances" ] && FC_DIR="$(cd "$d" && pwd)" && break
  done
fi
[ -n "${FC_DIR:-}" ] || { echo "FC_DIR: no manager tree found (set FC_DIR)"; exit 1; }
export PATH="$PATH:/usr/sbin:/sbin"      # mkfs.ext4
IMG="$FC_DIR/instances/$NAME-rootfs.ext4"
cd "$HERE"

echo "== [1] docker build =="
docker build -t "kaim56-$NAME-rootfs" .

echo "== [2] export =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create "kaim56-$NAME-rootfs")
docker export "$CID" | tar -C rootfs -xf -
docker rm "$CID" >/dev/null

echo "== [3] ext4 image =="
rm -f rootfs.ext4
truncate -s "${ROOTFS_MB:-1024}M" rootfs.ext4
mkfs.ext4 -F -q -d rootfs rootfs.ext4
rm -rf rootfs

echo "== [4] place atomically =="
# Never write INTO the target: a running VM holds it open as a block device.
# Next to it, then rename — a running VM keeps its old inode until it stops.
mv -f rootfs.ext4 "$IMG.new" && mv -f "$IMG.new" "$IMG"
ls -la "$IMG"
echo "done: template '$NAME' -> $IMG (restart instances to pick it up)"
