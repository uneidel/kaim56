// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"strings"
	"unicode"
)

// Wake-Word-Gate. Kein Modell auf dem Desktop: STT laeuft ohnehin fuer jede
// Aeusserung auf dem eigenen Server, das Gate ist ein Textvergleich danach.
//
// Die Realitaet dahinter (gemessen mit Piper->Parakeet): kurze Wake-Woerter
// werden von STT verstuemmelt — "Kat" kam als "Tat", "Card" oder GAR NICHT
// zurueck; "Kati"/"Katharina"/"Computer" dagegen fehlerfrei. Deshalb:
//   - wake_word darf eine KOMMA-LISTE von Varianten sein ("Kati, Kat"),
//   - pro Variante ab 4 Buchstaben ist EIN Tippfehler erlaubt (Levenshtein 1;
//     bei 3 Buchstaben nicht — sonst weckt "hat" das Wort "Kat"),
//   - Wortgrenze bleibt Pflicht: "Katalog" weckt "Kat" nicht.

func notWordRune(r rune) bool {
	return !unicode.IsLetter(r) && !unicode.IsNumber(r)
}

// levenshtein fuer kurze Woerter (Wake-Word vs. erstes Transkriptwort).
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

// wakeMatch prueft, ob das Transkript mit einer der Wake-Word-Varianten
// beginnt, und liefert den Rest (die eigentliche Nachricht) ohne das Wort
// und ohne Trennzeichen.
func wakeMatch(text, word string) (string, bool) {
	if strings.TrimSpace(word) == "" {
		return text, true // kein Wake-Word konfiguriert -> alles geht durch
	}
	t := []rune(strings.TrimLeftFunc(text, notWordRune))
	// Erstes Wort des Transkripts abtrennen.
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
		// Exakter Praefix mit Wortgrenze (auch mehrteilige Varianten).
		if len(t) >= len(w) && strings.EqualFold(string(t[:len(w)]), string(w)) {
			rest := t[len(w):]
			if len(rest) == 0 || notWordRune(rest[0]) {
				return strings.TrimLeftFunc(string(rest), notWordRune), true
			}
		}
		// Fuzzy nur gegen das erste Wort und nur ab 4 Buchstaben.
		if len(w) >= 4 && first != "" &&
			levenshtein([]rune(first), []rune(strings.ToLower(string(w)))) <= 1 {
			return strings.TrimLeftFunc(string(t[end:]), notWordRune), true
		}
	}
	return "", false
}

