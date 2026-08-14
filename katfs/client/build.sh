#!/usr/bin/env bash
# Baut katfs-share fuer Linux in Docker (kein lokales Rust noetig).
# Output: ../dist/katfs-share
#
# Fuer macOS gibt es von hier aus kein Cross-Compile (Apple-SDK fehlt) — dort
# auf dem Rechner selbst bauen:
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
#   cd client && cargo build --release && ./target/release/katfs-share ...
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

docker run --rm \
  -v "$HERE":/work -w /work \
  -v katfs_client_registry:/usr/local/cargo/registry \
  -v katfs_client_target:/work/target \
  rust:latest bash -eux -c '
    cargo build --release
    mkdir -p /work/.out
    cp target/release/katfs-share /work/.out/katfs-share
    chmod 0755 /work/.out/katfs-share
    chown -R '"$(id -u):$(id -g)"' /work/.out
  '

mkdir -p "$HERE/../dist"
mv "$HERE/.out/katfs-share" "$HERE/../dist/katfs-share"
rmdir "$HERE/.out" 2>/dev/null || true
echo "built: $HERE/../dist/katfs-share"
