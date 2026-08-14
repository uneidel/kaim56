#!/usr/bin/env bash
# Build the native katfs host node inside Docker (no local Rust needed).
# Output: ../dist/katfs-node
#
# Named volumes cache the crate registry and the target dir, so a second run
# skips the cold compile (iroh + ring are the expensive part).
#
# Usage:  ./build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

docker run --rm \
  -v "$HERE":/work -w /work \
  -v katfs_node_registry:/usr/local/cargo/registry \
  -v katfs_node_target:/work/target \
  rust:latest bash -eux -c '
    cargo build --release
    # Aus dem gecachten Volume herauskopieren, damit das Ergebnis auf dem Host
    # liegt und nicht im Volume verschwindet.
    mkdir -p /work/.out
    cp target/release/katfs-node /work/.out/katfs-node
    chmod 0755 /work/.out/katfs-node
    # Sonst gehoert das Ergebnis root und der Host kann es nicht wegbewegen.
    chown -R '"$(id -u)":"$(id -g)"' /work/.out
  '

mkdir -p "$HERE/../dist"
mv "$HERE/.out/katfs-node" "$HERE/../dist/katfs-node"
rmdir "$HERE/.out" 2>/dev/null || true
echo "built: $HERE/../dist/katfs-node"
"$HERE/../dist/katfs-node" --version 2>/dev/null || true
