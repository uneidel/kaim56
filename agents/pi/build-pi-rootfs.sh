#!/usr/bin/env bash
# Baut das Pi-Agent-Rootfs (pi.dev) -> instances/pi-rootfs.ext4.
cd "$(dirname "$0")"
INST=/home/ulrich/firecracker/instances/pi-rootfs.ext4
export PATH="$PATH:/usr/sbin:/sbin"  # mkfs.ext4 liegt in /usr/sbin
fail(){ echo "❌ FEHLER in: $1"; exit 1; }

echo "== [1] docker build (node + pi) =="
docker build -f Dockerfile.pi -t pi-fc-rootfs . || fail "docker build"

echo "== [1b] pi-Binary im Image pruefen =="
docker run --rm pi-fc-rootfs sh -c 'command -v pi && pi --version' || echo "   ⚠️ WARN: 'pi' nicht direkt gefunden — Bridge nutzt PATH /usr/local/bin"

echo "== [2] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create pi-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null
mkdir -p rootfs/home/node/workspace

echo "== [3] ext4-Image =="
SIZE_MB=${ROOTFS_MB:-3072}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail truncate
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d"
rm -rf rootfs

echo "== [4] als Instanz-Rootfs ablegen =="
cp rootfs.ext4 "$INST.new" || fail "cp -> instances/"
mv -f "$INST.new" "$INST" || fail "mv -> instances/"
echo "✅ FERTIG:"; ls -lh "$INST"
