// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//go:build !embedtunnel

package main

// Without -tags embedtunnel (dev/gate): no embedded tunnel, findTunnel looks
// for it next to the binary or in the PATH.
var embeddedTunnel []byte
