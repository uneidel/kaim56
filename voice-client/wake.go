// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"strings"
	"unicode"
)

// Wake-word gate. No model on the desktop: STT runs on our own server for
// every utterance anyway, the gate is a text comparison afterwards.
//
// The reality behind it (measured with Piper -> Parakeet): short wake words
// get garbled by STT — "Kat" came back as "Tat", "Card" or NOT AT ALL;
// "Kati"/"Katharina"/"Computer" on the other hand flawlessly. Therefore:
//   - wake_word may be a COMMA LIST of variants ("Kati, Kat"),
//   - per variant of 4+ letters ONE typo is allowed (Levenshtein 1; not at
//     3 letters — otherwise "hat" wakes the word "Kat"),
//   - the word boundary stays mandatory: "Katalog" does not wake "Kat".

func notWordRune(r rune) bool {
	return !unicode.IsLetter(r) && !unicode.IsNumber(r)
}

// levenshtein for short words (wake word vs. the first transcript word).
func levenshtein(a, b []rune) int {
	prev := make([]int, len(b)+1)
	cur := make([]int, len(b)+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= len(a); i++ {
		cur[0] = i
		for j := 1; j <= len(b); j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			cur[j] = min(min(cur[j-1]+1, prev[j]+1), prev[j-1]+cost)
		}
		prev, cur = cur, prev
	}
	return prev[len(b)]
}

// wakeMatch checks whether the transcript starts with one of the wake-word
// variants and returns the rest (the actual message) without the word and
// without separators.
func wakeMatch(text, word string) (string, bool) {
	if strings.TrimSpace(word) == "" {
		return text, true // no wake word configured -> everything passes
	}
	t := []rune(strings.TrimLeftFunc(text, notWordRune))
	// Split off the first word of the transcript.
	end := len(t)
	for i, r := range t {
		if notWordRune(r) {
			end = i
			break
		}
	}
	first := strings.ToLower(string(t[:end]))
	for _, variant := range strings.Split(word, ",") {
		w := []rune(strings.TrimSpace(variant))
		if len(w) == 0 {
			continue
		}
		// Exact prefix with a word boundary (multi-part variants too).
		if len(t) >= len(w) && strings.EqualFold(string(t[:len(w)]), string(w)) {
			rest := t[len(w):]
			if len(rest) == 0 || notWordRune(rest[0]) {
				return strings.TrimLeftFunc(string(rest), notWordRune), true
			}
		}
		// Fuzzy only against the first word and only from 4 letters on.
		if len(w) >= 4 && first != "" &&
			levenshtein([]rune(first), []rune(strings.ToLower(string(w)))) <= 1 {
			return strings.TrimLeftFunc(string(t[end:]), notWordRune), true
		}
	}
	return "", false
}

// withPrompt puts the configured prompt in front of the spoken message —
// marked, so the agent keeps instruction and user sentence apart.
func withPrompt(prompt, text string) string {
	prompt = strings.TrimSpace(prompt)
	if prompt == "" {
		return text
	}
	return "[Voice client] " + prompt + "\n\n" + text
}
