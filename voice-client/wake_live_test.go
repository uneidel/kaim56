// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Live-Test des lokalen Wake-Gates mit ECHTER Sprache (Piper-TTS statt
// Mikrofon): laeuft nur, wenn KAIM56_WAKE_WAVS auf ein Verzeichnis mit
// enroll*.wav / pos*.wav / neg*.wav zeigt (16 kHz mono s16). Im normalen
// Gate-Lauf wird er uebersprungen — die Suite bleibt netzwerk- und dateifrei.
package main

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

func wavPCM(t *testing.T, path string) []byte {
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// data-Chunk suchen statt Header-Offset raten (ffmpeg schreibt LIST-Chunks).
	for i := 12; i+8 < len(b); {
		id := string(b[i : i+4])
		ln := int(binary.LittleEndian.Uint32(b[i+4 : i+8]))
		if id == "data" {
			return b[i+8 : min(i+8+ln, len(b))]
		}
		i += 8 + ln + (ln & 1)
	}
	t.Fatalf("kein data-Chunk in %s", path)
	return nil
}

func TestWakeGateOnRealSpeech(t *testing.T) {
	dir := os.Getenv("KAIM56_WAKE_WAVS")
	if dir == "" {
		t.Skip("KAIM56_WAKE_WAVS nicht gesetzt")
	}
	glob := func(p string) []string {
		m, _ := filepath.Glob(filepath.Join(dir, p))
		sort.Strings(m)
		return m
	}
	var takes [][]byte
	for _, f := range glob("enroll*.wav") {
		takes = append(takes, wavPCM(t, f))
	}
	model, err := buildWakeModel(takes)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("Schwelle: %.3f (%d Takes)", model.Threshold, len(model.Templates))
	for _, f := range glob("pos*.wav") {
		pcm := wavPCM(t, f)
		score, cut, hit := model.Match(pcm)
		t.Logf("POS %s: %.3f hit=%v Schnitt=%.2fs", filepath.Base(f), score, hit,
			float64(cut)/2/sampleRate)
		if !hit {
			t.Errorf("%s: Wake-Word am Anfang nicht erkannt (%.3f >= %.3f)",
				filepath.Base(f), score, model.Threshold)
			continue
		}
		// Rest-Audio (nach dem Wort) fuer die STT-Gegenprobe ablegen.
		out := filepath.Join(dir, "cut_"+filepath.Base(f))
		if err := os.WriteFile(out, wavWrap(pcm[cut:]), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, f := range glob("neg*.wav") {
		score, _, hit := model.Match(wavPCM(t, f))
		t.Logf("NEG %s: %.3f hit=%v", filepath.Base(f), score, hit)
		if hit {
			t.Errorf("%s: falsch geweckt (%.3f < %.3f)",
				filepath.Base(f), score, model.Threshold)
		}
	}
}
