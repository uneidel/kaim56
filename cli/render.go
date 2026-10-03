// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"io"
	"strings"
	"unicode/utf8"
)

// The agent's stream is plain text with markers (openrouter bridge):
// ⟦think⟧…⟦/think⟧ reasoning, "\n🔧 tool …" tool calls (" ·" heartbeats while
// one runs), "\n↪ msg" steering, "⚠️ …" errors. renderer turns it into terminal
// output: reasoning hidden (or dimmed with showThink), tools dimmed; a marker
// split across two chunks is held back until it is complete.
const (
	thinkOpen  = "⟦think⟧"
	thinkClose = "⟦/think⟧"
)

type renderer struct {
	w         io.Writer
	showThink bool
	color     bool
	inThink   bool
	toolLine  bool   // inside a "🔧 …" line
	pending   string // a possible marker start, held back
	shownHint bool
	answer    strings.Builder // the visible text (no reasoning), for the caller
}

func (r *renderer) style(code, s string) string {
	if !r.color || s == "" {
		return s
	}
	return "\x1b[" + code + "m" + s + "\x1b[0m"
}

// Write takes stream bytes as they arrive.
func (r *renderer) Write(p []byte) (int, error) {
	s := r.pending + string(p)
	r.pending = ""
	for s != "" {
		marker := thinkOpen
		if r.inThink {
			marker = thinkClose
		}
		i := strings.Index(s, marker)
		if i < 0 {
			// hold back a tail that could be the start of the marker
			keep := 0
			for k := len(marker) - 1; k > 0; k-- {
				if strings.HasSuffix(s, marker[:k]) {
					keep = k
					break
				}
			}
			cut := len(s) - keep
			// never split a character: a 🔧 cut in half would not be seen
			for k := 1; k <= 3 && cut-k >= 0; k++ {
				if utf8.RuneStart(s[cut-k]) {
					if !utf8.FullRuneInString(s[cut-k : cut]) {
						cut -= k
					}
					break
				}
			}
			r.emit(s[:cut])
			r.pending = s[cut:]
			return len(p), nil
		}
		r.emit(s[:i])
		s = s[i+len(marker):]
		r.inThink = !r.inThink
		if r.inThink && !r.showThink && !r.shownHint {
			io.WriteString(r.w, r.style("2", "(thinking…)\n"))
			r.shownHint = true
		}
	}
	return len(p), nil
}

func (r *renderer) emit(s string) {
	if s == "" {
		return
	}
	if r.inThink {
		if r.showThink {
			io.WriteString(r.w, r.style("2;3", s))
		}
		return
	}
	// tool lines start with 🔧 and end at the next newline
	for s != "" {
		if r.toolLine {
			j := strings.IndexByte(s, '\n')
			if j < 0 {
				io.WriteString(r.w, r.style("2", s))
				return
			}
			io.WriteString(r.w, r.style("2", s[:j])+"\n")
			r.toolLine = false
			s = s[j+1:]
			continue
		}
		j := strings.Index(s, "🔧")
		if j < 0 {
			r.out(s)
			return
		}
		r.out(s[:j])
		r.toolLine = true
		s = s[j:]
	}
}

func (r *renderer) out(s string) {
	if s == "" {
		return
	}
	r.answer.WriteString(s)
	if strings.Contains(s, "⚠️") {
		io.WriteString(r.w, r.style("33", s))
		return
	}
	io.WriteString(r.w, s)
}

// Flush: whatever was held back is plain text after all.
func (r *renderer) Flush() {
	p := r.pending
	r.pending = ""
	r.emit(p)
}

// stripThink: an answer without its reasoning blocks (for recovered answers).
func stripThink(s string) string {
	for {
		i := strings.Index(s, thinkOpen)
		if i < 0 {
			return s
		}
		j := strings.Index(s[i:], thinkClose)
		if j < 0 {
			return s[:i]
		}
		s = s[:i] + s[i+j+len(thinkClose):]
	}
}
