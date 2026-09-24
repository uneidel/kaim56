// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

// Local wake-word model — audio leaves the desktop only AFTER the word.
//
// No pre-trained network (openWakeWord does not know "Kaim", Porcupine is
// commercial), but classic query-by-example keyword spotting: during
// enrollment the user says the word a few times, MFCC templates are stored.
// At runtime the START of every VAD utterance is held against the templates
// with open-end DTW; only a hit sends the audio to STT. Speaker-dependent —
// exactly right in a conference call: other voices match poorly.
//
// Pipeline: 16 kHz s16 -> frames 25 ms / hop 10 ms -> Hamming -> FFT 512 ->
// 26 mel filters (100..7600 Hz) -> log -> DCT-II -> 13 coefficients, channel
// mean from the enrollment subtracted. Takes are trimmed to the voiced core;
// subsequence DTW (start AND end free on the utterance axis), normalized by
// the template length. All stdlib.

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
)

const (
	mfccFrameLen = 400 // 25 ms @ 16 kHz
	mfccHop      = 160 // 10 ms
	mfccFFT      = 512
	mfccMels     = 26
	mfccCoeffs   = 13
	melLoHz      = 100.0
	melHiHz      = 7600.0
)

// ---- FFT (radix-2, iterative) -----------------------------------------------
func fft(re, im []float64) {
	n := len(re)
	for i, j := 1, 0; i < n; i++ { // bit reversal
		bit := n >> 1
		for ; j&bit != 0; bit >>= 1 {
			j ^= bit
		}
		j |= bit
		if i < j {
			re[i], re[j] = re[j], re[i]
			im[i], im[j] = im[j], im[i]
		}
	}
	for ln := 2; ln <= n; ln <<= 1 {
		ang := -2 * math.Pi / float64(ln)
		wr, wi := math.Cos(ang), math.Sin(ang)
		for i := 0; i < n; i += ln {
			cr, ci := 1.0, 0.0
			for j := 0; j < ln/2; j++ {
				ur, ui := re[i+j], im[i+j]
				vr := re[i+j+ln/2]*cr - im[i+j+ln/2]*ci
				vi := re[i+j+ln/2]*ci + im[i+j+ln/2]*cr
				re[i+j], im[i+j] = ur+vr, ui+vi
				re[i+j+ln/2], im[i+j+ln/2] = ur-vr, ui-vi
				cr, ci = cr*wr-ci*wi, cr*wi+ci*wr
			}
		}
	}
}

// ---- Mel filter bank (built once) -------------------------------------------
func hzToMel(hz float64) float64 { return 2595 * math.Log10(1+hz/700) }
func melToHz(m float64) float64  { return 700 * (math.Pow(10, m/2595) - 1) }

var melBank [][]float64 // [mel][bin]

func init() {
	bins := mfccFFT/2 + 1
	pts := make([]float64, mfccMels+2)
	lo, hi := hzToMel(melLoHz), hzToMel(melHiHz)
	for i := range pts {
		hz := melToHz(lo + (hi-lo)*float64(i)/float64(mfccMels+1))
		pts[i] = hz * mfccFFT / sampleRate
	}
	melBank = make([][]float64, mfccMels)
	for m := 0; m < mfccMels; m++ {
		f := make([]float64, bins)
		l, c, r := pts[m], pts[m+1], pts[m+2]
		for b := 0; b < bins; b++ {
			x := float64(b)
			if x > l && x < c {
				f[b] = (x - l) / (c - l)
			} else if x >= c && x < r {
				f[b] = (r - x) / (r - c)
			}
		}
		melBank[m] = f
	}
}

// mfccFrames computes the MFCC sequence of an s16le PCM chunk.
func mfccFrames(pcm []byte) [][]float64 {
	n := len(pcm) / 2
	samples := make([]float64, n)
	for i := 0; i < n; i++ {
		samples[i] = float64(int16(binary.LittleEndian.Uint16(pcm[i*2:]))) / 32768.0
	}
	var out [][]float64
	re := make([]float64, mfccFFT)
	im := make([]float64, mfccFFT)
	for off := 0; off+mfccFrameLen <= n; off += mfccHop {
		for i := 0; i < mfccFFT; i++ {
			if i < mfccFrameLen { // Hamming
				w := 0.54 - 0.46*math.Cos(2*math.Pi*float64(i)/float64(mfccFrameLen-1))
				re[i] = samples[off+i] * w
			} else {
				re[i] = 0
			}
			im[i] = 0
		}
		fft(re, im)
		bins := mfccFFT/2 + 1
		power := make([]float64, bins)
		for b := 0; b < bins; b++ {
			power[b] = re[b]*re[b] + im[b]*im[b]
		}
		coeffs := make([]float64, mfccCoeffs)
		var mel [mfccMels]float64
		for m := 0; m < mfccMels; m++ {
			s := 0.0
			for b, w := range melBank[m] {
				if w != 0 {
					s += w * power[b]
				}
			}
			mel[m] = math.Log(s + 1e-10)
		}
		for c := 0; c < mfccCoeffs; c++ { // DCT-II
			s := 0.0
			for m := 0; m < mfccMels; m++ {
				s += mel[m] * math.Cos(math.Pi*float64(c)*(float64(m)+0.5)/mfccMels)
			}
			coeffs[c] = s
		}
		out = append(out, coeffs)
	}
	// NO per-sequence CMN: the template (only the word) and the utterance
	// (word + sentence after it) would be normalized over different content
	// and shift against each other. The model does the channel normalization
	// instead: one mean from the enrollment, applied to BOTH sides (see
	// buildWakeModel/Match).
	return out
}

func subMean(seq [][]float64, mean []float64) {
	if len(mean) != mfccCoeffs {
		return
	}
	for _, f := range seq {
		for c := 0; c < mfccCoeffs; c++ {
			f[c] -= mean[c]
		}
	}
}

// dtwSubseq: distance of the complete template against the BEST stretch of
// the utterance — start and end on the utterance axis are free (silence or a
// breath before the word costs nothing). Normalized by the template length.
// Also returns the end frame of the alignment: that is where the word ends
// in the utterance, after it the actual message begins.
func dtwSubseq(tpl, utt [][]float64) (float64, int) {
	n, m := len(tpl), len(utt)
	if n == 0 || m == 0 {
		return math.Inf(1), 0
	}
	const big = math.MaxFloat64 / 4
	prev := make([]float64, m+1) // free start: row 0 costs nothing
	cur := make([]float64, m+1)
	for i := 1; i <= n; i++ {
		cur[0] = big
		for j := 1; j <= m; j++ {
			d := 0.0
			// c0 (energy) stays out: spoken in isolation vs. inside a sentence
			// differs mainly in the loudness contour, not in the timbre — and
			// only the timbre should decide.
			for c := 1; c < mfccCoeffs; c++ {
				diff := tpl[i-1][c] - utt[j-1][c]
				d += diff * diff
			}
			d = math.Sqrt(d)
			best := prev[j]
			if prev[j-1] < best {
				best = prev[j-1]
			}
			if cur[j-1] < best {
				best = cur[j-1]
			}
			cur[j] = d + best
		}
		prev, cur = cur, prev
	}
	best, bestEnd := math.Inf(1), 0
	for j := 1; j <= m; j++ {
		if prev[j] < best {
			best, bestEnd = prev[j], j
		}
	}
	return best / float64(n), bestEnd
}

// trimSilence cuts silence off both ends of a take (Piper and the VAD both
// deliver lead-in/lead-out): kept is everything from the first to the last
// 30 ms frame above a tenth of the peak RMS, plus two frames of margin.
func trimSilence(pcm []byte) []byte {
	nf := len(pcm) / frameBytes
	if nf == 0 {
		return pcm
	}
	rms := make([]int, nf)
	peak := 0
	for i := 0; i < nf; i++ {
		rms[i] = frameRMS(pcm[i*frameBytes : (i+1)*frameBytes])
		if rms[i] > peak {
			peak = rms[i]
		}
	}
	thr := peak / 10
	first, last := 0, nf-1
	for first < nf && rms[first] <= thr {
		first++
	}
	for last > first && rms[last] <= thr {
		last--
	}
	first = max(0, first-2)
	last = min(nf-1, last+2)
	return pcm[first*frameBytes : (last+1)*frameBytes]
}

// ---- Templates --------------------------------------------------------------
type WakeModel struct {
	Threshold float64       `json:"threshold"`
	Mean      []float64     `json:"mean"` // channel mean from the enrollment
	Templates [][][]float64 `json:"templates"`
}

func wakeModelPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "kaim56-voice-wake.json")
}

func loadWakeModel(path string) (*WakeModel, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var m WakeModel
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	if len(m.Templates) == 0 || m.Threshold <= 0 {
		return nil, fmt.Errorf("wake model %s is empty — please record it again (--enroll)", path)
	}
	return &m, nil
}

// buildWakeModel turns recorded takes into a model. The threshold comes from
// the cross distances of the takes among each other: as strict as the own
// voice is repeatable, plus headroom.
func buildWakeModel(takes [][]byte) (*WakeModel, error) {
	if len(takes) < 2 {
		return nil, fmt.Errorf("at least 2 takes are needed")
	}
	m := &WakeModel{}
	var seqs [][][]float64
	for _, t := range takes {
		s := mfccFrames(trimSilence(t))
		if len(s) < 20 { // < 200 ms of speech is not a word
			return nil, fmt.Errorf("one take is too short (%d frames)", len(s))
		}
		seqs = append(seqs, s)
	}
	// Channel mean over all enrollment frames — the utterance gets the same
	// shift at runtime.
	m.Mean = make([]float64, mfccCoeffs)
	total := 0
	for _, s := range seqs {
		for _, f := range s {
			for c := 0; c < mfccCoeffs; c++ {
				m.Mean[c] += f[c]
			}
		}
		total += len(s)
	}
	for c := range m.Mean {
		m.Mean[c] /= float64(total)
	}
	for _, s := range seqs {
		subMean(s, m.Mean)
	}
	worst := 0.0
	for i := range seqs {
		for j := range seqs {
			if i == j {
				continue
			}
			if d, _ := dtwSubseq(seqs[i], seqs[j]); d > worst {
				worst = d
			}
		}
	}
	m.Templates = seqs
	m.Threshold = worst*1.35 + 0.05
	return m, nil
}

func (m *WakeModel) save(path string) error {
	b, _ := json.Marshal(m)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o600)
}

// Match holds the start of an utterance against all templates.
// Returns (best score, byte offset after the word, hit).
// Score < Threshold = wake; the actual message begins at the offset.
func (m *WakeModel) Match(pcm []byte) (float64, int, bool) {
	// Compute only the head: the longest template times 2 is enough for a
	// word at the start of a sentence, and DTW stays cheap.
	maxTpl := 0
	for _, t := range m.Templates {
		if len(t) > maxTpl {
			maxTpl = len(t)
		}
	}
	headFrames := maxTpl*2 + 50 // headroom for lead-in silence and a hesitant start
	headBytes := (headFrames*mfccHop + mfccFrameLen) * 2
	if headBytes < len(pcm) {
		pcm = pcm[:headBytes]
	}
	utt := mfccFrames(pcm)
	subMean(utt, m.Mean)
	best, bestEnd := math.Inf(1), 0
	for _, t := range m.Templates {
		if d, end := dtwSubseq(t, utt); d < best {
			best, bestEnd = d, end
		}
	}
	cut := bestEnd * mfccHop * 2
	if cut > len(pcm) {
		cut = len(pcm)
	}
	return best, cut, best < m.Threshold
}
