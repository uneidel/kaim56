// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNewerVersion(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"1.1.0", "1.0.0", true}, {"1.10", "1.9", true}, {"1.0", "1.0.0", false},
		{"1.0.1", "1.0", true}, {"2", "1.9.9", true}, {"1.0.0", "1.0.0", false},
		{"0.9", "1.0", false},
	}
	for _, c := range cases {
		if got := newerVersion(c.a, c.b); got != c.want {
			t.Errorf("newerVersion(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

// releasesServer serves a releases list like GitHub's plus the binary bytes.
func releasesServer(t *testing.T, body []byte) *httptest.Server {
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/releases":
			asset := assetName()
			fmt.Fprintf(w, `[
			 {"tag_name":"app-v5.41","assets":[{"name":"katagent-5.41.apk","browser_download_url":"%[1]s/apk","size":1}]},
			 {"tag_name":"voice-v1.0.5","assets":[{"name":%[2]q,"browser_download_url":"%[1]s/bin/1.0.5","size":%[3]d}]},
			 {"tag_name":"voice-v1.2.0","assets":[{"name":%[2]q,"browser_download_url":"%[1]s/bin/1.2.0","size":%[3]d}]},
			 {"tag_name":"voice-v9.9.9","assets":[{"name":"kaim56-voice-plan9-mips","browser_download_url":"%[1]s/bin/other","size":1}]},
			 {"tag_name":"voice-v8.0.0","draft":true,"assets":[{"name":%[2]q,"browser_download_url":"%[1]s/bin/draft","size":%[3]d}]},
			 {"tag_name":"v1.0.0","assets":[{"name":"kaim56-voice","browser_download_url":"%[1]s/bin/old","size":1}]}
			]`, srv.URL, asset, len(body))
		case "/bin/1.2.0":
			w.Write(body)
		case "/bin/short":
			w.Write(body[:len(body)/2])
		default:
			http.NotFound(w, r)
		}
	}))
	return srv
}

func TestLatestReleasePicksNewestWithOurAsset(t *testing.T) {
	body := []byte("#!/bin/sh\necho new\n")
	srv := releasesServer(t, body)
	defer srv.Close()
	rel, err := latestRelease(srv.URL + "/releases")
	if err != nil {
		t.Fatal(err)
	}
	// 9.9.9 has no asset for this platform, 8.0.0 is a draft, v1.0.0 is a
	// platform release with the old asset name -> 1.2.0 wins.
	if rel == nil || rel.Version != "1.2.0" || rel.Size != int64(len(body)) ||
		!strings.HasSuffix(rel.URL, "/bin/1.2.0") {
		t.Fatalf("got %+v", rel)
	}
}

func TestApplyUpdateReplacesBinaryAtomically(t *testing.T) {
	body := []byte("#!/bin/sh\necho new\n")
	srv := releasesServer(t, body)
	defer srv.Close()
	exe := filepath.Join(t.TempDir(), "kaim56-voice")
	os.WriteFile(exe, []byte("old"), 0o755)
	// a short download must leave the old binary untouched
	err := applyUpdate(&release{Version: "1.2.0", URL: srv.URL + "/bin/short", Size: int64(len(body))}, exe)
	if err == nil || !strings.Contains(err.Error(), "incomplete") {
		t.Fatalf("short download must fail: %v", err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "old" {
		t.Fatalf("old binary damaged: %q", b)
	}
	if _, err := os.Stat(exe + ".new"); err == nil {
		t.Fatal("temp file left behind")
	}
	// the complete download replaces it, executable
	if err := applyUpdate(&release{Version: "1.2.0", URL: srv.URL + "/bin/1.2.0", Size: int64(len(body))}, exe); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(exe)
	st, _ := os.Stat(exe)
	if string(b) != string(body) || st.Mode().Perm()&0o111 == 0 {
		t.Fatalf("binary not replaced: %q mode %o", b, st.Mode().Perm())
	}
}

func TestUpdateStampLimitsChecks(t *testing.T) {
	stamp := filepath.Join(t.TempDir(), "sub", "update-check")
	now := time.Now()
	if !checkDue(stamp, now) {
		t.Fatal("no stamp: a check is due")
	}
	touchStamp(stamp)
	if checkDue(stamp, now) {
		t.Fatal("just checked: not due")
	}
	if !checkDue(stamp, now.Add(updateEvery+time.Minute)) {
		t.Fatal("after the interval: due again")
	}
}

func TestSelfUpdateUpToDate(t *testing.T) {
	// The newest release equals the running version -> nothing installed,
	// and the check is stamped so the next start stays quiet.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `[{"tag_name":"voice-v%s","assets":[{"name":%q,"browser_download_url":"x","size":1}]}]`,
			version, assetName())
	}))
	defer srv.Close()
	old := releasesURL
	releasesURL = srv.URL
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	defer func() { releasesURL = old }()
	if ok, msg := selfUpdate(true); ok || !strings.Contains(msg, "up to date") {
		t.Fatalf("got %v %q", ok, msg)
	}
	if checkDue(stampPath(), time.Now()) {
		t.Fatal("the check was not stamped")
	}
	if ok, msg := selfUpdate(false); ok || msg != "" {
		t.Fatalf("within the interval nothing should happen: %v %q", ok, msg)
	}
}
