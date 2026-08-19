#!/usr/bin/env bash
# Baut das Prime-Agent-Rootfs (primeintellect.ai) -> instances/prime-rootfs.ext4.
cd "$(dirname "$0")"
INST="${FC_DIR:-/home/ulrich/firecracker}"/instances/prime-rootfs.ext4
export PATH="$PATH:/usr/sbin:/sbin"  # mkfs.ext4 liegt in /usr/sbin
fail(){ echo "❌ FEHLER in: $1"; exit 1; }

echo "== [1] docker build (node24 + prime-agent + ipython) =="
docker build -f Dockerfile.prime -t prime-fc-rootfs . || fail "docker build"

echo "== [1b] prime-agent-Binary im Image pruefen =="
docker run --rm prime-fc-rootfs sh -c 'command -v prime-agent && prime-agent --version' \
  || echo "   ⚠️ WARN: 'prime-agent' nicht direkt gefunden — Bridge nutzt PATH /usr/local/bin"

echo "== [2] Rootfs exportieren =="
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create prime-fc-rootfs) || fail "docker create"
docker export "$CID" | tar -C rootfs -xf - || fail "docker export"
docker rm "$CID" >/dev/null
mkdir -p rootfs/home/node/workspace

echo "== [3] ext4-Image =="
SIZE_MB=${ROOTFS_MB:-4096}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4 || fail truncate
mkfs.ext4 -F -q -d rootfs rootfs.ext4 || fail "mkfs.ext4 -d"
rm -rf rootfs

echo "== [4] als Instanz-Rootfs ablegen =="
cp rootfs.ext4 "$INST.new" || fail "cp -> instances/"
mv -f "$INST.new" "$INST" || fail "mv -> instances/"
echo "✅ FERTIG:"; ls -lh "$INST"
