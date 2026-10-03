// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"crypto/sha256"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// iroh transport, as in the voice client: kaim56-tunnel (Rust, iroh-gw/) runs
// as a child process and offers the manager on a local port; the CLI stays a
// cgo-free Go binary. The tunnel identity is ~/.config/kaim56-tunnel.key — the
// one the voice client uses, so a desktop paired for voice is paired for the
// CLI too. If a tunnel already listens on the port (the voice client's), it is
// reused: two processes with one identity would fight over the relay.

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
	return path, os.Rename(tmp.Name(), path)
}

// findTunnel: the embedded one (release build), next to our binary, the PATH.
func findTunnel() string {
	if len(embeddedTunnel) > 0 {
		cache, err := os.UserCacheDir()
		if err != nil {
			cache = os.TempDir()
		}
		if p, err := materializeTunnel(embeddedTunnel, filepath.Join(cache, "kaim56-cli")); err == nil {
			return p
		}
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

// tunnelUp: is something on `listen` that speaks HTTP like the manager?
func tunnelUp(listen string) bool {
	c := &http.Client{Timeout: 3 * time.Second}
	r, err := c.Get("http://" + listen + "/api/version")
	if err != nil {
		return false
	}
	r.Body.Close()
	return r.StatusCode == 200 || r.StatusCode == 401 || r.StatusCode == 429
}

func tunnelID() (string, error) {
	exe := findTunnel()
	if exe == "" {
		return "", fmt.Errorf("kaim56-tunnel not found")
	}
	out, err := exec.Command(exe, "--id").Output()
	return strings.TrimSpace(string(out)), err
}

// startTunnel -> base URL and a stop function (a no-op for a reused tunnel).
func startTunnel(nodeID, listen string, verbose bool) (string, func(), error) {
	if tunnelUp(listen) {
		return "http://" + listen, func() {}, nil
	}
	exe := findTunnel()
	if exe == "" {
		return "", nil, fmt.Errorf("kaim56-tunnel not found — use a release build (tunnel embedded), " +
			"put it next to kaim56 or into the PATH, or set base_url for a direct route")
	}
	cmd := exec.Command(exe, nodeID, listen)
	if verbose {
		cmd.Stderr = os.Stderr
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGTERM, Setpgid: true}
	if err := cmd.Start(); err != nil {
		return "", nil, fmt.Errorf("starting the tunnel: %w", err)
	}
	stop := func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}
	for i := 0; i < 100; i++ { // the iroh endpoint needs a few seconds
		if c, err := net.DialTimeout("tcp", listen, time.Second); err == nil {
			c.Close()
			return "http://" + listen, stop, nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	stop()
	id, _ := tunnelID()
	return "", nil, fmt.Errorf("the iroh tunnel did not come up on %s — is this device (%s) in the gateway allowlist? "+
		"(manager web UI → iroh)", listen, id)
}
