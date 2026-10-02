package main

import (
	"strings"
	"testing"
)

func TestStatusErrorSaysWhatToFix(t *testing.T) {
	if e := statusError(401, "/api/stt", nil).Error(); !strings.Contains(e, "\"pass\"") || !strings.Contains(e, "kaim56-voice.json") {
		t.Fatalf("401 does not point at the config: %s", e)
	}
	if e := statusError(429, "/api/stt", []byte(`{"error":"too many failed logins, try again later"}`)).Error(); !strings.Contains(e, "locked") {
		t.Fatalf("lockout not named: %s", e)
	}
	if e := statusError(429, "/api/llm", []byte("rate limit")).Error(); !strings.HasPrefix(e, "HTTP 429") {
		t.Fatalf("other 429 changed: %s", e)
	}
	if e := statusError(500, "/x", []byte("boom")).Error(); e != "HTTP 500 from /x: boom" {
		t.Fatalf("other status changed: %s", e)
	}
}
