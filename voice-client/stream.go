// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import "strings"

// sentenceStreamer splits a token stream into speakable pieces: as soon as a
// sentence end is present, the sentence is emitted — so TTS starts at the
// first sentence while the model is still writing. Thinking blocks and code
// fences are emitted only once they are closed (speakable() then drops or
// replaces them); an open block only holds back, never forever — Close()
// flushes the rest.
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
			// Open thinking/code blocks: hold back until they are closed.
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
			for i := 0; i < len(r)-1; i++ { // last rune: the sentence may be unfinished
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
