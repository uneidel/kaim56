#!/usr/bin/env bash
# Build the iroh app<->manager gateway inside Docker (no local Rust needed).
# Output: dist/iroh-gw. Reuses the katfs crate registry cache so iroh/ring are
# already downloaded; a separate target volume caches this crate's artifacts.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

docker run --rm \
  -v "$HERE":/work -w /work \
  -v katfs_node_registry:/usr/local/cargo/registry \
  -v iroh_gw_target:/work/target \
  rust:latest bash -eux -c '
    cargo build --release
    mkdir -p /work/.out
    cp target/release/iroh-gw /work/.out/iroh-gw
    cp target/release/testclient /work/.out/testclient 2>/dev/null || true
    chmod 0755 /work/.out/iroh-gw
    chown -R '"$(id -u)":"$(id -g)"' /work/.out
  '

mkdir -p "$HERE/../dist"
mv "$HERE/.out/iroh-gw" "$HERE/../dist/iroh-gw"
cp "$HERE/.out/testclient" "$HERE/../dist/testclient" 2>/dev/null || true
rmdir "$HERE/.out" 2>/dev/null || true
echo "built: $HERE/../dist/iroh-gw"
"$HERE/../dist/iroh-gw" --version 2>/dev/null || true
