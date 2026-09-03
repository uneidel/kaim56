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

func TestWakeWordGate(t *testing.T) {
	cases := []struct {
		text, word, rest string
		ok               bool
	}{
		{"Kat, wie spät ist es", "Kat", "wie spät ist es", true},
		{"kat wie spät ist es", "Kat", "wie spät ist es", true},
		{"...Kat: mach das Licht an", "Kat", "mach das Licht an", true}, // STT-Interpunktion
		{"Kat?", "Kat", "", true},                                      // Wort allein -> "Ja?"
		{"Katalog öffnen", "Kat", "", false},                           // Wortgrenze
		{"wie spät ist es", "Kat", "", false},                          // Telko-Gemurmel
		{"Übernimm das mal bitte jemand", "Kat", "", false},
		{"Kat, 5 mal 3?", "Kat", "5 mal 3?", true},
		{"wie spät ist es", "", "wie spät ist es", true}, // kein Wake-Word -> alles durch
		// Varianten-Liste + Fuzzy (gemessene STT-Verstuemmelungen):
		{"Kati, wie spät ist es?", "Kati, Kat", "wie spät ist es?", true},
		{"Katie, wie spät ist es?", "Kati", "wie spät ist es?", true},  // Levenshtein 1
		{"Keim fasst das Dokument zusammen.", "Kati, Keim", "fasst das Dokument zusammen.", true},
		{"hat jemand noch Fragen", "Kat", "", false},  // 3 Buchstaben: KEIN Fuzzy ("hat"!)
		{"hat jemand noch Fragen", "Kati", "", false}, // Distanz 2 -> kein Treffer
		{"Tat, fass mir das zusammen", "Kati, Kat", "", false}, // ehrlich: Kat bleibt exakt
	}
	for _, c := range cases {
		rest, ok := wakeMatch(c.text, c.word)
		if ok != c.ok || rest != c.rest {
			t.Errorf("wakeMatch(%q, %q) = (%q, %v), will (%q, %v)",
				c.text, c.word, rest, ok, c.rest, c.ok)
		}
	}
}

// Ein "Pseudo-Wort" fuer Wake-Tests: eine charakteristische Tonfolge.
// Verschiedene Folgen unterscheiden sich in MFCC deutlich staerker als
// Wiederholungen derselben Folge — genau die Eigenschaft, die das Gate traegt.
func toneWord(freqs []float64, msEach int) []byte {
	var out []byte
	n := sampleRate * msEach / 1000
	for _, f := range freqs {
		for i := 0; i < n; i++ {
			v := int16(8000 * math.Sin(2*math.Pi*f*float64(i)/sampleRate))
			b := make([]byte, 2)
			binary.LittleEndian.PutUint16(b, uint16(v))
			out = append(out, b...)
		}
	}
	return out
}

func TestMfccBasics(t *testing.T) {
	a := mfccFrames(toneWord([]float64{440, 880}, 200))
	if len(a) < 30 {
		t.Fatalf("zu wenige Frames: %d", len(a))
	}
	if len(a[0]) != mfccCoeffs {
		t.Fatalf("Koeffizienten: %d", len(a[0]))
	}
	// Zwei Toene muessen unterscheidbare Frames liefern.
	same, _ := dtwSubseq(mfccFrames(toneWord([]float64{440}, 200)),
		mfccFrames(toneWord([]float64{440}, 200)))
	diff, _ := dtwSubseq(mfccFrames(toneWord([]float64{440}, 200)),
		mfccFrames(toneWord([]float64{2600}, 200)))
	if same > diff/2 {
		t.Fatal("MFCC trennt Frequenzen nicht")
	}
}

func TestDtwSeparatesWords(t *testing.T) {
	wordA := func() [][]float64 { return mfccFrames(toneWord([]float64{300, 1200, 500}, 150)) }
	wordB := mfccFrames(toneWord([]float64{2000, 600, 3000}, 150))
	same, _ := dtwSubseq(wordA(), wordA())
	diff, _ := dtwSubseq(wordA(), wordB)
	if same >= diff/3 {
		t.Fatalf("DTW trennt nicht: gleich=%f verschieden=%f", same, diff)
	}
}

func TestWakeModelGate(t *testing.T) {
	// Drei "Takes" desselben Pseudo-Worts, leicht gestaucht/gedehnt.
	takes := [][]byte{
		toneWord([]float64{300, 1200, 500}, 150),
		toneWord([]float64{300, 1200, 500}, 165),
		toneWord([]float64{300, 1200, 500}, 140),
	}
	m, err := buildWakeModel(takes)
	if err != nil {
		t.Fatal(err)
	}
	// Aeusserung, die mit dem Wort beginnt (plus "Satz" dahinter) -> Wake.
	utt := append(toneWord([]float64{300, 1200, 500}, 155),
		toneWord([]float64{700, 900, 400, 1100}, 120)...)
	score, cut, hit := m.Match(utt)
	if !hit {
		t.Fatalf("Wort am Anfang nicht erkannt (Score %.3f, Schwelle %.3f)", score, m.Threshold)
	}
	// Der Schnitt muss ungefaehr am Wortende liegen (450 ms +- 150 ms).
	if sec := float64(cut) / 2 / sampleRate; sec < 0.3 || sec > 0.6 {
		t.Fatalf("Schnitt bei %.2f s, erwartet ~0.45 s", sec)
	}
	// Fremde Aeusserung -> kein Wake.
	other := toneWord([]float64{2000, 600, 3000, 800}, 150)
	if score, _, hit := m.Match(other); hit {
		t.Fatalf("Fremdes Wort weckte (Score %.3f, Schwelle %.3f)", score, m.Threshold)
	}
}

func TestWakeModelRoundtripAndValidation(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "wake.json")
	m, err := buildWakeModel([][]byte{
		toneWord([]float64{300, 1200}, 150), toneWord([]float64{300, 1200}, 160)})
	if err != nil {
		t.Fatal(err)
	}
	if err := m.save(p); err != nil {
		t.Fatal(err)
	}
	m2, err := loadWakeModel(p)
	if err != nil || len(m2.Templates) != 2 || m2.Threshold != m.Threshold {
		t.Fatalf("Roundtrip kaputt: %v", err)
	}
	if _, err := buildWakeModel([][]byte{toneWord([]float64{300}, 30)}); err == nil {
		t.Fatal("zu kurze/wenige Takes muessen scheitern")
	}
}

func TestMaterializeTunnel(t *testing.T) {
	dir := t.TempDir()
	data := []byte("#!/bin/sh\necho fake-tunnel\n")
	p1, err := materializeTunnel(data, dir)
	if err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(p1); st.Mode().Perm()&0o111 == 0 {
		t.Fatal("nicht ausfuehrbar")
	}
	p2, err := materializeTunnel(data, dir) // zweiter Lauf: Cache-Treffer
	if err != nil || p2 != p1 {
		t.Fatalf("Cache-Treffer erwartet: %v %v", p2, err)
	}
	p3, _ := materializeTunnel([]byte("andere version"), dir)
	if p3 == p1 {
		t.Fatal("neue Version muss neuen Pfad bekommen")
	}
}

func TestTemplateIsWrittenButRefusedUnedited(t *testing.T) {
	// Der Platzhalter darf NICHT lauffaehig sein: mit "manager.example"
	// loszulaufen zeigt den Fehler erst beim ersten Satz (DNS-Fehler statt
	// "Config ausfuellen").
	p := filepath.Join(t.TempDir(), "cfg.json")
	if err := writeConfigTemplate(p); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(p); st.Mode().Perm() != 0o600 {
		t.Fatalf("mode %o, will 600 — da steht ein Passwort drin", st.Mode().Perm())
	}
	if _, err := loadConfig(p); err == nil || !strings.Contains(err.Error(), "iroh") {
		t.Fatalf("unbearbeitetes Template muss mit Hinweis scheitern, err=%v", err)
	}
}

func TestIrohOnlyConfigIsValid(t *testing.T) {
	// Der iroh-Weg braucht weder base_url noch user/pass (kein Traefik im Spiel).
	p := filepath.Join(t.TempDir(), "cfg.json")
	os.WriteFile(p, []byte(`{"iroh":"9954327fe3926b1e430a3b75e21a860666dab6de"}`), 0o600)
	cfg, err := loadConfig(p)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.IrohListen != "127.0.0.1:8701" || cfg.Instance != "myassistant" ||
		cfg.Vad.StartFrames != 5 {
		t.Fatalf("iroh-Defaults kaputt: %+v", cfg)
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
	if cfg.WakeWord != "" {
		t.Fatalf("Bestands-Config ohne wake_word muss beim Alt-Verhalten bleiben, hat %q", cfg.WakeWord)
	}
}
