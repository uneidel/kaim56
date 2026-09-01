#!/usr/bin/env bash
# Builds the OpenRouter agent rootfs -> instances/openrouter-rootfs.ext4.
cd "$(dirname "$0")"
# mkfs.ext4 lives in /usr/sbin — that is not on a normal user shell's PATH,
# and root doesn't need it for a file image.
export PATH="$PATH:/usr/sbin:/sbin"
INST="${FC_DIR:-/home/ulrich/firecracker}"/instances/openrouter-rootfs.ext4
fail(){ echo "❌ FEHLER in: $1"; exit 1; }

echo "== [1] docker build =="
docker build -f Dockerfile.openrouter -t openrouter-fc-rootfs . || fail "docker build"

echo "== [2] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create openrouter-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null
mkdir -p rootfs/home/node/workspace

echo "== [3] ext4-Image =="
SIZE_MB=${ROOTFS_MB:-2048}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail truncate
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d"
rm -rf rootfs

echo "== [4] place as instance rootfs =="
# NEVER cp into the target file: if a running microVM holds it open as a block
# device, old and new image mix and the guest hits an ext4 checksum panic on
# the next boot. Write alongside first, then swap in atomically — a running VM
# keeps its old inode until stop.
for pid in "${FC_DIR:-/home/ulrich/firecracker}"/run/*.pid; do
  [ -e "$pid" ] || continue
  p=$(cat "$pid" 2>/dev/null)
  # /proc instead of kill -0: firecracker runs as root, a signal test from a
  # user shell fails there and the warning would stay silent.
  if [ -n "$p" ] && [ -d "/proc/$p" ]; then
    n=$(basename "$pid" .pid)
    grep -q "openrouter-rootfs" ""${FC_DIR:-/home/ulrich/firecracker}"/instances/$n.json" 2>/dev/null &&
      echo "⚠️  instance '$n' is running on this rootfs — it sees the new image only after stop/start."
  fi
done
cp rootfs.ext4 "$INST.new" || fail "cp -> instances/"
mv -f "$INST.new" "$INST" || fail "mv -> instances/"
echo "✅ FERTIG:"; ls -lh "$INST"
