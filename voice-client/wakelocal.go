// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

// Lokales Wake-Word-Modell — Audio verlaesst den Desktop erst NACH dem Wort.
//
// Kein vortrainiertes Netz (openWakeWord kennt kein "Kaim", Porcupine ist
// kommerziell), sondern klassisches Query-by-Example-Keyword-Spotting:
// beim Enrollment spricht der Nutzer das Wort ein paar Mal ein, gespeichert
// werden MFCC-Templates. Zur Laufzeit wird der ANFANG jeder VAD-Aeusserung
// per Open-End-DTW gegen die Templates gehalten; erst ein Treffer schickt
// das Audio zu STT. Sprecherabhaengig — in der Telefonkonferenz genau
// richtig: fremde Stimmen matchen schlecht.
//
// Pipeline: 16 kHz s16 -> Frames 25 ms / Hop 10 ms -> Hamming -> FFT 512 ->
// 26 Mel-Filter (100..7600 Hz) -> log -> DCT-II -> 13 Koeffizienten, Kanal-
// Mittel aus dem Enrollment abgezogen. Takes werden auf den stimmhaften Kern
// getrimmt; Subsequenz-DTW (Start UND Ende auf der Aeusserungsachse frei),
// normalisiert auf die Templatelaenge. Alles stdlib.

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

// ---- FFT (radix-2, iterativ) ------------------------------------------------
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

// ---- Mel-Filterbank (einmalig gebaut) ---------------------------------------
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

// mfccFrames rechnet die MFCC-Sequenz eines s16le-PCM-Stuecks, CMN-normiert.
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
	// KEINE per-Sequenz-CMN: Template (nur das Wort) und Aeusserung (Wort +
	// Satz dahinter) wuerden ueber verschiedenen Inhalt normalisiert und
	// verschoeben sich gegeneinander. Kanal-Normalisierung macht stattdessen
	// das Modell: ein Mittelwert aus dem Enrollment, auf BEIDE Seiten
	// angewandt (siehe buildWakeModel/Match).
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

// dtwSubseq: Distanz des kompletten Templates gegen die BESTE Teilstrecke der
// Aeusserung — Start und Ende auf der Aeusserungsachse sind frei (Stille oder
// ein Atmer vor dem Wort kosten nichts). Normalisiert auf die Templatelaenge.
// Liefert zusaetzlich den End-Frame des Alignments: dort endet das Wort in
// der Aeusserung, dahinter beginnt die eigentliche Nachricht.
func dtwSubseq(tpl, utt [][]float64) (float64, int) {
	n, m := len(tpl), len(utt)
	if n == 0 || m == 0 {
		return math.Inf(1), 0
	}
	const big = math.MaxFloat64 / 4
	prev := make([]float64, m+1) // freier Start: Zeile 0 kostet nichts
	cur := make([]float64, m+1)
	for i := 1; i <= n; i++ {
		cur[0] = big
		for j := 1; j <= m; j++ {
			d := 0.0
			// c0 (Energie) bleibt draussen: isoliert gesprochen vs. im Satz
			// unterscheidet sich vor allem die Lautstaerke-Kontur, nicht der
			// Klang — und nur der Klang soll entscheiden.
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

// trimSilence schneidet Stille an beiden Enden eines Takes ab (Piper wie
// VAD liefern Vor-/Nachlauf): behalten wird vom ersten bis zum letzten
// 30-ms-Frame ueber einem Zehntel des Spitzen-RMS, plus zwei Frames Rand.
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
	Mean      []float64     `json:"mean"` // Kanal-Mittel aus dem Enrollment
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
		return nil, fmt.Errorf("wake model %s ist leer — bitte neu einsprechen (--enroll)", path)
	}
	return &m, nil
}

// buildWakeModel macht aus eingesprochenen Takes ein Modell. Der Schwellwert
// kommt aus den Kreuzdistanzen der Takes untereinander: so streng, wie die
// eigene Stimme wiederholbar ist, plus Luft.
func buildWakeModel(takes [][]byte) (*WakeModel, error) {
	if len(takes) < 2 {
		return nil, fmt.Errorf("mindestens 2 Aufnahmen noetig")
	}
	m := &WakeModel{}
	var seqs [][][]float64
	for _, t := range takes {
		s := mfccFrames(trimSilence(t))
		if len(s) < 20 { // < 200 ms Sprache ist kein Wort
			return nil, fmt.Errorf("eine Aufnahme ist zu kurz (%d Frames)", len(s))
		}
		seqs = append(seqs, s)
	}
	// Kanal-Mittel ueber alle Enrollment-Frames — dieselbe Verschiebung
	// bekommt zur Laufzeit auch die Aeusserung.
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

// Match haelt den Anfang einer Aeusserung gegen alle Templates.
// Liefert (bester Score, Byte-Offset hinter dem Wort, getroffen).
// Score < Threshold = Wake; ab dem Offset beginnt die eigentliche Nachricht.
func (m *WakeModel) Match(pcm []byte) (float64, int, bool) {
	// Nur den Kopf rechnen: laengstes Template mal 2 reicht fuer ein
	// Wort am Satzanfang, und DTW bleibt billig.
	maxTpl := 0
	for _, t := range m.Templates {
		if len(t) > maxTpl {
			maxTpl = len(t)
		}
	}
	headFrames := maxTpl*2 + 50 // Luft fuer Vorlauf-Stille und zoegerlichen Start
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
