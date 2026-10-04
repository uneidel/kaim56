#!/usr/bin/env bash
# Release build of the voice client: ONE binary, kaim56-tunnel and kaim56-wake
# embedded. Builds them first if needed (Docker, see iroh-gw/ and wake/).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GO="${GO:-$(command -v go || echo "$HOME/.local/go-toolchain/bin/go")}"

TUN="$HERE/../dist/kaim56-tunnel"
[ -x "$TUN" ] || "$HERE/../iroh-gw/build.sh"
mkdir -p "$HERE/embedded"
cp "$TUN" "$HERE/embedded/kaim56-tunnel"
WAKE="$HERE/../dist/kaim56-wake"
[ -x "$WAKE" ] || "$HERE/../wake/build.sh"
cp "$WAKE" "$HERE/embedded/kaim56-wake"

cd "$HERE"
CGO_ENABLED=0 "$GO" build -trimpath -ldflags="-s -w" -tags embedtunnel -o kaim56-voice .
echo "built: $HERE/kaim56-voice (tunnel + wake embedded)"
