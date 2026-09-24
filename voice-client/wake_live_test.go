// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Live test of the local wake gate with REAL speech (Piper TTS instead of a
// microphone): runs only when KAIM56_WAKE_WAVS points to a directory with
// enroll*.wav / pos*.wav / neg*.wav (16 kHz mono s16). In the normal gate
// run it is skipped — the suite stays free of network and files.
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
	// Look for the data chunk instead of guessing the header offset (ffmpeg writes LIST chunks).
	for i := 12; i+8 < len(b); {
		id := string(b[i : i+4])
		ln := int(binary.LittleEndian.Uint32(b[i+4 : i+8]))
		if id == "data" {
			return b[i+8 : min(i+8+ln, len(b))]
		}
		i += 8 + ln + (ln & 1)
	}
	t.Fatalf("no data chunk in %s", path)
	return nil
}

func TestWakeGateOnRealSpeech(t *testing.T) {
	dir := os.Getenv("KAIM56_WAKE_WAVS")
	if dir == "" {
		t.Skip("KAIM56_WAKE_WAVS not set")
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
	t.Logf("threshold: %.3f (%d takes)", model.Threshold, len(model.Templates))
	for _, f := range glob("pos*.wav") {
		pcm := wavPCM(t, f)
		score, cut, hit := model.Match(pcm)
		t.Logf("POS %s: %.3f hit=%v cut=%.2fs", filepath.Base(f), score, hit,
			float64(cut)/2/sampleRate)
		if !hit {
			t.Errorf("%s: wake word at the start not recognized (%.3f >= %.3f)",
				filepath.Base(f), score, model.Threshold)
			continue
		}
		// Store the remaining audio (after the word) for the STT cross-check.
		out := filepath.Join(dir, "cut_"+filepath.Base(f))
		if err := os.WriteFile(out, wavWrap(pcm[cut:]), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, f := range glob("neg*.wav") {
		score, _, hit := model.Match(wavPCM(t, f))
		t.Logf("NEG %s: %.3f hit=%v", filepath.Base(f), score, hit)
		if hit {
			t.Errorf("%s: woke falsely (%.3f < %.3f)",
				filepath.Base(f), score, model.Threshold)
		}
	}
}
