// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"net/url"
	"strings"
)

// Many manager routes answer HTTP 200 {"msg": "..."} even when they fail; the
// failure is only in the text. msgFailed reads it the way the app does.
func msgFailed(m string) bool {
	l := strings.ToLower(strings.TrimSpace(m))
	for _, p := range []string{"error", "unknown", "invalid", "??", "cannot ", "max "} {
		if strings.HasPrefix(l, p) {
			return true
		}
	}
	for _, s := range []string{" missing", "not allowed", "already exists", "not installed"} {
		if strings.Contains(l, s) {
			return true
		}
	}
	return false
}

// msgPost: a POST to a msg route; prints the msg, fails when it says so.
func msgPost(path string, body any) error {
	var r struct {
		Msg string `json:"msg"`
	}
	raw, err := api.Post(path, body, &r)
	if err != nil {
		return err
	}
	if flagJSON {
		printJSON(raw)
	} else if r.Msg != "" {
		fmt.Println(r.Msg)
	}
	if msgFailed(r.Msg) {
		return fmt.Errorf("the manager said: %s", r.Msg)
	}
	return nil
}

func esc(s string) string { return url.PathEscape(s) }
