// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//go:build embedtunnel

package main

import _ "embed"

// Release build (build.sh, -tags embedtunnel): kaim56-tunnel is INSIDE the
// binary — one file to copy. The gate/dev build does without the file
// (embed_off.go), so go test runs in a fresh checkout.
//
//go:embed embedded/kaim56-tunnel
var embeddedTunnel []byte
