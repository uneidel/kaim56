// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Unit-Tests fuer den Sprachclient — alles, was ohne Mikrofon und Manager
// testbar ist: VAD-Segmentierung an synthetischem PCM, WAV-Header,
// Vorlese-Filter, Config-Handling.
package main

import (
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func tone(ms int) []byte {
	n := sampleRate * ms / 1000
	out := make([]byte, n*2)
	for i := 0; i < n; i++ {
		v := int16(8000 * math.Sin(2*math.Pi*440*float64(i)/sampleRate))
		binary.LittleEndian.PutUint16(out[i*2:], uint16(v))
	}
	return out
}

func silence(ms int) []byte {
	n := sampleRate * ms / 1000
	out := make([]byte, n*2)
	for i := 0; i < n; i++ {
		v := int16(50)
		if i%2 == 0 {
			v = -50
		}
		binary.LittleEndian.PutUint16(out[i*2:], uint16(v))
	}
	return out
}

func feedAll(v *Vad, pcm []byte) [][]byte {
	var segs [][]byte
	for i := 0; i+frameBytes <= len(pcm); i += frameBytes {
		if seg := v.Feed(pcm[i : i+frameBytes]); seg != nil {
			segs = append(segs, seg)
		}
	}
	return segs
}

func testVad(endMs, minMs, maxS int) *Vad {
	cfg := defaultVadConfig()
	cfg.EndMs, cfg.MinMs = endMs, minMs
	if maxS > 0 {
		cfg.MaxS = maxS
	}
	return NewVad(cfg)
}

func TestUtteranceIsSegmentedWithPreroll(t *testing.T) {
	v := testVad(600, 300, 0)
	segs := feedAll(v, append(append(silence(1500), tone(900)...), silence(1200)...))
	if len(segs) != 1 {
		t.Fatalf("segs = %d, will 1", len(segs))
	}
	if min := 900 * sampleRate * 2 / 1000; len(segs[0]) < min {
		t.Fatalf("Segment %dB < Sprachdauer %dB", len(segs[0]), min)
	}
}

func TestShortBurstIsDropped(t *testing.T) {
	v := testVad(600, 400, 0)
	segs := feedAll(v, append(append(silence(1500), tone(200)...), silence(1200)...)) // Tuerknallen
	if len(segs) != 0 {
		t.Fatalf("segs = %d, will 0", len(segs))
	}
}

func TestSilenceAloneNeverTriggers(t *testing.T) {
	v := NewVad(defaultVadConfig())
	if segs := feedAll(v, silence(4000)); len(segs) != 0 || v.talk != nil {
		t.Fatalf("Stille loest aus: segs=%d talk=%v", len(segs), v.talk != nil)
	}
}

func TestTwoUtterancesTwoSegments(t *testing.T) {
	v := testVad(600, 300, 0)
	pcm := append(append(append(append(silence(1200), tone(800)...),
		silence(1200)...), tone(800)...), silence(1200)...)
	if segs := feedAll(v, pcm); len(segs) != 2 {
		t.Fatalf("segs = %d, will 2", len(segs))
	}
}

func TestSpeechCannotRaiseThresholdAboveItself(t *testing.T) {
	// Der Teppich darf aus Sprache nicht lernen: nach einem langen Satz muss
	// derselbe Pegel immer noch klar als stimmhaft gelten.
	v := testVad(600, 300, 0)
	feedAll(v, append(silence(900), tone(3000)...))
	if limit := float64(frameRMS(tone(30))) * 0.5; v.Threshold() >= limit {
		t.Fatalf("Threshold %.0f >= %.0f — Teppich hat Sprache gelernt", v.Threshold(), limit)
	}
}

func TestMaxLengthChunksContinuousSpeech(t *testing.T) {
	// Dauersprechen wird in Max-Laengen-Stuecke zerteilt, keins darueber.
	v := testVad(600, 300, 2)
	segs := feedAll(v, append(silence(900), tone(4000)...))
	if len(segs) != 2 {
		t.Fatalf("segs = %d, will 2", len(segs))
	}
	for _, s := range segs {
		if maxB := (2000/frameMs + 1) * frameBytes; len(s) > maxB {
			t.Fatalf("Segment %dB > max %dB", len(s), maxB)
		}
	}
}

func TestWavHeaderFields(t *testing.T) {
	pcm := tone(100)
	wav := wavWrap(pcm)
	if string(wav[:4]) != "RIFF" || string(wav[8:12]) != "WAVE" {
		t.Fatal("kein RIFF/WAVE-Header")
	}
	if r := binary.LittleEndian.Uint32(wav[24:]); r != sampleRate {
		t.Fatalf("Rate %d", r)
	}
	if n := binary.LittleEndian.Uint32(wav[40:]); int(n) != len(pcm) {
		t.Fatalf("data-Laenge %d != %d", n, len(pcm))
	}
	if string(wav[44:]) != string(pcm) {
		t.Fatal("PCM veraendert")
	}
}

func TestThinkBlocksAreStripped(t *testing.T) {
	if got := speakable("⟦think⟧inneres Gemurmel⟦/think⟧Hallo **Ulrich**!"); got != "Hallo Ulrich!" {
		t.Fatalf("got %q", got)
	}
}

func TestOpenThinkBlockIsStripped(t *testing.T) {
	if got := speakable("Antwort.⟦think⟧noch offen"); got != "Antwort." {
		t.Fatalf("got %q", got)
	}
}

func TestToolStatusLinesAreDropped(t *testing.T) {
	if got := speakable("🔧 listagents …\nEs laufen 5 Instanzen."); got != "Es laufen 5 Instanzen." {
		t.Fatalf("got %q", got)
	}
}

func TestCodeAndLinks(t *testing.T) {
	got := speakable("Nimm [die Doku](https://x.example/a) und:\n```py\nprint(1)\n```\nfertig")
	for _, want := range []string{"die Doku", "Codeblock übersprungen"} {
		if !strings.Contains(got, want) {
			t.Fatalf("%q fehlt in %q", want, got)
		}
	}
	for _, bad := range []string{"https://", "print(1)"} {
		if strings.Contains(got, bad) {
			t.Fatalf("%q steht noch in %q", bad, got)
		}
	}
}

func TestTemplateRoundtripAndMode(t *testing.T) {
	p := filepath.Join(t.TempDir(), "cfg.json")
	if err := writeConfigTemplate(p); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(p); st.Mode().Perm() != 0o600 {
		t.Fatalf("mode %o, will 600 — da steht ein Passwort drin", st.Mode().Perm())
	}
	cfg, err := loadConfig(p)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Instance != "myassistant" || cfg.Vad.StartFrames != 5 {
		t.Fatalf("Template-Defaults kaputt: %+v", cfg)
	}
}

func TestMissingCredentialsFailLoud(t *testing.T) {
	p := filepath.Join(t.TempDir(), "cfg.json")
	os.WriteFile(p, []byte(`{"base_url":"http://x"}`), 0o600)
	if _, err := loadConfig(p); err == nil {
		t.Fatal("fehlende Zugangsdaten muessen laut scheitern")
	}
}

func TestPartialVadConfigIsFilled(t *testing.T) {
	p := filepath.Join(t.TempDir(), "cfg.json")
	b, _ := json.Marshal(map[string]any{"base_url": "http://x", "user": "u",
		"pass": "p", "vad": map[string]int{"end_ms": 500}})
	os.WriteFile(p, b, 0o600)
	cfg, err := loadConfig(p)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Vad.EndMs != 500 || cfg.Vad.MinMs != 400 {
		t.Fatalf("Teil-VAD-Config nicht aufgefuellt: %+v", cfg.Vad)
	}
}
