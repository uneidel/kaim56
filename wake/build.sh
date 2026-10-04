#!/usr/bin/env bash
# Build kaim56-wake inside Docker (no local Rust). Output: ../dist/kaim56-wake
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm -v "$HERE":/work -w /work \
  -v kaim56_wake_registry:/usr/local/cargo/registry -v kaim56_wake_target:/work/target \
  rust:latest bash -eux -c '
    cargo build --release
    mkdir -p /work/.out && cp target/release/kaim56-wake /work/.out/ && chown -R '"$(id -u):$(id -g)"' /work/.out /work/Cargo.lock'
mkdir -p "$HERE/../dist" && mv "$HERE/.out/kaim56-wake" "$HERE/../dist/kaim56-wake" && rmdir "$HERE/.out"
echo "built: $HERE/../dist/kaim56-wake"
