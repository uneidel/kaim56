// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestOwwHitWindow(t *testing.T) {
	g := &owwGate{threshold: 0.5}
	t0 := time.Now()
	g.score(0.3, t0)
	g.score(0.9, t0.Add(time.Second))
	if hit, peak := g.Hit(t0.Add(2 * time.Second)); hit || peak != 0.9 {
		t.Fatalf("a hit before the window does not count, peak is kept for feedback: %v %v", hit, peak)
	}
	g.score(0.7, t0.Add(3*time.Second))
	if hit, _ := g.Hit(t0.Add(2 * time.Second)); !hit {
		t.Fatal("a hit inside the window counts")
	}
	if hit, peak := g.Hit(t0); hit || peak != 0 {
		t.Fatal("Hit consumes hits and peak")
	}
}

// The real sidecar on Piper speech: "Hey Bender." wakes, "Hello Kati, how
// are you today?" does not. Skipped without a kaim56-wake binary.
func TestOwwSidecarHeyBender(t *testing.T) {
	if findWake() == "" {
		t.Skip("kaim56-wake not built (wake/build.sh)")
	}
	for _, c := range []struct {
		file string
		want bool
	}{{"testdata/hey_bender.raw", true}, {"testdata/hello_kati.raw", false}} {
		g, err := startOww(0, "")
		if err != nil {
			t.Fatal(err)
		}
		pcm, _ := os.ReadFile(c.file)
		start := time.Now()
		for i := 0; i < len(pcm); i += frameBytes {
			g.Feed(pcm[i:min(i+frameBytes, len(pcm))])
		}
		want := len(pcm) / 2560
		for d := 0; d < 200; d++ {
			g.mu.Lock()
			n := g.n
			g.mu.Unlock()
			if n >= want {
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		hit, peak := g.Hit(start)
		g.Close()
		if hit != c.want {
			t.Errorf("%s: hit=%v (peak %.3f), want %v", c.file, hit, peak, c.want)
		}
	}
}

// The gate in the client: no wake word -> nothing is uploaded; the word
// alone -> "Yes?" and the next sentence passes without it.
func TestOwwGateUploadsOnlyAfterTheWakeWord(t *testing.T) {
	var stt, chat int32
	next := "Hey Bender"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/stt":
			atomic.AddInt32(&stt, 1)
			io.WriteString(w, `{"text":"`+next+`"}`)
		case strings.HasPrefix(r.URL.Path, "/api/chat/"):
			atomic.AddInt32(&chat, 1)
			io.WriteString(w, "ok")
		default:
			w.WriteHeader(500) // TTS: no playback in tests
		}
	}))
	defer srv.Close()
	c := NewVoiceClient(Config{BaseURL: srv.URL, User: "u", Pass: "p", Instance: "x", WakeWord: "Hey Bender, Bender"}, true)
	c.oww = &owwGate{threshold: 0.5}
	pcm := make([]byte, sampleRate*2) // 1 s

	c.handleUtterance(pcm)
	if stt != 0 || !strings.HasPrefix(c.lastHeard, "✕ wake") {
		t.Fatalf("without the wake word nothing may be uploaded: stt=%d %q", stt, c.lastHeard)
	}
	c.oww.score(0.9, time.Now())
	c.handleUtterance(pcm) // "Hey Bender" alone -> ack, armed
	if stt != 1 || chat != 0 || c.armedUntil.IsZero() {
		t.Fatalf("the word alone: stt=%d chat=%d armed=%v", stt, chat, c.armedUntil)
	}
	next = "what time is it"
	c.handleUtterance(pcm) // armed: passes without a new hit
	if stt != 2 || chat != 1 || c.lastHeard != "what time is it" {
		t.Fatalf("armed sentence: stt=%d chat=%d %q", stt, chat, c.lastHeard)
	}
	c.handleUtterance(pcm) // armed only once
	if stt != 2 {
		t.Fatalf("armed is used up: stt=%d", stt)
	}
}
