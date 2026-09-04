// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import "strings"

// sentenceStreamer zerlegt einen Token-Strom in sprechfertige Stuecke: sobald
// ein Satzende vorliegt, wird der Satz emittiert — die TTS beginnt also beim
// ersten Satz, waehrend das Modell noch schreibt. Denk-Bloecke und Codefences
// werden erst emittiert, wenn sie geschlossen sind (speakable() wirft sie dann
// weg bzw. ersetzt sie); ein offener Block haelt nur zurueck, nie fuer immer —
// Close() spuelt den Rest.
type sentenceStreamer struct {
	acc     []rune
	emitted int
	emit    func(string)
}

func newSentenceStreamer(emit func(string)) *sentenceStreamer {
	return &sentenceStreamer{emit: emit}
}

func (s *sentenceStreamer) Feed(tok string) {
	s.acc = append(s.acc, []rune(tok)...)
	s.tryEmit(false)
}

func (s *sentenceStreamer) Close() {
	s.tryEmit(true)
}

func countOccur(hay, needle string) int {
	return strings.Count(hay, needle)
}

func (s *sentenceStreamer) tryEmit(final bool) {
	for {
		tail := string(s.acc[s.emitted:])
		if tail == "" {
			return
		}
		if !final {
			// Offene Denk-/Codebloecke: zurueckhalten, bis sie zu sind.
			if countOccur(tail, "⟦think⟧") > countOccur(tail, "⟦/think⟧") {
				return
			}
			if countOccur(tail, "```")%2 == 1 {
				return
			}
		}
		cut := -1
		if final {
			cut = len([]rune(tail))
		} else {
			r := []rune(tail)
			for i := 0; i < len(r)-1; i++ { // letztes Zeichen: Satz evtl. unfertig
				if strings.ContainsRune(".!?…:", r[i]) &&
					(r[i+1] == ' ' || r[i+1] == '\n') {
					cut = i + 1
				}
			}
		}
		if cut < 0 {
			return
		}
		chunk := string([]rune(tail)[:cut])
		s.emitted += cut
		if say := speakable(chunk); say != "" {
			s.emit(say)
		}
		if final {
			return
		}
	}
}
