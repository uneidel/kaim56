// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"encoding/binary"
	"math"
)

const (
	sampleRate = 16000
	frameMs    = 30
	frameBytes = sampleRate * frameMs / 1000 * 2 // s16le mono
)

// frameRMS ist der RMS eines s16le-Frames.
func frameRMS(frame []byte) int {
	n := len(frame) / 2
	if n == 0 {
		return 0
	}
	var sum float64
	for i := 0; i < n*2; i += 2 {
		v := float64(int16(binary.LittleEndian.Uint16(frame[i:])))
		sum += v * v
	}
	return int(math.Sqrt(sum / float64(n)))
}

// VadConfig sind die einstellbaren Parameter (Config-Schluessel "vad").
type VadConfig struct {
	StartFrames     int     `json:"start_frames"`
	EndMs           int     `json:"end_ms"`
	MinMs           int     `json:"min_ms"`
	MaxS            int     `json:"max_s"`
	ThresholdFactor float64 `json:"threshold_factor"`
	ThresholdMin    int     `json:"threshold_min"`
}

func defaultVadConfig() VadConfig {
	return VadConfig{StartFrames: 5, EndMs: 800, MinMs: 400, MaxS: 30,
		ThresholdFactor: 3.0, ThresholdMin: 350}
}

// Vad: Energie-VAD ueber 30-ms-Frames mit adaptivem Rauschteppich.
//
// Feed(frame) liefert nil oder — am Ende einer Aeusserung — die kompletten
// PCM-Bytes inklusive Vorlauf. Zustandsautomat: IDLE (Ring von 8 Frames
// Vorlauf) -> TALK (sammeln) -> zurueck zu IDLE nach EndMs Stille. Der
// Teppich lernt NUR aus unstimmhaften Frames: sonst zieht lauteres Sprechen
// den eigenen Schwellwert in Sekunden ueber den Sprach-RMS und alles gilt
// als Stille (stimmhaft lernt nur homoeopathisch, als Ventil fuer dauerhaft
// lautere Umgebungen). Zu kurze Segmente (< MinMs) sind Tuerknallen, kein
// Satz — verworfen.
type Vad struct {
	cfg          VadConfig
	endFrames    int
	minFrames    int
	maxFrames    int
	noise        float64
	ring         [][]byte
	talk         [][]byte // nil = IDLE
	voicedRecent []bool
	silent       int
}

func NewVad(cfg VadConfig) *Vad {
	frames := func(ms int) int { return max(1, ms/frameMs) }
	return &Vad{cfg: cfg, endFrames: frames(cfg.EndMs), minFrames: frames(cfg.MinMs),
		maxFrames: frames(cfg.MaxS * 1000), noise: 200}
}

func (v *Vad) Threshold() float64 {
	return math.Max(v.noise*v.cfg.ThresholdFactor, float64(v.cfg.ThresholdMin))
}

func (v *Vad) Feed(frame []byte) []byte {
	rms := frameRMS(frame)
	voiced := float64(rms) > v.Threshold()
	if v.talk == nil {
		alpha := 0.1
		if voiced {
			alpha = 0.002
		}
		v.noise += alpha * (float64(rms) - v.noise)
		f := append([]byte(nil), frame...)
		v.ring = append(v.ring, f)
		if len(v.ring) > 8 {
			v.ring = v.ring[1:]
		}
		v.voicedRecent = append(v.voicedRecent, voiced)
		if len(v.voicedRecent) > 8 {
			v.voicedRecent = v.voicedRecent[1:]
		}
		n := 0
		for _, b := range v.voicedRecent {
			if b {
				n++
			}
		}
		if n >= v.cfg.StartFrames {
			v.talk = append([][]byte(nil), v.ring...)
			v.silent = 0
			v.voicedRecent = nil
		}
		return nil
	}
	v.talk = append(v.talk, append([]byte(nil), frame...))
	if voiced {
		v.silent = 0
	} else {
		v.silent++
	}
	if v.silent >= v.endFrames || len(v.talk) >= v.maxFrames {
		seg := v.talk
		v.talk, v.ring = nil, nil
		if len(seg)-v.silent < v.minFrames {
			return nil
		}
		out := make([]byte, 0, len(seg)*frameBytes)
		for _, f := range seg {
			out = append(out, f...)
		}
		return out
	}
	return nil
}

func (v *Vad) Reset() {
	v.talk, v.ring, v.voicedRecent, v.silent = nil, nil, nil, 0
}

// wavWrap packt s16le-mono-PCM in einen WAV-Container — /api/stt nimmt zwar
// jedes Format, aber mit Header muss ffmpeg drueben nichts raten.
func wavWrap(pcm []byte) []byte {
	out := make([]byte, 44+len(pcm))
	le := binary.LittleEndian
	copy(out[0:], "RIFF")
	le.PutUint32(out[4:], uint32(36+len(pcm)))
	copy(out[8:], "WAVE")
	copy(out[12:], "fmt ")
	le.PutUint32(out[16:], 16)
	le.PutUint16(out[20:], 1) // PCM
	le.PutUint16(out[22:], 1) // mono
	le.PutUint32(out[24:], sampleRate)
	le.PutUint32(out[28:], sampleRate*2)
	le.PutUint16(out[32:], 2)
	le.PutUint16(out[34:], 16)
	copy(out[36:], "data")
	le.PutUint32(out[40:], uint32(len(pcm)))
	copy(out[44:], pcm)
	return out
}
