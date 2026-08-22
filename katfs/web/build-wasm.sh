#!/usr/bin/env bash
# Build the katfs-web wasm bindings inside Docker (no local Rust needed).
# Output: web/wasm/katfs_web.js + web/wasm/katfs_web_bg.wasm  (vendored, no CDN).
#
# Usage:  ./build-wasm.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Named volumes cache the crate registry, the built target dir and the
# wasm-bindgen-cli binary, so a second run skips the ~10 min cold compile.
docker run --rm \
  -v "$HERE":/work -w /work/wasm-src \
  -v katfs_cargo_registry:/usr/local/cargo/registry \
  -v katfs_cargo_bin:/cargo-bin \
  -v katfs_target:/work/wasm-src/target \
  -e CARGO_INSTALL_ROOT=/cargo-bin \
  -e RUSTFLAGS='--cfg getrandom_backend="wasm_js"' \
  rust:latest bash -eux -c '
    # ring (via iroh tls-ring) compiles C for wasm32 and needs clang/llvm.
    apt-get update && apt-get install -y --no-install-recommends clang
    rustup target add wasm32-unknown-unknown
    export PATH="/cargo-bin/bin:$PATH"
    command -v wasm-bindgen >/dev/null 2>&1 || \
      cargo install wasm-bindgen-cli --version "^0.2" --locked || \
      cargo install wasm-bindgen-cli
    cargo build --release --target wasm32-unknown-unknown
    mkdir -p /work/wasm
    wasm-bindgen \
      target/wasm32-unknown-unknown/release/katfs_web.wasm \
      --out-dir /work/wasm --target web --no-typescript
  '
echo "Built -> $HERE/wasm/"
ls -la "$HERE/wasm/"
