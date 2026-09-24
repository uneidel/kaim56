// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

// version of this build. Bumping it and pushing to main IS the release: the
// pipeline (.github/workflows/voice.yml) tags voice-v<version>, attaches the
// binary and the running clients update themselves from it (update.go).
// Every version needs its section in RELEASE_NOTES.md first.
const version = "1.1.0"
