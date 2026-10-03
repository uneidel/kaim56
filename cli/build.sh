#!/usr/bin/env bash
# Release build of the CLI: ONE binary with kaim56-tunnel embedded (iroh route).
# Builds the tunnel first if needed (Docker, see iroh-gw/build.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GO="${GO:-$(command -v go || echo "$HOME/.local/go-toolchain/bin/go")}"

TUN="$HERE/../dist/kaim56-tunnel"
[ -x "$TUN" ] || "$HERE/../iroh-gw/build.sh"
mkdir -p "$HERE/embedded"
cp "$TUN" "$HERE/embedded/kaim56-tunnel"

cd "$HERE"
"$GO" test ./...
CGO_ENABLED=0 "$GO" build -trimpath -ldflags="-s -w" -tags embedtunnel -o kaim56 .
echo "built: $HERE/kaim56 (tunnel embedded)"
