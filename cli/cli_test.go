// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

func TestRendererHidesThinkingAcrossChunkBoundaries(t *testing.T) {
	var out bytes.Buffer
	r := &renderer{w: &out}
	stream := "⟦think⟧plan the reply⟦/think⟧Hello\n🔧 web_search … · ·\nWorld ⚠️ x"
	for i := 0; i < len(stream); i += 3 { // split everywhere, also inside markers
		j := i + 3
		if j > len(stream) {
			j = len(stream)
		}
		r.Write([]byte(stream[i:j]))
	}
	r.Flush()
	got := out.String()
	if strings.Contains(got, "plan the reply") || strings.Contains(got, "⟦") {
		t.Fatalf("reasoning leaked: %q", got)
	}
	for _, want := range []string{"(thinking…)", "Hello", "🔧 web_search", "World ⚠️ x"} {
		if !strings.Contains(got, want) {
			t.Fatalf("missing %q in %q", want, got)
		}
	}
	if a := r.answer.String(); strings.Contains(a, "web_search") || !strings.Contains(a, "Hello") {
		t.Fatalf("answer should hold the text, not the tool line: %q", a)
	}
	out.Reset()
	r2 := &renderer{w: &out, showThink: true}
	r2.Write([]byte("⟦think⟧why⟦/think⟧ok"))
	r2.Flush()
	if !strings.Contains(out.String(), "why") {
		t.Fatalf("--think should show reasoning: %q", out.String())
	}
	if s := stripThink("a⟦think⟧x⟦/think⟧b⟦think⟧open"); s != "ab" {
		t.Fatalf("stripThink: %q", s)
	}
}

func TestMsgFailed(t *testing.T) {
	for _, m := range []string{"error: x", "unknown", "invalid name", "??", "instance/message missing",
		"'x' already exists", "cannot pause (done)", "error: key 'X' not allowed"} {
		if !msgFailed(m) {
			t.Errorf("%q should count as failure", m)
		}
	}
	for _, m := range []string{"started (pid 5)", "stopped", "task abc created (pending)", "saved", "KEY = v (applies at once)"} {
		if msgFailed(m) {
			t.Errorf("%q is success", m)
		}
	}
}

// fakeManager answers like the real one (shapes from the API reference) and
// records the POSTs.
type fakeManager struct {
	mu     sync.Mutex
	posts  []string
	bodies []map[string]any
	auth   []string
	stream int // /chat/stream calls
}

func (f *fakeManager) handler() http.Handler {
	j := func(w http.ResponseWriter, v any) { json.NewEncoder(w).Encode(v) }
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		u, p, _ := r.BasicAuth()
		f.auth = append(f.auth, u+":"+p)
		f.mu.Unlock()
		if p != "pw" {
			w.WriteHeader(401)
			return
		}
		if r.Method == "POST" {
			var b map[string]any
			json.NewDecoder(r.Body).Decode(&b)
			f.mu.Lock()
			f.posts = append(f.posts, r.URL.Path)
			f.bodies = append(f.bodies, b)
			f.mu.Unlock()
		}
		p0 := r.URL.Path
		switch {
		case p0 == "/api/version":
			j(w, map[string]any{"installed": "2.9", "latest": "3.0", "available": true, "notes": "n"})
		case p0 == "/api/instances":
			j(w, []any{map[string]any{"name": "helper", "template": "openrouter", "running": true, "vcpus": 2, "mem_mib": 1024,
				"config": map[string]any{"TRANSPORT": "web", "OPENROUTER_MODEL": "g/m"}}})
		case p0 == "/api/session/helper":
			j(w, map[string]any{"name": "helper", "template": "openrouter", "running": true, "model": "g/m", "uptime": 5,
				"mcps": []any{map[string]any{"name": "fs", "ready": true, "missing": []any{}}}})
		case p0 == "/api/session/helper/log":
			io.WriteString(w, "boot ok\n")
		case strings.HasPrefix(p0, "/api/instances/") || p0 == "/api/create" || p0 == "/api/tasks" && r.Method == "POST" ||
			strings.HasPrefix(p0, "/api/tasks/") || p0 == "/api/skills" && r.Method == "POST" || strings.HasPrefix(p0, "/api/skills/") && r.Method == "POST" ||
			strings.HasPrefix(p0, "/api/skill-proposals/") || p0 == "/api/models" && r.Method == "POST" || p0 == "/api/settings" && r.Method == "POST" ||
			p0 == "/api/update" || strings.HasPrefix(p0, "/api/personas") && r.Method == "POST" || p0 == "/api/prompts" && r.Method == "POST" ||
			strings.HasPrefix(p0, "/api/mcps/") || strings.HasPrefix(p0, "/api/secret-store") || strings.HasPrefix(p0, "/api/memory/") && r.Method == "POST":
			j(w, map[string]any{"msg": "ok done"})
		case p0 == "/i/helper/api/chat/stream":
			f.mu.Lock()
			f.stream++
			n := f.stream
			f.mu.Unlock()
			if n == 1 {
				w.WriteHeader(503)
				io.WriteString(w, "Instance 'helper' is not running")
				return
			}
			w.Header().Set("X-Kaim-Turn", "abcdef123456")
			io.WriteString(w, "⟦think⟧hmm⟦/think⟧Hi there")
		case p0 == "/i/helper/api/steer":
			j(w, map[string]any{"queued": true})
		case p0 == "/api/chats":
			j(w, []any{map[string]any{"id": "c1", "title": "T", "instance": "helper", "updatedAt": 1.7e12,
				"messages": []any{map[string]any{"user": true, "text": "q"}, map[string]any{"user": false, "text": "⟦think⟧x⟦/think⟧a"}}}})
		case p0 == "/api/sessions-search":
			j(w, map[string]any{"hits": []any{map[string]any{"instance": "helper", "kind": "chat", "ref": "c1", "ts": 1.7e9, "snippet": "[q]"}}})
		case strings.HasPrefix(p0, "/api/trace/"):
			if r.URL.Query().Get("turn") != "" {
				j(w, map[string]any{"turn": map[string]any{"ts_start": 1.7e9, "ts_end": 1.7e9, "ms": 5, "steps": 1},
					"llm":   []any{map[string]any{"step": 1, "model": "g/m", "in": 5, "out": 6, "ms": 7, "ok": true}},
					"tools": []any{map[string]any{"tool": "bash", "target": "ls", "ok": false, "err": "x"}}, "answer": "⟦think⟧t⟦/think⟧the answer"})
			} else {
				j(w, map[string]any{"turns": []any{map[string]any{"turn": "abc", "kind": "chat", "ts_start": 1.7e9}}})
			}
		case p0 == "/api/tasks":
			j(w, []any{map[string]any{"id": "t1", "instance": "helper", "status": "done", "schedule": "", "updated": 1.7e9, "message": "m", "result": "r"}})
		case p0 == "/api/task-delete":
			j(w, map[string]any{"deleted": true, "id": "t1"})
		case p0 == "/api/history":
			j(w, map[string]any{"rows": []any{map[string]any{"ts": 1.7e9, "target": "helper", "task": "m", "result": "r", "ok": 1}}})
		case p0 == "/api/missions":
			j(w, map[string]any{"by_instance": map[string]any{"helper": []any{map[string]any{"id": "m-1", "goal": "g", "status": "active",
				"steps": []any{map[string]any{"n": 1, "text": "s", "status": "done", "result": "r"}}, "log": []any{"l"}}}}})
		case p0 == "/api/mission-admin":
			j(w, map[string]any{"msg": "ok"})
		case p0 == "/api/skills":
			j(w, []any{map[string]any{"name": "sk", "description": "d"}})
		case p0 == "/api/skill-stats":
			j(w, map[string]any{"skills": map[string]any{"sk": map[string]any{"verdict": "working", "uses": 3}}})
		case p0 == "/api/skills/sk":
			io.WriteString(w, "content")
		case p0 == "/api/skill-proposals":
			j(w, map[string]any{"proposals": []any{map[string]any{"id": "p1", "name": "n", "update": false}}})
		case p0 == "/api/notifications":
			j(w, map[string]any{"notifications": []any{map[string]any{"id": "n1", "ts": 1.7e9, "title": "t", "body": "b", "read": false}}, "unread": 1})
		case p0 == "/api/notifications/read":
			j(w, map[string]any{"marked": 1})
		case p0 == "/api/apps":
			j(w, map[string]any{"apps": []any{map[string]any{"name": "flow", "title": "Flow", "served_by": "celld", "cloud": map[string]any{"where": "local"}, "job": map[string]any{}}}})
		case p0 == "/api/apps/cloudflare":
			j(w, map[string]any{"workers": []any{map[string]any{"name": "stray", "app": nil, "url": "https://stray.x"}}, "configured": true})
		case strings.HasPrefix(p0, "/api/apps/") || strings.HasPrefix(p0, "/api/cfworkers/"):
			j(w, map[string]any{"ok": true, "trashed": "/x", "worker": "deleted"})
		case p0 == "/api/projects" && r.Method == "GET":
			j(w, map[string]any{"projects": []any{map[string]any{"name": "web", "strategy": "worktree", "source": map[string]any{"type": "host", "path": "/srv"},
				"members": map[string]any{"helper": map[string]any{"role": "lead"}}}}})
		case p0 == "/api/projects/web/status":
			j(w, map[string]any{"members": map[string]any{"helper": map[string]any{"branch": "b", "ahead": 1, "behind": 0, "files": []any{[]any{"M", "a.go"}}}}})
		case p0 == "/api/projects/web/diff/helper":
			io.WriteString(w, "diff --git a b\n")
		case strings.HasPrefix(p0, "/api/projects"):
			j(w, map[string]any{"ok": true, "commit": "c0ffee"})
		case p0 == "/api/models":
			j(w, map[string]any{"curated": []any{"g/m"}})
		case p0 == "/api/openrouter-models":
			j(w, []any{map[string]any{"id": "g/m", "price": "free", "ctx": 1000, "tools": true}})
		case p0 == "/api/router" && r.Method == "GET":
			j(w, map[string]any{"jev": map[string]any{"ok": true}, "tiers": map[string]any{}, "candidates": []any{map[string]any{"key": "openrouter/g/m", "instances": []any{"helper"}}},
				"recent": []any{map[string]any{"ts": 1.7e9, "instance": "helper", "own": "a", "chosen": "b", "why": "rule 0"}}})
		case p0 == "/api/router/tiers":
			j(w, map[string]any{"ok": true})
		case p0 == "/api/settings":
			j(w, map[string]any{"CF_API_TOKEN": "__unchanged__", "CELLD_URL": "http://x"})
		case p0 == "/api/resources":
			j(w, map[string]any{"resources": []any{map[string]any{"name": "helper", "running": true, "cpu_pct": 3.5, "rss_mb": 200, "mem_mib": 1024}}})
		case p0 == "/api/usage":
			j(w, map[string]any{"helper": map[string]any{"today": map[string]any{"calls": 2, "in": 10, "out": 5, "cost": 0.01}}})
		case p0 == "/api/usage-by-model":
			j(w, map[string]any{"rows": []any{map[string]any{"instance": "helper", "model": "g/m", "calls": 2, "cost": 0.5}}})
		case p0 == "/api/changelog":
			j(w, map[string]any{"text": "# Changelog"})
		case strings.HasPrefix(p0, "/api/audit/"):
			j(w, map[string]any{"events": []any{map[string]any{"ts": 1.7e9, "tool": "bash", "target": "ls", "ok": true}}})
		case p0 == "/api/memory/helper":
			j(w, map[string]any{"k": "v"})
		case strings.HasPrefix(p0, "/api/memory/helper/"):
			j(w, map[string]any{"value": "v"})
		case p0 == "/api/memory-search":
			j(w, map[string]any{"hits": []any{map[string]any{"score": 0.9, "text": "t"}}})
		case p0 == "/api/personas":
			j(w, []any{map[string]any{"name": "coder", "prompt": "You code.", "tools": []any{"bash"}, "model": "x/y"}})
		case p0 == "/api/prompts":
			j(w, map[string]any{"prompts": []any{map[string]any{"name": "sum", "text": "summarize"}}})
		case p0 == "/api/playbooks":
			j(w, map[string]any{"playbooks": []any{map[string]any{"id": "a1", "text": "rule"}}})
		case p0 == "/api/playbook-add":
			j(w, map[string]any{"id": "a2", "added": true})
		case p0 == "/api/playbook-remove":
			j(w, map[string]any{"removed": 1})
		case p0 == "/api/iroh":
			j(w, map[string]any{"ok": true, "msg": "added", "node_id": "gw", "allow": []any{map[string]any{"id": "d1", "label": "phone"}}, "available": true})
		case p0 == "/api/mcps":
			j(w, []any{map[string]any{"name": "fs", "command": "npx", "args": []any{"x"}}})
		case p0 == "/api/secret-keys":
			j(w, map[string]any{"keys": []any{"GH_TOKEN"}, "sources": map[string]any{"GH_TOKEN": "store"}})
		default:
			w.WriteHeader(404)
			j(w, map[string]any{"error": "not found"})
		}
	})
}

// run executes the CLI in-process against the fake and returns stdout.
func run(t *testing.T, f *fakeManager, srv *httptest.Server, stdin string, args ...string) (string, error) {
	t.Helper()
	api, cfg, flagJSON, flagThink, flagNoStart, flagInstance = nil, Config{}, false, false, false, ""
	os.Setenv("KAIM56_URL", srv.URL)
	os.Setenv("KAIM56_PASS", "pw")
	os.Setenv("KAIM56_INSTANCE", "helper")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	root := &cobra.Command{Use: "kaim56", SilenceUsage: true, SilenceErrors: true}
	pf := root.PersistentFlags()
	pf.StringVar(&flagConfig, "config", "", "")
	pf.StringVar(&flagURL, "url", "", "")
	pf.StringVar(&flagIroh, "iroh", "", "")
	pf.StringVar(&flagUser, "user", "", "")
	pf.StringVar(&flagPass, "pass", "", "")
	pf.StringVarP(&flagInstance, "instance", "i", "", "")
	pf.BoolVar(&flagJSON, "json", false, "")
	root.AddCommand(versionCmd(), configCmd())
	for _, add := range commandGroups {
		root.AddCommand(add()...)
	}
	root.SetArgs(args)
	oldOut, oldIn := os.Stdout, os.Stdin
	rOut, wOut, _ := os.Pipe()
	rIn, wIn, _ := os.Pipe()
	io.WriteString(wIn, stdin)
	wIn.Close()
	os.Stdout, os.Stdin = wOut, rIn
	var buf bytes.Buffer
	doneC := make(chan struct{})
	go func() { io.Copy(&buf, rOut); close(doneC) }()
	err := root.Execute()
	wOut.Close()
	<-doneC
	os.Stdout, os.Stdin = oldOut, oldIn
	return buf.String(), err
}

func TestEveryCommandAgainstAFakeManager(t *testing.T) {
	f := &fakeManager{}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()
	cases := []struct {
		args  []string
		stdin string
		want  string
	}{
		{[]string{"version"}, "", "manager"},
		{[]string{"instances", "list"}, "", "helper"},
		{[]string{"inst", "show"}, "", "OPENROUTER_MODEL=g/m"},
		{[]string{"inst", "log", "helper"}, "", "boot ok"},
		{[]string{"inst", "start"}, "", "ok done"},
		{[]string{"inst", "create", "x", "--persona", "coder", "--start"}, "", "ok done"},
		{[]string{"inst", "config", "get", "helper", "TRANSPORT"}, "", "web"},
		{[]string{"inst", "config", "set", "helper", "BUDGET_TOKENS", "100"}, "", "ok done"},
		{[]string{"inst", "model", "helper", "llama:x"}, "", "ok done"},
		{[]string{"inst", "internet", "helper", "off"}, "", "ok done"},
		{[]string{"inst", "delete", "helper", "-y"}, "", "ok done"},
		{[]string{"chat", "helper", "hi", "there"}, "", "Hi there"},
		{[]string{"steer", "helper", "also", "x"}, "", "queued"},
		{[]string{"chats", "list"}, "", "c1"},
		{[]string{"chats", "show", "c1"}, "", "helper> a"},
		{[]string{"search", "q"}, "", "[q]"},
		{[]string{"trace", "helper"}, "", "abc"},
		{[]string{"trace", "helper", "abc"}, "", "the answer"},
		{[]string{"tasks", "list"}, "", "t1"},
		{[]string{"tasks", "show", "t1"}, "", "result"},
		{[]string{"tasks", "add", "helper", "do", "it", "--every", "2h"}, "", "ok done"},
		{[]string{"tasks", "edit", "t1", "--at", "hourly"}, "", "ok done"},
		{[]string{"tasks", "run", "t1"}, "", "ok done"},
		{[]string{"tasks", "delete", "t1"}, "", "deleted"},
		{[]string{"tasks", "history"}, "", "helper"},
		{[]string{"missions", "list"}, "", "m-1"},
		{[]string{"missions", "show", "m-1"}, "", "1. [done] s"},
		{[]string{"missions", "pause", "m-1"}, "", "pause"},
		{[]string{"skills", "list"}, "", "working"},
		{[]string{"skills", "show", "sk"}, "", "content"},
		{[]string{"skills", "save", "sk", "-d", "d"}, "body", "ok done"},
		{[]string{"skills", "proposals"}, "", "p1"},
		{[]string{"skills", "approve", "p1"}, "", "ok done"},
		{[]string{"skills", "discard", "p1", "dup"}, "", "ok done"},
		{[]string{"notifications", "list"}, "", "1 unread"},
		{[]string{"notif", "read"}, "", "1 marked"},
		{[]string{"apps", "list"}, "", "stray"},
		{[]string{"apps", "rename", "flow", "flow2"}, "", "renamed"},
		{[]string{"apps", "delete", "flow", "-y"}, "", "deleted"},
		{[]string{"apps", "worker-delete", "stray", "-y"}, "", "deleted"},
		{[]string{"projects", "list"}, "", "helper(lead)"},
		{[]string{"projects", "create", "web", "--path", "/srv", "--member", "lead=helper"}, "", "saved"},
		{[]string{"projects", "status", "web"}, "", "a.go"},
		{[]string{"projects", "diff", "web", "helper"}, "", "diff --git"},
		{[]string{"projects", "merge", "web", "helper"}, "", "c0ffee"},
		{[]string{"models", "list"}, "", "g/m"},
		{[]string{"models", "list", "--all"}, "", "free"},
		{[]string{"models", "router", "status"}, "", "rule 0"},
		{[]string{"models", "router", "tier", "openrouter/g/m", "cheap"}, "", "cheap"},
		{[]string{"models", "router", "use", "helper", "default"}, "", "ok done"},
		{[]string{"settings", "get"}, "", "CF_API_TOKEN=(set)"},
		{[]string{"settings", "set", "CELLD_URL", "http://y"}, "", "ok done"},
		{[]string{"status"}, "", "update available"},
		{[]string{"usage"}, "", "0.50"},
		{[]string{"update", "check"}, "", "kaim56 update run"},
		{[]string{"changelog"}, "", "# Changelog"},
		{[]string{"audit", "helper"}, "", "bash"},
		{[]string{"memory", "get", "helper"}, "", "k: v"},
		{[]string{"memory", "get", "helper", "k"}, "", "v"},
		{[]string{"memory", "set", "helper", "k", "v"}, "", "ok done"},
		{[]string{"memory", "search", "helper", "q"}, "", "0.90"},
		{[]string{"personas", "list"}, "", "coder"},
		{[]string{"prompts", "list"}, "", "/sum"},
		{[]string{"playbook", "list", "helper"}, "", "rule"},
		{[]string{"playbook", "add", "helper", "be", "brief"}, "", "added: a2"},
		{[]string{"playbook", "rm", "helper", "a1"}, "", "1 removed"},
		{[]string{"iroh", "list"}, "", "phone"},
		{[]string{"iroh", "add", "d2", "laptop"}, "", "added"},
		{[]string{"mcp", "list"}, "", "npx"},
		{[]string{"secrets", "list"}, "", "GH_TOKEN"},
		{[]string{"tasks", "list", "--json"}, "", "\"id\": \"t1\""},
	}
	for _, c := range cases {
		out, err := run(t, f, srv, c.stdin, c.args...)
		if err != nil {
			t.Errorf("%v: %v", c.args, err)
			continue
		}
		if !strings.Contains(out, c.want) {
			t.Errorf("%v: want %q in output:\n%s", c.args, c.want, out)
		}
	}
	// chat: the stopped instance was started first, then the stream retried with a turn id
	f.mu.Lock()
	defer f.mu.Unlock()
	var started, streamBody bool
	for i, p := range f.posts {
		if p == "/api/instances/helper/start" {
			started = true
		}
		if p == "/i/helper/api/chat/stream" && f.bodies[i]["message"] == "hi there" && len(str(f.bodies[i]["turn"])) == 12 {
			streamBody = true
		}
	}
	if !started || !streamBody {
		t.Errorf("chat should start the instance and send message+turn: started=%v body=%v", started, streamBody)
	}
	for _, a := range f.auth {
		if a != "admin:pw" {
			t.Fatalf("every call must carry the login, got %q", a)
		}
	}
}

func TestWrongPasswordExplainsItself(t *testing.T) {
	f := &fakeManager{}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()
	_, err := run(t, f, srv, "", "instances", "list", "--pass", "wrong")
	if err == nil || !strings.Contains(err.Error(), "login refused") {
		t.Fatalf("want a login hint, got %v", err)
	}
}

func TestFailedMsgIsAnError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"msg":"error: key 'X' not allowed"}`)
	}))
	defer srv.Close()
	_, err := run(t, &fakeManager{}, srv, "", "inst", "config", "set", "helper", "X", "1")
	if err == nil || !strings.Contains(err.Error(), "not allowed") {
		t.Fatalf("a failing msg must fail the command, got %v", err)
	}
}

func TestUpdateFindsNewestCLIRelease(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `[{"tag_name":"cli-v0.10.0","assets":[{"name":"`+assetName()+`","browser_download_url":"u10","size":3}]},
		  {"tag_name":"cli-v0.9.0","assets":[{"name":"`+assetName()+`","browser_download_url":"u9"}]},
		  {"tag_name":"cli-v9.0.0","draft":true,"assets":[{"name":"`+assetName()+`"}]},
		  {"tag_name":"voice-v9.0.0","assets":[{"name":"`+assetName()+`"}]},
		  {"tag_name":"cli-v1.0.0","assets":[{"name":"kaim56-other-os"}]}]`)
	}))
	defer srv.Close()
	r, err := latestRelease(srv.URL, srv.Client())
	if err != nil || r == nil || r.Version != "0.10.0" || r.URL != "u10" {
		t.Fatalf("want cli-v0.10.0 (not a draft, not voice, with our asset), got %+v %v", r, err)
	}
	if !newerVersion("0.10.0", "0.9.9") || newerVersion("0.2.0", "0.2") {
		t.Fatal("version order")
	}
	// the hint remembers the latest version and checks again only after a day
	p := t.TempDir() + "/latest"
	if _, due := knownLatest(p, time.Now()); !due {
		t.Fatal("no file: a check is due")
	}
	rememberLatest(p, "0.3.0")
	if v, due := knownLatest(p, time.Now()); v != "0.3.0" || due {
		t.Fatalf("fresh: %q due=%v", v, due)
	}
	if _, due := knownLatest(p, time.Now().Add(25*time.Hour)); !due {
		t.Fatal("after a day a check is due")
	}
}

func TestApplyUpdateReplacesOnlyWithACompleteDownload(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "NEWBIN") }))
	defer srv.Close()
	exe := t.TempDir() + "/kaim56"
	os.WriteFile(exe, []byte("OLD"), 0o755)
	if err := applyUpdate(&release{URL: srv.URL, Size: 99}, exe); err == nil {
		t.Fatal("a short download must fail")
	}
	if b, _ := os.ReadFile(exe); string(b) != "OLD" {
		t.Fatal("the old binary must stay")
	}
	if err := applyUpdate(&release{URL: srv.URL, Size: 6}, exe); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "NEWBIN" {
		t.Fatal("not replaced")
	}
}
