// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Iroh-Transport: steht in der Config eine Gateway-NodeId ("iroh"), startet
// der Client kaim56-tunnel selbst als Kindprozess und redet mit dessen
// lokalem Port — derselbe Weg, den die App nimmt, nur dass die App den
// Rust-Stack eingebettet hat und wir ihn als Nachbarprozess fahren (der
// Go-Client bleibt cgo-frei). Stirbt der Client, stirbt der Tunnel mit
// (Pdeathsig); die Tunnel-Identitaet liegt in ~/.config/kaim56-tunnel.key
// und muss einmalig in die Gateway-Allowlist (Web-UI, iroh-Tab).

// findTunnel sucht das Tunnel-Binary: neben dem eigenen Binary, dann im PATH.
func findTunnel() string {
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

// startTunnel startet den Tunnel und wartet, bis der lokale Port annimmt.
// Liefert die base_url und eine Aufraeum-Funktion.
func startTunnel(nodeID, listen string) (string, func(), error) {
	exe := findTunnel()
	if exe == "" {
		return "", nil, fmt.Errorf("kaim56-tunnel nicht gefunden — neben " +
			"kaim56-voice legen oder in den PATH (Binary aus iroh-gw/, siehe README)")
	}
	// Identitaet zeigen: ohne Allowlist-Eintrag kommt sonst nur "unreachable".
	if out, err := exec.Command(exe, "--id").Output(); err == nil {
		fmt.Fprintf(os.Stderr, "[kaim56-voice] iroh-Identität: %s "+
			"(muss in der Gateway-Allowlist stehen — Web-UI, iroh-Tab)\n",
			strings.TrimSpace(string(out)))
	}
	cmd := exec.Command(exe, nodeID, listen)
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGTERM}
	if err := cmd.Start(); err != nil {
		return "", nil, fmt.Errorf("tunnel starten: %w", err)
	}
	stop := func() {
		if cmd.Process != nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}
	for i := 0; i < 75; i++ { // iroh-Endpoint braucht ein paar Sekunden
		if c, err := net.DialTimeout("tcp", listen, time.Second); err == nil {
			c.Close()
			return "http://" + listen, stop, nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	stop()
	return "", nil, fmt.Errorf("tunnel kam auf %s nicht hoch", listen)
}
