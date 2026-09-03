// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//go:build embedtunnel

package main

import _ "embed"

// Release-Build (build.sh, -tags embedtunnel): kaim56-tunnel steckt IM
// Binary — eine Datei zum Kopieren. Der Gate-/Dev-Build kommt ohne die
// Datei aus (embed_off.go), damit go test in einem frischen Checkout laeuft.
//
//go:embed embedded/kaim56-tunnel
var embeddedTunnel []byte
