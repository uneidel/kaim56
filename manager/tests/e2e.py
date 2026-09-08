#!/usr/bin/env python3
"""End-to-end and unit tests for kAIm56 (manager + openrouter agent).

Stdlib-only (unittest), in line with the project's philosophy — no test
dependency. Three tiers, depending on the environment:

  * OFFLINE  — agent/manager functions directly (import), no VM, no network.
               Always run.
  * HTTP     — against the running manager on 127.0.0.1:8700. Skipped when
               the manager is not reachable.
  * LIVE     — round-trip to a running agent VM (orchestrator). Only the free
               /goal path (no model call). Skipped when the instance is not
               running.

Run:  python3 tests/e2e.py            (or ./run-tests.sh)
One tier only:  python3 tests/e2e.py AgentLogic
"""
import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

# --- paths to the modules under test ----------------------------------------
FC_DIR = os.environ.get("FC_DIR", "/home/ulrich/firecracker")
AGENT_PATH = os.environ.get("AGENT_PATH", "/home/ulrich/openrouter-agent/agent.py")
MANAGER_PATH = os.path.join(FC_DIR, "manager.py")
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8700")

# manager.py imports the sibling module `chatui` -> its directory must be on the
# search path, otherwise the import fails in the manager unit tests.
if FC_DIR not in sys.path:
    sys.path.insert(0, FC_DIR)


def _load(name, path, env=None):
    """Load a module from a file; optionally set os.environ first."""
    if env:
        os.environ.update(env)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _http(path, method="GET", body=None, timeout=8):
    """(status, text) against the manager. Raises on a connection error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MANAGER_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _readj(path):
    with open(path) as fh:
        return json.load(fh)


def _manager_up():
    try:
        st, _ = _http("/api/agents", timeout=4)
        return st == 200
    except Exception:
        return False


def _orchestrator_running():
    try:
        st, txt = _http("/api/agents", timeout=4)
        if st != 200:
            return False
        for a in json.loads(txt).get("agents", []):
            if a.get("name") == "orchestrator":
                return bool(a.get("running"))
    except Exception:
        return False
    return False


# ===========================================================================
# OFFLINE: Agent-Logik
# ===========================================================================
class AgentLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="e2e-agent-")
        cls.a = _load("agent_e2e", AGENT_PATH,
                      {"CLAUDE_WORKDIR": cls.tmp, "OPENROUTER_API_KEY": "dummy"})
        cls.a.report_usage = lambda *a, **k: None   # no network to the manager

    def test_llama_tool_json_500_falls_back_to_no_tools(self):
        """llama.cpp 500 due to broken tool-call JSON -> retry the round without
        tools (text reply) instead of losing the turn."""
        import io, urllib.error
        a = self.a
        calls = []
        good = [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n', b'data: [DONE]\n']
        class FakeResp:
            headers = {}
            def __iter__(self): return iter(good)
        def fake_urlopen(req, *ar, **kw):
            body = req.data.decode()
            has_tools = '"tools"' in body
            calls.append("tools" if has_tools else "notools")
            if has_tools:
                raise urllib.error.HTTPError("http://x", 500, "err", {},
                    io.BytesIO(b'{"error":{"message":"Failed to parse tool call arguments as JSON"}}'))
            return FakeResp()
        saved = (a.urllib.request.urlopen, a._llm_headers, a._llm_url)
        a.urllib.request.urlopen = fake_urlopen
        a._llm_headers = lambda: {"Content-Type": "application/json"}
        a._llm_url = lambda: "http://x/v1/chat/completions"
        toks = []
        try:
            msg = a.or_chat_stream([{"role": "user", "content": "hi"}],
                                   [{"type": "function", "function": {"name": "t", "parameters": {}}}],
                                   toks.append)
        finally:
            (a.urllib.request.urlopen, a._llm_headers, a._llm_url) = saved
        self.assertEqual(calls, ["tools", "notools"])     # first with, then without tools
        self.assertIn("ok", "".join(toks))                # text reply came through
        self.assertEqual(msg["content"], "ok")

    def test_reasoning_content_streamed(self):
        """llama.cpp/Qwen3 sends thinking as reasoning_content — must be streamed;
        content stays the actual reply."""
        a = self.a
        lines = [
            b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            b'data: [DONE]\n',
        ]
        class FakeResp:
            headers = {}
            def __iter__(self): return iter(lines)
        saved = (a.urllib.request.urlopen, a._llm_headers, a._llm_url)
        a.urllib.request.urlopen = lambda *ar, **kw: FakeResp()
        a._llm_headers = lambda: {"Content-Type": "application/json"}
        a._llm_url = lambda: "http://x/v1/chat/completions"
        toks = []
        try:
            msg = a.or_chat_stream([{"role": "user", "content": "hi"}], None, toks.append)
        finally:
            (a.urllib.request.urlopen, a._llm_headers, a._llm_url) = saved
        out = "".join(toks)
        self.assertIn("thinking", out)            # reasoning_content not dropped
        self.assertIn("Hello", out)               # content streamed
        self.assertEqual(msg["content"], "Hello")

    def test_reasoning_only_no_empty_reply(self):
        """Thinking only, no content -> fall back to the thinking instead of an empty reply."""
        a = self.a
        lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"only thought"}}]}\n',
            b'data: [DONE]\n',
        ]
        class FakeResp:
            headers = {}
            def __iter__(self): return iter(lines)
        saved = (a.urllib.request.urlopen, a._llm_headers, a._llm_url)
        a.urllib.request.urlopen = lambda *ar, **kw: FakeResp()
        a._llm_headers = lambda: {"Content-Type": "application/json"}
        a._llm_url = lambda: "http://x/v1/chat/completions"
        try:
            msg = a.or_chat_stream([{"role": "user", "content": "hi"}], None, lambda t: None)
        finally:
            (a.urllib.request.urlopen, a._llm_headers, a._llm_url) = saved
        self.assertEqual(msg["content"], "only thought")  # no None -> no _(empty reply)_

    def test_steps_unlimited(self):
        """/steps accepts a number 1..x and 'unlimited' (0 = unlimited)."""
        import itertools
        a = self.a; saved = a.MAX_STEPS
        try:
            a._set_steps("/steps 5"); self.assertEqual(a.MAX_STEPS, 5)
            self.assertEqual(list(a._step_iter()), [0, 1, 2, 3, 4])
            a._set_steps("/steps 999"); self.assertEqual(a.MAX_STEPS, 999)   # no more 60-cap
            r = a._set_steps("/steps unlimited")
            self.assertLessEqual(a.MAX_STEPS, 0); self.assertIn("unlimited", r)
            self.assertIsInstance(a._step_iter(), itertools.count)           # unlimited
        finally:
            a.MAX_STEPS = saved

    def test_tool_heartbeat_keeps_stream_alive(self):
        """During a slow tool the stream must send a visible tool-status token
        (🔧) and periodic heartbeats (·), otherwise an idle timeout cuts the
        connection mid-sentence (slow local models)."""
        import time as _t
        a = self.a
        toks = []; calls = {"n": 0}
        def fake_stream(hist, tools, on_token):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "shell", "arguments": "{}"}}]}
            on_token("done"); return {"role": "assistant", "content": "done"}
        def fake_exec(name, args):
            _t.sleep(0.15); return "ok"
        saved = (a.or_chat_stream, a.exec_tool, a.HEARTBEAT_SEC, a._drain_steer, a._goal)
        a.or_chat_stream = fake_stream; a.exec_tool = fake_exec
        a.HEARTBEAT_SEC = 0.03; a._drain_steer = lambda *x: False; a._goal = None
        try:
            del a._history[1:]
            a.run_stream("build something", toks.append)
        finally:
            (a.or_chat_stream, a.exec_tool, a.HEARTBEAT_SEC, a._drain_steer, a._goal) = saved
        out = "".join(toks)
        self.assertIn("\U0001f527", out)   # Tool-Status
        self.assertIn("\u00b7", out)        # Heartbeat waehrend Tool-Lauf
        self.assertIn("done", out)           # final reply afterwards

    # --- Backend-Auswahl (Kernstueck orcarouter/llama/openrouter) -----------
    def _select(self, env):
        """Run the backend-selection block from agent.py with env and return
        the resulting variables (isolated, without re-import)."""
        src = open(AGENT_PATH).read()
        block = src[src.index("LLAMA_ENDPOINT = os.environ"):src.index("WORKDIR = os.environ")]
        g = {"os": type("O", (), {"environ": dict(env)})(),
             "OR_URL": "https://openrouter.ai/api/v1/chat/completions",
             "OR_MODEL": env.get("OPENROUTER_MODEL", "openai/gpt-4o")}
        # The block calls os.environ.get(...) -> we need a real mapping.
        g["os"].environ = dict(env)
        exec(block, g)
        return g

    def test_bare_mcp_tool_name_resolves_when_unique(self):
        """gemini dropped the 'mrmusic__' prefix and got 'unknown tool'. A bare
        name resolves when exactly one MCP tool matches; two candidates stay
        unknown; built-ins are untouched. /tools lists the registry."""
        a = self.a
        class Srv:
            def __init__(self): self.calls = []
            def call(self, tool, args): self.calls.append((tool, args)); return "ON"
        old_tools, old_mcp, old_audit = dict(a._mcp_tools), dict(a._mcp), a.audit
        try:
            a.audit = lambda *x, **k: None
            srv = Srv()
            a._mcp_tools.clear(); a._mcp.clear()
            a._mcp_tools["mrmusic__mrmusic_power"] = ("mrmusic", "mrmusic_power")
            a._mcp["mrmusic"] = srv
            self.assertEqual(a._resolve_tool_name("mrmusic_power"), "mrmusic__mrmusic_power")
            self.assertEqual(a._resolve_tool_name("mrmusic__mrmusic_power"), "mrmusic__mrmusic_power")
            self.assertEqual(a._resolve_tool_name("bash"), "bash")
            self.assertEqual(a.exec_tool("mrmusic_power", {"on": True}), "ON")
            self.assertEqual(srv.calls, [("mrmusic_power", {"on": True})])
            a._mcp_tools["other__mrmusic_power"] = ("other", "mrmusic_power")
            self.assertEqual(a._resolve_tool_name("mrmusic_power"), "mrmusic_power")   # ambiguous
            self.assertIn("unknown tool", a.exec_tool("mrmusic_power", {}))
            rep = a._tools_report()
            self.assertIn("mcp (2): mrmusic__mrmusic_power, other__mrmusic_power", rep)
            self.assertIn("built-in (", rep)
        finally:
            a._mcp_tools.clear(); a._mcp_tools.update(old_tools)
            a._mcp.clear(); a._mcp.update(old_mcp)
            a.audit = old_audit

    def test_now_line_gives_the_model_a_clock(self):
        """Every turn carries exactly one [Now] system line in the instance's
        timezone; a second injection replaces, never duplicates."""
        a = self.a
        old_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/Berlin"
            line = a._now_line()
            self.assertTrue(line.startswith("[Now] "))
            self.assertIn("(Europe/Berlin, UTC+0", line)             # +01:00 or +02:00
            self.assertRegex(line, r"T18:30:00\+0[12]:00")             # tool-input example
            self.assertIn(time.strftime("%Y-%m-%d"), line)   # host and guest tz agree today
            a._history[:] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "Es ist 16:36 Uhr."}]
            a._inject_now(); a._inject_now()
            nows = [m for m in a._history if m["role"] == "system" and m["content"].startswith("[Now]")]
            self.assertEqual(len(nows), 1)
            self.assertEqual(a._history[0]["content"], "sys")
            self.assertTrue(a._history[-1]["content"].startswith("[Now]"))   # last = next to the question
            self.assertIn("outdated", a._history[-1]["content"])
            os.environ["TZ"] = "Not/AZone"
            self.assertTrue(a._now_line().startswith("[Now] "))   # falls back, never raises
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz

    def test_spawn_subagent_rides_the_task_path(self):
        """spawn_subagent no longer calls admin routes (403 for guests since
        08-14): it posts create_task target=ephemeral, wait=true, with the
        chosen model, and returns the result text."""
        a = self.a
        calls = []
        old = a._mgr
        try:
            a._mgr = lambda base, path, payload, timeout=30: (calls.append((path, payload, timeout)),
                                                              json.dumps({"ok": True, "result": "42"}))[1]
            self.assertEqual(a.t_spawn_subagent("count things", model="google/gemini-2.5-flash"), "42")
            path, payload, timeout = calls[0]
            self.assertEqual(path, "/api/task")
            self.assertEqual(payload, {"message": "count things", "target": "ephemeral",
                                       "wait": True, "model": "google/gemini-2.5-flash"})
            self.assertGreaterEqual(timeout, 600)
            a._mgr = lambda *x, **k: json.dumps({"error": "target 'ephemeral' not allowed"})
            self.assertIn("not allowed", a.t_spawn_subagent("x"))
            self.assertIn("missing", a.t_spawn_subagent("  "))
            # create_task forwards the model too
            calls.clear(); a._mgr = lambda base, path, payload, timeout=30: (calls.append(payload), json.dumps({"id": "1", "status": "pending", "target": "ephemeral"}))[1]
            a.t_create_task("later", model="m/x")
            self.assertEqual(calls[0]["model"], "m/x")
        finally:
            a._mgr = old

    def test_backend_openrouter_default(self):
        g = self._select({})
        self.assertEqual(g["LLM_BACKEND"], "openrouter")
        self.assertEqual(g["LLM_KEY_SECRET"], "OPENROUTER_API_KEY")

    def test_backend_orcarouter_default_url(self):
        g = self._select({"ORCAROUTER_MODEL": "tencent/hy3"})
        self.assertEqual(g["LLM_BACKEND"], "orcarouter")
        self.assertEqual(g["OR_MODEL"], "tencent/hy3")
        self.assertEqual(g["OR_URL"], "https://api.orcarouter.ai/v1/chat/completions")
        self.assertEqual(g["LLM_KEY_SECRET"], "ORCAROUTER_API_KEY")

    def test_backend_orcarouter_selfhost_url(self):
        g = self._select({"ORCAROUTER_MODEL": "x/y", "ORCAROUTER_URL": "http://localhost:8000/v1"})
        self.assertEqual(g["OR_URL"], "http://localhost:8000/v1/chat/completions")

    def test_backend_llama_wins(self):
        g = self._select({"LLAMA_ENDPOINT": "http://h:8080/v1", "LLAMA_MODEL": "qwen",
                          "ORCAROUTER_MODEL": "should-be-ignored"})
        self.assertEqual(g["LLM_BACKEND"], "llama")
        self.assertEqual(g["OR_MODEL"], "qwen")

    # --- Context-Offloader --------------------------------------------------
    def test_offload_roundtrip(self):
        big = "X" * 20000
        out = self.a._finalize_output("http_fetch", big)
        self.assertLess(len(out), len(big))
        self.assertIn('offload_read(id="', out)
        import re
        oid = re.search(r'id="([^"]+)"', out).group(1)
        back = self.a.t_offload_read(id=oid, offset=0, length=25000)
        self.assertIn("XXXX", back)
        self.assertGreaterEqual(len(back), 19000)

    def test_offload_small_passthrough(self):
        self.assertEqual(self.a._finalize_output("bash", "kurz"), "kurz")

    def test_offload_read_missing(self):
        self.assertIn("not found", self.a.t_offload_read(id="gibtsnicht"))

    def test_offload_read_always_enabled(self):
        old = self.a._TOOL_ALLOW
        try:
            self.a._TOOL_ALLOW = {"bash"}          # strikte Allowlist
            self.assertTrue(self.a.tool_enabled("offload_read"))
            self.assertFalse(self.a.tool_enabled("http_fetch"))
        finally:
            self.a._TOOL_ALLOW = old

    # --- Tool-Hook / Guardrails ---------------------------------------------
    def test_http_fetch_html_becomes_readable_text(self):
        """A modern page is 90% markup — hard-truncated raw HTML cut content
        off before it appeared and the model called pages "too complex". The
        conversion must drop scripts/tags, resolve entities, keep link targets."""
        a = self.a
        html = ("<html><head><title>x</title><script>var a=1;</script>"
                "<style>.x{}</style></head><body>\r\n"
                "<div class='nav'><a href='https://firma.de/jobs'>Karriere</a></div>"
                "<h1>Die gr&ouml;&szlig;ten Medizintechnik-Firmen</h1>"
                "<ul><li>Alpha GmbH &amp; Co.</li><li>Beta AG</li></ul>"
                "<p>Umsatz: 3&nbsp;Mio.</p></body></html>")
        t = a._html_to_text(html)
        self.assertNotIn("<", t)                       # no tags survive
        self.assertNotIn("var a=1", t)                 # scripts gone
        self.assertIn("Die größten Medizintechnik-Firmen", t)
        self.assertIn("Alpha GmbH & Co.", t)
        self.assertIn("Beta AG", t)
        self.assertIn("Karriere [https://firma.de/jobs]", t)   # link target kept
        self.assertNotIn("\n\n\n", t)                 # no blank-line runs

    def test_web_search_reports_blocked_backends_instead_of_no_results(self):
        """DDG went behind a bot challenge (HTTP 202 + anomaly page); the old
        tool turned that into "no results" and the model concluded the thing
        searched for does not exist. A dead backend must be NAMED."""
        a = self.a
        old_d, old_b = a._ddg_search, a._bing_search
        try:
            a._ddg_search = lambda q, c: None                # challenge
            a._bing_search = lambda q, c: (_ for _ in ()).throw(OSError("net down"))
            out = a.t_web_search("anything")
            self.assertIn("unavailable", out)
            self.assertIn("duckduckgo: blocked", out)
            self.assertIn("bing", out)
            self.assertIn("NOT an empty result", out)
            # A backend that answers with an EMPTY list is a real empty result.
            a._ddg_search = lambda q, c: []
            self.assertEqual(a.t_web_search("gibberishquery"), "no results")
            # And the fallback chain: DDG blocked, Bing delivers.
            a._ddg_search = lambda q, c: None
            a._bing_search = lambda q, c: [("Titel", "https://x.de", "Schnipsel")]
            out = a.t_web_search("x")
            self.assertIn("Titel", out)
            self.assertIn("https://x.de", out)
        finally:
            a._ddg_search, a._bing_search = old_d, old_b

    def test_bing_redirect_urls_are_decoded(self):
        import base64
        a = self.a
        target = "https://de.wikipedia.org/wiki/Unternehmen"
        b64 = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        href = f"https://www.bing.com/ck/a?!&amp;&amp;p=xyz&amp;u=a1{b64}&amp;ntb=1"
        self.assertEqual(a._bing_real_url(href), target)
        # Without the redirect wrapper the URL passes through untouched.
        self.assertEqual(a._bing_real_url("https://example.org/x"), "https://example.org/x")

    def test_rejected_history_image_is_stripped_and_counted(self):
        """A provider that rejects an image the history has long carried kills
        EVERY later turn (found live: an instance whose memory stayed empty
        because no turn ever reached the tools). The strip helper must remove
        exactly the image parts and leave the text."""
        a = self.a
        hist = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "text", "text": "was ist das?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ]},
            {"role": "assistant", "content": "eine Katze"},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
            ]},
        ]
        n = a._strip_history_images(hist)
        self.assertEqual(n, 2)
        flat = __import__("json").dumps(hist)
        self.assertNotIn("image_url", flat)             # no image parts left
        self.assertIn("was ist das?", flat)             # text survives
        self.assertIn("image removed", flat)
        self.assertEqual(a._strip_history_images(hist), 0)   # idempotent

    def test_offload_preview_outlines_json(self):
        """A head-slice of a big JSON is an unclosed brace of the first record.
        The preview should say WHAT the payload is instead."""
        a = self.a
        rows = [{"id": i, "name": f"row {i}", "value": i * 3.14} for i in range(500)]
        out = __import__("json").dumps({"total": 500, "rows": rows})
        assert len(out) > a.OFFLOAD_MIN, "test payload must trigger offloading"
        got = a._finalize_output("http_fetch", out)
        self.assertIn("[JSON structure]", got)
        self.assertIn("rows", got)                     # the key survives
        self.assertIn("500 items", got)                # the count survives
        self.assertIn("offload_read", got)             # the full text is reachable
        self.assertLess(len(got), a.OFFLOAD_PREVIEW + 400)

    def test_offload_preview_folds_logs_and_keeps_errors(self):
        a = self.a
        noise = "GET /health 200 0.001s"
        lines = [noise] * 800 + ["ERROR: db connection refused"] + [noise] * 800
        out = "\n".join(lines)
        assert len(out) > a.OFFLOAD_MIN
        got = a._finalize_output("bash", out)
        self.assertIn("repeats ×800", got)             # duplicates folded
        self.assertIn("ERROR: db connection refused", got)   # the problem survives
        self.assertIn("1601 lines", got)
        self.assertLess(len(got), a.OFFLOAD_PREVIEW + 400)

    def test_offload_preview_plain_text_stays_head_slice(self):
        a = self.a
        out = ("word " * 20000).strip()
        got = a._finalize_output("read_file", out)
        self.assertTrue(got.startswith("word word"))
        self.assertIn("offload_read", got)

    def test_list_skills_names_only_without_query(self):
        """67 skills × full description cost ~2.5k tokens per call — the bare
        list must stay cheap, the descriptions come via query."""
        a = self.a
        catalog = __import__("json").dumps([
            {"name": "docker", "description": "Docker expert for containers"},
            {"name": "kubernetes", "description": "K8s operations expert"},
            {"name": "git-expert", "description": "Git operations expert"},
        ])
        old = a._mgr_get
        try:
            a._mgr_get = lambda base, path, **k: catalog
            bare = a.t_list_skills()
            self.assertIn("docker", bare)
            self.assertNotIn("containers", bare)       # no descriptions
            hit = a.t_list_skills(query="container")
            self.assertIn("Docker expert", hit)        # description on demand
            self.assertNotIn("git-expert", hit)
            miss = a.t_list_skills(query="quantum")
            self.assertIn("No skill matches", miss)
        finally:
            a._mgr_get = old

    def test_hook_denylist_blocks_rmrf(self):
        allow, reason = self.a._hook_before_tool("bash", {"command": "sudo rm -rf / --no-preserve-root"})
        self.assertFalse(allow)
        self.assertIn("rm -rf /", reason)

    def test_hook_denylist_blocks_forkbomb(self):
        allow, _ = self.a._hook_before_tool("bash", {"command": ":(){:|:&};:"})
        self.assertFalse(allow)

    def test_hook_allows_normal(self):
        allow, _ = self.a._hook_before_tool("bash", {"command": "ls -la"})
        self.assertTrue(allow)

    def test_hitl_default_off(self):
        self.assertFalse(self.a.HITL)   # opt-in, otherwise it blocks nothing

    def test_notify_tool_registered(self):
        self.assertIn("notify", self.a.BUILTIN)

    # --- /model: Laufzeit-Modellwechsel --------------------------------------
    def test_model_switch(self):
        a = self.a
        old = (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET, a.LLM_BACKEND, a.OR_KEY)
        try:
            self.assertIn("Model:", a._set_model("/model"))
            a._set_model("/model orcarouter:foo/bar")
            self.assertEqual(a.OR_MODEL, "foo/bar")
            self.assertIn("orcarouter.ai", a.OR_URL)
            self.assertEqual(a.LLM_KEY_SECRET, "ORCAROUTER_API_KEY")
            a._set_model("/model only-model-id")           # without provider: model only
            self.assertEqual(a.OR_MODEL, "only-model-id")
            self.assertIn("orcarouter.ai", a.OR_URL)       # Backend unveraendert
        finally:
            (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET, a.LLM_BACKEND, a.OR_KEY) = old

    # --- Steering -------------------------------------------------------------
    def test_steering_queue(self):
        a = self.a
        self.assertFalse(a.steer_push("x"))               # idle -> reject
        a._busy[0] = True
        try:
            self.assertTrue(a.steer_push("hold course"))
            hist = []
            self.assertTrue(a._drain_steer(hist))
            self.assertEqual(hist[0]["role"], "user")
            self.assertIn("hold course", hist[0]["content"])
            self.assertIn("[Steering", hist[0]["content"])
            self.assertFalse(a._drain_steer(hist))         # queue empty
        finally:
            a._busy[0] = False

    # --- Prompt-Templates -----------------------------------------------------
    def test_prompt_expansion(self):
        a = self.a
        a._prompts_cache["map"] = {"daily": "Write the daily briefing."}
        a._prompts_cache["ts"] = __import__("time").time()
        self.assertEqual(a._expand_prompt("/daily"), "Write the daily briefing.")
        self.assertEqual(a._expand_prompt("/daily just short"),
                         "Write the daily briefing. just short")
        self.assertEqual(a._expand_prompt("/reset"), "/reset")     # built-in takes precedence
        self.assertEqual(a._expand_prompt("/gibtsnicht"), "/gibtsnicht")
        self.assertEqual(a._expand_prompt("normal text"), "normal text")

    # --- Plugin-Loader ----------------------------------------------------------
    def test_plugin_loader(self):
        a = self.a
        tmp = tempfile.mkdtemp(prefix="e2e-plug-")
        with open(os.path.join(tmp, "echoplug.py"), "w") as fh:
            fh.write('DESC="Echo"\nPARAMS={"t":{"type":"string"}}\nREQUIRED=["t"]\n'
                     'def run(t):\n    return "ECHO:" + t\n')
        with open(os.path.join(tmp, "bash.py"), "w") as fh:      # collision -> ignore
            fh.write('DESC="evil"\ndef run():\n    return "no"\n')
        old_dir = a.PLUGIN_DIR
        try:
            a.PLUGIN_DIR = tmp
            a.load_plugins()
            self.assertIn("echoplug", a.BUILTIN)
            self.assertIn("echoplug", a.PLUGIN_TOOLS)
            self.assertEqual(a.BUILTIN["echoplug"][0]("hi"), "ECHO:hi")
            self.assertNotIn("bash", a.PLUGIN_TOOLS)              # collision blocked
        finally:
            a.PLUGIN_DIR = old_dir
            a.BUILTIN.pop("echoplug", None)
            a.PLUGIN_TOOLS.discard("echoplug")

    # --- Tree-Chat: /branch + /back -------------------------------------------
    def test_branch_and_back(self):
        a = self.a
        old_hist = list(a._history)
        old_chat = a.or_chat
        try:
            a._history[:] = [{"role": "system", "content": "s"},
                             {"role": "user", "content": "main topic"}]
            a.or_chat = lambda msgs, tools, model=None: {"role": "assistant",
                                                         "content": "essence of the follow-up"}
            out = a._branch_open("/branch piper")
            self.assertIn("depth 1", out)
            self.assertEqual(a._branch_depth(), 1)
            a._history.append({"role": "user", "content": "follow-up?"})
            a._history.append({"role": "assistant", "content": "reply in the branch"})
            out = a._branch_close("/back")
            self.assertEqual(a._branch_depth(), 0)
            self.assertIn("main topic", out)
            # branch content gone, sidenote present, origin intact
            joined = " | ".join(str(m.get("content")) for m in a._history)
            self.assertNotIn("reply in the branch", joined)
            self.assertIn(a.NOTE_TAG, joined)
            self.assertIn("main topic", joined)
            # /back without a branch
            self.assertIn("No open", a._branch_close("/back"))
        finally:
            a.or_chat = old_chat
            a._history[:] = old_hist

    def test_branch_drop(self):
        a = self.a
        old_hist = list(a._history)
        try:
            a._history[:] = [{"role": "system", "content": "s"}]
            a._branch_open("/branch x")
            a._history.append({"role": "user", "content": "geheim"})
            a._branch_close("/back drop")
            joined = " | ".join(str(m.get("content")) for m in a._history)
            self.assertNotIn("geheim", joined)
            self.assertNotIn(a.NOTE_TAG, joined)   # spurlos
        finally:
            a._history[:] = old_hist

    # --- Goal-Kommando ------------------------------------------------------
    def test_goal_set_show_off(self):
        try:
            self.assertIn("No goal", self.a._set_goal("/goal show"))
            self.a._set_goal("/goal Antworte knapp.")
            self.assertEqual(self.a._goal, "Antworte knapp.")
            self.a._set_goal("/goal off")
            self.assertIsNone(self.a._goal)
        finally:
            self.a._goal = None

    # --- Summarizing conversation manager -----------------------------------
    def test_summarizing_split(self):
        a = self.a
        old_sum, old_max, old_keep, old_hist = a._summarize, a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT, list(a._history)
        try:
            a._summarize = lambda msgs, prior="": f"MOCK(prior={prior or '-'},n={len(msgs)})"
            a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT = 6, 4
            h = [{"role": "system", "content": a.SYSTEM}]
            for i in range(5):
                h += [{"role": "user", "content": f"f{i}"}, {"role": "assistant", "content": f"a{i}"}]
            a._history[:] = h
            a._trim_history()
            self.assertTrue(a._history[1]["content"].startswith(a.SUMMARY_TAG))
            self.assertEqual(a._history[2]["role"], "user")        # recent an user-Grenze
            self.assertEqual(a._history[-1]["content"], "a4")      # juengste bleibt
        finally:
            a._summarize, a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT = old_sum, old_max, old_keep
            a._history[:] = old_hist

    def test_summarizing_folds_prior(self):
        a = self.a
        seen = {}
        old_sum, old_max, old_keep, old_hist = a._summarize, a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT, list(a._history)
        try:
            def fake_sum(msgs, prior=""):
                seen["prior"] = prior
                return "NEU"
            a._summarize = fake_sum
            a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT = 4, 2
            h = [{"role": "system", "content": a.SYSTEM},
                 {"role": "system", "content": a.SUMMARY_TAG + " ALT"}]
            for i in range(4):
                h += [{"role": "user", "content": f"f{i}"}, {"role": "assistant", "content": f"a{i}"}]
            a._history[:] = h
            a._trim_history()
            self.assertEqual(seen.get("prior"), "ALT")             # alte Zusammenfassung eingefaltet
        finally:
            a._summarize, a.CTX_MAX_MSGS, a.CTX_PRESERVE_RECENT = old_sum, old_max, old_keep
            a._history[:] = old_hist

    # --- Retry / leere-Tools-Fix im Request-Body ----------------------------
    def test_or_chat_omits_empty_tools(self):
        a = self.a
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                   "usage": {}}).encode()

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode())
            return FakeResp()

        orig = a.urllib.request.urlopen
        try:
            a.urllib.request.urlopen = fake_urlopen
            a.or_chat([{"role": "user", "content": "hi"}], [])       # leere Tools
            self.assertNotIn("tools", captured["body"])
            self.assertNotIn("tool_choice", captured["body"])
            a.or_chat([{"role": "user", "content": "hi"}],
                      [{"type": "function", "function": {"name": "x", "parameters": {}}}])
            self.assertIn("tools", captured["body"])
            self.assertEqual(captured["body"]["tool_choice"], "auto")
        finally:
            a.urllib.request.urlopen = orig

    # --- Key-Injection-Proxy (Keys verlassen den Host nie) -------------------
    def test_key_proxy_url_and_no_bearer(self):
        """With KEY_PROXY=1, _llm_url() points at the manager proxy path and
        or_chat sends NO Authorization bearer — otherwise the key would end up
        in the guest request again and the whole detour would be pointless."""
        a = self.a
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"choices": [{"message": {
                    "role": "assistant", "content": "ok"}}], "usage": {}}).encode()

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return FakeResp()

        orig_open, orig_base = a.urllib.request.urlopen, a._manager_base
        old = (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET,
               a.LLM_BACKEND, a.OR_KEY)
        os.environ["KEY_PROXY"] = "1"
        a.OR_KEY = "sk-super-geheim"          # darf NIE im Request auftauchen
        a.LLM_BACKEND = "openrouter"
        try:
            a.urllib.request.urlopen = fake_urlopen
            a._manager_base = lambda: "http://172.30.0.1:8700"
            self.assertEqual(
                a._llm_url(),
                "http://172.30.0.1:8700/api/llm/openrouter/chat/completions")
            a.or_chat([{"role": "user", "content": "hi"}], [])
            self.assertEqual(
                captured["url"],
                "http://172.30.0.1:8700/api/llm/openrouter/chat/completions")
            self.assertNotIn("authorization", captured["headers"])
            self.assertNotIn("sk-super-geheim", json.dumps(captured["headers"]))
            # /model-Wechsel muss im Proxy-Modus die PROXY-URL wechseln
            a._set_model("/model orcarouter:tencent/hy3")
            self.assertEqual(
                a._llm_url(),
                "http://172.30.0.1:8700/api/llm/orcarouter/chat/completions")
        finally:
            os.environ.pop("KEY_PROXY", None)
            a.urllib.request.urlopen, a._manager_base = orig_open, orig_base
            (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET,
             a.LLM_BACKEND, a.OR_KEY) = old

    def test_key_proxy_off_keeps_direct_url(self):
        """Without KEY_PROXY everything stays as before: direct backend URL."""
        a = self.a
        os.environ.pop("KEY_PROXY", None)
        self.assertEqual(a._llm_url(), a.OR_URL)


# ===========================================================================
# OFFLINE: Manager-Funktionen
# ===========================================================================
class ManagerFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load("manager_e2e", MANAGER_PATH)

    def test_resource_stats_shape(self):
        """resource_stats returns per instance size + live fields; an instance
        without a PID counts as not running (live values None)."""
        m = self.m
        old_load = m.load_instances
        m.load_instances = lambda: [{"name": "e2e-res-xyz", "vcpus": 4, "mem_mib": 2048, "config": {}}]
        try:
            r = next(x for x in m.resource_stats() if x["name"] == "e2e-res-xyz")
            self.assertEqual(r["vcpus"], 4)
            self.assertEqual(r["mem_mib"], 2048)
            self.assertFalse(r["running"])
            self.assertIsNone(r["rss_mb"])
            for k in ("cpu_pct", "upper_used_mb", "persist", "name"):
                self.assertIn(k, r)
        finally:
            m.load_instances = old_load

    def test_gateway_strips_noncharacters(self):
        """Layer-A extension (watermarks-remover): Unicode noncharacters and
        permanently reserved default-ignorable code points are removed; normal
        text and emoji are left untouched."""
        import text_unicode as tu
        for cp in (0xFDD0, 0xFFFE, 0x1FFFE, 0x2065, 0xFFF5, 0xE0000):
            out, _st = tu.clean_text("A" + chr(cp) + "B")
            self.assertEqual(out, "AB", "U+%04X not removed" % cp)
        self.assertEqual(tu.clean_text("Hallo Welt")[0], "Hallo Welt")
        self.assertEqual(tu.clean_text("x" + chr(0x2764) + chr(0xFE0F) + "y")[0],
                         "x" + chr(0x2764) + chr(0xFE0F) + "y")

    def test_plugin_hash_pinning(self):
        """Content-hash pinning: upload pins automatically; a direct file change
        -> modified=True; approve re-pins -> modified=False; delete removes the pin."""
        m = self.m
        import tempfile, os
        tmp = tempfile.mkdtemp(prefix="e2e-pin-")
        old_src, old_pins = m.PLUGINS_SRC, m.PLUGIN_PINS_FILE
        m.PLUGINS_SRC = tmp
        m.PLUGIN_PINS_FILE = os.path.join(tmp, ".pins.json")
        try:
            self.assertIsNone(m.plugin_write_py("foo", "DESC='x'\ndef run():\n    return 1\n"))
            lst = {p["name"]: p for p in m.list_plugins()}
            self.assertTrue(lst["foo"]["pinned"])
            self.assertFalse(lst["foo"]["modified"])
            with open(os.path.join(tmp, "foo", "tool.py"), "a") as fh:
                fh.write("# tampered\n")
            lst = {p["name"]: p for p in m.list_plugins()}
            self.assertTrue(lst["foo"]["modified"])          # Manipulation erkannt
            m.plugin_pin("foo")                              # Approve
            lst = {p["name"]: p for p in m.list_plugins()}
            self.assertFalse(lst["foo"]["modified"])
            m.plugin_delete("foo")
            self.assertNotIn("foo", m.load_plugin_pins())    # Pin mit weg
        finally:
            m.PLUGINS_SRC, m.PLUGIN_PINS_FILE = old_src, old_pins

    def test_plugin_files_link_into_vscode(self):
        """Approve must never be blind: each file name links to the file in the
        host's VS Code (code-server deep link: folder + payload openFile with
        the remote authority). Without CODE_URL/CODE_ROOT: no link, no crash."""
        m = self.m
        old = m.CODE_URL, m.CODE_ROOT
        try:
            m.CODE_URL, m.CODE_ROOT = "http://192.168.0.10:8443/", "/home/coder/firecracker"
            u = m.plugin_code_link("folder", "greeter", "lib/h.py")
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            self.assertTrue(u.startswith("http://192.168.0.10:8443/?"))
            self.assertEqual(q["folder"], ["/home/coder/firecracker"])
            self.assertEqual(json.loads(q["payload"][0]),
                             [["openFile", "vscode-remote://192.168.0.10:8443/home/coder/firecracker/plugins/greeter/lib/h.py"]])
            u1 = m.plugin_code_link("file", "dice", "dice.py")
            self.assertIn("/home/coder/firecracker/plugins/dice.py", json.loads(
                urllib.parse.parse_qs(urllib.parse.urlsplit(u1).query)["payload"][0])[0][1])
            m.CODE_ROOT = ""
            self.assertEqual(m.plugin_code_link("folder", "greeter", "tool.py"), "")
            # list_plugins carries the links per file
            tmp = tempfile.mkdtemp(prefix="e2e-pluglink-")
            old_src, old_pins = m.PLUGINS_SRC, m.PLUGIN_PINS_FILE
            m.PLUGINS_SRC, m.PLUGIN_PINS_FILE = tmp, os.path.join(tmp, ".pins.json")
            try:
                m.plugin_write_py("foo", "DESC='x'\n")
                self.assertEqual(m.list_plugins()[0]["links"], {"tool.py": ""})
                m.CODE_ROOT = "/home/coder/firecracker"
                self.assertIn("plugins/foo/tool.py", urllib.parse.unquote(
                    m.list_plugins()[0]["links"]["tool.py"]))
            finally:
                m.PLUGINS_SRC, m.PLUGIN_PINS_FILE = old_src, old_pins
        finally:
            m.CODE_URL, m.CODE_ROOT = old
        # the old in-manager viewer route is gone: guests never had it, admins use VS Code
        self.assertNotIn("/api/plugins/", {p for _, _, p, _ in m.ROUTER.inventory()})

    def test_set_instance_tools_roundtrip(self):
        """Saving policy tools: a subset persists (tools_all=False), ALL tools
        removes the field (tools_all=True), unknown names are filtered out.
        Regression for 'after saving, all tools are active again'."""
        m = self.m
        import tempfile, os
        tmp = tempfile.mkdtemp(prefix="e2e-tools-")
        with open(os.path.join(tmp, "toolinst.json"), "w") as fh:
            json.dump({"name": "toolinst", "template": "openrouter", "config": {}}, fh)
        old_dir, old_load, old_run = m.INST_DIR, m.load_instances, m.is_running
        m.INST_DIR = tmp
        m.load_instances = lambda: [_readj(os.path.join(tmp, "toolinst.json"))]
        m.is_running = lambda inst: False
        try:
            picks = sorted(m.AGENT_TOOL_NAMES)[:3]
            m.set_instance_tools("toolinst", picks)
            cfg = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertEqual(set(cfg["AGENT_TOOLS"].split(",")), set(picks))   # Subset bleibt
            self.assertFalse(m.effective_policy(_readj(os.path.join(tmp, "toolinst.json")))["tools_all"])

            m.set_instance_tools("toolinst", list(m.AGENT_TOOL_NAMES))
            cfg2 = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertNotIn("AGENT_TOOLS", cfg2)                              # alle -> Feld raus
            self.assertTrue(m.effective_policy(_readj(os.path.join(tmp, "toolinst.json")))["tools_all"])

            m.set_instance_tools("toolinst", ["kein_tool", picks[0]])
            cfg3 = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertEqual(cfg3["AGENT_TOOLS"], picks[0])                    # unbekannte gefiltert
        finally:
            m.INST_DIR, m.load_instances, m.is_running = old_dir, old_load, old_run

    def test_plugin_zip_and_slip_guard(self):
        """A multi-file zip lands in the tool folder; a ../ path (zip-slip) must NOT
        be extracted outside; a zip without an entry file is rejected."""
        m = self.m
        import tempfile, os, io, zipfile
        tmp = tempfile.mkdtemp(prefix="e2e-plug-")
        old = m.PLUGINS_SRC
        m.PLUGINS_SRC = tmp
        try:
            buf = io.BytesIO(); z = zipfile.ZipFile(buf, "w")
            z.writestr("tool.py", "DESC='x'\nPARAMS={}\nREQUIRED=[]\ndef run():\n    return 1\n")
            z.writestr("helper.py", "x=1\n"); z.close()
            self.assertIsNone(m.plugin_write_zip("mytool", buf.getvalue()))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mytool", "tool.py")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mytool", "helper.py")))
            b2 = io.BytesIO(); z2 = zipfile.ZipFile(b2, "w")
            z2.writestr("tool.py", "def run():\n    return 1\n")
            z2.writestr("../evil.py", "boom\n"); z2.close()
            m.plugin_write_zip("slip", b2.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.py")))
            b3 = io.BytesIO(); z3 = zipfile.ZipFile(b3, "w"); z3.writestr("readme.txt", "x"); z3.close()
            self.assertIsNotNone(m.plugin_write_zip("noentry", b3.getvalue()))
        finally:
            m.PLUGINS_SRC = old

    def test_playbook_add_imports_present(self):
        """Regression: mgr/rules.pb_add uses uuid+time -> they must be imported,
        otherwise /api/playbook-add crashes and the agent sees RemoteDisconnected."""
        import mgr.rules as rules, tempfile, os
        tmp = tempfile.mkdtemp(prefix="e2e-pb-")
        old = rules.PLAYBOOKS_FILE
        rules.PLAYBOOKS_FILE = os.path.join(tmp, "pb.json")
        try:
            pid = rules.pb_add("inst", "a rule")         # NameError on missing import
            self.assertTrue(pid and pid != "exists")
            self.assertEqual(len(rules.pb_list("inst")), 1)
        finally:
            rules.PLAYBOOKS_FILE = old

    def test_merge_chats_tombstones(self):
        """A deletion propagates and does not resurrect — except on a genuine,
        NEWER edit (then the tombstone is dropped)."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-tomb-")
        oc, ot = m.CHATS_FILE, m.TOMBSTONES_FILE
        m.CHATS_FILE = os.path.join(tmp, "chats.json")
        m.TOMBSTONES_FILE = os.path.join(tmp, "tombs.json")
        try:
            NOW = 1_800_000_000_000
            has = lambda: any(c.get("id") == "x" for c in m.load_chats())
            m.merge_chats([{"id": "x", "updatedAt": NOW - 5000,
                            "messages": [{"user": True, "text": "hi"}]}])
            self.assertTrue(has())
            m.merge_chats({"chats": [], "tombstones": {"x": NOW}})       # delete
            self.assertFalse(has())
            self.assertIn("x", m.load_tombstones())
            m.merge_chats([{"id": "x", "updatedAt": NOW - 1000,          # Re-Push alt
                            "messages": [{"user": True, "text": "hi"}]}])
            self.assertFalse(has())                                      # bleibt weg
            m.merge_chats([{"id": "x", "updatedAt": NOW + 9000,          # echte Bearbeitung
                            "messages": [{"user": True, "text": "edit"}]}])
            self.assertTrue(has())                                       # aufersteht
            self.assertNotIn("x", m.load_tombstones())                   # Tombstone weg
        finally:
            m.CHATS_FILE, m.TOMBSTONES_FILE = oc, ot

    def test_provider_switch_sets_and_clears_keys(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-inst-")
        inst = {"name": "e2e-switch", "config": {"OPENROUTER_MODEL": "google/gemini-2.5-flash"}}
        with open(os.path.join(tmp, "e2e-switch.json"), "w") as fh:
            json.dump(inst, fh)
        old_dir, old_load = m.INST_DIR, m.load_instances
        try:
            m.INST_DIR = tmp
            m.load_instances = lambda: [_readj(os.path.join(tmp, "e2e-switch.json"))]
            msg = m.set_model("e2e-switch", "orcarouter:tencent/hy3")
            cfg = _readj(os.path.join(tmp, "e2e-switch.json"))["config"]
            self.assertEqual(cfg.get("ORCAROUTER_MODEL"), "tencent/hy3")
            self.assertNotIn("OPENROUTER_MODEL", cfg)               # anderer Provider entfernt
        finally:
            m.INST_DIR, m.load_instances = old_dir, old_load

    def test_provider_switch_ignores_free_suffix(self):
        """':free' model variants must NOT be read as a provider."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-inst2-")
        with open(os.path.join(tmp, "e2e-free.json"), "w") as fh:
            json.dump({"name": "e2e-free", "config": {"OPENROUTER_MODEL": "x"}}, fh)
        old_dir, old_load = m.INST_DIR, m.load_instances
        try:
            m.INST_DIR = tmp
            m.load_instances = lambda: [_readj(os.path.join(tmp, "e2e-free.json"))]
            m.set_model("e2e-free", "mistralai/mistral-7b-instruct:free")
            cfg = _readj(os.path.join(tmp, "e2e-free.json"))["config"]
            self.assertEqual(cfg.get("OPENROUTER_MODEL"), "mistralai/mistral-7b-instruct:free")
            self.assertNotIn("ORCAROUTER_MODEL", cfg)
        finally:
            m.INST_DIR, m.load_instances = old_dir, old_load

    def test_hitl_lifecycle(self):
        m = self.m
        import mgr.signal as sigmod
        old_send = sigmod.signal_send
        try:
            sigmod.signal_send = lambda text, to=None: (True, "sent")
            hid = m.hitl_create("orchestrator", "bash", "rm foo")
            self.assertIsNotNone(hid)
            self.assertEqual(m.hitl_status(hid), "pending")
            self.assertTrue(m.hitl_resolve(hid, True))
            self.assertEqual(m.hitl_status(hid), "approved")
            self.assertFalse(m.hitl_resolve(hid, True))     # not resolvable twice
            self.assertEqual(m.hitl_status("nonexistent"), "unknown")
        finally:
            sigmod.signal_send = old_send

    def test_hitl_no_signal_no_block(self):
        """If the Signal send fails (no recipient), hitl_create returns None
        -> the agent then does not block."""
        m = self.m
        import mgr.signal as sigmod
        old_send = sigmod.signal_send
        try:
            sigmod.signal_send = lambda text, to=None: (False, "no recipient")
            self.assertIsNone(m.hitl_create("x", "bash", "y"))
        finally:
            sigmod.signal_send = old_send

    def test_katfs_zip_recursive(self):
        m = self.m
        tree = {".": [{"name": "a.txt", "dir": False}, {"name": "sub", "dir": True}],
                "sub": [{"name": "b.txt", "dir": False}]}
        files = {"a.txt": b"AAA", "sub/b.txt": b"BBB"}

        def fake_proxy(op, share, path, recursive=False, body=None):
            if op == "ls":
                return 200, "application/json", json.dumps({"entries": tree.get(path or ".", [])}).encode()
            if op == "read":
                return 200, "application/octet-stream", files[path]
            raise AssertionError(op)

        import mgr.katfs as kmod
        old = kmod.katfs_proxy_fs
        try:
            kmod.katfs_proxy_fs = fake_proxy
            data, stats = m.katfs_zip("share1", ".")
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = sorted(zf.namelist())
            self.assertEqual(names, ["a.txt", "sub/b.txt"])
            self.assertEqual(zf.read("sub/b.txt"), b"BBB")
            self.assertEqual(stats["files"], 2)
        finally:
            kmod.katfs_proxy_fs = old

    def test_audit_records_error_text_and_result(self):
        """ok alone cannot distinguish a healthy call from one that failed
        politely — the trail now carries the error text, a result excerpt and
        the turn id (that is what makes a saddler review possible at all)."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-audit-")
        old = m.AUDIT_DIR
        try:
            m.AUDIT_DIR = tmp
            m.audit_append("inst-a", "http_fetch", "https://x.example", False,
                           err="⚠️ HTTP 403: blocked", turn="t1234")
            m.audit_append("inst-a", "web_search", "cronn", True,
                           result="1. cronn GmbH …", turn="t1234")
            recs = m.audit_read("inst-a")     # newest first
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[1]["err"], "⚠️ HTTP 403: blocked")
            self.assertNotIn("result", recs[1])
            self.assertEqual(recs[0]["result"], "1. cronn GmbH …")
            self.assertEqual({r["turn"] for r in recs}, {"t1234"})
        finally:
            m.AUDIT_DIR = old

    def test_ha_alias_learns_via_fake_websocket(self):
        """learn_alias speaks the HA WebSocket protocol: handshake -> auth ->
        read current aliases -> append -> update. Verified against a fake HA
        server on a real loopback socket (frames masked from us, unmasked from
        it), so the framing itself is exercised, not mocked away."""
        import threading, struct as _st, socket as _sock, mgr.haalias as ha

        def ws_recv(conn, buf):
            def need(n):
                while len(buf) < n:
                    buf.extend(conn.recv(4096))
            need(2)
            masked = buf[1] & 0x80
            ln = buf[1] & 0x7f
            i = 2
            if ln == 126:
                need(4); ln = _st.unpack(">H", bytes(buf[2:4]))[0]; i = 4
            need(i + (4 if masked else 0) + ln)
            if masked:
                m = bytes(buf[i:i + 4]); i += 4
                data = bytes(buf[i + k] ^ m[k % 4] for k in range(ln))
            else:
                data = bytes(buf[i:i + ln])
            del buf[:i + ln]
            return json.loads(data)

        def ws_send(conn, obj):
            d = json.dumps(obj).encode(); ln = len(d)
            hdr = bytearray([0x81])
            if ln < 126:
                hdr.append(ln)
            else:
                hdr += bytes([126]) + _st.pack(">H", ln)
            conn.sendall(bytes(hdr) + d)   # server frames unmasked

        srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        captured = {}

        def fake_ha():
            conn, _ = srv.accept()
            buf = bytearray()
            hs = b""
            while b"\r\n\r\n" not in hs:
                hs += conn.recv(4096)
            import hashlib as _h, base64 as _b
            key = [l.split(": ", 1)[1] for l in hs.decode().split("\r\n")
                   if l.lower().startswith("sec-websocket-key")][0]
            acc = _b.b64encode(_h.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                                       .encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n").encode())
            ws_send(conn, {"type": "auth_required"})
            auth = ws_recv(conn, buf); captured["token"] = auth.get("access_token")
            ws_send(conn, {"type": "auth_ok"})
            get = ws_recv(conn, buf); captured["get"] = get
            ws_send(conn, {"id": get["id"], "success": True,
                           "result": {"aliases": ["schon da"]}})
            upd = ws_recv(conn, buf); captured["upd"] = upd
            ws_send(conn, {"id": upd["id"], "success": True,
                           "result": {"entity_entry": {"aliases": upd["aliases"]}}})
            conn.close()

        t = threading.Thread(target=fake_ha, daemon=True); t.start()
        old_tgt, old_tok = ha.ha_ws_target, ha.ha_token
        try:
            ha.configure(lambda: ("127.0.0.1", port), lambda: "tok-123")
            msg = ha.learn_alias("Gartenhaus denke rechts", "light.gartenhaus_decke_rechts")
        finally:
            ha.configure(old_tgt, old_tok)
            srv.close()
        t.join(timeout=5)
        self.assertEqual(captured["token"], "tok-123")
        self.assertEqual(captured["get"]["entity_id"], "light.gartenhaus_decke_rechts")
        self.assertEqual(captured["upd"]["aliases"], ["schon da", "Gartenhaus denke rechts"])
        self.assertIn("learned", msg)

    def test_ha_alias_rejects_bad_input(self):
        import mgr.haalias as ha
        self.assertIn("error", ha.learn_alias("", "light.x"))
        self.assertIn("error", ha.learn_alias("Wort", "noentityid"))

    def test_ha_control_matches_exact_area_and_fuzzy(self):
        """control() picks the right target server-side: a strong single match
        beats the area (even when it contains the area word), an area name plus
        a group cue switches the whole room, and a fuzzy single hit learns the
        alias. HA REST/WS are stubbed — the MATCHING logic is what's tested."""
        import mgr.haalias as ha
        idx = {"areas": {"gh": "Gartenhaus"}, "entities": [
            {"entity_id": "light.gartenhaus_decke_rechts",
             "names": ["Gartenhaus Decke rechts"], "area_id": "gh"},
            {"entity_id": "light.gartenhaus_decke_links",
             "names": ["Gartenhaus Decke links"], "area_id": "gh"},
            {"entity_id": "light.kitchen", "names": ["Küche"], "area_id": "k"}]}
        calls, learned = [], []
        old = (ha._entity_index, ha._rest, ha.learn_alias)
        try:
            ha._entity_index = lambda: idx
            ha._rest = lambda path, payload=None: calls.append((path, payload))
            ha.learn_alias = lambda spoken, eid: learned.append((spoken, eid)) or "learned"

            # exact -> single lamp, no learning
            calls.clear(); learned.clear()
            r = ha.control("Gartenhaus Decke rechts", "on")
            self.assertIn("turn_on", calls[0][0])
            self.assertEqual(calls[0][1]["entity_id"], "light.gartenhaus_decke_rechts")
            self.assertEqual(learned, [])

            # mishearing -> strong fuzzy single, learns alias (NOT the area)
            calls.clear(); learned.clear()
            r = ha.control("Gartenhaus Tecke rechts", "off")
            self.assertEqual(calls[0][1]["entity_id"], "light.gartenhaus_decke_rechts")
            self.assertEqual(learned, [("Gartenhaus Tecke rechts", "light.gartenhaus_decke_rechts")])

            # area + cue -> whole room (list of both lights)
            calls.clear(); learned.clear()
            r = ha.control("Licht im Gartenhaus", "off")
            self.assertEqual(sorted(calls[0][1]["entity_id"]),
                             ["light.gartenhaus_decke_links", "light.gartenhaus_decke_rechts"])

            # 2026-09-08: "Gartenhauslicht an" scored 0.76 against the relay
            # switch.gartenhaus_switch_l1 and switched THAT. Area + cue must
            # beat a merely good fuzzy hit; a near-exact one still wins.
            idx["entities"].append({"entity_id": "switch.gartenhaus_switch_l1",
                                    "names": ["gartenhaus_switch L1"], "area_id": "gh"})
            calls.clear(); learned.clear()
            r = ha.control("Gartenhauslicht", "on")
            self.assertEqual(sorted(calls[0][1]["entity_id"]),
                             ["light.gartenhaus_decke_links", "light.gartenhaus_decke_rechts"], r)
            self.assertEqual(learned, [])
            calls.clear(); learned.clear()
            r = ha.control("Gartenhaus Tecke rechts", "on")      # near-exact still single
            self.assertEqual(calls[0][1]["entity_id"], "light.gartenhaus_decke_rechts")

            # nonsense -> no switch
            calls.clear()
            r = ha.control("völliger Unsinn xyz", "on")
            self.assertIn("error", r)
            self.assertEqual(calls, [])
        finally:
            ha._entity_index, ha._rest, ha.learn_alias = old

    def test_ha_control_validates_action(self):
        import mgr.haalias as ha
        self.assertIn("error", ha.control("Licht", "blink"))
        self.assertIn("error", ha.control("", "on"))

    def test_saddler_digest_groups_and_reflects(self):
        """Failures grouped by error SHAPE (digits/urls normalised), current
        week next to the previous one — the drop after a patch is the
        reflection step."""
        import mgr.saddler as sad
        tmp = tempfile.mkdtemp(prefix="e2e-sad-")
        old = sad.AUDIT_DIR
        now = int(time.time())
        try:
            sad.AUDIT_DIR = tmp
            with open(os.path.join(tmp, "aiagent.jsonl"), "w") as fh:
                for ts, ok, err in (
                        (now - 3600, False, "⚠️ HTTP 403: https://a.example/x blocked"),
                        (now - 7200, False, "⚠️ HTTP 403: https://b.example/y blocked"),
                        (now - 8 * 86400, False, "⚠️ HTTP 403: https://c.example/z blocked"),
                        (now - 3600, True, "")):
                    fh.write(json.dumps({"ts": ts, "tool": "http_fetch",
                                         "target": "t", "ok": ok, "err": err}) + "\n")
            d = sad.digest(days=7)
            self.assertEqual(d["totals"]["cur_failed"], 2)
            self.assertEqual(d["totals"]["prev_failed"], 1)
            self.assertEqual(len(d["groups"]), 1, "same error shape must be ONE group")
            g = d["groups"][0]
            self.assertEqual((g["cur"], g["prev"]), (2, 1))
            self.assertIn("<url>", g["error"])
            txt = sad.render(d)
            self.assertIn("2x (1x)", txt)
            self.assertIn("http_fetch", txt)
        finally:
            sad.AUDIT_DIR = old

    def test_websearch_backend_order_and_key_stays_home(self):
        """Brave first WHEN the key is set; without it the reason is named.
        The key itself never leaves the manager — the agents only see results."""
        from mgr import websearch as ws
        old_get, old_ddg, old_bing = ws.get_setting, ws._ddg, ws._bing
        try:
            ws._ddg = lambda q, c: None                       # challenge
            ws._bing = lambda q, c: [("BingTitel", "https://b.example", "s")]
            # Without a key: brave is skipped WITH the reason, bing delivers.
            ws.get_setting = lambda k: ""
            out = ws.web_search("x", 3)
            self.assertIn("BingTitel", out)
            # All dead -> the error names every backend and the why.
            ws._bing = lambda q, c: (_ for _ in ()).throw(OSError("down"))
            out = ws.web_search("x", 3)
            self.assertIn("brave: no API key", out)
            self.assertIn("duckduckgo: blocked", out)
            self.assertIn("NOT an empty result", out)
            # With a key, brave's results win outright.
            ws.get_setting = lambda k: "fake-key" if k == "BRAVE_API_KEY" else ""
            old_brave = ws._brave
            ws._brave = lambda q, c: [("BraveTitel", "https://brave.example", "sn")]
            try:
                out = ws.web_search("x", 3)
            finally:
                ws._brave = old_brave
            self.assertIn("BraveTitel", out)
        finally:
            ws.get_setting, ws._ddg, ws._bing = old_get, old_ddg, old_bing

    def test_websearch_bing_redirects_decoded(self):
        import base64
        from mgr.websearch import bing_real_url
        target = "https://de.wikipedia.org/wiki/Unternehmen"
        b64 = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        self.assertEqual(bing_real_url(
            f"https://www.bing.com/ck/a?!&amp;&amp;p=x&amp;u=a1{b64}&amp;ntb=1"), target)
        self.assertEqual(bing_real_url("https://example.org/y"), "https://example.org/y")

    def test_extract_docx(self):
        """DOCX is a ZIP of XML — built in the test, no fixtures on disk."""
        import io, zipfile
        from mgr.extract import extract_document
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        doc = (f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
               f'<w:p><w:r><w:t>Erste Zeile mit Umlauten: äöü.</w:t></w:r></w:p>'
               f'<w:p><w:r><w:t>Zweiter</w:t></w:r><w:r><w:t xml:space="preserve"> Absatz.</w:t></w:r></w:p>'
               f'</w:body></w:document>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", doc)
        text, note = extract_document("brief.docx", buf.getvalue())
        self.assertIn("Erste Zeile mit Umlauten: äöü.", text)
        self.assertIn("Zweiter Absatz.", text)          # runs joined, paragraphs split
        self.assertEqual(text.splitlines()[0], "Erste Zeile mit Umlauten: äöü.")
        self.assertEqual(note, "")

    def test_extract_pdf_builtin(self):
        """A minimal Flate-compressed PDF, built in the test. Covers the
        fallback path used when the host has no pdftotext."""
        import zlib
        from mgr import extract as ex
        content = zlib.compress(
            b"BT /F1 12 Tf (Hello from a ) Tj (tiny PDF.) Tj T* "
            b"[(Second) ( line) (.)] TJ ET")
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(content)).encode()
               + b" /Filter /FlateDecode >>\nstream\n" + content
               + b"\nendstream\nendobj\ntrailer\n<<>>\n%%EOF")
        old_which = ex.shutil.which
        try:
            ex.shutil.which = lambda _n: None          # force the builtin path
            text, _ = ex.extract_document("doc.pdf", pdf)
        finally:
            ex.shutil.which = old_which
        self.assertIn("Hello from a tiny PDF.", text)
        self.assertIn("Second line.", text)

    @unittest.skipUnless(
        __import__("shutil").which("docker") and __import__("subprocess").run(
            ["docker", "image", "inspect", "kaim56-pdftotext"],
            capture_output=True).returncode == 0,
        "kaim56-pdftotext image not available")
    def test_extract_pdf_via_docker_fallback(self):
        """The host has no pdftotext; the poppler container must cover what the
        built-in extractor cannot (CID/subset fonts — the case a user hit)."""
        import zlib
        from mgr import extract as ex
        content = zlib.compress(b"BT /F1 12 Tf (Container weg funktioniert.) Tj ET")
        pdf = (b"%PDF-1.4\n"
               b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
               b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
               b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
               b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
               b"4 0 obj\n<< /Length " + str(len(content)).encode()
               + b" /Filter /FlateDecode >>\nstream\n" + content + b"\nendstream\nendobj\n"
               b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
               b"trailer\n<< /Root 1 0 R >>\n%%EOF")
        old_which, old_builtin = ex.shutil.which, ex._pdf_text_builtin
        old_probe = ex._docker_image_ok
        def no_pdftotext(name):
            return None if name == "pdftotext" else old_which(name)
        try:
            ex.shutil.which = no_pdftotext
            ex._pdf_text_builtin = lambda d: ""       # builtin "cannot" -> docker must
            ex._docker_image_ok = None                # re-probe with real which()
            ex.shutil.which = old_which               # probe needs real docker path
            ex.shutil.which = no_pdftotext
            text, _ = ex.extract_document("cid.pdf", pdf)
        finally:
            ex.shutil.which, ex._pdf_text_builtin = old_which, old_builtin
            ex._docker_image_ok = old_probe
        self.assertIn("Container weg funktioniert.", text)

    def test_extract_refuses_garbage_instead_of_feeding_it(self):
        """CID-font PDFs decode to noise — the caller must get an error, not
        gibberish that quietly poisons the model's context."""
        import zlib
        from mgr import extract as ex
        noise = bytes(range(1, 32)) * 40               # unprintable soup
        content = zlib.compress(b"BT (" + noise.replace(b"(", b"").replace(b")", b"")
                                .replace(b"\\", b"") + b") Tj ET")
        pdf = (b"%PDF-1.4\nstream\n" + content + b"\nendstream\n%%EOF")
        old_which = ex.shutil.which
        try:
            ex.shutil.which = lambda _n: None
            with self.assertRaises(ValueError):
                ex.extract_document("scan.pdf", pdf)
        finally:
            ex.shutil.which = old_which

    def test_extract_plain_and_unsupported(self):
        from mgr.extract import extract_document
        text, _ = extract_document("notes.md", "# Titel\nInhalt äöü".encode())
        self.assertIn("Inhalt äöü", text)
        with self.assertRaises(ValueError):
            extract_document("video.mp4", b"\x00\x01\x02")
        with self.assertRaises(ValueError):
            extract_document("empty.txt", b"   ")

    def test_extract_caps_huge_documents(self):
        from mgr import extract as ex
        text, note = ex.extract_document("big.txt", (b"x" * (ex.MAX_CHARS + 500)))
        self.assertEqual(len(text), ex.MAX_CHARS)
        self.assertIn("truncated", note)

    def test_no_route_is_shadowed_by_an_earlier_prefix(self):
        """Guard for the if-chain: a prefix branch standing BEFORE an exact
        branch swallows it, and the exact branch becomes dead code. That has
        bitten twice (/api/skills/ vs /api/skills). Anything migrated into the
        router is immune by construction; this covers what is still a chain."""
        from mgr.routes import source_routes, shadowed
        src = open(MANAGER_PATH).read()
        get_src = src[src.index("    def _do_GET(self):"):src.index("    def _do_POST(self):")]
        post_src = src[src.index("    def _do_POST(self):"):]
        for name, part in (("GET", get_src), ("POST", post_src)):
            bad = shadowed(source_routes(part))
            self.assertEqual(bad, [], f"{name}: dead branches behind a prefix: {bad}")

    def test_router_prefers_exact_over_prefix(self):
        """The property the chain could not guarantee: registration order does
        not decide who answers."""
        from mgr.routes import Router
        r = Router()
        r.add("GET", "/api/skills/", lambda h: "prefix", prefix=True)   # first!
        r.add("GET", "/api/skills", lambda h: "exact")
        r.add("GET", "/api/skills/deep/", lambda h: "deeper", prefix=True)
        self.assertEqual(r.resolve("GET", "/api/skills")[0](None), "exact")
        self.assertEqual(r.resolve("GET", "/api/skills?x=1")[0](None), "exact")
        self.assertEqual(r.resolve("GET", "/api/skills/docker")[0](None), "prefix")
        # Longest prefix wins, whatever the order of registration.
        self.assertEqual(r.resolve("GET", "/api/skills/deep/x")[0](None), "deeper")
        self.assertIsNone(r.resolve("GET", "/nope"))
        self.assertIsNone(r.resolve("POST", "/api/skills"))     # method matters

    def test_router_refuses_duplicate_routes(self):
        from mgr.routes import Router
        r = Router()
        r.add("GET", "/x", lambda h: 1)
        with self.assertRaises(ValueError):
            r.add("GET", "/x", lambda h: 2)
        r.add("GET", "/y/", lambda h: 1, prefix=True)
        with self.assertRaises(ValueError):
            r.add("GET", "/y/", lambda h: 2, prefix=True)

    def test_router_inventory_records_who_may_call(self):
        """The inventory is what makes an access audit a loop instead of a
        reading exercise: every route says whether guests may call it."""
        m = self.m
        inv = m.ROUTER.inventory()
        self.assertTrue(inv, "the router should carry routes")
        for method, kind, path, admin in inv:
            self.assertIn(method, ("GET", "POST"))
            self.assertIn(kind, ("exact", "prefix"))
            self.assertTrue(path.startswith("/"))
            self.assertIsInstance(admin, bool)
        by_path = {p: admin for _, _, p, admin in inv}
        # Settings once served the API keys in plain text — guests must not see it.
        self.assertTrue(by_path["/api/settings"], "/api/settings must stay admin-only")
        self.assertTrue(by_path["/api/instances"])
        # The agents need these, so they are deliberately open to guests.
        self.assertFalse(by_path["/api/skills"])
        self.assertFalse(by_path["/api/personas"])

    def test_skills_page_carries_no_contents(self):
        """Regression: the page inlined the COMPLETE skills.json. With the
        imported catalog (~870 KB) that would ship on every page load — only
        name + description belong in the page, the body comes from
        GET /api/skills/<name> when editing."""
        m = self.m
        marker = "SKILL-BODY-MARKER-DO-NOT-INLINE"
        old = m.load_skills
        try:
            m.load_skills = lambda: [{"name": "e2e-skill", "description": "kurz",
                                      "content": marker + " x" * 5000}]
            page = m.render()
            self.assertIn("e2e-skill", page)          # name/description are in
            self.assertIn("kurz", page)
            self.assertNotIn(marker, page)            # the body is NOT
        finally:
            m.load_skills = old

    def test_footer_code_link_optional(self):
        """The editor link is host-specific (site.json CODE_URL): set = link in
        the footer, unset = no placeholder left over in the page."""
        m = self.m
        old = m.CODE_URL
        try:
            m.CODE_URL = "http://example.invalid:8443/"
            page = m.render()
            self.assertIn('href="http://example.invalid:8443/"', page)
            self.assertIn('rel="noopener noreferrer"', page)
            m.CODE_URL = ""
            page = m.render()
            self.assertNotIn("__CODE_LINK__", page)
        finally:
            m.CODE_URL = old

    def test_tool_catalog_matches_agent(self):
        """Drift guard: every tool in the agent (BUILTIN) must be in the manager
        catalog (AGENT_TOOLS_CATALOG) — otherwise it is missing from the create
        form and a tool allowlist blocks it silently (happened with mission_start
        and offload_read). And vice versa: no catalog entry without a real tool."""
        m = self.m
        a = _load("agent_cat_e2e", AGENT_PATH,
                  {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-cat-"),
                   "OPENROUTER_API_KEY": "dummy"})
        agent_tools = set(a.BUILTIN.keys())
        catalog = set(m.AGENT_TOOL_NAMES)
        missing_in_catalog = agent_tools - catalog
        self.assertFalse(missing_in_catalog,
                         f"tools in the agent but not in the manager catalog: {sorted(missing_in_catalog)}")
        ghost_in_catalog = catalog - agent_tools
        self.assertFalse(ghost_in_catalog,
                         f"catalog entries without a real agent tool: {sorted(ghost_in_catalog)}")

    def test_provider_model_key_covers_all(self):
        m = self.m
        for k in m.PROVIDER_MODEL_KEY.values():
            self.assertIn(k, m.MODEL_KEYS)

    def test_llm_proxy_route_registered(self):
        """Injection gateway: the path must be in the guest allowlist (otherwise
        403 for the VM), the upstreams must match the secret names, and the
        settings toggle must appear in the schema."""
        m = self.m
        self.assertIn("/api/llm/", m.GUEST_POST_PREFIXES)
        self.assertEqual(set(m.LLM_PROXY_UPSTREAMS), {"openrouter", "orcarouter"})
        for url, keyname in m.LLM_PROXY_UPSTREAMS.values():
            self.assertTrue(url.endswith("/chat/completions"), url)
            self.assertIn(keyname, m.SECRET_PARAMS)
        self.assertIn("LLM_KEY_PROXY", [s["key"] for s in m.SETTINGS_SCHEMA])

    def test_overlay_upper_lifecycle(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-ov-")
        old_run, old_inst = m.RUN_DIR, m.INST_DIR
        try:
            m.RUN_DIR = tmp; m.INST_DIR = tmp
            inst = {"name": "e2e-ov", "rootfs": "instances/openrouter-rootfs.ext4"}
            # Wegwerf-Upper landet in RUN_DIR
            self.assertTrue(m.upper_path(inst).startswith(tmp))
            self.assertIn(".upper.ext4", m.upper_path(inst))
            # Persistenter Upper in INST_DIR mit anderem Namen
            inst["persist_disk"] = True
            self.assertIn("-upper.ext4", m.upper_path(inst))
            p = m.make_upper(inst)
            self.assertTrue(os.path.exists(p))
            size1 = os.path.getsize(p)
            self.assertEqual(size1, m.UPPER_PERSIST_SIZE_MB * 1024 * 1024)
            # persist: second call uses the existing file (no reset)
            with open(p, "r+b") as fh:
                fh.seek(0); marker = fh.read(4)
            self.assertEqual(m.make_upper(inst), p)
        finally:
            m.RUN_DIR, m.INST_DIR = old_run, old_inst

    def test_overlay_bootarg_in_config(self):
        m = self.m
        inst = next((i for i in m.load_instances()
                     if i.get("rootfs") in m.OVERLAY_ROOTFS), None)
        if not inst:
            self.skipTest("no overlay instance available")
        old_mk = m.make_upper
        try:
            m.make_upper = lambda i: "/tmp/fake-upper.ext4"   # no real mkfs in the test
            cfg = m.gen_config(inst)
        finally:
            m.make_upper = old_mk
        self.assertIn("fc_upper=/dev/vd", cfg["boot-source"]["boot_args"])
        root = next(d for d in cfg["drives"] if d["drive_id"] == "rootfs")
        self.assertTrue(root["is_read_only"], "Basis muss read-only sein")
        self.assertEqual(cfg["drives"][-1]["drive_id"], "upper")

    def test_mission_lifecycle(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi-")
        import mgr.missions as mmod
        old_file = mmod.MISSIONS_FILE
        old_notify = mmod.notify_add
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")   # no real push in the test
            mid, note = m.mission_start("orchestrator", "Testziel", ["s1", "s2"])
            self.assertTrue(mid)
            self.assertEqual(m.mission_start("orchestrator", "", [])[0], None)
            self.assertEqual(m.mission_update("orchestrator", mid, step=1,
                                              status="doing", task_id="t-1"), "ok")
            inst, mi, st = m.mission_for_task("t-1")
            self.assertEqual((inst, mi["id"], st["n"]), ("orchestrator", mid, 1))
            self.assertEqual(m.mission_update("orchestrator", mid, step=1,
                                              status="done", result="ok"), "ok")
            self.assertIsNone(m.mission_for_task("t-1")[1])   # done -> no more trigger
            self.assertEqual(m.mission_admin("orchestrator", mid, "pause"), "ok")
            self.assertEqual(m.mission_admin("orchestrator", mid, "resume"), "ok")
            self.assertEqual(m.mission_finish("orchestrator", mid, "fertig"), "ok")
            done = m.mission_list("orchestrator")[0]
            self.assertEqual(done["status"], "done")
            self.assertIn("cannot", m.mission_admin("orchestrator", mid, "abort"))
        finally:
            mmod.MISSIONS_FILE = old_file
            mmod.notify_add = old_notify

    def test_voice_turns_show_up_in_the_shared_chat(self):
        """A voice client's turns land in the shared store as a 'Voice · inst'
        conversation (live on the web); /reset archives it and the next turn
        opens a new one. Web-chat ids are left alone."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-voice-")
        oc, ot = m.CHATS_FILE, m.TOMBSTONES_FILE
        m.CHATS_FILE, m.TOMBSTONES_FILE = os.path.join(tmp, "chats.json"), os.path.join(tmp, "tombs.json")
        m._voice_sessions.clear()
        try:
            self.assertEqual(m.voice_session("vc", "10.0.0.9", "1757000000000"), "")   # web chat
            self.assertEqual(m.voice_session("vc", "10.0.0.9", "voice-1757"), "voice-vc-1757")
            s1 = m.voice_session("vc", "10.0.0.9")            # ESP: manager-kept
            self.assertTrue(s1.startswith("voice-vc-"))
            self.assertEqual(m.voice_session("vc", "10.0.0.9"), s1)   # stable
            m.chat_log_append("vc", s1, "Radio aus", "Radio ist aus.", kind="voice")
            m.chat_log_append("vc", s1, "Wie spät?", "16:36 Uhr.", kind="voice")
            m.voice_session("vc", "10.0.0.9", reset=True)
            time.sleep(1.1)                                     # session ids carry seconds
            s2 = m.voice_session("vc", "10.0.0.9")
            self.assertNotEqual(s1, s2)
            m.chat_log_append("vc", s2, "Hallo", "Hallo!", kind="voice")
            chats = {c["id"]: c for c in m.load_chats()}
            self.assertEqual(len(chats[s1]["messages"]), 4)     # archived, intact
            self.assertEqual(len(chats[s2]["messages"]), 2)
            self.assertEqual(chats[s1]["instance"], "vc")
            self.assertTrue(chats[s1]["title"].startswith("Voice · vc · "))
        finally:
            m.CHATS_FILE, m.TOMBSTONES_FILE = oc, ot
            m._voice_sessions.clear()

    def test_tts_reads_no_tool_lines(self):
        """Read aloud must skip tool status, think blocks, fences and decor —
        filtered in the manager so web, app and ESP get it for free."""
        m = self.m
        self.assertEqual(m.speakable_text("🔧 ha_control …\nEs gab ein Problem."), "Es gab ein Problem.")
        self.assertEqual(m.speakable_text("  🔧 caldav__list-events …\n\nAm Freitag: **Frühstück**."),
                         "Am Freitag: Frühstück.")
        self.assertEqual(m.speakable_text("⟦think⟧ plan ⟦/think⟧Es ist 10 Uhr."), "Es ist 10 Uhr.")
        self.assertEqual(m.speakable_text("Siehe [Doku](https://x.y/z) und https://a.b/c."), "Siehe Doku und")
        self.assertIn("Codeblock übersprungen", m.speakable_text("Hier:\n```py\nprint(1)\n```\nfertig"))
        self.assertEqual(m.speakable_text(""), "")

    def test_stt_recent_ring(self):
        """What did STT hear? Newest first, bounded, in memory only."""
        m = self.m
        m._stt_recent.clear()
        m.stt_remember("Radio aus", 1.2, "192.168.1.5")
        m.stt_remember("Wie spät ist es?", 1.8, "192.168.1.5")
        r = m.stt_recent()
        self.assertEqual([x["text"] for x in r], ["Wie spät ist es?", "Radio aus"])
        for i in range(m.STT_RECENT_MAX + 5):
            m.stt_remember(f"t{i}", 1, "x")
        self.assertEqual(len(m.stt_recent()), m.STT_RECENT_MAX)
        by_path = {p: admin for _, _, p, admin in m.ROUTER.inventory()}
        self.assertTrue(by_path["/api/stt-recent"])
        self.assertTrue(by_path["/api/stt-recent/audio"])
        m._stt_audio.clear()
        m.stt_remember("a", 1, "x", audio=b"RIFFaaa", ctype="audio/wav")
        m.stt_remember("b", 1, "x", audio=b"RIFFbbb", ctype="audio/wav")
        self.assertEqual(m.stt_audio(0)[3], b"RIFFbbb")
        self.assertEqual(m.stt_audio(1)[3], b"RIFFaaa")
        self.assertIsNone(m.stt_audio(2))
        m._stt_recent.clear(); m._stt_audio.clear()

    def test_hub_processes_get_the_host_timezone(self):
        """caldav-mcp formats event times in its process TZ — the manager hands
        every hub process the host zone; a catalog entry may still override."""
        import mgr.mcp as mcpmod
        old = mcpmod.HUB_TZ
        try:
            mcpmod.HUB_TZ = "Europe/Berlin"
            self.assertEqual(mcpmod.hub_env(None), {"TZ": "Europe/Berlin"})
            self.assertEqual(mcpmod.hub_env({"A": "1"}), {"TZ": "Europe/Berlin", "A": "1"})
            self.assertEqual(mcpmod.hub_env({"TZ": "UTC"})["TZ"], "UTC")
        finally:
            mcpmod.HUB_TZ = old
        self.assertEqual(self.m._mcp.HUB_TZ, self.m.HOST_TZ)

    def test_mcp_servers_validated_against_catalog(self):
        """The Policy tab assigns MCPs through the config route: names must
        exist in the catalog, spaces are tolerated, empty means none."""
        m = self.m
        old = m.load_mcps
        try:
            m.load_mcps = lambda: [{"name": "homeassistant"}, {"name": "caldav"}]
            self.assertEqual(m.mcp_servers_error("homeassistant, caldav"), "")
            self.assertEqual(m.mcp_servers_error(""), "")
            self.assertIn("calendar", m.mcp_servers_error("caldav,calendar"))
        finally:
            m.load_mcps = old

    def test_stale_image_detection_and_rebuild_push(self):
        """A running VM started before its base image was rebuilt is 'stale':
        the API says so, and the idle sweep pushes once per rebuild, naming
        the affected instances — silent when nothing is affected."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-stale-")
        os.makedirs(os.path.join(tmp, "instances")); os.makedirs(os.path.join(tmp, "run"))
        img = os.path.join(tmp, "instances", "openrouter-rootfs.ext4")
        old = m.BASE, m.RUN_DIR, m.is_running, m.load_instances, m.notify_add, dict(m._img_seen)
        pushes = []
        try:
            m.BASE, m.RUN_DIR = tmp, os.path.join(tmp, "run")
            m.is_running = lambda i: i["name"] != "off"
            insts = [{"name": "old", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "fresh", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "off", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "priv", "rootfs": "instances/priv.ext4"}]
            m.load_instances = lambda: insts
            m.notify_add = lambda *a, **k: pushes.append(a)
            for n, ts in (("old", 1000), ("fresh", 3000), ("off", 1000), ("priv", 1000)):
                pf = os.path.join(tmp, "run", n + ".pid"); open(pf, "w").write("1"); os.utime(pf, (ts, ts))
            open(img, "w").write("x"); os.utime(img, (2000, 2000))
            self.assertEqual([m.image_state(i)[0] for i in insts], [True, False, False, False])
            self.assertEqual(m.stale_instances(), ["old"])
            m._img_seen.clear()
            self.assertEqual(m.image_sweep(), [])           # first sight: baseline, no push
            self.assertEqual(m.image_sweep(), [])           # unchanged: quiet
            os.utime(img, (4000, 4000))                      # rebuilt -> both VMs older now
            self.assertEqual(m.image_sweep(), ["old", "fresh"])
            self.assertEqual(len(pushes), 1)
            self.assertIn("old, fresh", pushes[0][2])
            self.assertEqual(m.image_sweep(), [])           # once per rebuild
        finally:
            m.BASE, m.RUN_DIR, m.is_running, m.load_instances, m.notify_add = old[:5]
            m._img_seen.clear(); m._img_seen.update(old[5])

    def test_guest_config_carries_host_timezone(self):
        """Guests boot in UTC; the manager hands them the host's zone name."""
        m = self.m
        self.assertTrue(m.HOST_TZ)
        self.assertEqual(m.HOST_TZ, os.environ.get("GUEST_TZ") or m._host_tz())

    def test_task_model_reaches_the_ephemeral_vm(self):
        """spawn_subagent/create_task may name a model: it travels through
        /api/task (wait) and through the queue (worker) into _run_ephemeral;
        a named instance keeps its own model."""
        m = self.m
        import mgr.store as st
        seen = []
        old_eph, old_named, old_file = m._run_ephemeral, m._run_named, st.TASKS_FILE
        try:
            m._run_ephemeral = lambda msg, model=None: (seen.append(("eph", msg, model)), (True, "r"))[1]
            m._run_named = lambda inst, msg: (seen.append(("named", inst, msg)), (True, "r"))[1]
            m._run_task_now("ephemeral", "do", "google/gemini-2.5-flash")
            m._run_task_now("ephemeral", "do")
            m._run_task_now("hass", "do", "google/gemini-2.5-flash")
            self.assertEqual(seen, [("eph", "do", "google/gemini-2.5-flash"), ("eph", "do", None),
                                    ("named", "hass", "do")])
            st.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-taskmodel-"), "tasks.json")
            t = st.add_task("ephemeral", "queued", "", model=" x/y ")
            self.assertEqual(st.load_tasks()[0]["model"], "x/y")
            t2 = st.add_task("ephemeral", "plain", "")
            self.assertNotIn("model", next(x for x in st.load_tasks() if x["id"] == t2["id"]))
        finally:
            m._run_ephemeral, m._run_named, st.TASKS_FILE = old_eph, old_named, old_file

    def test_task_target_resolved_and_validated(self):
        """'@orchestrator' (an agent's typo) failed daily with 'instance unknown'
        and nobody was told. Targets lose a leading '@', unknown names are
        refused at creation, and an edit may move a task to another instance."""
        m = self.m
        import mgr.store as st
        old_load, old_file = m.load_instances, st.TASKS_FILE
        try:
            m.load_instances = lambda: [{"name": "orchestrator"}, {"name": "hass"}]
            self.assertEqual(m.resolve_task_target("@orchestrator"), ("orchestrator", ""))
            self.assertEqual(m.resolve_task_target(" hass "), ("hass", ""))
            self.assertEqual(m.resolve_task_target(""), ("ephemeral", ""))
            self.assertEqual(m.resolve_task_target(None), ("ephemeral", ""))
            self.assertEqual(m.resolve_task_target("@")[0], "ephemeral")
            name, err = m.resolve_task_target("orchestartor")
            self.assertEqual(name, "")
            self.assertIn("unknown", err)
            tmp = tempfile.mkdtemp(prefix="e2e-tasktarget-")
            st.TASKS_FILE = os.path.join(tmp, "tasks.json")
            t = st.add_task("orchestrator", "msft", "daily 08:00")
            msg = st.update_task(t["id"], instance="hass")
            self.assertIn("updated", msg)
            self.assertEqual(st.load_tasks()[0]["instance"], "hass")
            self.assertEqual(st.load_tasks()[0]["schedule"], "daily 08:00")   # untouched
            # hourly sweep: a task whose target died is pushed ONCE, edit clears the mark
            st.add_task("@orchestrator", "typo target", "daily 08:00")
            pushes = []
            old_notify = m.notify_add
            m.notify_add = lambda *a, **k: pushes.append(a)
            try:
                hit = m.task_target_sweep()
                self.assertEqual([h[1] for h in hit], ["@orchestrator"])
                self.assertEqual(len(pushes), 1)
                self.assertIn("@orchestrator", pushes[0][2])
                self.assertEqual(m.task_target_sweep(), [])          # not again
                bad = next(t for t in st.load_tasks() if t["instance"] == "@orchestrator")
                st.update_task(bad["id"], instance="orchestrator")   # fixed -> mark gone
                self.assertNotIn("target_warned", next(t for t in st.load_tasks() if t["id"] == bad["id"]))
            finally:
                m.notify_add = old_notify
        finally:
            m.load_instances, st.TASKS_FILE = old_load, old_file

    def test_mission_edit_and_delete_any_status(self):
        """The UI may correct or remove a mission in ANY state: goal/steps/
        status editable (positions keep their progress, reopening respects the
        active cap), delete removes done/failed/active alike, owner resolved
        from the id when the caller does not know it."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-miedit-")
        import mgr.missions as mmod
        old_file, old_notify = mmod.MISSIONS_FILE, mmod.notify_add
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            mid, _ = m.mission_start("hass", "Goal A", ["s1", "s2", "s3"])
            m.mission_update("hass", mid, step=1, status="done", result="r1")
            m.mission_finish("hass", mid, "all good")            # -> done
            # edit a DONE mission (no instance given -> owner lookup)
            self.assertEqual(m.mission_edit("", mid, goal="Goal B",
                                            steps=["s1", "s2x", "s3", "s4"]), "ok")
            mi = m.mission_list("hass")[0]
            self.assertEqual(mi["goal"], "Goal B")
            self.assertEqual([s["text"] for s in mi["steps"]], ["s1", "s2x", "s3", "s4"])
            self.assertEqual(mi["steps"][0]["status"], "done")   # progress kept by position
            self.assertEqual(mi["steps"][0]["result"], "r1")
            self.assertEqual(mi["steps"][3]["status"], "open")   # new step
            self.assertEqual(mi["status"], "done")
            # reopen -> active; the agent's mission_update works again
            self.assertEqual(m.mission_edit("hass", mid, status="active"), "ok")
            self.assertEqual(m.mission_update("hass", mid, step=4, status="doing"), "ok")
            self.assertEqual(m.mission_edit("hass", mid, status="active"), "unchanged")
            self.assertIn("unknown status", m.mission_edit("hass", mid, status="weird"))
            self.assertEqual(m.mission_edit("hass", mid, goal="  "), "goal missing")
            self.assertEqual(m.mission_edit("hass", mid, steps=[]), "steps missing")
            self.assertEqual(m.mission_edit("hass", "m-nope"), "unknown mission")
            # cap: 5 active others -> reopening a failed one is refused
            for i in range(m.MISSION_MAX_ACTIVE):
                m.mission_start("cap", f"g{i}", ["x"])
            fid, _ = m.mission_start("cap", "late", ["x"])     # None: cap reached
            self.assertIsNone(fid)
            m.mission_finish("cap", m.mission_list("cap")[0]["id"], "bad", failed=True)
            m.mission_start("cap", "fill", ["x"])                 # back at the cap
            failed_id = m.mission_list("cap")[0]["id"]
            self.assertIn("max", m.mission_edit("cap", failed_id, status="active"))
            # delete: failed, then active, then unknown
            self.assertEqual(m.mission_delete("", failed_id), "ok")
            self.assertEqual(m.mission_delete("hass", mid), "ok")
            self.assertEqual(m.mission_list("hass"), [])
            self.assertEqual(m.mission_delete("hass", mid), "unknown mission")
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify

    def test_mission_cross_instance(self):
        """Multi-owner missions: ANY agent owns missions, the steps carry the
        instance they were delegated to, and admin actions find the owner from
        the id alone (web UI/app only know the mission id)."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi3-")
        import mgr.missions as mmod
        old_file, old_notify = mmod.MISSIONS_FILE, mmod.notify_add
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            mid, _ = m.mission_start("jobresearcher", "cross-instance goal", ["s1", "s2"])
            self.assertTrue(mid)
            # Step 1 is executed by a DIFFERENT agent than the owner.
            self.assertEqual(m.mission_update("jobresearcher", mid, step=1, status="doing",
                                              task_id="t-x1", target="hass"), "ok")
            st1 = m.mission_list("jobresearcher")[0]["steps"][0]
            self.assertEqual(st1["target"], "hass")
            self.assertIn("@hass", m.mission_list("jobresearcher")[0]["log"][-1])
            # The advance trigger has to find the OWNER, not the executor.
            inst, mi, st = m.mission_for_task("t-x1")
            self.assertEqual((inst, mi["id"], st["n"]), ("jobresearcher", mid, 1))
            self.assertEqual(m.mission_owner(mid), "jobresearcher")
            self.assertIsNone(m.mission_owner("m-nope"))
            # Admin action without an instance resolves the owner itself.
            self.assertEqual(m.mission_admin("", mid, "pause"), "ok")
            self.assertEqual(m.mission_list("jobresearcher")[0]["status"], "paused")
            self.assertEqual(m.mission_admin("", mid, "resume"), "ok")
            self.assertEqual(m.mission_admin("", "m-nope", "pause"), "unknown mission")
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify

    def test_idle_heartbeat_skip_persists_and_does_not_reskip(self):
        """Regression (2026-09-03, 9.961 Logzeilen): ein Skip mutiert next_run
        und MUSS dirty=True melden — sonst wirft with_tasks das Weiterplanen
        weg und derselbe Heartbeat wird alle 5 s neu geskippt. Vertragstest
        direkt gegen worker_claim."""
        m = self.m
        now = int(time.time())
        hb = {"id": "hb1", "instance": "orchestrator", "status": "scheduled",
              "message": "/fresh Heartbeat: x", "schedule": "every 30m",
              "next_run": 0}
        tasks = [hb]
        skipped, throttled = [], []
        dirty, claimed = m.worker_claim(tasks, now, lambda t: True, skipped, throttled)
        self.assertTrue(dirty, "Skip mutiert next_run — MUSS dirty melden")
        self.assertIsNone(claimed)
        self.assertEqual(skipped, ["hb1"])
        self.assertGreater(hb["next_run"], now, "next_run muss vorruecken")
        # Zweiter Zyklus auf dem (nun gespeicherten) Stand: nichts mehr faellig.
        skipped2 = []
        dirty2, claimed2 = m.worker_claim(tasks, now + 5, lambda t: True, skipped2, [])
        self.assertFalse(dirty2)
        self.assertIsNone(claimed2)
        self.assertEqual(skipped2, [], "5 s spaeter darf NICHT erneut geskippt werden")
        # Nicht-idle (hb_idle False): der Task wird normal geclaimt.
        hb["next_run"] = 0
        dirty3, claimed3 = m.worker_claim(tasks, now, lambda t: False, [], [])
        self.assertTrue(dirty3)
        self.assertEqual(claimed3["id"], "hb1")
        self.assertEqual(hb["status"], "running")

    def test_mission_advance_collects_bursts_into_one_push(self):
        """Collect mode: every advance push is a full /fresh turn with ~5k fixed
        input tokens. Several tasks finishing inside the window must produce ONE
        push that lists them all — not one turn each."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi5-")
        import mgr.missions as mmod
        old_file, old_notify = mmod.MISSIONS_FILE, mmod.notify_add
        old_run, old_load, old_win = m._run_named, m.load_instances, m.MISSION_COLLECT_SECS
        pushes = []
        done = threading.Event()
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            m.load_instances = lambda: [{"name": "owner-a"}]
            m._run_named = lambda inst, msg: (pushes.append((inst, msg)), done.set(), (True, "ok"))[-1]
            m.MISSION_COLLECT_SECS = 0.3
            mid, _ = m.mission_start("owner-a", "burst goal", ["s1", "s2", "s3"])
            for n, tid in ((1, "t-b1"), (2, "t-b2"), (3, "t-b3")):
                m.mission_update("owner-a", mid, step=n, status="doing", task_id=tid)
                m._mission_advance_fire(tid)
            self.assertTrue(done.wait(5), "no push fired")
            time.sleep(0.4)                            # window fully drained
            self.assertEqual(len(pushes), 1, f"expected ONE push, got {len(pushes)}")
            inst, msg = pushes[0]
            self.assertEqual(inst, "owner-a")
            for tid in ("t-b1", "t-b2", "t-b3"):
                self.assertIn(tid, msg)
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify
            m._run_named, m.load_instances = old_run, old_load
            m.MISSION_COLLECT_SECS = old_win

    def test_mission_advance_fires_at_owner(self):
        """Regression guard: the push after a finished task goes to the mission's
        owner — previously it was hard-wired to the orchestrator, so a mission
        owned by any other agent would never advance. The push is debounced by
        the collect window, hence the shortened window here."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi4-")
        import mgr.missions as mmod
        old_file, old_notify = mmod.MISSIONS_FILE, mmod.notify_add
        old_run, old_load, old_win = m._run_named, m.load_instances, m.MISSION_COLLECT_SECS
        fired = []
        done = threading.Event()
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            m.MISSION_COLLECT_SECS = 0.2
            m.load_instances = lambda: [{"name": "jobresearcher"}, {"name": "orchestrator"}]
            m._run_named = lambda inst, msg: (fired.append(inst), done.set(), (True, "ok"))[-1]
            mid, _ = m.mission_start("jobresearcher", "owned elsewhere", ["s1"])
            m.mission_update("jobresearcher", mid, step=1, status="doing",
                             task_id="t-y1", target="hass")
            m._mission_advance_fire("t-y1")
            self.assertTrue(done.wait(5), "no advance push fired")
            self.assertEqual(fired, ["jobresearcher"])
            # Owner gone -> no push (the TTL sweep pauses the mission instead).
            fired.clear(); done.clear()
            m.load_instances = lambda: [{"name": "orchestrator"}]
            m._mission_advance_fire("t-y1")
            self.assertFalse(done.wait(0.6))
            self.assertEqual(fired, [])
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify
            m._run_named, m.load_instances = old_run, old_load
            m.MISSION_COLLECT_SECS = old_win

    def test_mission_caps(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi2-")
        import mgr.missions as mmod
        old_file = mmod.MISSIONS_FILE
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            for i in range(m.MISSION_MAX_ACTIVE):
                self.assertTrue(m.mission_start("o", f"g{i}", ["s"])[0])
            self.assertIsNone(m.mission_start("o", "zuviel", ["s"])[0])
        finally:
            mmod.MISSIONS_FILE = old_file

    def test_task_store_survives_concurrent_writers(self):
        """The lock used to guard only the WRITE: worker and HTTP threads both
        did load→modify→save, and an interleaving silently dropped tasks. All
        mutations go through with_tasks() now — this hammers the store from
        four threads and demands an exact result."""
        import mgr.store as st
        tmp = tempfile.mkdtemp(prefix="e2e-race-")
        old = st.TASKS_FILE
        try:
            st.TASKS_FILE = os.path.join(tmp, "tasks.json")
            ids, errs = [], []

            def adder(n):
                try:
                    for i in range(25):
                        ids.append(st.add_task(f"inst{n}", f"job {n}-{i}")["id"])
                except Exception as e:
                    errs.append(repr(e))

            threads = [threading.Thread(target=adder, args=(n,)) for n in range(4)]
            for th in threads:
                th.start()
            # Waehrenddessen mutiert ein fuenfter Thread Status-Felder — die
            # Rolle des Workers.
            def toucher():
                for _ in range(40):
                    st.with_tasks(lambda ts: (bool(ts), [t.update(
                        {"updated": int(time.time())}) for t in ts] and True))
            tt = threading.Thread(target=toucher)
            tt.start()
            for th in threads + [tt]:
                th.join()
            self.assertEqual(errs, [])
            stored = {t["id"] for t in st.load_tasks()}
            self.assertEqual(len(ids), 100)
            self.assertEqual(stored, set(ids), "lost update: created tasks vanished")
        finally:
            st.TASKS_FILE = old

    def test_add_task_roundtrip(self):
        """Regression: the mgr/ split shipped store.py without `import uuid`,
        and for 13 days NO new task could be created (UI, app, create_task) —
        unnoticed because the pre-split scheduled tasks kept running and
        wait=true bypasses add_task. This test calls the real thing."""
        import mgr.store as st
        tmp = tempfile.mkdtemp(prefix="e2e-task-")
        old = st.TASKS_FILE
        try:
            st.TASKS_FILE = os.path.join(tmp, "tasks.json")
            t = st.add_task("someinst", "do the thing")
            self.assertTrue(t["id"] and len(t["id"]) == 12)
            self.assertEqual(t["status"], "pending")
            t2 = st.add_task("someinst", "recurring", "every 7d")
            self.assertEqual(t2["status"], "scheduled")
            self.assertGreater(t2["next_run"], int(time.time()) - 5)
            ids = {x["id"] for x in st.load_tasks()}
            self.assertEqual(ids, {t["id"], t2["id"]})
        finally:
            st.TASKS_FILE = old

    def test_tasks_file_wired(self):
        """Regression: TASKS_FILE must be set in store.configure() — otherwise
        load_tasks crashes (bug from 2026-08-20, /api/tasks returned nothing)."""
        import mgr.store as st
        self.assertTrue(st.TASKS_FILE and st.TASKS_FILE.endswith("tasks.json"))
        self.assertIsInstance(st.load_tasks(), list)

    def test_irohgw_allowlist_roundtrip(self):
        """iroh app-transport pairing: add/remove phone node-ids; only 64-hex
        ids are accepted; a missing gateway node-id reads as unavailable."""
        import mgr.irohgw as ig
        tmp = tempfile.mkdtemp(prefix="e2e-iroh-")
        ig.configure(tmp)
        nid = "a" * 64
        self.assertFalse(ig.status()["available"])          # no nodeid.txt yet
        ok, _ = ig.allow_add("nothex", "x")
        self.assertFalse(ok)                                 # rejected: not 64 hex
        ok, _ = ig.allow_add(nid, "Phone")
        self.assertTrue(ok)
        self.assertEqual(ig.load_allow(), [{"id": nid, "label": "Phone"}])
        ig.allow_add(nid, "Phone")                           # idempotent
        self.assertEqual(len(ig.load_allow()), 1)
        ig.allow_remove(nid)
        self.assertEqual(ig.load_allow(), [])
        # gateway node-id surfaces once the gateway writes nodeid.txt
        with open(os.path.join(tmp, "iroh-gw", "nodeid.txt"), "w") as fh:
            fh.write("b" * 64 + "\n")
        self.assertEqual(ig.gateway_node_id(), "b" * 64)
        self.assertTrue(ig.status()["available"])

    def test_reclaim_stuck_tasks(self):
        m = self.m
        import mgr.store as st
        tmp = tempfile.mkdtemp(prefix="e2e-rc-")
        old = st.TASKS_FILE
        try:
            st.TASKS_FILE = os.path.join(tmp, "tasks.json")
            st.save_tasks([{"id": "a", "status": "running", "schedule": "daily 07:00"},
                           {"id": "b", "status": "running"},          # einmalig
                           {"id": "c", "status": "done"}])
            m.reclaim_stuck_tasks()
            by = {t["id"]: t["status"] for t in st.load_tasks()}
            self.assertEqual(by["a"], "scheduled")   # geplant -> scheduled
            self.assertEqual(by["b"], "pending")      # einmalig -> pending
            self.assertEqual(by["c"], "done")         # unberuehrt
        finally:
            st.TASKS_FILE = old

    def test_leak_filter(self):
        import mgr.gateway as g
        red, n = g.redact_secrets("key sk-or-v1-abcdef0123456789xyz and ptr_ABCDEFGHIJ1234567890")
        self.assertEqual(n, 2)
        self.assertNotIn("sk-or-v1", red)
        self.assertNotIn("ptr_ABCD", red)
        # HuggingFace token is masked
        self.assertEqual(g.redact_secrets("tok hf_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 x")[1], 1)
        self.assertEqual(g.redact_secrets("normaler Text")[1], 0)
        # git-SHA (40 hex) darf NICHT als Secret gelten
        self.assertEqual(g.redact_secrets("commit deadbeef00112233445566778899aabbccddeeff")[1], 0)

    def test_guard_rate_limit(self):
        m = self.m
        inst = {"name": "e2e-guard-r", "config": {"LLM_RATE_MIN": "2", "BUDGET_TOKENS": "0"}}
        import mgr.store  # loaded just to be safe
        r1 = m._guard_check(inst)[0]
        r2 = m._guard_check(inst)[0]
        r3, why = m._guard_check(inst)
        self.assertTrue(r1 and r2)
        self.assertFalse(r3)
        self.assertIn("rate", why)
        self.assertTrue(m._guard_check(None)[0])   # Admin/Host immer frei

    def test_usage_for_shape(self):
        m = self.m
        d = m.usage_for("orchestrator", 0)
        self.assertEqual(set(d.keys()), {"calls", "in", "out", "cost"})
        self.assertIsInstance(d["calls"], int)
        z = m.usage_for("gibtsnichtxyz", 0)     # unknown instance -> zeros
        self.assertEqual(z["calls"], 0)

    def test_prompt_store(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-pr-")
        import mgr.rules as rmod
        old = rmod.PROMPTS_FILE
        try:
            rmod.PROMPTS_FILE = os.path.join(tmp, "prompts.json")
            self.assertEqual(m.prompt_upsert("Daily!", "Text"), "saved")   # Name normalisiert
            self.assertEqual(m.load_prompts()[0]["name"], "daily")
            self.assertEqual(m.prompt_upsert("daily", "New"), "saved")     # update
            self.assertEqual(m.load_prompts()[0]["text"], "New")
            self.assertIn("built-in", m.prompt_upsert("reset", "x"))       # reserved
            self.assertEqual(m.prompt_delete("daily"), "deleted")
            self.assertEqual(m.prompt_delete("daily"), "unknown")
        finally:
            rmod.PROMPTS_FILE = old

    def test_notify_store(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-notif-")
        import mgr.notify as nmod
        old_file, old_sent = nmod.NOTIF_FILE, list(nmod._notif_sent)
        try:
            nmod.NOTIF_FILE = os.path.join(tmp, "notifications.json")
            nmod._notif_sent.clear()
            nid, note = m.notify_add("orchestrator", "Title", "Text")
            self.assertTrue(nid)
            lst = m.load_notifications()
            self.assertEqual(len(lst), 1)
            self.assertEqual(lst[0]["title"], "Title")
            self.assertFalse(lst[0]["read"])
            self.assertEqual(m.notif_mark_read(mark_all=True), 1)
            self.assertTrue(m.load_notifications()[0]["read"])
            self.assertIsNone(m.notify_add("x", "", "")[0])   # leer -> nichts
        finally:
            nmod.NOTIF_FILE = old_file
            nmod._notif_sent[:] = old_sent

    def test_mcp_caldav_secret_stays_on_the_host(self):
        """The caldav catalog entry takes its password from the host secret
        store, and only for an instance the policy released it to. Without the
        release the placeholder survives unsubstituted — the guest never sees a
        credential, and a missing release is visible instead of silent."""
        m = self.m
        import mgr.mcp as mcpmod
        self.assertIn("caldav", [x["name"] for x in m.load_mcps()])
        self.assertEqual(m.mcp_required_secrets(["caldav"]), {"CALDAV_PASSWORD"})
        for name in ("myassistant", "voicecommand"):
            self.assertIn("CALDAV_PASSWORD",
                          m.allowed_secret_keys({"name": name, "template": "openrouter"}),
                          f"{name} has no CALDAV_PASSWORD release")
        old = mcpmod.secret_store
        mcpmod.secret_store = lambda: {"CALDAV_PASSWORD": "s3cret"}
        try:
            env = json.loads(m.build_mcp_config(
                ["caldav"], allowed={"CALDAV_PASSWORD"}))["mcpServers"]["caldav"]["env"]
            self.assertEqual(env["CALDAV_PASSWORD"], "s3cret")
            env = json.loads(m.build_mcp_config(
                ["caldav"], allowed=set()))["mcpServers"]["caldav"]["env"]
            self.assertEqual(env["CALDAV_PASSWORD"], "${CALDAV_PASSWORD}")
        finally:
            mcpmod.secret_store = old

    # ---- guest boundary (multi-tenancy S-fixes, 2026-09-06) ------------------
    def _handler(self, path, ip, method="GET", auth=None):
        """A handler object without a socket: enough of BaseHTTPRequestHandler's
        state for _auth/_do_GET to run and write their response into a buffer."""
        import email.message
        m = self.m
        h = object.__new__(m.H)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = (ip, 40000)
        h.headers = email.message.Message()
        if auth:
            h.headers["Authorization"] = auth
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.close_connection = True
        return h

    def test_guest_get_denylist_covers_ui_proxy_and_terminal(self):
        """GET /i/<other>/term opened the shell of every other VM — only POST
        was gated. The denylist names the admin UI, chat, katfs and /i/."""
        m = self.m
        for p in ("/", "/chat", "/chat?i=x", "/katfs", "/katfs/", "/katfs/x.txt",
                  "/i/orchestrator/", "/i/orchestrator/term", "/i/x/term/ws?y=1"):
            self.assertTrue(m.guest_get_blocked(p), p)
        for p in ("/api/agents", "/api/memory/self", "/api/skills?meta=1",
                  "/api/inbox", "/chatx", "/logo.svg"):
            self.assertFalse(m.guest_get_blocked(p), p)

    def test_guest_gets_403_on_denied_paths_and_skips_basic_auth(self):
        """Guests carry no credentials (identity = source IP), so they pass
        _auth even with MANAGER_PASS set; an admin without credentials gets
        401. Denied GET paths answer 403 before any handler runs."""
        m = self.m
        guest = {"name": "hass", "index": 7, "config": {}}
        old_ibi, old_pw = m.instance_by_ip, m.PW
        try:
            m.instance_by_ip = lambda ip: guest if ip == "172.30.7.2" else None
            m.PW = "secret"
            for p in ("/i/orchestrator/term", "/", "/chat", "/katfs/", "/api/inbox",
                      "/no/such/page"):
                h = self._handler(p, "172.30.7.2")
                self.assertTrue(h._auth(), p)
                h._do_GET()
                self.assertIn(b" 403 ", h.wfile.getvalue().split(b"\r\n", 1)[0], p)
            h = self._handler("/", "192.168.1.5")
            self.assertFalse(h._auth())
            self.assertIn(b" 401 ", h.wfile.getvalue().split(b"\r\n", 1)[0])
            h = self._handler("/", "192.168.1.5", auth="Basic " + base64.b64encode(
                f"{m.USER}:secret".encode()).decode())
            self.assertTrue(h._auth())
        finally:
            m.instance_by_ip, m.PW = old_ibi, old_pw

    def test_guest_task_target_policy(self):
        """A guest may task itself or an ephemeral VM; other instances only via
        DELEGATE_TARGETS in its config ('*' = all); the orchestrator: all."""
        m = self.m
        me = {"name": "jobresearcher", "config": {}}
        for t in ("ephemeral", "", None, "jobresearcher"):
            self.assertTrue(m.guest_may_target(me, t), repr(t))
        for t in ("orchestrator", "hass", "claudy"):
            self.assertFalse(m.guest_may_target(me, t), t)
        me["config"]["DELEGATE_TARGETS"] = "hass, claudy"
        self.assertTrue(m.guest_may_target(me, "hass"))
        self.assertTrue(m.guest_may_target(me, "claudy"))
        self.assertFalse(m.guest_may_target(me, "orchestrator"))
        me["config"]["DELEGATE_TARGETS"] = "*"
        self.assertTrue(m.guest_may_target(me, "orchestrator"))
        orch = {"name": m.ORCH_INSTANCE, "config": {}}
        self.assertTrue(m.guest_may_target(orch, "anything"))

    def test_history_search_scoped_to_instance(self):
        """recall_tasks from a guest returns only runs it created or executed."""
        import mgr.store as st
        tmp = tempfile.mkdtemp(prefix="e2e-hist-")
        old = st.HISTORY_DB
        try:
            st.HISTORY_DB = os.path.join(tmp, "h.db")
            st.history_add("hass", "light on", "done", True, origin="worker")
            st.history_add("orchestrator", "ping", "pong", True, origin="jobresearcher")
            st.history_add("claudy", "review", "ok", True, origin="orchestrator")
            self.assertEqual(len(st.history_search()), 3)
            self.assertEqual([r["target"] for r in st.history_search(instance="hass")], ["hass"])
            self.assertEqual([r["target"] for r in st.history_search(instance="jobresearcher")],
                             ["orchestrator"])
            self.assertEqual(st.history_search(instance="remote"), [])
            self.assertEqual(len(st.history_search("light", instance="hass")), 1)
            self.assertEqual(st.history_search("light", instance="orchestrator"), [])
        finally:
            st.HISTORY_DB = old

    def test_hitl_status_bound_to_requesting_instance(self):
        """Approval ids are 8 hex chars — a guest may only poll its own."""
        m = self.m
        import mgr.signal as sigmod
        old_send = sigmod.signal_send
        try:
            sigmod.signal_send = lambda text, to=None: (True, "sent")
            hid = m.hitl_create("hass", "bash", "rm x")
            self.assertEqual(m.hitl_status(hid), "pending")            # admin
            self.assertEqual(m.hitl_status(hid, "hass"), "pending")    # owner
            self.assertEqual(m.hitl_status(hid, "uncensored"), "unknown")
        finally:
            sigmod.signal_send = old_send

    def test_guest_input_rules_only_manager_and_nfs(self):
        """Guest -> host is limited to :8700 and NFS; the DROP is inserted first
        so the ACCEPTs land above it. Per tap, a source other than the guest's
        own /30 address is dropped (the IP is the guest's identity)."""
        import types
        m = self.m
        calls = []
        old_sh = m.sh
        try:
            # every -C fails -> "rule missing" -> everything gets inserted
            m.sh = lambda *a, check=True: (calls.append(a), types.SimpleNamespace(returncode=1))[1]
            m.ensure_guest_input_rules()
            ins = [c for c in calls if c[1] == "-I"]
            self.assertEqual(ins[0][2:], ("INPUT", "1", "-i", "fc+", "-j", "DROP"))
            ports = {c[c.index("--dport") + 1] for c in ins if "--dport" in c}
            self.assertEqual(ports, {str(m.LISTEN[1]), "2049"})
            self.assertTrue(any("ESTABLISHED,RELATED" in c for c in ins))
            self.assertTrue(all(c[2] == "INPUT" and "fc+" in c for c in ins))
            calls.clear()
            m.ensure_antispoof({"name": "hass", "index": 7})
            spec = ("-i", "fc7", "!", "-s", "172.30.7.2", "-j", "DROP")
            self.assertEqual({c[2] for c in calls if c[1] == "-I"}, {"INPUT", "FORWARD"})
            for c in calls:
                if c[1] == "-I":
                    self.assertEqual(c[4:], spec)
        finally:
            m.sh = old_sh


# ===========================================================================
# HTTP: against the running manager
# ===========================================================================
@unittest.skipUnless(_manager_up(), "manager on 127.0.0.1:8700 not reachable")
class ManagerHTTP(unittest.TestCase):
    def test_root_page(self):
        st, _ = _http("/")
        self.assertEqual(st, 200)

    def test_agents_have_backend_and_model(self):
        st, txt = _http("/api/agents")
        self.assertEqual(st, 200)
        agents = json.loads(txt).get("agents", [])
        self.assertTrue(agents)
        known = {"openrouter", "orcarouter", "anthropic", "pi", "prime", "llama", "claude"}
        for a in agents:
            self.assertIn("backend", a, f"{a.get('name')} without a backend field")
            self.assertIn("model", a)
            self.assertIn(a["backend"], known, f"unbekanntes backend {a['backend']}")

    def test_orchestrator_reports_orcarouter(self):
        """The most recently fixed bug: the orcarouter agent must not appear as
        'openrouter, no model'. Only checked WHEN the orchestrator runs via
        ORCAROUTER_MODEL."""
        st, txt = _http("/api/agents")
        orch = next((a for a in json.loads(txt)["agents"] if a["name"] == "orchestrator"), None)
        if not orch:
            self.skipTest("no orchestrator")
        if orch["backend"] == "orcarouter":
            self.assertTrue(orch["model"], "orcarouter backend but empty model (the old bug)")

    def test_hitl_status_route(self):
        st, txt = _http("/api/hitl/deadbeef")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(txt).get("status"), "unknown")

    def test_katfs_status_route(self):
        st, txt = _http("/api/katfs/status")
        self.assertEqual(st, 200)
        self.assertIn("up", json.loads(txt))

    def test_missions_get(self):
        st, txt = _http("/api/missions?instance=orchestrator")
        self.assertEqual(st, 200)
        self.assertIn("missions", json.loads(txt))

    def test_usage_by_instance(self):
        st, txt = _http("/api/usage/orchestrator?since=0")
        self.assertEqual(st, 200)
        d = json.loads(txt)
        for k in ("calls", "in", "out", "cost"):
            self.assertIn(k, d)

    def test_voice_health_route(self):
        st, txt = _http("/api/voice-health")
        self.assertEqual(st, 200)
        d = json.loads(txt)
        self.assertIn("ready", d)
        if d.get("voices"):
            self.assertIn("de-thorsten-medium", d["voices"])

    def test_notifications_get(self):
        st, txt = _http("/api/notifications")
        self.assertEqual(st, 200)
        d = json.loads(txt)
        self.assertIn("notifications", d)
        self.assertIn("unread", d)
        self.assertIsInstance(d["unread"], int)

    def test_memory_key_with_space_survives_the_url(self):
        """Store takes the key via JSON body, recall via URL path — a key like
        "jobsuche Firmen" could be stored but never retrieved (live bug). The
        path segments are URL-decoded now, and a slash inside a key stays one
        key."""
        import urllib.parse as up
        st, _ = _http("/api/memory/e2e-memtest", "POST",
                      {"key": "jobsuche Firmen", "value": "Ford, Bayer"})
        self.assertEqual(st, 200)
        st, txt = _http("/api/memory/e2e-memtest/" + up.quote("jobsuche Firmen", safe=""))
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(txt).get("value"), "Ford, Bayer")
        # Slash inside a key: everything after the instance is ONE key.
        st, _ = _http("/api/memory/e2e-memtest", "POST",
                      {"key": "a/b c", "value": "x"})
        self.assertEqual(st, 200)
        st, txt = _http("/api/memory/e2e-memtest/" + up.quote("a/b c", safe=""))
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(txt).get("value"), "x")
        # Clean up after ourselves: value null deletes — the production memory
        # store is not a place for test residue (bit the saddler once).
        for k in ("jobsuche Firmen"[:0] or "jobsuche Firmen", "a/b c"):
            _http("/api/memory/e2e-memtest", "POST", {"key": k, "value": None})
        st, txt = _http("/api/memory/e2e-memtest")
        self.assertEqual(json.loads(txt), {}, "test residue left in memory store")

    def test_notify_route_rejects_empty(self):
        # Empty notification -> 429, id null: the route exists, without
        # polluting the live store (no ping to the device).
        st, txt = _http("/api/notify", "POST", {"title": "", "message": ""})
        self.assertEqual(st, 429)
        self.assertIsNone(json.loads(txt).get("id"))

    def test_orcarouter_template_registered(self):
        st, txt = _http("/")
        self.assertEqual(st, 200)
        self.assertIn("orcarouter", txt)

    def test_stopped_instance_api_error_is_plain_text(self):
        """The app pours the body of an API answer straight into the chat bubble.
        For a stopped/unknown instance that must be plain text — HTML showed up
        there as a raw "<p>Instance … is not running</p>"."""
        req = urllib.request.Request(MANAGER_URL + "/i/e2e-gibtsnicht/api/chat",
                                     data=b'{"message":"hi"}', method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=8)
            self.fail("expected 503")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            self.assertEqual(e.code, 503)
            self.assertTrue(e.headers.get("Content-Type", "").startswith("text/plain"))
            self.assertNotIn("<", body)
            self.assertIn("not running", body)

    def test_stopped_instance_page_stays_html(self):
        """The browser path keeps its markup."""
        st, txt = _http("/i/e2e-gibtsnicht/")
        self.assertEqual(st, 503)
        self.assertIn("<p>", txt)


# ===========================================================================
# LIVE: round-trip to the agent VM (free /goal path only)
# ===========================================================================
@unittest.skipUnless(_orchestrator_running(), "orchestrator VM not running")
class LiveAgent(unittest.TestCase):
    @staticmethod
    def _reply(txt):
        # The manager proxy returns the reply as plain text; falls back to JSON
        # {"reply": ...} in case that ever changes.
        txt = txt.strip()
        if txt.startswith("{"):
            try:
                return json.loads(txt).get("reply", txt)
            except Exception:
                return txt
        return txt

    def test_goal_command_roundtrip(self):
        """Proves the new agent code is live in the VM — without a model call,
        so without token cost."""
        st, txt = _http("/i/orchestrator/api/chat", "POST",
                        {"message": "/goal show"}, timeout=30)
        self.assertEqual(st, 200)
        self.assertIn("goal", self._reply(txt))

    def test_tools_registry_after_rebuild(self):
        """Smoke test for a rootfs rebuild, no model call: '/tools' on an
        instance that was STARTED AFTER the current image was built must list
        its built-ins and every assigned MCP with the full 'server__tool'
        name. Instances still on an older image are skipped — a stale one
        would answer through the model (cost, no signal)."""
        img = os.path.join(FC_DIR, "instances", "openrouter-rootfs.ext4")
        if not os.path.exists(img):
            self.skipTest("no openrouter rootfs here")
        built = os.path.getmtime(img)
        st, txt = _http("/api/agents")
        fresh = []
        for a in json.loads(txt).get("agents", []):
            pf = os.path.join(FC_DIR, "run", a["name"] + ".pid")
            if a.get("running") and a.get("template") == "openrouter" \
                    and os.path.exists(pf) and os.path.getmtime(pf) > built:
                fresh.append(a)
        if not fresh:
            self.skipTest("no openrouter instance started on the current image")
        for a in fresh[:2]:
            # A freshly (re)started VM answers 502/503 through the proxy until
            # the agent bridge is up (MCP init takes ~20 s) — wait, don't fail.
            for _ in range(40):
                st, txt = _http(f"/i/{a['name']}/api/chat", "POST", {"message": "/tools"}, timeout=30)
                if st == 200:
                    break
                time.sleep(3)
            self.assertEqual(st, 200, f"{a['name']}: bridge not up after 120 s ({txt[:80]})")
            rep = self._reply(txt)
            self.assertIn("built-in (", rep, a["name"])
            for mcp in a.get("mcps", []):
                self.assertIn(mcp + "__", rep, f"{a['name']}: MCP '{mcp}' not registered")

    def test_reasoning_command_roundtrip(self):
        st, txt = _http("/i/orchestrator/api/chat", "POST",
                        {"message": "/reasoning"}, timeout=30)
        self.assertEqual(st, 200)
        # /reasoning without an argument shows the status -> some text comes back
        self.assertTrue(self._reply(txt))


if __name__ == "__main__":
    # Short environment report, then unittest.
    print(f"manager reachable: {_manager_up()} | orchestrator running: {_orchestrator_running()}",
          file=sys.stderr)
    unittest.main(verbosity=2)
