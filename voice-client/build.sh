#!/usr/bin/env bash
# Release-Build des Sprachclients: EIN Binary, kaim56-tunnel eingebettet.
# Baut den Tunnel bei Bedarf zuerst (Docker, siehe iroh-gw/build.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GO="${GO:-$(command -v go || echo "$HOME/.local/go-toolchain/bin/go")}"

TUN="$HERE/../dist/kaim56-tunnel"
[ -x "$TUN" ] || "$HERE/../iroh-gw/build.sh"
mkdir -p "$HERE/embedded"
cp "$TUN" "$HERE/embedded/kaim56-tunnel"

cd "$HERE"
CGO_ENABLED=0 "$GO" build -trimpath -ldflags="-s -w" -tags embedtunnel -o kaim56-voice .
echo "built: $HERE/kaim56-voice (tunnel embedded)"
