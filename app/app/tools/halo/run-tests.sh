#!/usr/bin/env bash
# Tests the glasses' device side without hardware. Lua is not on the host,
# hence the container (alpine + lua5.4).
set -e
cd "$(dirname "$0")/../.."
docker run --rm -v "$PWD:/w" -w /w alpine:latest \
  sh -c "apk add --no-cache lua5.4 >/dev/null 2>&1 && lua5.4 tools/halo/test_frame_app.lua"
