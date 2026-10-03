// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"text/tabwriter"
	"time"
)

// printJSON: --json output (pretty-printed raw answer).
func printJSON(raw []byte) {
	var b bytes.Buffer
	if json.Indent(&b, raw, "", "  ") != nil {
		os.Stdout.Write(raw)
		fmt.Println()
		return
	}
	fmt.Println(b.String())
}

// done: the answer as JSON with --json, else a short line.
func done(raw []byte, line string, a ...any) {
	if flagJSON {
		printJSON(raw)
		return
	}
	fmt.Printf(line+"\n", a...)
}

func table(header []string, rows [][]string) {
	w := tabwriter.NewWriter(os.Stdout, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, strings.Join(header, "\t"))
	for _, r := range rows {
		fmt.Fprintln(w, strings.Join(r, "\t"))
	}
	w.Flush()
}

func clip(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	if r := []rune(s); len(r) > n {
		return string(r[:n-1]) + "…"
	}
	return s
}

// ago: a unix time (seconds or ms) as "5m ago".
func ago(v any) string {
	var t float64
	switch x := v.(type) {
	case float64:
		t = x
	case int64:
		t = float64(x)
	case string:
		if p, err := time.Parse(time.RFC3339, x); err == nil {
			t = float64(p.Unix())
		}
	}
	if t <= 0 {
		return ""
	}
	if t > 1e12 {
		t /= 1000
	}
	d := time.Since(time.Unix(int64(t), 0))
	switch {
	case d < time.Minute:
		return "just now"
	case d < time.Hour:
		return fmt.Sprintf("%dm ago", int(d.Minutes()))
	case d < 48*time.Hour:
		return fmt.Sprintf("%dh ago", int(d.Hours()))
	}
	return fmt.Sprintf("%dd ago", int(d.Hours()/24))
}

// str: a JSON value as text ("" for null).
func str(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case float64:
		if x == float64(int64(x)) {
			return fmt.Sprintf("%d", int64(x))
		}
		return fmt.Sprintf("%.2f", x)
	case bool:
		if x {
			return "yes"
		}
		return "no"
	}
	b, _ := json.Marshal(v)
	return string(b)
}
