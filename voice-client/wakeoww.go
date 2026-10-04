// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// wake_mode "oww": openWakeWord ("Hey Bender") as an acoustic gate. The
// sidecar kaim56-wake (Rust/tract, wake/) gets every microphone frame while
// we listen and prints one score per 80 ms; an utterance goes to STT only if
// the wake word scored above the threshold while it was spoken. Nothing
// leaves the desktop otherwise — like the local DTW gate, but trained.

const (
	owwDefaultThreshold = 0.5
	owwSlack            = 1500 * time.Millisecond // the score peaks just after the word
	armedFor            = 8 * time.Second         // "Hey Bender" alone: the next sentence needs no word
)

type owwGate struct {
	threshold float64
	cmd       *exec.Cmd
	in        io.WriteCloser
	mu        sync.Mutex
	hits      []time.Time // scores >= threshold, last few seconds
	peak      float64     // best score since the last utterance (for "✕" feedback)
	n         int         // scores read (tests wait on it)
}

func findWake() string {
	if len(embeddedWake) > 0 {
		cache, err := os.UserCacheDir()
		if err != nil {
			cache = os.TempDir()
		}
		if p, err := materializeAs("kaim56-wake", embeddedWake, filepath.Join(cache, "kaim56-voice")); err == nil {
			return p
		}
	}
	if exe, err := os.Executable(); err == nil {
		if p := filepath.Join(filepath.Dir(exe), "kaim56-wake"); isExec(p) {
			return p
		}
	}
	if p, err := exec.LookPath("kaim56-wake"); err == nil {
		return p
	}
	return ""
}

func isExec(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.Mode()&0o111 != 0
}

// startOww starts the sidecar; model = "" uses the embedded Hey Bender model.
func startOww(threshold float64, model string) (*owwGate, error) {
	exe := findWake()
	if exe == "" {
		return nil, fmt.Errorf("kaim56-wake not found — use a release build (embedded) or put it next to kaim56-voice")
	}
	if threshold <= 0 {
		threshold = owwDefaultThreshold
	}
	args := []string{}
	if model != "" {
		args = append(args, model)
	}
	cmd := exec.Command(exe, args...)
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGTERM}
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("starting kaim56-wake: %w", err)
	}
	g := &owwGate{threshold: threshold, cmd: cmd, in: in}
	go g.read(out)
	return g, nil
}

func (g *owwGate) read(out io.Reader) {
	sc := bufio.NewScanner(out)
	for sc.Scan() {
		s, err := strconv.ParseFloat(strings.TrimSpace(sc.Text()), 64)
		if err != nil {
			continue
		}
		g.score(s, time.Now())
	}
}

func (g *owwGate) score(s float64, now time.Time) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.n++
	if s > g.peak {
		g.peak = s
	}
	if s >= g.threshold {
		g.hits = append(g.hits, now)
	}
	for len(g.hits) > 0 && now.Sub(g.hits[0]) > time.Minute {
		g.hits = g.hits[1:]
	}
}

// Feed: one microphone frame (16 kHz s16le); a write error is ignored — a
// dead sidecar simply never wakes us (Hit stays false), stderr says why.
func (g *owwGate) Feed(frame []byte) { g.in.Write(frame) }

// Hit: was the wake word heard since `since`? Consumes the hits and the peak.
func (g *owwGate) Hit(since time.Time) (bool, float64) {
	g.mu.Lock()
	defer g.mu.Unlock()
	hit := false
	for _, t := range g.hits {
		if !t.Before(since) {
			hit = true
		}
	}
	peak := g.peak
	g.hits, g.peak = nil, 0
	return hit, peak
}

func (g *owwGate) Close() {
	g.in.Close()
	if g.cmd.Process != nil {
		g.cmd.Process.Kill()
		g.cmd.Wait()
	}
}
