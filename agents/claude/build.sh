#!/usr/bin/env bash
# Builds all artifacts: firecracker binary, guest kernel (vmlinux), rootfs.ext4.
# Braucht: docker, curl, e2fsprogs (mkfs.ext4 -d). Als DEIN User ausfuehren
# (no root needed; the docker group is enough). Places login into the rootfs.
set -euo pipefail
cd "$(dirname "$0")"
ARCH=x86_64

echo "[1/5] Firecracker-Binary..."
if [ ! -x ./firecracker ]; then
  ver=$(curl -fsSL https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest \
        | grep -oP '"tag_name":\s*"\K[^"]+')
  curl -fsSL "https://github.com/firecracker-microvm/firecracker/releases/download/${ver}/firecracker-${ver}-${ARCH}.tgz" -o fc.tgz
  tar -xzf fc.tgz
  cp "release-${ver}-${ARCH}/firecracker-${ver}-${ARCH}" ./firecracker
  cp "release-${ver}-${ARCH}/jailer-${ver}-${ARCH}"      ./jailer 2>/dev/null || true
  chmod +x ./firecracker ./jailer 2>/dev/null || true
  rm -rf fc.tgz "release-${ver}-${ARCH}"
  echo "     ${ver}"
fi

echo "[2/5] Guest kernel (vmlinux)..."
if [ ! -f ./vmlinux ]; then
  # direct, known CI kernel URL (the listing filter was error-prone)
  curl -fsSL "https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.12/${ARCH}/vmlinux-6.1.128" -o vmlinux
  echo "     vmlinux-6.1.128"
fi

echo "[3/5] Build rootfs image (docker)..."
cp ../claude-signal-bridge/bridge.py ./bridge.py
docker build -f Dockerfile.rootfs -t claude-fc-rootfs .

echo "[4/5] Export rootfs + insert login..."
rm -rf rootfs && mkdir -p rootfs
CID=$(docker create claude-fc-rootfs)
docker export "$CID" | tar -C rootfs -xf -
docker rm "$CID" >/dev/null
mkdir -p rootfs/root/.claude rootfs/root/workspace
if [ -f "$HOME/.claude/.credentials.json" ]; then
  cp "$HOME/.claude/.credentials.json" rootfs/root/.claude/
  chmod 600 rootfs/root/.claude/.credentials.json
  echo "     login inserted."
else
  echo "     WARN: ~/.claude/.credentials.json missing -> guest has no login."
fi

echo "[5/5] ext4-Image erzeugen..."
SIZE_MB=${ROOTFS_MB:-3072}
rm -f rootfs.ext4
truncate -s "${SIZE_MB}M" rootfs.ext4
mkfs.ext4 -F -q -d rootfs rootfs.ext4
rm -rf rootfs
echo "FERTIG: firecracker | vmlinux | rootfs.ext4"
echo "Next: 'sudo ./net.sh' then 'sudo ./run.sh' (or install the systemd unit)."
