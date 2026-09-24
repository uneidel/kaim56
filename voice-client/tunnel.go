// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"crypto/sha256"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// iroh transport: when the config holds a gateway NodeId ("iroh"), the client
// starts kaim56-tunnel itself as a child process and talks to its local port
// — the same route the app takes, except that the app embeds the Rust stack
// and we run it as a neighbouring process (the Go client stays cgo-free). If
// the client dies, the tunnel dies with it (Pdeathsig); the tunnel identity
// lives in ~/.config/kaim56-tunnel.key and must be added once to the gateway
// allowlist (web UI, iroh tab).

// materializeTunnel writes the embedded tunnel bytes as an executable file
// into the cache — the file name carries the hash, so a new release replaces
// itself and old versions do not collide. Written via temp file + rename so a
// parallel start never executes a half-written file.
func materializeTunnel(data []byte, dir string) (string, error) {
	sum := sha256.Sum256(data)
	path := filepath.Join(dir, fmt.Sprintf("kaim56-tunnel-%x", sum[:6]))
	if st, err := os.Stat(path); err == nil && st.Size() == int64(len(data)) {
		return path, nil
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp(dir, ".tunnel-*")
	if err != nil {
		return "", err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return "", err
	}
	tmp.Close()
	if err := os.Chmod(tmp.Name(), 0o755); err != nil {
		return "", err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return "", err
	}
	return path, nil
}

// findTunnel: the embedded tunnel first (release build, same version),
// otherwise next to our own binary, then in the PATH.
func findTunnel() string {
	if len(embeddedTunnel) > 0 {
		cache, err := os.UserCacheDir()
		if err != nil {
			cache = os.TempDir()
		}
		if p, err := materializeTunnel(embeddedTunnel, filepath.Join(cache, "kaim56-voice")); err == nil {
			return p
		}
		fmt.Fprintln(os.Stderr, "[kaim56-voice] embedded tunnel could not be unpacked, looking for an external one")
	}
	if exe, err := os.Executable(); err == nil {
		p := filepath.Join(filepath.Dir(exe), "kaim56-tunnel")
		if st, err := os.Stat(p); err == nil && st.Mode()&0o111 != 0 {
			return p
		}
	}
	if p, err := exec.LookPath("kaim56-tunnel"); err == nil {
		return p
	}
	return ""
}

// startTunnel starts the tunnel and waits until the local port accepts.
// Returns the base_url and a cleanup function.
func startTunnel(nodeID, listen string) (string, func(), error) {
	exe := findTunnel()
	if exe == "" {
		return "", nil, fmt.Errorf("kaim56-tunnel not found — put it next to " +
			"kaim56-voice or into the PATH (binary from iroh-gw/, see README)")
	}
	// Show the identity: without an allowlist entry all you get is "unreachable".
	if out, err := exec.Command(exe, "--id").Output(); err == nil {
		fmt.Fprintf(os.Stderr, "[kaim56-voice] iroh identity: %s "+
			"(must be in the gateway allowlist — web UI, iroh tab)\n",
			strings.TrimSpace(string(out)))
	}
	cmd := exec.Command(exe, nodeID, listen)
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGTERM}
	if err := cmd.Start(); err != nil {
		return "", nil, fmt.Errorf("starting the tunnel: %w", err)
	}
	stop := func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}
	for i := 0; i < 75; i++ { // the iroh endpoint needs a few seconds
		if c, err := net.DialTimeout("tcp", listen, time.Second); err == nil {
			c.Close()
			return "http://" + listen, stop, nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	stop()
	return "", nil, fmt.Errorf("tunnel did not come up on %s", listen)
}
