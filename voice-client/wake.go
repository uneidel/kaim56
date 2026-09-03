// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"strings"
	"unicode"
)

// wakeMatch prueft, ob das Transkript mit dem Wake-Word beginnt, und liefert
// den Rest (die eigentliche Nachricht) ohne das Wort und ohne Trennzeichen.
//
// Kein Modell auf dem Desktop: STT laeuft ohnehin fuer jede Aeusserung auf dem
// eigenen Server, das Gate ist ein Textvergleich danach. Absichtlich simpel —
// case-insensitiv, fuehrende Satzzeichen egal ("...Kat" von STT), aber mit
// Wortgrenze: "Katalog" weckt "Kat" nicht.
func wakeMatch(text, word string) (string, bool) {
	if word == "" {
		return text, true // kein Wake-Word konfiguriert -> alles geht durch
	}
	notWordRune := func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsNumber(r)
	}
	t := []rune(strings.TrimLeftFunc(text, notWordRune))
	w := []rune(word)
	if len(t) < len(w) || !strings.EqualFold(string(t[:len(w)]), word) {
		return "", false
	}
	rest := t[len(w):]
	if len(rest) > 0 && !notWordRune(rest[0]) {
		return "", false // Wortgrenze verlangt
	}
	return strings.TrimLeftFunc(string(rest), notWordRune), true
}
