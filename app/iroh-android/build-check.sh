#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm -v "$HERE":/work -w /work \
  -v katfs_node_registry:/usr/local/cargo/registry \
  -v kaim_iroh_target:/work/target \
  rust:latest bash -eux -c 'cargo build --release 2>&1 | tail -30'
