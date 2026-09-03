// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//go:build !embedtunnel

package main

// Ohne -tags embedtunnel (Dev/Gate): kein eingebetteter Tunnel, findTunnel
// sucht ihn neben dem Binary bzw. im PATH.
var embeddedTunnel []byte
