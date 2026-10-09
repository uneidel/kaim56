#!/usr/bin/env bash
# Builds all artifacts: firecracker binary, guest kernel (vmlinux), rootfs.ext4.
# Braucht: docker, curl. Als DEIN User ausfuehren
# (no root needed; the docker group is enough). The login comes from the manager at boot.
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

echo "[3/5] rootfs.ext4 (build-rootfs.sh: owners as in the image, no login baked in)..."
bash ./build-rootfs.sh
echo "FERTIG: firecracker | vmlinux | rootfs.ext4"
echo "Next: 'sudo ./net.sh' then 'sudo ./run.sh' (or install the systemd unit)."
