// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Unit tests for the voice client — everything testable without a
// microphone and a manager: VAD segmentation on synthetic PCM, the WAV
// header, the read-aloud filter, config handling.
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
		t.Fatalf("segs = %d, want 1", len(segs))
	}
	if min := 900 * sampleRate * 2 / 1000; len(segs[0]) < min {
		t.Fatalf("segment %dB < speech duration %dB", len(segs[0]), min)
	}
}

func TestShortBurstIsDropped(t *testing.T) {
	v := testVad(600, 400, 0)
	segs := feedAll(v, append(append(silence(1500), tone(200)...), silence(1200)...)) // a door slamming
	if len(segs) != 0 {
		t.Fatalf("segs = %d, want 0", len(segs))
	}
}

func TestSilenceAloneNeverTriggers(t *testing.T) {
	v := NewVad(defaultVadConfig())
	if segs := feedAll(v, silence(4000)); len(segs) != 0 || v.talk != nil {
		t.Fatalf("silence triggers: segs=%d talk=%v", len(segs), v.talk != nil)
	}
}

func TestTwoUtterancesTwoSegments(t *testing.T) {
	v := testVad(600, 300, 0)
	pcm := append(append(append(append(silence(1200), tone(800)...),
		silence(1200)...), tone(800)...), silence(1200)...)
	if segs := feedAll(v, pcm); len(segs) != 2 {
		t.Fatalf("segs = %d, want 2", len(segs))
	}
}

func TestSpeechCannotRaiseThresholdAboveItself(t *testing.T) {
	// The floor must not learn from speech: after a long sentence the same
	// level must still clearly count as voiced.
	v := testVad(600, 300, 0)
	feedAll(v, append(silence(900), tone(3000)...))
	if limit := float64(frameRMS(tone(30))) * 0.5; v.Threshold() >= limit {
		t.Fatalf("threshold %.0f >= %.0f — the floor learned from speech", v.Threshold(), limit)
	}
}

func TestMaxLengthChunksContinuousSpeech(t *testing.T) {
	// Continuous speech is split into max-length pieces, none above it.
	v := testVad(600, 300, 2)
	segs := feedAll(v, append(silence(900), tone(4000)...))
	if len(segs) != 2 {
		t.Fatalf("segs = %d, want 2", len(segs))
	}
	for _, s := range segs {
		if maxB := (2000/frameMs + 1) * frameBytes; len(s) > maxB {
			t.Fatalf("segment %dB > max %dB", len(s), maxB)
		}
	}
}

func TestWavHeaderFields(t *testing.T) {
	pcm := tone(100)
	wav := wavWrap(pcm)
	if string(wav[:4]) != "RIFF" || string(wav[8:12]) != "WAVE" {
		t.Fatal("no RIFF/WAVE header")
	}
	if r := binary.LittleEndian.Uint32(wav[24:]); r != sampleRate {
		t.Fatalf("rate %d", r)
	}
	if n := binary.LittleEndian.Uint32(wav[40:]); int(n) != len(pcm) {
		t.Fatalf("data length %d != %d", n, len(pcm))
	}
	if string(wav[44:]) != string(pcm) {
		t.Fatal("PCM altered")
	}
}

func TestThinkBlocksAreStripped(t *testing.T) {
	if got := speakable("⟦think⟧inner mumbling⟦/think⟧Hello **Ulrich**!"); got != "Hello Ulrich!" {
		t.Fatalf("got %q", got)
	}
}

func TestOpenThinkBlockIsStripped(t *testing.T) {
	if got := speakable("Answer.⟦think⟧still open"); got != "Answer." {
		t.Fatalf("got %q", got)
	}
}

func TestToolStatusLinesAreDropped(t *testing.T) {
	if got := speakable("🔧 listagents …\n5 instances are running."); got != "5 instances are running." {
		t.Fatalf("got %q", got)
	}
}

func TestCodeAndLinks(t *testing.T) {
	got := speakable("Take [the docs](https://x.example/a) and:\n```py\nprint(1)\n```\ndone")
	for _, want := range []string{"the docs", "Code block skipped"} {
		if !strings.Contains(got, want) {
			t.Fatalf("%q missing in %q", want, got)
		}
	}
	for _, bad := range []string{"https://", "print(1)"} {
		if strings.Contains(got, bad) {
			t.Fatalf("%q still in %q", bad, got)
		}
	}
}

func TestWakeWordGate(t *testing.T) {
	cases := []struct {
		text, word, rest string
		ok               bool
	}{
		{"Kat, what time is it", "Kat", "what time is it", true},
		{"kat what time is it", "Kat", "what time is it", true},
		{"...Kat: turn the light on", "Kat", "turn the light on", true}, // STT punctuation
		{"Kat?", "Kat", "", true},                                       // the word alone -> "Yes?"
		{"Katalog open", "Kat", "", false},                              // word boundary
		{"what time is it", "Kat", "", false},                           // conference-call mumbling
		{"Could someone take that over please", "Kat", "", false},
		{"Kat, 5 times 3?", "Kat", "5 times 3?", true},
		{"what time is it", "", "what time is it", true}, // no wake word -> everything passes
		// variant list + fuzzy (measured STT garblings):
		{"Kati, what time is it?", "Kati, Kat", "what time is it?", true},
		{"Katie, what time is it?", "Kati", "what time is it?", true}, // Levenshtein 1
		{"Keim summarizes the document.", "Kati, Keim", "summarizes the document.", true},
		{"hat anyone got questions", "Kat", "", false},         // 3 letters: NO fuzzy ("hat"!)
		{"hat anyone got questions", "Kati", "", false},        // distance 2 -> no hit
		{"Tat, summarize that for me", "Kati, Kat", "", false}, // honest: Kat stays exact
	}
	for _, c := range cases {
		rest, ok := wakeMatch(c.text, c.word)
		if ok != c.ok || rest != c.rest {
			t.Errorf("wakeMatch(%q, %q) = (%q, %v), want (%q, %v)",
				c.text, c.word, rest, ok, c.rest, c.ok)
		}
	}
}

func TestSentenceStreamer(t *testing.T) {
	var got []string
	ss := newSentenceStreamer(func(s string) { got = append(got, s) })
	for _, tok := range []string{"Hel", "lo. How are", " you? And", " now"} {
		ss.Feed(tok)
	}
	ss.Close()
	want := []string{"Hello.", "How are you?", "And now"}
	if len(got) != 3 || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatalf("chunks = %q, want %q", got, want)
	}
}

func TestSentenceStreamerHoldsThinkAndFences(t *testing.T) {
	var got []string
	ss := newSentenceStreamer(func(s string) { got = append(got, s) })
	ss.Feed("⟦think⟧First. Thinking. ")
	if len(got) != 0 {
		t.Fatalf("an open thinking block must not emit: %q", got)
	}
	ss.Feed("⟦/think⟧Sure. ")
	if len(got) != 1 || got[0] != "Sure." {
		t.Fatalf("got %q", got)
	}
	ss.Feed("```py\nprint(1)\n")
	ss.Feed("``` Done. ")
	ss.Close()
	all := strings.Join(got, " | ")
	if !strings.Contains(all, "Code block skipped") || !strings.Contains(all, "Done.") {
		t.Fatalf("fence handling broken: %q", got)
	}
	if strings.Contains(all, "print(1)") || strings.Contains(all, "Thinking") {
		t.Fatalf("content should have been dropped: %q", got)
	}
}

func TestWithPrompt(t *testing.T) {
	if got := withPrompt("", "What time is it?"); got != "What time is it?" {
		t.Fatalf("an empty prompt must not change anything: %q", got)
	}
	got := withPrompt("Answer briefly.", "What time is it?")
	if got != "[Voice client] Answer briefly.\n\nWhat time is it?" {
		t.Fatalf("got %q", got)
	}
}

// A "pseudo word" for wake tests: a characteristic tone sequence. Different
// sequences differ far more in MFCC than repetitions of the same sequence —
// exactly the property the gate relies on.
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
		t.Fatalf("too few frames: %d", len(a))
	}
	if len(a[0]) != mfccCoeffs {
		t.Fatalf("coefficients: %d", len(a[0]))
	}
	// Two tones must yield distinguishable frames.
	same, _ := dtwSubseq(mfccFrames(toneWord([]float64{440}, 200)),
		mfccFrames(toneWord([]float64{440}, 200)))
	diff, _ := dtwSubseq(mfccFrames(toneWord([]float64{440}, 200)),
		mfccFrames(toneWord([]float64{2600}, 200)))
	if same > diff/2 {
		t.Fatal("MFCC does not separate frequencies")
	}
}

func TestDtwSeparatesWords(t *testing.T) {
	wordA := func() [][]float64 { return mfccFrames(toneWord([]float64{300, 1200, 500}, 150)) }
	wordB := mfccFrames(toneWord([]float64{2000, 600, 3000}, 150))
	same, _ := dtwSubseq(wordA(), wordA())
	diff, _ := dtwSubseq(wordA(), wordB)
	if same >= diff/3 {
		t.Fatalf("DTW does not separate: same=%f different=%f", same, diff)
	}
}

func TestWakeModelGate(t *testing.T) {
	// Three "takes" of the same pseudo word, slightly compressed/stretched.
	takes := [][]byte{
		toneWord([]float64{300, 1200, 500}, 150),
		toneWord([]float64{300, 1200, 500}, 165),
		toneWord([]float64{300, 1200, 500}, 140),
	}
	m, err := buildWakeModel(takes)
	if err != nil {
		t.Fatal(err)
	}
	// An utterance that starts with the word (plus a "sentence" after it) -> wake.
	utt := append(toneWord([]float64{300, 1200, 500}, 155),
		toneWord([]float64{700, 900, 400, 1100}, 120)...)
	score, cut, hit := m.Match(utt)
	if !hit {
		t.Fatalf("word at the start not recognized (score %.3f, threshold %.3f)", score, m.Threshold)
	}
	// The cut must sit roughly at the end of the word (450 ms +- 150 ms).
	if sec := float64(cut) / 2 / sampleRate; sec < 0.3 || sec > 0.6 {
		t.Fatalf("cut at %.2f s, expected ~0.45 s", sec)
	}
	// A foreign utterance -> no wake.
	other := toneWord([]float64{2000, 600, 3000, 800}, 150)
	if score, _, hit := m.Match(other); hit {
		t.Fatalf("a foreign word woke (score %.3f, threshold %.3f)", score, m.Threshold)
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
		t.Fatalf("round trip broken: %v", err)
	}
	if _, err := buildWakeModel([][]byte{toneWord([]float64{300}, 30)}); err == nil {
		t.Fatal("too short/too few takes must fail")
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
		t.Fatal("not executable")
	}
	p2, err := materializeTunnel(data, dir) // second run: cache hit
	if err != nil || p2 != p1 {
		t.Fatalf("cache hit expected: %v %v", p2, err)
	}
	p3, _ := materializeTunnel([]byte("another version"), dir)
	if p3 == p1 {
		t.Fatal("a new version must get a new path")
	}
}

func TestTemplateIsWrittenButRefusedUnedited(t *testing.T) {
	// The placeholder must NOT be runnable: starting with "manager.example"
	// shows the error only at the first sentence (a DNS error instead of
	// "fill in the config").
	p := filepath.Join(t.TempDir(), "cfg.json")
	if err := writeConfigTemplate(p); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(p); st.Mode().Perm() != 0o600 {
		t.Fatalf("mode %o, want 600 — it contains a password", st.Mode().Perm())
	}
	if _, err := loadConfig(p); err == nil || !strings.Contains(err.Error(), "iroh") {
		t.Fatalf("an unedited template must fail with a hint, err=%v", err)
	}
}

func TestIrohOnlyConfigIsValid(t *testing.T) {
	// The iroh route needs neither base_url nor user/pass (no Traefik involved).
	p := filepath.Join(t.TempDir(), "cfg.json")
	os.WriteFile(p, []byte(`{"iroh":"9954327fe3926b1e430a3b75e21a860666dab6de"}`), 0o600)
	cfg, err := loadConfig(p)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.IrohListen != "127.0.0.1:8701" || cfg.Instance != "myassistant" ||
		cfg.Vad.StartFrames != 5 {
		t.Fatalf("iroh defaults broken: %+v", cfg)
	}
}

func TestMissingCredentialsFailLoud(t *testing.T) {
	p := filepath.Join(t.TempDir(), "cfg.json")
	os.WriteFile(p, []byte(`{"base_url":"http://x"}`), 0o600)
	if _, err := loadConfig(p); err == nil {
		t.Fatal("missing credentials must fail loudly")
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
		t.Fatalf("partial VAD config not filled in: %+v", cfg.Vad)
	}
	if cfg.WakeWord != "" {
		t.Fatalf("an existing config without wake_word must keep the old behaviour, has %q", cfg.WakeWord)
	}
}
