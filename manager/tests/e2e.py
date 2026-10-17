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
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
import re
import socket

# Every temp dir of a run lives under one parent that goes at exit: the
# tests' 86 mkdtemp calls left ~100 dirs in /tmp (a RAM disk) per run.
import atexit
tempfile.tempdir = tempfile.mkdtemp(prefix="kaim56-e2e-run-")
atexit.register(shutil.rmtree, tempfile.tempdir, True)

# A test server stopped with shutdown() waits out serve_forever's poll interval
# (0.5 s): ~25 tests paid it once or twice each. 20 ms polls cost nothing here.
import socketserver
socketserver.BaseServer.serve_forever.__defaults__ = (0.02,)

# The loopback services of a live install (voice :8770, embeddings :8772) are
# real endpoints: the manager code under test would call them (timeouts 2-30 s)
# and get live answers. Port 1 is closed: refused at once, the code's
# "service down" fallback runs — the same as on a box without them.
os.environ.setdefault("VOICE_PORT", "1")
os.environ.setdefault("EMBED_PORT", "1")

# --- paths to the modules under test ----------------------------------------
FC_DIR = os.environ.get("FC_DIR", "/home/ulrich/firecracker")
AGENT_PATH = os.environ.get("AGENT_PATH", "/home/ulrich/openrouter-agent/agent/__init__.py")   # the agent package
MANAGER_PATH = os.path.join(FC_DIR, "manager.py")
HTTPD_PATH = os.path.join(FC_DIR, "mgr", "httpd.py")     # the HTTP handler class H
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8700")

# manager.py imports the sibling module `chatui` -> its directory must be on the
# search path, otherwise the import fails in the manager unit tests.
if FC_DIR not in sys.path:
    sys.path.insert(0, FC_DIR)


def _load(name, path, env=None):
    """Load a module — or a package via its __init__.py — from a file under a
    fresh name; optionally set os.environ first. A package gets its own copy
    of every submodule per load, so env-dependent module state stays per test."""
    if env:
        os.environ.update(env)
    if os.path.basename(path) == "__init__.py":
        spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[os.path.dirname(path)])
    else:
        spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                  # relative imports inside a package need the parent registered
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
    if os.environ.get("E2E_OFFLINE"):      # offline tier only, even with a manager on :8700
        return False
    try:
        st, _ = _http("/api/agents", timeout=4)
        return st == 200
    except Exception:
        return False


def _orchestrator_running():
    if os.environ.get("E2E_OFFLINE"):
        return False
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
        cls.a._mgrclient.report_usage = lambda *a, **k: None   # no network to the manager
        # The guest derives the manager from its default gateway (.1:8700): on a
        # dev host that is the LAN router — a real endpoint that, when it drops
        # instead of refusing, stalls each call up to its 60 s timeout. A closed
        # loopback port refuses at once.
        cls.a._mgrclient._manager_base = lambda: "http://127.0.0.1:1"

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
        saved = (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url)
        urllib.request.urlopen = fake_urlopen
        a._mgrclient._llm_headers = lambda: {"Content-Type": "application/json"}
        a._mgrclient._llm_url = lambda: "http://x/v1/chat/completions"
        toks = []
        try:
            msg = a._llm.or_chat_stream([{"role": "user", "content": "hi"}],
                                   [{"type": "function", "function": {"name": "t", "parameters": {}}}],
                                   toks.append)
        finally:
            (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url) = saved
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
        saved = (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url)
        urllib.request.urlopen = lambda *ar, **kw: FakeResp()
        a._mgrclient._llm_headers = lambda: {"Content-Type": "application/json"}
        a._mgrclient._llm_url = lambda: "http://x/v1/chat/completions"
        toks = []
        try:
            msg = a._llm.or_chat_stream([{"role": "user", "content": "hi"}], None, toks.append)
        finally:
            (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url) = saved
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
        saved = (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url)
        urllib.request.urlopen = lambda *ar, **kw: FakeResp()
        a._mgrclient._llm_headers = lambda: {"Content-Type": "application/json"}
        a._mgrclient._llm_url = lambda: "http://x/v1/chat/completions"
        try:
            msg = a._llm.or_chat_stream([{"role": "user", "content": "hi"}], None, lambda t: None)
        finally:
            (urllib.request.urlopen, a._mgrclient._llm_headers, a._mgrclient._llm_url) = saved
        self.assertEqual(msg["content"], "only thought")  # no None -> no _(empty reply)_

    def test_steps_unlimited(self):
        """/steps accepts a number 1..x and 'unlimited' (0 = unlimited)."""
        import itertools
        a = self.a; saved = a._config.MAX_STEPS
        try:
            a._config._set_steps("/steps 5"); self.assertEqual(a._config.MAX_STEPS, 5)
            self.assertEqual(list(a._config._step_iter()), [0, 1, 2, 3, 4])
            a._config._set_steps("/steps 999"); self.assertEqual(a._config.MAX_STEPS, 999)   # no more 60-cap
            r = a._config._set_steps("/steps unlimited")
            self.assertLessEqual(a._config.MAX_STEPS, 0); self.assertIn("unlimited", r)
            self.assertIsInstance(a._config._step_iter(), itertools.count)           # unlimited
        finally:
            a._config.MAX_STEPS = saved

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
        saved = (a._llm.or_chat_stream, a._tools.exec_tool, a._config.HEARTBEAT_SEC, a._loop._drain_steer, a._loop._goal,
                 a._observe.trace_turn)
        ends = []
        a._observe.trace_turn = lambda event, **kw: ends.append(kw) if event == "end" else None
        a._llm.or_chat_stream = fake_stream; a._tools.exec_tool = fake_exec
        a._config.HEARTBEAT_SEC = 0.03; a._loop._drain_steer = lambda *x: False; a._loop._goal = None
        try:
            del a._context._history[1:]
            a._loop.run_stream("build something", toks.append)
        finally:
            (a._llm.or_chat_stream, a._tools.exec_tool, a._config.HEARTBEAT_SEC, a._loop._drain_steer, a._loop._goal,
             a._observe.trace_turn) = saved
        out = "".join(toks)
        self.assertIn("\U0001f527", out)   # Tool-Status
        self.assertEqual(ends[-1].get("answer"), out)                   # the trace keeps what the client got
        self.assertIn("\u00b7", out)        # Heartbeat waehrend Tool-Lauf
        self.assertIn("done", out)           # final reply afterwards

    # --- Backend-Auswahl (Kernstueck orcarouter/llama/openrouter) -----------
    def _select(self, env):
        """Run the backend-selection block from agent.py with env and return
        the resulting variables (isolated, without re-import)."""
        src = open(os.path.join(os.path.dirname(AGENT_PATH), "config.py")).read()
        block = src[src.index("LLAMA_ENDPOINT = os.environ"):src.index("WORKDIR = os.environ")]
        g = {"os": type("O", (), {"environ": dict(env)})(),
             "OR_URL": "https://openrouter.ai/api/v1/chat/completions",
             "OR_MODEL": env.get("OPENROUTER_MODEL", "openai/gpt-4o")}
        # The block calls os.environ.get(...) -> we need a real mapping.
        g["os"].environ = dict(env)
        exec(block, g)
        return g

    def test_mcp_image_result_becomes_a_workspace_file(self):
        """A browser screenshot from an MCP server (image content) is written
        to <workspace>/screenshots/; the tool text names the path (no base64),
        the pixels follow as a vision message after the tool round."""
        a = self.a
        tmp = tempfile.mkdtemp(prefix="e2e-mcpimg-")
        old = a._config.WORKDIR
        a._config.WORKDIR = tmp
        try:
            png = base64.b64encode(b"\x89PNG fake").decode()
            out = a._mcp._content_text({"content": [{"type": "text", "text": "shot"},
                                                     {"type": "image", "mimeType": "image/png", "data": png}]})
            path = re.search(r"saved screenshot: (\S+)", out).group(1)
            self.assertTrue(out.startswith("shot\n"))
            self.assertTrue(path.startswith(os.path.join(tmp, "screenshots")) and path.endswith(".png"))
            self.assertEqual(open(path, "rb").read(), b"\x89PNG fake")
            self.assertNotIn(png, out)
            self.assertIn("not saved", a._mcp._content_text({"content": [{"type": "image", "data": "!!notb64"}]}))
            # the saved image is also queued for the model: one user message
            # after the tool round, with the path and the pixels
            hist = [{"role": "tool", "tool_call_id": "t1", "content": out}]
            a._mcp.attach_images(hist)
            self.assertEqual(hist[-1]["role"], "user")
            self.assertIn(path, hist[-1]["content"][0]["text"])
            self.assertEqual(hist[-1]["content"][1]["image_url"]["url"], "data:image/png;base64," + png)
            a._mcp.attach_images(hist)                       # nothing pending -> nothing added
            self.assertEqual(len(hist), 2)
            # the next screenshot replaces the pixels of the previous one
            a._mcp._content_text({"content": [{"type": "image", "mimeType": "image/png", "data": png}]})
            a._mcp.attach_images(hist)
            self.assertEqual(sum(isinstance(m.get("content"), list) for m in hist), 1)
            self.assertIn(path, hist[1]["content"])
            # too big for the context (or not an image type): file only
            a._mcp._content_text({"content": [{"type": "image", "mimeType": "image/png",
                                               "data": "A" * (a._mcp.MAX_ATTACH_B64 + 4)}]})
            self.assertEqual(a._mcp._pending_images, [])
        finally:
            a._config.WORKDIR = old
            a._mcp._pending_images.clear()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bare_mcp_tool_name_resolves_when_unique(self):
        """gemini dropped the 'mrmusic__' prefix and got 'unknown tool'. A bare
        name resolves when exactly one MCP tool matches; two candidates stay
        unknown; built-ins are untouched. /tools lists the registry."""
        a = self.a
        class Srv:
            def __init__(self): self.calls = []
            def call(self, tool, args): self.calls.append((tool, args)); return "ON"
        old_tools, old_mcp, old_audit = dict(a._mcp._mcp_tools), dict(a._mcp._mcp), a._observe.audit
        try:
            a._observe.audit = lambda *x, **k: None
            srv = Srv()
            a._mcp._mcp_tools.clear(); a._mcp._mcp.clear()
            a._mcp._mcp_tools["mrmusic__mrmusic_power"] = ("mrmusic", "mrmusic_power")
            a._mcp._mcp["mrmusic"] = srv
            self.assertEqual(a._tools._resolve_tool_name("mrmusic_power"), "mrmusic__mrmusic_power")
            self.assertEqual(a._tools._resolve_tool_name("mrmusic__mrmusic_power"), "mrmusic__mrmusic_power")
            self.assertEqual(a._tools._resolve_tool_name("bash"), "bash")
            self.assertEqual(a._tools.exec_tool("mrmusic_power", {"on": True}), "ON")
            self.assertEqual(srv.calls, [("mrmusic_power", {"on": True})])
            a._mcp._mcp_tools["other__mrmusic_power"] = ("other", "mrmusic_power")
            self.assertEqual(a._tools._resolve_tool_name("mrmusic_power"), "mrmusic_power")   # ambiguous
            self.assertIn("unknown tool", a._tools.exec_tool("mrmusic_power", {}))
            rep = a._tools._tools_report()
            self.assertIn("mcp (2): mrmusic__mrmusic_power, other__mrmusic_power", rep)
            self.assertIn("built-in (", rep)
        finally:
            a._mcp._mcp_tools.clear(); a._mcp._mcp_tools.update(old_tools)
            a._mcp._mcp.clear(); a._mcp._mcp.update(old_mcp)
            a._observe.audit = old_audit

    def test_context_brake_keeps_a_long_tool_turn_inside_the_window(self):
        """fit_window: over LLM_CTX_FILL of the window, old tool outputs are cut
        (oldest first, the newest few whole, tool_call/result pairs intact),
        then earlier turns are folded into the summary; under it nothing
        changes; without a known window (cloud) there is no brake."""
        a = self.a
        c = a._context
        old = (list(c._history), c._ctx_window[0], c._summarize)
        try:
            c._summarize = lambda msgs, prior="", focus="": "earlier: user asked about apps"
            big = "x" * 9000
            hist = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"},
                    {"role": "user", "content": "read the manager"}]
            for k in range(8):
                hist.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"t{k}", "type": "function",
                             "function": {"name": "bash", "arguments": "{}"}}]})
                hist.append({"role": "tool", "tool_call_id": f"t{k}", "content": big})
            c._history[:] = [dict(m) for m in hist]
            c._ctx_window[0] = 0
            self.assertEqual(c.fit_window([]), "")                                # cloud: no brake
            c._ctx_window[0] = 100000
            self.assertEqual(c.fit_window([]), "")                                # fits: untouched
            c._ctx_window[0] = 16000                                              # budget 12000 tokens ~ 40k chars
            note = c.fit_window([])
            self.assertIn("shortened", note)
            tools = [m for m in c._history if m["role"] == "tool"]
            self.assertEqual(len(tools), 8)                                       # nothing removed: pairs intact
            self.assertTrue(all(len(m["content"]) == 9000 for m in tools[-4:]))   # newest stay whole
            self.assertTrue(tools[0]["content"].endswith("model's window]"))
            self.assertLessEqual(c.ctx_tokens(c._history, []), 12000)
            # still too big even after cutting -> earlier turns go into the summary
            c._history[:] = [dict(m) for m in hist]
            c._ctx_window[0] = 9000
            note = c.fit_window([])
            self.assertIn("earlier turns summarized", note)
            self.assertNotIn("old question", [m.get("content") for m in c._history])
            self.assertTrue(c._history[1]["content"].startswith(c.SUMMARY_TAG))
            self.assertEqual(c._history[2]["content"], "read the manager")        # the current turn stays
        finally:
            c._history[:], c._ctx_window[0], c._summarize = old

    def test_now_line_gives_the_model_a_clock(self):
        """Every turn carries exactly one [Now] system line in the instance's
        timezone; a second injection replaces, never duplicates."""
        a = self.a
        old_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/Berlin"
            line = a._context._now_line()
            self.assertTrue(line.startswith("[Now] "))
            self.assertIn("(Europe/Berlin, UTC+0", line)             # +01:00 or +02:00
            self.assertRegex(line, r"T18:30:00\+0[12]:00")             # tool-input example
            self.assertIn(time.strftime("%Y-%m-%d"), line)   # host and guest tz agree today
            a._context._history[:] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "Es ist 16:36 Uhr."}]
            a._context._inject_now(); a._context._inject_now()
            nows = [m for m in a._context._history if m["role"] == "system" and m["content"].startswith("[Now]")]
            self.assertEqual(len(nows), 1)
            self.assertEqual(a._context._history[0]["content"], "sys")
            self.assertTrue(a._context._history[-1]["content"].startswith("[Now]"))   # last = next to the question
            self.assertIn("outdated", a._context._history[-1]["content"])
            # playbooks and missions sit next to the question as well (rules at
            # the top were ignored: "12:31 Uhr" against a no-"Uhr" rule)
            old_get = a._mgrclient._mgr_get
            try:
                a._mgrclient._mgr_get = lambda base, path, timeout=30: (
                    json.dumps({"playbooks": [{"text": "keine Uhr"}]}) if "playbooks" in path
                    else json.dumps({"missions": []}))
                a._context._inject_playbooks(); a._context._inject_playbooks()
                pbs = [m for m in a._context._history if m["role"] == "system" and m["content"].startswith(a._context.PLAYBOOK_TAG)]
                self.assertEqual(len(pbs), 1)
                self.assertTrue(a._context._history[-1]["content"].startswith(a._context.PLAYBOOK_TAG))
                self.assertEqual(a._context._history[0]["content"], "sys")
                # standard skills: one block naming them, bodies stay out
                a._mgrclient._mgr_get = lambda base, path, timeout=30: json.dumps(
                    [{"name": "ponytail", "description": "lazy senior dev", "default": True}]) if "default=1" in path else "[]"
                a._context._std_cache["ts"] = 0
                a._context._inject_standard_skills(); a._context._inject_standard_skills()
                sks = [m for m in a._context._history if m["role"] == "system" and m["content"].startswith(a._context.STANDARD_SKILLS_TAG)]
                self.assertEqual(len(sks), 1)
                self.assertIn("- ponytail: lazy senior dev", sks[0]["content"])
                self.assertIn("load_skill", sks[0]["content"])
            finally:
                a._mgrclient._mgr_get = old_get
            os.environ["TZ"] = "Not/AZone"
            self.assertTrue(a._context._now_line().startswith("[Now] "))   # falls back, never raises
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz

    def test_memory_index_injected_from_folder(self):
        """With MEMORY_DIR the head of MEMORY.md sits in every turn's context
        (one block, refreshed); without a folder nothing is injected."""
        a = self.a
        tmp = tempfile.mkdtemp(prefix="e2e-memidx-")
        open(os.path.join(tmp, "MEMORY.md"), "w").write("# Memory of vc\n\n- [[radio]] Radio — Swiss Classic\n")
        old = a._context.MEMORY_DIR
        try:
            a._context.MEMORY_DIR = tmp
            a._context._history[:] = [{"role": "system", "content": "sys"}]
            a._context._inject_memory_index(); a._context._inject_memory_index()
            blocks = [m for m in a._context._history if m["content"].startswith(a._context.MEMINDEX_TAG)]
            self.assertEqual(len(blocks), 1)
            self.assertIn("[[radio]]", blocks[0]["content"])
            self.assertIn(tmp, blocks[0]["content"])
            a._context.MEMORY_DIR = ""
            a._context._inject_memory_index()
            self.assertEqual([m for m in a._context._history if m["content"].startswith(a._context.MEMINDEX_TAG)], [])
        finally:
            a._context.MEMORY_DIR = old

    def test_spawn_subagent_rides_the_task_path(self):
        """spawn_subagent no longer calls admin routes (403 for guests since
        08-14): it posts create_task target=ephemeral, wait=true, with the
        chosen model, and returns the result text."""
        a = self.a
        calls = []
        old = a._mgrclient._mgr
        try:
            a._mgrclient._mgr = lambda base, path, payload, timeout=30: (calls.append((path, payload, timeout)),
                                                              json.dumps({"ok": True, "result": "42"}))[1]
            self.assertEqual(a._tools_manager.t_spawn_subagent("count things", model="google/gemini-2.5-flash"), "42")
            path, payload, timeout = calls[0]
            self.assertEqual(path, "/api/task")
            self.assertEqual(payload, {"message": "count things", "target": "ephemeral",
                                       "wait": True, "model": "google/gemini-2.5-flash"})
            self.assertGreaterEqual(timeout, 600)
            a._mgrclient._mgr = lambda *x, **k: json.dumps({"error": "target 'ephemeral' not allowed"})
            self.assertIn("not allowed", a._tools_manager.t_spawn_subagent("x"))
            self.assertIn("missing", a._tools_manager.t_spawn_subagent("  "))
            # create_task forwards the model too
            calls.clear(); a._mgrclient._mgr = lambda base, path, payload, timeout=30: (calls.append(payload), json.dumps({"id": "1", "status": "pending", "target": "ephemeral"}))[1]
            a._tools_manager.t_create_task("later", model="m/x")
            self.assertEqual(calls[0]["model"], "m/x")
        finally:
            a._mgrclient._mgr = old

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
        out = self.a._offload._finalize_output("http_fetch", big)
        self.assertLess(len(out), len(big))
        self.assertIn('offload_read(id="', out)
        import re
        oid = re.search(r'id="([^"]+)"', out).group(1)
        back = self.a._offload.t_offload_read(id=oid, offset=0, length=25000)
        self.assertIn("XXXX", back)
        self.assertGreaterEqual(len(back), 19000)

    def test_offload_small_passthrough(self):
        self.assertEqual(self.a._offload._finalize_output("bash", "kurz"), "kurz")

    def test_offload_read_missing(self):
        self.assertIn("not found", self.a._offload.t_offload_read(id="gibtsnicht"))

    def test_offload_read_always_enabled(self):
        old = self.a._tools._TOOL_ALLOW
        try:
            self.a._tools._TOOL_ALLOW = {"bash"}          # strikte Allowlist
            self.assertTrue(self.a._tools.tool_enabled("offload_read"))
            self.assertFalse(self.a._tools.tool_enabled("http_fetch"))
        finally:
            self.a._tools._TOOL_ALLOW = old

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
        t = a._tools_local._html_to_text(html)
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
        old_d, old_b = a._tools_local._ddg_search, a._tools_local._bing_search
        try:
            a._tools_local._ddg_search = lambda q, c: None                # challenge
            a._tools_local._bing_search = lambda q, c: (_ for _ in ()).throw(OSError("net down"))
            out = a._tools_local.t_web_search("anything")
            self.assertIn("unavailable", out)
            self.assertIn("duckduckgo: blocked", out)
            self.assertIn("bing", out)
            self.assertIn("NOT an empty result", out)
            # A backend that answers with an EMPTY list is a real empty result.
            a._tools_local._ddg_search = lambda q, c: []
            self.assertEqual(a._tools_local.t_web_search("gibberishquery"), "no results")
            # And the fallback chain: DDG blocked, Bing delivers.
            a._tools_local._ddg_search = lambda q, c: None
            a._tools_local._bing_search = lambda q, c: [("Titel", "https://x.de", "Schnipsel")]
            out = a._tools_local.t_web_search("x")
            self.assertIn("Titel", out)
            self.assertIn("https://x.de", out)
        finally:
            a._tools_local._ddg_search, a._tools_local._bing_search = old_d, old_b

    def test_bing_redirect_urls_are_decoded(self):
        import base64
        a = self.a
        target = "https://de.wikipedia.org/wiki/Unternehmen"
        b64 = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        href = f"https://www.bing.com/ck/a?!&amp;&amp;p=xyz&amp;u=a1{b64}&amp;ntb=1"
        self.assertEqual(a._tools_local._bing_real_url(href), target)
        # Without the redirect wrapper the URL passes through untouched.
        self.assertEqual(a._tools_local._bing_real_url("https://example.org/x"), "https://example.org/x")

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
        n = a._llm._strip_history_images(hist)
        self.assertEqual(n, 2)
        flat = __import__("json").dumps(hist)
        self.assertNotIn("image_url", flat)             # no image parts left
        self.assertIn("was ist das?", flat)             # text survives
        self.assertIn("image removed", flat)
        self.assertEqual(a._llm._strip_history_images(hist), 0)   # idempotent

    def test_offload_preview_outlines_json(self):
        """A head-slice of a big JSON is an unclosed brace of the first record.
        The preview should say WHAT the payload is instead."""
        a = self.a
        rows = [{"id": i, "name": f"row {i}", "value": i * 3.14} for i in range(500)]
        out = __import__("json").dumps({"total": 500, "rows": rows})
        assert len(out) > a._offload.OFFLOAD_MIN, "test payload must trigger offloading"
        got = a._offload._finalize_output("http_fetch", out)
        self.assertIn("[JSON structure]", got)
        self.assertIn("rows", got)                     # the key survives
        self.assertIn("500 items", got)                # the count survives
        self.assertIn("offload_read", got)             # the full text is reachable
        self.assertLess(len(got), a._offload.OFFLOAD_PREVIEW + 400)

    def test_offload_preview_folds_logs_and_keeps_errors(self):
        a = self.a
        noise = "GET /health 200 0.001s"
        lines = [noise] * 800 + ["ERROR: db connection refused"] + [noise] * 800
        out = "\n".join(lines)
        assert len(out) > a._offload.OFFLOAD_MIN
        got = a._offload._finalize_output("bash", out)
        self.assertIn("repeats ×800", got)             # duplicates folded
        self.assertIn("ERROR: db connection refused", got)   # the problem survives
        self.assertIn("1601 lines", got)
        self.assertLess(len(got), a._offload.OFFLOAD_PREVIEW + 400)

    def test_offload_preview_plain_text_stays_head_slice(self):
        a = self.a
        out = ("word " * 20000).strip()
        got = a._offload._finalize_output("read_file", out)
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
        old = a._mgrclient._mgr_get
        try:
            a._mgrclient._mgr_get = lambda base, path, **k: catalog
            bare = a._tools_manager.t_list_skills()
            self.assertIn("docker", bare)
            self.assertNotIn("containers", bare)       # no descriptions
            hit = a._tools_manager.t_list_skills(query="container")
            self.assertIn("Docker expert", hit)        # description on demand
            self.assertNotIn("git-expert", hit)
            miss = a._tools_manager.t_list_skills(query="quantum")
            self.assertIn("No skill matches", miss)
        finally:
            a._mgrclient._mgr_get = old

    def test_instance_skills_ride_the_standard_skills_block(self):
        """The agent names what /api/skills?default=1 returns — the manager
        adds the instance's own SKILLS there — in ONE standard-skills block."""
        a, ctx = self.a, self.a._context
        seen, old = [], (a._mgrclient._mgr_get, list(ctx._history), dict(ctx._std_cache))
        try:
            a._mgrclient._mgr_get = lambda base, path, **k: (seen.append(path), json.dumps(
                [{"name": "security-audit", "description": "std"}, {"name": "tdd", "description": "the instance's own"}]))[1]
            ctx._std_cache.update(ts=0.0, items=[])
            ctx._history[:] = [{"role": "system", "content": "sys"}]
            ctx._inject_standard_skills(); ctx._inject_standard_skills()
            blocks = [x["content"] for x in ctx._history if x["content"].startswith(ctx.STANDARD_SKILLS_TAG)]
            self.assertEqual(seen, ["/api/skills?default=1"])
            self.assertEqual(len(blocks), 1)
            self.assertIn("- tdd: the instance's own", blocks[0])
        finally:
            a._mgrclient._mgr_get = old[0]
            ctx._history[:] = old[1]
            ctx._std_cache.clear(); ctx._std_cache.update(old[2])

    def test_hook_denylist_blocks_rmrf(self):
        allow, reason = self.a._tools._hook_before_tool("bash", {"command": "sudo rm -rf / --no-preserve-root"})
        self.assertFalse(allow)
        self.assertIn("rm -rf /", reason)

    def test_hook_denylist_blocks_forkbomb(self):
        allow, _ = self.a._tools._hook_before_tool("bash", {"command": ":(){:|:&};:"})
        self.assertFalse(allow)

    def test_hook_allows_normal(self):
        allow, _ = self.a._tools._hook_before_tool("bash", {"command": "ls -la"})
        self.assertTrue(allow)

    def test_hitl_default_off(self):
        self.assertFalse(self.a._tools.HITL)   # opt-in, otherwise it blocks nothing

    def test_notify_tool_registered(self):
        self.assertIn("notify", self.a._tools.BUILTIN)

    # --- /model: Laufzeit-Modellwechsel --------------------------------------
    def test_model_switch(self):
        a = self.a
        old = (a._config.OR_MODEL, a._config.OR_URL, a._config.LLM_NAME, a._config.LLM_KEY_SECRET, a._config.LLM_BACKEND, a._config.OR_KEY)
        try:
            self.assertIn("Model:", a._config._set_model("/model"))
            a._config._set_model("/model orcarouter:foo/bar")
            self.assertEqual(a._config.OR_MODEL, "foo/bar")
            self.assertIn("orcarouter.ai", a._config.OR_URL)
            self.assertEqual(a._config.LLM_KEY_SECRET, "ORCAROUTER_API_KEY")
            a._config._set_model("/model only-model-id")           # without provider: model only
            self.assertEqual(a._config.OR_MODEL, "only-model-id")
            self.assertIn("orcarouter.ai", a._config.OR_URL)       # Backend unveraendert
        finally:
            (a._config.OR_MODEL, a._config.OR_URL, a._config.LLM_NAME, a._config.LLM_KEY_SECRET, a._config.LLM_BACKEND, a._config.OR_KEY) = old

    # --- Steering -------------------------------------------------------------
    def test_history_persists_across_restart(self):
        """The conversation is written to <MEMORY_DIR>/.state/history.json
        between turns and read back at start: the per-turn injections and
        image bytes are dropped, summaries/branches kept, the system prompt
        is the current one, the saved timestamp feeds the auto-reset clock."""
        import tempfile, shutil
        a, ctx, ps = self.a, self.a._context, self.a._persist
        tmp = tempfile.mkdtemp(prefix="e2e-hist-")
        old = (ctx.MEMORY_DIR, list(ctx._history), ps._last[0], a._loop._last_turn[0], ps.PERSIST)
        try:
            ctx.MEMORY_DIR = tmp; ps.PERSIST = True; ps._last[0] = ""
            ctx._history[:] = [
                {"role": "system", "content": "OLD SYS"},
                {"role": "system", "content": ctx.SUMMARY_TAG + " earlier: did X"},
                {"role": "system", "content": ctx.PLAYBOOK_TAG + " rules"},
                {"role": "user", "content": [{"type": "text", "text": "what is this"},
                                             {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "t1", "function": {"name": "shell", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "t1", "content": "out"},
                {"role": "system", "content": ctx.NOW_TAG + " Monday"},
                {"role": "system", "content": ctx.MEMINDEX_TAG + " idx"},
                {"role": "system", "content": ctx.MISSION_TAG + " m"},
                {"role": "system", "content": ctx.RECALL_TAG + " r"},
                {"role": "assistant", "content": "a cat"},
            ]
            self.assertTrue(ps.save())
            self.assertFalse(ps.save())                                   # unchanged: no rewrite
            f = os.path.join(tmp, ".state", "history.json")
            self.assertTrue(os.path.exists(f))
            self.assertNotIn("AAAA", open(f).read())                      # image bytes not stored
            # "restart": fresh history with a NEW system prompt, then restore
            ctx._history[:] = [{"role": "system", "content": "NEW SYS"}]
            ps._last[0] = ""; a._loop._last_turn[0] = 0.0
            self.assertTrue(a._loop.restore())
            roles = [(m["role"], str(m.get("content") or "")[:12]) for m in ctx._history]
            self.assertEqual(roles[0], ("system", "NEW SYS"))
            self.assertTrue(ctx._history[1]["content"].startswith(ctx.SUMMARY_TAG + " earlier"))
            self.assertEqual([r for r, _ in roles], ["system", "system", "user", "assistant", "tool", "assistant"])
            self.assertIn("what is this", ctx._history[2]["content"])
            self.assertIn("image not kept", ctx._history[2]["content"])
            self.assertEqual(ctx._history[3]["tool_calls"][0]["id"], "t1")   # tool_calls + result intact
            self.assertGreater(a._loop._last_turn[0], 0)                     # idle time counts for AUTO_RESET_MIN
            self.assertFalse(a._loop.restore())                              # already running: no second restore
            # a broken file is ignored, the agent starts fresh
            ctx._history[:] = [{"role": "system", "content": "S"}]
            open(f, "w").write("{not json")
            self.assertFalse(a._loop.restore())
            self.assertEqual(len(ctx._history), 1)
            # no memory folder / switched off: nothing written, nothing read
            ctx.MEMORY_DIR = ""
            self.assertFalse(ps.save()); self.assertFalse(ps.load())
            ctx.MEMORY_DIR = tmp; ps.PERSIST = False
            self.assertFalse(ps.save())
        finally:
            ctx.MEMORY_DIR, ps._last[0], a._loop._last_turn[0], ps.PERSIST = old[0], old[2], old[3], old[4]
            ctx._history[:] = old[1]
            shutil.rmtree(tmp, ignore_errors=True)

    def test_turn_ends_with_history_on_disk(self):
        """run() and the slash commands save after the turn (a /reset leaves
        an empty list, so a restart does not bring the old context back)."""
        import tempfile, shutil, json
        a, ctx, ps = self.a, self.a._context, self.a._persist
        tmp = tempfile.mkdtemp(prefix="e2e-hist-")
        old = (ctx.MEMORY_DIR, list(ctx._history), ps._last[0], a._loop._tool_loop, ps.PERSIST)
        try:
            ctx.MEMORY_DIR = tmp; ps.PERSIST = True; ps._last[0] = ""
            ctx._history[:] = [{"role": "system", "content": "S"}]
            a._loop._tool_loop = lambda hist: (hist.append({"role": "assistant", "content": "hello back"}), "hello back")[1]
            self.assertEqual(a._loop.run("hello"), "hello back")
            f = os.path.join(tmp, ".state", "history.json")
            saved = json.load(open(f))["messages"]
            self.assertEqual([m["role"] for m in saved][-2:], ["user", "assistant"])
            self.assertFalse(any(m["role"] == "system" and m["content"].startswith(ctx.NOW_TAG) for m in saved))
            a._loop.run("/reset")
            self.assertEqual(json.load(open(f))["messages"], [])
        finally:
            ctx.MEMORY_DIR, ps._last[0], a._loop._tool_loop, ps.PERSIST = old[0], old[2], old[3], old[4]
            ctx._history[:] = old[1]
            shutil.rmtree(tmp, ignore_errors=True)

    def test_compact_summarizes_and_replaces_history(self):
        """/compact folds the whole conversation into one [Summary] block, keeps
        the system prompt, passes the focus to the summarizer, and reports empty."""
        a = self.a
        ctx = a._context
        captured = {}
        old = a._llm.or_chat
        try:
            def fake(msgs, tools, model=None):
                captured["sys"] = msgs[0]["content"]
                return {"content": "- did X\n- open: Y"}
            a._llm.or_chat = fake
            ctx._history[:] = [{"role": "system", "content": "SYS"},
                               {"role": "user", "content": "find jobs in NRW"},
                               {"role": "assistant", "content": "here are three"},
                               {"role": "user", "content": "more detail please"}]
            out = ctx._compact("job listings in Bonn")
            self.assertIn("compacted", out.lower())
            self.assertIn("job listings in Bonn", captured["sys"])
            self.assertEqual(len(ctx._history), 2)
            self.assertEqual(ctx._history[0]["content"], "SYS")
            self.assertTrue(ctx._history[1]["content"].startswith(ctx.SUMMARY_TAG))
            self.assertIn("did X", ctx._history[1]["content"])
            ctx._history[:] = [{"role": "system", "content": "SYS"}]
            self.assertIn("empty", ctx._compact("").lower())
            self.assertEqual(len(ctx._history), 1)
        finally:
            a._llm.or_chat = old

    def test_steering_queue(self):
        a = self.a
        self.assertFalse(a._loop.steer_push("x"))               # idle -> reject
        a._loop._busy[0] = True
        try:
            self.assertTrue(a._loop.steer_push("hold course"))
            hist = []
            self.assertTrue(a._loop._drain_steer(hist))
            self.assertEqual(hist[0]["role"], "user")
            self.assertIn("hold course", hist[0]["content"])
            self.assertIn("[Steering", hist[0]["content"])
            self.assertFalse(a._loop._drain_steer(hist))         # queue empty
        finally:
            a._loop._busy[0] = False

    # --- Prompt-Templates -----------------------------------------------------
    def test_prompt_expansion(self):
        a = self.a
        a._context._prompts_cache["map"] = {"daily": "Write the daily briefing."}
        a._context._prompts_cache["ts"] = __import__("time").time()
        self.assertEqual(a._context._expand_prompt("/daily"), "Write the daily briefing.")
        self.assertEqual(a._context._expand_prompt("/daily just short"),
                         "Write the daily briefing. just short")
        self.assertEqual(a._context._expand_prompt("/reset"), "/reset")     # built-in takes precedence
        self.assertEqual(a._context._expand_prompt("/gibtsnicht"), "/gibtsnicht")
        self.assertEqual(a._context._expand_prompt("normal text"), "normal text")

    # --- Plugin-Loader ----------------------------------------------------------
    def test_plugin_loader(self):
        a = self.a
        tmp = tempfile.mkdtemp(prefix="e2e-plug-")
        with open(os.path.join(tmp, "echoplug.py"), "w") as fh:
            fh.write('DESC="Echo"\nPARAMS={"t":{"type":"string"}}\nREQUIRED=["t"]\n'
                     'def run(t):\n    return "ECHO:" + t\n')
        with open(os.path.join(tmp, "bash.py"), "w") as fh:      # collision -> ignore
            fh.write('DESC="evil"\ndef run():\n    return "no"\n')
        old_dir = a._tools.PLUGIN_DIR
        try:
            a._tools.PLUGIN_DIR = tmp
            a._tools.load_plugins()
            self.assertIn("echoplug", a._tools.BUILTIN)
            self.assertIn("echoplug", a._tools.PLUGIN_TOOLS)
            self.assertEqual(a._tools.BUILTIN["echoplug"][0]("hi"), "ECHO:hi")
            self.assertNotIn("bash", a._tools.PLUGIN_TOOLS)              # collision blocked
        finally:
            a._tools.PLUGIN_DIR = old_dir
            a._tools.BUILTIN.pop("echoplug", None)
            a._tools.PLUGIN_TOOLS.discard("echoplug")

    def test_turn_deadline_and_per_turn_steps(self):
        """A turn with a deadline stops calling tools before it runs out and
        answers with what it has; '/steps N <text>' (alias /maxSteps) caps the
        steps for that turn only."""
        a = self.a
        old = a._llm.or_chat, a._tools.exec_tool, a._config.MAX_STEPS, a._context._deadline[0], list(a._context._history)
        calls = []
        try:
            def chat(msgs, tools, model=None):
                final = msgs[-1].get("content") == a._loop._DEADLINE_NOTE
                calls.append(not final)
                if not final:
                    return {"role": "assistant", "content": None,
                            "tool_calls": [{"id": "1", "function": {"name": "web_search", "arguments": "{}"}}]}
                return {"role": "assistant", "content": "partial: 2 hits so far"}
            a._llm.or_chat = chat
            a._tools.exec_tool = lambda name, args: "hit"
            hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "search"}]
            a._context._deadline[0] = time.time() + a._loop.DEADLINE_MARGIN - 1          # already inside the margin
            out = a._loop._tool_loop(hist)
            self.assertIn("partial: 2 hits", out)
            self.assertIn("time budget", out)
            self.assertEqual(calls, [False])                                # one call, tools off
            self.assertTrue(any(a._loop._DEADLINE_NOTE in str(m.get("content")) for m in hist))
            # a deadline far away: the loop runs its steps as usual
            calls.clear(); a._context._deadline[0] = time.time() + 3600; a._config.MAX_STEPS = 2
            self.assertEqual(a._loop._tool_loop([{"role": "user", "content": "x"}]), "(max tool steps reached)")
            self.assertEqual(calls, [True, True])
            # /steps N <text> for one turn, /maxSteps alias, then the old cap again
            a._config.MAX_STEPS = 12
            self.assertEqual(a._loop._turn_steps("/maxSteps 100 Tägliche Jobsuche"), (100, "Tägliche Jobsuche"))
            self.assertEqual(a._loop._turn_steps("/steps unlimited go"), (0, "go"))
            self.assertIsNone(a._loop._turn_steps("/steps 30"))                   # the setter, not a turn
            self.assertIsNone(a._loop._turn_steps("Jobsuche /steps 3"))
            seen = []
            old_loop = a._loop._tool_loop
            a._loop._tool_loop = lambda hist: (seen.append(a._config.MAX_STEPS), "ok")[1]
            try:
                self.assertEqual(a._loop.run("/steps 3 hallo", deadline=0), "ok")
            finally:
                a._loop._tool_loop = old_loop
            self.assertEqual(seen, [3])
            self.assertEqual(a._config.MAX_STEPS, 12)
        finally:
            a._llm.or_chat, a._tools.exec_tool, a._config.MAX_STEPS, a._context._deadline[0] = old[:4]
            a._context._history[:] = old[4]

    def test_turn_markers_and_spans(self):
        """A turn posts start/end markers (kind, steps, ms, outcome), every LLM
        call a usage span with turn/step/ms, every tool call an audit line with
        ms; slash commands produce no trace."""
        a = self.a
        posts = []
        old = a._mgrclient._mgr, a._llm.or_chat, a._tools.exec_tool, a._mgrclient.report_usage, list(a._context._history), a._config.MAX_STEPS, a._tools.BUILTIN.get("web_search")
        try:
            a._mgrclient._mgr = lambda base, path, payload=None, timeout=60: posts.append((path, payload))
            a._mgrclient.report_usage = lambda u, ms=None, ok=True, err="": posts.append(("/api/usage", {"turn": a._observe._turn_id[0], "step": a._observe._turn_step[0], "ms": ms, "ok": ok, "err": err}))
            calls = [0]
            def chat(msgs, tools, model=None):
                calls[0] += 1
                a._observe._turn_step[0] += 1; a._mgrclient.report_usage({}, ms=5)      # what or_chat does
                if calls[0] == 1:
                    return {"role": "assistant", "content": None,
                            "tool_calls": [{"id": "1", "function": {"name": "web_search", "arguments": "{}"}}]}
                return {"role": "assistant", "content": "done"}
            a._llm.or_chat = chat
            a._tools.BUILTIN["web_search"] = (lambda **kw: "1. hit", {}, [])
            a._config.MAX_STEPS = 5
            self.assertEqual(a._loop.run("/fresh find it", kind="task", turn="abc12345"), "done")
            self.assertTrue(all(b["turn"] == "abc12345" for p, b in posts if p == "/api/trace"))   # named by the bridge
            kinds = [(p, b.get("event")) for p, b in posts if p == "/api/trace"]
            self.assertEqual(kinds, [("/api/trace", "start"), ("/api/trace", "end")])
            end = next(b for p, b in posts if p == "/api/trace" and b["event"] == "end")
            self.assertEqual((end["kind"], end["steps"], end["outcome"]), ("fresh", 2, "ok"))
            self.assertGreaterEqual(end["ms"], 0)
            turn = end["turn"]
            usage = [b for p, b in posts if p == "/api/usage"]
            self.assertEqual([u["step"] for u in usage], [1, 2])
            self.assertTrue(all(u["turn"] == turn for u in usage))
            audits = [b for p, b in posts if p == "/api/audit"]
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0]["turn"], turn)
            self.assertIn("ms", audits[0])
            posts.clear()
            a._loop.run("/steps")                                       # a slash command: no markers
            self.assertEqual([p for p, _ in posts if p == "/api/trace"], [])
            self.assertEqual(a._learn._outcome_of("(max tool steps reached)"), "max_steps")
            self.assertEqual(a._learn._outcome_of("x ⏱️ (time budget exhausted — partial result)"), "deadline")
            self.assertEqual(a._learn._outcome_of("⚠️ LLM HTTP 500"), "error")
        finally:
            a._mgrclient._mgr, a._llm.or_chat, a._tools.exec_tool, a._mgrclient.report_usage = old[:4]
            a._context._history[:] = old[4]; a._config.MAX_STEPS = old[5]
            if old[6] is not None:
                a._tools.BUILTIN["web_search"] = old[6]
            else:
                a._tools.BUILTIN.pop("web_search", None)

    def test_skill_distilled_after_a_long_successful_turn(self):
        """After a turn with enough model calls that ended well, one extra
        model call distills a SKILL proposal and files it; NONE files
        nothing; short turns, failed turns and slash commands never trigger."""
        a = self.a
        posts = []
        old = a._llm.or_chat, a._mgrclient._mgr, a._learn.SKILL_LEARN, a._learn.SKILL_LEARN_MIN_STEPS, a._observe._turn_step[0], threading.Thread

        class SyncThread:
            def __init__(self, target=None, args=(), daemon=None): self.t, self.a = target, args
            def start(self): self.t(*self.a)
        try:
            a._mgrclient._mgr = lambda base, path, payload=None, timeout=60: posts.append((path, payload)) or "ok"
            threading.Thread = SyncThread
            a._learn.SKILL_LEARN, a._learn.SKILL_LEARN_MIN_STEPS = True, 3
            hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "old answer"},
                    {"role": "user", "content": "find jobs"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "web_search", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "x" * 5000},
                    {"role": "assistant", "content": "done"}]
            sl = a._learn._turn_slice(hist, "find jobs")
            self.assertEqual(sl[0]["content"], "find jobs"); self.assertEqual(len(sl), 4)
            self.assertEqual(len(sl[2]["content"]), 1500)                       # tool output trimmed
            a._llm.or_chat = lambda msgs, tools, model=None: {"role": "assistant", "content":
                '```json\n{"name": "job-search", "description": "Daily search", "content": "# Purpose\\n..."}\n```'}
            a._observe._turn_step[0] = 4
            self.assertTrue(a._learn._maybe_learn(hist, "find jobs", "ok"))
            self.assertEqual(posts[-1][0], "/api/skill-proposals")
            self.assertEqual(posts[-1][1]["name"], "job-search")
            self.assertIn("find jobs", posts[-1][1]["note"])
            posts.clear()
            a._llm.or_chat = lambda msgs, tools, model=None: {"role": "assistant", "content": "NONE"}
            self.assertTrue(a._learn._maybe_learn(hist, "find jobs", "ok")); self.assertEqual(posts, [])
            a._observe._turn_step[0] = 2
            self.assertFalse(a._learn._maybe_learn(hist, "find jobs", "ok"))              # too short
            a._observe._turn_step[0] = 9
            self.assertFalse(a._learn._maybe_learn(hist, "find jobs", "max_steps"))       # did not end well
            self.assertFalse(a._learn._maybe_learn(hist, "/fresh x", "ok"))               # slash command
            a._learn.SKILL_LEARN = False
            self.assertFalse(a._learn._maybe_learn(hist, "find jobs", "ok"))
        finally:
            a._llm.or_chat, a._mgrclient._mgr, a._learn.SKILL_LEARN, a._learn.SKILL_LEARN_MIN_STEPS, a._observe._turn_step[0], threading.Thread = old

    def test_distiller_sees_the_catalog_and_the_discards(self):
        """RRSI's proposal-side rule: the distiller is told which skills exist
        and which proposals were discarded (with the reason), so it does not
        redraw a turned-down idea."""
        a = self.a
        seen, posts = [], []
        old = a._llm.or_chat, a._mgrclient._mgr, a._mgrclient._mgr_get
        try:
            a._mgrclient._mgr_get = lambda base, path, timeout=30: json.dumps(
                {"skills": ["job-search"], "discarded": [{"name": "cv-mailer", "description": "mail CVs", "reason": "one-off"}]})
            a._mgrclient._mgr = lambda base, path, payload=None, timeout=60: posts.append(payload) or "ok"
            a._llm.or_chat = lambda msgs, tools, model=None: seen.append(msgs[0]["content"]) or {"content": "NONE"}
            a._learn._learn_skill([{"role": "user", "content": "x"}], "x")
            self.assertIn("job-search", seen[0])
            self.assertIn("cv-mailer: mail CVs (reason: one-off)", seen[0])
            self.assertEqual(posts, [])
            a._mgrclient._mgr_get = lambda *a_, **k: (_ for _ in ()).throw(OSError("manager away"))
            seen.clear(); a._learn._learn_skill([{"role": "user", "content": "x"}], "x")
            self.assertTrue(seen[0].startswith(a._learn._LEARN_SYSTEM))            # no history: still distils
        finally:
            a._llm.or_chat, a._mgrclient._mgr, a._mgrclient._mgr_get = old

    def test_fixed_context_is_measured_per_part(self):
        """The cost rule's input: the agent reports what each fixed part of the
        context costs at turn start (system, tools, injected blocks)."""
        a = self.a
        cx = a._context
        hist = [{"role": "system", "content": "S" * 400},
                {"role": "system", "content": cx.PLAYBOOK_TAG + " " + "p" * 199},
                {"role": "system", "content": cx.MEMINDEX_TAG + " " + "m" * 386},
                {"role": "user", "content": "u" * 80}, {"role": "assistant", "content": "a" * 40},
                {"role": "system", "content": cx.RECALL_TAG + " " + "r" * 32}]
        got = cx.ctx_sizes(hist, [{"type": "function", "function": {"name": "x" * 380}}])
        self.assertEqual({k: got[k] for k in ("system", "playbooks", "memory_index", "recall", "conversation")},
                         {"system": 100, "playbooks": 52, "memory_index": 100, "recall": 10, "conversation": 30})
        self.assertGreater(got["tools"], 95)

    # --- Tree-Chat: /branch + /back -------------------------------------------
    def test_branch_and_back(self):
        a = self.a
        old_hist = list(a._context._history)
        old_chat = a._llm.or_chat
        try:
            a._context._history[:] = [{"role": "system", "content": "s"},
                             {"role": "user", "content": "main topic"}]
            a._llm.or_chat = lambda msgs, tools, model=None: {"role": "assistant",
                                                         "content": "essence of the follow-up"}
            out = a._context._branch_open("/branch piper")
            self.assertIn("depth 1", out)
            self.assertEqual(a._context._branch_depth(), 1)
            a._context._history.append({"role": "user", "content": "follow-up?"})
            a._context._history.append({"role": "assistant", "content": "reply in the branch"})
            out = a._context._branch_close("/back")
            self.assertEqual(a._context._branch_depth(), 0)
            self.assertIn("main topic", out)
            # branch content gone, sidenote present, origin intact
            joined = " | ".join(str(m.get("content")) for m in a._context._history)
            self.assertNotIn("reply in the branch", joined)
            self.assertIn(a._context.NOTE_TAG, joined)
            self.assertIn("main topic", joined)
            # /back without a branch
            self.assertIn("No open", a._context._branch_close("/back"))
        finally:
            a._llm.or_chat = old_chat
            a._context._history[:] = old_hist

    def test_branch_drop(self):
        a = self.a
        old_hist = list(a._context._history)
        try:
            a._context._history[:] = [{"role": "system", "content": "s"}]
            a._context._branch_open("/branch x")
            a._context._history.append({"role": "user", "content": "geheim"})
            a._context._branch_close("/back drop")
            joined = " | ".join(str(m.get("content")) for m in a._context._history)
            self.assertNotIn("geheim", joined)
            self.assertNotIn(a._context.NOTE_TAG, joined)   # spurlos
        finally:
            a._context._history[:] = old_hist

    # --- Goal-Kommando ------------------------------------------------------
    def test_goal_set_show_off(self):
        try:
            self.assertIn("No goal", self.a._loop._set_goal("/goal show"))
            self.a._loop._set_goal("/goal Antworte knapp.")
            self.assertEqual(self.a._loop._goal, "Antworte knapp.")
            self.a._loop._set_goal("/goal off")
            self.assertIsNone(self.a._loop._goal)
        finally:
            self.a._loop._goal = None

    # --- Summarizing conversation manager -----------------------------------
    def test_local_model_crash_on_image_is_not_retried_and_images_leave_history(self):
        """llama.cpp closed the connection on every image (a crash, then a
        model reload): no retry queue behind it, the image parts leave the
        history so the next turn cannot trigger it again, the user gets one
        clear line. A 503 while the model loads is answered the same way."""
        import http.client, urllib.error
        a = self.a
        old = a._config.LLAMA_ENDPOINT, urllib.request.urlopen, a._llm._retry_sleep
        slept = []
        try:
            a._config.LLAMA_ENDPOINT = "http://10.0.0.5:8080/"
            a._llm._retry_sleep = lambda n: slept.append(n)
            msgs = [{"role": "system", "content": "S"},
                    {"role": "user", "content": [{"type": "text", "text": "was siehst du?"},
                                                 {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]}]
            def drop(req, timeout=None):
                raise http.client.RemoteDisconnected("Remote end closed connection without response")
            urllib.request.urlopen = drop
            r = a._llm.or_chat(msgs, [])
            self.assertIn("image was removed", r["content"])
            self.assertEqual(slept, [])                                              # no retry
            self.assertFalse(any(isinstance(p, dict) and p.get("type") == "image_url"
                                 for m in msgs if isinstance(m.get("content"), list) for p in m["content"]))
            r = a._llm.or_chat(msgs, [])
            self.assertIn("probably restarting", r["content"])                      # no image any more
            def loading(req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b'{"error":{"message":"Loading model"}}'))
            urllib.request.urlopen = loading
            r = a._llm.or_chat(msgs, [])
            self.assertIn("loading its model", r["content"])
            self.assertEqual(r.get("role"), "assistant")   # the error reply lands in the history: it needs a role (llama.cpp 500 "Missing 'role'")
            self.assertEqual(slept, [])
        finally:
            a._config.LLAMA_ENDPOINT, urllib.request.urlopen, a._llm._retry_sleep = old

    def test_local_model_empty_stream_after_image_counts_as_dropped(self):
        """The streaming variant of the crash: llama.cpp sends the 200 headers,
        dies on the image, the stream ends with nothing — that was shown as
        "(empty reply)" and the image stayed in the history."""
        import io
        a = self.a
        old = a._config.LLAMA_ENDPOINT, urllib.request.urlopen, a._mgrclient.report_usage
        reported = []
        try:
            a._config.LLAMA_ENDPOINT = "http://10.0.0.5:8080/"
            a._mgrclient.report_usage = lambda u, ms=None, ok=True, err="": reported.append((ok, err))
            urllib.request.urlopen = lambda req, timeout=None: io.BytesIO(b"")     # 200, then nothing
            msgs = [{"role": "system", "content": "S"},
                    {"role": "user", "content": [{"type": "text", "text": "was siehst du?"},
                                                 {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]}]
            out = []
            r = a._llm.or_chat_stream(msgs, [], out.append)
            self.assertIn("image was removed", r["content"])
            self.assertIn("image was removed", "".join(out))
            self.assertEqual(reported, [(False, r["content"])])
            self.assertNotIn("image_url", json.dumps(msgs))
        finally:
            a._config.LLAMA_ENDPOINT, urllib.request.urlopen, a._mgrclient.report_usage = old

    def test_spawn_subagent_passes_the_sandbox(self):
        """The tool sends tools/egress/skill as one `sandbox` object, and none
        when nothing narrows."""
        a = self.a
        seen = []
        old = a._mgrclient._mgr
        try:
            a._mgrclient._mgr = lambda base, path, payload=None, timeout=60: seen.append((path, payload)) or '{"result": "done"}'
            self.assertEqual(a._tools_manager.t_spawn_subagent("do it"), "done")
            self.assertNotIn("sandbox", seen[-1][1])
            a._tools_manager.t_spawn_subagent("do it", tools="bash,read_file", egress="none", skill="pdf-digest")
            self.assertEqual(seen[-1][1]["sandbox"], {"tools": "bash,read_file", "egress": "none", "skill": "pdf-digest"})
            self.assertEqual(seen[-1][1]["target"], "ephemeral")
        finally:
            a._mgrclient._mgr = old

    def test_auto_reset_after_idle_minutes(self):
        """AUTO_RESET_MIN: a context that idled longer than that starts over at
        the next turn; a recent turn, a fresh context or 0 leave it alone."""
        a = self.a
        old = a._loop.AUTO_RESET_MIN, a._loop._last_turn[0], list(a._context._history)
        try:
            a._context._history[:] = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
            a._loop.AUTO_RESET_MIN = 0; a._loop._last_turn[0] = time.time() - 3600
            self.assertFalse(a._loop._auto_reset()); self.assertEqual(len(a._context._history), 3)
            a._loop.AUTO_RESET_MIN = 30; a._loop._last_turn[0] = time.time() - 600
            self.assertFalse(a._loop._auto_reset()); self.assertEqual(len(a._context._history), 3)      # 10 min idle: keep
            a._loop._last_turn[0] = time.time() - 3600
            self.assertTrue(a._loop._auto_reset()); self.assertEqual(len(a._context._history), 1)       # 60 min idle: fresh
            a._loop._last_turn[0] = time.time() - 3600
            self.assertFalse(a._loop._auto_reset())                                             # already fresh: nothing to drop
            a._loop._last_turn[0] = 0.0; a._context._history.append({"role": "user", "content": "u"})
            self.assertFalse(a._loop._auto_reset())                                             # first turn after boot: keep
        finally:
            a._loop.AUTO_RESET_MIN, a._loop._last_turn[0] = old[0], old[1]; a._context._history[:] = old[2]

    def test_llm_timeouts_and_retry_policy_for_local_models(self):
        """A local model gets long timeouts and no retry after a timeout (it is
        still busy with the request that timed out); cloud backends keep the
        short timeouts and retry."""
        a = self.a
        old = a._config.LLAMA_ENDPOINT
        try:
            self.assertGreaterEqual(a._llm.LLM_STREAM_TIMEOUT, a._llm.LLM_TIMEOUT)
            a._config.LLAMA_ENDPOINT = "http://10.0.0.5:8080/"
            self.assertFalse(a._llm._retry_after(TimeoutError("timed out"), 0))
            self.assertFalse(a._llm._retry_after(OSError("The read operation timed out"), 0))
            self.assertTrue(a._llm._retry_after(OSError("connection reset"), 0))
            a._config.LLAMA_ENDPOINT = ""
            self.assertTrue(a._llm._retry_after(TimeoutError("timed out"), 0))
            self.assertFalse(a._llm._retry_after(TimeoutError("timed out"), a._llm.LLM_RETRIES))
        finally:
            a._config.LLAMA_ENDPOINT = old

    def test_wire_messages_never_leaves_adjacent_assistant(self):
        """The goal loop appends assistant answers with a note between them;
        folding the notes for a local model must not leave two assistant
        messages adjacent (Qwen: HTTP 400 "2 or more assistant messages")."""
        a = self.a
        old = a._llm.FOLD_SYSTEM
        try:
            a._llm.FOLD_SYSTEM = True
            msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "first"},
                    {"role": "system", "content": "critique"},          # folded away
                    {"role": "assistant", "content": "second"}]
            w = a._llm._wire_messages(msgs)
            roles = [m["role"] for m in w]
            self.assertEqual(roles, ["system", "user", "assistant"])     # the two answers merged
            self.assertIn("first", w[-1]["content"]); self.assertIn("second", w[-1]["content"])
            # a tool-call assistant is never merged away
            msgs2 = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"},
                     {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                     {"role": "tool", "tool_call_id": "1", "content": "r"},
                     {"role": "assistant", "content": "done"}]
            self.assertEqual([m["role"] for m in a._llm._wire_messages(msgs2)],
                             ["system", "user", "assistant", "tool", "assistant"])
        finally:
            a._llm.FOLD_SYSTEM = old

    def test_recall_dedups_and_budgets(self):
        """A-3: a recall hit already in the memory index is dropped, exact
        duplicates are dropped, and the block is capped by RECALL_MAX_CHARS."""
        a = self.a
        old = a._mgrclient._mgr, list(a._context._history), a._context.MEMORY_DIR, a._context.RECALL_MAX_CHARS
        try:
            a._context.MEMORY_DIR = ""
            a._context.RECALL_MAX_CHARS = 60
            hits = [{"text": "Ulrich bikes to work", "score": 0.9},
                    {"text": "Ulrich bikes to work", "score": 0.9},
                    {"text": "likes Radio Lauder in the mornings", "score": 0.9},
                    {"text": "a third distinct fact that pushes over the budget", "score": 0.9}]
            a._mgrclient._mgr = lambda base, path, payload=None, timeout=60: json.dumps({"hits": hits})
            a._context._history[:] = [{"role": "system", "content": a._config.SYSTEM},
                             {"role": "system", "content": a._context.MEMINDEX_TAG + " index: Ulrich bikes to work"}]
            a._context._recall("how does he commute?")
            block = [mm for mm in a._context._history if str(mm.get("content","")).startswith(a._context.RECALL_TAG)]
            self.assertEqual(len(block), 1)
            txt = block[0]["content"]
            self.assertNotIn("bikes to work", txt)
            self.assertIn("Radio Lauder", txt)
            self.assertNotIn("third distinct fact", txt)
            self.assertEqual(txt.count("- "), 1)
        finally:
            a._mgrclient._mgr, a._context.MEMORY_DIR, a._context.RECALL_MAX_CHARS = old[0], old[2], old[3]
            a._context._history[:] = old[1]

    def test_maybe_learn_gated_and_visible(self):
        """A-4: skill-learning fires only on a long successful non-slash turn,
        is off when SKILL_LEARN is false, and logs when it fires (visibility)."""
        a = self.a
        old = a._learn.SKILL_LEARN, a._learn.SKILL_LEARN_MIN_STEPS, a._observe._turn_step[0], a._learn.threading, a._config.log
        started = []; logged = []
        class _T:
            def __init__(self, target=None, args=(), daemon=None): pass
            def start(self): started.append(1)
        try:
            a._learn.threading = type("x", (), {"Thread": _T})
            a._config.log = lambda *m, **k: logged.append(" ".join(str(x) for x in m))
            a._learn.SKILL_LEARN_MIN_STEPS = 5
            a._learn.SKILL_LEARN = True; a._observe._turn_step[0] = 6
            self.assertTrue(a._learn._maybe_learn([], "do a multi-step job", "ok")); self.assertEqual(len(started), 1)
            self.assertTrue(any("skill-learn" in x for x in logged))
            started.clear()
            self.assertFalse(a._learn._maybe_learn([], "/reset", "ok")); self.assertEqual(started, [])   # slash: no
            self.assertFalse(a._learn._maybe_learn([], "x", "error")); self.assertEqual(started, [])      # bad outcome: no
            a._observe._turn_step[0] = 2
            self.assertFalse(a._learn._maybe_learn([], "x", "ok")); self.assertEqual(started, [])         # too short: no
            a._observe._turn_step[0] = 6; a._learn.SKILL_LEARN = False
            self.assertFalse(a._learn._maybe_learn([], "x", "ok")); self.assertEqual(started, [])         # disabled: no
        finally:
            a._learn.SKILL_LEARN, a._learn.SKILL_LEARN_MIN_STEPS, a._observe._turn_step[0], a._learn.threading, a._config.log = old

    def test_office_tools_helpers_and_missing_libs(self):
        """write_xlsx/write_docx: row normalisation (lists, objects, JSON) and
        the Markdown-subset parser are pure; without the libs the tools say so
        instead of crashing (the libs live in the rootfs, not on the host)."""
        a = self.a
        self.assertEqual(a._tools_local._rows_norm([["a", "b"], [1, 2]]), [["a", "b"], [1, 2]])
        self.assertEqual(a._tools_local._rows_norm([{"firma": "X", "ort": "K"}, {"firma": "Y", "link": "u"}]),
                         [["firma", "ort", "link"], ["X", "K", ""], ["Y", "", "u"]])
        self.assertEqual(a._tools_local._rows_norm('[["h"],["v"]]'), [["h"], ["v"]])
        with self.assertRaises(ValueError):
            a._tools_local._rows_norm({"not": "a list"})
        blocks = a._tools_local._md_blocks("# Titel\n\nErster Absatz\nzweite Zeile\n\n- eins\n- **zwei**\n\n## Sub\nText")
        self.assertEqual(blocks, [("h", 1, "Titel"), ("p", "Erster Absatz zweite Zeile"),
                                  ("li", "eins"), ("li", "**zwei**"), ("h", 2, "Sub"), ("p", "Text")])
        import importlib
        have_x = importlib.util.find_spec("openpyxl") is not None
        have_d = importlib.util.find_spec("docx") is not None
        if not have_x:
            self.assertIn("openpyxl", a._tools_local.t_write_xlsx("t.xlsx", [["a"]]))
        if not have_d:
            self.assertIn("python-docx", a._tools_local.t_write_docx("t.docx", "# x"))
        self.assertIn("write_xlsx", a._tools.BUILTIN); self.assertIn("write_docx", a._tools.BUILTIN)   # registered tools

    def test_reset_clears_the_goal(self):
        """/reset drops a stale goal; otherwise every later turn runs the goal
        loop (which is what broke the uncensored instance)."""
        a = self.a
        old = a._loop._goal
        try:
            a._loop._goal = "write a file to the host"
            out = []
            a._loop.run_stream("/reset", out.append)
            self.assertIsNone(a._loop._goal)
            self.assertIn("reset", "".join(out).lower())
        finally:
            a._loop._goal = old

    def test_wire_strict_alternation_invariant(self):
        """A-5: after wiring for a local model, no two adjacent messages share a
        role (user OR assistant), and tool_calls/tool sequences are untouched."""
        a = self.a
        old = a._llm.FOLD_SYSTEM
        try:
            a._llm.FOLD_SYSTEM = True
            msgs = [{"role": "system", "content": "S"},
                    {"role": "user", "content": "u1"},
                    {"role": "user", "content": "u2 (steer)"},          # two users -> must merge
                    {"role": "assistant", "content": "a1"},
                    {"role": "system", "content": "note"},              # folded away
                    {"role": "assistant", "content": "a2"},             # two assistants -> merge
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                    {"role": "tool", "tool_call_id": "1", "content": "r"},
                    {"role": "assistant", "content": "done"}]
            w = a._llm._wire_messages(msgs)
            roles = [m["role"] for m in w]
            for i in range(1, len(roles)):
                self.assertFalse(roles[i] == roles[i-1] and roles[i] in ("user", "assistant")
                                 and not w[i].get("tool_calls") and not w[i-1].get("tool_calls"),
                                 f"adjacent {roles[i]} at {i}: {roles}")
            self.assertIn("u1", w[1]["content"]); self.assertIn("u2", w[1]["content"])
            self.assertEqual(roles.count("tool"), 1)                    # tool sequence intact
            self.assertEqual(roles[-1], "assistant"); self.assertEqual(w[-1]["content"], "done")
        finally:
            a._llm.FOLD_SYSTEM = old

    def test_wire_messages_folds_system_notes_for_local_models(self):
        """Qwen3's chat template in llama.cpp rejects a system message that is
        not the first one; the agent's [Memory]/[Playbooks]/date notes are
        such messages. For the llama backend they fold into the first system
        message, in order; other backends get the history untouched."""
        a = self.a
        old = a._llm.FOLD_SYSTEM
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "u1"},
                {"role": "system", "content": "[Memory] m"}, {"role": "assistant", "content": "a1"},
                {"role": "system", "content": "   "}, {"role": "system", "content": "[Now] d"},
                {"role": "user", "content": "u2"}]
        try:
            a._llm.FOLD_SYSTEM = True
            w = a._llm._wire_messages(msgs)
            self.assertEqual([m["role"] for m in w], ["system", "user", "assistant", "user"])
            self.assertEqual(w[0]["content"], "SYS\n\n[Memory] m\n\n[Now] d")
            self.assertEqual(msgs[0]["content"], "SYS")                           # the history itself is untouched
            self.assertEqual(a._llm._wire_messages([{"role": "user", "content": "x"}]), [{"role": "user", "content": "x"}])
            a._llm.FOLD_SYSTEM = False
            self.assertIs(a._llm._wire_messages(msgs), msgs)
        finally:
            a._llm.FOLD_SYSTEM = old

    def test_summarizing_split(self):
        a = self.a
        old_sum, old_max, old_keep, old_hist = a._context._summarize, a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT, list(a._context._history)
        try:
            a._context._summarize = lambda msgs, prior="": f"MOCK(prior={prior or '-'},n={len(msgs)})"
            a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT = 6, 4
            h = [{"role": "system", "content": a._config.SYSTEM}]
            for i in range(5):
                h += [{"role": "user", "content": f"f{i}"}, {"role": "assistant", "content": f"a{i}"}]
            a._context._history[:] = h
            a._context._trim_history()
            self.assertTrue(a._context._history[1]["content"].startswith(a._context.SUMMARY_TAG))
            self.assertEqual(a._context._history[2]["role"], "user")        # recent an user-Grenze
            self.assertEqual(a._context._history[-1]["content"], "a4")      # juengste bleibt
        finally:
            a._context._summarize, a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT = old_sum, old_max, old_keep
            a._context._history[:] = old_hist

    def test_summarizing_folds_prior(self):
        a = self.a
        seen = {}
        old_sum, old_max, old_keep, old_hist = a._context._summarize, a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT, list(a._context._history)
        try:
            def fake_sum(msgs, prior=""):
                seen["prior"] = prior
                return "NEU"
            a._context._summarize = fake_sum
            a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT = 4, 2
            h = [{"role": "system", "content": a._config.SYSTEM},
                 {"role": "system", "content": a._context.SUMMARY_TAG + " ALT"}]
            for i in range(4):
                h += [{"role": "user", "content": f"f{i}"}, {"role": "assistant", "content": f"a{i}"}]
            a._context._history[:] = h
            a._context._trim_history()
            self.assertEqual(seen.get("prior"), "ALT")             # alte Zusammenfassung eingefaltet
        finally:
            a._context._summarize, a._context.CTX_MAX_MSGS, a._context.CTX_PRESERVE_RECENT = old_sum, old_max, old_keep
            a._context._history[:] = old_hist

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

        orig = urllib.request.urlopen
        try:
            urllib.request.urlopen = fake_urlopen
            a._llm.or_chat([{"role": "user", "content": "hi"}], [])       # leere Tools
            self.assertNotIn("tools", captured["body"])
            self.assertNotIn("tool_choice", captured["body"])
            a._llm.or_chat([{"role": "user", "content": "hi"}],
                      [{"type": "function", "function": {"name": "x", "parameters": {}}}])
            self.assertIn("tools", captured["body"])
            self.assertEqual(captured["body"]["tool_choice"], "auto")
        finally:
            urllib.request.urlopen = orig

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

        orig_open, orig_base = urllib.request.urlopen, a._mgrclient._manager_base
        old = (a._config.OR_MODEL, a._config.OR_URL, a._config.LLM_NAME, a._config.LLM_KEY_SECRET,
               a._config.LLM_BACKEND, a._config.OR_KEY)
        os.environ["KEY_PROXY"] = "1"
        a._config.OR_KEY = "sk-super-geheim"          # darf NIE im Request auftauchen
        a._config.LLM_BACKEND = "openrouter"
        try:
            urllib.request.urlopen = fake_urlopen
            a._mgrclient._manager_base = lambda: "http://172.30.0.1:8700"
            self.assertEqual(
                a._mgrclient._llm_url(),
                "http://172.30.0.1:8700/api/llm/openrouter/chat/completions")
            a._llm.or_chat([{"role": "user", "content": "hi"}], [])
            self.assertEqual(
                captured["url"],
                "http://172.30.0.1:8700/api/llm/openrouter/chat/completions")
            self.assertNotIn("authorization", captured["headers"])
            self.assertNotIn("sk-super-geheim", json.dumps(captured["headers"]))
            # /model-Wechsel muss im Proxy-Modus die PROXY-URL wechseln
            a._config._set_model("/model orcarouter:tencent/hy3")
            self.assertEqual(
                a._mgrclient._llm_url(),
                "http://172.30.0.1:8700/api/llm/orcarouter/chat/completions")
        finally:
            os.environ.pop("KEY_PROXY", None)
            urllib.request.urlopen, a._mgrclient._manager_base = orig_open, orig_base
            (a._config.OR_MODEL, a._config.OR_URL, a._config.LLM_NAME, a._config.LLM_KEY_SECRET,
             a._config.LLM_BACKEND, a._config.OR_KEY) = old

    def test_llm_async_job_mode_and_max_tokens(self):
        """LLM_ASYNC=1 (proxy mode): or_chat submits a manager job and polls it
        with short requests instead of holding one open; an upstream error in
        the result is the same error message as in sync mode; the stream call
        emits the job's text at once; LLM_MAX_TOKENS rides in every body;
        without the key proxy the job mode stays off."""
        import http.server
        a = self.a
        log, polls = [], {}

        class M(http.server.BaseHTTPRequestHandler):
            def _out(self, code, obj):
                out = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                log.append(("POST", self.path, body))
                jid = "bad" if body["messages"][-1]["content"] == "fail" else f"j{len(log)}"
                polls[jid] = 0
                self._out(202, {"job": jid})

            def do_GET(self):
                log.append(("GET", self.path, None))
                jid = self.path.rsplit("/", 1)[-1]
                polls[jid] += 1
                if polls[jid] < 3:
                    return self._out(200, {"status": "running"})
                if jid == "bad":
                    return self._out(400, {"error": {"message": "context too long"}})
                self._out(200, {"choices": [{"message": {"role": "assistant", "content": "done!",
                                                         "reasoning": "thought"}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), M)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = (a._config.LLM_ASYNC, a._config.LLM_MAX_TOKENS, a._config.LLM_BACKEND, a._llm.LLM_POLL_SEC,
               a._mgrclient._manager_base)
        os.environ["KEY_PROXY"] = "1"
        try:
            a._config.LLM_ASYNC, a._config.LLM_MAX_TOKENS, a._config.LLM_BACKEND = True, 777, "openrouter"
            a._llm.LLM_POLL_SEC = 0.01
            a._mgrclient._manager_base = lambda: f"http://127.0.0.1:{srv.server_address[1]}"
            msg = a._llm.or_chat([{"role": "user", "content": "hi"}], [])
            self.assertEqual(msg["content"], "done!")
            self.assertEqual(log[0][:2], ("POST", "/api/llm/openrouter/jobs"))
            self.assertEqual(log[0][2]["max_tokens"], 777)
            self.assertEqual([x[1] for x in log[1:]], ["/api/llm/jobs/j1"] * 3)       # short polls until done
            msg = a._llm.or_chat([{"role": "user", "content": "fail"}], [])
            self.assertIn("HTTP 400", msg["content"]); self.assertIn("context too long", msg["content"])
            toks = []
            msg = a._llm.or_chat_stream([{"role": "user", "content": "hi"}], [], toks.append)
            self.assertEqual(msg["content"], "done!")
            self.assertEqual("".join(toks), a._config.THINK_START + "thought" + a._config.THINK_END + "done!")
            self.assertNotIn("stream", log[-4][2])
            os.environ.pop("KEY_PROXY")                     # no manager in the path: no job mode
            self.assertFalse(a._llm._async_on())
        finally:
            os.environ.pop("KEY_PROXY", None)
            srv.shutdown(); srv.server_close()
            (a._config.LLM_ASYNC, a._config.LLM_MAX_TOKENS, a._config.LLM_BACKEND, a._llm.LLM_POLL_SEC,
             a._mgrclient._manager_base) = old

    def test_key_proxy_off_keeps_direct_url(self):
        """Without KEY_PROXY everything stays as before: direct backend URL."""
        a = self.a
        os.environ.pop("KEY_PROXY", None)
        self.assertEqual(a._mgrclient._llm_url(), a._config.OR_URL)


# ===========================================================================
# OFFLINE: Manager-Funktionen
# ===========================================================================
class AgentSquad(unittest.TestCase):
    def test_squad_ask_polls_until_answered(self):
        """squad_ask posts the question, then polls the answer endpoint; a
        refused call returns the manager's reason, not a traceback."""
        import urllib.error
        a = _load("agent_squad_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-sq-"),
                                                  "OPENROUTER_API_KEY": "dummy"})
        tm, c = a._tools_manager, a._mgrclient
        sent, polls = [], iter([{"status": "pending"}, {"status": "answered", "answer": "main", "from": "b"}])
        c._manager_base = lambda: "http://mgr"
        c._mgr = lambda base, path, payload=None, timeout=60: (sent.append((path, payload)), json.dumps(
            {"id": 7, "project": "sq", "delivered": ["b"], "blocked": ""}))[1]
        c._mgr_get = lambda base, path, timeout=30: json.dumps(next(polls))
        sleep = time.sleep
        tm.time.sleep = lambda s: None
        try:
            self.assertEqual(tm.t_squad_ask("b", "which branch?"), "b answered: main")
            self.assertEqual(sent[0], ("/api/squad/ask", {"to": "b", "question": "which branch?", "project": ""}))
            self.assertIn("delivered to b", tm.t_squad_post("b", "hi"))
            def refuse(base, path, payload=None, timeout=60):
                raise urllib.error.HTTPError(path, 403, "Forbidden", {}, io.BytesIO(b'{"error": "you are in no squad-enabled project"}'))
            c._mgr = refuse
            self.assertEqual(tm.t_squad_post("b", "hi"), "Error: you are in no squad-enabled project")
        finally:
            tm.time.sleep = sleep
        for t in ("squad_post", "squad_ask", "squad_read", "squad_members"):
            self.assertIn(t, a._tools.BUILTIN)

    def test_squad_gate_tools(self):
        """squad_propose / ticket_* call the gate routes; the answer says the
        user approves first, a ticket change names whom it woke; a refusal
        carries the manager's reason."""
        import urllib.error
        a = _load("agent_sqgate_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-sg-"),
                                                   "OPENROUTER_API_KEY": "dummy"})
        tm, c = a._tools_manager, a._mgrclient
        sent, reply = [], {}
        c._manager_base = lambda: "http://mgr"
        c._mgr = lambda base, path, payload=None, timeout=60: (sent.append((path, payload)), json.dumps(reply))[1]
        reply.update(id="p1", project="sq", kind="squad", status="pending", warnings=["f: persona 'x' does not exist"])
        out = tm.t_squad_propose("ship", [{"name": "e"}], [{"title": "t"}], 1000)
        self.assertEqual(sent[-1], ("/api/squad/propose", {"goal": "ship", "members": [{"name": "e"}], "tickets": [{"title": "t"}],
                                                           "budget_tokens_per_day": 1000, "project": ""}))
        self.assertIn("waiting for the user's approval", out); self.assertIn("persona 'x'", out)
        reply.update(version=2)
        self.assertIn("Proposal p1 is now v2, waiting for approval", tm.t_squad_propose("ship", [{"name": "e"}]))
        self.assertIn("Revise proposal", a._tools.BUILTIN["squad_propose"][1])
        reply.clear(); reply.update(id=1, key="T1", status="done", title="tests", assignee="e", depends_on=[],
                                    woke={"T2": "woken"})
        self.assertEqual(tm.t_ticket_update("1", "done"), "#1 T1 [done] tests -> e\nT2: woken")
        self.assertEqual(sent[-1][0], "/api/squad/ticket-update")
        def refuse(base, path, payload=None, timeout=60):
            raise urllib.error.HTTPError(path, 400, "Bad", {}, io.BytesIO(b'{"error": "waiting for approval: x"}'))
        c._mgr = refuse
        self.assertEqual(tm.t_ticket_create("x"), "Error: waiting for approval: x")
        for t in ("squad_propose", "squad_request_member", "squad_request_merge",
                  "ticket_list", "ticket_update", "ticket_create", "ticket_assign"):
            self.assertIn(t, a._tools.BUILTIN)

    def test_toolchain_tools(self):
        """toolchain_propose posts the spec to the gate (the user approves first);
        toolchain_status lists it and says whether this VM has the layer."""
        a = _load("agent_tc_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-tc-"),
                                               "OPENROUTER_API_KEY": "dummy"})
        tm, c = a._tools_manager, a._mgrclient
        sent = []
        c._manager_base = lambda: "http://mgr"
        c._mgr = lambda base, path, payload=None, timeout=60: (sent.append((path, payload)), json.dumps(
            {"id": "p9", "project": "sq", "kind": "toolchain", "status": "pending"}))[1]
        out = tm.t_toolchain_propose(["golang-go"], "go version", {"GOFLAGS": "-mod=mod"}, "Go for all")
        self.assertEqual(sent[-1], ("/api/squad/toolchain-propose", {"apt": ["golang-go"], "script": "go version",
                                    "env": {"GOFLAGS": "-mod=mod"}, "reason": "Go for all", "project": ""}))
        self.assertIn("waiting for the user's approval", out)
        c._mgr_get = lambda base, path, timeout=30: json.dumps({"project": "sq", "version": 2, "active": True,
            "spec": {"apt": ["gcc"], "script": "", "env": {"CC": "gcc"}}, "templates": [{"template": "openrouter", "state": "ready", "layer_version": 2}]})
        st = tm.t_toolchain_status()
        for x in ("toolchain v2", "HAS it", "apt: gcc", "env: CC=gcc", "openrouter ready (v2)"):
            self.assertIn(x, st)
        c._mgr_get = lambda base, path, timeout=30: json.dumps({"project": "sq", "spec": {"apt": [], "script": "", "env": {}}})
        self.assertIn("no shared toolchain yet", tm.t_toolchain_status())
        for t in ("toolchain_propose", "toolchain_status"):
            self.assertIn(t, a._tools.BUILTIN)


    def test_squad_catalog_tool(self):
        """squad_catalog GETs /api/squad/catalog and lists what a lead picks
        from; squad_propose documents template and new_persona."""
        a = _load("agent_sqcat_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-sc-"),
                                                  "OPENROUTER_API_KEY": "dummy"})
        tm, c = a._tools_manager, a._mgrclient
        got = []
        c._manager_base = lambda: "http://mgr"
        c._mgr_get = lambda base, path, timeout=30: (got.append(path), json.dumps(_CATALOG))[1]
        out = tm.t_squad_catalog()
        self.assertEqual(got, ["/api/squad/catalog?project="])
        for line in ("- coder: You code. [tools: bash]", "new_persona", "- std (standard): s", "- strong: openrouter/acme/big",
                     "- local: no model assigned", "- claude: Claude Code"):
            self.assertIn(line, out)
        self.assertIn("squad_catalog", a._tools.BUILTIN)
        props = a._tools.BUILTIN["squad_propose"][2]["members"]["items"]["properties"]
        self.assertEqual(props["template"]["enum"], ["openrouter", "claude"]); self.assertIn("new_persona", props)

    def test_squad_git_tools(self):
        """git_push_branch / pr_* / issue_* call /api/git/<op> on the manager
        (no token in the guest); empty arguments are left out; a refusal
        carries the manager's reason."""
        import urllib.error
        a = _load("agent_sqgit_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-sgit-"),
                                                  "OPENROUTER_API_KEY": "dummy"})
        tm, c = a._tools_manager, a._mgrclient
        sent, reply = [], {}
        c._manager_base = lambda: "http://mgr"
        c._mgr = lambda base, path, payload=None, timeout=60: (sent.append((path, payload)), json.dumps(reply))[1]
        reply.update(project="wp", repo="own/rep", number=4, title="Add X", state="open", author="bot",
                     url="https://github.com/own/rep/pull/4", head="squad/b/wp", base="main")
        self.assertEqual(tm.t_pr_create("Add X", "body"),
                         "Opened PR #4 [open] Add X (bot) squad/b/wp -> main https://github.com/own/rep/pull/4")
        self.assertEqual(sent[-1], ("/api/git/pr-create", {"title": "Add X", "body": "body"}))
        reply.clear(); reply.update(branch="squad/b/wp", commit="abcdef0123456789", repo="own/rep")
        self.assertEqual(tm.t_git_push_branch(project="wp"), "Pushed abcdef012345 to squad/b/wp on own/rep.")
        self.assertEqual(sent[-1], ("/api/git/push", {"project": "wp"}))
        reply.clear(); reply.update(items=[], repo="own/rep")
        self.assertEqual(tm.t_issue_list(), "No open issues in own/rep.")
        self.assertEqual(sent[-1], ("/api/git/issues", {"state": "open"}))
        def refuse(base, path, payload=None, timeout=60):
            raise urllib.error.HTTPError(path, 403, "Forbidden", {}, io.BytesIO(b'{"error": "pr_create: writers and the lead only (you are reader)"}'))
        c._mgr = refuse
        self.assertEqual(tm.t_pr_create("x"), "Error: pr_create: writers and the lead only (you are reader)")
        for t in ("git_push_branch", "git_fetch", "pr_create", "pr_list", "pr_view", "pr_comment",
                  "issue_create", "issue_list", "issue_comment"):
            self.assertIn(t, a._tools.BUILTIN)

    def test_squad_approval_hook_and_ask_human(self):
        """In a squad (policy from the manager, cached): a listed tool or a risky bash
        command returns "waiting for approval <id>" without waiting; approved passes;
        other commands pass without asking; the denylist stays first; outside a squad
        the old opt-in HITL (off) asks nothing. ask_human posts the question."""
        a = _load("agent_hitl_e2e", AGENT_PATH, {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-hq-"),
                                                 "OPENROUTER_API_KEY": "dummy"})
        tl, c = a._tools, a._mgrclient
        pol, sent, reply, gets = {}, [], {"id": "abc123abc123", "status": "open"}, []
        c._manager_base = lambda: "http://mgr"
        c._mgr_get = lambda base, path, timeout=30: (gets.append(path), json.dumps(pol))[1]
        c._mgr = lambda base, path, payload=None, timeout=60: (sent.append((path, payload)), json.dumps(reply))[1]
        hook = tl._hook_before_tool
        self.assertEqual(hook("bash", {"command": "curl http://x"}), (True, ""))     # no squad, HITL off
        self.assertEqual((sent, gets), ([], ["/api/hitl/policy"]))
        pol.update(squad="sq", tools=["git_push_branch", "pr_create", "issue_create", "bash"],
                   bash_re=r"\b(curl|wget)\b|\bgit\s+push\b")
        tl._policy["ts"] = 0
        ok, why = hook("bash", {"command": "curl http://x"})
        self.assertFalse(ok)
        self.assertEqual(why, "waiting for approval abc123abc123 — end your turn; you will be woken with the decision")
        self.assertEqual(sent[-1], ("/api/hitl", {"tool": "bash", "target": "curl http://x", "async": True}))
        self.assertEqual(hook("bash", {"command": "ls -la"}), (True, "")); self.assertEqual(len(sent), 1)
        self.assertFalse(hook("git_push_branch", {"name": "x"})[0])
        self.assertEqual(sent[-1][1]["target"], '{"name": "x"}')
        self.assertEqual(hook("read_file", {"path": "a"}), (True, ""))
        self.assertEqual(hook("bash", {"command": "rm -rf / ; curl x"})[1], "blocked by security rule (rm -rf /)")
        self.assertTrue(tl.exec_tool("issue_create", {"title": "x"}).startswith(
            "Tool 'issue_create' not executed: waiting for approval abc123abc123"))
        reply.update(status="approved")
        self.assertEqual(hook("pr_create", {"title": "t"}), (True, ""))
        self.assertEqual(len(gets), 2)                                               # cached for POLICY_TTL
        sent.clear(); reply.clear(); reply.update(id="q1q1q1q1q1q1", ticket="T1", status="open")
        self.assertEqual(a._tools_manager.t_ask_human("Which DB?", ["pg", "sqlite"]),
                         "asked q1q1q1q1q1q1 — end your turn; the answer wakes you (ticket T1 is blocked until then)")
        self.assertEqual(sent[-1], ("/api/hitl/ask", {"question": "Which DB?", "options": ["pg", "sqlite"], "ticket": ""}))
        self.assertIn("ask_human", tl.BUILTIN); self.assertIn("END YOUR TURN", tl.BUILTIN["ask_human"][1])
        reply.clear(); reply.update(id=1, key="T1", status="review", title="t", assignee="e", depends_on=[], note="waits for review")
        self.assertEqual(a._tools_manager.t_ticket_update("1", "done"), "#1 T1 [review] t -> e\nwaits for review")

_CATALOG = {"project": "sq", "personas": [{"name": "coder", "description": "You code.", "tools": ["bash"]}],
            "skills": [{"name": "std", "description": "s", "default": True}],
            "tiers": [{"tier": "strong", "models": ["openrouter/acme/big"]}, {"tier": "local", "models": []}],
            "templates": [{"template": "claude", "note": "Claude Code on the user's subscription"}]}


def _wtext(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def _wtextp(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _wtext(path, text)


class ManagerFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load("manager_e2e", MANAGER_PATH)
        # the live projects.json (root-only) must not reach any mount test
        cls.m._projects.PROJECTS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-noproj-"), "projects.json")
        # nor any squad record / archive the real squads dir (mgr/squadrec.py)
        cls.m._squadrec.REC_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-norec-"), "squadrec.json")
        cls.m._settings.SITE["SQUADS_DIR"] = tempfile.mkdtemp(prefix="e2e-nosquads-")
        # nor the live human-in-the-loop queue, nor its notifications (mgr/hitlq.py)
        cls.m._hitlq.QUEUE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-hq-"), "hitlq.json")
        cls.m._notify.NOTIF_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-notif-"), "notifications.json")

    def test_open_manager_warns_at_start(self):
        """Without MANAGER_PASS every request is an admin: the start says so in
        the log and as a notification; with a password it stays quiet."""
        m = self.m
        sent, old = [], (m._auth.PW, m._notify.notify_add)
        try:
            m._notify.notify_add = lambda *a, **k: sent.append(a)
            m._auth.PW = ""
            self.assertTrue(m._startup.warn_if_open())
            self.assertEqual(sent[0][1], "No login password")
            sent.clear(); m._auth.PW = "s3cret"
            self.assertFalse(m._startup.warn_if_open()); self.assertEqual(sent, [])
        finally:
            m._auth.PW, m._notify.notify_add = old

    def test_resource_stats_shape(self):
        """resource_stats returns per instance size + live fields; an instance
        without a PID counts as not running (live values None)."""
        m = self.m
        old_load = m._instances.load_instances
        m._instances.load_instances = lambda: [{"name": "e2e-res-xyz", "vcpus": 4, "mem_mib": 2048, "config": {}}]
        try:
            r = next(x for x in m._resources.resource_stats() if x["name"] == "e2e-res-xyz")
            self.assertEqual(r["vcpus"], 4)
            self.assertEqual(r["mem_mib"], 2048)
            self.assertFalse(r["running"])
            self.assertIsNone(r["rss_mb"])
            for k in ("cpu_pct", "upper_used_mb", "persist", "name"):
                self.assertIn(k, r)
        finally:
            m._instances.load_instances = old_load

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
        old_src, old_pins = m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE
        m._plugins.PLUGINS_SRC = tmp
        m._plugins.PLUGIN_PINS_FILE = os.path.join(tmp, ".pins.json")
        try:
            self.assertIsNone(m._plugins.plugin_write_py("foo", "DESC='x'\ndef run():\n    return 1\n"))
            lst = {p["name"]: p for p in m._plugins.list_plugins()}
            self.assertTrue(lst["foo"]["pinned"])
            self.assertFalse(lst["foo"]["modified"])
            with open(os.path.join(tmp, "foo", "tool.py"), "a") as fh:
                fh.write("# tampered\n")
            lst = {p["name"]: p for p in m._plugins.list_plugins()}
            self.assertTrue(lst["foo"]["modified"])          # Manipulation erkannt
            m._plugins.plugin_pin("foo")                              # Approve
            lst = {p["name"]: p for p in m._plugins.list_plugins()}
            self.assertFalse(lst["foo"]["modified"])
            m._plugins.plugin_delete("foo")
            self.assertNotIn("foo", m._plugins.load_plugin_pins())    # Pin mit weg
        finally:
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = old_src, old_pins

    def test_plugin_files_link_into_vscode(self):
        """Approve must never be blind: each file name links to the file in the
        host's VS Code (code-server deep link: folder + payload openFile with
        the remote authority). Without CODE_URL/CODE_ROOT: no link, no crash."""
        m = self.m
        old = m._settings.CODE_URL, m._settings.CODE_ROOT
        try:
            m._settings.CODE_URL, m._settings.CODE_ROOT = "http://192.168.0.10:8443/", "/home/coder/firecracker"
            u = m._plugins.plugin_code_link("folder", "greeter", "lib/h.py")
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            self.assertTrue(u.startswith("http://192.168.0.10:8443/?"))
            self.assertEqual(q["folder"], ["/home/coder/firecracker"])
            self.assertEqual(json.loads(q["payload"][0]),
                             [["openFile", "vscode-remote://192.168.0.10:8443/home/coder/firecracker/plugins/greeter/lib/h.py"]])
            u1 = m._plugins.plugin_code_link("file", "dice", "dice.py")
            self.assertIn("/home/coder/firecracker/plugins/dice.py", json.loads(
                urllib.parse.parse_qs(urllib.parse.urlsplit(u1).query)["payload"][0])[0][1])
            m._settings.CODE_ROOT = ""
            self.assertEqual(m._plugins.plugin_code_link("folder", "greeter", "tool.py"), "")
            # list_plugins carries the links per file
            tmp = tempfile.mkdtemp(prefix="e2e-pluglink-")
            old_src, old_pins = m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = tmp, os.path.join(tmp, ".pins.json")
            try:
                m._plugins.plugin_write_py("foo", "DESC='x'\n")
                self.assertEqual(m._plugins.list_plugins()[0]["links"], {"tool.py": ""})
                m._settings.CODE_ROOT = "/home/coder/firecracker"
                self.assertIn("plugins/foo/tool.py", urllib.parse.unquote(
                    m._plugins.list_plugins()[0]["links"]["tool.py"]))
            finally:
                m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = old_src, old_pins
        finally:
            m._settings.CODE_URL, m._settings.CODE_ROOT = old
        # the old in-manager viewer route is gone: guests never had it, admins use VS Code
        self.assertNotIn(("GET", "/api/plugins/"), {(meth, p) for meth, _, p, _ in m._routes.ROUTER.inventory()})

    def test_set_instance_tools_roundtrip(self):
        """Saving policy tools: a subset persists (tools_all=False), ALL tools
        removes the field (tools_all=True), unknown names are filtered out.
        Regression for 'after saving, all tools are active again'."""
        m = self.m
        import tempfile, os
        tmp = tempfile.mkdtemp(prefix="e2e-tools-")
        with open(os.path.join(tmp, "toolinst.json"), "w") as fh:
            json.dump({"name": "toolinst", "template": "openrouter", "config": {}}, fh)
        old_dir, old_load, old_run = m._paths.INST_DIR, m._instances.load_instances, m._instances.is_running
        m._paths.INST_DIR = tmp
        m._instances.load_instances = lambda: [_readj(os.path.join(tmp, "toolinst.json"))]
        m._instances.is_running = lambda inst: False
        try:
            picks = sorted(m._policy.AGENT_TOOL_NAMES)[:3]
            m._instances.set_instance_tools("toolinst", picks)
            cfg = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertEqual(set(cfg["AGENT_TOOLS"].split(",")), set(picks))   # Subset bleibt
            self.assertFalse(m._policy.effective_policy(_readj(os.path.join(tmp, "toolinst.json")))["tools_all"])

            m._instances.set_instance_tools("toolinst", list(m._policy.tool_names()))   # built-in + plugins
            cfg2 = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertNotIn("AGENT_TOOLS", cfg2)                              # alle -> Feld raus
            self.assertTrue(m._policy.effective_policy(_readj(os.path.join(tmp, "toolinst.json")))["tools_all"])

            m._instances.set_instance_tools("toolinst", ["kein_tool", picks[0]])
            cfg3 = _readj(os.path.join(tmp, "toolinst.json"))["config"]
            self.assertEqual(cfg3["AGENT_TOOLS"], picks[0])                    # unbekannte gefiltert
        finally:
            m._paths.INST_DIR, m._instances.load_instances, m._instances.is_running = old_dir, old_load, old_run

    def test_plugin_zip_and_slip_guard(self):
        """A multi-file zip lands in the tool folder; a ../ path (zip-slip) must NOT
        be extracted outside; a zip without an entry file is rejected."""
        m = self.m
        import tempfile, os, io, zipfile
        tmp = tempfile.mkdtemp(prefix="e2e-plug-")
        old = m._plugins.PLUGINS_SRC
        m._plugins.PLUGINS_SRC = tmp
        try:
            buf = io.BytesIO(); z = zipfile.ZipFile(buf, "w")
            z.writestr("tool.py", "DESC='x'\nPARAMS={}\nREQUIRED=[]\ndef run():\n    return 1\n")
            z.writestr("helper.py", "x=1\n"); z.close()
            self.assertIsNone(m._plugins.plugin_write_zip("mytool", buf.getvalue()))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mytool", "tool.py")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mytool", "helper.py")))
            b2 = io.BytesIO(); z2 = zipfile.ZipFile(b2, "w")
            z2.writestr("tool.py", "def run():\n    return 1\n")
            z2.writestr("../evil.py", "boom\n"); z2.close()
            m._plugins.plugin_write_zip("slip", b2.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.py")))
            b3 = io.BytesIO(); z3 = zipfile.ZipFile(b3, "w"); z3.writestr("readme.txt", "x"); z3.close()
            self.assertIsNotNone(m._plugins.plugin_write_zip("noentry", b3.getvalue()))
        finally:
            m._plugins.PLUGINS_SRC = old

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
        oc, ot = m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE
        m._chats.CHATS_FILE = os.path.join(tmp, "chats.json")
        m._chats.TOMBSTONES_FILE = os.path.join(tmp, "tombs.json")
        try:
            NOW = 1_800_000_000_000
            has = lambda: any(c.get("id") == "x" for c in m._chats.load_chats())
            m._chats.merge_chats([{"id": "x", "updatedAt": NOW - 5000,
                            "messages": [{"user": True, "text": "hi"}]}])
            self.assertTrue(has())
            m._chats.merge_chats({"chats": [], "tombstones": {"x": NOW}})       # delete
            self.assertFalse(has())
            self.assertIn("x", m._chats.load_tombstones())
            m._chats.merge_chats([{"id": "x", "updatedAt": NOW - 1000,          # Re-Push alt
                            "messages": [{"user": True, "text": "hi"}]}])
            self.assertFalse(has())                                      # bleibt weg
            m._chats.merge_chats([{"id": "x", "updatedAt": NOW + 9000,          # echte Bearbeitung
                            "messages": [{"user": True, "text": "edit"}]}])
            self.assertTrue(has())                                       # aufersteht
            self.assertNotIn("x", m._chats.load_tombstones())                   # Tombstone weg
            # A bogus far-future timestamp (leaked sync fixture, year 2286) must
            # still be deletable — it must not beat the deletion.
            m._chats.merge_chats([{"id": "y", "updatedAt": 10000000000001,
                            "messages": [{"user": True, "text": "SYNC"}]}])
            self.assertTrue(any(c.get("id") == "y" for c in m._chats.load_chats()))
            m._chats.merge_chats({"chats": [], "tombstones": {"y": NOW}})       # delete now
            self.assertFalse(any(c.get("id") == "y" for c in m._chats.load_chats()))
            m._chats.merge_chats([{"id": "y", "updatedAt": 10000000000001,      # re-push the future chat
                            "messages": [{"user": True, "text": "SYNC"}]}])
            self.assertFalse(any(c.get("id") == "y" for c in m._chats.load_chats()))   # stays deleted
        finally:
            m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE = oc, ot

    def test_provider_switch_sets_and_clears_keys(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-inst-")
        inst = {"name": "e2e-switch", "config": {"OPENROUTER_MODEL": "google/gemini-2.5-flash"}}
        with open(os.path.join(tmp, "e2e-switch.json"), "w") as fh:
            json.dump(inst, fh)
        old_dir, old_load = m._paths.INST_DIR, m._instances.load_instances
        try:
            m._paths.INST_DIR = tmp
            m._instances.load_instances = lambda: [_readj(os.path.join(tmp, "e2e-switch.json"))]
            msg = m._instances.set_model("e2e-switch", "orcarouter:tencent/hy3")
            cfg = _readj(os.path.join(tmp, "e2e-switch.json"))["config"]
            self.assertEqual(cfg.get("ORCAROUTER_MODEL"), "tencent/hy3")
            self.assertNotIn("OPENROUTER_MODEL", cfg)               # anderer Provider entfernt
        finally:
            m._paths.INST_DIR, m._instances.load_instances = old_dir, old_load

    def test_provider_switch_ignores_free_suffix(self):
        """':free' model variants must NOT be read as a provider."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-inst2-")
        with open(os.path.join(tmp, "e2e-free.json"), "w") as fh:
            json.dump({"name": "e2e-free", "config": {"OPENROUTER_MODEL": "x"}}, fh)
        old_dir, old_load = m._paths.INST_DIR, m._instances.load_instances
        try:
            m._paths.INST_DIR = tmp
            m._instances.load_instances = lambda: [_readj(os.path.join(tmp, "e2e-free.json"))]
            m._instances.set_model("e2e-free", "mistralai/mistral-7b-instruct:free")
            cfg = _readj(os.path.join(tmp, "e2e-free.json"))["config"]
            self.assertEqual(cfg.get("OPENROUTER_MODEL"), "mistralai/mistral-7b-instruct:free")
            self.assertNotIn("ORCAROUTER_MODEL", cfg)
        finally:
            m._paths.INST_DIR, m._instances.load_instances = old_dir, old_load

    def test_hitl_lifecycle(self):
        m = self.m
        import mgr.signal as sigmod
        old_send = sigmod.signal_send
        try:
            sigmod.signal_send = lambda text, to=None: (True, "sent")
            hid = m._signal_mod.hitl_create("orchestrator", "bash", "rm foo")
            self.assertIsNotNone(hid)
            self.assertEqual(m._signal_mod.hitl_status(hid), "pending")
            self.assertTrue(m._signal_mod.hitl_resolve(hid, True))
            self.assertEqual(m._signal_mod.hitl_status(hid), "approved")
            self.assertFalse(m._signal_mod.hitl_resolve(hid, True))     # not resolvable twice
            self.assertEqual(m._signal_mod.hitl_status("nonexistent"), "unknown")
        finally:
            sigmod.signal_send = old_send

    def test_hitl_without_signal_still_waits_in_the_queue(self):
        """Signal is an optional extra channel: without it (send fails) the
        approval still lands in the queue (web "Waiting for you", app), the
        agent polls; a Signal reply "ok <id>" (12 hex) still decides; an
        unanswered one expires after HITL_TTL (-> denied)."""
        m = self.m
        import mgr.signal as sigmod
        old = (sigmod.signal_send, m._notify.notify_add, m._hitlq.QUEUE_FILE)
        notes = []
        try:
            m._hitlq.QUEUE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-hq-"), "hitlq.json")
            m._notify.notify_add = lambda *a, **k: notes.append((a, k)) or ("n", "ok")
            sigmod.signal_send = lambda text, to=None: (False, "no recipient")
            hid = m._signal_mod.hitl_create("x", "bash", "y")
            self.assertRegex(hid, r"^[0-9a-f]{12}$")
            self.assertEqual(m._signal_mod.hitl_status(hid), "pending")
            self.assertEqual(notes[0][0][1], "Waiting for you: x wants to run bash")
            self.assertEqual(notes[0][1], {"link": "squads"})
            it = m._hitlq.get(hid)
            self.assertEqual((it["kind"], it["project"], it["wake_on_decision"], it["target"]), ("approval", "", False, "y"))
            sigmod._handle_signal_envelope({"envelope": {"sourceNumber": "+49", "dataMessage": {"message": f"ok {hid}"}}}, {"+49"})
            self.assertEqual((m._signal_mod.hitl_status(hid), m._hitlq.get(hid)["decided_by"]), ("approved", "signal"))
            hid2 = m._signal_mod.hitl_create("x", "bash", "z")
            q = m._hitlq._load(); q[-1]["created"] -= m._hitlq.HITL_TTL + 1; m._hitlq._save(q)
            self.assertEqual(m._signal_mod.hitl_status(hid2), "denied")
            self.assertEqual(m._hitlq.get(hid2)["status"], "expired")
        finally:
            sigmod.signal_send, m._notify.notify_add, m._hitlq.QUEUE_FILE = old

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
            data, stats = m._katfs.katfs_zip("share1", ".")
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
        old = m._audit.AUDIT_DIR
        try:
            m._audit.AUDIT_DIR = tmp
            m._audit.audit_append("inst-a", "http_fetch", "https://x.example", False,
                           err="⚠️ HTTP 403: blocked", turn="t1234")
            m._audit.audit_append("inst-a", "web_search", "cronn", True,
                           result="1. cronn GmbH …", turn="t1234")
            recs = m._audit.audit_read("inst-a")     # newest first
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[1]["err"], "⚠️ HTTP 403: blocked")
            self.assertNotIn("result", recs[1])
            self.assertEqual(recs[0]["result"], "1. cronn GmbH …")
            self.assertEqual({r["turn"] for r in recs}, {"t1234"})
        finally:
            m._audit.AUDIT_DIR = old

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
        src = open(HTTPD_PATH).read()
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
        inv = m._routes.ROUTER.inventory()
        self.assertTrue(inv, "the router should carry routes")
        for method, kind, path, admin in inv:
            self.assertIn(method, ("GET", "POST"))
            self.assertIn(kind, ("exact", "prefix"))
            self.assertTrue(path.startswith("/"))
            self.assertIsInstance(admin, bool)
        by_route = {(meth, p): admin for meth, _, p, admin in inv}
        # Settings once served the API keys in plain text — guests must not see it.
        self.assertTrue(by_route[("GET", "/api/settings")], "/api/settings must stay admin-only")
        self.assertTrue(by_route[("POST", "/api/settings")])
        self.assertTrue(by_route[("GET", "/api/instances")])
        # The agents need these reads, so they are deliberately open to guests —
        # the writes on the same paths are admin-only.
        self.assertFalse(by_route[("GET", "/api/skills")])
        self.assertFalse(by_route[("GET", "/api/personas")])
        self.assertTrue(by_route[("POST", "/api/skills")])
        self.assertTrue(by_route[("POST", "/api/personas")])
        # The whole HTTP surface lives in the table now: no if-chain left.
        from mgr.routes import source_routes
        src = open(HTTPD_PATH).read()
        chain = src[src.index("    def _do_GET(self):"):src.index("    def _dispatch(self, method):")]
        self.assertEqual([p for _, p, _ in source_routes(chain)], [],
                         "a path literal crept back into the handler chain")
        self.assertGreater(len(inv), 90)

    def test_standard_skills_flag(self):
        """A skill can be standard: the flag survives an edit without it, the
        page and ?meta=1 carry it, ?default=1 lists only the standard ones
        (what every agent is told each turn), set_default toggles it."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-stdsk-")
        old = m._skills.SKILLS_FILE
        try:
            m._skills.SKILLS_FILE = os.path.join(tmp, "skills.json")
            m._skills.upsert_skill("ponytail", "lazy", "body", default=True)
            m._skills.upsert_skill("other", "x", "body")
            m._skills.upsert_skill("ponytail", "lazier", "body2")              # an edit keeps the flag
            self.assertEqual({s["name"]: s["default"] for s in m._skills.meta()}, {"ponytail": True, "other": False})
            h = self._handler("/api/skills?default=1", "10.0.0.9"); h._do_GET()
            body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(body, [{"name": "ponytail", "description": "lazier", "default": True, "category": "coding"}])   # no bodies
            self.assertIn('"default": true', m._ui.render().replace('"default":true', '"default": true'))
            self.assertIn("on demand", m._skills.set_default("ponytail", False))
            self.assertEqual([s["name"] for s in m._skills.meta() if s["default"]], [])
            self.assertIn("unknown", m._skills.set_default("nope", True))
        finally:
            m._skills.SKILLS_FILE = old

    def test_usage_series_buckets(self):
        """The Instances chart's data: tokens per instance per bucket (hourly for
        24h, per day for 7d/30d), task VMs folded into "tasks", 14 days of cost;
        an unknown range falls back to 24h; admin only (not in the user role)."""
        m = self.m
        import mgr.store as st
        old = st.HISTORY_DB, st._migrated[0], st._turns_migrated[0]
        tmp = tempfile.mkdtemp(prefix="e2e-useries-")
        try:
            st.HISTORY_DB, st._migrated[0], st._turns_migrated[0] = os.path.join(tmp, "history.db"), False, None
            now = int(time.time())
            for inst, pt, ct, cost, ts in (("orch", 100, 10, 0.5, now), ("orch", 1, 1, 0.0, now - 2 * 3600),
                                           ("task-0a1b2c", 20, 2, 0.25, now), ("orch", 7, 0, 1.0, now - 40 * 86400)):
                with st._hist_lock, st._hist_conn() as c:
                    c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,completion_tokens,cost) VALUES(?,?,?,?,?,?)",
                              (ts, inst, "a/x", pt, ct, cost))
            d = st.usage_series("24h", now)
            self.assertEqual(len(d["buckets"]), 24)
            self.assertEqual(d["series"]["orch"][-1], 110)
            self.assertEqual(d["series"]["orch"][-3], 2)
            self.assertEqual(d["series"]["tasks"][-1], 22)
            self.assertEqual((len(d["days"]), d["cost"][-1]), (14, 0.75))       # the 40-day-old row is outside
            d = st.usage_series("30d", now)
            self.assertEqual(len(d["buckets"]), 30)
            self.assertEqual(sum(d["series"]["orch"]), 112)
            h = self._handler("/api/usage-series?range=bogus", "10.0.0.9"); h._do_GET()
            body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual((body["range"], len(body["buckets"])), ("24h", 24))
            self.assertFalse(m._auth.user_allowed("GET", "/api/usage-series"))
        finally:
            st.HISTORY_DB, st._migrated[0], st._turns_migrated[0] = old

    def test_skills_page_carries_no_contents(self):
        """Regression: the page inlined the COMPLETE skills.json. With the
        imported catalog (~870 KB) that would ship on every page load — only
        name + description belong in the page, the body comes from
        GET /api/skills/<name> when editing."""
        m = self.m
        marker = "SKILL-BODY-MARKER-DO-NOT-INLINE"
        old = m._skills.load_skills
        try:
            m._skills.load_skills = lambda: [{"name": "e2e-skill", "description": "kurz",
                                      "content": marker + " x" * 5000}]
            page = m._ui.render()
            self.assertIn("e2e-skill", page)          # name/description are in
            self.assertIn("kurz", page)
            self.assertNotIn(marker, page)            # the body is NOT
        finally:
            m._skills.load_skills = old

    def test_squad_card_is_member_blocks_not_a_table(self):
        """The squad proposal card: one block per member with label rows,
        skill chips + "+ add", provenance badges, model as "<Tier> — <model>",
        a two-option Runs-on radio — no longer the wide member table."""
        page = self.m._ui.render()
        for s in ("function sqMem(", "function sqModelOpts(", "class=sq-chip", "list=sq-skl",
                  "role=radiogroup", "'empty — choose'", "sq-foot", ".sq-row{"):
            self.assertIn(s, page)
        for s in ("<th>Member</th>", "<th>Template</th>", "chosen by:", "from the tier\" data-k", "sqMemRows"):
            self.assertNotIn(s, page)

    def test_instance_card_collapses_and_has_config_editor(self):
        """A card starts collapsed (details in .ic-more, a triangle toggles)
        and offers the config editor, which only uses the validated /config route."""
        m = self.m
        card = m._ui._card({"name": "cardx", "index": 3, "template": "openrouter", "rootfs": "x",
                            "config": {"TRANSPORT": "web", "OPENROUTER_MODEL": "a/b"}, "mounts": []})
        self.assertIn("class=ic-tog aria-expanded=false", card)
        self.assertIn("ic-more", card)
        self.assertIn("editConfig('cardx')", card)
        self.assertIn("id=skq", m._ui.render())                                  # Skills tab: keyword filter
        self.assertNotIn("encodeURIComponent(SQ)", m._ui.render())                # squad calls: never an empty project name
        self.assertIn("aria-controls=sk-at", m._ui.render())                     # Squads composer: @-mention list
        page = m._ui.render()
        self.assertIn("id=cfdlg", page)
        self.assertIn("/api/instances/${CFDLG}/config", page)

    def test_footer_code_link_optional(self):
        """The editor link is host-specific (site.json CODE_URL): set = link in
        the footer, unset = no placeholder left over in the page."""
        m = self.m
        old = m._settings.CODE_URL
        try:
            m._settings.CODE_URL = "http://example.invalid:8443/"
            page = m._ui.render()
            self.assertIn('href="http://example.invalid:8443/"', page)
            self.assertIn('rel="noopener noreferrer"', page)
            m._settings.CODE_URL = ""
            page = m._ui.render()
            self.assertNotIn("__CODE_LINK__", page)
        finally:
            m._settings.CODE_URL = old

    def test_tool_catalog_matches_agent(self):
        """Drift guard: every tool in the agent (BUILTIN) must be in the manager
        catalog (AGENT_TOOLS_CATALOG) — otherwise it is missing from the create
        form and a tool allowlist blocks it silently (happened with mission_start
        and offload_read). And vice versa: no catalog entry without a real tool."""
        m = self.m
        a = _load("agent_cat_e2e", AGENT_PATH,
                  {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-cat-"),
                   "OPENROUTER_API_KEY": "dummy"})
        agent_tools = set(a._tools.BUILTIN.keys())
        catalog = set(m._policy.AGENT_TOOL_NAMES)
        missing_in_catalog = agent_tools - catalog
        self.assertFalse(missing_in_catalog,
                         f"tools in the agent but not in the manager catalog: {sorted(missing_in_catalog)}")
        ghost_in_catalog = catalog - agent_tools
        self.assertFalse(ghost_in_catalog,
                         f"catalog entries without a real agent tool: {sorted(ghost_in_catalog)}")

    def test_provider_model_key_covers_all(self):
        m = self.m
        for k in m._instances.PROVIDER_MODEL_KEY.values():
            self.assertIn(k, m._instances.MODEL_KEYS)

    def test_llm_proxy_route_registered(self):
        """Injection gateway: the path must be in the guest allowlist (otherwise
        403 for the VM), the upstreams must match the secret names, and the
        settings toggle must appear in the schema."""
        m = self.m
        self.assertIn("/api/llm/", m._guests.GUEST_POST_PREFIXES)
        self.assertEqual(set(m._llmproxy.LLM_PROXY_UPSTREAMS), {"openrouter", "orcarouter"})
        for url, keyname in m._llmproxy.LLM_PROXY_UPSTREAMS.values():
            self.assertTrue(url.endswith("/chat/completions"), url)
            self.assertIn(keyname, m._settings.SECRET_PARAMS)
        self.assertIn("LLM_KEY_PROXY", [s["key"] for s in m._settings.SETTINGS_SCHEMA])

    def test_agent_folder_is_a_template(self):
        """Create instance lists every <AGENTS_DIR>/<x>/template.json next to
        templates/*.json: the folder name is the default template name, the
        folder path is recorded, a folder wins over a json of the same name,
        a broken file is skipped, and "overlay": true reaches the instance
        record so vm.py attaches a write layer for a custom rootfs."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-agents-")
        os.makedirs(os.path.join(tmp, "myagent")); os.makedirs(os.path.join(tmp, "broken")); os.makedirs(os.path.join(tmp, "nofile"))
        json.dump({"description": "mine", "rootfs": "instances/myagent-rootfs.ext4", "overlay": True,
                   "params": [{"key": "TRANSPORT", "default": "web"}]},
                  open(os.path.join(tmp, "myagent", "template.json"), "w"))
        os.makedirs(os.path.join(tmp, "openrouter"))
        json.dump({"template": "openrouter", "description": "FOLDER WINS", "rootfs": "x"},
                  open(os.path.join(tmp, "openrouter", "template.json"), "w"))
        open(os.path.join(tmp, "broken", "template.json"), "w").write("{nope")
        old = (m._settings.SITE.get("AGENTS_DIR"), m._paths.INST_DIR, m._instances.load_instances)
        inst_dir = tempfile.mkdtemp(prefix="e2e-agents-inst-")
        try:
            m._settings.SITE["AGENTS_DIR"] = tmp
            tpls = {t["template"]: t for t in m._instances.load_templates()}
            self.assertEqual(tpls["myagent"]["dir"], os.path.join(tmp, "myagent"))
            self.assertEqual(tpls["myagent"]["description"], "mine")
            self.assertEqual(tpls["openrouter"]["description"], "FOLDER WINS")
            self.assertNotIn("broken", tpls); self.assertNotIn("nofile", tpls)
            self.assertIn("claude", tpls)                                   # templates/*.json still there
            # the instance created from it carries the overlay flag -> upper drive + fc_upper
            m._paths.INST_DIR = inst_dir
            m._instances.load_instances = lambda: [_readj(os.path.join(inst_dir, f)) for f in sorted(os.listdir(inst_dir)) if f.endswith(".json")]
            self.assertIn("created", m._instances.create_instance("skel-e2e", "myagent", {}, []))
            inst = _readj(os.path.join(inst_dir, "skel-e2e.json"))
            self.assertTrue(inst["overlay"]); self.assertEqual(inst["rootfs"], "instances/myagent-rootfs.ext4")
            self.assertTrue(m._vm.is_overlay(inst))
            self.assertFalse(m._vm.is_overlay({"rootfs": "instances/other.ext4"}))
            old_mk = m._vm.make_upper
            m._vm.make_upper = lambda i: "/tmp/e2e-upper.ext4"
            try:
                cfg = m._vm.gen_config(inst)
            finally:
                m._vm.make_upper = old_mk
            ids = [d["drive_id"] for d in cfg["drives"]]
            self.assertEqual(ids[0], "rootfs"); self.assertTrue(cfg["drives"][0]["is_read_only"])
            self.assertEqual(ids[-1], "upper"); self.assertIn("fc_upper=/dev/vd", cfg["boot-source"]["boot_args"])
            self.assertNotIn("harness", ids)                                # no agent drive for a foreign agent
            # the select in the Instances tab offers it
            self.assertIn("value='myagent'", m._ui.render())
            m._settings.SITE["AGENTS_DIR"] = "/nonexistent"
            self.assertNotIn("myagent", {t["template"] for t in m._instances.load_templates()})
        finally:
            if old[0] is None:
                m._settings.SITE.pop("AGENTS_DIR", None)
            else:
                m._settings.SITE["AGENTS_DIR"] = old[0]
            m._paths.INST_DIR, m._instances.load_instances = old[1], old[2]

    def test_overlay_upper_lifecycle(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-ov-")
        old_run, old_inst = m._paths.RUN_DIR, m._paths.INST_DIR
        try:
            m._paths.RUN_DIR = tmp; m._paths.INST_DIR = tmp
            inst = {"name": "e2e-ov", "rootfs": "instances/openrouter-rootfs.ext4"}
            # Wegwerf-Upper landet in RUN_DIR
            self.assertTrue(m._vm.upper_path(inst).startswith(tmp))
            self.assertIn(".upper.ext4", m._vm.upper_path(inst))
            # Persistenter Upper in INST_DIR mit anderem Namen
            inst["persist_disk"] = True
            self.assertIn("-upper.ext4", m._vm.upper_path(inst))
            p = m._vm.make_upper(inst)
            self.assertTrue(os.path.exists(p))
            size1 = os.path.getsize(p)
            self.assertEqual(size1, m._vm.UPPER_PERSIST_SIZE_MB * 1024 * 1024)
            # persist: second call uses the existing file (no reset)
            with open(p, "r+b") as fh:
                fh.seek(0); marker = fh.read(4)
            self.assertEqual(m._vm.make_upper(inst), p)
        finally:
            m._paths.RUN_DIR, m._paths.INST_DIR = old_run, old_inst

    def test_overlay_bootarg_in_config(self):
        m = self.m
        inst = next((i for i in m._instances.load_instances()
                     if i.get("rootfs") in m._vm.OVERLAY_ROOTFS), None)
        if not inst:
            self.skipTest("no overlay instance available")
        old_mk = m._vm.make_upper
        try:
            m._vm.make_upper = lambda i: "/tmp/fake-upper.ext4"   # no real mkfs in the test
            cfg = m._vm.gen_config(inst)
        finally:
            m._vm.make_upper = old_mk
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
            mid, note = m._missions.mission_start("orchestrator", "Testziel", ["s1", "s2"])
            self.assertTrue(mid)
            self.assertEqual(m._missions.mission_start("orchestrator", "", [])[0], None)
            self.assertEqual(m._missions.mission_update("orchestrator", mid, step=1,
                                              status="doing", task_id="t-1"), "ok")
            inst, mi, st = m._missions.mission_for_task("t-1")
            self.assertEqual((inst, mi["id"], st["n"]), ("orchestrator", mid, 1))
            self.assertEqual(m._missions.mission_update("orchestrator", mid, step=1,
                                              status="done", result="ok"), "ok")
            self.assertIsNone(m._missions.mission_for_task("t-1")[1])   # done -> no more trigger
            self.assertEqual(m._missions.mission_admin("orchestrator", mid, "pause"), "ok")
            self.assertEqual(m._missions.mission_admin("orchestrator", mid, "resume"), "ok")
            self.assertEqual(m._missions.mission_finish("orchestrator", mid, "fertig"), "ok")
            done = m._missions.mission_list("orchestrator")[0]
            self.assertEqual(done["status"], "done")
            self.assertIn("cannot", m._missions.mission_admin("orchestrator", mid, "abort"))
        finally:
            mmod.MISSIONS_FILE = old_file
            mmod.notify_add = old_notify

    def test_voice_turns_show_up_in_the_shared_chat(self):
        """A voice client's turns land in the shared store as a 'Voice · inst'
        conversation (live on the web); /reset archives it and the next turn
        opens a new one. Web-chat ids are left alone."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-voice-")
        oc, ot = m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE
        m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE = os.path.join(tmp, "chats.json"), os.path.join(tmp, "tombs.json")
        m._chats._voice_sessions.clear()
        try:
            self.assertEqual(m._chats.voice_session("vc", "10.0.0.9", "1757000000000"), "")   # web chat
            self.assertEqual(m._chats.voice_session("vc", "10.0.0.9", "voice-1757"), "voice-vc-1757")
            s1 = m._chats.voice_session("vc", "10.0.0.9")            # ESP: manager-kept
            self.assertTrue(s1.startswith("voice-vc-"))
            self.assertEqual(m._chats.voice_session("vc", "10.0.0.9"), s1)   # stable
            m._chats.chat_log_append("vc", s1, "Radio aus", "Radio ist aus.", kind="voice")
            m._chats.chat_log_append("vc", s1, "Wie spät?", "16:36 Uhr.", kind="voice")
            m._chats.voice_session("vc", "10.0.0.9", reset=True)
            time.sleep(1.1)                                     # session ids carry seconds
            s2 = m._chats.voice_session("vc", "10.0.0.9")
            self.assertNotEqual(s1, s2)
            m._chats.chat_log_append("vc", s2, "Hallo", "Hallo!", kind="voice")
            chats = {c["id"]: c for c in m._chats.load_chats()}
            self.assertEqual(len(chats[s1]["messages"]), 4)     # archived, intact
            self.assertEqual(len(chats[s2]["messages"]), 2)
            self.assertEqual(chats[s1]["instance"], "vc")
            self.assertTrue(chats[s1]["title"].startswith("Voice · vc · "))
        finally:
            m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE = oc, ot
            m._chats._voice_sessions.clear()

    def test_tts_reads_no_tool_lines(self):
        """Read aloud must skip tool status, think blocks, fences and decor —
        filtered in the manager so web, app and ESP get it for free."""
        m = self.m
        self.assertEqual(m._voice.speakable_text("🔧 ha_control …\nEs gab ein Problem."), "Es gab ein Problem.")
        self.assertEqual(m._voice.speakable_text("  🔧 caldav__list-events …\n\nAm Freitag: **Frühstück**."),
                         "Am Freitag: Frühstück.")
        self.assertEqual(m._voice.speakable_text("⟦think⟧ plan ⟦/think⟧Es ist 10 Uhr."), "Es ist 10 Uhr.")
        self.assertEqual(m._voice.speakable_text("Siehe [Doku](https://x.y/z) und https://a.b/c."), "Siehe Doku und")
        self.assertIn("Codeblock übersprungen", m._voice.speakable_text("Hier:\n```py\nprint(1)\n```\nfertig"))
        self.assertEqual(m._voice.speakable_text(""), "")

    def test_stt_recent_ring(self):
        """What did STT hear? Newest first, bounded, in memory only."""
        m = self.m
        m._voice._stt_recent.clear()
        m._voice.stt_remember("Radio aus", 1.2, "192.168.1.5")
        m._voice.stt_remember("Wie spät ist es?", 1.8, "192.168.1.5")
        r = m._voice.stt_recent()
        self.assertEqual([x["text"] for x in r], ["Wie spät ist es?", "Radio aus"])
        for i in range(m._voice.STT_RECENT_MAX + 5):
            m._voice.stt_remember(f"t{i}", 1, "x")
        self.assertEqual(len(m._voice.stt_recent()), m._voice.STT_RECENT_MAX)
        by_path = {p: admin for _, _, p, admin in m._routes.ROUTER.inventory()}
        self.assertTrue(by_path["/api/stt-recent"])
        self.assertTrue(by_path["/api/stt-recent/audio"])
        m._voice._stt_audio.clear()
        m._voice.stt_remember("a", 1, "x", audio=b"RIFFaaa", ctype="audio/wav")
        m._voice.stt_remember("b", 1, "x", audio=b"RIFFbbb", ctype="audio/wav")
        self.assertEqual(m._voice.stt_audio(0)[3], b"RIFFbbb")
        self.assertEqual(m._voice.stt_audio(1)[3], b"RIFFaaa")
        self.assertIsNone(m._voice.stt_audio(2))
        m._voice._stt_recent.clear(); m._voice._stt_audio.clear()

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
        self.assertEqual(self.m._mcp.HUB_TZ, self.m._host.HOST_TZ)

    def test_jobspy_mcp_server_protocol(self):
        """mcp-hub/jobspy_mcp.py speaks the MCP subset the agent uses (initialize,
        tools/list, tools/call, ping, notifications) and turns JobSpy's DataFrame
        into compact postings; the catalog example carries the entry."""
        import importlib.util, sys, types
        hub = os.path.join(os.path.dirname(FC_DIR), "mcp-hub", "jobspy_mcp.py")
        if not os.path.exists(hub):
            self.skipTest("mcp-hub not next to the manager tree")
        spec = importlib.util.spec_from_file_location("jobspy_mcp_e2e", hub)
        js = importlib.util.module_from_spec(spec); spec.loader.exec_module(js)
        nan = float("nan")
        class Df(list):
            def to_dict(self, kind): return list(self)
        calls = []
        fake = types.ModuleType("jobspy")
        fake.scrape_jobs = lambda **kw: (calls.append(kw), Df([
            {"title": "Data Engineer", "company": "ACME", "location": "Köln, NW, DE", "site": "indeed",
             "date_posted": "2026-09-25", "job_url": "https://x/1", "job_url_direct": nan,
             "min_amount": 60000.0, "max_amount": nan, "currency": "EUR", "interval": "yearly",
             "is_remote": False, "job_type": "fulltime", "description": "x" * 700},
            {"title": "Dev", "company": "B", "location": nan, "site": "linkedin", "date_posted": None,
             "job_url": "https://x/2", "job_url_direct": "https://direct/2", "min_amount": nan, "max_amount": nan,
             "description": nan}]))[1]
        old = sys.modules.get("jobspy"); sys.modules["jobspy"] = fake
        try:
            self.assertIsNone(js.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            r = js.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
            self.assertEqual(r["result"]["serverInfo"]["name"], "jobspy")
            self.assertEqual(js.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"], {})
            tl = js.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
            self.assertEqual([t["name"] for t in tl], ["search_jobs"])
            self.assertIn("search_term", tl[0]["inputSchema"]["required"])
            r = js.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search_jobs",
                  "arguments": {"search_term": "data engineer", "location": "Köln", "hours_old": 72,
                                "site_name": ["indeed", "bogus"], "results_wanted": 500, "is_remote": False}}})
            self.assertFalse(r["result"]["isError"])
            out = json.loads(r["result"]["content"][0]["text"])
            self.assertEqual(out["count"], 2)
            j0, j1 = out["jobs"]
            self.assertEqual(j0["salary"], {"min": 60000.0, "max": None, "currency": "EUR", "interval": "yearly"})
            self.assertEqual(j0["job_url"], "https://x/1"); self.assertTrue(j0["description"].endswith("…"))
            self.assertEqual(len(j0["description"]), js.DESC_CHARS + 1)
            self.assertEqual(j1["job_url"], "https://direct/2"); self.assertNotIn("location", j1); self.assertNotIn("salary", j1)
            kw = calls[-1]
            self.assertEqual(kw["site_name"], ["indeed"]); self.assertEqual(kw["results_wanted"], js.MAX_RESULTS)
            self.assertEqual(kw["hours_old"], 72); self.assertEqual(kw["country_indeed"], js.DEFAULT_COUNTRY)
            self.assertNotIn("Authorization", json.dumps(kw))
            e = js.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}})
            self.assertEqual(e["error"]["code"], -32602)
            e = js.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "search_jobs", "arguments": {}}})
            self.assertTrue(e["result"]["isError"])
            fake.scrape_jobs = lambda **kw: (_ for _ in ()).throw(RuntimeError("blocked"))
            e = js.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "search_jobs", "arguments": {"search_term": "x"}}})
            self.assertTrue(e["result"]["isError"]); self.assertIn("blocked", e["result"]["content"][0]["text"])
            self.assertEqual(js.handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})["error"]["code"], -32601)
        finally:
            if old is None:
                sys.modules.pop("jobspy", None)
            else:
                sys.modules["jobspy"] = old
        cat = json.load(open(os.path.join(os.path.dirname(FC_DIR), "kaim56", "examples", "mcp-catalog.example.json"))) \
            if os.path.exists(os.path.join(os.path.dirname(FC_DIR), "kaim56", "examples", "mcp-catalog.example.json")) else []
        if cat:
            js_entry = next(e for e in cat if e["name"] == "jobspy")
            self.assertEqual(js_entry["args"], ["/app/jobspy_mcp.py"])

    def test_mail_routing_parsing_and_inbound_turn(self):
        """mgr/mail: an instance's address is <local>+<name>@<domain>; a mail is
        routed by that tag (unknown/no tag -> orchestrator); only allowed senders
        reach an agent; the turn gets the body marked as untrusted, lands in a
        'mail' conversation and the reply is mailed back to the sender with
        threading headers; duplicates and own mails are dropped."""
        m = self.m
        ml = m._mail
        st = {"MAIL_ADDRESS": "Agent@Example.com", "MAIL_IMAP_HOST": "imap.x", "MAIL_PASSWORD": "pw",
              "MAIL_ALLOWED_SENDERS": "Ulrich@Home.test, boss@corp.test"}
        insts = [{"name": "orchestrator", "config": {}}, {"name": "jobresearcher", "config": {}},
                 {"name": "hass", "config": {"MAIL_TAG": "Home-Bot"}}]
        old = (m._settings.load_settings, m._instances.load_instances, m._chats.chat_log_append,
               m._audit.audit_append, ml._smtp, dict(ml._seen_ids))
        logged, sent = [], []
        class FakeSmtp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def send_message(self, msg): sent.append(msg)
        try:
            m._settings.load_settings = lambda: st
            m._instances.load_instances = lambda: insts
            m._chats.chat_log_append = lambda inst, sender, u, r, kind="signal": logged.append((inst, sender, u, r, kind))
            m._audit.audit_append = lambda *a, **k: None
            ml._smtp = lambda c: FakeSmtp()
            ml._seen_ids.clear()
            c = ml.conf()
            self.assertTrue(c["ok"]); self.assertEqual(c["user"], "agent@example.com"); self.assertEqual(c["smtp_host"], "imap.x")
            self.assertEqual(ml.address_of(insts[1]), "agent+jobresearcher@example.com")
            self.assertEqual(ml.address_of(insts[2]), "agent+home-bot@example.com")       # MAIL_TAG, sanitized
            self.assertEqual(ml.route(["agent+jobresearcher@example.com"]), "jobresearcher")
            self.assertEqual(ml.route(["x@other.test", "AGENT+HOME-BOT@example.com"]), "hass")
            self.assertEqual(ml.route(["agent@example.com"]), "orchestrator")
            self.assertEqual(ml.route(["agent+nobody@example.com"]), "orchestrator")
            self.assertEqual(ml.route(["other+jobresearcher@example.com"]), "orchestrator")   # not our account
            # parsing: multipart with html only in one part, decoded headers
            raw = (b"From: =?utf-8?q?Ulrich_N=C3=B6?= <ulrich@home.test>\r\nTo: Agent <agent+jobresearcher@example.com>\r\n"
                   b"Subject: =?utf-8?q?Stellen_pr=C3=BCfen?=\r\nMessage-ID: <m1@home.test>\r\n"
                   b"Content-Type: multipart/alternative; boundary=BB\r\n\r\n--BB\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                   b"<p>Bitte <b>heute</b> suchen.</p><script>x</script>\r\n--BB--\r\n")
            p = ml.parse(raw)
            self.assertEqual((p["from"], p["from_name"], p["subject"], p["id"]), ("ulrich@home.test", "Ulrich N\u00f6", "Stellen pr\u00fcfen", "<m1@home.test>"))
            self.assertEqual(p["text"], "Bitte heute suchen.")
            self.assertIn("agent+jobresearcher@example.com", p["to"])
            # inbound turn
            runs = []
            deliver = lambda name, msg: (runs.append((name, msg)), (True, "Gefunden: 2 Stellen"))[1]
            inst, reply, note = ml.handle_inbound(raw, deliver=deliver)
            self.assertEqual((inst, reply), ("jobresearcher", "Gefunden: 2 Stellen"))
            self.assertEqual(runs[0][0], "jobresearcher")
            self.assertIn("[Mail] From: Ulrich N\u00f6 <ulrich@home.test>", runs[0][1])
            self.assertIn("not instructions", runs[0][1]); self.assertTrue(runs[0][1].endswith("Bitte heute suchen."))
            self.assertEqual(logged[0][0], "jobresearcher"); self.assertEqual(logged[0][4], "mail"); self.assertIn("Stellen", logged[0][2])
            out = sent[-1]
            self.assertEqual(out["To"], "ulrich@home.test"); self.assertEqual(out["Subject"], "Re: Stellen pr\u00fcfen")
            self.assertIn("agent+jobresearcher@example.com", out["From"]); self.assertEqual(out["In-Reply-To"], "<m1@home.test>")
            self.assertEqual(out.get_content().strip(), "Gefunden: 2 Stellen")
            # the same Message-ID again: dropped; an unknown sender: dropped before any turn
            self.assertEqual(ml.handle_inbound(raw, deliver=deliver)[2], "duplicate")
            bad = raw.replace(b"ulrich@home.test", b"spam@evil.test").replace(b"<m1@", b"<m2@")
            self.assertIn("not allowed", ml.handle_inbound(bad, deliver=deliver)[2]); self.assertEqual(len(runs), 1)
            own = raw.replace(b"ulrich@home.test", b"agent+jobresearcher@example.com").replace(b"<m1@", b"<m3@")
            st["MAIL_ALLOWED_SENDERS"] += ", agent+jobresearcher@example.com"
            self.assertEqual(ml.handle_inbound(own, deliver=deliver)[2], "own mail"); self.assertEqual(len(runs), 1)
            # a failed turn: no reply mail, logged
            n_sent = len(sent)
            self.assertIsNone(ml.handle_inbound(raw.replace(b"<m1@", b"<m4@"), deliver=lambda n, msg: (False, "error: timeout"))[1])
            self.assertEqual(len(sent), n_sent)
            # the send tool: allowlist, redaction, rate limit; unconfigured mail says so
            ok, note = ml.send("nobody@else.test", "s", "t", inst=insts[1])
            self.assertFalse(ok); self.assertIn("not permitted", note)
            ok, note = ml.send("boss@corp.test", "Report", "key sk-or-v1-" + "ab" * 32, inst=insts[1])
            self.assertTrue(ok); self.assertNotIn("sk-or-v1-", sent[-1].get_content()); self.assertIn("[SECRET", sent[-1].get_content())
            ml._sent[:] = [time.time()] * ml.MAIL_RATE[0]
            self.assertIn("rate limit", ml.send("boss@corp.test", "s", "t")[1])
            ml._sent.clear()
            st["MAIL_PASSWORD"] = ""
            self.assertIn("not configured", ml.send("boss@corp.test", "s", "t")[1])
            self.assertEqual(ml.fetch_once(), 0)
        finally:
            (m._settings.load_settings, m._instances.load_instances, m._chats.chat_log_append,
             m._audit.audit_append, ml._smtp) = old[:5]
            ml._seen_ids.clear(); ml._seen_ids.update(old[5]); ml._sent.clear()

    def test_mcp_servers_validated_against_catalog(self):
        """The Policy tab assigns MCPs through the config route: names must
        exist in the catalog, spaces are tolerated, empty means none."""
        m = self.m
        old = m._mcp.load_mcps
        try:
            m._mcp.load_mcps = lambda: [{"name": "homeassistant"}, {"name": "caldav"}]
            self.assertEqual(m._instances.mcp_servers_error("homeassistant, caldav"), "")
            self.assertEqual(m._instances.mcp_servers_error(""), "")
            self.assertIn("calendar", m._instances.mcp_servers_error("caldav,calendar"))
        finally:
            m._mcp.load_mcps = old

    def test_terminal_ws_requires_our_origin_and_page_is_served_by_manager(self):
        """H-2: the terminal WebSocket handshake needs a present, allowed Origin
        (403 otherwise). H-3: the terminal page comes from the manager's own
        webterm.py, never proxied from the guest."""
        m = self.m
        tunneled = []
        old = m._instances.load_instances, m._instances.is_running, m._instances.net_of
        try:
            inst = {"name": "vm1", "index": 3, "template": "openrouter"}
            m._instances.load_instances = lambda: [inst]; m._instances.is_running = lambda i: True
            m._instances.net_of = lambda i: {"guest": "172.30.3.2", "tap": "fc3"}
            def ws(path, origin=None):
                h = self._handler(path, "10.0.0.9")
                h.headers["Connection"] = "Upgrade"; h.headers["Upgrade"] = "websocket"
                if origin is not None:
                    h.headers["Origin"] = origin
                h._ws_tunnel = lambda guest, port, p: tunneled.append((guest, port, p)) or None
                h._term_route("vm1", "term/ws")
                return h
            self.assertEqual(self._status(ws("/i/vm1/term/ws")), 403)                              # no Origin
            self.assertEqual(self._status(ws("/i/vm1/term/ws", "http://evil.example")), 403)        # foreign
            self.assertEqual(tunneled, [])
            h = ws("/i/vm1/term/ws", "http://localhost:8700")                                        # ours
            self.assertEqual(tunneled, [("172.30.3.2", m._instances.TERM_GUEST_PORT, "/ws")])
            # the page: manager-served, the guest proxy must NOT be consulted
            h = self._handler("/i/vm1/term/", "10.0.0.9")
            h._proxy = lambda *a, **k: (_ for _ in ()).throw(AssertionError("terminal page was proxied from the guest"))
            h._term_route("vm1", "term/")
            self.assertEqual(self._status(h), 200)
            self.assertIn(b"<!doctype html>", h.wfile.getvalue().lower())
        finally:
            m._instances.load_instances, m._instances.is_running, m._instances.net_of = old

    def test_proxied_guest_html_is_sandboxed(self):
        """H-3: HTML relayed from a guest carries Content-Security-Policy: sandbox;
        JSON/plain answers (the chat API) do not."""
        m = self.m
        import types
        old = m._instances.load_instances, m._instances.is_running, m._instances.net_of, urllib.request.urlopen, m._guests.instance_by_ip
        try:
            inst = {"name": "vm1", "index": 3, "template": "openrouter"}
            m._instances.load_instances = lambda: [inst]; m._instances.is_running = lambda i: True
            m._instances.net_of = lambda i: {"guest": "172.30.3.2", "tap": "fc3"}; m._guests.instance_by_ip = lambda ip: None
            class Resp:
                def __init__(self, ct, body): self.status, self.headers, self._b = 200, {"Content-Type": ct}, body
                def read(self, n=-1):
                    b, self._b = self._b, b""; return b
                def __enter__(self): return self
                def __exit__(self, *a): return False
            for ct, path, want in (("text/html; charset=utf-8", "/i/vm1/", True),
                                   ("application/json", "/i/vm1/api/tools", False)):
                urllib.request.urlopen = lambda req, timeout=0, _ct=ct: Resp(_ct, b"<p>hi</p>" if "html" in _ct else b"{}")
                h = self._handler(path, "10.0.0.9"); h._do_GET()
                raw = h.wfile.getvalue()
                self.assertEqual(self._status(h), 200)
                self.assertEqual(b"content-security-policy: sandbox" in raw.lower(), want, (ct, raw[:200]))
        finally:
            m._instances.load_instances, m._instances.is_running, m._instances.net_of, urllib.request.urlopen, m._guests.instance_by_ip = old

    def test_mgr_modules_never_import_manager_and_manager_is_wiring_only(self):
        """The two invariants of the split: no mgr module imports manager.py
        (no cycles, every module loads on its own), and manager.py holds no
        routes, handler or domain code — imports, wiring and main only."""
        import ast as _ast
        mgr_dir = os.path.join(FC_DIR, "mgr")
        for f in sorted(os.listdir(mgr_dir)):
            if not f.endswith(".py"):
                continue
            for n in _ast.walk(_ast.parse(open(os.path.join(mgr_dir, f)).read())):
                if isinstance(n, _ast.Import):
                    self.assertFalse(any(a.name == "manager" for a in n.names), f"mgr/{f} imports manager")
                elif isinstance(n, _ast.ImportFrom):
                    self.assertNotEqual(n.module, "manager", f"mgr/{f} imports from manager")
        tree = _ast.parse(open(MANAGER_PATH).read())
        self.assertFalse([n for n in tree.body if isinstance(n, _ast.ClassDef)], "a class crept back into manager.py")
        fns = [n.name for n in tree.body if isinstance(n, _ast.FunctionDef)]
        self.assertEqual(fns, ["_ha_ws_target"], f"domain code in manager.py: {fns}")

    def test_memfs_git_never_runs_as_root_with_hooks(self):
        """C-1: git in the guest-writable memory folder must run as the guest
        user (when the manager is root) with every config-driven execution
        path disabled on the command line — a planted hook can neither run as
        root nor run at all."""
        import mgr.memfs as mf
        old = mf.OWNER, mf.os.geteuid
        try:
            mf.OWNER = (1234, 5678); mf.os.geteuid = lambda: 0
            cmd, env, kw = mf._git_invocation("/tmp/x", "commit", "-q", "-m", "m")
            self.assertEqual((kw.get("user"), kw.get("group"), kw.get("extra_groups")), (1234, 5678, [5678]))
            joined = " ".join(cmd)
            self.assertIn("core.hooksPath=/dev/null", joined)
            self.assertIn("core.fsmonitor=false", joined)
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null"); self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(cmd[-4:], ["commit", "-q", "-m", "m"])
            mf.os.geteuid = lambda: 1000                      # not root (tests, dev box): no user switch, flags stay
            cmd, env, kw = mf._git_invocation("/tmp/x", "add", "-A")
            self.assertEqual(kw, {}); self.assertIn("core.hooksPath=/dev/null", " ".join(cmd))
            mf.OWNER = None
            self.assertEqual(mf._git_invocation("/tmp/x", "add")[2], {})
        finally:
            mf.OWNER, mf.os.geteuid = old
        # and a hook planted in a real repo does not fire through _git
        import mgr.memfs as mf2
        tmp = tempfile.mkdtemp(prefix="e2e-c1-")
        r = mf2._git(tmp, "init", "-q"); self.assertIsNotNone(r)
        hooks = os.path.join(tmp, ".git", "hooks"); os.makedirs(hooks, exist_ok=True)
        marker = os.path.join(tmp, "PWNED")
        with open(os.path.join(hooks, "pre-commit"), "w") as fh:
            fh.write(f"#!/bin/sh\ntouch {marker}\n")
        os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
        with open(os.path.join(tmp, "f.md"), "w") as fh:
            fh.write("x")
        mf2._git(tmp, "config", "user.email", "t@t"); mf2._git(tmp, "config", "user.name", "t")
        mf2._git(tmp, "add", "-A"); mf2._git(tmp, "commit", "-q", "-m", "c")
        self.assertFalse(os.path.exists(marker), "a planted pre-commit hook ran through the manager's git")

    def test_memory_folder_notes_timeline_coarsening(self):
        """Memory as files: a note per key with a regenerated index, one raw
        timeline entry per turn, coarsened deterministically (trimmed after
        RAW_DAYS, folded into a weekly file after DAILY_DAYS), git-committed."""
        import mgr.memfs as mf
        from datetime import date
        tmp = tempfile.mkdtemp(prefix="e2e-memfs-")
        old = mf.MEMORY_ROOT
        try:
            mf.MEMORY_ROOT = os.path.join(tmp, "memory")
            d = mf.folder("vc")
            self.assertTrue(os.path.isdir(os.path.join(d, ".git")))
            p = mf.note_write("vc", "Lieblingssender", "Swiss Classic, morgens leise.")
            self.assertTrue(p.endswith("notes/lieblingssender.md"))
            idx = mf.index_text("vc")
            self.assertIn("[[lieblingssender]] Lieblingssender — Swiss Classic", idx)
            mf.note_write("vc", "Lieblingssender", None)
            self.assertNotIn("lieblingssender", mf.index_text("vc"))
            self.assertIsNone(mf.folder("../x"))          # no traversal via the name
            # timeline: raw today, trimmed at 3 days, weekly at 15 days
            import time as _t
            now = _t.time()
            mf.timeline_add("vc", "voice", "Radio an " * 40, "OK.", when=now)
            mf.timeline_add("vc", "voice", "Wie spät?", "12:32.", when=now - 3 * 86400)
            mf.timeline_add("vc", "task", "MSFT-Kurs holen", "499.7", when=now - 20 * 86400)
            files = sorted(os.listdir(os.path.join(d, "timeline")))
            self.assertEqual(len(files), 3)
            raw_today = open(os.path.join(d, "timeline", files[-1])).read()
            self.assertIn("[voice] Radio an", raw_today)
            st = mf.coarsen("vc", today=date.today())
            self.assertEqual(st, {"trimmed": 1, "folded": 1})
            files = sorted(os.listdir(os.path.join(d, "timeline")))
            self.assertTrue(any("-W" in f for f in files), files)           # weekly file exists
            self.assertEqual(sum(1 for f in files if "-W" not in f), 2)    # today + the trimmed day
            self.assertTrue(mf.commit("vc", "test"))
            self.assertEqual(mf.coarsen("vc", today=date.today()), {"trimmed": 0, "folded": 0})
            # the agent's runtime state (persisted history) is never committed as a note
            os.makedirs(os.path.join(d, ".state")); open(os.path.join(d, ".state", "history.json"), "w").write("[]")
            open(os.path.join(d, "notes", "x.md"), "w").write("x")
            self.assertTrue(mf.commit("vc", "state"))
            tracked = mf._git(d, "ls-files").stdout
            self.assertIn("notes/x.md", tracked); self.assertNotIn(".state", tracked)
        finally:
            mf.MEMORY_ROOT = old

    def test_memory_folder_is_mounted_into_the_guest(self):
        """The folder rides the host-folder mechanism: exported to the guest
        only, mounted read-write at /memory; the config disk names it."""
        m = self.m
        import mgr.memfs as mf
        tmp = tempfile.mkdtemp(prefix="e2e-memmount-")
        old = mf.MEMORY_ROOT
        try:
            mf.MEMORY_ROOT = os.path.join(tmp, "memory")
            inst = {"name": "vc", "index": 8, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4",
                    "mounts": [{"host": tmp, "guest": "/mnt/x"}]}
            specs = m._mounts.mount_specs(inst)
            mem = [s for s in specs if s["guest"] == "/memory"]
            self.assertEqual(len(mem), 1)
            self.assertFalse(mem[0]["ro"])
            self.assertTrue(mem[0]["host"].endswith("/memory/vc"))
            self.assertEqual(mem[0]["fsid"], 4000 + 8 * 16 + 15)
            self.assertEqual(m._mounts.mount_specs({**inst, "template": "claude", "rootfs": "instances/claude-rootfs.ext4"})[0]["guest"], "/mnt/x")
        finally:
            mf.MEMORY_ROOT = old

    def test_secret_broker_requires_guest_readable(self):
        """A release lets the HUB substitute a key on the host; the raw value
        reaches a VM only when the key is also in guest_readable."""
        m = self.m
        guest = {"name": "hass", "template": "openrouter", "index": 7, "config": {}}
        old = m._guests.instance_by_ip, m._secrets.load_secret_policy, m._secrets.secret_store
        try:
            m._guests.instance_by_ip = lambda ip: guest if ip == "172.30.7.2" else None
            m._secrets.secret_store = lambda: {"HA_TOKEN": "t0k", "OPENROUTER_API_KEY": "k3y"}
            m._secrets.load_secret_policy = lambda: {"by_template": {"openrouter": ["OPENROUTER_API_KEY"]},
                                            "by_instance": {"hass": ["HA_TOKEN"]},
                                            "guest_readable": ["HA_TOKEN"]}
            self.assertEqual(m._secrets.allowed_secret_keys(guest), {"OPENROUTER_API_KEY", "HA_TOKEN"})
            self.assertEqual(m._secrets.guest_readable_keys(guest), {"HA_TOKEN"})
            h = self._handler("/api/secret/HA_TOKEN", "172.30.7.2"); h._do_GET()
            self.assertIn(b'"value": "t0k"', h.wfile.getvalue())
            h = self._handler("/api/secret/OPENROUTER_API_KEY", "172.30.7.2"); h._do_GET()
            self.assertIn(b" 403 ", h.wfile.getvalue().split(b"\r\n", 1)[0])   # released, not readable
            h = self._handler("/api/secrets", "172.30.7.2"); h._do_GET()
            self.assertIn(b'"allowed": ["HA_TOKEN"]', h.wfile.getvalue())
            # the saver keeps the third list, deduplicated and sorted
            tmp = tempfile.mkdtemp(prefix="e2e-secpol-"); oldf = m._secrets.SECRET_POLICY_FILE
            try:
                m._secrets.SECRET_POLICY_FILE = os.path.join(tmp, "p.json")
                m._secrets.save_secret_policy({"by_template": {}, "by_instance": {}, "guest_readable": ["B", "A", "B", 3]})
                self.assertEqual(json.load(open(m._secrets.SECRET_POLICY_FILE))["guest_readable"], ["A", "B"])
            finally:
                m._secrets.SECRET_POLICY_FILE = oldf
        finally:
            m._guests.instance_by_ip, m._secrets.load_secret_policy, m._secrets.secret_store = old

    # ---- Claude accounts (mgr/claudeaccts.py) ---------------------------------
    def _ca_env(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-ca-")
        old = (m._claudeaccts.ACCTS_FILE, m._audit.AUDIT_DIR, m._secrets.CLAUDE_CRED_SRC,
               m._claudeaccts._open, dict(m._claudeaccts.USAGE), m._claudeaccts._last[0])
        m._claudeaccts.ACCTS_FILE = os.path.join(tmp, "claude_accounts.json")
        m._audit.AUDIT_DIR = os.path.join(tmp, "audit")
        m._secrets.CLAUDE_CRED_SRC = os.path.join(tmp, "host-creds.json")
        m._claudeaccts.USAGE.clear()

        def undo():
            (m._claudeaccts.ACCTS_FILE, m._audit.AUDIT_DIR, m._secrets.CLAUDE_CRED_SRC,
             m._claudeaccts._open, usage, m._claudeaccts._last[0]) = old
            m._claudeaccts.USAGE.clear(); m._claudeaccts.USAGE.update(usage)
        return tmp, undo

    def test_claude_accounts_add_validate_list_without_secrets(self):
        """Accounts: names and tokens validated, the creds JSON needs an
        accessToken, the store is 0600, the list never carries a secret,
        an update keeps the stored token, default cannot be removed, audited."""
        m = self.m
        tmp, undo = self._ca_env()
        ca = m._claudeaccts
        try:
            self.assertIn("invalid name", ca.add("Bad Name", "", "tok"))
            self.assertIn("invalid name", ca.add("default", "", "tok"))
            self.assertIn("token required", ca.add("work", "", ""))
            self.assertIn("without spaces", ca.add("work", "", "a b"))
            self.assertIn("without spaces", ca.add("work", "", "ab\ncd"))
            self.assertIn("accessToken", ca.add("work", "", "fake-tok-SECRET1", '{"claudeAiOauth": {}}'))
            self.assertIn("accessToken", ca.add("work", "", "fake-tok-SECRET1", "not json"))
            self.assertEqual(ca.add("work", "Work", "fake-tok-SECRET1",
                                    json.dumps({"claudeAiOauth": {"accessToken": "AT-SECRET2", "refreshToken": "RT-SECRET3"}})), "work added")
            self.assertEqual(os.stat(ca.ACCTS_FILE).st_mode & 0o777, 0o600)
            self.assertEqual(ca.add("work", "Work 2", ""), "work updated")     # empty token = keep
            self.assertEqual(ca.token_for("work"), "fake-tok-SECRET1")
            self.assertEqual(ca.token_for("nope"), "")
            lst = ca.list_public()
            self.assertEqual([a["name"] for a in lst], ["default", "work"])
            w = lst[1]
            self.assertEqual((w["label"], w["has_token"], w["has_creds"]), ("Work 2", True, True))
            self.assertNotIn("SECRET", json.dumps(lst))
            h = self._handler("/api/claude/accounts", "127.0.0.1"); h._do_GET()
            self.assertEqual(self._status(h), 200)
            self.assertNotIn(b"SECRET", h.wfile.getvalue())
            self.assertIn("cannot be removed", ca.remove("default"))
            self.assertEqual(ca.remove("work"), "work removed")
            self.assertEqual(ca.list_public()[1:], [])
            with open(os.path.join(m._audit.AUDIT_DIR, "admin.jsonl")) as fh:
                recs = [json.loads(x) for x in fh]
            self.assertEqual([r["tool"] for r in recs], ["claude_account_set", "claude_account_set", "claude_account_remove"])
            self.assertNotIn("SECRET", json.dumps(recs))
        finally:
            undo()

    def test_claude_credentials_route_per_account(self):
        """/api/claude-credentials: an instance with CLAUDE_ACCOUNT gets its
        setup-token as oauth_token, an unknown account an error, one without
        the host's claudeAiOauth block as before; non-claude guests 403."""
        m = self.m
        tmp, undo = self._ca_env()
        guests = {"172.30.5.2": {"name": "c1", "template": "claude", "index": 5, "config": {"CLAUDE_ACCOUNT": "work"}},
                  "172.30.6.2": {"name": "c2", "template": "claude", "index": 6, "config": {"CLAUDE_ACCOUNT": ""}},
                  "172.30.7.2": {"name": "c3", "template": "claude", "index": 7, "config": {"CLAUDE_ACCOUNT": "ghost"}},
                  "172.30.8.2": {"name": "o1", "template": "openrouter", "index": 8, "config": {}}}
        old_ip = m._guests.instance_by_ip
        try:
            m._guests.instance_by_ip = lambda ip: guests.get(ip)
            _wtext(m._secrets.CLAUDE_CRED_SRC, json.dumps({"claudeAiOauth": {"accessToken": "host-at"}, "mcpOAuth": {"x": 1}}))
            m._claudeaccts.add("work", "", "tok-work")
            h = self._handler("/api/claude-credentials", "172.30.5.2"); h._do_GET()
            self.assertEqual(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1]), {"oauth_token": "tok-work"})
            h = self._handler("/api/claude-credentials", "172.30.6.2"); h._do_GET()
            self.assertEqual(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1]), {"claudeAiOauth": {"accessToken": "host-at"}})
            h = self._handler("/api/claude-credentials", "172.30.7.2"); h._do_GET()
            self.assertEqual(self._status(h), 404); self.assertIn(b"unknown Claude account", h.wfile.getvalue())
            h = self._handler("/api/claude-credentials", "172.30.8.2"); h._do_GET()
            self.assertEqual(self._status(h), 403)
        finally:
            m._guests.instance_by_ip = old_ip
            undo()

    def test_claude_usage_poller_parses_and_maps_errors(self):
        """The poller reads five_hour/seven_day per account (at most every
        5 min), a 403 becomes a short error without the token, an expired
        manager-owned login is refreshed and stored back, and the host login
        (default) is never refreshed — only marked expired."""
        import urllib.error
        m = self.m
        tmp, undo = self._ca_env()
        ca = m._claudeaccts
        calls = []

        def fake_open(req):
            calls.append((req.full_url, req.get_header("Authorization")))
            if req.full_url == ca.TOKEN_URL:
                body = json.loads(req.data)
                self.assertEqual((body["grant_type"], body["client_id"], body["refresh_token"]), ("refresh_token", ca.CLIENT_ID, "rt-own"))
                return io.BytesIO(json.dumps({"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600}).encode())
            if req.get_header("Authorization") == "Bearer tok-forbidden":
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"{}"))
            return io.BytesIO(json.dumps({"five_hour": {"utilization": 42.5, "resets_at": "2026-10-12T15:00:00Z"},
                                          "seven_day": {"utilization": 81, "resets_at": "2026-10-15T09:00:00Z"},
                                          "seven_day_opus": None}).encode())
        try:
            ca._open = fake_open
            now = 2_000_000_000
            _wtext(m._secrets.CLAUDE_CRED_SRC, json.dumps({"claudeAiOauth": {"accessToken": "host-at", "refreshToken": "host-rt",
                                                                               "expiresAt": (now - 10) * 1000}}))
            ca.add("own", "", "tok-own", json.dumps({"claudeAiOauth": {"accessToken": "at-old", "refreshToken": "rt-own",
                                                                       "expiresAt": (now - 10) * 1000, "scopes": ["user:inference", "user:profile"]}}))
            ca.add("plain", "", "tok-forbidden")
            ca._last[0] = 0
            u = ca.poll_usage(now=now)
            self.assertEqual(u["own"]["five_hour"], {"pct": 42.5, "resets_at": "2026-10-12T15:00:00Z"})
            self.assertEqual(u["own"]["seven_day"]["pct"], 81.0)
            self.assertEqual(u["own"]["error"], "")
            self.assertEqual(u["plain"]["error"], "no usage access for this token (403)")
            self.assertNotIn("tok-forbidden", json.dumps(u))
            self.assertEqual(u["default"], {"error": "expired", "checked": now})
            # default never refreshed: only the own login hit the token endpoint, with its own refresh token
            self.assertEqual([c for c in calls if c[0] == ca.TOKEN_URL], [(ca.TOKEN_URL, None)])
            self.assertNotIn("Bearer host-at", [c[1] for c in calls])
            stored = _readj(ca.ACCTS_FILE)["own"]["creds"]
            self.assertEqual((stored["accessToken"], stored["refreshToken"]), ("at-new", "rt-new"))
            self.assertIn(("https://api.anthropic.com/api/oauth/usage", "Bearer at-new"), calls)
            n = len(calls)
            ca.poll_usage(now=now + 60)                     # not due yet
            self.assertEqual(len(calls), n)
            h = self._handler("/api/claude/usage", "127.0.0.1"); h._do_GET()
            self.assertIn(b'"pct": 42.5', h.wfile.getvalue())
        finally:
            undo()

    def test_squad_member_gets_claude_account(self):
        """A squad's claude_account reaches new claude members as CLAUDE_ACCOUNT, not API members."""
        m = self.m
        self.assertEqual(m._squads.normalize_settings({"claude_account": "work"})[0]["claude_account"], "work")
        self.assertIsNone(m._squads.normalize_settings({"claude_account": "Bad Name"})[0])
        created, old = [], (m._instances.create_instance, m._instances.load_instances)
        try:
            m._instances.load_instances = lambda: []
            m._instances.create_instance = lambda n, t, cfg, persist=False: created.append((n, cfg)) or f"instance '{n}' created"
            proj = {"name": "sq", "squad": {"claude_account": "work"}}
            for name, tpl in (("c", "claude"), ("o", "openrouter")):
                mem = {"name": name, "role": "writer", "skills": [], "persona": "", "template": tpl,
                       "purpose": "", "model": "", "model_tier": ""}
                self.assertTrue(m._squadgate._create(proj, mem, []))
            self.assertEqual(created[0][1]["CLAUDE_ACCOUNT"], "work")
            self.assertNotIn("CLAUDE_ACCOUNT", created[1][1])
        finally:
            m._instances.create_instance, m._instances.load_instances = old

    def test_harness_disk_rebuilds_when_agent_source_changes(self):
        """The agent code rides a read-only drive built from AGENT_SRC: built
        once, reused while the sources' content is unchanged (mtimes are not
        trusted), rebuilt when a file changes; an incomplete source set or no
        AGENT_SRC gives no drive. Every rootfs carrying this agent uses it."""
        m = self.m
        if not shutil.which("mkfs.ext4", path="/usr/sbin:/sbin:" + os.environ.get("PATH", "")):
            self.skipTest("mkfs.ext4 not available")
        tmp = tempfile.mkdtemp(prefix="e2e-harness-")
        src = os.path.join(tmp, "src"); os.makedirs(src); run = os.path.join(tmp, "run"); os.makedirs(run)
        os.makedirs(os.path.join(src, "agent"))
        for n in ("agent/__init__.py", "agent/config.py", "run_agent.py", "webterm.py"):
            with open(os.path.join(src, n), "w") as fh:
                fh.write(f"# {n}\n")
        old = m._vm.AGENT_SRC, m._vm.HARNESS_IMG, m._paths.RUN_DIR
        try:
            m._vm.AGENT_SRC, m._vm.HARNESS_IMG, m._paths.RUN_DIR = src, os.path.join(run, "harness.ext4"), run
            self.assertEqual([os.path.relpath(p, src) for p in m._vm.harness_sources()],
                             ["agent/__init__.py", "agent/config.py", "run_agent.py", "webterm.py"])
            img = m._vm.harness_image()
            self.assertEqual(img, m._vm.HARNESS_IMG)
            self.assertTrue(os.path.exists(img) and os.path.exists(img + ".src"))
            ino1 = os.stat(img).st_ino
            os.utime(os.path.join(src, "agent", "config.py"), (time.time() + 60,) * 2)   # a newer mtime alone
            self.assertEqual(m._vm.harness_image(), img)
            self.assertEqual(os.stat(img).st_ino, ino1)                         # is no rebuild
            with open(os.path.join(src, "agent", "config.py"), "a") as fh:
                fh.write("VERSION = 2\n")
            m._vm.harness_image()
            self.assertNotEqual(os.stat(img).st_ino, ino1)                      # content change is
            self.assertEqual(len(os.listdir(run)), 2, os.listdir(run))          # no temp files left
            shutil.rmtree(os.path.join(src, "agent"))
            self.assertEqual(m._vm.harness_sources(), [])                           # incomplete: no drive
            inst = {"name": "x", "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4", "index": 9}
            self.assertTrue(m._vm.uses_harness(inst))
            self.assertTrue(m._vm.uses_harness({**inst, "template": "llama"}))      # same image, same agent
            self.assertFalse(m._vm.uses_harness({**inst, "template": "claude", "rootfs": "instances/claude-rootfs.ext4"}))
            m._vm.AGENT_SRC = ""
            self.assertIsNone(m._vm.harness_image())
        finally:
            m._vm.AGENT_SRC, m._vm.HARNESS_IMG, m._paths.RUN_DIR = old

    def test_secret_policy_seeds_guest_readable_on_upgrade(self):
        """A policy file from before the two-rights model has no guest_readable:
        it is seeded from the releases once (nothing breaks on upgrade) and
        the file is rewritten with the list; a file that has the key is left alone."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-secpol2-"); oldf, olds = m._secrets.SECRET_POLICY_FILE, m._settings.load_settings
        try:
            m._secrets.SECRET_POLICY_FILE = os.path.join(tmp, "p.json")
            legacy = {"by_template": {"openrouter": ["OPENROUTER_API_KEY"]},
                      "by_instance": {"hass": ["HA_TOKEN", "OPENROUTER_API_KEY"]}}
            with open(m._secrets.SECRET_POLICY_FILE, "w") as fh:
                json.dump(legacy, fh)
            m._settings.load_settings = lambda: {}
            self.assertEqual(m._secrets.load_secret_policy()["guest_readable"], ["HA_TOKEN", "OPENROUTER_API_KEY"])
            self.assertEqual(json.load(open(m._secrets.SECRET_POLICY_FILE))["guest_readable"], ["HA_TOKEN", "OPENROUTER_API_KEY"])
            with open(m._secrets.SECRET_POLICY_FILE, "w") as fh:
                json.dump(legacy, fh)
            m._settings.load_settings = lambda: {"LLM_KEY_PROXY": "1"}       # proxy on: the LLM key stays on the host
            self.assertEqual(m._secrets.load_secret_policy()["guest_readable"], ["HA_TOKEN"])
            m._secrets.save_secret_policy({"by_template": {"openrouter": ["OPENROUTER_API_KEY"]}, "by_instance": {}, "guest_readable": []})
            self.assertEqual(m._secrets.load_secret_policy()["guest_readable"], [])          # an explicit empty list stays
        finally:
            m._secrets.SECRET_POLICY_FILE, m._settings.load_settings = oldf, olds

    def test_stale_image_detection_and_rebuild_push(self):
        """A running VM started before its base image was rebuilt is 'stale':
        the API says so, and the idle sweep pushes once per rebuild, naming
        the affected instances — silent when nothing is affected."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-stale-")
        os.makedirs(os.path.join(tmp, "instances")); os.makedirs(os.path.join(tmp, "run"))
        img = os.path.join(tmp, "instances", "openrouter-rootfs.ext4")
        old = m._paths.BASE, m._paths.RUN_DIR, m._instances.is_running, m._instances.load_instances, m._notify.notify_add, dict(m._vm._img_seen), m._vm.HARNESS_IMG
        pushes = []
        try:
            m._paths.BASE, m._paths.RUN_DIR = tmp, os.path.join(tmp, "run")
            m._vm.HARNESS_IMG = os.path.join(tmp, "run", "harness.ext4")    # none built here: rootfs only
            m._instances.is_running = lambda i: i["name"] != "off"
            insts = [{"name": "old", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "fresh", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "off", "rootfs": "instances/openrouter-rootfs.ext4"},
                     {"name": "priv", "rootfs": "instances/priv.ext4"}]
            m._instances.load_instances = lambda: insts
            m._notify.notify_add = lambda *a, **k: pushes.append(a)
            for n, ts in (("old", 1000), ("fresh", 3000), ("off", 1000), ("priv", 1000)):
                pf = os.path.join(tmp, "run", n + ".pid"); open(pf, "w").write("1"); os.utime(pf, (ts, ts))
            open(img, "w").write("x"); os.utime(img, (2000, 2000))
            self.assertEqual([m._vm.image_state(i)[0] for i in insts], [True, False, False, False])
            self.assertEqual(m._vm.stale_instances(), ["old"])
            m._vm._img_seen.clear()
            self.assertEqual(m._vm.image_sweep(), [])           # first sight: baseline, no push
            self.assertEqual(m._vm.image_sweep(), [])           # unchanged: quiet
            os.utime(img, (4000, 4000))                      # rebuilt -> both VMs older now
            self.assertEqual(m._vm.image_sweep(), ["old", "fresh"])
            self.assertEqual(len(pushes), 1)
            self.assertIn("old, fresh", pushes[0][2])
            self.assertEqual(m._vm.image_sweep(), [])           # once per rebuild
        finally:
            m._paths.BASE, m._paths.RUN_DIR, m._instances.is_running, m._instances.load_instances, m._notify.notify_add = old[:5]
            m._vm._img_seen.clear(); m._vm._img_seen.update(old[5]); m._vm.HARNESS_IMG = old[6]

    def test_guest_config_carries_host_timezone(self):
        """Guests boot in UTC; the manager hands them the host's zone name."""
        m = self.m
        self.assertTrue(m._host.HOST_TZ)
        self.assertEqual(m._host.HOST_TZ, os.environ.get("GUEST_TZ") or m._host._host_tz())

    def test_task_model_reaches_the_ephemeral_vm(self):
        """spawn_subagent/create_task may name a model: it travels through
        /api/task (wait) and through the queue (worker) into _run_ephemeral;
        a named instance keeps its own model."""
        m = self.m
        import mgr.store as st
        seen = []
        old_eph, old_named, old_file = m._tasks._run_ephemeral, m._tasks._run_named, st.TASKS_FILE
        try:
            m._tasks._run_ephemeral = lambda msg, model=None, timeout=600: (seen.append(("eph", msg, model)), (True, "r"))[1]
            m._tasks._run_named = lambda inst, msg, timeout=600: (seen.append(("named", inst, msg)), (True, "r"))[1]
            m._tasks._run_task_now("ephemeral", "do", "google/gemini-2.5-flash")
            m._tasks._run_task_now("ephemeral", "do")
            m._tasks._run_task_now("hass", "do", "google/gemini-2.5-flash")
            self.assertEqual(seen, [("eph", "do", "google/gemini-2.5-flash"), ("eph", "do", None),
                                    ("named", "hass", "do")])
            st.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-taskmodel-"), "tasks.json")
            t = st.add_task("ephemeral", "queued", "", model=" x/y ")
            self.assertEqual(st.load_tasks()[0]["model"], "x/y")
            t2 = st.add_task("ephemeral", "plain", "")
            self.assertNotIn("model", next(x for x in st.load_tasks() if x["id"] == t2["id"]))
        finally:
            m._tasks._run_ephemeral, m._tasks._run_named, st.TASKS_FILE = old_eph, old_named, old_file

    def test_tasks_delete_all_done(self):
        """Tasks tab "Delete all done": one-off done tasks go, recurring and
        open/error ones stay; admin only (role user may not)."""
        m = self.m
        import mgr.store as st
        old_file = st.TASKS_FILE
        try:
            st.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-taskdone-"), "tasks.json")
            a, b, c, d = (st.add_task("x", s) for s in ("a", "b", "c", "d"))
            r = st.add_task("x", "rec", "daily 08:00")
            st.with_tasks(lambda ts: (True, [t.update(status=s) for t, s in zip(ts, ("done", "done", "error", "pending", "done"))]))
            h = self._post_handler("/api/tasks/done/delete", "127.0.0.1", b"{}")
            h._do_POST()
            self.assertEqual(self._status(h), 200)
            self.assertIn(b"2 done task(s) deleted", h.wfile.getvalue())
            self.assertEqual(sorted(t["id"] for t in st.load_tasks()), sorted([c["id"], d["id"], r["id"]]))
            self.assertFalse(m._auth.user_allowed("POST", "/api/tasks/done/delete"))
        finally:
            st.TASKS_FILE = old_file

    def test_task_target_resolved_and_validated(self):
        """'@orchestrator' (an agent's typo) failed daily with 'instance unknown'
        and nobody was told. Targets lose a leading '@', unknown names are
        refused at creation, and an edit may move a task to another instance."""
        m = self.m
        import mgr.store as st
        old_load, old_file = m._instances.load_instances, st.TASKS_FILE
        try:
            m._instances.load_instances = lambda: [{"name": "orchestrator"}, {"name": "hass"}]
            self.assertEqual(m._tasks.resolve_task_target("@orchestrator"), ("orchestrator", ""))
            self.assertEqual(m._tasks.resolve_task_target(" hass "), ("hass", ""))
            self.assertEqual(m._tasks.resolve_task_target(""), ("ephemeral", ""))
            self.assertEqual(m._tasks.resolve_task_target(None), ("ephemeral", ""))
            self.assertEqual(m._tasks.resolve_task_target("@")[0], "ephemeral")
            name, err = m._tasks.resolve_task_target("orchestartor")
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
            old_notify = m._notify.notify_add
            m._notify.notify_add = lambda *a, **k: pushes.append(a)
            try:
                hit = m._tasks.task_target_sweep()
                self.assertEqual([h[1] for h in hit], ["@orchestrator"])
                self.assertEqual(len(pushes), 1)
                self.assertIn("@orchestrator", pushes[0][2])
                self.assertEqual(m._tasks.task_target_sweep(), [])          # not again
                bad = next(t for t in st.load_tasks() if t["instance"] == "@orchestrator")
                st.update_task(bad["id"], instance="orchestrator")   # fixed -> mark gone
                self.assertNotIn("target_warned", next(t for t in st.load_tasks() if t["id"] == bad["id"]))
            finally:
                m._notify.notify_add = old_notify
        finally:
            m._instances.load_instances, st.TASKS_FILE = old_load, old_file

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
            mid, _ = m._missions.mission_start("hass", "Goal A", ["s1", "s2", "s3"])
            m._missions.mission_update("hass", mid, step=1, status="done", result="r1")
            m._missions.mission_finish("hass", mid, "all good")            # -> done
            # edit a DONE mission (no instance given -> owner lookup)
            self.assertEqual(m._missions.mission_edit("", mid, goal="Goal B",
                                            steps=["s1", "s2x", "s3", "s4"]), "ok")
            mi = m._missions.mission_list("hass")[0]
            self.assertEqual(mi["goal"], "Goal B")
            self.assertEqual([s["text"] for s in mi["steps"]], ["s1", "s2x", "s3", "s4"])
            self.assertEqual(mi["steps"][0]["status"], "done")   # progress kept by position
            self.assertEqual(mi["steps"][0]["result"], "r1")
            self.assertEqual(mi["steps"][3]["status"], "open")   # new step
            self.assertEqual(mi["status"], "done")
            # reopen -> active; the agent's mission_update works again
            self.assertEqual(m._missions.mission_edit("hass", mid, status="active"), "ok")
            self.assertEqual(m._missions.mission_update("hass", mid, step=4, status="doing"), "ok")
            self.assertEqual(m._missions.mission_edit("hass", mid, status="active"), "unchanged")
            self.assertIn("unknown status", m._missions.mission_edit("hass", mid, status="weird"))
            self.assertEqual(m._missions.mission_edit("hass", mid, goal="  "), "goal missing")
            self.assertEqual(m._missions.mission_edit("hass", mid, steps=[]), "steps missing")
            self.assertEqual(m._missions.mission_edit("hass", "m-nope"), "unknown mission")
            # cap: 5 active others -> reopening a failed one is refused
            for i in range(m._missions.MISSION_MAX_ACTIVE):
                m._missions.mission_start("cap", f"g{i}", ["x"])
            fid, _ = m._missions.mission_start("cap", "late", ["x"])     # None: cap reached
            self.assertIsNone(fid)
            m._missions.mission_finish("cap", m._missions.mission_list("cap")[0]["id"], "bad", failed=True)
            m._missions.mission_start("cap", "fill", ["x"])                 # back at the cap
            failed_id = m._missions.mission_list("cap")[0]["id"]
            self.assertIn("max", m._missions.mission_edit("cap", failed_id, status="active"))
            # delete: failed, then active, then unknown
            self.assertEqual(m._missions.mission_delete("", failed_id), "ok")
            self.assertEqual(m._missions.mission_delete("hass", mid), "ok")
            self.assertEqual(m._missions.mission_list("hass"), [])
            self.assertEqual(m._missions.mission_delete("hass", mid), "unknown mission")
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
            mid, _ = m._missions.mission_start("jobresearcher", "cross-instance goal", ["s1", "s2"])
            self.assertTrue(mid)
            # Step 1 is executed by a DIFFERENT agent than the owner.
            self.assertEqual(m._missions.mission_update("jobresearcher", mid, step=1, status="doing",
                                              task_id="t-x1", target="hass"), "ok")
            st1 = m._missions.mission_list("jobresearcher")[0]["steps"][0]
            self.assertEqual(st1["target"], "hass")
            self.assertIn("@hass", m._missions.mission_list("jobresearcher")[0]["log"][-1])
            # The advance trigger has to find the OWNER, not the executor.
            inst, mi, st = m._missions.mission_for_task("t-x1")
            self.assertEqual((inst, mi["id"], st["n"]), ("jobresearcher", mid, 1))
            self.assertEqual(m._missions.mission_owner(mid), "jobresearcher")
            self.assertIsNone(m._missions.mission_owner("m-nope"))
            # Admin action without an instance resolves the owner itself.
            self.assertEqual(m._missions.mission_admin("", mid, "pause"), "ok")
            self.assertEqual(m._missions.mission_list("jobresearcher")[0]["status"], "paused")
            self.assertEqual(m._missions.mission_admin("", mid, "resume"), "ok")
            self.assertEqual(m._missions.mission_admin("", "m-nope", "pause"), "unknown mission")
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
        dirty, claimed = m._tasks.worker_claim(tasks, now, lambda t: True, skipped, throttled)
        self.assertTrue(dirty, "Skip mutiert next_run — MUSS dirty melden")
        self.assertIsNone(claimed)
        self.assertEqual(skipped, ["hb1"])
        self.assertGreater(hb["next_run"], now, "next_run muss vorruecken")
        # Zweiter Zyklus auf dem (nun gespeicherten) Stand: nichts mehr faellig.
        skipped2 = []
        dirty2, claimed2 = m._tasks.worker_claim(tasks, now + 5, lambda t: True, skipped2, [])
        self.assertFalse(dirty2)
        self.assertIsNone(claimed2)
        self.assertEqual(skipped2, [], "5 s spaeter darf NICHT erneut geskippt werden")
        # Nicht-idle (hb_idle False): der Task wird normal geclaimt.
        hb["next_run"] = 0
        dirty3, claimed3 = m._tasks.worker_claim(tasks, now, lambda t: False, [], [])
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
        old_run, old_load, old_win = m._tasks._run_named, m._instances.load_instances, m._tasks.MISSION_COLLECT_SECS
        pushes = []
        done = threading.Event()
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            m._instances.load_instances = lambda: [{"name": "owner-a"}]
            m._tasks._run_named = lambda inst, msg: (pushes.append((inst, msg)), done.set(), (True, "ok"))[-1]
            m._tasks.MISSION_COLLECT_SECS = 0.3
            mid, _ = m._missions.mission_start("owner-a", "burst goal", ["s1", "s2", "s3"])
            for n, tid in ((1, "t-b1"), (2, "t-b2"), (3, "t-b3")):
                m._missions.mission_update("owner-a", mid, step=n, status="doing", task_id=tid)
                m._tasks._mission_advance_fire(tid)
            self.assertTrue(done.wait(5), "no push fired")
            time.sleep(0.4)                            # window fully drained
            self.assertEqual(len(pushes), 1, f"expected ONE push, got {len(pushes)}")
            inst, msg = pushes[0]
            self.assertEqual(inst, "owner-a")
            for tid in ("t-b1", "t-b2", "t-b3"):
                self.assertIn(tid, msg)
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify
            m._tasks._run_named, m._instances.load_instances = old_run, old_load
            m._tasks.MISSION_COLLECT_SECS = old_win

    def test_mission_advance_fires_at_owner(self):
        """Regression guard: the push after a finished task goes to the mission's
        owner — previously it was hard-wired to the orchestrator, so a mission
        owned by any other agent would never advance. The push is debounced by
        the collect window, hence the shortened window here."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi4-")
        import mgr.missions as mmod
        old_file, old_notify = mmod.MISSIONS_FILE, mmod.notify_add
        old_run, old_load, old_win = m._tasks._run_named, m._instances.load_instances, m._tasks.MISSION_COLLECT_SECS
        fired = []
        done = threading.Event()
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            mmod.notify_add = lambda *a, **k: ("x", "ok")
            m._tasks.MISSION_COLLECT_SECS = 0.2
            m._instances.load_instances = lambda: [{"name": "jobresearcher"}, {"name": "orchestrator"}]
            m._tasks._run_named = lambda inst, msg: (fired.append(inst), done.set(), (True, "ok"))[-1]
            mid, _ = m._missions.mission_start("jobresearcher", "owned elsewhere", ["s1"])
            m._missions.mission_update("jobresearcher", mid, step=1, status="doing",
                             task_id="t-y1", target="hass")
            m._tasks._mission_advance_fire("t-y1")
            self.assertTrue(done.wait(5), "no advance push fired")
            self.assertEqual(fired, ["jobresearcher"])
            # Owner gone -> no push (the TTL sweep pauses the mission instead).
            fired.clear(); done.clear()
            m._instances.load_instances = lambda: [{"name": "orchestrator"}]
            m._tasks._mission_advance_fire("t-y1")
            self.assertFalse(done.wait(0.6))
            self.assertEqual(fired, [])
        finally:
            mmod.MISSIONS_FILE, mmod.notify_add = old_file, old_notify
            m._tasks._run_named, m._instances.load_instances = old_run, old_load
            m._tasks.MISSION_COLLECT_SECS = old_win

    def test_mission_caps(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi2-")
        import mgr.missions as mmod
        old_file = mmod.MISSIONS_FILE
        try:
            mmod.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            for i in range(m._missions.MISSION_MAX_ACTIVE):
                self.assertTrue(m._missions.mission_start("o", f"g{i}", ["s"])[0])
            self.assertIsNone(m._missions.mission_start("o", "zuviel", ["s"])[0])
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
        old = st.TASKS_FILE                      # the live file is root-only now: read a copy
        try:
            st.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-tasks-"), "tasks.json")
            with open(st.TASKS_FILE, "w") as fh:
                fh.write("[]")
            self.assertIsInstance(st.load_tasks(), list)
        finally:
            st.TASKS_FILE = old

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

    def test_worker_one_lane_per_instance(self):
        """worker_claim: a due task of an instance that already runs one waits
        untouched; other instances and 'ephemeral' (own VM per task) go."""
        m = self.m
        now = int(time.time())
        tasks = [{"id": "x1", "instance": "x", "status": "running", "message": "m"},
                 {"id": "x2", "instance": "x", "status": "pending", "message": "m"},
                 {"id": "y1", "instance": "y", "status": "pending", "message": "m"}]
        dirty, t = m._tasks.worker_claim(tasks, now, lambda t: False, [], [])
        self.assertEqual(t["id"], "y1")
        self.assertEqual(tasks[1]["status"], "pending"); self.assertNotIn("recent_runs", tasks[1])   # untouched
        dirty, t = m._tasks.worker_claim(tasks, now, lambda t: False, [], [])
        self.assertIsNone(t)                                          # x busy, y now running
        eph = [{"id": "e1", "instance": "ephemeral", "status": "running", "message": "m"},
               {"id": "e2", "instance": "ephemeral", "status": "pending", "message": "m"}]
        self.assertEqual(m._tasks.worker_claim(eph, now, lambda t: False, [], [])[1]["id"], "e2")

    def test_worker_runs_instances_in_parallel_but_each_serially(self):
        """The dispatcher against a real (temporary) task store: tasks of
        different instances overlap, at most WORKER_PARALLEL at once, never
        two of one instance; every task ends done and is logged."""
        import threading as _th
        m, tk = self.m, self.m._tasks
        tmp = tempfile.mkdtemp(prefix="e2e-lanes-")
        running, peak, per_inst, lock = set(), [0], {}, _th.Lock()
        overlap_same = []
        def fake_run(instance, message, model=None, timeout=0, sandbox=None):
            with lock:
                if instance in running:
                    overlap_same.append(instance)
                running.add(instance); peak[0] = max(peak[0], len(running))
            time.sleep(0.4)
            with lock:
                running.discard(instance); per_inst[instance] = per_inst.get(instance, 0) + 1
            return True, f"ok {message}"
        saved = (m._store.TASKS_FILE, tk._run_task_now, tk.WORKER_PARALLEL, m._chats.chat_log_append,
                 m._store.history_add, tk._mission_advance_fire, tk.time.sleep)
        try:
            m._store.TASKS_FILE = os.path.join(tmp, "tasks.json")
            m._store.save_tasks([]) if hasattr(m._store, "save_tasks") else open(m._store.TASKS_FILE, "w").write("[]")
            for inst, n in (("x", 3), ("y", 1), ("z", 1), ("w", 1)):
                for k in range(n):
                    m._store.add_task(inst, f"{inst}{k}")
            tk._run_task_now, tk.WORKER_PARALLEL = fake_run, 2
            m._chats.chat_log_append = lambda *a, **k: None
            m._store.history_add = lambda *a, **k: None
            tk._mission_advance_fire = lambda tid: None
            real_sleep = saved[6]
            tk.time.sleep = lambda s: real_sleep(min(s, 0.05))         # fast dispatcher cycles
            tk._stop.clear()
            th = _th.Thread(target=tk._task_worker, daemon=True); th.start()
            deadline = time.time() + 15
            while time.time() < deadline and any(t["status"] != "done" for t in m._store.load_tasks()):
                real_sleep(0.1)
            tk._stop.set(); th.join(5)
            done = m._store.load_tasks()
            self.assertTrue(all(t["status"] == "done" for t in done), [(t["id"], t["status"]) for t in done])
            self.assertEqual(per_inst, {"x": 3, "y": 1, "z": 1, "w": 1})
            self.assertEqual(overlap_same, [])                                        # never two of one instance
            self.assertEqual(peak[0], 2)                                              # parallel, and capped
            self.assertTrue(all(t["result"].startswith("ok ") for t in done))
        finally:
            tk._stop.set()
            (m._store.TASKS_FILE, tk._run_task_now, tk.WORKER_PARALLEL, m._chats.chat_log_append,
             m._store.history_add, tk._mission_advance_fire, tk.time.sleep) = saved
            tk._active.clear()

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
            m._tasks.reclaim_stuck_tasks()
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
        r1 = m._llmproxy._guard_check(inst)[0]
        r2 = m._llmproxy._guard_check(inst)[0]
        r3, why = m._llmproxy._guard_check(inst)
        self.assertTrue(r1 and r2)
        self.assertFalse(r3)
        self.assertIn("rate", why)
        self.assertTrue(m._llmproxy._guard_check(None)[0])   # Admin/Host immer frei

    def test_budget_is_runtime_configurable_per_instance(self):
        """BUDGET_TOKENS: the key proxy reads it per request, so raising or
        lowering it applies at once; the config route says so (and still says
        'after stop/start' for keys the VM reads); a bad value is refused; the
        Policy tab gets budget, default flag and today's usage."""
        m, lp = self.m, self.m._llmproxy
        tmp = tempfile.mkdtemp(prefix="e2e-bud-")
        old = (lp.used_today, m._paths.INST_DIR, m._instances.is_running, dict(lp._guard_calls))
        try:
            self.assertEqual(lp.budget_of({"name": "x"}), lp.GUARD_BUDGET_TOKENS)
            self.assertEqual(lp.budget_of({"name": "x", "config": {"BUDGET_TOKENS": "20000000"}}), 20_000_000)
            self.assertEqual(lp.budget_of({"name": "x", "config": {"BUDGET_TOKENS": "lots"}}), lp.GUARD_BUDGET_TOKENS)
            lp.used_today = lambda name: 5_021_229
            inst = {"name": "e2e-bud", "config": {"LLM_RATE_MIN": "0"}}
            ok, why = lp._guard_check(inst)
            self.assertFalse(ok); self.assertIn("5,021,229/5,000,000", why)
            inst["config"]["BUDGET_TOKENS"] = "20000000"                       # raised: the next call passes
            self.assertEqual(lp._guard_check(inst), (True, ""))
            inst["config"]["BUDGET_TOKENS"] = "0"                              # 0 = no limit
            self.assertTrue(lp._guard_check(inst)[0])
            # the config route
            m._paths.INST_DIR = tmp
            json.dump({"name": "e2e-bud", "index": 7, "config": {}}, open(os.path.join(tmp, "e2e-bud.json"), "w"))
            m._instances.is_running = lambda i: True
            self.assertEqual(m._instances._set_config_key("e2e-bud", "BUDGET_TOKENS", "20000000"),
                             "BUDGET_TOKENS = 20000000 (applies at once)")
            self.assertIn("whole number", m._instances._set_config_key("e2e-bud", "BUDGET_TOKENS", "2e7"))
            self.assertIn("after stop/start", m._instances._set_config_key("e2e-bud", "AUTO_RESET_MIN", "30"))
            # the katfs share is read by the manager per file operation -> a running instance gets it at once
            self.assertEqual(m._instances._set_config_key("e2e-bud", "KATFS_SHARE", "abc123"), "KATFS_SHARE = abc123 (applies at once)")
            self.assertEqual(m._katfs.katfs_share_for(_readj(os.path.join(tmp, "e2e-bud.json"))), "abc123")
            self.assertEqual(_readj(os.path.join(tmp, "e2e-bud.json"))["config"]["BUDGET_TOKENS"], "20000000")
            pol = m._policy.effective_policy(_readj(os.path.join(tmp, "e2e-bud.json")))
            self.assertEqual((pol["budget"], pol["budget_default"], pol["used_today"]), (20_000_000, False, 5_021_229))
            self.assertEqual(m._instances._set_config_key("e2e-bud", "BUDGET_TOKENS", ""), "BUDGET_TOKENS removed (applies at once)")
            pol = m._policy.effective_policy(_readj(os.path.join(tmp, "e2e-bud.json")))
            self.assertEqual((pol["budget"], pol["budget_default"]), (lp.GUARD_BUDGET_TOKENS, True))
        finally:
            lp.used_today, m._paths.INST_DIR, m._instances.is_running = old[:3]
            lp._guard_calls.clear(); lp._guard_calls.update(old[3])

    def test_usage_for_shape(self):
        m = self.m
        d = m._store.usage_for("orchestrator", 0)
        self.assertEqual(set(d.keys()), {"calls", "in", "out", "cost"})
        self.assertIsInstance(d["calls"], int)
        z = m._store.usage_for("gibtsnichtxyz", 0)     # unknown instance -> zeros
        self.assertEqual(z["calls"], 0)

    def test_prompt_store(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-pr-")
        import mgr.rules as rmod
        old = rmod.PROMPTS_FILE
        try:
            rmod.PROMPTS_FILE = os.path.join(tmp, "prompts.json")
            self.assertEqual(m._rules.prompt_upsert("Daily!", "Text"), "saved")   # Name normalisiert
            self.assertEqual(m._rules.load_prompts()[0]["name"], "daily")
            self.assertEqual(m._rules.prompt_upsert("daily", "New"), "saved")     # update
            self.assertEqual(m._rules.load_prompts()[0]["text"], "New")
            self.assertIn("built-in", m._rules.prompt_upsert("reset", "x"))       # reserved
            self.assertEqual(m._rules.prompt_delete("daily"), "deleted")
            self.assertEqual(m._rules.prompt_delete("daily"), "unknown")
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
            nid, note = m._notify.notify_add("orchestrator", "Title", "Text")
            self.assertTrue(nid)
            lst = m._notify.load_notifications()
            self.assertEqual(len(lst), 1)
            self.assertEqual(lst[0]["title"], "Title")
            self.assertFalse(lst[0]["read"])
            self.assertEqual(m._notify.notif_mark_read(mark_all=True), 1)
            self.assertTrue(m._notify.load_notifications()[0]["read"])
            self.assertIsNone(m._notify.notify_add("x", "", "")[0])   # leer -> nichts
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
        # Self-contained: a catalog entry and a policy of its own, so the test
        # does not depend on this host's catalog (a fresh install has none).
        entry = {"name": "caldav", "command": "caldav-mcp", "args": [],
                 "env": {"CALDAV_BASE_URL": "https://cal.example.com/dav", "CALDAV_USERNAME": "me",
                         "CALDAV_PASSWORD": "${CALDAV_PASSWORD}"}}
        old = mcpmod.secret_store, mcpmod.load_mcps, m._mcp.load_mcps, m._secrets.load_secret_policy
        mcpmod.load_mcps = m._mcp.load_mcps = lambda: [entry]
        m._secrets.load_secret_policy = lambda: {"by_template": {}, "guest_readable": [],
                                        "by_instance": {"myassistant": ["CALDAV_PASSWORD"], "voicecommand": ["CALDAV_PASSWORD"]}}
        self.assertIn("caldav", [x["name"] for x in m._mcp.load_mcps()])
        self.assertEqual(m._mcp.mcp_required_secrets(["caldav"]), {"CALDAV_PASSWORD"})
        for name in ("myassistant", "voicecommand"):
            self.assertIn("CALDAV_PASSWORD",
                          m._secrets.allowed_secret_keys({"name": name, "template": "openrouter"}),
                          f"{name} has no CALDAV_PASSWORD release")
        mcpmod.secret_store = lambda: {"CALDAV_PASSWORD": "s3cret"}
        try:
            env = json.loads(m._mcp.build_mcp_config(
                ["caldav"], allowed={"CALDAV_PASSWORD"}))["mcpServers"]["caldav"]["env"]
            self.assertEqual(env["CALDAV_PASSWORD"], "s3cret")
            env = json.loads(m._mcp.build_mcp_config(
                ["caldav"], allowed=set()))["mcpServers"]["caldav"]["env"]
            self.assertEqual(env["CALDAV_PASSWORD"], "${CALDAV_PASSWORD}")
        finally:
            mcpmod.secret_store, mcpmod.load_mcps, m._mcp.load_mcps, m._secrets.load_secret_policy = old

    # ---- guest boundary (multi-tenancy S-fixes, 2026-09-06) ------------------
    def _handler(self, path, ip, method="GET", auth=None):
        """A handler object without a socket: enough of BaseHTTPRequestHandler's
        state for _auth/_do_GET to run and write their response into a buffer."""
        import email.message
        m = self.m
        h = object.__new__(m._httpd.H)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = (ip, 40000)
        h.headers = email.message.Message()
        if auth:
            h.headers["Authorization"] = auth
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.close_connection = True
        return h

    def _post_handler(self, path, ip, body=b"", origin=None, ctype="application/json"):
        h = self._handler(path, ip, method="POST")
        h.rfile = io.BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        h.headers["Content-Type"] = ctype
        if origin is not None:
            h.headers["Origin"] = origin
        return h

    @staticmethod
    def _status(h):
        return int(h.wfile.getvalue().split(b" ", 2)[1] or 0)

    # ---- skills: history of proposals, measured use, the fixed context ----------
    def _skills_env(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-skl-")
        st, sk = m._store, m._skills
        old = (st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], sk.SKILLS_FILE, sk.SKILL_PROPOSALS_FILE,
               m._notify.notify_add)
        st.HISTORY_DB, st._migrated[0], st._turns_migrated[0] = os.path.join(tmp, "history.db"), False, None
        sk.SKILLS_FILE, sk.SKILL_PROPOSALS_FILE = os.path.join(tmp, "skills.json"), os.path.join(tmp, "props.json")
        m._notify.notify_add = lambda *a, **k: None

        def restore():
            (st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], sk.SKILLS_FILE, sk.SKILL_PROPOSALS_FILE,
             m._notify.notify_add) = old
        return tmp, restore

    def test_discarded_skill_is_not_redrawn(self):
        """A discard carries a reason; the same name is refused for 30 days and
        the distiller's history lists it — RRSI: condition on the edit history."""
        m = self.m
        sk = m._skills
        tmp, restore = self._skills_env()
        body = "## Purpose\n" + "step " * 30
        try:
            pid, _ = sk.proposal_add("vm1", "cv-mailer", "mail CVs", body)
            self.assertIn("discarded", sk.proposal_decide(pid, False, reason="one-off, not reusable"))
            pid2, why = sk.proposal_add("vm1", "cv-mailer", "mail CVs again", body)
            self.assertIsNone(pid2)
            self.assertIn("one-off, not reusable", why)
            h = sk.proposal_history()
            self.assertEqual(h["discarded"], [{"name": "cv-mailer", "description": "mail CVs", "reason": "one-off, not reusable"}])
            # after the window the idea may come back
            items = sk.load_proposals(); items[0]["decided"] -= (sk.REDRAW_DAYS + 1) * 86400; sk.save_proposals(items)
            self.assertIsNotNone(sk.proposal_add("vm1", "cv-mailer", "mail CVs", body)[0])
            pid3, _ = sk.proposal_add("vm1", "job-search", "search jobs", body)
            sk.proposal_decide(pid3, True)
            self.assertEqual(sk.proposal_history()["skills"], ["job-search"])
        finally:
            restore()

    def test_skill_stats_judge_only_with_enough_loads(self):
        """What a skill did: loads recorded with their turn (the guest route
        strips ?turn= from the name), the turns' outcomes decide the verdict —
        nothing is judged below SKILL_MIN_USES loads."""
        m = self.m
        st, sk = m._store, m._skills
        tmp, restore = self._skills_env()
        old_ip = m._guests.instance_by_ip
        try:
            now = int(time.time())
            json.dump([{"name": n, "description": "", "content": "x" * 400, "added": now - 40 * 86400}
                       for n in ("good", "bad", "rare", "idle")] +
                      [{"name": "fresh", "description": "", "content": "y", "added": now}], open(sk.SKILLS_FILE, "w"))
            vm = {"name": "vm1"}
            m._guests.instance_by_ip = lambda ip: vm if ip == "172.30.1.2" else None
            h = self._handler("/api/skills/good?turn=t0", "172.30.1.2"); h._do_GET()
            self.assertIn(b"x" * 400, h.wfile.getvalue())                        # the name without the query
            st.turn_start("vm1", "t0"); st.turn_end("vm1", "t0", outcome="ok")
            for i, (skill, outcome) in enumerate([("good", "ok")] * 5 + [("bad", "error")] * 4 + [("bad", "ok")] * 2
                                                 + [("rare", "ok")] * 2):
                turn = f"u{i}"
                st.turn_start("vm1", turn); st.turn_end("vm1", turn, outcome=outcome)
                st.skill_use_add("vm1", skill, turn)
            stats = sk.skill_stats(now + 5)
            self.assertEqual((stats["good"]["verdict"], stats["good"]["uses"], stats["good"]["ok"]), ("working", 6, 6))
            self.assertEqual((stats["bad"]["verdict"], stats["bad"]["failed"]), ("failing", 4))
            self.assertEqual(stats["rare"]["verdict"], "few")
            self.assertEqual(stats["idle"]["verdict"], "unused")
            self.assertEqual(stats["fresh"]["verdict"], "new")                   # just added: not "unused"
            json.dump([{"name": "legacy", "description": "", "content": "z"}], open(sk.SKILLS_FILE, "w"))
            self.assertEqual(sk.skill_stats(now)["legacy"]["verdict"], "new")     # predates the measurement
            self.assertEqual(sk.load_skills()[0]["added"], now)
            json.dump([{"name": n, "description": "", "content": "x" * 400, "added": now - 40 * 86400}
                       for n in ("good", "bad", "rare", "idle")], open(sk.SKILLS_FILE, "w"))
            self.assertEqual(stats["good"]["load_tokens"], 100)
            h = self._handler("/api/skills/good?turn=t9", "10.0.0.5"); h._do_GET()   # the operator's read: not counted
            self.assertEqual(sk.skill_stats(now + 5)["good"]["uses"], 6)
        finally:
            m._guests.instance_by_ip = old_ip
            restore()

    def test_fixed_context_cost_in_policy(self):
        """The trace start carries the agent's context sizes; the policy shows
        the fixed part per call and per day (calls × fixed)."""
        m = self.m
        st = m._store
        tmp, restore = self._skills_env()
        old_ip = m._guests.instance_by_ip
        try:
            vm = {"name": "vm1", "config": {}}
            m._guests.instance_by_ip = lambda ip: vm if ip == "172.30.1.2" else None
            body = json.dumps({"turn": "t1", "event": "start", "kind": "chat",
                               "ctx": {"system": 1200, "tools": 3000, "playbooks": 400, "conversation": 900, "bad": "x"}}).encode()
            h = self._post_handler("/api/trace", "172.30.1.2", body); h._do_POST()
            self.assertEqual(self._status(h), 204)
            for _ in range(3):
                st.usage_add("vm1", "m", 10, 1, 0.0, turn="t1")
            c = m._policy._context_cost("vm1")
            self.assertEqual((c["fixed"], c["conversation"], c["calls_24h"], c["per_day"]), (4600, 900, 3, 13800))
            self.assertNotIn("bad", c["parts"])
        finally:
            m._guests.instance_by_ip = old_ip
            restore()

    # ---- projects: one folder set, instances join with a role ----------------------
    def _projects_env(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-proj-")
        os.makedirs(os.path.join(tmp, "repo", "app"))
        insts = [{"name": n, "index": i, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4",
                  "mounts": [], "config": {}} for n, i in (("a", 3), ("b", 4), ("c", 5))]
        old = (m._browse.BROWSE_ROOTS, m._projects.PROJECTS_FILE, m._instances.load_instances,
               m._instances.is_running, m._mounts.apply_live, m._memfs.folder)
        m._browse.BROWSE_ROOTS = (tmp,)
        m._projects.PROJECTS_FILE = os.path.join(tmp, "projects.json")
        m._instances.load_instances = lambda: [dict(i) for i in insts]
        m._instances.is_running = lambda i: False
        m._memfs.folder = lambda name: ""

        def restore():
            (m._browse.BROWSE_ROOTS, m._projects.PROJECTS_FILE, m._instances.load_instances,
             m._instances.is_running, m._mounts.apply_live, m._memfs.folder) = old
        return tmp, insts, restore

    def test_projects_shared_host_folder_by_role(self):
        """A shared host project is one folder at /project/<name> in every
        member: writable for writers only, its own fsid block, a stable slot
        per membership, and only what the design allows is accepted."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        pj = m._projects
        try:
            src = {"type": "host", "path": os.path.join(tmp, "repo"), "subdir": "app"}
            r = pj.upsert({"name": "Dev", "source": src,
                           "members": {"a": {"role": "lead"}, "b": {"role": "writer"}, "c": "reader"}})
            self.assertTrue(r.get("ok"), r)
            specs = {i["name"]: [s for s in m._mounts.mount_specs(i) if s["guest"] == "/project/dev"] for i in insts}
            for n, ro, fsid in (("a", True, 8000 + 3 * 64), ("b", False, 8000 + 4 * 64), ("c", True, 8000 + 5 * 64)):
                self.assertEqual(len(specs[n]), 1, n)
                sp = specs[n][0]
                self.assertEqual((sp["ro"], sp["fsid"], sp["host"]), (ro, fsid, os.path.join(tmp, "repo", "app")), n)
                self.assertTrue(sp["sub"].endswith(f"/.fcmnt/{n}/p-dev"), sp["sub"])   # inside the guest's own subtree
            self.assertIn(f"/project/dev|{'rw'}", m._mounts.desired_lines(insts[1]))
            # a second project: the next free slot; re-saving the first keeps its slot
            self.assertTrue(pj.upsert({"name": "dev2", "source": {"type": "host", "path": tmp},
                                       "members": {"b": {"role": "writer"}}}).get("ok"))
            fs = {s["guest"]: s["fsid"] for s in m._mounts.mount_specs(insts[1])}
            self.assertEqual((fs["/project/dev"], fs["/project/dev2"]), (8000 + 256, 8000 + 257))
            pj.upsert({"name": "dev", "source": src, "members": {"b": {"role": "writer"}}})
            fs = {s["guest"]: s["fsid"] for s in m._mounts.mount_specs(insts[1])}
            self.assertEqual((fs["/project/dev"], fs["/project/dev2"]), (8000 + 256, 8000 + 257))
            self.assertFalse([s for s in m._mounts.mount_specs(insts[0]) if s["guest"].startswith("/project/")])  # a left
            # refused
            for body, why in (({"name": "x", "strategy": "overlay", "source": src}, "not built yet"),
                              ({"name": "x", "strategy": "magic", "source": src}, "strategy must be"),
                              ({"name": "x", "source": src, "members": {"zz": "writer"}}, "unknown instance"),
                              ({"name": "x", "source": src, "members": {"a": "boss"}}, "role must be"),
                              ({"name": "x", "source": src, "members": {"a": "lead", "b": "lead"}}, "one lead"),
                              ({"name": "x", "source": {**src, "subdir": "../.."}}, "inside the source"),
                              ({"name": "x", "source": {"type": "host", "path": "/srv"}}, "must be under"),
                              ({"name": "../x", "source": src}, "name:"),
                              ({"name": "x", "source": {"type": "nfs"}}, "source.type")):
                self.assertIn(why, pj.upsert(body).get("error", ""), body)
            self.assertNotIn("x", [p["name"] for p in pj.load()])
            self.assertEqual(pj.delete("dev").get("ok"), True)
            self.assertIn("unknown", pj.delete("dev").get("error", ""))
        finally:
            restore()

    def test_projects_apply_live_to_running_members(self):
        """Joining and leaving re-exports the folders of the RUNNING members,
        with their specs from before the change (so the old mount is released)."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        calls = []
        try:
            m._instances.is_running = lambda i: i["name"] != "c"
            m._mounts.apply_live = lambda inst, before: calls.append((inst["name"], [s["guest"] for s in before]))
            src = {"type": "host", "path": tmp}
            m._projects.upsert({"name": "p", "source": src, "members": {"a": "writer", "c": "reader"}})
            self.assertEqual(calls, [("a", [])])                          # c is stopped: it mounts at start
            calls.clear()
            m._projects.upsert({"name": "p", "source": src, "members": {"b": "writer"}})
            self.assertEqual(sorted(calls), [("a", ["/project/p"]), ("b", [])])
            calls.clear()
            m._projects.delete("p")
            self.assertEqual(calls, [("b", ["/project/p"])])
        finally:
            restore()

    def test_projects_katfs_share_and_write_only_for_writers(self):
        """A katfs project is the member's share when its config names none;
        lead and readers may read it but not write; one katfs share per instance."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        old_ip, old_answer = m._guests.instance_by_ip, m._katfs._katfs_answer
        try:
            r = m._projects.upsert({"name": "k", "source": {"type": "katfs", "share": "abc123"},
                                    "members": {"a": "lead", "b": "writer"}})
            self.assertTrue(r.get("ok"), r)
            a, b, c = insts
            self.assertEqual([m._katfs.katfs_share_for(i) for i in insts], ["abc123", "abc123", ""])
            self.assertEqual([m._katfs.katfs_writable_for(i) for i in insts], [False, True, True])
            own = {**a, "config": {"KATFS_SHARE": "mine"}}                # its own share wins, as before
            self.assertEqual((m._katfs.katfs_share_for(own), m._katfs.katfs_writable_for(own)), ("mine", True))
            self.assertIn("one katfs share", m._projects.upsert(
                {"name": "k2", "source": {"type": "katfs", "share": "def456"}, "members": {"b": "writer"}}).get("error", ""))
            self.assertEqual(m._mounts.mount_specs(b), [])                 # katfs is no NFS mount
            # the guest route: the lead is refused, the writer reaches the node
            sent = []
            m._katfs._katfs_answer = lambda h, op, share, path, *a: (sent.append((op, share)), (200, "text/plain", b"ok"))[1]
            m._guests.instance_by_ip = lambda ip: {"172.30.3.2": a, "172.30.4.2": b}.get(ip)
            h = self._post_handler("/api/katfs/write?path=x.txt", "172.30.3.2", b"hi"); h._do_POST()
            self.assertEqual(self._status(h), 403)
            h = self._post_handler("/api/katfs/write?path=x.txt", "172.30.4.2", b"hi"); h._do_POST()
            self.assertEqual((self._status(h), sent), (200, [("write", "abc123")]))
        finally:
            m._guests.instance_by_ip, m._katfs._katfs_answer = old_ip, old_answer
            restore()

    def _git_repo(self, tmp):
        import subprocess as sp
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "apps", "x"))
        g = lambda *a: sp.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout
        g("init", "-q", "-b", "main")
        g("config", "user.name", "op"); g("config", "user.email", "op@example.com")
        _wtext(os.path.join(repo, "apps", "x", "file.txt"), "one\n")
        _wtext(os.path.join(repo, "top.txt"), "top\n")
        g("add", "-A"); g("commit", "-q", "-m", "init")
        return repo, g

    def test_projects_worktree_branch_per_writer(self):
        """worktree: each writer its own folder (the subdir only) on its own
        branch; the lead sees the source and every writer read-only. A snapshot
        commits the folder — a planted symlink stays a link, a FIFO never
        reaches git; merge (clean / conflict / dirty / base not checked out),
        discard, leave and delete behave as documented."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        pj, wt = m._projects, m._projwt
        old = m._mounts.AGENT_ROOT, wt.STAGE_ROOT
        try:
            m._mounts.AGENT_ROOT, wt.STAGE_ROOT = os.path.join(tmp, "agent"), tmp
            repo, g = self._git_repo(tmp)
            body = {"name": "wp", "strategy": "worktree", "source": {"type": "host", "path": repo, "subdir": "apps/x"},
                    "members": {"a": "lead", "b": "writer", "c": "reader"}}
            r = pj.upsert(body)
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["project"]["base"], "main")                        # the checked-out branch
            d = wt.tree_dir("wp", "b")
            self.assertEqual(sorted(os.listdir(d)), ["file.txt"])                 # the subdir, no .git
            self.assertIn("proj/wp/b", g("branch", "--list", "proj/*"))
            specs = {i["name"]: {s["guest"]: s for s in m._mounts.mount_specs(i)} for i in insts}
            self.assertEqual((specs["b"]["/project/wp"]["host"], specs["b"]["/project/wp"]["ro"]), (d, False))
            self.assertEqual((specs["a"]["/project/wp"]["ro"], specs["a"]["/project/wp.members/b"]["host"],
                              specs["a"]["/project/wp.members/b"]["ro"]), (True, d, True))
            self.assertNotEqual(specs["a"]["/project/wp"]["fsid"], specs["a"]["/project/wp.members/b"]["fsid"])
            self.assertEqual(specs["c"]["/project/wp"]["host"], os.path.join(repo, "apps", "x"))
            self.assertNotIn("/project/wp.members/b", specs["c"])
            # the writer works: a change, a new file, a symlink out, a FIFO
            _wtext(os.path.join(d, "file.txt"), "one\ntwo\n")
            _wtext(os.path.join(d, "new.txt"), "new\n")
            os.symlink("/etc/passwd", os.path.join(d, "link"))
            os.mkfifo(os.path.join(d, "pipe"))
            st, code = pj.member_op("wp", "status", "b")
            self.assertEqual(code, 200, st)
            self.assertEqual((st["ahead"], st["behind"]), (1, 0))
            self.assertEqual(sorted(tuple(f) for f in st["files"]),
                             [("A", "apps/x/link"), ("A", "apps/x/new.txt"), ("M", "apps/x/file.txt")])
            self.assertEqual(g("cat-file", "-p", "proj/wp/b:apps/x/link"), "/etc/passwd")    # a link, not the file
            self.assertEqual(g("show", "proj/wp/b:top.txt"), "top\n")                         # outside the subdir: kept
            tip = g("rev-parse", "proj/wp/b").strip()
            pj.member_op("wp", "status", "b")
            self.assertEqual(g("rev-parse", "proj/wp/b").strip(), tip)            # no change -> no new commit
            patch, code = pj.member_op("wp", "diff", "b")
            self.assertIn("+two", patch)
            # merge: main is checked out and clean -> git merge --no-ff
            r, code = pj.member_op("wp", "merge", "b")
            self.assertEqual(code, 200, r)
            self.assertEqual(len(g("log", "-1", "--format=%P", "main").split()), 2)
            self.assertTrue(os.path.exists(os.path.join(repo, "apps", "x", "new.txt")))
            # conflict: nothing changes
            _wtext(os.path.join(d, "file.txt"), "B\n")
            _wtext(os.path.join(repo, "apps", "x", "file.txt"), "M\n")
            g("commit", "-qam", "op edits")
            head = g("rev-parse", "main").strip()
            r, code = pj.member_op("wp", "merge", "b")
            self.assertEqual((code, r.get("conflicts")), (409, ["apps/x/file.txt"]), r)
            self.assertEqual((g("rev-parse", "main").strip(), g("status", "--porcelain")), (head, ""))
            # dirty checkout: refused
            _wtext(os.path.join(repo, "top.txt"), "dirty\n")
            r, code = pj.member_op("wp", "merge", "b")
            self.assertIn("uncommitted", r.get("error", ""))
            g("checkout", "-q", "--", "top.txt")
            # discard: branch back to main, folder refilled in place
            ino = os.stat(d).st_ino
            r, code = pj.member_op("wp", "discard", "b")
            self.assertEqual(code, 200, r)
            self.assertEqual(g("rev-parse", "proj/wp/b").strip(), head)
            self.assertEqual((sorted(os.listdir(d)), open(os.path.join(d, "file.txt")).read(), os.stat(d).st_ino),
                             (["file.txt", "link", "new.txt"], "M\n", ino))
            self.assertTrue(os.path.islink(os.path.join(d, "link")))              # merged earlier: back as a link
            # base not checked out: merge-tree + commit-tree, the operator's checkout untouched
            g("checkout", "-q", "-b", "other")
            _wtext(os.path.join(d, "late.txt"), "late\n")
            r, code = pj.member_op("wp", "merge", "b")
            self.assertEqual(code, 200, r)
            self.assertEqual(g("show", "main:apps/x/late.txt"), "late\n")
            self.assertEqual((g("symbolic-ref", "--short", "HEAD").strip(), os.path.exists(os.path.join(repo, "apps", "x", "late.txt"))),
                             ("other", False))
            # review is for writers of worktree projects only
            self.assertEqual(pj.member_op("wp", "merge", "c")[1], 404)
            self.assertEqual(pj.member_op("nope", "status", "b")[1], 404)
            # b leaves: folder gone, branch kept; delete: the project folder gone
            self.assertTrue(pj.upsert({**body, "base": "main", "members": {"a": "lead", "b": "reader"}}).get("ok"))
            self.assertFalse(os.path.exists(d))
            self.assertIn("proj/wp/b", g("branch", "--list", "proj/*"))
            pj.delete("wp")
            self.assertFalse(os.path.exists(os.path.join(m._mounts.AGENT_ROOT, ".projects", "wp")))
            # refused
            os.makedirs(os.path.join(repo, "untracked"))                          # on disk, not in the branch
            for b2, why in (({**body, "source": {"type": "host", "path": os.path.join(repo, "apps")}}, "not the top"),
                            ({**body, "base": "nope"}, "does not exist"),
                            ({**body, "source": {"type": "host", "path": repo, "subdir": "untracked"}}, "has no folder"),
                            ({**body, "source": {"type": "katfs", "share": "abc"}}, "katfs works with shared only"),
                            ({**body, "strategy": "overlay"}, "not built yet")):
                self.assertIn(why, pj.upsert(b2).get("error", ""), b2)
        finally:
            m._mounts.AGENT_ROOT, wt.STAGE_ROOT = old
            restore()

    def test_projects_lead_reviews_over_guest_routes(self):
        """A lead VM sees its writers' changes and diffs; anyone else is refused;
        it merges only when the project allows it."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        old = m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._guests.instance_by_ip
        try:
            m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT = os.path.join(tmp, "agent"), tmp
            repo, g = self._git_repo(tmp)
            body = {"name": "lp", "strategy": "worktree", "source": {"type": "host", "path": repo},
                    "members": {"a": "lead", "b": "writer"}}
            self.assertTrue(m._projects.upsert(body).get("ok"))
            _wtext(os.path.join(m._projwt.tree_dir("lp", "b"), "x.txt"), "x\n")
            ips = {"172.30.3.2": insts[0], "172.30.4.2": insts[1]}
            m._guests.instance_by_ip = lambda ip: ips.get(ip)
            h = self._handler("/api/project/status", "172.30.3.2"); h._do_GET()
            d = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(d["projects"][0]["members"]["b"]["files"], [["A", "x.txt"]])
            h = self._handler("/api/project/status", "172.30.4.2"); h._do_GET()      # a writer leads nothing
            self.assertEqual(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["projects"], [])
            h = self._handler("/api/project/diff?project=lp&member=b", "172.30.3.2"); h._do_GET()
            self.assertIn(b"+x", h.wfile.getvalue())
            h = self._handler("/api/project/diff?project=lp&member=b", "172.30.4.2"); h._do_GET()
            self.assertEqual(self._status(h), 403)
            mb = json.dumps({"project": "lp", "member": "b"}).encode()
            h = self._post_handler("/api/project/merge", "172.30.3.2", mb); h._do_POST()
            self.assertEqual(self._status(h), 403)                                  # not allowed in this project
            self.assertTrue(m._projects.upsert({**body, "lead_may_merge": True}).get("ok"))
            h = self._post_handler("/api/project/merge", "172.30.3.2", mb); h._do_POST()
            self.assertEqual(self._status(h), 200, h.wfile.getvalue())
            self.assertEqual(g("show", "main:x.txt"), "x\n")
            # the admin side: status and diff over /api/projects/<p>/…
            m._guests.instance_by_ip = lambda ip: None
            h = self._handler("/api/projects/lp/status", "127.0.0.1"); h._do_GET()
            self.assertIn(b'"b"', h.wfile.getvalue())
        finally:
            m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._guests.instance_by_ip = old
            restore()

    def test_projects_admin_routes(self):
        """The Projects tab's API is admin-only and answers errors as 400/404."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        try:
            self.assertTrue(m._routes.ROUTER.resolve("GET", "/api/projects")[1])
            self.assertTrue(m._routes.ROUTER.resolve("POST", "/api/projects")[1])
            self.assertTrue(m._routes.ROUTER.resolve("POST", "/api/projects/p/delete")[1])
        finally:
            restore()

    # ---- squads: a project's members talk, addressed members are woken ---------------
    def test_projects_sync_brings_merged_work_to_the_other_writers(self):
        """project_sync: the base merged INTO a writer's branch, its own unmerged
        edit kept, the folder refilled in place; up to date is a no-op; a conflict
        changes nothing and names the file. A merge syncs every OTHER writer (a
        note each, nobody woken), a conflict wakes that writer and the lead. The
        guest route serves writers of worktree projects only."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        pj, wt, sq = m._projects, m._projwt, m._squads
        tmp = os.path.dirname(sq.SQUAD_DB)
        old = m._mounts.AGENT_ROOT, wt.STAGE_ROOT, pj._spawn
        try:
            m._mounts.AGENT_ROOT, wt.STAGE_ROOT = os.path.join(tmp, "agent"), tmp
            pj._spawn = lambda fn, *a: fn(*a)                                     # the thread, inline
            repo, g = self._git_repo(tmp)
            self.assertTrue(pj.upsert({"name": "wp", "strategy": "worktree",
                                       "source": {"type": "host", "path": repo, "subdir": "apps/x"},
                                       "members": {"a": "lead", "b": "writer", "c": "writer", "d": "reader"}}).get("ok"))
            pj.set_squad("wp", sq.normalize_settings({"enabled": True, "approved": True})[0])
            db, dc = wt.tree_dir("wp", "b"), wt.tree_dir("wp", "c")
            def rd(f):
                with open(os.path.join(dc, f)) as fh:
                    return fh.read()
            # up to date: nothing happens
            tip = g("rev-parse", "proj/wp/c").strip()
            r, st = pj.member_op("wp", "sync", "c")
            self.assertEqual((st, r["commit"], r["note"].startswith("up to date")), (200, tip, True), r)
            # b's work is merged: c gets it and keeps its own uncommitted edit; b is not synced
            _wtext(os.path.join(dc, "mine.txt"), "c\n")
            _wtext(os.path.join(db, "feat.txt"), "b\n")
            ino, btip = os.stat(dc).st_ino, wt.snapshot(pj.get("wp"), "b")
            r, st = pj.member_op("wp", "merge", "b")
            self.assertEqual(st, 200, r)
            self.assertEqual((rd("feat.txt"), rd("mine.txt"), os.stat(dc).st_ino), ("b\n", "c\n", ino))
            self.assertEqual(len(g("log", "-1", "--format=%P", "proj/wp/c").split()), 2)   # c's work + the base
            self.assertNotIn("mine.txt", g("ls-tree", "-r", "--name-only", "main"))       # the base untouched
            self.assertEqual(g("rev-parse", "proj/wp/b").strip(), btip)
            texts = [x["text"] for x in sq.read("wp")]
            self.assertTrue(any(t.startswith("synced c to main@") for t in texts), texts)
            self.assertFalse(any("synced b" in t for t in texts), texts)
            self.assertEqual(woke, [])                                                    # a note wakes nobody
            # conflict: c's folder and branch stay as they were, the file is named, c and the lead are woken
            _wtext(os.path.join(dc, "file.txt"), "C\n")
            _wtext(os.path.join(db, "file.txt"), "B\n")
            ctip = wt.snapshot(pj.get("wp"), "c")
            r, st = pj.member_op("wp", "merge", "b")
            self.assertEqual(st, 200, r)
            self.assertEqual((g("rev-parse", "proj/wp/c").strip(), rd("file.txt")), (ctip, "C\n"))
            last = sq.read("wp")[-1]
            self.assertIn("conflict in apps/x/file.txt", last["text"])
            self.assertEqual(sorted(w[0] for w in woke), ["a", "c"])
            r, st = pj.member_op("wp", "sync", "c")
            self.assertEqual((st, r["conflicts"], g("rev-parse", "proj/wp/c").strip()), (409, ["apps/x/file.txt"], ctip))
            # the guest route: lead and reader refused, the writer syncs itself
            for ip in ("172.30.3.2", "172.30.6.2"):
                h = self._post_handler("/api/project/sync", ip, b"{}"); h._do_POST()
                self.assertEqual(self._status(h), 403, ip)
                self.assertIn(b"no writer", h.wfile.getvalue())
            h = self._post_handler("/api/project/sync", "172.30.5.2", b"{}"); h._do_POST()
            self.assertEqual(self._status(h), 409, h.wfile.getvalue())
            self.assertIn(b"apps/x/file.txt", h.wfile.getvalue())
            _wtext(os.path.join(dc, "file.txt"), "B\n")                                  # c resolves
            h = self._post_handler("/api/project/sync", "172.30.5.2", b"{}"); h._do_POST()
            self.assertEqual(self._status(h), 200, h.wfile.getvalue())
            self.assertEqual((rd("file.txt"), rd("mine.txt")), ("B\n", "c\n"))
        finally:
            m._mounts.AGENT_ROOT, wt.STAGE_ROOT, pj._spawn = old
            undo()

    def _squad_env(self):
        """Project 'sq' with members a (lead), b (writer), d (reader); c is no
        member. Guests by IP 172.30.<index>.2; wakes recorded, not queued."""
        m = self.m
        tmp, insts, restore = self._projects_env()
        self._sq_insts = insts
        insts.append({"name": "d", "index": 6, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4",
                      "mounts": [], "config": {}})
        old = (m._squads.SQUAD_DB, m._squads.wake, m._guests.instance_by_ip, m._audit.AUDIT_DIR,
               m._llmproxy.used_today, m._store.load_tasks, m._squadrec.REC_FILE, m._settings.SITE,
               m._repoprofile.CACHE_FILE)
        m._repoprofile.CACHE_FILE = os.path.join(tmp, "repoprofile.json")
        m._squads.SQUAD_DB = os.path.join(tmp, "squad.db")
        m._squadrec.REC_FILE = os.path.join(tmp, "squadrec.json")
        m._settings.SITE = {**m._settings.SITE, "SQUADS_DIR": os.path.join(tmp, "squads")}
        m._audit.AUDIT_DIR = os.path.join(tmp, "audit")
        woke, tasks = [], []
        m._squads.wake = lambda member, msg, project, depth, mid, **kw: woke.append((member, msg, depth)) or f"t{len(woke)}"
        m._guests.instance_by_ip = lambda ip: next((i for i in insts if ip == f"172.30.{i['index']}.2"), None)
        m._llmproxy.used_today = lambda name: 0
        m._store.load_tasks = lambda: tasks
        src = {"type": "host", "path": os.path.join(tmp, "repo")}
        self.assertTrue(m._projects.upsert({"name": "sq", "source": src,
                                            "members": {"a": "lead", "b": "writer", "d": "reader"}}).get("ok"))
        m._projects.set_squad("sq", m._squads.normalize_settings({"enabled": True, "approved": True})[0])

        def undo():
            (m._squads.SQUAD_DB, m._squads.wake, m._guests.instance_by_ip, m._audit.AUDIT_DIR,
             m._llmproxy.used_today, m._store.load_tasks, m._squadrec.REC_FILE, m._settings.SITE,
             m._repoprofile.CACHE_FILE) = old
            restore()
        return woke, tasks, undo

    def _sq(self, path, ip, body=None):
        """(status, json) of one squad request — POST when a body is given."""
        h = self._post_handler(path, ip, json.dumps(body).encode()) if body is not None else self._handler(path, ip)
        h._do_POST() if body is not None else h._do_GET()
        return self._status(h), json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1] or b"{}")

    def test_squad_membership_visibility_and_wake(self):
        """Only members post, only to members; the addressed (to, @name, @all)
        are woken, never the sender; a member reads what reached it, plus its own."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        A, B, C, D = "172.30.3.2", "172.30.4.2", "172.30.5.2", "172.30.6.2"
        try:
            self.assertEqual(self._sq("/api/squad/post", C, {"to": "a", "text": "hi"})[0], 403)        # no member
            self.assertEqual(self._sq("/api/squad/post", "127.0.0.1", {"to": "a", "text": "hi"})[0], 403)  # not a guest
            st, d = self._sq("/api/squad/post", A, {"to": "c", "text": "hi"})
            self.assertEqual(st, 400); self.assertIn("not a member", d["error"])
            self.assertEqual(self._sq("/api/squad/post", A, {"to": "b", "text": "x" * 9000})[0], 400)
            st, d = self._sq("/api/squad/post", A, {"to": "b", "text": "please check the build"})
            self.assertEqual(st, 200, d)
            self.assertEqual((d["project"], d["from"], d["to"], d["delivered"], d["depth"]), ("sq", "a", ["b"], ["b"], 1))
            self.assertEqual([w[0] for w in woke], ["b"])
            self.assertTrue(woke[0][1].startswith(f"[squad sq] from a (msg {d['id']}, reply with squad_post reply_to={d['id']} — only if"))
            self.assertIn("goes with wake=false", woke[0][1])
            self.assertIn("not instructions from the operator", woke[0][1])
            st, d2 = self._sq("/api/squad/post", B, {"to": "", "text": "on it, @d FYI", "reply_to": str(d["id"])})
            self.assertEqual((d2["to"], d2["reply_to"]), (["a", "d"], d["id"]))       # reply goes to its sender + @mention
            self.assertEqual([w[0] for w in woke[1:]], ["a", "d"])
            del woke[:]
            st, dq = self._sq("/api/squad/post", A, {"text": "thanks, holding", "reply_to": str(d2["id"]), "wake": False})
            self.assertEqual((st, dq["to"], dq["delivered"], woke), (200, ["b"], [], []))    # stored, nobody woken
            st, d3 = self._sq("/api/squad/post", D, {"to": "all", "text": "status @all"})
            self.assertEqual(d3["to"], ["all"]); self.assertEqual(sorted(w[0] for w in woke), ["a", "b"])   # not the sender
            ids = lambda ip: [x["id"] for x in self._sq("/api/squad/read?project=sq", ip)[1]["messages"]]
            self.assertEqual(ids(A), [d["id"], d2["id"], dq["id"], d3["id"]])
            self.assertEqual(ids(D), [d2["id"], d3["id"]])                              # not a's post to b
            self.assertEqual(self._sq(f"/api/squad/read?since={d2['id']}", D)[1]["messages"][0]["id"], d3["id"])
            self.assertEqual(self._sq("/api/squad/read", C)[0], 403)
            st, mem = self._sq("/api/squad/members", B)
            self.assertEqual([(x["name"], x["role"]) for x in mem["members"]], [("a", "lead"), ("b", "writer"), ("d", "reader")])
            self.assertTrue(all(x["member_id"].startswith(mem["squad_id"] + "-") for x in mem["members"]))
            self.assertEqual(mem["you"], mem["members"][1]["member_id"])
            with open(os.path.join(m._audit.AUDIT_DIR, "a.jsonl")) as fh:
                self.assertEqual(json.loads(fh.readlines()[-1])["tool"], "squad_post")
            # a tool allowlist without the squad tools closes the routes
            self._sq_insts[0]["config"] = {"AGENT_TOOLS": "bash"}
            self.assertEqual(self._sq("/api/squad/post", A, {"to": "b", "text": "hi"})[0], 403)
        finally:
            undo()

    def test_squad_wake_is_a_task_whose_depth_a_post_inherits(self):
        """The wake is a one-off task in the member's lane carrying the depth;
        while it runs, the member's posts are one level deeper."""
        m = self.m
        old = m._store.TASKS_FILE
        m._store.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-sqt-"), "tasks.json")
        try:
            tid = m._squads.wake("b", "[squad sq] hi", "sq", 2, 7)
            t = m._store.load_tasks()[0]
            self.assertEqual((t["id"], t["instance"], t["status"], t["squad"]), (tid, "b", "pending", {"project": "sq", "depth": 2, "msg": 7}))
            self.assertEqual(m._squads.turn_depth("b"), 0)                              # queued, not running yet
            m._store.with_tasks(lambda ts: (ts[0].update(status="running"), (True, None))[1])
            self.assertEqual((m._squads.turn_depth("b"), m._squads.turn_depth("a")), (2, 0))
        finally:
            m._store.TASKS_FILE = old

    def test_squad_member_post_without_addressee_goes_to_the_operator(self):
        """A member's post with no `to` (or to=user/admin) goes to the person who
        last wrote in the channel and wakes nobody; the operator must still name
        someone. A lead is created with a raised step limit."""
        woke, tasks, undo = self._squad_env()
        m, A = self.m, "172.30.3.2"
        try:
            m._squads.post(m._projects.get("sq"), "user:ulrich", ["a"], "goal")
            n = len(woke)
            for to in ("", "admin", "user"):
                st, d = self._sq("/api/squad/post", A, {"to": to, "text": "plan is up"})
                self.assertEqual((st, d["to"], d["delivered"]), (200, ["user:ulrich"], []))
            self.assertEqual(len(woke), n)
        finally:
            undo()
        self.assertEqual(m._squadgate.LEAD_MAX_STEPS, 40)

    def test_squad_restart_members_idle_now_busy_after_turn(self):
        """'Restart members': an idle running member restarts at once, a busy one
        right after its turn, a stopped one is left to start fresh; admin only."""
        woke, tasks, undo = self._squad_env()
        m, L = self.m, "127.0.0.1"
        calls = []
        old = (m._instances.is_running, m._vm.stop, m._vm.start, m._tasks.threading.Thread)
        m._instances.is_running = lambda i: i["name"] in ("a", "b")
        m._vm.stop = lambda i: calls.append(("stop", i["name"]))
        m._vm.start = lambda i: calls.append(("start", i["name"]))
        class T:                                                       # run the restart inline
            def __init__(self, target, daemon=True, name=""): self.t = target
            def start(self): self.t()
        m._tasks.threading.Thread = T
        tasks.append({"id": "t1", "instance": "b", "status": "running"})
        try:
            st, d = self._sq("/api/projects/sq/squad/restart", L, {})
            self.assertEqual((st, d["members"]), (200, {"a": "restarting", "b": "after its turn",
                                                        "d": "stopped (starts fresh on its next wake)"}))
            self.assertEqual(calls, [("stop", "a"), ("start", "a")])
            self.assertEqual(self._sq("/api/projects/sq/squad/restart", "172.30.3.2", {})[0], 403)
            m._tasks._restart_now("b")                                  # what the worker does after b's turn
            self.assertEqual(calls[-2:], [("stop", "b"), ("start", "b")])
            self.assertNotIn("b", m._tasks._restart_after)
        finally:
            m._instances.is_running, m._vm.stop, m._vm.start, m._tasks.threading.Thread = old
            undo()

    def test_squad_idle_sweep_stops_idle_members_keeps_layer(self):
        """An idle squad member with a persistent write layer stops after
        SQUAD_IDLE_STOP s; busy (task or open turn), fresh, not persistent, not in
        an enabled squad or switched off (0): left running."""
        woke, tasks, undo = self._squad_env()
        m, now, calls = self.m, 100000, []
        d = tempfile.mkdtemp(prefix="e2e-idle-")
        insts = [{"name": n, "persist_disk": n != "d", "template": "claude"} for n in ("a", "b", "d", "x")]
        turns = {"a": [{"ts_start": now - 2000, "ts_end": now - 1900}], "b": [{"ts_start": now - 60, "ts_end": None}]}
        old = (m._instances.load_instances, m._instances.is_running, m._instances.pidfile, m._vm.stop,
               m._store.turns_read, m._tasks.SQUAD_IDLE_STOP)
        m._instances.load_instances = lambda: insts
        m._instances.is_running = lambda i: True
        m._instances.pidfile = lambda i: os.path.join(d, i["name"] + ".pid")
        m._vm.stop = lambda i: calls.append(i["name"])
        m._store.turns_read = lambda n, limit=50, since=0: turns.get(n, [])
        for n in ("a", "b", "d", "x"):
            _wtext(os.path.join(d, n + ".pid"), "1")
            os.utime(os.path.join(d, n + ".pid"), (now - 5000, now - 5000))
        try:
            m._tasks.SQUAD_IDLE_STOP = 900
            self.assertEqual(m._tasks.squad_idle_sweep(now=now), ["a"])     # b: open turn, d: no layer, x: no squad
            tasks.append({"id": "t1", "instance": "a", "status": "pending"})
            self.assertEqual(m._tasks.squad_idle_sweep(now=now), [])        # a wake is queued for a
            del tasks[:]
            os.utime(os.path.join(d, "a.pid"), (now - 100, now - 100))
            self.assertEqual(m._tasks.squad_idle_sweep(now=now), [])        # just started
            m._tasks.SQUAD_IDLE_STOP = 0
            self.assertEqual(m._tasks.squad_idle_sweep(now=now + 9999), [])
            self.assertEqual(calls, ["a"])
        finally:
            (m._instances.load_instances, m._instances.is_running, m._instances.pidfile, m._vm.stop,
             m._store.turns_read, m._tasks.SQUAD_IDLE_STOP) = old
            shutil.rmtree(d, ignore_errors=True)
            undo()

    def test_claude_guest_image_has_what_its_init_calls(self):
        """guest-init.sh fetches its mount list and the skills block with curl:
        the image must install it (live 2026-10-10: Claude members had no
        /project mount and no skills because curl was missing)."""
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude")
        if not os.path.exists(os.path.join(root, "Dockerfile.rootfs")):
            self.skipTest("agents/claude not next to the manager")
        init = open(os.path.join(root, "guest-init.sh")).read()
        docker = open(os.path.join(root, "Dockerfile.rootfs")).read()
        if re.search(r"^[^#]*\bcurl\s+-", init, re.M):
            self.assertRegex(docker, r"apt-get install[^\n]*\n?[^\n]*\bcurl\b")

    def test_squad_catalog_fits_the_goal_and_security_needs_the_squad_switch(self):
        """The lead's catalog lists the standard skills plus at most
        CATALOG_SKILLS matching the repo/goal, best first; category security and
        the security personas only with the squad's 'Security skills' (live: a
        Claude architect was cut off by its vendor's cyber safeguards)."""
        m = self.m
        g = m._squadgate
        old = (m._skills.meta, m._personas.load_personas, g.profile)
        cat = lambda n, d="": m._skills.guess_category(n, d)
        raw = ([("durable-objects", "Cloudflare Durable Objects", False), ("ponytail", "lean code", True),
                ("social-engineering-jailbreak", "x", False), ("stack-smashing", "exploit memory", False),
                ("security-audit", "security review", True)]
               + [(f"misc-{i}", "cloudflare helper", False) for i in range(30)])
        m._skills.meta = lambda: [{"name": n, "description": d, "default": dflt, "category": cat(n, d)} for n, d, dflt in raw]
        m._personas.load_personas = lambda: [{"name": "coder", "prompt": "You code."}, {"name": "osint", "prompt": "Intel."}]
        g.profile = lambda proj: None
        try:
            c = g.catalog(None, "a UI for the Cloudflare Durable Objects CLI")
            names = [k["name"] for k in c["skills"]]
            self.assertEqual(names[0], "ponytail")                                   # standard first
            self.assertEqual(names[1], "durable-objects")                            # then the best match
            self.assertLessEqual(len(names), g.CATALOG_SKILLS)
            for bad in ("social-engineering-jailbreak", "stack-smashing", "security-audit"):
                self.assertNotIn(bad, names)
            self.assertEqual([p["name"] for p in c["personas"]], ["coder"])
            on = {"name": "sq", "squad": {"enabled": True, "security_skills": True}}
            c2 = g.catalog(on, "security audit of the CLI")
            self.assertIn("security-audit", [k["name"] for k in c2["skills"]])
            self.assertEqual([p["name"] for p in c2["personas"]], ["coder", "osint"])
        finally:
            m._skills.meta, m._personas.load_personas, g.profile = old

    def test_task_whose_answer_is_an_api_error_counts_as_failed(self):
        m = self.m
        self.assertTrue(m._tasks._api_failure("API Error: Opus 4.8's safeguards flagged this message for a cybersecurity topic."))
        self.assertTrue(m._tasks._api_failure("Failed to authenticate. OAuth session expired"))
        self.assertFalse(m._tasks._api_failure("Done. Note: the API error handling now retries."))

    def test_skill_categories_and_security_opt_in(self):
        """Skills carry a category (guessed, a stored one wins, the startup
        migration fills only missing ones and backs up); security skills reach
        a guest only with SECURITY_SKILLS=1 or its squad's 'Security skills':
        hidden in /api/skills (also the standard block), refused on load, in
        SKILLS validation and the squad catalog."""
        m = self.m
        sk = m._skills
        g = sk.guess_category
        for n, c in (("security-audit", "security"), ("security-audit-hunting", "security"), ("stack-smashing", "security"),
                     ("social-engineering-jailbreak", "security"), ("redteaming-code", "security"), ("hacker-mindset", "security"),
                     ("page-cache-lpe-mitigation", "security"), ("daily-job-search-nrw", "personal"), ("docker", "devops"),
                     ("codebase-design", "design"), ("ponytail", "coding"), ("code-reviewer", "coding"), ("postgres-expert", "data")):
            self.assertEqual(g(n, ""), c, n)
        self.assertEqual(g("helper", "teaches a jailbreak"), "security")             # unambiguous description term
        self.assertEqual(g("reviewer", "a review that also checks security"), "coding")
        tmp = tempfile.mkdtemp(prefix="e2e-skcat-")
        old = (sk.SKILLS_FILE, m._guests.instance_by_ip, m._projects.memberships)
        sk.SKILLS_FILE = os.path.join(tmp, "skills.json")
        _wtext(sk.SKILLS_FILE, json.dumps([
            {"name": "ponytail", "description": "lean", "content": "P", "default": True},
            {"name": "security-audit", "description": "audit", "content": "S", "default": True},
            {"name": "stack-smashing", "description": "x", "content": "X"},
            {"name": "docker", "description": "containers", "content": "D", "category": "data"}]))   # stored wins
        insts = {"172.30.3.2": {"name": "plain", "config": {}}, "172.30.4.2": {"name": "sec", "config": {"SECURITY_SKILLS": "1"}}}
        m._guests.instance_by_ip = lambda ip: insts.get(ip)
        m._projects.memberships = lambda name: []
        def get(path, ip):
            h = self._handler(path, ip); h._do_GET()
            body = h.wfile.getvalue().split(b"\r\n\r\n", 1)[1]
            return self._status(h), body
        try:
            self.assertEqual(sk.category({"name": "docker", "category": "data"}), "data")
            names = lambda ip, q="meta=1": [x["name"] for x in json.loads(get("/api/skills?" + q, ip)[1])]
            self.assertEqual(names("172.30.3.2"), ["ponytail", "docker"])
            self.assertEqual(names("172.30.4.2"), ["ponytail", "security-audit", "stack-smashing", "docker"])
            self.assertEqual(names("172.30.3.2", "default=1"), ["ponytail"])                 # standard set: no security
            self.assertEqual(names("172.30.4.2", "default=1"), ["ponytail", "security-audit"])
            self.assertEqual(names("172.30.4.2", "meta=1&category=security"), ["security-audit", "stack-smashing"])
            self.assertEqual(get("/api/skills/stack-smashing", "172.30.3.2")[0], 403)
            st, body = get("/api/skills/stack-smashing", "172.30.4.2")
            self.assertEqual((st, body), (200, b"X"))
            self.assertIn("SECURITY_SKILLS", sk.names_error("docker,stack-smashing", False))
            self.assertEqual(sk.names_error("docker,stack-smashing", True), "")
            # squad switch
            m._projects.memberships = lambda name: [({"name": "sq", "squad": {"enabled": True, "security_skills": True}}, "writer")]
            self.assertTrue(sk.security_allowed({"name": "plain", "config": {}}))
            m._projects.memberships = lambda name: []
            # migration: only missing categories, with a backup
            self.assertEqual(sk.migrate_categories(), 3)
            self.assertTrue(os.path.exists(sk.SKILLS_FILE + ".bak-categories"))
            stored = {x["name"]: x["category"] for x in json.load(open(sk.SKILLS_FILE))}
            self.assertEqual(stored, {"ponytail": "coding", "security-audit": "security", "stack-smashing": "security", "docker": "data"})
            self.assertEqual(sk.migrate_categories(), 0)
            self.assertIn("must be one of", sk.set_category("docker", "nonsense"))
            sk.set_category("docker", "devops")
            self.assertEqual(sk.category(next(x for x in sk.load_skills() if x["name"] == "docker")), "devops")
        finally:
            sk.SKILLS_FILE, m._guests.instance_by_ip, m._projects.memberships = old

    def test_squad_rate_limited_wakes_are_queued_and_delivered_later(self):
        """A wake held back by the hourly limit is stored as queued (not lost),
        shows in queue_info, and the idle worker delivers it in order once the
        limit allows; another reason ends the wait. Old default 20 -> 60."""
        woke, tasks, undo = self._squad_env()
        m = self.m
        sq = m._squads
        p = lambda: m._projects.get("sq")
        try:
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 1})
            a, _ = sq.post(p(), "a", ["b"], "one")
            b, _ = sq.post(p(), "a", ["b"], "two")
            c, _ = sq.post(p(), "a", ["d"], "three")
            self.assertEqual((a["queued"], b["queued"], c["queued"]), (False, True, True))
            self.assertTrue(b["blocked"].startswith("rate:"))
            n0 = len(woke)
            op, _ = sq.post(p(), "user:ulrich", ["d"], "now")                    # a person is not held by the rate
            self.assertEqual((op["queued"], op["blocked"], [w[0] for w in woke[n0:]]), (False, "", ["d"]))
            self.assertEqual(sq.queue_info("sq")["count"], 2)
            self.assertEqual(sq.deliver_queued(), 0)                              # still full
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 10})
            n0 = len(woke)
            self.assertEqual(sq.deliver_queued(), 2)
            self.assertEqual([w[0] for w in woke[n0:]], ["b", "d"])               # in order
            got = {x["id"]: x for x in sq.read("sq")}
            self.assertEqual((got[b["id"]]["queued"], got[b["id"]]["blocked"], got[b["id"]]["delivered"]), (False, "", ["b"]))
            self.assertEqual(sq.queue_info("sq")["count"], 0)
            # another reason ends the wait
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 0})
            d, _ = sq.post(p(), "a", ["b"], "four")
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 10, "enabled": False})
            self.assertEqual(sq.deliver_queued(), 0)
            self.assertEqual({x["id"]: x for x in sq.read("sq")}[d["id"]]["blocked"], "squad disabled")
            # migration of the old default
            m._projects.set_squad("sq", {**sq.settings(p()), "enabled": True, "max_wakes_per_hour": 20})
            self.assertEqual(sq.migrate_wakes_default(), 1)
            self.assertEqual(sq.settings(p())["max_wakes_per_hour"], 60)
            # migration of the old reply depth: exactly 3 moves to 8, a chosen 5 stays
            m._projects.set_squad("sq", {**sq.settings(p()), "max_depth": 3})
            self.assertEqual((sq.migrate_depth_default(), sq.settings(p())["max_depth"]), (1, 8))
            m._projects.set_squad("sq", {**sq.settings(p()), "max_depth": 5})
            self.assertEqual((sq.migrate_depth_default(), sq.settings(p())["max_depth"]), (0, 5))
        finally:
            undo()

    def test_dashboard_session_lasts_like_the_web_login(self):
        """The repowise dashboard (INDEX_HOST) kept asking for a login after 12 h;
        it lasts as long as the web session, the IdP re-check still revokes it."""
        self.assertEqual(self.m._sessions.TTL["dash"], self.m._sessions.TTL["web"])

    def test_squad_queued_member_replies_expire(self):
        """A member's throttled reply is dropped once its addressee posted since
        or after QUEUE_TTL; a queued message to someone else still goes out (live
        2026-10-13: late acks woke members and fed an ack loop)."""
        woke, tasks, undo = self._squad_env()
        m = self.m
        sq = m._squads
        p = lambda: m._projects.get("sq")
        try:
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 1})
            x, _ = sq.post(p(), "a", ["b"], "go")
            r1, _ = sq.post(p(), "b", [], "ok, holding", reply_to=x["id"])
            op, _ = sq.post(p(), "a", ["d"], "please look")
            self.assertEqual((r1["to"], r1["queued"], op["queued"]), (["a"], True, True))
            sq.post(p(), "a", ["user"], "status: waiting")                     # a posted since r1
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 10})
            n0 = len(woke)
            self.assertEqual(sq.deliver_queued(), 1)
            self.assertEqual([w[0] for w in woke[n0:]], ["d"])
            got = {y["id"]: y for y in sq.read("sq")}
            self.assertTrue(got[r1["id"]]["blocked"].startswith("expired"))
            self.assertFalse(got[r1["id"]]["queued"])
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 0})
            r2, _ = sq.post(p(), "b", [], "still holding", reply_to=x["id"])
            m._projects.set_squad("sq", {**sq.settings(p()), "max_wakes_per_hour": 10})
            self.assertEqual(sq.deliver_queued(now=int(time.time()) + sq.QUEUE_TTL + 5), 0)
            self.assertTrue({y["id"]: y for y in sq.read("sq")}[r2["id"]]["blocked"].startswith("expired"))
        finally:
            undo()

    def test_squad_code_goes_through_git_not_the_channel(self):
        """A diff or a long code block in a member's post is refused with the git
        hint; short snippets and the operator's posts pass; the frame says work
        happens on the ticket (live 2026-10-13: pasted patches were cut off)."""
        woke, tasks, undo = self._squad_env()
        m, B = self.m, "172.30.4.2"
        block = "```go\n" + "x := 1\n" * 31 + "```"
        try:
            st, d = self._sq("/api/squad/post", B, {"to": "a", "text": "patch:\n" + block})
            self.assertEqual(st, 400); self.assertIn("through git", d["error"])
            st, d = self._sq("/api/squad/post", B, {"to": "a", "text": "diff --git a/x.go b/x.go\n+x"})
            self.assertEqual(st, 400); self.assertIn("a diff", d["error"])
            self.assertIn("squad_request_merge writer=<you>", d["error"])
            st, d = self._sq("/api/squad/post", B, {"to": "a", "text": "PART 1/4\n" + "QUJD" * 120})
            self.assertEqual(st, 400); self.assertIn("base64", d["error"])
            st, d = self._sq("/api/squad/post", B, {"to": "a", "text": "sha256 " + "ab12" * 16})
            self.assertEqual(st, 200, d)                                                # a hash is fine
            st, d = self._sq("/api/squad/post", B, {"to": "a", "text": "fixed in `run_gui.go`:\n```go\nx := 1\n```"})
            self.assertEqual(st, 200, d)
            self.assertIn("progress goes on the ticket", woke[-1][1])
            self.assertTrue(m._squads.post(m._projects.get("sq"), "user:ulrich", ["a"], block)[0])
        finally:
            undo()

    def test_squad_wake_outcome_reaches_the_channel(self):
        """'delivered' only means a wake turn was queued: the task worker books
        how it ended, so the channel shows received / not received + reason
        (live 2026-10-09: an architect on TRANSPORT=signal never got anything
        while the chat said 'delivered')."""
        woke, tasks, undo = self._squad_env()
        m, A = self.m, "172.30.3.2"
        try:
            st, msg = self._sq("/api/squad/post", A, {"to": ["b", "d"], "text": "hi"})
            self.assertEqual((st, msg["delivered"], msg["received"], msg["failed"]), (200, ["b", "d"], [], {}))
            m._squads.wake_result(msg["id"], "b", True, "done")
            m._squads.wake_result(msg["id"], "d", False, "instance 'd' not ready")
            got = next(x for x in m._squads.read("sq", "a") if x["id"] == msg["id"])
            self.assertEqual((got["received"], got["failed"]), (["b"], {"d": "instance 'd' not ready"}))
            # the worker calls it for a squad task, success or not
            calls, old = [], (m._tasks._run_task_now, m._squads.wake_result)
            m._tasks._run_task_now = lambda *a, **k: (False, "error: no web bridge")
            m._squads.wake_result = lambda *a: calls.append(a)
            try:
                m._tasks._execute({"id": "t1", "instance": "d", "message": "x", "squad": {"project": "sq", "depth": 1, "msg": msg["id"]}})
            finally:
                m._tasks._run_task_now, m._squads.wake_result = old
            self.assertEqual(calls, [(msg["id"], "d", False, "error: no web bridge")])
        finally:
            undo()

    def test_squad_working_and_queued_in_the_endpoints(self):
        """A running squad wake turn shows as 'working' since its start, a
        pending one as 'queued' — in /squad/messages and /squad/board; other
        projects' and non-squad tasks do not."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        tasks = m._store.load_tasks()
        now = int(time.time())
        tasks += [{"id": "t1", "instance": "b", "status": "running", "started": now - 75, "created": now - 90,
                   "squad": {"project": "sq", "depth": 1, "msg": 3}},
                  {"id": "t2", "instance": "d", "status": "pending", "created": now - 5, "squad": {"project": "sq", "msg": 4}},
                  {"id": "t3", "instance": "a", "status": "running", "started": now},                     # no squad wake
                  {"id": "t4", "instance": "a", "status": "pending", "squad": {"project": "other", "msg": 1}},
                  {"id": "t5", "instance": "a", "status": "done", "squad": {"project": "sq", "msg": 2}}]
        try:
            for path in ("/api/projects/sq/squad/messages", "/api/projects/sq/squad/board"):
                w = self._sq(path, "127.0.0.1")[1]["working"]
                self.assertEqual([(x["member"], x["state"], x["since"], x["msg"]) for x in w],
                                 [("b", "working", now - 75, 3), ("d", "queued", now - 5, 4)], path)
                self.assertTrue(75 <= w[0]["elapsed"] <= 80, w)
            t = [{"id": "x", "instance": "b", "status": "pending", "schedule": "", "next_run": 0}]
            claimed = m._tasks.worker_claim(t, now, lambda t: False, [], [])[1]
            self.assertEqual((claimed["status"], claimed["started"]), ("running", now))         # the worker stamps the start
        finally:
            undo()

    def test_squad_turn_answer_into_the_channel(self):
        """A finished wake turn's answer is posted once, from the member to the
        user who started the thread (kind 'turn'), wakes nobody, is archived,
        capped; skipped when the member already posted it during the turn, and
        for a failed turn."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        A = "172.30.3.2"
        try:
            P = lambda: m._projects.get("sq")
            q1 = m._squads.post(P(), "user:ann", ["a"], "build the parser")[0]
            m._squads.post(P(), "user:ulrich", ["a"], "and the docs")
            r = m._squads.post(P(), "b", [], "I can help", reply_to=q1["id"])[0]           # b answers ann's thread
            now = int(time.time())
            turn = lambda msg, **k: {"id": "t", "instance": "a", "started": now, "squad": {"project": "sq", "depth": 2, "msg": msg}, **k}
            n = len(woke)
            got = m._squads.turn_answer(turn(r["id"]), True, "Parser done, @b please review.")
            self.assertEqual((got["from"], got["to"], got["kind"], got["delivered"], got["depth"]),
                             ("a", ["user:ann"], "turn", [], 2))                            # the thread's user, not the last
            self.assertEqual(len(woke), n)                                                   # @b in the text wakes nobody
            self.assertIsNone(m._squads.turn_answer(turn(r["id"]), True, "Parser done, @b please review. "))   # once
            # the member posted it already during the turn: no duplicate
            st, d = self._sq("/api/squad/post", A, {"to": "user", "text": "Docs are written: see README."})
            self.assertEqual(st, 200, d)
            self.assertIsNone(m._squads.turn_answer(turn(q1["id"]), True, "Docs are written - see README"))
            self.assertIsNone(m._squads.turn_answer(turn(q1["id"]), False, "something else entirely"))   # failed turn
            self.assertIsNone(m._squads.turn_answer(turn(q1["id"]), True, "   "))
            long_ = m._squads.turn_answer(turn(q1["id"]), True, "x" * 9000)
            self.assertEqual(len(long_["text"]), m._squads.TURN_MAX + len("\n…[full text in the archive]"))
            self.assertTrue(long_["text"].endswith("…[full text in the archive]"))
            self.assertEqual([x["kind"] for x in m._squads.read("sq")].count("turn"), 2)
            fold = m._squadrec.folder(m._squadrec.of_project("sq"))
            ch = self._sr_lines(os.path.join(fold, "conversations", "channel", time.strftime("%Y-%m-%d") + ".jsonl"))
            self.assertEqual({e["msg_id"] for e in ch if e.get("kind") == "turn"}, {got["id"], long_["id"]})
            # the worker posts it after the turn (and not for a provider error)
            calls, old = [], (m._tasks._run_task_now, m._squads.turn_answer)
            m._squads.turn_answer = lambda *a: calls.append(a)
            try:
                for res in ("done it", "API Error: 500"):
                    m._tasks._run_task_now = lambda *a, _r=res, **k: (True, _r)
                    m._tasks._execute({"id": "t9", "instance": "d", "message": "x", "squad": {"project": "sq", "depth": 1, "msg": q1["id"]}})
            finally:
                m._tasks._run_task_now, m._squads.turn_answer = old
            self.assertEqual([(c[1], c[2]) for c in calls], [(True, "done it"), (False, "API Error: 500")])
        finally:
            undo()

    def test_squad_tools_added_on_join_and_repaired_at_startup(self):
        """A member with a fixed AGENT_TOOLS list gets the squad tools it lacks
        (the lead also LEAD_TOOLS) on approval, member approval, the operator's
        project save, the kickoff and at startup (idempotent); the channel and
        the steps say to restart; members without a list stay untouched."""
        m = self.m
        g = m._squadgate
        woke, created, notes, merges, undo = self._gate_env()
        insts, L, A = self._sq_insts, "127.0.0.1", "172.30.3.2"
        saved = []

        def save(inst):
            saved.append(inst["name"])
            insts[:] = [inst if i["name"] == inst["name"] else i for i in insts]
        old = m._instances.save_instance
        m._instances.save_instance = save
        tools = lambda n: next(i for i in insts if i["name"] == n)["config"].get("AGENT_TOOLS", "")
        setcfg = lambda n, v: next(i for i in insts if i["name"] == n)["config"].__setitem__("AGENT_TOOLS", v)
        try:
            setcfg("a", "bash,squad_propose,squad_request_member"); setcfg("b", "bash,read_file,squad_post")   # d: no list = all tools
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": True})
            self.assertEqual(st, 200, p)
            self.assertEqual(tools("b"), ",".join(["bash", "read_file", *m._squadgate.MEMBER_TOOLS]))
            self.assertEqual(tools("a"), ",".join(dict.fromkeys(["bash", "squad_propose", "squad_request_member", *g.MEMBER_TOOLS, *g.LEAD_TOOLS])))
            self.assertEqual(tools("d"), "")
            step = next(s for s in p["steps"] if s["step"] == "squad tools")
            self.assertIn("Restart members", step["detail"]); self.assertIn("b (+squad_ask", step["detail"])
            note = [x for x in m._squads.read("sq") if x["kind"] == "note"]
            self.assertEqual((len(note), note[0]["from"], note[0]["delivered"]), (1, "manager", []))
            self.assertNotIn("d (+", step["detail"])
            # a member proposal for an existing instance with a list
            insts.append({"name": "h", "index": 20, "template": "openrouter", "rootfs": "x", "mounts": [], "config": {"AGENT_TOOLS": "bash"}})
            st, d = self._sq("/api/squad/request-member", A, {"member": {"name": "h", "existing": True, "role": "reader"}, "reason": "docs"})
            self.assertEqual(st, 200, d)
            self.assertEqual(self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})[0], 200)
            self.assertEqual(tools("h"), ",".join(["bash", *g.MEMBER_TOOLS]))
            # the operator adds c in Projects
            setcfg("c", "bash")
            proj = m._projects.get("sq")
            body = {**proj, "members": {**{n: x["role"] for n, x in proj["members"].items()}, "c": "reader"}}
            st, r = self._sq("/api/projects", L, body)
            self.assertEqual((st, r["squad_tools"]), (200, {"c": list(g.MEMBER_TOOLS)}))
            # the kickoff's step (its architect already has the tools: nothing to do)
            job = {"name": "sq", "arch": "a", "done": ["index"], "log": []}
            setcfg("a", "bash,squad_post")
            m._squadkick.step_squad(job)
            self.assertIn("Restart members", job["log"][-1]["text"]); self.assertIn("squad_catalog", tools("a"))
            # startup repair: only what is missing, once
            setcfg("b", "bash"); del saved[:]
            g.repair_tools()
            self.assertEqual((saved, tools("b")), (["b"], ",".join(["bash", *g.MEMBER_TOOLS])))
            g.repair_tools()
            self.assertEqual((saved, tools("d")), (["b"], ""))
        finally:
            m._instances.save_instance = old
            undo()

    def test_squad_ask_answer_roundtrip(self):
        """ask -> the one member is woken; its reply_to post is the answer
        (wakes nobody); only the asker may poll it."""
        woke, tasks, undo = self._squad_env()
        A, B, D = "172.30.3.2", "172.30.4.2", "172.30.6.2"
        try:
            self.assertEqual(self._sq("/api/squad/ask", A, {"to": "all", "question": "?"})[0], 400)
            self.assertEqual(self._sq("/api/squad/ask", A, {"to": "a", "question": "?"})[0], 400)    # not oneself
            st, q = self._sq("/api/squad/ask", A, {"to": "b", "question": "which branch, @d?"})
            self.assertEqual((st, q["kind"], q["to"], q["delivered"]), (200, "ask", ["b"], ["b"]))
            self.assertIn(f"question {q['id']} — answer with squad_post reply_to={q['id']}", woke[0][1])
            self.assertEqual(self._sq(f"/api/squad/answer/{q['id']}", A)[1]["status"], "pending")
            self.assertEqual(self._sq(f"/api/squad/answer/{q['id']}", D)[0], 404)       # not d's question
            st, ans = self._sq("/api/squad/post", B, {"text": "main", "reply_to": q["id"]})
            self.assertEqual((ans["kind"], ans["to"], ans["delivered"]), ("answer", ["a"], []))
            self.assertEqual(len(woke), 1)                                              # the asker polls
            self.assertEqual(self._sq(f"/api/squad/answer/{q['id']}", A)[1],
                             {"status": "answered", "answer": "main", "from": "b"})
        finally:
            undo()

    def test_squad_guards_block_with_a_reason(self):
        """disabled, paused, wakes per hour, the members' tokens today and the
        reply depth each block the wake; the message is kept with the reason."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        A = "172.30.3.2"
        sq = m._squads
        try:
            def blocked(**settings):
                m._projects.set_squad("sq", sq.normalize_settings({"enabled": True, "approved": True, **settings})[0])
                st, d = self._sq("/api/squad/post", A, {"to": "b", "text": "go", "project": "sq"})
                self.assertEqual(st, 200, d)
                return d["blocked"]
            self.assertIn("disabled", blocked(enabled=False)); self.assertIn("paused", blocked(paused=True))
            self.assertEqual(woke, [])
            self.assertEqual(blocked(max_wakes_per_hour=1), ""); self.assertEqual(len(woke), 1)
            self.assertIn("rate: 1 wakes", blocked(max_wakes_per_hour=1))
            m._llmproxy.used_today = lambda name: 600
            self.assertIn("budget: members used 1800", blocked(daily_token_budget=1000))
            m._llmproxy.used_today = lambda name: 0
            tasks.append({"instance": "a", "status": "running", "squad": {"project": "sq", "depth": 8}})
            self.assertIn("depth 9 > max_depth 8", blocked())
            tasks[0]["squad"]["depth"] = 2
            self.assertEqual(blocked(), "")
            self.assertEqual(woke[-1][2], 3)                                              # the woken turn's depth
            self.assertEqual(len(woke), 2)
            self.assertIsNone(sq.normalize_settings({"max_depth": 0})[0])
            msgs = self._sq("/api/projects/sq/squad/messages", "127.0.0.1")[1]["messages"]
            self.assertEqual(sum(1 for x in msgs if x["blocked"]), 5)
        finally:
            undo()

    def test_squad_operator_routes_and_user_post(self):
        """Settings over the admin routes (validated, kept across a project
        save); the operator posts as user:<name> and wakes the @mentioned;
        role user may read and post, not change settings; delete purges."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        L = "127.0.0.1"
        try:
            st, s = self._sq("/api/projects/sq/squad", L)
            self.assertEqual((st, s["settings"]["enabled"], s["settings"]["max_wakes_per_hour"]), (200, True, 60))
            self.assertEqual(self._sq("/api/projects/sq/squad", L, {"max_depth": "x"})[0], 400)
            st, s = self._sq("/api/projects/sq/squad", L, {"paused": True, "max_depth": 5})
            self.assertEqual((st, s["settings"]["paused"], s["settings"]["max_depth"], s["settings"]["enabled"]), (200, True, 5, True))
            p = m._projects.get("sq")
            m._projects.upsert({k: p[k] for k in ("name", "strategy", "source", "members")})
            self.assertEqual(m._projects.get("sq")["squad"]["max_depth"], 5)            # a project save keeps it
            self._sq("/api/projects/sq/squad", L, {"paused": False})
            st, d = self._sq("/api/projects/sq/squad/post", L, {"text": "nobody named"})   # no addressee: the lead
            self.assertEqual((st, d["to"], d["delivered"]), (200, ["a"], ["a"]))
            self.assertNotIn("Proposal", woke[-1][1]); del woke[:]
            st, d = self._sq("/api/projects/sq/squad/post", L, {"text": "@b please rebase"})
            self.assertEqual((st, d["from"], d["to"], d["delivered"]), (200, "user:admin", ["b"], ["b"]))
            self.assertEqual(woke[0][1].split(":")[0], "[squad sq] from user")
            st, d = self._sq("/api/squad/post", "172.30.4.2", {"text": "done", "reply_to": d["id"]})
            self.assertEqual((d["to"], d["delivered"]), (["user:admin"], []))           # a reply to the operator wakes nobody
            self.assertEqual(len(self._sq("/api/projects/sq/squad/messages", L)[1]["messages"]), 3)
            for path in ("/api/projects/sq/squad", "/api/projects/sq/squad/messages", "/api/projects/sq/squad/post"):
                self.assertTrue(m._routes.ROUTER.resolve("GET" if "messages" in path else "POST", path)[1])   # admin-only for guests
            ua = m._auth.user_allowed
            self.assertTrue(ua("GET", "/api/projects/sq/squad/messages"))
            self.assertTrue(ua("POST", "/api/projects/sq/squad/post"))
            self.assertFalse(ua("GET", "/api/projects/sq/squad")); self.assertFalse(ua("POST", "/api/projects/sq/squad"))
            self.assertFalse(ua("GET", "/api/projects"))
            self.assertEqual(self._sq("/api/projects/sq/delete", L, {})[0], 200)
            self.assertEqual(m._squads.read("sq"), [])
        finally:
            undo()

    # ---- squad gate: the lead proposes, the user approves; tickets ---------------------
    _SG_PLAN = {"project": "sq", "goal": "ship v2", "budget_tokens_per_day": 500000,
                "members": [{"name": "e", "purpose": "tests", "persona": "coder", "skills": ["tdd"],
                             "model_tier": "cheap", "role": "writer"},
                            {"name": "f", "persona": "ghost", "skills": ["nope"], "model_tier": "local", "role": "reader"},
                            {"name": "b", "existing": True, "role": "writer"}],
                "tickets": [{"key": "T1", "title": "write tests", "assignee": "e"},
                            {"key": "T2", "title": "review", "assignee": "f", "depends_on": ["T1"]}]}

    def _gate_env(self):
        """_squad_env with the squad enabled but NOT approved; instance
        creation, templates, personas, skills, router tiers, notify and the
        merge path patched (recorded)."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        g = m._squadgate
        old = (g.GATE_FILE, m._instances.create_instance, m._instances.load_templates, m._personas.load_personas,
               m._skills.load_skills, m._router.load, m._notify.notify_add, m._projects.member_op, m._util.rate_ok,
               g._spawn)
        g._spawn = lambda fn, *a: None                 # no Jev thread outliving the test
        g.GATE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-sg-"), "squadgate.json")
        created, notes, merges = [], [], []

        def create(name, template, config=None, mounts=None, internet=True, persist=False):
            created.append((name, template, config, persist))
            self._sq_insts.append({"name": name, "index": 10 + len(created), "template": template,
                                   "rootfs": "x", "mounts": [], "config": config or {}})
            return f"instance '{name}' created from template '{template}'"
        m._instances.create_instance = create
        m._instances.load_templates = lambda: [{"template": "openrouter"}]
        m._personas.load_personas = lambda: [{"name": "coder", "prompt": "You code.", "tools": ["bash"]}]
        m._skills.load_skills = lambda: [{"name": "tdd"}]
        m._router.load = lambda: {"tiers": {"openrouter/acme/fast": "cheap", "llama/qwen": "local"}, "policies": {}}
        m._notify.notify_add = lambda *a, **k: notes.append(a) or ("n1", "ok")
        m._projects.member_op = lambda n, op, w: (merges.append((n, op, w)), ({"commit": "abc123"}, 200))[1]
        m._util.rate_ok = lambda *a: True
        m._projects.set_squad("sq", m._squads.normalize_settings({"enabled": True})[0])     # not approved

        def undo2():
            (g.GATE_FILE, m._instances.create_instance, m._instances.load_templates, m._personas.load_personas,
             m._skills.load_skills, m._router.load, m._notify.notify_add, m._projects.member_op, m._util.rate_ok,
             g._spawn) = old
            undo()
        return woke, created, notes, merges, undo2

    def test_squad_delete_keeps_the_repo(self):
        """Delete a squad (Squads tab): its members' instances go unless another
        repo has them, tickets/proposals/channel/settings go, the repo stays in
        Repos; admin only; a repo without a squad -> 404."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        gone, old = [], m._instances.delete_instance
        m._instances.delete_instance = lambda n: gone.append(n) or f"instance '{n}' deleted"
        try:
            other = os.path.join(os.path.dirname(m._projects.PROJECTS_FILE), "other"); os.makedirs(other)
            self.assertTrue(m._projects.upsert({"name": "other", "source": {"type": "host", "path": other},
                                                "members": {"b": "writer"}}).get("ok"))
            self.assertEqual(self._sq("/api/squad/propose", A, self._SG_PLAN)[0], 200)
            m._squads.post(m._projects.get("sq"), "a", [], "hello")
            self.assertFalse(m._auth.user_allowed("POST", "/api/squads/delete/sq"))
            st, d = self._sq("/api/squads/delete/sq", L, {})
            self.assertEqual(st, 200, d)
            self.assertEqual((d["project"], d["deleted"], d["kept"]), ("sq", ["a", "d"], ["b"]))
            self.assertEqual(gone, ["a", "d"])
            p = m._projects.get("sq")
            self.assertIsNotNone(p)                                             # the repo stays
            self.assertEqual((p.get("members"), m._squads.settings(p)["enabled"]), ({}, False))
            self.assertEqual([x for x in m._squadgate._load()["proposals"] if x.get("project") == "sq"], [])
            self.assertEqual(self._sq("/api/squads/delete/sq", L, {})[0], 404)   # no squad any more
            self.assertEqual(self._sq("/api/squads/delete/nope", L, {})[0], 404)
            self.assertIn("other", [x["name"] for x in m._projects.load()])
        finally:
            m._instances.delete_instance = old
            undo()

    def test_squadgate_propose_lead_only_validated_one_pending(self):
        """Only the lead proposes; names, roles, tiers, tickets are checked,
        unknown persona/skills flagged; one pending plan; the user is notified;
        before approval nothing is created, only the lead can be woken, and
        tickets / member requests are refused."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, B, C = "172.30.3.2", "172.30.4.2", "172.30.5.2"
        plan = self._SG_PLAN
        try:
            self.assertEqual(self._sq("/api/squad/propose", C, plan)[0], 403)                 # no member
            st, d = self._sq("/api/squad/propose", B, plan)
            self.assertEqual(st, 400); self.assertIn("only the lead", d["error"])
            bad = [({"name": "E_1"}, "a-z 0-9"), ({"name": "e", "model_tier": "huge"}, "model_tier"),
                   ({"name": "e", "role": "lead"}, "one lead"), ({"name": "c"}, "exists"),
                   ({"name": "zz", "existing": True}, "no such instance"), ({"name": "e", "template": "nope"}, "template")]
            for mem, why in bad:
                st, d = self._sq("/api/squad/propose", A, {**plan, "members": [mem]})
                self.assertEqual(st, 400, mem); self.assertIn(why, d["error"])
            for tks, why in (([{"title": "x", "assignee": "c"}], "no member"),
                             ([{"key": "T1", "title": "x", "depends_on": ["T9"]}], "no other ticket"),
                             ([{"key": "T1", "title": ""}], "title missing")):
                st, d = self._sq("/api/squad/propose", A, {**plan, "tickets": tks})
                self.assertEqual(st, 400, tks); self.assertIn(why, d["error"])
            self.assertEqual(self._sq("/api/squad/propose", A, {**plan, "goal": ""})[0], 400)
            self.assertEqual(notes, [])
            st, d = self._sq("/api/squad/propose", A, plan)
            self.assertEqual((st, d["kind"], d["status"]), (200, "squad", "pending"), d)
            self.assertTrue(any("ghost" in w for w in d["warnings"]) and any("nope" in w for w in d["warnings"]))
            self.assertEqual(notes[0][1], "Squad sq: proposal waiting for approval")
            st, d2 = self._sq("/api/squad/propose", A, {k: v for k, v in plan.items() if k != "project"})   # the one it leads
            self.assertEqual(st, 400); self.assertIn("waiting for approval already", d2["error"])
            st, d2 = self._sq("/api/squad/request-member", A, {"member": {"name": "g"}})
            self.assertEqual(st, 400); self.assertIn("waiting for approval", d2["error"])
            st, d2 = self._sq("/api/squad/ticket-create", A, {"title": "x"})
            self.assertEqual(st, 400); self.assertIn("waiting for approval", d2["error"])
            self.assertEqual(created, [])
            st, msg = self._sq("/api/squad/post", A, {"to": "b", "text": "start"})              # rule 5: lead only
            self.assertIn("waiting for approval", msg["blocked"]); self.assertEqual(woke, [])
            st, msg = self._sq("/api/projects/sq/squad/post", "127.0.0.1", {"text": "@a what is the plan?"})
            self.assertEqual(msg["delivered"], ["a"])
            board = self._sq("/api/projects/sq/squad/board", "127.0.0.1")[1]
            self.assertEqual(([p["status"] for p in board["proposals"]], board["approved"]), (["pending"], False))
            self.assertEqual(sorted(board["tier_models"]), sorted(m._squadgate._router.TIERS))  # the card's model labels
        finally:
            undo()

    def test_squadgate_approve_creates_members_tickets_idempotent(self):
        """Approve (with the operator's edits) creates the missing instances
        with persona, SKILLS, tier model; adds the members with their
        roles; sets the budget; enables + approves; creates the tickets; tells
        the lead. Approving again changes nothing; a reject is refused then."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            plan = self._SG_PLAN
            edits = {"members": plan["members"], "budget_tokens_per_day": 400000,
                     "tickets": [dict(plan["tickets"][0], title="write the tests"), plan["tickets"][1]]}
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": True, **edits})
            self.assertEqual((st, p["status"], p["decided_by"]), (200, "approved", "admin"), p)
            self.assertEqual([c[0] for c in created], ["e", "f"])
            e = created[0][2]
            self.assertEqual((e["OPENROUTER_MODEL"], e["SKILLS"], e["TRANSPORT"]), ("acme/fast", "tdd", "web"))   # reachable by wake
            self.assertEqual(e["AGENT_TOOLS"], ",".join(["bash", *m._squadgate.MEMBER_TOOLS]))
            self.assertTrue(e["AGENT_SYSTEM"].startswith("You code."))
            self.assertNotIn("load_skill", e["AGENT_SYSTEM"]); self.assertIn("writer in the squad of project sq", e["AGENT_SYSTEM"])
            self.assertIn("install the tools and toolchains you need", e["AGENT_SYSTEM"])
            self.assertTrue(all(c[3] for c in created))                                       # persistent disk
            self.assertIn("You have root in your VM via sudo", e["AGENT_SYSTEM"])
            self.assertEqual([c[2].get("AGENT_SUDO") for c in created], ["1", "1"])   # root in its VM
            self.assertNotIn("OPENROUTER_MODEL", created[1][2])                              # local tier: flagged
            failed = [s["step"] for s in p["steps"] if not s["ok"]]
            self.assertEqual(failed, ["persona f", "model f"])
            proj = m._projects.get("sq")
            self.assertEqual({n: x["role"] for n, x in proj["members"].items()},
                             {"a": "lead", "b": "writer", "d": "reader", "e": "writer", "f": "reader"})
            s = m._squads.settings(proj)
            self.assertEqual((s["enabled"], s["approved"], s["daily_token_budget"]), (True, True, 400000))
            ts = m._squadgate.tickets("sq")
            self.assertEqual([(t["id"], t["key"], t["title"], t["status"]) for t in ts],
                             [(1, "T1", "write the tests", "open"), (2, "T2", "review", "open")])
            self.assertEqual([w[0] for w in woke], ["a"]); self.assertIn("was approved by admin", woke[0][1])
            st, p2 = self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": True})   # retry = no-op
            self.assertEqual(st, 200)
            self.assertEqual((len(created), len(m._squadgate.tickets("sq"))), (2, 2))
            self.assertEqual(self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": False})[0], 400)
            with open(os.path.join(m._audit.AUDIT_DIR, "a.jsonl")) as fh:
                rec = json.loads(fh.readlines()[-1])
            self.assertEqual((rec["tool"], rec["target"]), ("squad_decision", f"sq squad {pid}"))
            # a member request now: approve creates and adds it
            st, d = self._sq("/api/squad/request-member", A, {"member": {"name": "g", "role": "reader"}, "reason": "docs"})
            self.assertEqual(st, 200, d)
            st, p3 = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})
            self.assertEqual((st, created[-1][0], m._projects.get("sq")["members"]["g"]["role"]), (200, "g", "reader"))
            self.assertEqual(self._sq("/api/projects/sq/delete", L, {})[0], 200)               # purge goes with it
            self.assertEqual((m._squadgate.proposals("sq"), m._squadgate.tickets("sq")), ([], []))
        finally:
            undo()

    def test_squadgate_request_changes_versions_and_card_edits(self):
        """Request changes needs a comment, sets changes_requested, wakes the
        lead with the comment and the card's edits; the lead's next proposal
        replaces it as v2 (history, diff); without the operator's answer a
        second proposal stays blocked; a channel message to the lead (no
        addressee) carries the revise hint and opens it again; goal / ticket
        description / add / remove edits are validated and applied on
        approve; the archive keeps the versions; admin only."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        plan, D = self._SG_PLAN, "/api/projects/sq/squad/decide"
        try:
            self._sq("/api/projects/sq/squad", L, {"enabled": True})                         # the squad record (archive)
            pid = self._sq("/api/squad/propose", A, plan)[1]["id"]
            st, d = self._sq("/api/squad/propose", A, plan)                                   # a new one: still blocked
            self.assertEqual(st, 400); self.assertIn("waiting for approval already", d["error"])
            self.assertEqual(self._sq(D, L, {"id": pid, "changes": True, "note": " "})[0], 400)   # comment required
            edits = {"goal": "ship v2 as an app", "members": plan["members"], "budget_tokens_per_day": 500000,
                     "tickets": [dict(plan["tickets"][0], description="pytest only"),
                                 {"title": "app shell", "assignee": "e", "depends_on": ["T1"]}]}
            st, p = self._sq(D, L, {"id": pid, "changes": True, "note": "no web ui, a simple app", **edits})
            self.assertEqual((st, p["status"], p["comments"][0]["text"]), (200, "changes_requested", "no web ui, a simple app"), p)
            self.assertEqual(created, [])
            w = woke[-1]
            self.assertEqual(w[0], "a")
            for part in (f"Revise proposal {pid} (v1): no web ui, a simple app", "goal: 'ship v2 as an app'",
                         "ticket added: T3 'app shell'", "ticket removed: T2 'review'", "ticket T1: description 'pytest only'",
                         "replaces this proposal as v2"):
                self.assertIn(part, w[1])
            st, d = self._sq(D, L, {"id": pid, "changes": True, "note": "x", **edits, "tickets": [{"title": "t", "assignee": "zz"}]})
            self.assertEqual(st, 400); self.assertIn("no member", d["error"])                 # edits validated
            # the lead revises: same id, v2, history and diff
            plan2 = {**plan, "goal": "ship v2 as an app", "members": plan["members"][:1] + plan["members"][2:],
                     "tickets": [plan["tickets"][0]]}
            st, d = self._sq("/api/squad/propose", A, plan2)
            self.assertEqual((st, d["id"], d["version"], d["status"]), (200, pid, 2, "pending"), d)
            p = m._squadgate._get("sq", pid)
            self.assertEqual([v["version"] for v in p["versions"]], [1])
            self.assertEqual((p["versions"][0]["status"], p["versions"][0]["goal"], len(p["versions"][0]["comments"])),
                             ("changes_requested", "ship v2", 1))
            self.assertIn("member removed: f", p["changes"]); self.assertIn("ticket removed: T2 'review'", p["changes"])
            self.assertIn("goal: 'ship v2 as an app'", p["changes"])
            self.assertEqual(len([x for x in m._squadgate.proposals("sq")]), 1)
            st, d = self._sq("/api/squad/propose", A, plan2)                                  # no answer yet: blocked
            self.assertEqual(st, 400); self.assertIn("waiting for approval already", d["error"])
            # a plain channel message goes to the lead, with the revise hint; then v3 may come
            st, msg = self._sq("/api/projects/sq/squad/post", L, {"text": "please no web ui, a simple app ui"})
            self.assertEqual((st, msg["to"], msg["delivered"]), (200, ["a"], ["a"]), msg)
            self.assertIn(f"Proposal {pid} v2 is waiting for approval", woke[-1][1]); self.assertIn("squad_propose", woke[-1][1])
            self.assertNotIn("Proposal", msg["text"])                                          # the channel keeps the text
            self.assertEqual(self._sq("/api/squad/propose", A, plan2)[1]["version"], 3)
            self.assertIsNone(m._squadgate.diff(plan2, plan2) or None)
            # approve with card edits: goal, a description, a ticket added (key auto) and one removed
            st, d = self._sq(D, L, {"id": pid, "approve": True, **edits, "tickets": [{"title": "x", "depends_on": ["T9"]}]})
            self.assertEqual(st, 400); self.assertIn("no other ticket", d["error"])
            ed = {"goal": "an app", "members": plan2["members"], "budget_tokens_per_day": 1000,
                  "tickets": [{"key": "T1", "title": "write tests", "description": "pytest", "assignee": "e"},
                              {"key": "", "title": "build app", "description": "tk", "assignee": "b", "depends_on": ["T1"]}]}
            st, p = self._sq(D, L, {"id": pid, "approve": True, **ed})
            self.assertEqual((st, p["status"], p["goal"]), (200, "approved", "an app"), p)
            self.assertEqual([(t["key"], t["title"], t["description"], t["depends_on"]) for t in m._squadgate.tickets("sq")],
                             [("T1", "write tests", "pytest", []), ("T2", "build app", "tk", ["T1"])])
            self.assertEqual(self._sq(D, L, {"id": pid, "changes": True, "note": "more"})[0], 400)   # decided
            fold = m._squadrec.folder(m._squadrec.of_project("sq"))
            with open(os.path.join(fold, "proposals", pid + ".json")) as fh:
                pj = json.load(fh)
            self.assertEqual(([v["version"] for v in pj["versions"]], pj["version"], pj["status"], pj["comments"][0]["by"]),
                             ([1, 2], 3, "approved", "admin"))
            # a member request: the same member is revised, another one is a new proposal
            st, r1 = self._sq("/api/squad/request-member", A, {"member": {"name": "g"}, "reason": "docs"})
            self.assertEqual(self._sq("/api/squad/request-member", A, {"member": {"name": "g"}})[0], 400)
            self._sq(D, L, {"id": r1["id"], "changes": True, "note": "a reader"})
            st, r2 = self._sq("/api/squad/request-member", A, {"member": {"name": "g", "role": "reader"}})
            self.assertEqual((st, r2["id"], r2["version"]), (200, r1["id"], 2))
            self.assertIn("member g: role 'writer' -> 'reader'", m._squadgate._get("sq", r1["id"])["changes"])
            st, r3 = self._sq("/api/squad/request-member", A, {"member": {"name": "h"}})
            self.assertEqual(st, 200); self.assertNotEqual(r3["id"], r1["id"])
            self.assertFalse(m._auth.user_allowed("POST", "/api/projects/sq/squad/decide"))   # role user: no
        finally:
            undo()

    def test_squadgate_reject_tells_the_lead(self):
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": False, "note": "too many members"})
            self.assertEqual((st, p["status"], p["note"]), (200, "rejected", "too many members"))
            self.assertEqual(created, [])
            self.assertEqual(woke[0][0], "a"); self.assertIn("rejected by admin. Note: too many members", woke[0][1])
            self.assertEqual(self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": True})[0], 400)
            self.assertFalse(m._squadgate.approved(m._projects.get("sq")))
            self.assertEqual(self._sq("/api/squad/propose", A, self._SG_PLAN)[0], 200)           # a new plan may come
        finally:
            undo()

    def test_squadgate_tickets_wake_assignee_and_dependents(self):
        """After approval: only the lead creates/assigns; assigning a ready
        ticket wakes the assignee; the last dependency done wakes the next;
        only the assignee or the lead updates; the operator edits any time;
        role user reads, never decides or edits."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, B, D, L = "172.30.3.2", "172.30.4.2", "172.30.6.2", "127.0.0.1"
        E = "172.30.11.2"                                                                     # created first
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            self.assertEqual(m._squadgate.decide("sq", pid, True, "admin")[0]["status"], "approved")
            del woke[:]
            st, d = self._sq("/api/squad/ticket-create", B, {"title": "x"})
            self.assertEqual(st, 400); self.assertIn("only the lead", d["error"])
            st, t3 = self._sq("/api/squad/ticket-create", A, {"title": "docs", "assignee": "d"})
            self.assertEqual((st, t3["key"], t3["wake"]), (200, "T3", "woken"), t3)
            self.assertEqual(woke[-1][0], "d"); self.assertIn("ticket_update id=3", woke[-1][1])
            st, t4 = self._sq("/api/squad/ticket-create", A, {"title": "release", "depends_on": ["T2"]})
            st, d = self._sq("/api/squad/ticket-assign", A, {"id": t4["id"], "assignee": "b"})
            self.assertEqual((st, d["woke"]), (200, {}))                                     # T2 not done yet
            n = len(woke)
            st, d = self._sq("/api/squad/ticket-update", D, {"id": 1, "status": "done"})
            self.assertEqual(st, 400); self.assertIn("only the assignee", d["error"])
            st, d = self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "done", "comment": "green"})
            self.assertEqual((st, d["status"], d["comments"][0]["text"], list(d["woke"])), (200, "done", "green", ["T2"]))
            self.assertEqual([w[0] for w in woke[n:]], ["f"])                                 # T2 became ready
            st, d = self._sq("/api/projects/sq/squad/ticket", L, {"id": 2, "assignee": "b"})  # operator reassigns
            self.assertEqual((st, d["assignee"], list(d["woke"])), (200, "b", ["T2"]))
            self.assertEqual(woke[-1][0], "b")
            st, d = self._sq("/api/projects/sq/squad/ticket", L, {"id": 2, "status": "done"})
            self.assertEqual(list(d["woke"]), ["T4"])                                          # release is next
            self.assertEqual(self._sq("/api/projects/sq/squad/ticket", L, {"id": 2, "status": "nope"})[0], 400)
            self.assertEqual([t["status"] for t in self._sq("/api/squad/tickets", B)[1]["tickets"]],
                             ["done", "done", "open", "open"])
            ua = m._auth.user_allowed
            self.assertTrue(ua("GET", "/api/projects/sq/squad/board"))
            self.assertFalse(ua("POST", "/api/projects/sq/squad/decide"))
            self.assertFalse(ua("POST", "/api/projects/sq/squad/ticket"))
            self.assertTrue(m._routes.ROUTER.resolve("POST", "/api/projects/sq/squad/decide")[1])   # admin route
        finally:
            undo()

    def test_squadgate_comments_by_anyone_review_wakes_the_lead(self):
        """Any member comments on any ticket (only the assignee/lead change it);
        an over-long or code-carrying comment is refused, not cut; the assignee
        setting review wakes the lead (live 2026-10-13: a member could not
        comment, posted to the channel instead, and review was a message)."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, D, E = "172.30.3.2", "172.30.6.2", "172.30.11.2"
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            self.assertEqual(m._squadgate.decide("sq", pid, True, "admin")[0]["status"], "approved")
            self._sq("/api/squad/ticket-create", A, {"title": "docs", "assignee": "d"})
            self.assertIn("Only you work on it", woke[-1][1])
            st, d = self._sq("/api/squad/ticket-update", D, {"id": 1, "comment": "edge case: empty list"})
            self.assertEqual((st, d["comments"][-1]["by"]), (200, "d"), d)
            st, d = self._sq("/api/squad/ticket-update", D, {"id": 1, "status": "done"})
            self.assertEqual(st, 400); self.assertIn("add a comment instead", d["error"])
            st, d = self._sq("/api/squad/ticket-update", E, {"id": 1, "comment": "x" * 2001})
            self.assertEqual(st, 400); self.assertIn("longer than 2000", d["error"])
            st, d = self._sq("/api/squad/ticket-update", E, {"id": 1, "comment": "@@ -1,2 +1,2 @@\n-a\n+b"})
            self.assertEqual(st, 400); self.assertIn("through git", d["error"])
            del woke[:]
            st, d = self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "review", "comment": "green, sha abc123"})
            self.assertEqual((st, d["review_wake"]), (200, "woken"), d)
            self.assertEqual(woke[-1][0], "a"); self.assertIn("ready for review", woke[-1][1])
            self.assertIn("sha abc123", woke[-1][1]); self.assertIn("squad_request_merge writer=e", woke[-1][1])
        finally:
            undo()

    # ---- human in the loop: approvals, questions, reviews (mgr/hitlq.py) -------------
    def _hq_env(self, **squad):
        """_gate_env with the plan approved (members a lead, b/e writers, d/f readers;
        tickets T1 -> e, T2 -> f after T1) and a fresh queue."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        old = m._hitlq.QUEUE_FILE
        m._hitlq.QUEUE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-hq-"), "hitlq.json")
        pid = self._sq("/api/squad/propose", "172.30.3.2", self._SG_PLAN)[1]["id"]
        self.assertEqual(m._squadgate.decide("sq", pid, True, "admin")[0]["status"], "approved")
        squad.setdefault("approval_tools", m._squads.OLD_APPROVAL_DEFAULT)   # these tests exercise the gate
        self.assertEqual(self._sq("/api/projects/sq/squad", "127.0.0.1", squad)[0], 200)
        del woke[:], notes[:]

        def undo2():
            m._hitlq.QUEUE_FILE = old
            undo()
        return woke, notes, undo2

    def test_squads_overview_live_state_and_board_page(self):
        """/api/squads carries the pills' live state: members working now,
        done/total tickets, open HITL items; the operator moves a ticket via
        /squad/ticket; the page has the Kanban board and the ticket panel."""
        m = self.m
        woke, notes, undo = self._hq_env()
        L = "127.0.0.1"
        pills = lambda: {x["name"]: x for x in self._sq("/api/squads", L)[1]["squads"]}["sq"]
        try:
            s = pills()
            self.assertEqual((s["working"], s["tickets_done"], s["tickets_total"], s["waiting_for_you"]), (0, 0, 2, 0))
            m._store.load_tasks = lambda: [{"instance": "e", "status": "running", "squad": {"project": "sq", "msg": 1},
                                            "started": int(time.time())},
                                           {"instance": "f", "status": "pending", "squad": {"project": "sq"}}]
            self.assertTrue(m._hitlq.ask("e", "Which DB?")[0])
            s = pills()
            self.assertEqual((s["working"], s["waiting_for_you"]), (1, 1))     # queued f does not count
            t1 = next(t for t in m._squadgate.tickets("sq") if t["key"] == "T1")
            self.assertGreater(t1["created"], 0)                       # the ticket panel's "Created" event
            st, d = self._sq("/api/projects/sq/squad/ticket", L, {"id": t1["id"], "status": "done"})
            self.assertEqual((st, d["status"]), (200, "done"), d)
            self.assertEqual(pills()["tickets_done"], 1)
        finally:
            undo()
        page = m._ui.render()
        for s in ("function skBoardRender", "id=sk-tkd", "kb-board", "onclick=skChat()", "skTkSave", "dragstart",
                  "waiting for you", "prefers-reduced-motion:reduce){.sk-live",
                  # the ticket side panel: header selects, re-scope banner, dep chips, timeline, sticky comment box
                  "class=kb-pane", "function kbMd", "onchange=\"skTkSet({status:", "kb-rescope", "/re-scope/i",
                  "Waiting on ${wait} ticket", "turn result", "Show full answer", "Ctrl+Enter sends", "skTkSave(1)",
                  "hourCycle:'h23'", "#s-squads.kb-open", "function kbPrev", "skTkEditSave()", "t.versions||[]",
                  # who does what: member rows with the 2 h strip, the problem line, blocked reasons
                  "class=kb-mem", "<span>-1h</span><span>-30m</span><span>now</span>", "t0=now-3600", "skClearSquad(); skRepos();\n  document.getElementById('sk-title').textContent='New squad'", "onclick=skReindex()", "onclick=\"sqOpen(SK)\"", "id=sk-repo", "Open in Squads →", "pjSquadLock(!!(p.squad||{}).enabled)", "/squad/reindex',{method:'POST'", "kb-strip", "id=sk-prob",
                  "Retry failed turns (${nf})", "onclick=skDismissTurns()", "onclick=skNudge()", "/squad/retry", "/squad/nudge", "function kbWhy",
                  "last turn failed", "starts when", "no ticket ready", "previous turn failed: ", "esc('failed: '+x.why)"):
            self.assertIn(s, page)
        for s in ("skBoard()", "kb-dlg", "gestern", "Schließen", "kb-who"):
            self.assertNotIn(s, page)
        # board first: waiting cards, board, working line, composer, then the collapsible chat (closed by default)
        sq = page[page.index("<section class=\"screen\" id=s-squads>"):]
        order = [sq.index(x) for x in ("id=sk-cards", "id=sk-board", "id=sk-working", "class=sk-comp", "<details id=sk-chatbox", "id=sk-thread")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("<details id=sk-chatbox open", page)
        for s in ("localStorage.getItem('sk-chat-open')", "skChatPref(!this.parentNode.open)", "Chat (${msgs.length} message",
                  ".sk-kick>#sk-chatbox{order:1}", "skMode(true)", "skMode(false)",
                  "(p.members||[]).map(m=>m.suggested||0)",          # a merge proposal has no members: the board still renders
                  "console.error('squad '+what"):
            self.assertIn(s, page)

    def test_squad_activity_rows_retry_nudge_and_auto_retry(self):
        """Per member what it does or why not (working, failed, asks, waiting, idle) with
        a 2 h strip from squad_events; /squad/retry re-delivers a failed turn's wake at
        depth 0 (not when paused, 400 without a failure); /squad/nudge posts to the lead;
        'Restart members' retries a member whose last turn failed on its provider once."""
        m = self.m
        woke, notes, undo = self._sm_env()
        sm, L, now = m._squadmetrics, "127.0.0.1", int(time.time())
        old = m._instances.is_running, m._tasks._auto_retried.copy()
        try:
            proj = m._projects.get("sq")
            sid = m._squads.squad_id(proj)
            msg = self._sq("/api/projects/sq/squad/post", L, {"to": ["b"], "text": "build it"})[1]
            del woke[:]
            m._squads.wake_result(msg["id"], "b", False, "Not logged in · Please run /login\nmore")
            sm.record(sid, "turn_end", "task:tb", now - 600, member="b", outcome="api_error", duration_ms=5000,
                      detail={"msg": msg["id"]})
            sm.record(sid, "turn_end", "task:td", now - 1800, member="d", outcome="ok", duration_ms=120000, detail={})
            sm.record(sid, "message", "m999", now - 1700, member="d")
            sm.record(sid, "turn_end", "task:old", now - 25200, member="d", outcome="ok", duration_ms=1000, detail={})
            m._store.load_tasks = lambda: [{"instance": "e", "status": "running", "started": now - 30,
                                            "squad": {"project": "sq", "msg": msg["id"]}}]
            self.assertTrue(m._hitlq.ask("a", "Which DB?")[0])
            act = self._sq("/api/projects/sq/squad/board", L)[1]["activity"]
            a = act["members"]
            self.assertEqual({n: x["state"] for n, x in a.items()},
                             {"a": "ask", "b": "err", "d": "idle", "e": "work", "f": "wait"})
            self.assertEqual((a["b"]["reason"], a["b"]["last_turn"]["ok"]), ("Not logged in · Please run /login", False))
            self.assertEqual((a["f"]["ticket"], a["f"]["dep"], a["f"]["reason"]), ("T2", "T1", "open"))
            self.assertEqual((a["e"]["ticket"], a["e"]["since"], a["a"]["reason"]), ("T1", now - 30, "Which DB?"))
            self.assertEqual(a["d"]["strip"], [{"start": now - 1920, "end": now - 1800, "ok": True}])   # not the 7 h old one
            self.assertEqual((a["d"]["msgs"], a["d"]["last_turn"]["dur"]), ([now - 1700], 120))
            self.assertEqual(a["e"]["strip"][-1], {"start": now - 30, "end": 0, "ok": True})
            self.assertEqual((act["failed_members"], act["stalled_min"], act["queued"]), (1, None, 0))
            self.assertEqual(a["b"]["strip"][0]["why"], "Not logged in · Please run /login")   # the red block's tooltip
            # working again (a retry): the last failure stays visible next to "working"
            m._store.load_tasks = lambda: [{"instance": "b", "status": "running", "started": now - 10,
                                            "squad": {"project": "sq", "msg": msg["id"]}}]
            b = self._sq("/api/projects/sq/squad/board", L)[1]["activity"]["members"]["b"]
            self.assertEqual((b["state"], b["last_turn"]["error"]), ("work", "Not logged in · Please run /login"))
            m._store.load_tasks = lambda: []
            act = self._sq("/api/projects/sq/squad/board", L)[1]["activity"]
            self.assertEqual(act["stalled_min"], 10)
            self.assertEqual(act["stalled_why"], "#1 T1 (open): e ended its turn without finishing it; "
                                                 "#2 T2 (open): waits for T1 (open)")              # why, per open ticket
            # retry: paused -> refused; running -> re-delivered at depth 0; then nothing left
            self._sq("/api/projects/sq/squad", L, {"paused": True})
            st, d = self._sq("/api/projects/sq/squad/retry", L, {})
            self.assertEqual((st, d["retried"], d["refused"]), (200, {}, {"b": "squad paused"}))
            self._sq("/api/projects/sq/squad", L, {"paused": False})
            st, d = self._sq("/api/projects/sq/squad/retry", L, {})
            self.assertEqual((st, list(d["retried"]), woke[-1][0], woke[-1][2]), (200, ["b"], "b", 0))
            self.assertIn("build it", woke[-1][1])
            self.assertNotIn("b", m._squads._get(msg["id"])["failed"])
            sm.record(sid, "turn_end", "task:tb2", now - 5, member="b", outcome="ok", detail={"msg": msg["id"]})
            self.assertEqual(self._sq("/api/projects/sq/squad/retry", L, {})[0], 400)
            self.assertEqual(self._sq("/api/projects/sq/squad/retry", "172.30.3.2", {})[0], 403)
            # nudge: an operator post to the lead
            st, d = self._sq("/api/projects/sq/squad/nudge", L, {})
            self.assertEqual((st, d["to"], woke[-1][0]), (200, ["a"], "a"))
            self.assertIn("Status check: nobody is working", woke[-1][1])
            # auto-retry after "Restart members": once per failed provider turn
            sm.record(sid, "turn_end", "task:tb3", now - 1, member="b", outcome="api_error", detail={"msg": msg["id"]})
            m._instances.is_running = lambda i: False
            del woke[:]
            self._sq("/api/projects/sq/squad/restart", L, {})
            self._sq("/api/projects/sq/squad/restart", L, {})
            self.assertEqual([w[0] for w in woke], ["b"])
            # dismiss: the failed turn is no failure any more, no retry; a newer failure counts again
            sm.record(sid, "turn_end", "task:tb4", now, member="b", outcome="api_error", detail={"msg": msg["id"]})
            self.assertEqual(self._sq("/api/projects/sq/squad/dismiss", "172.30.3.2", {})[0], 403)
            st, d = self._sq("/api/projects/sq/squad/dismiss", L, {})
            self.assertEqual((st, d["dismissed"]), (200, ["b"]))
            act = self._sq("/api/projects/sq/squad/board", L)[1]["activity"]
            self.assertEqual((act["failed_members"], act["members"]["b"]["state"]), (0, "idle"))
            self.assertEqual(self._sq("/api/projects/sq/squad/retry", L, {})[0], 400)
            self.assertEqual(self._sq("/api/projects/sq/squad/dismiss", L, {})[0], 400)
            sm.record(sid, "turn_end", "task:tb5", now + 5, member="b", outcome="api_error", detail={"msg": msg["id"]})
            self.assertEqual(self._sq("/api/projects/sq/squad/board", L)[1]["activity"]["failed_members"], 1)
        finally:
            m._instances.is_running = old[0]
            m._tasks._auto_retried.clear(); m._tasks._auto_retried.update(old[1])
            undo()

    def test_squad_lead_rescopes_a_ticket(self):
        """The lead (guest route) or the operator (/squad/ticket) may change a
        ticket's title + description: the old version is kept (last 5) and the
        assignee is woken with 're-scoped'; another member is refused."""
        m = self.m
        woke, notes, undo = self._hq_env(approval_tools=[])
        g = m._squadgate
        try:
            t1 = next(t for t in g.tickets("sq") if t["key"] == "T1")
            ip = lambda n: next(f"172.30.{i['index']}.2" for i in self._sq_insts if i["name"] == n)
            st, d = self._sq("/api/squad/ticket-update", ip("e"), {"project": "sq", "id": t1["id"], "title": "mine now"})
            self.assertEqual(st, 400); self.assertIn("only the lead or the operator", d["error"])   # e is the assignee
            del woke[:]
            st, d = self._sq("/api/squad/ticket-update", ip("a"), {"project": "sq", "id": t1["id"], "title": "CLI only",
                                                                   "description": "no web"})
            self.assertEqual(st, 200, d)
            self.assertEqual((d["title"], d["description"]), ("CLI only", "no web"))
            self.assertEqual([(v["title"], v["by"]) for v in d["versions"]], [(t1["title"], "a")])
            self.assertIn("T1", d["woke"])
            self.assertEqual(woke[-1][0], "e"); self.assertIn("Ticket T1 re-scoped: CLI only", woke[-1][1])
            for i in range(6):                                                # the operator's route; the last 5 kept
                st, d = self._sq("/api/projects/sq/squad/ticket", "127.0.0.1", {"id": t1["id"], "title": f"v{i}"})
                self.assertEqual(st, 200, d)
            self.assertEqual([v["title"] for v in d["versions"]], ["v0", "v1", "v2", "v3", "v4"])
            self.assertEqual((d["title"], d["description"], d["versions"][-1]["by"]), ("v5", "no web", "user:admin"))
            d, _ = g.update_ticket(m._projects.get("sq"), "user:admin", t1["id"], {"title": "v5"}, lambda tk: "")
            self.assertEqual(len(d["versions"]), 5)                           # unchanged: no new version
        finally:
            undo()

    def test_projects_route_with_an_empty_name_says_so(self):
        """'/api/projects//squad/decide' (a page lost the project name) answers
        400 'project name missing …' instead of 'unknown operation'."""
        h = self._post_handler("/api/projects//squad/decide", "127.0.0.1", b"{}")
        h._do_POST()
        st = self._status(h)
        body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
        self.assertEqual(st, 400); self.assertIn("project name missing", body["error"])

    def test_squad_reassign_or_restart_wakes_the_assignee(self):
        """Re-assigning a ticket to the member who already holds it, or setting
        it to doing, wakes that member (it used to be silent); a ticket that is
        not ready says why nobody was woken; the assignee updating its own
        ticket wakes nobody."""
        m = self.m
        woke, notes, undo = self._hq_env(approval_tools=[])
        g, p = m._squadgate, lambda: m._projects.get("sq")
        try:
            ts = {t["key"]: t for t in g.tickets("sq")}
            first = next(t for t in ts.values() if not t["depends_on"])
            later = next(t for t in ts.values() if t["depends_on"])
            del woke[:]
            out, _ = g.update_ticket(p(), "a", first["id"], {"assignee": first["assignee"]}, lambda tk: "")
            self.assertIn(first["key"], out["woke"]); self.assertEqual(woke[-1][0], first["assignee"])
            n = len(woke)
            out, _ = g.update_ticket(p(), "a", first["id"], {"status": "doing"}, lambda tk: "")
            self.assertEqual(len(woke), n + 1)
            out, _ = g.update_ticket(p(), first["assignee"], first["id"], {"status": "doing"}, lambda tk: "")
            self.assertEqual(out["woke"], {})                                     # its own update
            out, _ = g.update_ticket(p(), "a", later["id"], {"assignee": later["assignee"]}, lambda tk: "")
            self.assertEqual(out["woke"], {}); self.assertIn("waiting for", out["note"])
        finally:
            undo()

    def test_squad_all_tickets_done_asks_the_lead_for_a_summary_once(self):
        """When the last ticket is done the lead is woken to write the user a
        final summary and the user gets a notification — once per ticket set;
        old-default approval lists are switched to 'none' once."""
        m = self.m
        woke, notes, undo = self._hq_env(approval_tools=[])
        g, p = m._squadgate, lambda: m._projects.get("sq")
        try:
            ts = g.tickets("sq")
            for t in ts[:-1]:
                g.update_ticket(p(), "user:admin", t["id"], {"status": "done"}, lambda tk: "")
            self.assertFalse(any("final summary" in w[1] for w in woke))
            out, _ = g.update_ticket(p(), "user:admin", ts[-1]["id"], {"status": "done"}, lambda tk: "")
            self.assertEqual(out["summary"], "lead asked for the summary")
            lead = [w for w in woke if "final summary" in w[1]]
            self.assertEqual(lead[0][0], "a")
            self.assertTrue(any("tickets done" in str(n) for n in notes))
            g.update_ticket(p(), "user:admin", ts[-1]["id"], {"status": "review"}, lambda tk: "")
            out, _ = g.update_ticket(p(), "user:admin", ts[-1]["id"], {"status": "done"}, lambda tk: "")
            self.assertEqual(out["summary"], "already requested")
            # migration of the old default approval list
            m._projects.set_squad("sq", {**m._squads.settings(p()), "approval_tools": list(m._squads.OLD_APPROVAL_DEFAULT)})
            self.assertEqual(m._squads.migrate_approval_default(), 1)
            self.assertEqual(m._squads.settings(p())["approval_tools"], [])
            self.assertEqual(m._squads.migrate_approval_default(), 0)
        finally:
            undo()

    def _hq_user(self, path, body=None):
        """(status) of a request by an OIDC principal with role user."""
        m = self.m
        h = self._post_handler(path, "127.0.0.1", json.dumps(body).encode()) if body is not None else self._handler(path, "127.0.0.1")
        h.principal = m._oidc.Principal("u1", "User", "", "user")
        self.assertTrue(h._dispatch("POST" if body is not None else "GET"))
        return self._status(h)

    def test_hitl_squad_approval_wakes_member_and_lets_the_call_through_once(self):
        """A squad member's gated call: policy names the squad and the default tools;
        the request returns open (same id on a retry), a card in the channel that
        wakes nobody, a notification; approving wakes the member with the comment,
        the same call then passes once; deny wakes with DENIED; all archived."""
        m = self.m
        woke, notes, undo = self._hq_env()
        B, C, L = "172.30.4.2", "172.30.5.2", "127.0.0.1"
        try:
            st, pol = self._sq("/api/hitl/policy", B)
            self.assertEqual((st, pol["squad"], pol["tools"]), (200, "sq", ["bash"]))     # git/PR/issue: gated by the manager
            self.assertEqual(pol["bash_re"], m._hitlq.BASH_RE)
            self.assertEqual(self._sq("/api/hitl/policy", C)[1], {"squad": "", "tools": [], "bash_re": ""})
            req = {"tool": "git_push_branch", "target": '{"name": "x"}', "async": True}
            st, d = self._sq("/api/hitl", B, req)
            self.assertEqual((st, d["status"]), (200, "open")); hid = d["id"]
            self.assertEqual(self._sq("/api/hitl", B, req)[1], {"id": hid, "status": "open"})      # a retry: the same item
            card = [x for x in m._squads.read("sq") if x["kind"] == "hitl"]
            self.assertEqual(len(card), 1)
            self.assertEqual((card[0]["from"], card[0]["delivered"]), ("b", []))
            self.assertIn(f"b needs your approval ({hid}): b wants to run git_push_branch", card[0]["text"])
            self.assertEqual(woke, [])
            self.assertEqual([n[1] for n in notes], ["Waiting for you: b wants to run git_push_branch"])
            st, w = self._sq("/api/waiting?status=open", L)
            self.assertEqual((st, w["count"], w["items"][0]["id"]), (200, 1, hid))
            it = w["items"][0]
            for k in ("id", "kind", "status", "created", "project", "squad_id", "instance", "ticket", "title", "detail",
                      "options", "tool", "target"):
                self.assertIn(k, it)
            self.assertEqual((it["kind"], it["project"], it["instance"], it["tool"], it["squad_id"]),
                             ("approval", "sq", "b", "git_push_branch", m._squads.squad_id(m._projects.get("sq"))))
            st, d = self._sq(f"/api/waiting/{hid}", L, {"action": "approve", "text": "go ahead"})
            self.assertEqual((st, d["ok"], d["item"]["status"], d["item"]["comment"], d["item"]["decided_by"]),
                             (200, True, "approved", "go ahead", "admin"))
            self.assertEqual(woke[-1][0], "b")
            self.assertIn(f"Approval {hid} for git_push_branch", woke[-1][1]); self.assertIn("APPROVED by admin", woke[-1][1])
            self.assertIn("Comment: go ahead", woke[-1][1])
            self.assertEqual(self._sq("/api/hitl", B, req)[1], {"id": hid, "status": "approved"})  # the retry passes ...
            st, d = self._sq("/api/hitl", B, req)
            self.assertEqual(d["status"], "open"); self.assertNotEqual(d["id"], hid)                # ... once
            self._sq(f"/api/waiting/{d['id']}", L, {"action": "deny"})
            self.assertIn("DENIED by admin", woke[-1][1]); self.assertIn("Do not retry", woke[-1][1])
            self.assertEqual(self._sq(f"/api/waiting/{d['id']}", L, {"action": "approve"})[0], 409)   # decided already
            rec = m._squadrec.of_project("sq")
            day = os.listdir(os.path.join(m._squadrec.folder(rec), "conversations", "hitl"))[0]
            with open(os.path.join(m._squadrec.folder(rec), "conversations", "hitl", day)) as fh:
                ev = [json.loads(x) for x in fh]
            self.assertEqual([(e["event"], e["item"]["status"]) for e in ev],
                             [("create", "open"), ("decide", "approved"), ("create", "open"), ("decide", "denied")])
            self.assertTrue(ev[0]["member_id"].startswith(rec["id"] + "-"))
        finally:
            undo()

    # ---- squad metrics (mgr/squadmetrics.py): squad_events in history.db ---------------
    def _sm_env(self):
        """_hq_env (plan approved: proposal + tickets T1/T2) on a fresh history.db."""
        m = self.m
        st = m._store
        old = st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], st._routes_migrated[0]
        st.HISTORY_DB = os.path.join(tempfile.mkdtemp(prefix="e2e-sm-"), "history.db")
        st._migrated[0], st._turns_migrated[0], st._routes_migrated[0] = False, None, None
        woke, notes, undo = self._hq_env()

        def undo2():
            undo()
            st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], st._routes_migrated[0] = old
        return woke, notes, undo2

    def _sm_rows(self, kind=None):
        m = self.m
        sid = m._squads.squad_id(m._projects.get("sq"))
        return m._squadmetrics.events(sid, 0, kind or "", 5000)

    def test_squadmetrics_hooks_record_one_row_per_event(self):
        """Proposal, message, wake_blocked, ticket_status (from/to), hitl open/decided
        (wait), git push: each source writes its row once; duplicates are ignored."""
        m = self.m
        woke, notes, undo = self._sm_env()
        sm, A = m._squadmetrics, "172.30.3.2"
        sg = m._squadgit
        old = sg.resolve, sg.settings, sg.push_branch
        try:
            props = self._sm_rows("proposal")
            self.assertEqual([r["outcome"] for r in props], ["created", "approved"])
            self.assertEqual((props[1]["member"], json.loads(props[1]["detail"])["kind"]), ("a", "squad"))
            self.assertIsNotNone(props[1]["duration_ms"])
            n = len(self._sm_rows("message"))
            d = self._sq("/api/squad/post", A, {"to": "b", "text": "please check"})[1]
            msg = [r for r in self._sm_rows("message") if r["ref"] == f"m{d['id']}"]
            self.assertEqual((len(self._sm_rows("message")), msg[0]["member"], msg[0]["outcome"]), (n + 1, "a", "post"))
            self.assertTrue(msg[0]["member_id"].startswith(m._squads.squad_id(m._projects.get("sq"))))
            self._sq("/api/projects/sq/squad", "127.0.0.1", {"paused": True})
            d = self._sq("/api/squad/post", A, {"to": "b", "text": "again"})[1]
            bl = self._sm_rows("wake_blocked")
            self.assertEqual((len(bl), bl[0]["ref"], bl[0]["outcome"]), (1, f"m{d['id']}", "squad paused"))
            proj = m._projects.get("sq")
            t1 = next(t for t in m._squadgate.tickets("sq") if t["key"] == "T1")
            m._squadgate.update_ticket(proj, "admin", t1["id"], {"status": "doing"}, lambda t: "")
            m._squadgate.update_ticket(proj, "admin", t1["id"], {"comment": "no status change"}, lambda t: "")
            ts = [(r["ticket"], json.loads(r["detail"])["from"], r["outcome"]) for r in self._sm_rows("ticket_status")]
            self.assertEqual(ts, [("T1", "", "open"), ("T2", "", "open"), ("T1", "open", "doing")])
            it = m._hitlq.approval("b", "git_push_branch", "x", proj)
            m._hitlq.decide(it["id"], "approve", "admin")
            ho, hd = self._sm_rows("hitl_open"), self._sm_rows("hitl_decided")
            self.assertEqual((ho[0]["ref"], ho[0]["outcome"], ho[0]["member"]), (it["id"], "approval", "b"))
            self.assertEqual((hd[0]["outcome"], hd[0]["duration_ms"] >= 0), ("approved", True))
            sg.resolve = lambda inst, project="": (proj, "writer")
            sg.settings = lambda p: {"url": "https://h/o/r", "provider": "github", "base": "main"}
            sg.push_branch = lambda *a: {"branch": "squad/b/x", "commit": "abc", "repo": "o/r"}
            sg.call("b", "push", {"name": "x"})
            sg.call("b", "push", {"name": "x"})                          # same commit: one row
            g = self._sm_rows("git")
            self.assertEqual([(r["outcome"], r["ref"], r["member"]) for r in g], [("push", "push:squad/b/x@abc", "b")])
            # a repeat of the same archived event adds nothing
            before = len(self._sm_rows())
            m._squadrec.log_proposal(m._squadgate.proposals("sq")[0])
            self.assertEqual(len(self._sm_rows()), before)
        finally:
            sg.resolve, sg.settings, sg.push_branch = old
            undo()

    def test_squadmetrics_turn_end_tokens_level_and_outcome(self):
        """turn_start/turn_end of a wake turn: tokens + cost + model are the member's
        llm_usage rows inside the turn (not other instances', not earlier ones); level
        and escalated from the route log; a provider error text is api_error."""
        m = self.m
        woke, notes, undo = self._sm_env()
        st, sm = m._store, m._squadmetrics
        try:
            with st._hist_conn() as c:                                 # before the turn: not counted
                c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,completion_tokens,cost) "
                          "VALUES(?,?,?,?,?,?)", (int(time.time()) - 500, "b", "old", 999, 999, 9.0))
            task = {"id": "tk1", "instance": "b", "message": "Ticket T1 (id 1) is yours: x",
                    "squad": {"project": "sq", "depth": 1, "msg": 7}}
            begun = m._squadrec.turn_begin(task)
            self.assertEqual(len(self._sm_rows("turn_start")), 1)
            begun = (begun[0] - 30, begun[1])                          # a 30-s turn
            st.usage_add("b", "m-small", 100, 10, 0.01)
            st.usage_add("b", "m-big", 1000, 200, 0.10)
            st.usage_add("e", "m-big", 5000, 5000, 5.0)                # another member
            st.route_add("b", "x", "own", "m-big", "", 1, "why", level=3, escalated=True)
            m._squadrec.turn_end(task, begun, True, "done it")
            r = self._sm_rows("turn_end")[0]
            self.assertEqual((r["tokens_in"], r["tokens_out"], round(r["cost"], 2), r["model"], r["level"], r["outcome"],
                              r["ticket"], r["ref"]), (1100, 210, 0.11, "m-big", 3, "ok", "T1", "task:tk1"))
            self.assertTrue(json.loads(r["detail"])["escalated"])
            self.assertGreaterEqual(r["duration_ms"], 29000)
            self.assertEqual(len(self._sm_rows("turn_start")), 1)      # the end's own start row is a duplicate
            t2 = {**task, "id": "tk2"}
            m._squadrec.turn_end(t2, m._squadrec.turn_begin(t2), False, "API Error: 500 overloaded")
            t3 = {**task, "id": "tk3"}
            m._squadrec.turn_end(t3, m._squadrec.turn_begin(t3), False, "worker-exception (run): boom")
            self.assertEqual([x["outcome"] for x in self._sm_rows("turn_end")], ["ok", "api_error", "failed"])
        finally:
            undo()

    def test_squadmetrics_failures_never_break_the_caller(self):
        """A broken history.db: record/ingest/git/prune return quietly, the squad posts on."""
        m = self.m
        import sqlite3
        woke, notes, undo = self._sm_env()
        sm, st = m._squadmetrics, m._store
        old = st._hist_conn
        try:
            st._hist_conn = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error"))
            st_, d = self._sq("/api/squad/post", "172.30.3.2", {"to": "b", "text": "still works"})
            self.assertEqual(st_, 200)
            self.assertFalse(sm.record("x", "message", "r1"))
            sm.ingest("channel", {"nonsense": True})
            sm.ingest("nope", {})
            self.assertFalse(sm.git("sq", "b", "push", "r"))
            self.assertEqual(sm.prune(), 0)
        finally:
            st._hist_conn = old
            undo()

    def test_squadmetrics_backfill_imports_the_archive_once(self):
        """Backfill rebuilds the rows from the archive (channel, member turns, hitl,
        tickets, proposals); a re-run adds nothing (offsets + dedupe), a new
        archive line adds exactly its row, a half-written line waits."""
        m = self.m
        woke, notes, undo = self._sm_env()
        sm, st = m._squadmetrics, m._store
        try:
            proj = m._projects.get("sq")
            self._sq("/api/squad/post", "172.30.3.2", {"to": "b", "text": "hello"})
            t1 = next(t for t in m._squadgate.tickets("sq") if t["key"] == "T1")
            m._squadgate.update_ticket(proj, "admin", t1["id"], {"status": "done"}, lambda t: "")
            it = m._hitlq.approval("b", "git_push_branch", "x", proj)
            m._hitlq.decide(it["id"], "deny", "admin")
            task = {"id": "tk9", "instance": "b", "message": "go", "squad": {"project": "sq", "depth": 1, "msg": 1}}
            m._squadrec.turn_end(task, m._squadrec.turn_begin(task), True, "ok")
            live = sorted((r["kind"], r["ref"], r["outcome"]) for r in self._sm_rows())
            self.assertEqual(sm.backfill()["sq"] > 0, True)            # the first run reads the files ...
            self.assertEqual(sorted((r["kind"], r["ref"], r["outcome"]) for r in self._sm_rows()), live)   # ... no dups
            with st._hist_conn() as c:
                c.execute("DELETE FROM squad_events"); c.execute("DELETE FROM squad_metrics_import")
            sm.backfill()
            self.assertEqual(sorted((r["kind"], r["ref"], r["outcome"]) for r in self._sm_rows()), live)
            ts = [json.loads(r["detail"])["from"] for r in self._sm_rows("ticket_status") if r["ticket"] == "T1"]
            self.assertEqual(ts, ["", "open"])                          # from/to rebuilt in order
            self.assertEqual(sm.backfill()["sq"], 0)                    # re-run: nothing read, nothing added
            self.assertEqual(len(self._sm_rows()), len(live))
            rec = m._squadrec.of_project("sq")
            ch = os.path.join(m._squadrec.folder(rec), "conversations", "channel")
            path = os.path.join(ch, sorted(os.listdir(ch))[-1])
            ev = {"ts": int(time.time()), "msg_id": 99999, "from": "a", "to": ["b"], "kind": "post", "project": "sq"}
            with open(path, "a") as fh:
                fh.write(json.dumps(ev) + "\n" + json.dumps({**ev, "msg_id": 99998})[:20])
            self.assertEqual(sm.backfill()["sq"], 1)
            self.assertEqual(len(self._sm_rows()), len(live) + 1)
            with open(path, "a") as fh:
                fh.write(json.dumps({**ev, "msg_id": 99998})[20:] + "\n")
            self.assertEqual(sm.backfill()["sq"], 1)                    # the completed line, once
            self.assertEqual(sm.backfill()["sq"], 0)
        finally:
            undo()

    def test_squadmetrics_retention_and_summary(self):
        """Rows past SQUAD_METRICS_DAYS are pruned and never imported; the summary adds
        tokens/cost/turns, counts done tickets, takes the median cycle time and the human wait."""
        m = self.m
        woke, notes, undo = self._sm_env()
        sm, st = m._squadmetrics, m._store
        try:
            now, sid = int(time.time()), "zzzzzzzzzzzzzzzz"
            self.assertTrue(sm.record(sid, "message", "old", now - 10 * 86400))
            self.assertEqual(sm.prune(5), 1)
            m._settings.SITE["SQUAD_METRICS_DAYS"] = 1
            self.assertFalse(sm.record(sid, "message", "old2", now - 2 * 86400))   # too old: not imported
            m._settings.SITE["SQUAD_METRICS_DAYS"] = 365
            rec = sm.record
            rec(sid, "turn_end", "t1", now, tokens_in=100, tokens_out=10, cost=0.5, outcome="ok")
            rec(sid, "turn_end", "t2", now, tokens_in=50, tokens_out=5, cost=0.25, outcome="ok")
            rec(sid, "turn_end", "t3", now, outcome="failed")
            rec(sid, "turn_end", "t4", now, outcome="api_error")
            for tid, cyc in (("p#1", 100), ("p#2", 300)):
                rec(sid, "ticket_status", f"{tid}:{now - 1000}:open", now - 1000, outcome="open", detail={"from": "", "to": "open"})
                rec(sid, "ticket_status", f"{tid}:{now - 1000 + cyc}:done", now - 1000 + cyc, outcome="done",
                    detail={"from": "open", "to": "done"})
            rec(sid, "ticket_status", f"p#3:{now}:open", now, outcome="open", detail={"from": "", "to": "open"})
            rec(sid, "hitl_decided", "h1", now, duration_ms=5000, outcome="approved")
            rec(sid, "proposal", "p1:approved", now, duration_ms=2000, outcome="approved")
            rec(sid, "proposal", "p1:v1", now, duration_ms=999, outcome="created")      # not a wait
            s = sm.summary(sid)
            self.assertEqual((s["tokens_in"], s["tokens_out"], s["cost"], s["turns"], s["turns_ok"], s["turns_failed"],
                              s["turns_api_error"], s["tickets_done"], s["median_cycle_s"], s["human_wait_ms"]),
                             (150, 15, 0.75, 4, 2, 1, 1, 2, 200, 7000))
            self.assertEqual(sm.summary(sid, now + 1)["turns"], 0)
        finally:
            m._settings.SITE.pop("SQUAD_METRICS_DAYS", None)
            undo()

    def test_squadmetrics_routes_roles_and_csv(self):
        """GET /api/squads/<name|id>/metrics/(events|summary): admin and role user read,
        a guest never; unknown squad 404; ?kind filters; ?format=csv is a download."""
        m = self.m
        woke, notes, undo = self._sm_env()
        B, L = "172.30.4.2", "127.0.0.1"
        try:
            sid = m._squads.squad_id(m._projects.get("sq"))
            m._squadmetrics.record(sid, "message", "=evil", outcome="=1+1")
            st, d = self._sq("/api/squads/sq/metrics/summary", L)
            self.assertEqual((st, d["squad"], d["squad_id"]), (200, "sq", sid))
            self.assertIn("proposal", d["counts"])
            st, d = self._sq(f"/api/squads/{sid}/metrics/events?kind=proposal", L)
            self.assertEqual((st, {e["kind"] for e in d["events"]}), (200, {"proposal"}))
            self.assertEqual(self._sq("/api/squads/nope/metrics/events", L)[0], 404)
            self.assertEqual(self._sq("/api/squads/sq/metrics/other", L)[0], 404)
            h = self._handler("/api/squads/sq/metrics/events?format=csv", L); h._do_GET()
            head, body = h.wfile.getvalue().split(b"\r\n\r\n", 1)
            self.assertIn(b"text/csv", head); self.assertIn(b"attachment", head)
            lines = body.decode().splitlines()
            self.assertTrue(lines[0].startswith("id,ts,squad_id,project,kind"))
            self.assertIn("'=evil", body.decode()); self.assertIn("'=1+1", body.decode())   # no spreadsheet formula
            self.assertEqual(self._hq_user("/api/squads/sq/metrics/summary"), 200)
            self.assertEqual(self._hq_user("/api/squads/sq/metrics/events"), 200)
            self.assertTrue(m._auth.user_allowed("GET", "/api/squads/sq/metrics/events"))
            self.assertFalse(m._auth.user_allowed("POST", "/api/squads/sq/metrics/events"))
            self.assertEqual(self._sq("/api/squads/sq/metrics/summary", B)[0], 403)
            self.assertTrue(m._routes.ROUTER.resolve("GET", "/api/squads/sq/metrics/summary")[1])
            self.assertEqual(m._routes.ROUTER.resolve("GET", "/api/squads/kickoff")[0].__name__, "_rt_kick_get")
        finally:
            undo()

    def _note_user(self, path, body=None, name="User"):
        """(status, json) of a request by an OIDC principal with role user."""
        m = self.m
        h = self._post_handler(path, "127.0.0.1", json.dumps(body).encode()) if body is not None else self._handler(path, "127.0.0.1")
        h.principal = m._oidc.Principal("u-" + name, name, "", "user")
        self.assertTrue(h._dispatch("POST" if body is not None else "GET"))
        return self._status(h), json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1] or b"{}")

    def test_squad_notes_drafts_roles_and_send_to_the_lead(self):
        """Feedback notes (mgr/squadnotes.py): admin and role user keep drafts on a
        message (caps, own list, a user deletes only its own), a guest never; send
        posts ONE numbered message to the lead that wakes it, marks the notes sent,
        records a 'feedback' metrics row and archives; nothing left -> 400."""
        m = self.m
        woke, notes, undo = self._sm_env()
        B, L, base = "172.30.4.2", "127.0.0.1", "/api/projects/sq/squad/notes"
        try:
            p = m._projects.get("sq")
            msg, _ = m._squads.post(p, "b", ["user:admin"], "The login form validates nothing. "
                                    "The tests cover only the happy path.", kind="turn")
            del woke[:]
            st, n1 = self._sq(base, L, {"msg_id": msg["id"], "quote": "validates nothing", "prefix": "x" * 60,
                                        "suffix": ". The tests", "comment": "add server-side validation"})
            self.assertEqual((st, n1["status"], n1["by"], len(n1["prefix"])), (200, "draft", "user:admin", 40))
            st, n2 = self._sq(base, L, {"msg_id": msg["id"], "quote": "only the happy path", "comment": "add error cases"})
            self.assertEqual(st, 200)
            for bad in ({"quote": "q" * 1001}, {"comment": "c" * 4001}, {"comment": " "}, {"msg_id": 99999}):
                self.assertEqual(self._sq(base, L, {"msg_id": msg["id"], "quote": "login", "comment": "x", **bad})[0], 400, bad)
            st, nu = self._note_user(base, {"msg_id": msg["id"], "quote": "login form", "comment": "mine"})
            self.assertEqual((st, nu["by"]), (200, "user:User"))
            ids = lambda d: [x["id"] for x in d["notes"]]
            self.assertEqual(ids(self._sq(f"{base}?msg={msg['id']}", L)[1]), [n1["id"], n2["id"]])   # not the user's draft
            self.assertEqual(ids(self._note_user(base)[1]), [nu["id"]])
            self.assertEqual(self._sq(base, B, {"msg_id": msg["id"], "quote": "login", "comment": "x"})[0], 403)  # guest
            self.assertEqual(self._sq(base, B)[0], 403)
            self.assertEqual(self._note_user(f"{base}/{n1['id']}/delete", {})[0], 404)          # not the user's
            self.assertEqual(self._note_user(f"{base}/{nu['id']}/delete", {}, name="Other")[0], 404)
            self.assertEqual(self._sq(f"{base}/{nu['id']}/delete", L, {})[0], 200)               # admin: any draft
            self.assertEqual(self._note_user(f"{base}/send", {"msg_id": msg["id"]})[0], 400)     # no drafts left
            self.assertEqual(woke, [])
            st, post = self._sq(f"{base}/send", L, {"msg_id": msg["id"]})
            self.assertEqual((st, post["from"], post["to"], post["reply_to"], post["delivered"]),
                             (200, "user:admin", ["a"], msg["id"], ["a"]))
            self.assertEqual(post["text"].splitlines()[:3], [
                f"Feedback on message #{msg['id']} (b):",
                "1. «validates nothing» — add server-side validation", "2. «only the happy path» — add error cases"])
            self.assertIn("ticket_create/ticket_assign", post["text"])
            self.assertEqual([w[0] for w in woke], ["a"])
            sent = self._sq(base, L)[1]["notes"]
            self.assertEqual({(x["status"], x["sent_msg_id"]) for x in sent}, {("sent", post["id"])})
            self.assertEqual(ids(self._note_user(base)[1]), [n1["id"], n2["id"]])               # sent ones: visible to all
            self.assertEqual(self._sq(f"{base}/{n1['id']}/delete", L, {})[0], 400)               # a sent note stays
            self.assertEqual(self._sq(f"{base}/send", L, {"ids": [n1["id"]]})[0], 400)
            rows = self._sm_rows("feedback")
            self.assertEqual((len(rows), rows[0]["member"], json.loads(rows[0]["detail"])["count"]), (1, "user:admin", 2))
            rec = m._squadrec.of_project("sq")
            fb = os.path.join(m._squadrec.folder(rec), "conversations", "feedback")
            ev = json.loads(open(os.path.join(fb, os.listdir(fb)[0])).read().splitlines()[0])
            self.assertEqual((ev["post_id"], len(ev["notes"])), (post["id"], 2))
            for meth, path in (("GET", "/api/projects/sq/squad/notes"), ("POST", "/api/projects/sq/squad/notes"),
                               ("POST", "/api/projects/sq/squad/notes/send"), ("POST", "/api/projects/sq/squad/notes/7/delete")):
                self.assertTrue(m._auth.user_allowed(meth, path), path)
            page = m._ui.render()
            for bit in ("id=sqa-pop", "class=sqa-menu role=menu", "id=sqa-fab", "class=sq-ann", "mark class=", "sqaBusy("):
                self.assertIn(bit, page)
        finally:
            undo()

    def test_hitl_manager_executed_git_tools_are_gated_by_the_manager(self):
        """git_push_branch / pr_create / issue_create run in the manager, so the
        manager demands the approval itself — a guest that skips its hook and
        calls /api/git/* directly still waits; an approval of that call lets it
        through once."""
        m = self.m
        woke, notes, undo = self._hq_env()
        B, L = "172.30.4.2", "127.0.0.1"
        sg, old = m._squadgit, (m._squadgit.resolve, m._squadgit.call)
        sg.resolve = lambda inst, project="": (m._projects.get("sq"), "writer")      # a squad project with a remote
        sg.call = lambda inst, op, b: ({"url": "u"}, "sq", "own/rep", 1)
        try:
            st, d = self._sq("/api/git/issue-create", B, {"title": "Bug", "body": "x"})
            self.assertEqual(st, 403); self.assertIn("waiting for approval", d["error"]); hid = d["waiting"]
            st, d2 = self._sq("/api/git/issue-create", B, {"title": "Bug", "body": "x"})
            self.assertEqual(d2["waiting"], hid)                                     # same call, same item
            self._sq(f"/api/waiting/{hid}", L, {"action": "approve"})
            st, d3 = self._sq("/api/git/issue-create", B, {"title": "Bug", "body": "x"})
            self.assertNotIn("waiting", d3)                                          # passed the gate (no remote here)
            st, d4 = self._sq("/api/git/issue-create", B, {"title": "Bug", "body": "x"})
            self.assertIn("waiting", d4)                                             # once
            self.assertEqual(self._sq("/api/git/issues", B, {})[1].get("waiting"), None)   # not in the list: not gated
        finally:
            sg.resolve, sg.call = old
            undo()

    def test_hitl_waiting_api_contract_errors_and_roles(self):
        """/api/waiting: list by status; decide with the kind's actions only;
        400 (bad action / missing text), 404, 409; role user reads, never
        decides; guests get neither; a non-squad guest's async request is not needed."""
        m = self.m
        woke, notes, undo = self._hq_env()
        B, C, L = "172.30.4.2", "172.30.5.2", "127.0.0.1"
        try:
            self.assertEqual(self._sq("/api/hitl", C, {"tool": "bash", "target": "curl x", "async": True})[1],
                             {"id": None, "status": "not_needed"})
            hid = self._sq("/api/hitl", B, {"tool": "bash", "target": "curl x", "async": True})[1]["id"]
            q, _ = m._hitlq.ask("b", "Which DB?", ["postgres", "sqlite"])
            self.assertEqual(self._sq("/api/waiting", L)[1]["count"], 2)                  # default: open
            for body, code, err in (({"action": "nope"}, 400, "action must be"), ({}, 400, "action must be"),
                                    ({"action": "answer", "text": "x"}, 400, "takes action approve or deny")):
                st, d = self._sq(f"/api/waiting/{hid}", L, body)
                self.assertEqual(st, code); self.assertIn(err, d["error"])
            self.assertEqual(self._sq("/api/waiting/000000000000", L, {"action": "approve"})[0], 404)
            st, d = self._sq(f"/api/waiting/{q['id']}", L, {"action": "answer"})
            self.assertEqual(st, 400); self.assertIn("text or choice required", d["error"])
            st, d = self._sq(f"/api/waiting/{q['id']}", L, {"action": "answer", "choice": "mysql"})
            self.assertEqual(st, 400); self.assertIn("choice must be one of", d["error"])
            # role user: list yes, decide no; guests: neither
            self.assertEqual(self._hq_user("/api/waiting?status=open"), 200)
            self.assertEqual(self._hq_user(f"/api/waiting/{hid}", {"action": "approve"}), 403)
            self.assertTrue(m._auth.user_allowed("GET", "/api/waiting"))
            self.assertFalse(m._auth.user_allowed("POST", f"/api/waiting/{hid}"))
            self.assertEqual(self._sq("/api/waiting", B)[0], 403)
            self.assertEqual(self._sq(f"/api/waiting/{hid}", B, {"action": "approve"})[0], 403)
            self.assertTrue(m._routes.ROUTER.resolve("GET", "/api/waiting")[1])
            self.assertTrue(m._routes.ROUTER.resolve("POST", f"/api/waiting/{hid}")[1])
            st, d = self._sq(f"/api/waiting/{q['id']}", L, {"action": "answer", "choice": "sqlite", "text": "small"})
            self.assertEqual((st, d["item"]["answer"], d["item"]["comment"], d["item"]["status"]), (200, "sqlite", "small", "answered"))
            self.assertEqual(self._sq(f"/api/waiting/{q['id']}", L, {"action": "answer", "text": "x"})[0], 409)
            st, w = self._sq("/api/waiting?status=answered", L)
            self.assertEqual([x["id"] for x in w["items"]], [q["id"]])
            self.assertEqual(self._sq("/api/waiting?status=all", L)[1]["count"], 2)
        finally:
            undo()

    def test_hitl_question_blocks_the_ticket_and_the_answer_wakes(self):
        """ask_human: the member's doing ticket (or the named one) goes blocked; the
        answer sets it back to doing and wakes the member with it; outside a squad refused."""
        m = self.m
        woke, notes, undo = self._hq_env()
        E, C, L = "172.30.11.2", "172.30.5.2", "127.0.0.1"
        try:
            self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "doing"})
            del woke[:]
            st, d = self._sq("/api/hitl/ask", C, {"question": "?"})
            self.assertEqual(st, 400); self.assertIn("in a squad only", d["error"])
            self.assertEqual(self._sq("/api/hitl/ask", E, {"question": " "})[0], 400)
            self.assertEqual(self._sq("/api/hitl/ask", E, {"question": "x", "ticket": "T9"})[0], 400)
            st, d = self._sq("/api/hitl/ask", E, {"question": "Postgres or SQLite?", "options": ["postgres", "sqlite"]})
            self.assertEqual((st, d["ticket"], d["status"]), (200, "T1", "open"), d)
            t1 = lambda: next(t for t in m._squadgate.tickets("sq") if t["key"] == "T1")
            self.assertEqual(t1()["status"], "blocked")
            self.assertIn("asked the operator", t1()["comments"][-1]["text"])
            card = [x for x in m._squads.read("sq") if x["kind"] == "hitl"][-1]
            self.assertIn("e asks you", card["text"]); self.assertIn("Options: postgres | sqlite", card["text"])
            self.assertEqual(woke, [])
            self.assertIn("Waiting for you: Postgres or SQLite?", [n[1] for n in notes])
            st, r = self._sq(f"/api/waiting/{d['id']}", L, {"action": "answer", "choice": "sqlite"})
            self.assertEqual((st, r["item"]["status"]), (200, "answered"))
            self.assertEqual(t1()["status"], "doing")
            self.assertEqual(woke[-1][0], "e")
            self.assertIn(f"Answer to your question {d['id']} (Postgres or SQLite?): sqlite", woke[-1][1])
            self.assertIn("Ticket T1 is back to doing", woke[-1][1])
            with open(os.path.join(m._squadrec.folder(m._squadrec.of_project("sq")), "tickets.jsonl")) as fh:
                self.assertEqual([json.loads(x)["ticket"]["status"] for x in fh][-2:], ["blocked", "doing"])
        finally:
            undo()

    def test_hitl_supervised_review_done_approve_and_request_changes(self):
        """autonomy=supervised: a member's done becomes review + a review item
        (one per ticket); Approve -> done and the dependents wake; Request changes
        (comment required) -> doing and the assignee is woken with it; trusted: done stays done."""
        m = self.m
        woke, notes, undo = self._hq_env(autonomy="supervised")
        E, L = "172.30.11.2", "127.0.0.1"
        try:
            self.assertEqual(m._squads.settings(m._projects.get("sq"))["autonomy"], "supervised")
            st, d = self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "done", "comment": "tests green"})
            self.assertEqual((st, d["status"], d["woke"]), (200, "review", {}), d)
            self.assertIn("waits for the operator's review", d["note"])
            rid = d["review"]
            it = m._hitlq.get(rid)
            self.assertEqual((it["kind"], it["ticket"], it["title"], it["detail"]), ("review", "T1", "Review T1: write tests", "tests green"))
            self.assertEqual(self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "done"})[1]["review"], rid)   # one per ticket
            st, r = self._sq(f"/api/waiting/{rid}", L, {"action": "request_changes"})
            self.assertEqual(st, 400); self.assertIn("text required", r["error"])
            del woke[:]
            st, r = self._sq(f"/api/waiting/{rid}", L, {"action": "request_changes", "text": "cover the error path"})
            self.assertEqual((st, r["item"]["status"]), (200, "changes_requested"))
            t1 = lambda: next(t for t in m._squadgate.tickets("sq") if t["key"] == "T1")
            self.assertEqual(t1()["status"], "doing")
            self.assertEqual(woke[-1][0], "e"); self.assertIn("changes requested by admin: cover the error path", woke[-1][1])
            rid2 = self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "done"})[1]["review"]
            self.assertNotEqual(rid2, rid)
            del woke[:]
            st, r = self._sq(f"/api/waiting/{rid2}", L, {"action": "approve", "text": "nice"})
            self.assertEqual((st, r["item"]["status"], r["item"]["effect"]), (200, "approved", "done"))
            self.assertEqual(t1()["status"], "done")
            self.assertEqual([w[0] for w in woke], ["f"])                                       # T2 (after T1) wakes
            self.assertEqual(self._sq("/api/projects/sq/squad", L, {"autonomy": "bogus"})[0], 400)
            self._sq("/api/projects/sq/squad", L, {"autonomy": "trusted"})
            self._sq("/api/projects/sq/squad/ticket", L, {"id": 2, "status": "doing"})
            st, d = self._sq("/api/squad/ticket-update", "172.30.12.2", {"id": 2, "status": "done"})
            self.assertEqual((d["status"], "review" in d), ("done", False))
        finally:
            undo()

    def test_hitl_squad_settings_default_tools_and_bash_patterns(self):
        """approval_tools default + validation; BASH_RE gates network, install, delete,
        push — not ordinary commands."""
        m = self.m
        sq, rx = m._squads, re.compile(m._hitlq.BASH_RE)
        self.assertEqual(sq.settings({})["approval_tools"], [])                 # members run in VMs: nothing gated by default
        self.assertEqual(sq.settings({})["autonomy"], "trusted")
        self.assertEqual(sq.normalize_settings({"approval_tools": "bash, pr_create,bash"})[0]["approval_tools"], ["bash", "pr_create"])
        self.assertEqual(sq.normalize_settings({"approval_tools": []})[0]["approval_tools"], [])
        self.assertIsNone(sq.normalize_settings({"approval_tools": "rm -rf"})[0])
        self.assertIsNone(sq.normalize_settings({"approval_tools": 5})[0])
        for cmd in ("curl https://x", "wget x", "pip install foo", "pip3 install foo", "npm install", "npm i left-pad",
                    "sudo apt-get install x", "apt install y", "rm -r build", "rm -rf build", "rm -fr x", "git push origin main"):
            self.assertTrue(rx.search(cmd), cmd)
        for cmd in ("ls -la", "git status", "rm file.txt", "python3 -m pytest", "grep -r curly .", "npm test", "git commit -m push"):
            self.assertFalse(rx.search(cmd), cmd)

    def test_squadgate_merge_proposal_runs_the_merge_path(self):
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        try:
            st, d = self._sq("/api/squad/request-merge", A, {"writer": "b", "summary": "feature done"})
            self.assertEqual(st, 400); self.assertIn("worktree", d["error"])                   # shared project
            items = m._projects.load(); items[0]["strategy"] = "worktree"; m._projects._save(items)
            self.assertEqual(self._sq("/api/squad/request-merge", A, {"writer": "d", "summary": "x"})[0], 400)
            st, d = self._sq("/api/squad/request-merge", A, {"writer": "b", "summary": "feature done"})
            self.assertEqual(st, 200, d)
            self.assertEqual(merges, [])
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})
            self.assertEqual((st, merges, p["steps"][0]["ok"], p["steps"][0]["detail"]),
                             (200, [("sq", "merge", "b")], True, "abc123"))
        finally:
            undo()

    def test_squad_requirements_before_the_plan(self):
        """A drafting squad: the lead may not propose; REQUIREMENTS.md from the
        lead's workspace shows on the board (never through a symlink); the
        operator agrees only with a document, admin only; then the lead is told
        to plan and may propose (live 2026-10-13: cfdo-2 began with "app ui,
        not browser" after the plan)."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        old_root = m._mounts.AGENT_ROOT
        m._mounts.AGENT_ROOT = tempfile.mkdtemp(prefix="e2e-req-")
        try:
            m._squads.save_settings("sq", {**m._squads.settings(m._projects.get("sq")), "requirements": "drafting",
                                           "team": "- devops: git and CI"})
            st, d = self._sq("/api/squad/propose", A, self._SG_PLAN)
            self.assertEqual(st, 400); self.assertIn("requirements not agreed", d["error"])
            st, d = self._sq("/api/projects/sq/squad/requirements", L, {})
            self.assertEqual(st, 400); self.assertIn("no REQUIREMENTS.md", d["error"])
            os.makedirs(os.path.join(m._mounts.AGENT_ROOT, "a"))
            f = os.path.join(m._mounts.AGENT_ROOT, "a", "REQUIREMENTS.md")
            os.symlink("/etc/hostname", f)                                     # the VM planted a link
            self.assertEqual(self._sq("/api/projects/sq/squad/board", L)[1]["requirements"]["text"], "")
            os.remove(f)
            with open(f, "w") as fh:
                fh.write("# Goal\nA desktop app, not a browser UI.\n")
            r = self._sq("/api/projects/sq/squad/board", L)[1]["requirements"]
            self.assertEqual((r["state"], r["text"].splitlines()[1]), ("drafting", "A desktop app, not a browser UI."))
            self.assertFalse(m._auth.user_allowed("POST", "/api/projects/sq/squad/requirements"))
            del woke[:]
            st, d = self._sq("/api/projects/sq/squad/requirements", L, {})
            self.assertEqual((st, d["state"]), (200, "agreed"), d)
            self.assertEqual(woke[-1][0], "a"); self.assertIn("squad_propose", woke[-1][1])
            self.assertIn("exactly these roles", woke[-1][1]); self.assertIn("- devops: git and CI", woke[-1][1])
            self.assertEqual(self._sq("/api/squad/propose", A, self._SG_PLAN)[0], 200)
            self.assertEqual(self._sq("/api/projects/sq/squad/requirements", L, {})[0], 400)    # agreed already
            self.assertIn("ONE question at a time", sys.modules["mgr.squadreq"].INTERVIEW)
        finally:
            m._mounts.AGENT_ROOT = old_root
            undo()

    def test_squadgate_trusted_squad_merges_without_the_operator(self):
        """Trusted: a merge proposal is decided at once (auto) and a done ticket
        merges its writer; supervised: both wait for the operator (live
        2026-10-13: a done ticket's work never reached main)."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        g, A, E = m._squadgate, "172.30.3.2", "172.30.11.2"
        g._spawn = lambda fn, *a: fn(*a)                  # run the background step inline
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            self.assertEqual(g.decide("sq", pid, True, "admin")[0]["status"], "approved")
            items = m._projects.load(); items[0]["strategy"] = "worktree"; m._projects._save(items)
            del merges[:]
            st, d = self._sq("/api/squad/request-merge", A, {"writer": "b", "summary": "feature done"})
            self.assertEqual((st, merges), (200, [("sq", "merge", "b")]), d)
            self.assertEqual(g._get("sq", d["id"])["decided_by"], g.AUTO)
            del merges[:]
            st, t = self._sq("/api/squad/ticket-update", E, {"id": 1, "status": "done"})
            self.assertEqual(st, 200, t)
            self.assertEqual((t["merge"], merges), ("started", [("sq", "merge", "e")]))
            self.assertIn("merged e's work for T1", m._squads.read("sq")[-1]["text"])
            # the last done ticket pushes the run's branch — after its merge
            seq, old_push = [], m._projects.push_run
            m._projects.push_run = lambda n: seq.append(("push", n))
            try:
                st, t2 = self._sq("/api/projects/sq/squad/ticket", "127.0.0.1", {"id": 2, "status": "done"})
                self.assertEqual((st, seq), (200, [("push", "sq")]), t2)                # T2's f is a reader: no merge
                del seq[:]
                m._projects.member_op = lambda n, op, w: (seq.append((op, w)), ({"commit": "abc123"}, 200))[1]
                g.after_done("sq", "e", "T1", True)
                self.assertEqual(seq, [("merge", "e"), ("push", "sq")])
            finally:
                m._projects.push_run = old_push
            # a failed merge wakes the writer and the lead
            m._projects.member_op = lambda n, op, w: ({"error": "merge conflict", "conflicts": ["a.go"]}, 409)
            del woke[:]
            g.merge_done("sq", "e", "T1")
            self.assertEqual(sorted(w[0] for w in woke), ["a", "e"]); self.assertIn("a.go", woke[0][1])
            # supervised: the operator decides
            m._projects.member_op = lambda n, op, w: (merges.append((n, op, w)), ({"commit": "abc123"}, 200))[1]
            self.assertEqual(self._sq("/api/projects/sq/squad", "127.0.0.1", {"autonomy": "supervised"})[0], 200)
            del merges[:]
            st, d = self._sq("/api/squad/request-merge", A, {"writer": "b", "summary": "again"})
            self.assertEqual((st, d["status"], merges), (200, "pending", []))
        finally:
            undo()

    def test_push_run_branch_to_every_network_remote(self):
        """A finished run: the base goes out as squad/<squad id> to every network
        remote (not to their base), as the repo owner with stored credentials,
        never forced; a local-path remote is left out; one failure is reported
        per remote; push_when_done=false turns it off."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        wt, calls = m._projwt, []
        old = (wt._run, wt.git, m._squads.squad_id)
        try:
            items = m._projects.load(); items[0].update(strategy="worktree", base="main"); m._projects._save(items)
            p = m._projects.get("sq")
            m._squads.squad_id = lambda proj: "run42"
            urls = {"origin": "https://github.com/x/y.git", "gitea": "https://git.example/x/y.git", "local": "/srv/repo"}
            wt.git = lambda repo, args, **k: " ".join(urls) if args == ["remote"] else urls[args[2]]
            ok = lambda *a, **k: type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            wt._run = lambda repo, args, *a, **k: calls.append(args) or ok()
            r = m._projects.push_run("sq")
            self.assertEqual((r["pushed"], r["failed"], r["branch"]), (["origin", "gitea"], {}, "squad/run42"))
            self.assertEqual(calls[0], ["-c", "credential.helper=store", "push", "origin", "refs/heads/main:refs/heads/squad/run42"])
            self.assertFalse(any("--force" in c or "local" in c for c in calls))
            self.assertIn("pushed squad/run42 to origin, gitea", m._squads.read("sq")[-1]["text"])
            wt._run = lambda repo, args, *a, **k: type("R", (), {"returncode": int("gitea" in args), "stderr": "rejected", "stdout": ""})()
            r = m._projects.push_run("sq")
            self.assertEqual((r["pushed"], r["failed"]), (["origin"], {"gitea": "rejected"}))
            self.assertIn("push to gitea failed: rejected", m._squads.read("sq")[-1]["text"])
            m._projects.set_squad("sq", m._squads.normalize_settings({"push_when_done": False}, m._squads.settings(p))[0])
            self.assertIsNone(m._projects.push_run("sq"))
        finally:
            wt._run, wt.git, m._squads.squad_id = old
            undo()

    # ---- squad toolchain layer (mgr/toolchain.py) ----
    def test_toolchain_spec_validation_and_version(self):
        """Package names, script size, env names/values are checked; a changed
        spec bumps toolchain_version, the same spec (or none in the body) does not."""
        sq = self.m._squads
        ok, why = sq.toolchain_spec({"apt": "golang-go, g++  libgl1-mesa-dev golang-go", "script": "echo hi\r\n",
                                     "env": {"PATH": "/usr/local/go/bin:$PATH", "GOROOT": "${HOME}/go", "N": 3}})
        self.assertEqual(why, "")
        self.assertEqual(ok, {"apt": ["golang-go", "g++", "libgl1-mesa-dev"], "script": "echo hi",
                              "env": {"GOROOT": "${HOME}/go", "N": "3", "PATH": "/usr/local/go/bin:$PATH"}})
        bad = [({"apt": ["rm -rf /"]}, "not a package name"), ({"apt": ["Foo"]}, "not a package name"),
               ({"apt": ["a;b"]}, "not a package name"), ({"apt": ["$(id)"]}, "not a package name"),
               ({"apt": ["-y"]}, "not a package name"), ({"apt": [f"p{i}" for i in range(101)]}, "at most 100"),
               ({"apt": 5}, "a list"), ({"script": "x" * 8193}, "8192 bytes"), ({"script": "a\0b"}, "8192 bytes"),
               ({"env": {"path": "x"}}, "A-Z 0-9 _"), ({"env": {"1X": "x"}}, "A-Z 0-9 _"),
               ({"env": {"X": "a\nb"}}, "printable"), ({"env": {"X": "`id`"}}, "printable"),
               ({"env": {"X": "$(id)"}}, "printable"), ({"env": {"X": "${X:-$(id)}"}}, "printable"),
               ({"env": {"X": "x" * 513}}, "printable"), ({"env": {"X": ["a"]}}, "printable"),
               ({"env": {f"K{i}": "v" for i in range(51)}}, "at most 50"), ("apt", "an object")]
        for raw, want in bad:
            spec, why = sq.toolchain_spec(raw)
            self.assertIsNone(spec, raw); self.assertIn(want, why, raw)
        self.assertEqual(sq.settings({})["toolchain_version"], 0)
        s1 = sq.normalize_settings({"toolchain": {"apt": ["gcc"]}})[0]
        self.assertEqual((s1["toolchain"]["apt"], s1["toolchain_version"]), (["gcc"], 1))
        self.assertEqual(sq.normalize_settings({"toolchain": {"apt": ["gcc"]}}, s1)[0]["toolchain_version"], 1)   # same spec
        self.assertEqual(sq.normalize_settings({"paused": True}, s1)[0]["toolchain"]["apt"], ["gcc"])          # kept
        self.assertEqual(sq.normalize_settings({"toolchain": {"apt": ["gcc", "make"]}}, s1)[0]["toolchain_version"], 2)
        self.assertIn("not a package name", sq.normalize_settings({"toolchain": {"apt": ["x y"]}}, s1)[1])

    def test_toolchain_script_generation(self):
        """toolchain.sh: set -e, apt update/install (names shell-quoted), the
        squad's script, clean, lists removed, env file + profile.d, the marker
        — in that order; the env file is valid sh where only $NAME expands."""
        import subprocess
        tc = self.m._toolchain
        spec = {"apt": ["gcc", "g++", "a b"], "script": "echo hi", "env": {"PATH": "/opt/go/bin:$PATH", "Q": 'a"b\\c', "R": "${HOME}/x"}}
        sh = tc.build_script(spec, 3)
        order = ["set -e", "apt-get update", "apt-get install -y --no-install-recommends gcc g++ 'a b'",
                 "bash -e /config/toolchain-user.sh", "apt-get clean", "rm -rf /var/lib/apt/lists/*",
                 f"cat > {tc.ENV_FILE} <<'KAIM56_ENV'", 'export PATH="/opt/go/bin:$PATH"', "\nKAIM56_ENV\n",
                 f"cp {tc.ENV_FILE} {tc.PROFILE_FILE}", "echo v3 > /.kaim56-toolchain-ok"]
        pos = [sh.index(x) for x in order]
        self.assertEqual(pos, sorted(pos), sh)
        self.assertEqual(subprocess.run(["sh", "-n"], input=sh, text=True).returncode, 0)
        self.assertNotIn("bash -e", tc.build_script({"apt": ["gcc"], "script": " ", "env": {}}, 1))
        self.assertNotIn("apt-get install", tc.build_script({"apt": [], "script": "true", "env": {}}, 1))
        env = os.path.join(tempfile.mkdtemp(prefix="e2e-tcenv-"), "env")
        with open(env, "w") as fh:
            fh.write(tc.env_text(spec["env"]))
        out = subprocess.run(["sh", "-c", f'. {env}; printf "%s|%s|%s" "$PATH" "$Q" "$R"'], text=True, capture_output=True,
                             env={"PATH": "/usr/bin:/bin", "HOME": "/h"}).stdout
        self.assertEqual(out, '/opt/go/bin:/usr/bin:/bin|a"b\\c|/h/x')
        self.assertEqual(tc.result("boot\nKAIM56_TOOLCHAIN_RESULT=ok\nKAIM56_TOOLCHAIN_RESULT=fail rc=2\n"), "fail rc=2")
        self.assertEqual(tc.result("no verdict"), "")
        with self.assertRaises(ValueError):
            tc.layer_path("../etc", "claude", 1)
        with self.assertRaises(ValueError):
            tc.layer_path("a" * 16, "../x", 1)

    def test_toolchain_propose_card_approve_builds(self):
        """The lead proposes (members may not; a bad spec is refused; one
        pending), the card carries the diff, the operator's edit is what is
        approved: the spec lands in the squad settings (v1) and the build is
        started; members read it with toolchain_status."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, B, C, L = "172.30.3.2", "172.30.4.2", "172.30.5.2", "127.0.0.1"
        tc, spawned = m._toolchain, []
        old = (tc._spawn, tc.STATE_FILE, tc._active.copy())
        tc._spawn = lambda fn, *a: spawned.append((fn.__name__, a))
        tc.STATE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-tc-"), "toolchains.json")
        try:
            body = {"apt": ["golang-go"], "script": "go version", "env": {"GOFLAGS": "-mod=mod"}, "reason": "Go for everybody"}
            st, d = self._sq("/api/squad/toolchain-propose", B, body)
            self.assertEqual(st, 400); self.assertIn("only the lead", d["error"])
            self.assertEqual(self._sq("/api/squad/toolchain-propose", C, body)[0], 403)
            st, d = self._sq("/api/squad/toolchain-propose", A, {"apt": ["x;reboot"]})
            self.assertEqual(st, 400); self.assertIn("not a package name", d["error"])
            self.assertIn("empty", self._sq("/api/squad/toolchain-propose", A, {"apt": []})[1]["error"])
            st, d = self._sq("/api/squad/toolchain-propose", A, body)
            self.assertEqual((st, d["kind"], d["status"]), (200, "toolchain", "pending"), d)
            self.assertIn("waiting for approval already", self._sq("/api/squad/toolchain-propose", A, body)[1]["error"])
            board = self._sq("/api/projects/sq/squad/board", L)[1]
            card = board["proposals"][0]
            self.assertEqual((card["kind"], card["goal"], card["toolchain"]["apt"]), ("toolchain", "Go for everybody", ["golang-go"]))
            self.assertIn("apt added: golang-go", card["spec_diff"]); self.assertIn("env GOFLAGS: — -> '-mod=mod'", card["spec_diff"])
            self.assertEqual(board["toolchain"]["empty"], True)
            self.assertEqual(spawned, [])                                       # nothing before approval
            edit = {"toolchain": {"apt": ["golang-go", "gcc"], "script": "go version", "env": {}}}
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True, **edit})
            self.assertEqual((st, p["status"]), (200, "approved"), p)
            steps = {s["step"]: s for s in p["steps"]}
            self.assertEqual(steps["toolchain spec"]["detail"], "v1")
            self.assertEqual(steps["toolchain build"]["detail"], "building toolchain v1 for openrouter", p["steps"])
            s = m._squads.settings(m._projects.get("sq"))
            self.assertEqual((s["toolchain"]["apt"], s["toolchain_version"]), (["golang-go", "gcc"], 1))
            self.assertEqual(spawned[0][0], "_build_all")
            self.assertEqual(spawned[0][1][2:], (["openrouter"], s["toolchain"], 1))
            self.assertIn("Your toolchain", woke[-1][1]); self.assertIn("was approved", woke[-1][1])
            st, ts = self._sq("/api/squad/toolchain", B)                        # every member reads it
            self.assertEqual((st, ts["version"], ts["spec"]["apt"], ts["active"]), (200, 1, ["golang-go", "gcc"], False), ts)
            self.assertEqual(ts["templates"][0]["state"], "queued")
            self.assertEqual(self._sq("/api/squad/toolchain", C)[0], 403)
            self.assertIn("toolchain_propose", m._squadgate.LEAD_TOOLS)
            self.assertIn("toolchain_status", m._squadgate.MEMBER_TOOLS)
            self.assertTrue({"toolchain_propose", "toolchain_status"} <= m._policy.AGENT_TOOL_NAMES)
            self.assertIn("/api/squad/toolchain-propose", m._guests.GUEST_POST_PATHS)
            # the operator edits the spec directly: saved = approved, built
            st, r = self._sq("/api/projects/sq/squad", L, {"toolchain": {"apt": ["gcc"]}})
            self.assertEqual((st, r["settings"]["toolchain_version"]), (200, 2), r)
            self.assertEqual(r["toolchain"], "a build is running already")      # the v1 thread (never started here)
            tc._active.discard(m._squads.squad_id(m._projects.get("sq")))
            st, r = self._sq("/api/projects/sq/squad/toolchain", L, {})           # Rebuild
            self.assertEqual((st, r["msg"]), (200, "building toolchain v2 for openrouter"), r)
            import inspect
            self.assertIn("toolchain_propose", m._squadgate.PLAN)
        finally:
            tc._spawn, tc.STATE_FILE = old[0], old[1]
            tc._active.clear(); tc._active.update(old[2])
            undo()

    def _tc_env(self):
        """_squad_env plus a fake base image tree (BASE/instances/<base>-rootfs.ext4),
        the toolchain state, layers and RUN_DIR in a temp dir, network and
        iptables off, a fake Firecracker (`fc["log"]` is what its console
        prints, `fc["exit"]` False = it never exits)."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        tc = m._toolchain
        tmp = tempfile.mkdtemp(prefix="e2e-tcb-")
        os.makedirs(os.path.join(tmp, "instances"))
        for b in tc.BASES.values():
            with open(os.path.join(tmp, b), "wb") as fh:
                fh.write(b"base")
        fc = {"log": "", "exit": True, "args": []}

        class Proc:
            pid = 4242
            def __init__(self, args, stdout=None, stderr=None, start_new_session=False):
                fc["args"].append(args); stdout.write(fc["log"].encode()); stdout.flush(); self.done = fc["exit"]
                fc["conf"] = _readj(args[args.index("--config-file") + 1])
            def poll(self):
                return 0 if self.done else None
            def terminate(self):
                self.done = True
            kill = terminate
            def wait(self, t=None):
                return 0
        old = (m._paths.BASE, m._paths.RUN_DIR, tc.STATE_FILE, tc.LAYER_DIR, tc.UPPER_MB, tc._popen, tc._sleep,
               tc._host_guard, m._netfw.ensure_net_base, m._netfw.setup_tap, m._netfw.teardown_tap, tc._spawn,
               tc.BUILD_TIMEOUT, tc._active.copy(), m._notify.notify_add)
        m._paths.BASE = tmp
        m._paths.RUN_DIR = os.path.join(tmp, "run"); os.makedirs(m._paths.RUN_DIR)
        tc.STATE_FILE, tc.LAYER_DIR, tc.UPPER_MB = os.path.join(tmp, "toolchains.json"), os.path.join(tmp, "instances", "toolchains"), 8
        tc._popen, tc._sleep, tc._host_guard = Proc, lambda s: None, lambda inst, on: None
        m._netfw.ensure_net_base = m._netfw.setup_tap = m._netfw.teardown_tap = lambda *a: None
        spawned = []
        tc._spawn = lambda fn, *a: spawned.append(a)
        m._notify.notify_add = lambda *a, **k: ("n", "ok")
        proj = m._projects.get("sq")
        m._squads.record(proj)                          # the squad id
        m._projects.set_squad("sq", m._squads.normalize_settings({"toolchain": {"apt": ["gcc"]}}, proj["squad"])[0])

        def undo2():
            (m._paths.BASE, m._paths.RUN_DIR, tc.STATE_FILE, tc.LAYER_DIR, tc.UPPER_MB, tc._popen, tc._sleep,
             tc._host_guard, m._netfw.ensure_net_base, m._netfw.setup_tap, m._netfw.teardown_tap, tc._spawn,
             tc.BUILD_TIMEOUT, active, m._notify.notify_add) = old
            tc._active.clear(); tc._active.update(active)
            undo()
        return woke, fc, spawned, undo2

    def test_toolchain_build_ok_fail_timeout_stale(self):
        """A build boots the builder (no secrets on its config disk), installs
        the upper as the layer (cp + e2fsck), records it; a failed build keeps
        its log and the previous layer; a hung builder is killed at the
        timeout; a changed base image makes the layer stale (skipped, rebuilt
        once). The member's VM gets the layer read-only only while it is ready."""
        if not shutil.which("mkfs.ext4", path="/usr/sbin:/sbin:" + os.environ.get("PATH", "")):
            self.skipTest("no mkfs.ext4")
        m = self.m
        woke, fc, spawned, undo = self._tc_env()
        tc = m._toolchain
        try:
            proj = m._projects.get("sq")
            sid, spec = m._squads.squad_id(proj), m._squads.settings(proj)["toolchain"]
            self.assertRegex(sid, r"^[a-z0-9]{16}$")
            self.assertEqual(tc.ensure(proj), "building toolchain v1 for openrouter")
            self.assertEqual(spawned, [(sid, "sq", ["openrouter"], spec, 1)])
            self.assertEqual(tc.ensure(proj), "a build is running already")
            fc["log"] = "[toolchain] build starts\ngcc installed\nKAIM56_TOOLCHAIN_RESULT=ok\n"
            tc._build_all(*spawned[0])                                          # what the thread runs
            st = _readj(tc.STATE_FILE)[sid]
            lay, b = st["layers"]["openrouter"], st["builds"]["openrouter"]
            self.assertEqual((lay["version"], lay["ok"], lay["base_image_id"], b["status"]),
                             (1, True, tc.base_image_id("openrouter"), "ok"))
            self.assertTrue(lay["size"] > 0); self.assertIn("gcc installed", lay["log_tail"])
            path = tc.layer_path(sid, "openrouter", 1)
            self.assertTrue(os.path.exists(path))
            self.assertEqual([f for f in os.listdir(m._paths.RUN_DIR) if f.endswith(".ext4")], [])   # upper + config gone
            args = fc["args"][0]
            conf = args[args.index("--config-file") + 1]
            self.assertFalse(os.path.exists(conf))
            self.assertEqual(tc.ensure(proj), "up to date")
            self.assertTrue(any("Toolchain v1: ready for openrouter" in x["text"] for x in m._squads.read("sq")))
            # the builder's config disk: the build flag and the scripts, nothing of an instance
            files = tc.builder_files(spec, 1)
            self.assertEqual(sorted(files), ["config.env", "toolchain-user.sh", "toolchain.sh"])
            self.assertEqual([ln.split("=")[0] for ln in files["config.env"].splitlines()], ["TOOLCHAIN_BUILD", "GUEST_DNS", "PATH"])
            vmc = fc["conf"]                                                    # the builder's VM: base + config ro, empty upper rw, nothing else
            self.assertEqual([(d["drive_id"], d["is_read_only"]) for d in vmc["drives"]],
                             [("rootfs", True), ("config", True), ("upper", False)])
            self.assertTrue(vmc["boot-source"]["boot_args"].endswith("init=/init fc_upper=/dev/vdc fc_build=1"))
            self.assertEqual(vmc["network-interfaces"][0]["host_dev_name"], "fc250")
            # the member VM: the layer read-only between harness and upper, fc_layer names its device
            inst = next(i for i in m._instances.load_instances() if i["name"] == "b")
            old_mk, old_h = m._vm.make_upper, m._vm.harness_image
            m._vm.make_upper, m._vm.harness_image = (lambda i: "/x/upper.ext4"), (lambda: None)
            try:
                drives = lambda c: [d["drive_id"] for d in c["drives"]]
                c = m._vm.gen_config(inst)
                self.assertEqual(drives(c)[-2:], ["toolchain", "upper"])
                lay_d = c["drives"][-2]
                self.assertEqual((lay_d["path_on_host"], lay_d["is_read_only"]), (path, True))
                self.assertIn(f"fc_layer=/dev/vd{chr(ord('a') + len(c['drives']) - 2)}", c["boot-source"]["boot_args"])
                self.assertEqual(tc.layer_for({**inst, "rootfs": "instances/other.ext4"}), None)
                self.assertEqual(tc.layer_for({**inst, "name": "c"}), None)          # no member
                # fail: the log tail stays, the previous layer stays active
                m._projects.set_squad("sq", m._squads.normalize_settings({"toolchain": {"apt": ["gcc", "make"]}},
                                                                         m._projects.get("sq")["squad"])[0])
                fc["log"] = "E: Unable to locate package make\nKAIM56_TOOLCHAIN_RESULT=fail rc=100\n"
                spawned.clear()
                self.assertEqual(tc.ensure(m._projects.get("sq")), "building toolchain v2 for openrouter")
                tc._build_all(*spawned[0])
                st = _readj(tc.STATE_FILE)[sid]
                self.assertEqual((st["builds"]["openrouter"]["status"], st["layers"]["openrouter"]["version"]), ("failed", 1))
                self.assertIn("rc=100", st["builds"]["openrouter"]["error"])
                self.assertIn("Unable to locate", tc.logs(m._projects.get("sq"))["openrouter"]["log"])
                row = tc.status(m._projects.get("sq"))["templates"][0]
                self.assertEqual((row["state"], row["ready"]), ("failed", True))
                self.assertEqual(m._vm.gen_config(inst)["drives"][-2]["path_on_host"], path)   # v1 still used
                self.assertEqual(tc.ensure(m._projects.get("sq")), "up to date")             # tried once
                # timeout: a builder that never answers is killed
                tc.BUILD_TIMEOUT, fc["exit"], fc["log"] = -1, False, "apt-get update\n"
                spawned.clear()
                self.assertEqual(tc.ensure(m._projects.get("sq"), force=True), "building toolchain v2 for openrouter")
                tc._build_all(*spawned[0])
                self.assertIn("timeout", _readj(tc.STATE_FILE)[sid]["builds"]["openrouter"]["error"])
                # stale: the base image was rebuilt -> no layer, "stale", one automatic rebuild
                base = os.path.join(m._paths.BASE, tc.BASES["openrouter"])
                os.utime(base, (time.time() + 60, time.time() + 60))
                self.assertIsNone(tc.layer_for(inst))
                self.assertNotIn("toolchain", drives(m._vm.gen_config(inst)))
                self.assertNotIn("fc_layer", m._vm.gen_config(inst)["boot-source"]["boot_args"])
                self.assertEqual(tc.status(m._projects.get("sq"))["templates"][0]["state"], "stale")
                spawned.clear()
                self.assertEqual(tc.sweep(), {"sq": "building toolchain v2 for openrouter"})
                tc._active.clear()
                self.assertEqual(tc.sweep(), {})                                    # once per base image
            finally:
                m._vm.make_upper, m._vm.harness_image = old_mk, old_h
        finally:
            undo()

    def test_toolchain_sweep_fails_orphaned_builds(self):
        """A build left 'building' by a manager restart is failed, its leftovers removed."""
        m = self.m
        woke, fc, spawned, undo = self._tc_env()
        tc = m._toolchain
        try:
            sid = m._squads.squad_id(m._projects.get("sq"))
            tc._set_build(sid, "claude", status="building", index=251, version=1, base_image_id=tc.base_image_id("claude"))
            open(os.path.join(m._paths.RUN_DIR, f"tc-{sid}-claude.upper.ext4"), "w").close()
            tc.sweep()
            b = _readj(tc.STATE_FILE)[sid]["builds"]["claude"]
            self.assertEqual(b["status"], "failed"); self.assertIn("restarted", b["error"])
            self.assertEqual(os.listdir(m._paths.RUN_DIR), [])
        finally:
            undo()

    # ---- code indexer (mgr/indexer.py): reindex, hooks, idle stop, the repowise MCP ----
    def _idx_env(self):
        """_squad_env plus an indexer instance 'idx' (index 20, stopped), the
        project folder a git repo, the VM and its HTTP faked (recorded):
        start/stop flip `running`, /health is ok while running, /reindex and
        /mcp answer from `answers`. Runs inline instead of in a thread."""
        m = self.m
        woke, tasks, undo = self._squad_env()
        x = m._indexer
        tmp = os.path.dirname(m._projects.PROJECTS_FILE)
        os.makedirs(os.path.join(tmp, "repo", ".git"), exist_ok=True)
        self._sq_insts.append({"name": "idx", "index": 20, "template": "indexer", "rootfs": "instances/indexer-rootfs.ext4",
                               "mounts": [], "config": {}})
        running, calls, answers = set(), [], {}
        old = (x.STATE_FILE, x._spawn, x._http, x.HEALTH_WAIT, m._vm.start, m._vm.stop, m._instances.is_running,
               m._mounts.apply_live, x._last_use[0])
        x.STATE_FILE = os.path.join(tmp, "indexer.json")
        x._spawn = lambda fn, *a: fn(*a)
        x.HEALTH_WAIT = 0

        def http(inst, path, body=None, timeout=10):
            calls.append((path, body))
            if inst["name"] not in running:
                raise ConnectionRefusedError("down")
            a = answers.get(path, {"ok": True})
            if isinstance(a, list):                      # one answer per call; an exception is raised
                a = a.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        x._http = http
        m._vm.start = lambda inst: running.add(inst["name"]) or "started"
        m._vm.stop = lambda inst: running.discard(inst["name"]) or "stopped"
        m._instances.is_running = lambda inst: inst["name"] in running
        m._mounts.apply_live = lambda *a: None

        def undo2():
            (x.STATE_FILE, x._spawn, x._http, x.HEALTH_WAIT, m._vm.start, m._vm.stop, m._instances.is_running,
             m._mounts.apply_live, x._last_use[0]) = old
            x._pending.clear()
            undo()
        return running, calls, answers, woke, undo2

    def test_indexer_reindex_starts_the_stopped_vm_joins_as_reader_and_posts(self):
        m = self.m
        running, calls, answers, woke, undo = self._idx_env()
        try:
            answers["/reindex"] = {"ok": True, "commit": "abc123"}
            self.assertEqual(m._indexer.reindex("sq", "test"), "reindex queued")
            self.assertEqual(running, {"idx"})                                         # started on demand
            self.assertEqual(calls[-1], ("/reindex", {"project": "sq", "ref": "HEAD"}))
            self.assertEqual(m._projects.get("sq")["members"]["idx"]["role"], "reader")  # ensure_member
            st = m._indexer.project_status("sq")
            self.assertEqual((st["instance"], st["running"], st["member"], st["last"]["ok"], st["last"]["detail"]),
                             ("idx", True, True, True, "abc123"))
            self.assertEqual(m._indexer.ensure_member("sq"), "")                       # idempotent
            answers["/reindex"] = {"ok": False, "error": "git fetch: exit 128"}
            m._indexer.reindex("sq", "again")
            self.assertEqual(m._indexer.project_status("sq")["last"]["detail"], "git fetch: exit 128")
            items = m._projects.load(); items[0]["source"]["path"] = os.path.join(os.path.dirname(m._projects.PROJECTS_FILE), "nogit")
            m._projects._save(items)
            self.assertIn("not a git repository", m._indexer.reindex("sq"))
            m._indexer._pending.add("other")
            self.assertEqual(m._indexer.reindex("nope"), "unknown project")
            # the squad channel never wakes the indexer: @all reaches the agents only
            woke.clear()
            m._squads.post(m._projects.get("sq"), "user:admin", ["all"], "hello")
            self.assertEqual(sorted(w[0] for w in woke), ["a", "b", "d"])
        finally:
            undo()

    def test_indexer_hooks_ticket_done_and_merge_approve(self):
        m = self.m
        running, calls, answers, woke, undo = self._idx_env()
        g = m._squadgate
        old = (g.GATE_FILE, m._projects.member_op, m._util.rate_ok, g._spawn, m._indexer.reindex)
        g.GATE_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-sgi-"), "squadgate.json")
        g._spawn = lambda fn, *a: None
        m._util.rate_ok = lambda *a: True
        m._projects.member_op = lambda n, op, w: ({"commit": "abc123"}, 200)
        fired = []
        m._indexer.reindex = lambda project, why="": fired.append((project, why)) or "reindex queued"
        L = "127.0.0.1"
        try:
            t, why = g._ticket_add("sq", {"key": "T1", "title": "feature", "assignee": "b"}, "admin"), ""
            self.assertEqual(self._sq("/api/projects/sq/squad/ticket", L, {"id": t["id"], "status": "doing"})[0], 200)
            self.assertEqual(fired, [])
            st, d = self._sq("/api/projects/sq/squad/ticket", L, {"id": t["id"], "status": "done"})
            self.assertEqual((st, d["reindex"], fired), (200, "reindex queued", [("sq", "ticket T1 done")]))
            self._sq("/api/projects/sq/squad/ticket", L, {"id": t["id"], "comment": "still done"})
            self.assertEqual(len(fired), 1)                                            # only the step to done
            items = m._projects.load(); items[0]["strategy"] = "worktree"; m._projects._save(items)
            st, d = self._sq("/api/squad/request-merge", "172.30.3.2", {"writer": "b", "summary": "feature done"})
            self.assertEqual(st, 200, d)
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})
            self.assertEqual((st, [s["step"] for s in p["steps"]], fired[-1]), (200, ["merge b", "reindex"], ("sq", "merge b")))
        finally:
            (g.GATE_FILE, m._projects.member_op, m._util.rate_ok, g._spawn, m._indexer.reindex) = old
            undo()

    def test_indexer_idle_stop_after_30_minutes(self):
        m = self.m
        running, calls, answers, woke, undo = self._idx_env()
        x = m._indexer
        try:
            self.assertEqual(x.idle_sweep(), "")                                       # stopped: nothing to do
            running.add("idx")
            x._last_use[0] = 1000.0
            self.assertEqual(x.idle_sweep(now=1000.0 + 29 * 60), "")
            x._pending.add("sq")
            self.assertEqual(x.idle_sweep(now=1000.0 + 31 * 60), "")                   # a run is queued
            x._pending.clear()
            self.assertEqual(x.idle_sweep(now=1000.0 + 31 * 60), "stopped")
            self.assertEqual(running, set())
        finally:
            undo()

    def test_indexer_mcp_on_demand_start_retry_and_membership(self):
        """/api/mcp for 'repowise': initialize and the cached tool list without
        a boot; a call starts the stopped indexer; a failed call restarts and
        retries once; the project must be one of the caller's; other servers
        still go to the hub."""
        m = self.m
        running, calls, answers, woke, undo = self._idx_env()
        old = (m._mcp.load_mcps, m._mcp.MCP_HUB)
        m._mcp.load_mcps = lambda: [{"name": "repowise", "command": "kaim56-indexer", "args": []},
                                    {"name": "browser", "command": "mcp-remote", "args": ["http://x/mcp"]}]
        m._mcp.MCP_HUB = "http://127.0.0.1:9"                                          # no hub in this test
        b = next(i for i in self._sq_insts if i["name"] == "b")
        b["config"]["MCP_SERVERS"] = "repowise,browser"
        call = lambda server, payload: m._mcp.mcp_hub_call(dict(b), server, payload)
        tc = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_overview", "arguments": {}}}
        try:
            m._indexer.ensure_member("sq")
            st, out = call("repowise", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual((st, out["result"]["serverInfo"]["name"], running, calls), (200, "kaim56-indexer", set(), []))
            self.assertEqual(call("repowise", {"jsonrpc": "2.0", "method": "notifications/initialized"}), (200, {}))
            answers["/mcp"] = [{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "get_overview", "inputSchema": {
                "type": "object", "properties": {"project": {"type": "string"}}}}]}}]
            st, out = call("repowise", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(out["result"]["tools"][0]["inputSchema"]["properties"]["project"]["enum"], ["sq"])
            running.clear(); n = len(calls)
            st, out = call("repowise", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
            self.assertEqual((st, len(calls), running), (200, n, set()))                # cached: no boot
            answers["/mcp"] = [{"jsonrpc": "2.0", "id": 7, "result": {"content": []}}]
            st, out = call("repowise", tc)                                              # stopped -> started
            self.assertEqual((st, out["id"], running), (200, 7, {"idx"}))
            self.assertEqual(calls[-1][1]["params"]["arguments"], {"project": "sq"})     # the one project filled in
            answers["/mcp"] = [ConnectionRefusedError("vm gone"), {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}]
            st, out = call("repowise", tc)                                              # retried once
            self.assertEqual((st, [c[0] for c in calls[-3:]]), (200, ["/mcp", "/health", "/mcp"]))
            answers["/mcp"] = [ConnectionRefusedError("1"), ConnectionRefusedError("2")]
            self.assertEqual(call("repowise", tc)[0], 502)                             # and only once
            n = len(calls)
            bad = dict(tc, params={"name": "get_overview", "arguments": {"project": "other"}})
            st, out = call("repowise", bad)
            self.assertEqual((st, len(calls)), (200, n)); self.assertIn("project 'other'", out["error"]["message"])
            c = next(i for i in self._sq_insts if i["name"] == "c")                    # no member of sq
            c["config"]["MCP_SERVERS"] = "repowise"
            st, out = m._mcp.mcp_hub_call(dict(c), "repowise", tc)
            self.assertIn("none", out["error"]["message"])
            running.clear(); n = len(calls)
            st, out = call("browser", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            self.assertEqual((st, len(calls), running), (502, n, set()))               # the hub path, untouched
            self.assertEqual(m._mcp.mcp_hub_call(dict(b, config={}), "repowise", tc)[0], 403)   # not assigned
        finally:
            m._mcp.load_mcps, m._mcp.MCP_HUB = old
            undo()

    def test_indexer_reindex_route_is_admin_only_and_template_defaults(self):
        m = self.m
        running, calls, answers, woke, undo = self._idx_env()
        try:
            self.assertTrue(m._routes.ROUTER.resolve("POST", "/api/projects/sq/squad/reindex")[1])
            self.assertTrue(m._routes.ROUTER.resolve("GET", "/api/projects/sq/squad/index")[1])
            self.assertFalse(m._auth.user_allowed("POST", "/api/projects/sq/squad/reindex"))
            self.assertFalse(m._auth.user_allowed("GET", "/api/projects/sq/squad/index"))
            st, d = self._sq("/api/projects/sq/squad/reindex", "127.0.0.1", {})
            self.assertEqual((st, d["msg"], calls[-1][0]), (200, "reindex queued", "/reindex"))
            st, d = self._sq("/api/projects/sq/squad/index", "127.0.0.1")
            self.assertEqual((st, d["instance"], d["last"]["ok"]), (200, "idx", True))
            self.assertEqual(self._sq("/api/projects/sq/squad/reindex", "172.30.4.2", {})[0], 403)   # a guest
        finally:
            undo()
        # a folder template may ask for a persistent disk and no internet
        tpl = os.path.join(os.path.dirname(FC_DIR), "agents", "indexer", "template.json")
        if os.path.exists(tpl):
            t = _readj(tpl)
            self.assertEqual((t["vcpus"], t["mem_mib"], t["overlay"], t["persist_disk"], t["internet"]), (4, 4096, True, True, False))
            saved, old = [], (m._instances.load_templates, m._instances.load_instances, m._instances.save_instance)
            m._instances.load_templates = lambda: [t]
            m._instances.load_instances = lambda: []
            m._instances.save_instance = saved.append
            try:
                m._instances.create_instance("idx", "indexer")
                self.assertEqual((saved[0]["persist_disk"], saved[0]["internet"], saved[0]["mem_mib"]), (True, False, 4096))
            finally:
                m._instances.load_templates, m._instances.load_instances, m._instances.save_instance = old

    def test_indexer_dashboard_route_admin_only_starts_indexer_and_proxies(self):
        """On INDEX_HOST: /indexer/open/<p> starts the stopped indexer and the
        dashboard, then redirects to /indexer/ui; /indexer/ui/... is relayed
        unchanged (method, path, query, body) without the browser's credentials,
        upstream cookies dropped; counts as use. Admin only: not for guests, not
        for role user. (Who gets onto INDEX_HOST: test_index_host_isolation.)"""
        import http.server
        m = self.m
        x = m._indexer
        running, calls, answers, woke, undo = self._idx_env()
        seen = []

        class D(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.command, self.path, dict(self.headers), self.rfile.read(n)))
                out = b"<html>dash</html>"
                self.send_response(308 if self.path == "/indexer/ui/" else 200)
                self.send_header("Location", "/indexer/ui")
                self.send_header("Set-Cookie", "x=1")
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), D)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = (x.DASH_PORT, m._instances.net_of, m._settings.INDEX_HOST)
        x.DASH_PORT = srv.server_address[1]
        m._instances.net_of = lambda inst: {"guest": "127.0.0.1"}
        m._settings.INDEX_HOST = "index.example.com"
        L = "127.0.0.1"
        admin = m._oidc.Principal("a1", "Admin", "", "admin")

        def on_index(h, method="GET"):                   # past _auth_index with a dash session
            h.headers["Host"] = "index.example.com"
            h.principal = admin
            self.assertTrue(h._dispatch(method))
            return h
        try:
            for meth, path in (("GET", "/indexer/open/sq"), ("GET", "/indexer/ui/repos/r"), ("POST", "/indexer/ui/api/x")):
                self.assertTrue(m._routes.ROUTER.resolve(meth, path)[1])                 # admin: no guest
                self.assertFalse(m._auth.user_allowed(meth, path))                       # no role user
            h = self._handler("/indexer/ui/repos/r", "172.30.4.2")                      # a guest
            h._do_GET()
            self.assertEqual((self._status(h), seen), (403, []))
            h = self._handler("/indexer/ui/repos/r", L)
            h.headers["Host"] = "index.example.com"
            h.principal = m._oidc.Principal("u1", "User", "", "user")
            self.assertTrue(h._dispatch("GET"))
            self.assertEqual((self._status(h), seen), (403, []))

            h = on_index(self._handler("/indexer/ui", L))                               # indexer stopped
            self.assertEqual((self._status(h), seen), (503, []))
            answers["/dashboard"] = {"ok": True, "project": "sq", "port": 7341}
            x._last_use[0] = 0
            h = on_index(self._handler("/indexer/open/sq", L))
            self.assertEqual(self._status(h), 302)
            self.assertIn(b"Location: /indexer/ui\r\n", h.wfile.getvalue())
            self.assertEqual((running, calls[-1]), ({"idx"}, ("/dashboard", {"project": "sq"})))
            self.assertGreater(x._last_use[0], 0)                                       # a use

            x._last_use[0] = 0
            h = self._handler("/indexer/ui/repos/r?tab=1", L, auth="Basic eDp5")
            h.headers["Cookie"] = "__Host-kaim56_dash=secret"
            h.headers["RSC"] = "1"
            on_index(h)
            raw = h.wfile.getvalue()
            self.assertEqual(self._status(h), 200)
            self.assertTrue(raw.endswith(b"<html>dash</html>")); self.assertNotIn(b"Set-Cookie", raw)
            meth, path, hdrs, _ = seen[-1]
            self.assertEqual((meth, path, hdrs.get("RSC")), ("GET", "/indexer/ui/repos/r?tab=1", "1"))
            self.assertFalse({"Authorization", "Cookie"} & set(hdrs))
            self.assertGreater(x._last_use[0], 0)
            h = on_index(self._handler("/indexer/ui/", L))
            self.assertEqual(self._status(h), 308)                                      # redirects pass through
            h = on_index(self._post_handler("/indexer/ui/api/search", L, b'{"q":"x"}'), "POST")
            self.assertEqual((self._status(h), seen[-1][0], seen[-1][1], seen[-1][3]),
                             (200, "POST", "/indexer/ui/api/search", b'{"q":"x"}'))
            h = on_index(self._handler("/indexer/other", L))
            self.assertEqual(self._status(h), 404)

            answers["/dashboard"] = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": "not indexed here (yet)"}'))
            h = on_index(self._handler("/indexer/open/sq", L))
            self.assertEqual(self._status(h), 503); self.assertIn(b"not indexed here", h.wfile.getvalue())
            h = on_index(self._handler("/indexer/open/nope", L))
            self.assertIn(b"unknown project", h.wfile.getvalue())
        finally:
            x.DASH_PORT, m._instances.net_of, m._settings.INDEX_HOST = old
            srv.shutdown(); srv.server_close()
            undo()

    # ---- Jev setup suggestions (mgr/jevsetup.py), per-instance SKILLS ----------------
    def _fake_jev(self):
        """A Jev on a free port: entailment by substring; records each call's
        hypotheses and premise. -> (url, calls, stop)."""
        import http.server
        calls = []
        score = {"coder fits": 0.9, "assistant fits": 0.6, "writing or fixing code": 0.92, "moderate difficulty": 0.8,
                 "needs std": 0.99, "needs sk10": 0.3, "needs sk11": 0.49}
        score.update({f"needs sk{i}:": 0.99 - i * 0.01 for i in range(10)})

        class J(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(b)
                out = json.dumps({"entail": [next((v for k, v in score.items() if k in h), 0.05)
                                             for h in b["hypotheses"]], "ms": 5}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), J)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def stop():
            srv.shutdown(); srv.server_close()
        return f"http://127.0.0.1:{srv.server_address[1]}", calls, stop

    def _jev_env(self, url):
        m = self.m
        old = (m._settings.load_settings, m._personas.load_personas, m._skills.load_skills, m._router.load)
        m._settings.load_settings = lambda: {"JEV_URL": url} if url else {}
        m._personas.load_personas = lambda: [{"name": "assistant", "prompt": "You are a helpful agent. Be brief."},
                                             {"name": "coder", "prompt": "You are an experienced software developer. Test."},
                                             {"name": "code-architect", "prompt": "## Process\n- study the code"}]
        m._skills.load_skills = lambda: ([{"name": "std", "description": "standard", "default": True}]
                                         + [{"name": f"sk{i}", "description": f"skill {i}"} for i in range(12)])
        m._router.load = lambda: {"tiers": {"openrouter/qwen/coder": "code", "orcarouter/tencent/hy3": "strong"},
                                  "policies": {"default": m._router.DEFAULT_POLICY}}

        def undo():
            (m._settings.load_settings, m._personas.load_personas, m._skills.load_skills, m._router.load) = old
        return undo

    def test_jevsetup_suggest_maps_persona_skills_tier(self):
        """Best persona above the floor; skills above the floor, at most 8, no
        standard skill; the router questions through the default rules to a
        tier and its first concrete model; calls of <= 16 short hypotheses;
        the purpose capped. Jev down or not configured -> a note, no raise."""
        js = self.m._jevsetup
        url, calls, stop = self._fake_jev()
        undo = self._jev_env(url)
        try:
            s = js.suggest("write and fix the backend code " + "x" * 3000)
            self.assertEqual(s["persona"], {"name": "coder", "score": 0.9})
            self.assertEqual([x["name"] for x in s["skills"]], [f"sk{i}" for i in range(8)])
            self.assertEqual((s["tier"], s["model"], s["model_key"]), ({"name": "code", "score": 0.92}, "qwen/coder", "OPENROUTER_MODEL"))
            self.assertEqual(s["classes"], {"task": ["code", 0.92], "difficulty": ["moderate", 0.8]})
            hyps = [h for c in calls for h in c["hypotheses"]]
            self.assertTrue(all(len(c["hypotheses"]) <= 16 for c in calls) and all(len(h) <= 100 for h in hyps))
            self.assertEqual(len(hyps), 8 + 3 + 12)                         # router + personas + non-standard skills
            self.assertFalse(any("needs std" in h for h in hyps))
            self.assertIn("A code architect fits this task.", hyps)            # no prose sentence: the name only
            self.assertTrue(all(len(c["premise"]) <= 1000 for c in calls))
            self.assertEqual(js.suggest("")["note"], "no purpose given")
        finally:
            stop(); undo()
        for jev, note in ((url, "Jev unreachable"), ("", "Jev is not configured")):   # stopped server, then none
            undo = self._jev_env(jev)
            try:
                s = js.suggest("write code")
                self.assertTrue(s["note"].startswith(note), s["note"])
                self.assertEqual((s["persona"], s["skills"], s["tier"], s["model"]), (None, [], None, ""))
            finally:
                undo()

    def test_setup_suggest_route_admin_only_and_create_stores_skills(self):
        """/api/setup/suggest and /squad/suggest: admin only (no guest, no
        role user). /api/create and the config setter take only catalog
        skills into SKILLS; a guest's /api/skills?default=1 lists its own
        SKILLS (SQUAD_SKILLS as the fallback) next to the standard skills."""
        m = self.m
        url, calls, stop = self._fake_jev()
        undo = self._jev_env(url)
        g = {"name": "g", "index": 9, "config": {"SKILLS": "sk3"}}
        made, saved = [], []
        old = (m._guests.instance_by_ip, m._instances.create_instance, m._instances.load_instances, m._instances.save_instance)
        m._guests.instance_by_ip = lambda ip: g if ip == "172.30.9.2" else None
        m._instances.create_instance = lambda n, t, cfg=None, *a, **k: made.append(cfg) or f"instance '{n}' created"
        m._instances.load_instances = lambda: [g]
        m._instances.save_instance = saved.append
        try:
            for path in ("/api/setup/suggest", "/api/projects/sq/squad/suggest"):
                self.assertTrue(m._routes.ROUTER.resolve("POST", path)[1], path)
                self.assertFalse(m._auth.user_allowed("POST", path), path)
            h = self._post_handler("/api/setup/suggest", "172.30.9.2", b'{"purpose": "code"}'); h._do_POST()
            self.assertEqual(self._status(h), 403)
            self.assertEqual(calls, [])
            h = self._post_handler("/api/setup/suggest", "127.0.0.1", b'{"purpose": "write code"}'); h._do_POST()
            d = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual((self._status(h), d["persona"]["name"], d["model"]), (200, "coder", "qwen/coder"))
            for skills, want in ((["sk1", "nope"], "error: unknown skill(s): nope"), (["sk1", "sk2"], "created")):
                h = self._post_handler("/api/create", "127.0.0.1", json.dumps(
                    {"name": "n", "template": "openrouter", "skills": skills}).encode()); h._do_POST()
                self.assertIn(want, json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["msg"])
            self.assertEqual([c.get("SKILLS") for c in made], ["sk1,sk2"])
            self.assertTrue(m._instances._set_config_key("g", "SKILLS", "sk1,bad").startswith("error: unknown skill(s): bad"))
            self.assertEqual(saved, [])
            self.assertTrue(m._instances._set_config_key("g", "SKILLS", "sk1, sk2").startswith("SKILLS = "))
            for cfg, want in (({"SKILLS": "sk3"}, ["std", "sk3"]), ({"SQUAD_SKILLS": "sk4"}, ["std", "sk4"]), ({}, ["std"])):
                g["config"] = cfg
                for ip, names in (("172.30.9.2", want), ("127.0.0.1", ["std"])):
                    h = self._handler("/api/skills?default=1", ip); h._do_GET()
                    self.assertEqual([x["name"] for x in json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])], names)
        finally:
            (m._guests.instance_by_ip, m._instances.create_instance, m._instances.load_instances,
             m._instances.save_instance) = old
            stop(); undo()

    def test_squadgate_members_get_jev_suggestions(self):
        """A new member without persona/tier gets Jev's persona/tier only
        from 0.8 (skills never — they and the rest stay hints), with the squad
        goal as context; a member with persona + tier or an existing one is
        left alone; 'Suggest again' replaces one member's all three (admin);
        nothing is created before Approve, then the member gets SKILLS."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        g = m._squadgate
        A, L = "172.30.3.2", "127.0.0.1"
        sugg = {"persona": {"name": "coder", "score": 0.91}, "skills": [{"name": "tdd", "score": 0.88}],
                "tier": {"name": "cheap", "score": 0.77}, "model": "acme/fast", "model_key": "OPENROUTER_MODEL",
                "classes": {}, "ms": 12, "note": "n"}
        asked = []
        old = (m._jevsetup.suggest, m._router.jev_url)
        m._jevsetup.suggest = lambda purpose, context="", allow_security=False, be=None: asked.append((purpose, context)) or json.loads(json.dumps(sugg))
        m._router.jev_url = lambda: "http://jev"
        g._spawn = lambda fn, *a: fn(*a)                                       # synchronous here
        try:
            plan = dict(self._SG_PLAN, members=self._SG_PLAN["members"] + [{"name": "h", "purpose": "write docs"}])
            st, d = self._sq("/api/squad/propose", A, plan)
            self.assertEqual(st, 200, d)
            self.assertEqual(asked, [("write docs", "Squad goal: ship v2")])    # e, f complete; b existing
            p = g._get("sq", d["id"])
            h = next(x for x in p["members"] if x["name"] == "h")
            self.assertEqual((h["persona"], h["skills"], h["model_tier"]), ("coder", [], ""))   # 0.77 < 0.8: no tier
            self.assertEqual(h["suggested"], {"note": "n", "ms": 12, "persona": 0.91, "hint": {
                "skills": {"tdd": 0.88}, "tier": {"name": "cheap", "score": 0.77}}})
            self.assertEqual(h["by"], {"persona": "jev (0.91)", "template": "default"})       # who chose it, on the card
            self.assertNotIn("suggested", next(x for x in p["members"] if x["name"] == "e"))
            self.assertEqual(created, [])
            st, d2 = self._sq("/api/projects/sq/squad/suggest", L, {"id": d["id"], "member": "f"})
            self.assertEqual(st, 200, d2)
            f = next(x for x in d2["members"] if x["name"] == "f")
            self.assertEqual((f["persona"], f["skills"], f["model_tier"], f["suggested"]["persona"]), ("coder", ["tdd"], "cheap", 0.91))
            self.assertEqual(self._sq("/api/projects/sq/squad/suggest", L, {"id": d["id"], "member": "b"})[0], 400)
            self.assertEqual(self._sq("/api/projects/sq/squad/suggest", A, {"id": d["id"], "member": "f"})[0], 403)
            self.assertEqual(created, [])
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})
            self.assertEqual(st, 200, p)
            self.assertEqual(dict((c[0], c[2].get("SKILLS")) for c in created), {"e": "tdd", "f": "tdd", "h": None})
            self.assertEqual(self._sq("/api/projects/sq/squad/suggest", L, {"id": d["id"], "member": "h"})[0], 400)
        finally:
            m._jevsetup.suggest, m._router.jev_url = old
            undo()

    def test_squadgate_jev_never_gives_a_writer_a_lead_persona(self):
        """code-architect / orchestrator stay a hint for non-leads even at a
        high score (live: a CLI dev got code-architect at 0.96)."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        g = m._squadgate
        sugg = {"persona": {"name": "code-architect", "score": 0.96}, "skills": [], "tier": None, "model": "",
                "model_key": "", "classes": {}, "ms": 1, "note": "n"}
        old = (m._jevsetup.suggest, m._router.jev_url)
        m._jevsetup.suggest = lambda purpose, context="", allow_security=False, be=None: json.loads(json.dumps(sugg))
        m._router.jev_url = lambda: "http://jev"
        g._spawn = lambda fn, *a: fn(*a)
        try:
            plan = dict(self._SG_PLAN, members=self._SG_PLAN["members"] + [{"name": "h", "purpose": "build the CLI"}])
            st, d = self._sq("/api/squad/propose", "172.30.3.2", plan)
            self.assertEqual(st, 200, d)
            h = next(x for x in g._get("sq", d["id"])["members"] if x["name"] == "h")
            self.assertEqual(h["persona"], "")
            self.assertEqual(h["suggested"]["hint"]["persona"]["name"], "code-architect")
        finally:
            m._jevsetup.suggest, m._router.jev_url = old
            undo()

    # ---- repo profile (mgr/repoprofile.py): what the squad's choices are based on ------
    def _rp_node(self, root):
        """A cfdo-like fixture: a Worker with a Durable Object binding, a CLI
        bin, a README with a badge; node_modules and a symlinked manifest to
        be ignored."""
        _wtextp(os.path.join(root, "package.json"), json.dumps({
            "name": "cfdo", "bin": {"cfdo": "bin/cfdo.js"}, "scripts": {"build": "tsc", "test": "vitest"},
            "dependencies": {"hono": "4"}, "devDependencies": {"wrangler": "3", "typescript": "5", "@types/node": "1"}}))
        _wtextp(os.path.join(root, "wrangler.toml"), 'name = "x"\n[[durable_objects.bindings]]\nname = "ROOM"\n')
        _wtextp(os.path.join(root, "README.md"), "# cfdo\n\n[![ci](https://x/b.svg)](https://x)\n\n"
               "Tooling for **Cloudflare Durable Objects**: deploy, inspect and back up `objects` from a [CLI](https://x).\n\nMore.\n")
        for f in ("src/index.ts", "src/room.ts", "bin/cfdo.js"):
            _wtextp(os.path.join(root, f), "export {}\n")
        _wtextp(os.path.join(root, "Dockerfile"), "FROM node\n")
        _wtextp(os.path.join(root, ".github", "workflows", "ci.yml"), "on: push\n")
        for i in range(30):
            _wtextp(os.path.join(root, "node_modules", "react", f"f{i}.py"), "x\n")
        _wtextp(os.path.join(root, "node_modules", "react", "package.json"), '{"dependencies": {"nm-dep": "1"}}')
        outside = tempfile.mkdtemp()
        _wtextp(os.path.join(outside, "package.json"), '{"dependencies": {"evil": "1"}}')
        _wtextp(os.path.join(outside, "many.py"), "x\n")
        os.makedirs(os.path.join(root, "pkg"))
        os.symlink(os.path.join(outside, "package.json"), os.path.join(root, "pkg", "package.json"))
        os.symlink(outside, os.path.join(root, "linked"))
        return root

    def test_repoprofile_fixtures_bounded_and_cached(self):
        """Node + wrangler (Durable Objects), Python pyproject, go.mod: the
        languages, frameworks, entry points and keywords; node_modules
        skipped, symlinks (dir and manifest) never followed, MAX_FILES
        bounds the scan; the per-project cache holds while HEAD does."""
        import subprocess as sp
        rp = self.m._repoprofile
        tmp = tempfile.mkdtemp()
        p = rp.build(self._rp_node(os.path.join(tmp, "node")))
        for k in ("Durable Objects", "Cloudflare Workers", "TypeScript", "Hono", "Docker", "GitHub Actions"):
            self.assertIn(k, p["keywords"], p["keywords"])
        self.assertEqual(p["languages"], [["TypeScript", 2], ["JavaScript", 1]])     # no node_modules, no linked/
        self.assertNotIn("evil", p["deps"]); self.assertNotIn("nm-dep", p["deps"]); self.assertNotIn("Python", p["keywords"])
        self.assertIn("bin cfdo", p["entry_points"]); self.assertIn("npm scripts: build, test", p["entry_points"])
        self.assertEqual(p["readme"], "Tooling for Cloudflare Durable Objects: deploy, inspect and back up objects from a CLI.")
        self.assertIn("Cloudflare Durable Objects", p["keywords"])                   # a proper name from the README
        self.assertNotIn("types/node", " ".join(p["keywords"]))
        self.assertLessEqual(len(p["keywords"]), 25)
        txt = rp.text(p)
        self.assertTrue(txt.startswith("Repo profile:\n- keywords: ")); self.assertIn("- README: Tooling", txt)
        py = os.path.join(tmp, "py")
        _wtextp(os.path.join(py, "pyproject.toml"), '[project]\nname = "t"\ndependencies = ["fastapi>=0.1", "pydantic[email]"]\n'
               '[project.scripts]\nt = "t:main"\n')
        _wtextp(os.path.join(py, "t", "main.py"), "x = 1\n")
        p = rp.build(py)
        self.assertEqual((p["languages"], p["deps"]), ([["Python", 1]], ["fastapi", "pydantic"]))
        self.assertTrue({"FastAPI", "Pydantic", "Python"} <= set(p["keywords"])); self.assertIn("script t", p["entry_points"])
        go = os.path.join(tmp, "go")
        _wtextp(os.path.join(go, "go.mod"), "module example.com/tool\n\ngo 1.22\n\nrequire (\n\tgithub.com/spf13/cobra v1.8.0\n)\n")
        _wtextp(os.path.join(go, "cmd", "tool", "main.go"), "package main\n")
        p = rp.build(go)
        self.assertTrue({"CLI", "Go", "cobra"} <= set(p["keywords"]), p); self.assertIn("cmd/tool", p["entry_points"])
        old = rp.MAX_FILES
        rp.MAX_FILES = 3
        try:
            self.assertTrue(rp.build(os.path.join(tmp, "node"))["truncated"])
        finally:
            rp.MAX_FILES = old
        # cache by HEAD
        old = rp.CACHE_FILE
        rp.CACHE_FILE = os.path.join(tmp, "cache.json")
        g = lambda *a: sp.run(["git", "-C", py, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
        try:
            g("init", "-q"); g("add", "-A"); g("commit", "-q", "-m", "one")
            self.assertNotIn("Go", rp.get("t", py)["keywords"])
            self.assertEqual(os.stat(rp.CACHE_FILE).st_mode & 0o777, 0o600)
            _wtextp(os.path.join(py, "x.go"), "package x\n")
            self.assertNotIn("Go", rp.get("t", py)["keywords"])                     # same HEAD: the cache
            g("add", "-A"); g("commit", "-q", "-m", "two")
            self.assertIn("Go", rp.get("t", py)["keywords"])                        # new HEAD: recomputed
            self.assertIsNone(rp.of_project({"name": "k", "source": {"type": "katfs"}}))
        finally:
            rp.CACHE_FILE = old

    def test_jevsetup_context_reaches_premise_and_prefilter(self):
        """suggest(purpose, context): the premise is purpose first, then the
        context, within the cap; the word pre-filter also matches the context,
        so durable-objects reaches Jev's hypotheses for a cfdo-like repo it
        never did from "Implement the web UI." alone."""
        m = self.m
        url, calls, stop = self._fake_jev()
        undo = self._jev_env(url)
        sk = m._skills.load_skills() + [{"name": "durable-objects", "description": "Cloudflare Durable Objects: state, alarms"}]
        m._skills.load_skills = lambda: sk
        prof = m._repoprofile.build(self._rp_node(tempfile.mkdtemp()))
        try:
            hyps = lambda: [h for c in calls for h in c["hypotheses"]]
            m._jevsetup.suggest("Implement the web UI.")
            self.assertFalse([h for h in hyps() if "durable objects" in h])
            calls.clear()
            ctx = "Repository: " + m._repoprofile.context(prof) + ". Squad goal: " + "x " * 800
            m._jevsetup.suggest("Implement the web UI.", ctx)
            self.assertTrue([h for h in hyps() if h.startswith("This task needs durable objects")], hyps())
            self.assertTrue(all(c["premise"].startswith("Implement the web UI. Context: Repository: Cloudflare Workers, Durable Objects")
                                and len(c["premise"]) <= m._jevsetup.PURPOSE_MAX for c in calls))
        finally:
            stop(); undo()

    # ---- decision backend (mgr/decision.py): local LLM / SubQ, Jev fallback ----
    def _fake_llm(self, answers, delay=0, status=200):
        """An OpenAI-compatible chat-completions server: answers with
        {"answers": answers} as the message content (in a code fence, after a
        think block). Records (path, headers, body). -> (base, calls, stop)."""
        import http.server
        calls = []

        class L(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, dict(self.headers), b))
                time.sleep(delay)
                content = "<think>hm</think>```json\n" + json.dumps({"answers": answers}) + "\n```"
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(out))); self.end_headers()
                try:
                    self.wfile.write(out)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), L)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def stop():
            srv.shutdown(); srv.server_close()
        return f"http://127.0.0.1:{srv.server_address[1]}", calls, stop

    _DEC_ANSWERS = {"persona": {"coder": 1.7, "ghost": 0.99, "assistant": 0.1},
                    "tier": {"code": 0.8, "huge": 0.9},
                    "skills": {**{f"sk{i}": 0.9 - i * 0.05 for i in range(8)}, "std": 0.99, "nope": 0.95, "sk11": -2}}

    def _dec_env(self, jev, extra, base):
        """_jev_env plus decision settings; the OpenRouter upstream and the
        Azure https check pointed at the local fake."""
        m = self.m
        undo1 = self._jev_env(jev)
        st = {"JEV_URL": jev, "OPENROUTER_API_KEY": "sk-or-SECRETKEY1",
              "LLAMA_API_KEY": "llama-SECRETKEY3", "LLAMA_ENDPOINT": base + "/v1", **extra}
        old = (dict(m._llmproxy.LLM_PROXY_UPSTREAMS), m._settings.validate_decision)
        m._settings.load_settings = lambda: dict(st)
        m._llmproxy.LLM_PROXY_UPSTREAMS["openrouter"] = (base + "/api/v1/chat/completions", "OPENROUTER_API_KEY")
        m._settings.validate_decision = lambda d: ""            # the fake is http, not https

        def undo():
            m._llmproxy.LLM_PROXY_UPSTREAMS.clear(); m._llmproxy.LLM_PROXY_UPSTREAMS.update(old[0])
            m._settings.validate_decision = old[1]
            undo1()
        return st, undo

    def test_decision_backends_suggest_with_full_catalogs(self):
        """local-llm: ONE request with every persona and
        every non-standard skill (no pre-filter), the tiers, purpose and
        context; the key in the header (each backend its own); unknown names
        dropped, scores clamped, at most 6 skills; same shape as Jev's."""
        m = self.m
        base, calls, stop = self._fake_llm(self._DEC_ANSWERS)
        want = {
                "local-llm": ("/v1/chat/completions", "Authorization", "Bearer llama-SECRETKEY3", "qwen-local")}
        try:
            for be, (path, hdr, val, model) in want.items():
                calls.clear()
                st, undo = self._dec_env("", {"DECISION_BACKEND": be, "DECISION_LOCAL_MODEL": "qwen-local"}, base)
                try:
                    s = m._jevsetup.suggest("write and fix the backend code", "Repository: Cloudflare Workers. Squad goal: ship")
                finally:
                    undo()
                self.assertEqual(len(calls), 1, be)
                p, h, b = calls[0]
                self.assertEqual((p, {k.lower(): v for k, v in h.items()}[hdr.lower()], b["model"]), (path, val, model), be)
                task = json.loads(b["messages"][1]["content"])
                self.assertEqual(sorted(task["questions"]["skills"]["criteria"]), sorted(f"sk{i}" for i in range(12)))
                self.assertEqual(sorted(task["questions"]["persona"]["criteria"]), ["assistant", "code-architect", "coder"])
                self.assertEqual(sorted(task["questions"]["tier"]["criteria"]), ["code", "strong"])
                self.assertIn("write and fix the backend code", task["state"]); self.assertIn("Cloudflare Workers", task["state"])
                self.assertEqual(s["persona"], {"name": "coder", "score": 1.0}, be)                 # ghost dropped, clamped
                self.assertEqual([x["name"] for x in s["skills"]], [f"sk{i}" for i in range(6)])   # std/nope dropped, max 6
                self.assertTrue(all(0 <= x["score"] <= 1 for x in s["skills"]))
                self.assertEqual((s["tier"], s["model"], s["model_key"], s["backend"]),
                                 ({"name": "code", "score": 0.8}, "qwen/coder", "OPENROUTER_MODEL", be))
                self.assertEqual(set(s) - {"backend"}, set(m._jevsetup.empty("x")) - {"backend"})
                self.assertEqual(be == "local-llm", "chat_template_kwargs" in b)
        finally:
            stop()
        # choose(): a generic choice, unknown option dropped
        base, calls, stop = self._fake_llm({"q": {"code": 0.8, "strong": 0.2, "huge": 1}})
        st, undo = self._dec_env("", {"DECISION_BACKEND": "local-llm"}, base)
        try:
            r = m._decision.choose("Which tier?", {"code": "code model", "strong": "big"}, "fix a bug")
            self.assertEqual(r, [{"option": "code", "score": 0.8}, {"option": "strong", "score": 0.2}])
        finally:
            undo(); stop()

    def test_decision_subq_native_api(self):
        """subq: SubQ's /v1/systemone with the Bearer key; a choice stays a
        choice (probabilities as a map or a list), a multi becomes one noul per
        option, at most 16 questions per request; a squad's own backend wins
        over Settings (New squad -> Suggestions); no key -> Jev, noted."""
        m = self.m
        calls, real = [], urllib.request.urlopen

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=None):
            b = json.loads(req.data)
            calls.append((req.full_url, req.get_header("Authorization"), b))
            ans = {}
            for k, q in b["questions"].items():
                if q["type"] == "noul":
                    ans[k] = {"type": "noul", "noul": 0.9 - int(k.split(".")[1]) * 0.05}
                elif k == "tier":
                    ans[k] = {"type": "choice", "choice": "code", "probabilities": [0.8, 0.2][:len(q["criteria"])]}
                else:
                    ans[k] = {"type": "choice", "choice": "coder", "probabilities": {"coder": 0.9, "assistant": 0.1, "ghost": 1}}
            return Resp(json.dumps({"model": b["model"], "answers": ans}).encode())
        st, undo = self._dec_env("", {"DECISION_BACKEND": "jev", "SUBQ_API_KEY": "subq-SECRETKEY4"}, "http://127.0.0.1:9")
        urllib.request.urlopen = fake
        try:
            s = m._jevsetup.suggest("write and fix the backend code", "goal: ship", be="subq")
            self.assertEqual(s["backend"], "subq", s.get("note"))
            self.assertTrue(len(calls) >= 1 and all(len(c[2]["questions"]) <= 16 for c in calls))
            url, auth, b = calls[0]
            self.assertEqual((url, auth, b["model"]), ("https://api.subq.ai/v1/systemone", "Bearer subq-SECRETKEY4", "subq-snap-preview"))
            self.assertEqual(b["questions"]["persona"]["type"], "choice")
            self.assertIn("write and fix the backend code", b["state"])
            self.assertTrue(all(q["type"] == "noul" for k, q in b["questions"].items() if k.startswith("skills.")))
            self.assertEqual(s["persona"], {"name": "coder", "score": 0.9})                     # ghost dropped
            self.assertEqual([x["name"] for x in s["skills"]], [f"sk{i}" for i in range(6)])   # the best 6
            self.assertEqual(s["tier"]["score"], 0.8)
            # 1 option -> noul; 20 multi options -> 2 requests
            calls.clear()
            ans, _ = m._decision.ask({"state": "x", "questions": {
                "one": {"type": "choice", "instructions": "i", "criteria": {"only": ""}},
                "many": {"type": "multi", "instructions": "i", "max": 3, "criteria": {f"o{i}": "" for i in range(20)}}}}, "subq")
            self.assertEqual(len(calls), 2); self.assertEqual(calls[0][2]["questions"]["one.0"]["type"], "noul")
            self.assertEqual((ans["one"], list(ans["many"])), ({"only": 0.9}, ["o0", "o1", "o2"]))
            # the global setting stays Jev; no key -> Jev's (empty) suggestion with a note, no key leaked
            st.pop("SUBQ_API_KEY")
            s = m._jevsetup.suggest("write code", be="subq")
            self.assertIn("SUBQ_API_KEY not configured", s["note"]); self.assertEqual(s["backend"], "jev")
            # the squad's switch
            self.assertEqual(m._squads.normalize_settings({"decision": "subq"})[0]["decision"], "subq")
            self.assertIn("decision must be", m._squads.normalize_settings({"decision": "gpt"})[1])
            self.assertIsNone(m._squadgate.decision_of({"name": "x", "squad": {}}))
            self.assertEqual(m._squadgate.decision_of({"name": "x", "squad": {"decision": "subq"}}), "subq")
            self.assertTrue(m._decision.ready("subq"))
            self.assertIn("SUBQ_API_KEY", {x["key"] for x in m._settings.SETTINGS_SCHEMA})
            self.assertTrue(m._settings.is_secret_setting("SUBQ_API_KEY"))
        finally:
            urllib.request.urlopen = real
            undo()

    def test_decision_failure_falls_back_to_jev_without_leaking_keys(self):
        """HTTP error, garbage and timeout -> Jev's suggestion, noted; no key
        in the note, the log line or the /api/decision/test answer; the route
        is admin only."""
        import contextlib
        import io
        m = self.m
        jev, _, stopj = self._fake_jev()
        dec = m._decision
        old_t, old_ip = (dec.TIMEOUT, dec.LOCAL_TIMEOUT), m._guests.instance_by_ip
        m._guests.instance_by_ip = lambda ip: {"name": "g", "index": 9, "config": {}} if ip == "172.30.9.2" else None
        try:
            for kind, srv in (("http", self._fake_llm({}, status=500)), ("garbage", self._fake_llm("nope")),
                              ("timeout", self._fake_llm(self._DEC_ANSWERS, delay=1.5))):
                base, calls, stop = srv
                dec.TIMEOUT = dec.LOCAL_TIMEOUT = 0.5
                st, undo = self._dec_env(jev, {"DECISION_BACKEND": "local-llm"}, base)
                err = io.StringIO()
                try:
                    with contextlib.redirect_stderr(err):
                        s = m._jevsetup.suggest("write and fix the backend code")
                        h = self._post_handler("/api/decision/test", "127.0.0.1", b"{}"); h._do_POST()
                        out = h.wfile.getvalue()
                    hg = self._post_handler("/api/decision/test", "172.30.9.2", b"{}"); hg._do_POST()
                finally:
                    undo(); stop()
                self.assertEqual((s["backend"], s["persona"]["name"]), ("jev", "coder"), kind)
                self.assertTrue(s["note"].startswith("local-llm failed"), s["note"])
                d = json.loads(out.split(b"\r\n\r\n", 1)[1])
                self.assertEqual((self._status(h), d["backend"], d["used"]), (200, "local-llm", "jev"))
                self.assertIn("Jev used", err.getvalue())
                for blob in (s["note"], err.getvalue(), out.decode()):
                    self.assertFalse(any(k in blob for k in ("SECRETKEY1", "SECRETKEY2", "SECRETKEY3")), kind)
                self.assertEqual(self._status(hg), 403)
            self.assertFalse(m._auth.user_allowed("POST", "/api/decision/test"))
            self.assertEqual(dec.safe_err(ValueError("bad sk-or-SECRETKEY1"), {"OPENROUTER_API_KEY": "sk-or-SECRETKEY1"}),
                             "ValueError: bad ***")
        finally:
            (dec.TIMEOUT, dec.LOCAL_TIMEOUT), m._guests.instance_by_ip = old_t, old_ip
            stopj()

    def test_decision_router_only_when_switched_on(self):
        """DECISION_ROUTER off (or backend jev) -> the router never asks the
        decision backend; on -> one request with the policy's classes, Jev on
        failure."""
        m = self.m
        rt, dec = m._router, m._decision
        asked, jevd = [], []
        old = (dec.ask, rt.jev_classify, m._settings.load_settings)
        dec.ask = lambda task, be=None: asked.append(task) or (
            {"task": {"code": 0.9, "chat": 0.1}, "difficulty": {"hard": 0.7}}, 12)
        rt.jev_classify = lambda pol, text: jevd.append(text) or ({"task": ("chat", 0.6)}, 3)
        try:
            for st in ({"DECISION_BACKEND": "local-llm"}, {"DECISION_BACKEND": "jev", "DECISION_ROUTER": "1"},
                       {"DECISION_ROUTER": "1"}):
                m._settings.load_settings = lambda st=st: st
                self.assertEqual(rt.classify(rt.DEFAULT_POLICY, "fix it"), ({"task": ("chat", 0.6)}, 3))
            self.assertEqual(asked, [])
            m._settings.load_settings = lambda: {"DECISION_BACKEND": "local-llm", "DECISION_ROUTER": "1"}
            self.assertEqual(rt.classify(rt.DEFAULT_POLICY, "fix it"), ({"task": ("code", 0.9), "difficulty": ("hard", 0.7)}, 12))
            self.assertEqual(asked[0]["state"], "fix it")
            self.assertEqual(asked[0]["questions"]["task"]["criteria"], rt.DEFAULT_POLICY["questions"]["task"])
            dec.ask = lambda task, be=None: (_ for _ in ()).throw(TimeoutError("slow"))
            import contextlib
            import io
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rt.classify(rt.DEFAULT_POLICY, "fix it"), ({"task": ("chat", 0.6)}, 3))
        finally:
            dec.ask, rt.jev_classify, m._settings.load_settings = old

    def test_decision_settings_validated_and_key_masked(self):
        m = self.m
        tmp = tempfile.mkdtemp()
        old = m._paths.SETTINGS_FILE
        m._paths.SETTINGS_FILE = os.path.join(tmp, "settings.json")
        try:
            for bad in ({"DECISION_BACKEND": "gpt"}, {"DECISION_ROUTER": "yes"}, {"DECISION_LOCAL_MODEL": "a b"},
                        {"DECISION_BACKEND": "decision1-azure"}, {"DECISION_BACKEND": "decision1-openrouter"}):   # removed 2026-10-15
                self.assertTrue(m._settings.save_settings(bad).startswith("error: "), bad)
            self.assertFalse(os.path.exists(m._paths.SETTINGS_FILE))
            ok = {"DECISION_BACKEND": "subq", "DECISION_ROUTER": "", "DECISION_LOCAL_MODEL": "qwen-local",
                  "SUBQ_API_KEY": "subq-SECRETKEY4"}
            self.assertEqual(m._settings.save_settings(ok), "saved")
            self.assertEqual(m._settings.settings_for_ui()["SUBQ_API_KEY"], m._settings.SETTINGS_KEEP)
            self.assertNotIn("SUBQ_API_KEY", m._settings.SECRET_PARAMS)     # never released to a VM
            self.assertEqual(m._decision.backend(), "subq")
            keys = {s["key"] for s in m._settings.SETTINGS_SCHEMA}
            self.assertTrue({"DECISION_BACKEND", "SUBQ_API_KEY", "DECISION_LOCAL_MODEL", "DECISION_ROUTER"} <= keys)
            self.assertFalse(keys & {"DECISION_MODEL", "AZURE_DECISION_ENDPOINT", "AZURE_DECISION_KEY"})     # Decision-1 removed
        finally:
            m._paths.SETTINGS_FILE = old

    def test_squadgate_profile_in_catalog_proposal_and_kickoff(self):
        """The project's repo profile is in squad_catalog (and its text), in a
        squad proposal (what the card shows) and in the kickoff goal message;
        Jev gets its keywords as context. skills.meta lists a name once."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        src = m._projects.get("sq")["source"]["path"]
        self._rp_node(src)
        asked, old = [], (m._jevsetup.suggest, m._router.jev_url)
        m._jevsetup.suggest = lambda purpose, context="", allow_security=False, be=None: asked.append(context) or m._jevsetup.empty("x")
        m._router.jev_url = lambda: "http://jev"
        m._squadgate._spawn = lambda fn, *a: fn(*a)
        try:
            st, d = self._sq("/api/squad/catalog?project=sq", "172.30.3.2")
            self.assertEqual(st, 200, d)
            self.assertIn("Durable Objects", d["profile"]["keywords"]); self.assertTrue(d["profile_text"].startswith("Repo profile:"))
            plan = dict(self._SG_PLAN, members=[{"name": "h", "purpose": "Implement the web UI."}], tickets=[])
            st, d = self._sq("/api/squad/propose", "172.30.3.2", plan)
            self.assertEqual(st, 200, d)
            self.assertIn("Durable Objects", m._squadgate._get("sq", d["id"])["profile"]["frameworks"])
            self.assertTrue(asked[0].startswith("Repository: Cloudflare Workers, Durable Objects"), asked)
            self.assertTrue(asked[0].endswith("Squad goal: ship v2"))
        finally:
            m._jevsetup.suggest, m._router.jev_url = old
            undo()
        sk = [{"name": "a", "description": "1"}, {"name": "b"}, {"name": "a", "description": "2"}]
        self.assertEqual([(x["name"], x["description"]) for x in m._skills.meta(sk)], [("a", "1"), ("b", "")])

    # ---- squad catalog, new personas, claude members, provenance -------------------
    def _cat_env(self):
        """_gate_env + both member templates, a persona list upsert_persona
        writes to (recorded), an anthropic-less strong tier."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        pers = [{"name": "coder", "prompt": "You code. Carefully.", "tools": ["bash"]},
                {"name": "writer-x", "description": "Writes things. Well.", "prompt": "You write."}]
        ups, old = [], m._personas.upsert_persona
        m._instances.load_templates = lambda: [{"template": "openrouter"}, {"template": "claude"}, {"template": "orcarouter"}]
        m._personas.load_personas = lambda: pers
        m._skills.load_skills = lambda: [{"name": "tdd", "description": "test first"}, {"name": "std", "description": "s", "default": True}]
        m._router.load = lambda: {"tiers": {"openrouter/acme/fast": "cheap", "openrouter/acme/big": "strong"}, "policies": {}}

        def upsert(name, prompt, tools=None, model=None, description=None):
            ups.append((name, prompt, tools, description))
            pers.append({"name": name, "prompt": prompt, "description": description})
            return f"persona '{name}' saved"
        m._personas.upsert_persona = upsert

        def undo2():
            m._personas.upsert_persona = old
            undo()
        return created, pers, ups, undo2

    def test_squad_catalog_member_only_and_content(self):
        """squad_catalog: guests of the project only (a non-member and the
        host are refused); personas with a one-line description (and tools),
        skills with the standard ones flagged, tiers with their models, the
        member templates with a note. Read-only."""
        m = self.m
        created, pers, ups, undo = self._cat_env()
        try:
            self.assertEqual(self._sq("/api/squad/catalog", "172.30.5.2")[0], 403)          # c: no member
            self.assertEqual(self._sq("/api/squad/catalog", "127.0.0.1")[0], 403)           # not a guest
            st, d = self._sq("/api/squad/catalog?project=sq", "172.30.3.2")
            self.assertEqual(st, 200, d)
            self.assertEqual(d["personas"], [{"name": "coder", "description": "You code.", "tools": ["bash"]},
                                             {"name": "writer-x", "description": "Writes things."}])
            self.assertEqual(d["skills"], [{"name": "tdd", "description": "test first", "default": False, "category": "coding"},
                                           {"name": "std", "description": "s", "default": True, "category": "coding"}])
            self.assertEqual({t["tier"]: t["models"] for t in d["tiers"]},
                             {"cheap": ["openrouter/acme/fast"], "strong": ["openrouter/acme/big"], "code": [], "local": []})
            self.assertEqual([t["template"] for t in d["templates"]], ["openrouter", "claude"])
            self.assertIn("subscription", d["templates"][1]["note"]); self.assertIn("budget", d["templates"][0]["note"])
            self.assertIn("squad_catalog", m._squadgate.LEAD_TOOLS)
            self.assertIn("squad_catalog", m._policy.tool_names())
            self.assertEqual((created, ups), ([], []))
        finally:
            undo()

    def test_squadgate_new_persona_claude_member_and_provenance(self):
        """A member may bring new_persona (validated; an existing name refused
        for the lead); template is openrouter or claude; nothing is created
        before Approve; then the persona is created — a name taken meanwhile
        is used, never overwritten. A claude member gets TRANSPORT=web, the
        tier as ANTHROPIC_MODEL (alias without an anthropic/* model; a tier
        with none keeps the account default, noted) and AGENT_SYSTEM. Each
        field records who chose it: architect, then you for an edit."""
        m = self.m
        created, pers, ups, undo = self._cat_env()
        A, L = "172.30.3.2", "127.0.0.1"
        npx = {"name": "doc-writer", "description": "Writes docs", "prompt": "You write docs."}
        good = [{"name": "h", "purpose": "docs", "template": "claude", "model_tier": "strong", "skills": ["tdd"],
                 "new_persona": npx},
                {"name": "i", "template": "claude", "model_tier": "local", "persona": "coder"},
                {"name": "j", "persona": "coder", "new_persona": {"name": "rev2", "prompt": "You review (new)."}}]
        try:
            for mem, why in (({"name": "e", "new_persona": {"name": "coder", "prompt": "x"}}, "exists"),
                             ({"name": "e", "new_persona": {"name": "Bad_N", "prompt": "x"}}, "a-z 0-9"),
                             ({"name": "e", "new_persona": {"name": "ok", "prompt": "x" * 7000}}, "prompt"),
                             ({"name": "e", "new_persona": {"name": "ok", "prompt": "x", "tools": ["nope"]}}, "tools unknown"),
                             ({"name": "e", "new_persona": "x"}, "an object"),
                             ({"name": "e", "template": "orcarouter"}, "openrouter or claude")):
                st, d = self._sq("/api/squad/propose", A, {"goal": "g", "members": [mem]})
                self.assertEqual(st, 400, mem); self.assertIn(why, d["error"])
            st, d = self._sq("/api/squad/propose", A, {"goal": "g", "members": good})
            self.assertEqual(st, 200, d)
            self.assertEqual(d["warnings"], [])
            prop = self._sq("/api/projects/sq/squad/board", L)[1]["proposals"][0]          # the card's data
            h, i, j = prop["members"]
            self.assertEqual((h["persona"], h["new_persona"], h["template"]), ("doc-writer", npx, "claude"))
            self.assertEqual(h["by"], {"persona": "architect", "new_persona": "architect", "skills": "architect",
                                       "model_tier": "architect", "template": "architect"})
            self.assertEqual(j["by"]["template"], "default")
            self.assertEqual((created, ups), ([], []))                                       # only on approve
            pers.append({"name": "rev2", "prompt": "You review (old)."})                     # taken meanwhile
            i2 = dict(i, skills=["tdd"])                                                      # the operator's edit
            st, p = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True, "members": [h, i2, j]})
            self.assertEqual(st, 200, p)
            self.assertEqual(ups, [("doc-writer", "You write docs.", None, "Writes docs")])   # rev2 not overwritten
            self.assertEqual([x for x in pers if x["name"] == "rev2"], [{"name": "rev2", "prompt": "You review (old)."}])
            steps = {s["step"]: s for s in p["steps"]}
            self.assertIn("exists already", steps["persona rev2"]["detail"])
            self.assertTrue(steps["persona doc-writer"]["ok"])
            self.assertIn("account default", steps["model i"]["detail"])
            cfg = {c[0]: (c[1], c[2]) for c in created}
            self.assertEqual(sorted(cfg), ["h", "i", "j"])
            tpl, ch = cfg["h"]
            self.assertEqual((tpl, ch["TRANSPORT"], ch["ANTHROPIC_MODEL"], ch["SKILLS"]), ("claude", "web", "opus", "tdd"))
            self.assertNotIn("OPENROUTER_MODEL", ch)
            self.assertTrue(ch["AGENT_SYSTEM"].startswith("You write docs.")); self.assertIn("writer in the squad of project sq", ch["AGENT_SYSTEM"])
            self.assertEqual(cfg["i"][0], "claude"); self.assertNotIn("ANTHROPIC_MODEL", cfg["i"][1])
            self.assertIn("squad_post", cfg["i"][1]["AGENT_TOOLS"])                           # coder's tool subset + squad tools
            self.assertTrue(cfg["j"][1]["AGENT_SYSTEM"].startswith("You review (old)."))
            self.assertEqual(cfg["j"][0], "openrouter")
            by = {x["name"]: x["by"] for x in p["members"]}
            self.assertEqual((by["i"]["skills"], by["i"]["persona"], by["h"]["persona"]), ("you", "architect", "architect"))
            st, p2 = self._sq("/api/projects/sq/squad/decide", L, {"id": d["id"], "approve": True})   # retry: nothing again
            self.assertEqual((st, len(ups), len(created)), (200, 1, 3))
            # claude, explicit model: anthropic/<id> -> ANTHROPIC_MODEL; an API model is no Claude model
            steps = []
            self.assertEqual(m._squadgate._model_cfg({"name": "x", "template": "claude", "model": "anthropic/claude-opus-5",
                                                      "model_tier": ""}, steps), {"ANTHROPIC_MODEL": "claude-opus-5"})
            self.assertEqual(m._squadgate._model_cfg({"name": "x", "template": "claude", "model": "acme/big",
                                                      "model_tier": ""}, steps), {})
            self.assertIn("no Claude model", steps[0]["detail"])
        finally:
            undo()

    def test_squad_requirements_engineer_hands_over_and_retires(self):
        """While drafting, the ephemeral engineer may be woken before approval and
        gets the operator's unaddressed messages; the board shows its
        REQUIREMENTS.md; on agree the lead gets the plan and the document in its
        workspace (never through a planted link), the engineer leaves the squad
        and its instance is deleted."""
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        L = "127.0.0.1"
        old_root, old_del = m._mounts.AGENT_ROOT, m._instances.delete_instance
        m._mounts.AGENT_ROOT = tempfile.mkdtemp(prefix="e2e-eng-")
        gone = []
        m._instances.delete_instance = lambda n: gone.append(n) or f"instance '{n}' deleted"
        try:
            m._squads.save_settings("sq", {**m._squads.settings(m._projects.get("sq")), "requirements": "drafting", "engineer": "d"})
            st, d = self._sq("/api/projects/sq/squad/post", L, {"text": "a desktop app"})
            self.assertEqual((st, d["to"], woke[-1][0]), (200, ["d"], "d"), d)          # not approved, yet d is woken
            for w in ("a", "d"):
                os.makedirs(os.path.join(m._mounts.AGENT_ROOT, w))
            with open(os.path.join(m._mounts.AGENT_ROOT, "d", "REQUIREMENTS.md"), "w") as fh:
                fh.write("# Goal\nA desktop app.\n")
            os.symlink("/etc/hostname", os.path.join(m._mounts.AGENT_ROOT, "a", "REQUIREMENTS.md"))   # the lead planted a link
            r = self._sq("/api/projects/sq/squad/board", L)[1]["requirements"]
            self.assertEqual((r["engineer"], r["text"].splitlines()[1]), ("d", "A desktop app."))
            st, d = self._sq("/api/projects/sq/squad/requirements", L, {})
            self.assertEqual((st, d["state"], d["engineer"]), (200, "agreed", ""), d)
            self.assertEqual(woke[-1][0], "a"); self.assertIn("squad_propose", woke[-1][1])
            self.assertNotIn("at the top of your workspace", woke[-1][1])               # the link was not followed
            self.assertEqual(open("/etc/hostname").read() != "# Goal\nA desktop app.\n\n", True)
            p = m._projects.get("sq")
            self.assertNotIn("d", p["members"]); self.assertEqual(gone, ["d"])
            self.assertEqual(m._squads.settings(p)["engineer"], "")
            # without a planted link the document lands in the lead's workspace
            os.remove(os.path.join(m._mounts.AGENT_ROOT, "a", "REQUIREMENTS.md"))
            self.assertTrue(sys.modules["mgr.squadreq"]._hand_over(p, "# Goal\nX"))
            with open(os.path.join(m._mounts.AGENT_ROOT, "a", "REQUIREMENTS.md")) as fh:
                self.assertEqual(fh.read(), "# Goal\nX\n")
        finally:
            m._mounts.AGENT_ROOT, m._instances.delete_instance = old_root, old_del
            undo()

    def test_squadkick_on_an_existing_repo(self):
        """"+ New squad" on a repo from the Repos tab: no clone, no new project —
        the architect joins as lead, the squad is enabled in the requirements
        phase and the goal goes to the architect; a repo with a squad, an
        unknown repo and an empty goal are refused."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        L, k = "127.0.0.1", m._squadkick
        try:
            st, d = self._sq("/api/squads/kickoff", L, {"project": "sq", "text": "add tests"})
            self.assertEqual(st, 400); self.assertIn("has a squad already", d["error"])
            p = m._projects.get("sq")
            m._projects.set_squad("sq", m._squads.normalize_settings({"enabled": False}, p.get("squad"))[0])
            self.assertIn("unknown repo", self._sq("/api/squads/kickoff", L, {"project": "nope", "text": "x"})[1]["error"])
            self.assertIn("say what", self._sq("/api/squads/kickoff", L, {"project": "sq", "text": " "})[1]["error"])
            before = [x["name"] for x in m._projects.load()]
            st, j = self._sq("/api/squads/kickoff", L, {"project": "sq", "text": "add tests"})
            self.assertEqual(st, 200, j)
            self.assertEqual(k.get(j["id"])["status"], "done", k.get(j["id"])["log"])
            self.assertEqual([x["name"] for x in m._projects.load()], before)                  # no new project
            self.assertFalse(any("clone" in a for a, _env in calls))                           # no clone
            self.assertEqual([c[0] for c in created], ["sq-req"])                             # its lead a plans; an engineer asks
            self.assertEqual(woke[-1][0], "sq-req"); self.assertIn("add tests", woke[-1][1])
            # without a lead: an architect instance joins as the lead
            items = m._projects.load(); items[0]["members"].pop("a"); items[0]["squad"] = {}; m._projects._save(items)
            st, j = self._sq("/api/squads/kickoff", L, {"project": "sq", "text": "add tests", "team": " - coder: x ", "decision": "subq"})
            self.assertEqual(k.get(j["id"])["status"], "done", k.get(j["id"])["log"])
            self.assertEqual([c[0] for c in created], ["sq-req", "sq-architect"])           # the engineer is reused
            s = m._squads.settings(m._projects.get("sq"))
            self.assertEqual((s["enabled"], s["requirements"]), (True, "drafting"))
            self.assertEqual(s["team"], "- coder: x")                                         # the form's Team box
            self.assertEqual(s["decision"], "subq")                                           # the Suggestions switch
            self.assertIn("decision must be", self._sq("/api/squads/kickoff", L, {"project": "sq", "text": "x", "decision": "gpt"})[1]["error"])
            self.assertEqual(k._team(None), m._squads.TEAM_DEFAULT)                            # API without it: the default roles
            for r in ("project-designer", "architect", "devops", "agent-ops", "code-planner", "asset-planner", "coder"):
                self.assertIn("- " + r, m._squads.TEAM_DEFAULT)
            self.assertIn("code-planner", sys.modules["mgr.ui_html"].HTML_TOP.split("id=sk-team rows")[1][:2000])
            self.assertEqual(m._projects.get("sq")["members"]["sq-architect"]["role"], "lead")
            self.assertEqual(woke[-1][0], "sq-req"); self.assertIn("add tests", woke[-1][1])
        finally:
            undo()

    def test_squadkick_claude_architect(self):
        """architect_template=claude: the architect is a claude instance (web,
        tier strong -> ANTHROPIC_MODEL, lead tools incl. squad_catalog); the
        goal message asks for squad_catalog first; another template is refused."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        L = "127.0.0.1"
        m._instances.load_templates = lambda: [{"template": "openrouter"}, {"template": "claude"}]
        try:
            st, d = self._sq("/api/squads/kickoff", L, {"text": "https://github.com/own/repo x", "architect_template": "llama"})
            self.assertEqual(st, 400); self.assertIn("architect_template", d["error"])
            st, j = self._sq("/api/squads/kickoff", L, {"text": "https://github.com/own/repo add dark mode",
                                                        "architect_template": "claude"})
            self.assertEqual((st, j["arch_template"]), (200, "claude"), j)
            self.assertEqual(m._squadkick.get(j["id"])["status"], "done", m._squadkick.get(j["id"])["log"])
            name, tpl, cfg = created[0][:3]
            self.assertEqual((name, tpl, cfg["TRANSPORT"], cfg["ANTHROPIC_MODEL"]), ("repo-architect", "claude", "web", "opus"))
            self.assertNotIn("OPENROUTER_MODEL", cfg)
            self.assertTrue({"squad_catalog", "squad_propose"} <= set(cfg["AGENT_TOOLS"].split(",")))
            self.assertIn("ONE question at a time", woke[0][1]); self.assertIn("/project/repo", woke[0][1])
            self.assertEqual(m._squads.settings(m._projects.get("repo"))["requirements"], "drafting")
            for x in ("Call squad_catalog", "new_persona", "(openrouter or claude)"):     # the plan comes after agreement
                self.assertIn(x, m._squadgate.PLAN)
        finally:
            undo()

    # ---- checkout: a GitHub repo into an instance's workspace ------------------------
    def test_checkout_parse_repo(self):
        """Every GitHub spelling is accepted, nothing else."""
        co = self.m._checkout
        for text, want in (("acme/shop", ("acme", "shop", "")), ("https://github.com/acme/shop.git", ("acme", "shop", "")),
                           ("github.com/Acme/Shop/tree/feature/x", ("Acme", "Shop", "feature/x")),
                           ("git@github.com:acme/shop.git", ("acme", "shop", "")), ("https://www.github.com/acme/shop/", ("acme", "shop", ""))):
            self.assertEqual(co.parse_repo(text), want, text)
        for bad in ("shop", "https://gitlab.com/acme/shop", "https://evil.com/github.com/acme/shop", "acme/..",
                    "acme/shop/tree/../../x", "acme/shop/tree/-rf", "-x/shop", "file:///etc/passwd", ""):
            with self.assertRaises(ValueError, msg=bad):
                co.parse_repo(bad)

    def test_checkout_github_meta_messages(self):
        """The GitHub API pre-check: size/private/default branch mapped; a 404
        tells whether a token is missing or lacks access; the token goes as a
        Bearer header, never in the URL."""
        import urllib.error, io
        co = self.m._checkout
        seen = []
        old = co._http_get_json
        try:
            co._http_get_json = lambda url, h: (seen.append((url, dict(h))), {"full_name": "acme/shop", "private": True, "size": 2048, "default_branch": "main"})[1]
            self.assertEqual(co.github_meta("acme", "shop", "tok123"),
                             ({"full_name": "acme/shop", "private": True, "size_kb": 2048, "default_branch": "main"}, ""))
            self.assertEqual(seen[0][0], "https://api.github.com/repos/acme/shop"); self.assertEqual(seen[0][1]["Authorization"], "Bearer tok123")
            def raise_(code):
                def f(url, h):
                    raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
                return f
            co._http_get_json = raise_(404)
            self.assertIn("GITHUB_TOKEN in the Secrets tab", co.github_meta("acme", "shop", "")[1])
            self.assertIn("token has no read access", co.github_meta("acme", "shop", "t")[1])
            co._http_get_json = raise_(401)
            self.assertIn("refused the token", co.github_meta("acme", "shop", "t")[1])
        finally:
            co._http_get_json = old

    def test_checkout_clones_hardened_into_the_workspace(self):
        """start(): pre-check, then git as a hardened subprocess in a private
        staging folder with the token only in its environment; only the files
        (no .git) land in <workspace>/src; status walks running -> done; a new
        checkout replaces src; a failure is reported without the token; bad
        repos, unknown instances and oversized repos are refused up front."""
        import subprocess as _sp, base64 as _b64
        m, co = self.m, self.m._checkout
        tmp = tempfile.mkdtemp(prefix="e2e-co-")
        agent_root, stage = os.path.join(tmp, "agent"), os.path.join(tmp, "stage")
        os.makedirs(agent_root); os.makedirs(stage)
        TOK = "ghp_" + "S3CR3T" * 6
        calls, fail, files = [], [False], [["app/api.py", "print(1)\n"]]
        def fake_run(argv, cwd=None, env=None, **kw):
            calls.append((list(argv), dict(env or {}), cwd))
            verb = [a for a in argv[1:] if a != "-c" and not a.startswith(("core.", "protocol.", "submodule."))][0]
            if verb == "clone":
                if fail[0]:
                    return _sp.CompletedProcess(argv, 128, "", f"fatal: could not read https://x-access-token:{TOK}@github.com")
                dst = os.path.join(cwd, argv[-1]); os.makedirs(os.path.join(dst, ".git"))
                for f, body in files:
                    os.makedirs(os.path.dirname(os.path.join(dst, f)), exist_ok=True); open(os.path.join(dst, f), "w").write(body)
                return _sp.CompletedProcess(argv, 0, "", "")
            return _sp.CompletedProcess(argv, 0, "abc123def4567890\n" if verb == "rev-parse" else "", "")
        saved = (co.STAGE_ROOT, co.github_meta, co.token, co._spawn, _sp.run, m._mounts.AGENT_ROOT, m._instances.load_instances)
        try:
            co.STAGE_ROOT, m._mounts.AGENT_ROOT = stage, agent_root
            co.github_meta = lambda o, r, t: ({"full_name": f"{o}/{r}", "private": True, "size_kb": 900, "default_branch": "main"}, "")
            co.token = lambda: TOK
            co._spawn = lambda fn, *a: fn(*a)
            _sp.run = fake_run
            m._instances.load_instances = lambda: [{"name": "repo-shop", "index": 5}]
            ok, st = co.start("repo-shop", "https://github.com/acme/shop")
            self.assertTrue(ok, st); self.assertEqual(st["status"], "running"); self.assertEqual(st["repo"], "acme/shop")
            st = co.status("repo-shop")
            self.assertEqual((st["status"], st["commit"], st["private"], st["branch"]), ("done", "abc123def4567890", True, "main"))
            ws = os.path.join(agent_root, "repo-shop")
            self.assertTrue(os.path.isfile(os.path.join(ws, "src", "app", "api.py")))
            self.assertFalse(os.path.exists(os.path.join(ws, "src", ".git")))                     # no .git reaches the VM
            self.assertEqual(os.listdir(stage), [])                                               # staging cleaned up
            clone = next(c for c in calls if "clone" in c[0])
            argv, env, cwd = clone
            self.assertTrue(cwd.startswith(stage)); self.assertIn("https://github.com/acme/shop.git", argv)
            for flag in ("core.hooksPath=/dev/null", "protocol.file.allow=never", "core.symlinks=false", "--no-recurse-submodules"):
                self.assertIn(flag, argv)
            self.assertTrue(all(TOK not in a for c in calls for a in c[0]), "token in argv")
            self.assertEqual(env["GIT_CONFIG_VALUE_0"], "AUTHORIZATION: basic " + _b64.b64encode(f"x-access-token:{TOK}".encode()).decode())
            self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_TERMINAL_PROMPT"]), ("/dev/null", "0"))
            self.assertFalse([c for c in calls if (c[2] or "").startswith(ws)])                    # git never ran in the workspace
            # a new checkout replaces src (a file of the old one is gone)
            files[:] = [["README.md", "x\n"]]
            self.assertTrue(co.start("repo-shop", "acme/shop")[0])
            self.assertEqual(os.listdir(os.path.join(ws, "src")), ["README.md"])
            # a failed clone: failed, token nowhere in the error, old src kept
            fail[0] = True
            co.start("repo-shop", "acme/shop")
            st = co.status("repo-shop")
            self.assertEqual(st["status"], "failed"); self.assertNotIn(TOK, st["error"]); self.assertIn("git:", st["error"])
            self.assertEqual(os.listdir(os.path.join(ws, "src")), ["README.md"])
            # refusals before anything runs
            n = len(calls)
            self.assertIn("unknown instance", co.start("nope", "acme/shop")[1]["error"])
            self.assertIn("only GitHub", co.start("repo-shop", "https://gitlab.com/a/b")[1]["error"])
            co.github_meta = lambda o, r, t: ({"full_name": "acme/huge", "private": False, "size_kb": co.REPO_MAX_KB + 1, "default_branch": "main"}, "")
            self.assertIn("over the", co.start("repo-shop", "acme/huge")[1]["error"])
            self.assertEqual(len(calls), n)
            co._jobs["repo-shop"] = {"status": "running"}
            co.github_meta = lambda o, r, t: ({"full_name": "acme/shop", "private": False, "size_kb": 1, "default_branch": "main"}, "")
            self.assertIn("already running", co.start("repo-shop", "acme/shop")[1]["error"])
            self.assertEqual(co.status("never-checked-out"), {"status": "none"})
        finally:
            (co.STAGE_ROOT, co.github_meta, co.token, co._spawn, _sp.run, m._mounts.AGENT_ROOT, m._instances.load_instances) = saved
            co._jobs.pop("repo-shop", None)

    def test_checkout_routes_are_admin_only(self):
        """GET/POST /api/checkout/<instance>: the admin gets JSON (with whether a
        GitHub token is configured), a refusal is a 400 with the reason, a VM
        gets nothing."""
        m = self.m
        old = m._checkout.status, m._checkout.token, m._checkout.start, m._guests.instance_by_ip
        try:
            m._checkout.status = lambda n: {"status": "done", "repo": "acme/shop"}
            m._checkout.token = lambda: ""
            m._checkout.start = lambda n, text: (False, {"status": "failed", "error": "only GitHub repositories (github.com/owner/repo)"})
            m._guests.instance_by_ip = lambda ip: {"name": "vm"} if ip == "172.30.1.2" else None
            h = self._handler("/api/checkout/repo-shop", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 200); self.assertIn(b'"token": false', h.wfile.getvalue()); self.assertIn(b'"status": "done"', h.wfile.getvalue())
            h = self._post_handler("/api/checkout/repo-shop", "10.0.0.9", json.dumps({"repo": "gitlab.com/a/b"}).encode()); h._do_POST()
            self.assertEqual(self._status(h), 400); self.assertIn(b"only GitHub", h.wfile.getvalue())
            h = self._handler("/api/checkout/repo-shop", "172.30.1.2"); h._do_GET()
            self.assertNotEqual(self._status(h), 200)
            h = self._post_handler("/api/checkout/repo-shop", "172.30.1.2", json.dumps({"repo": "a/b"}).encode()); h._do_POST()
            self.assertNotEqual(self._status(h), 200)
        finally:
            m._checkout.status, m._checkout.token, m._checkout.start, m._guests.instance_by_ip = old

    def test_workspace_read_route_is_read_only_and_fenced(self):
        """/api/workspace/<inst>/<path>: a folder lists, a file streams with its
        type; traversal, dotfiles, symlinks out, unknown instances -> 404; VMs
        never (admin only); nothing is ever written."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-ws-")
        ws = os.path.join(tmp, "agent", "jr"); os.makedirs(os.path.join(ws, "runs"))
        open(os.path.join(ws, "runs", "2026-09-26.json"), "w").write('[{"title": "CTO"}]')
        open(os.path.join(ws, ".secret"), "w").write("x"); open(os.path.join(tmp, "outside"), "w").write("host")
        os.symlink(os.path.join(tmp, "outside"), os.path.join(ws, "link"))
        old = m._mounts.AGENT_ROOT, m._instances.load_instances, m._guests.instance_by_ip
        try:
            m._mounts.AGENT_ROOT = os.path.join(tmp, "agent")
            m._instances.load_instances = lambda: [{"name": "jr", "index": 2}]
            m._guests.instance_by_ip = lambda ip: {"name": "jr", "index": 2} if ip == "172.30.2.2" else None
            kind, lst, _ = m._wsfiles.read("jr", "")
            self.assertEqual(kind, "dir"); self.assertEqual([e["name"] for e in lst], ["link", "runs"])   # no dotfiles listed
            self.assertEqual(m._wsfiles.read("jr", "runs")[1][0]["name"], "2026-09-26.json")
            kind, data, ct = m._wsfiles.read("jr", "runs/2026-09-26.json")
            self.assertEqual((kind, data, ct), ("file", b'[{"title": "CTO"}]', "application/json; charset=utf-8"))
            open(os.path.join(ws, "runs", "[id].tsx"), "w").write("x"); os.makedirs(os.path.join(ws, "@types"))
            self.assertEqual(m._wsfiles.read("jr", "runs/[id].tsx")[0], "file")               # repo-style names pass
            self.assertEqual(m._wsfiles.read("jr", "@types")[0], "dir")
            for bad in ("../outside", "runs/../../outside", ".secret", "link", "runs/nope.json", "runs/a\x01b"):
                self.assertIsNone(m._wsfiles.read("jr", bad)[0], bad)
            self.assertIsNone(m._wsfiles.read("other", "")[0])
            h = self._handler("/api/workspace/jr/runs/2026-09-26.json", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 200); self.assertTrue(h.wfile.getvalue().endswith(b'[{"title": "CTO"}]'))
            h = self._handler("/api/workspace/jr/runs", "10.0.0.9"); h._do_GET()
            self.assertIn(b'"entries"', h.wfile.getvalue())
            h = self._handler("/api/workspace/jr/../outside", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 404)
            h = self._handler("/api/workspace/jr/runs", "172.30.2.2"); h._do_GET()      # the VM itself (index 2)
            self.assertNotEqual(self._status(h), 200)
        finally:
            m._mounts.AGENT_ROOT, m._instances.load_instances, m._guests.instance_by_ip = old

    def test_task_answer_links_the_files_it_names(self):
        """A task's chat answer gets download links for the workspace files it
        names (relative or under .../workspace/), never for missing files,
        dotfiles or symlinks out; the web chat renders them as downloads
        (live 2026-10-15: the job run named its .xlsx and .json, no way to get them)."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-wsl-")
        ws = os.path.join(tmp, "agent", "jr"); os.makedirs(os.path.join(ws, "runs"))
        for f in ("runs/2026-10-15.json", "jobs-2026-10-15.xlsx", ".env"):
            open(os.path.join(ws, f), "w").write("x")
        open(os.path.join(tmp, "outside.txt"), "w").write("host")
        os.symlink(os.path.join(tmp, "outside.txt"), os.path.join(ws, "out.txt"))
        old = m._mounts.AGENT_ROOT, m._instances.load_instances
        try:
            m._mounts.AGENT_ROOT = os.path.join(tmp, "agent")
            m._instances.load_instances = lambda: [{"name": "jr", "index": 2}]
            text = ("Ergebnis: runs/2026-10-15.json (92 Einträge) und /home/node/workspace/jobs-2026-10-15.xlsx. "
                    "Nicht: nope.csv, .env, out.txt, ../outside.txt, example.com")
            self.assertEqual(m._wsfiles.mentioned("jr", text), ["runs/2026-10-15.json", "jobs-2026-10-15.xlsx"])
            self.assertEqual(m._wsfiles.links("jr", text),
                             "\n\n📎 [2026-10-15.json](/api/workspace/jr/runs/2026-10-15.json)"
                             " · [jobs-2026-10-15.xlsx](/api/workspace/jr/jobs-2026-10-15.xlsx)")
            self.assertEqual(m._wsfiles.links("jr", "nothing here"), "")
            self.assertEqual(m._wsfiles.links("ghost", text), "")
            src = open(os.path.join(os.path.dirname(m.__file__), "chatui.py")).read()
            self.assertIn("(\\/api\\/workspace\\/[^)\\s\"'<>&]+)\\)/g,'<a href=\"$2\" download>", src)
        finally:
            m._mounts.AGENT_ROOT, m._instances.load_instances = old

    def test_apps_hosted_on_celld_with_the_manager_as_fallback(self):
        """CELLD_URL set: /apps/<name>/ comes from celld (behind the same login
        and path checks); its 404 is a 404; celld down or unset: the manager's
        own copy. A path the manager would refuse never reaches celld."""
        import http.server, threading
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-celld-")
        os.makedirs(os.path.join(tmp, "board"))
        json.dump({"title": "Board", "server": True}, open(os.path.join(tmp, "board", "app.json"), "w"))
        _wtext(os.path.join(tmp, "board", "index.html"), "<h1>local copy</h1>")
        asked = []

        class Celld(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/_status":
                    b = json.dumps({"ok": True, "apps": {"board": {"text": "3 repos · 1 ready"}}}).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
                asked.append(self.path)
                if self.path == "/apps/board/index.html":
                    b = b"<h1>from celld</h1>"
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
                else:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Celld)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = m._settings.SITE.get("APPS_DIR"), m._settings.load_settings
        try:
            m._settings.SITE["APPS_DIR"] = tmp
            m._settings.load_settings = lambda: {"CELLD_URL": f"http://127.0.0.1:{srv.server_port}/"}
            self.assertEqual(m._apps.hosted("board", ""), (b"<h1>from celld</h1>", "text/html; charset=utf-8"))
            self.assertEqual(m._apps.hosted("board", "nope.js"), (None, "not found"))
            for bad in ("../x", ".env", "a/../../b"):
                self.assertIsNone(m._apps.hosted("board", bad), bad)
            self.assertIsNone(m._apps.hosted("nomanifest", ""))
            self.assertEqual(asked, ["/apps/board/index.html", "/apps/board/nope.js"])     # nothing else was forwarded
            h = self._handler("/apps/board/", "10.0.0.9"); h._do_GET()
            self.assertTrue(h.wfile.getvalue().endswith(b"<h1>from celld</h1>"))
            def api_apps():
                h = self._handler("/api/apps", "10.0.0.9"); h._do_GET()
                return json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            d = api_apps()
            self.assertEqual((d["hosting"], d["celld"]["reachable"], d["apps"][0]["served_by"], d["apps"][0]["server_side"]),
                             ("celld", True, "celld", {"text": "3 repos · 1 ready"}))
            srv.shutdown(); srv.server_close()                                               # celld gone
            self.assertIsNone(m._apps.hosted("board", ""))
            d = api_apps()
            self.assertEqual((d["hosting"], d["celld"]["reachable"], d["apps"][0]["served_by"], d["apps"][0]["server_side"]),
                             ("celld", False, "manager", None))                              # the fallback, visibly
            self.assertTrue(d["apps"][0]["server"])                                          # declared: "unreachable", not "none"
            self.assertTrue(d["celld"]["error"])
            h = self._handler("/apps/board/", "10.0.0.9"); h._do_GET()
            self.assertTrue(h.wfile.getvalue().endswith(b"<h1>local copy</h1>"))
            m._settings.load_settings = lambda: {}                                           # not configured
            self.assertIsNone(m._apps.hosted("board", ""))
            d = api_apps()
            self.assertEqual((d["hosting"], d["celld"]["configured"], d["apps"][0]["served_by"]), ("manager", False, "manager"))
        finally:
            m._settings.SITE["APPS_DIR"] = old[0]
            m._settings.load_settings = old[1]

    def test_app_server_side_goes_to_celld_only(self):
        """/apps/<name>/_api/... (GET and POST) is the app's own server side on
        celld: forwarded with its body after the path checks, behind the login;
        no local fallback (503 without celld); a POST elsewhere is refused; a
        VM never gets through."""
        import http.server, threading
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-capi-")
        os.makedirs(os.path.join(tmp, "flow"))
        json.dump({"title": "Flow"}, open(os.path.join(tmp, "flow", "app.json"), "w"))
        _wtext(os.path.join(tmp, "flow", "index.html"), "<h1>x</h1>")
        seen = []

        class Celld(http.server.BaseHTTPRequestHandler):
            def _reply(self, body):
                b = json.dumps(body).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self):
                seen.append(("GET", self.path, b"")); self._reply({"repo-a": {"CODEFLOW_STATE": "ready"}})
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                seen.append(("POST", self.path, body)); self._reply({"ok": True})
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Celld)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, m._guests.instance_by_ip
        try:
            m._settings.SITE["APPS_DIR"] = tmp
            m._settings.load_settings = lambda: {"CELLD_URL": f"http://127.0.0.1:{srv.server_port}"}
            m._guests.instance_by_ip = lambda ip: {"name": "vm"} if ip.startswith("172.30.") else None
            h = self._handler("/apps/flow/_api/repos", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 200); self.assertIn(b'"CODEFLOW_STATE": "ready"', h.wfile.getvalue())
            body = json.dumps({"tab": "A", "ttl": 600000}).encode()
            h = self._post_handler("/apps/flow/_api/repos/repo-a/claim", "10.0.0.9", body, origin="http://127.0.0.1:8700"); h._do_POST()
            self.assertEqual(self._status(h), 200)
            self.assertEqual(seen, [("GET", "/apps/flow/_api/repos", b""), ("POST", "/apps/flow/_api/repos/repo-a/claim", body)])
            h = self._post_handler("/apps/flow/index.html", "10.0.0.9", b"{}", origin="http://127.0.0.1:8700"); h._do_POST()
            self.assertEqual(self._status(h), 405)                                       # files take no POST
            for bad in ("/apps/flow/_api/../x", "/apps/nomanifest/_api/repos", "/apps/flow/_api/.env"):
                h = self._post_handler(bad, "10.0.0.9", b"{}", origin="http://127.0.0.1:8700"); h._do_POST()
                self.assertNotEqual(self._status(h), 200, bad)
            h = self._post_handler("/apps/flow/_api/repos/repo-a", "10.0.0.9", b"{}", origin="https://evil.example"); h._do_POST()
            self.assertEqual(self._status(h), 403)                                       # the CSRF origin check of every POST
            h = self._handler("/apps/flow/_api/repos", "172.30.1.2"); h._do_GET()
            self.assertNotEqual(self._status(h), 200)                                    # a VM is not the operator
            self.assertEqual(len(seen), 2)                                               # none of those reached celld
            m._settings.load_settings = lambda: {}
            h = self._handler("/apps/flow/_api/repos", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 503)                                       # no local fallback for state
            srv.shutdown(); srv.server_close()
            m._settings.load_settings = lambda: {"CELLD_URL": f"http://127.0.0.1:{srv.server_port}"}
            self.assertEqual(m._apps.api_forward("GET", "flow", "_api/repos")[0], 503)    # celld gone
        finally:
            m._settings.SITE["APPS_DIR"] = old[0]
            m._settings.load_settings, m._guests.instance_by_ip = old[1], old[2]

    def test_cfapi_migration_hash_multipart_and_retry(self):
        """cfdo's upload path in Python: the class is created once, later the
        same tag needs nothing; the asset hash is Cloudflare's reference one;
        multipart parts are well-formed; 429 is retried, a 4xx is an error
        with Cloudflare's message; every request has its own User-Agent."""
        import email, hashlib as hl, base64 as b64
        cf = self.m._cfapi
        self.assertEqual(cf.migration("C", "v1", ""), {"new_tag": "v1", "new_sqlite_classes": ["C"]})
        self.assertIsNone(cf.migration("C", "v1", "v1"))
        self.assertEqual(cf.migration("C", "v2", "v1"), {"new_tag": "v2", "new_sqlite_classes": [], "old_tag": "v1"})
        self.assertEqual(cf.asset_hash(b"hi", "/a/x.js"), hl.sha256(b64.b64encode(b"hi") + b"js").hexdigest()[:32])
        body, ct = cf.multipart([("metadata", None, "application/json", b'{"a":1}'), ("w.mjs", "w.mjs", "application/javascript+module", b"export default {}")])
        msg = email.message_from_bytes(b"Content-Type: " + ct.encode() + b"\r\n\r\n" + body)
        parts = [(p.get_param("name", header="content-disposition"), p.get_content_type(), p.get_payload(decode=True)) for p in msg.get_payload()]
        self.assertEqual(parts, [("metadata", "application/json", b'{"a":1}'), ("w.mjs", "application/javascript+module", b"export default {}")])
        calls = []
        class R:
            def __init__(self, status, body): self.status, self._b = status, body
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False
        seq = [urllib.error.HTTPError("u", 429, "slow", {}, io.BytesIO(b"")), R(200, b'{"success": true, "result": {"ok": 1}}')]
        old = cf.urllib.request.urlopen
        try:
            def fake(req, timeout=None):
                calls.append(req.get_header("User-agent"))
                x = seq.pop(0)
                if isinstance(x, Exception): raise x
                return x
            cf.urllib.request.urlopen = fake
            self.assertEqual(cf.request("GET", "/x", "t", sleep=lambda s: None), {"ok": 1})
            self.assertEqual(calls, [cf.UA, cf.UA]); self.assertNotIn("Python-urllib", cf.UA)
            seq.append(urllib.error.HTTPError("u", 403, "no", {}, io.BytesIO(b'{"success": false, "errors": [{"code": 10000, "message": "Authentication error"}]}')))
            with self.assertRaisesRegex(cf.CFError, "10000: Authentication error"):
                cf.request("GET", "/x", "t", sleep=lambda s: None)
        finally:
            cf.urllib.request.urlopen = old

    def test_app_to_cloudflare_and_back(self):
        """Upload through the Workers API (assets session, script, workers.dev),
        then the state moves celld -> Cloudflare and the app is 'cloud'
        (redirect, local _api refuses). The token opens only the apps' API
        paths and only while in the cloud. Restore: the state comes back, the
        token is dead, the app is local."""
        import http.server, threading, email, base64 as b64
        m = self.m
        cl, cf = m._cloud, m._cfapi
        tmp = tempfile.mkdtemp(prefix="e2e-cf-")
        apps, celld = os.path.join(tmp, "apps"), os.path.join(tmp, "celld")
        os.makedirs(os.path.join(apps, "flow", "tests")); os.makedirs(os.path.join(celld, "do"))
        json.dump({"title": "Flow", "server": True}, open(os.path.join(apps, "flow", "app.json"), "w"))
        _wtext(os.path.join(apps, "flow", "index.html"), "<h1>flow</h1>")
        _wtext(os.path.join(apps, "flow", ".env"), "SECRET=1")
        _wtext(os.path.join(apps, "flow", "tests", "t.mjs"), "test")
        _wtext(os.path.join(celld, "do", "storage.js"), "// s\nexport async function storageExport(ctx) { return []; }\n")
        _wtext(os.path.join(celld, "do", "flow.js"), 'import { DurableObject } from "cloudflare:workers";\n'
               'import { storageExport } from "./storage.js";\nexport class FlowState extends DurableObject {}\n')
        stores = {"celld": [["repo:a", {"CODEFLOW_STATE": "ready"}], ["repo:b", {"CODEFLOW_STATE": "failed"}]], "cf": []}
        auths, api = [], {"uas": [], "manifest": None, "uploaded": [], "meta": None, "module": "", "scripts": []}

        def ok(h, obj, status=200):
            b = json.dumps(obj).encode()
            h.send_response(status); h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)

        class CFApi(http.server.BaseHTTPRequestHandler):           # a fake Cloudflare API
            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))
            def _parts(self, raw):
                msg = email.message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw)
                return [(p.get_param("name", header="content-disposition"), p.get_payload(decode=True)) for p in msg.get_payload()]
            def do_GET(self):
                api["uas"].append(self.headers.get("User-Agent"))
                if self.path.endswith("/workers/scripts"):
                    return ok(self, {"success": True, "result": [{"id": s, "migration_tag": "v1"} for s in api["scripts"]]})
                if self.path.endswith("/workers/subdomain"):
                    return ok(self, {"success": True, "result": {"subdomain": "t"}})
                ok(self, {"success": False, "errors": [{"code": 1, "message": "no"}]}, 404)
            def do_POST(self):
                api["uas"].append(self.headers.get("User-Agent")); raw = self._body()
                if self.path.endswith("/assets-upload-session"):
                    api["manifest"] = json.loads(raw)["manifest"]
                    return ok(self, {"success": True, "result": {"jwt": "sess", "buckets": [[v["hash"] for v in api["manifest"].values()]]}})
                if "/workers/assets/upload" in self.path:
                    self.assertion = self.headers.get("Authorization")
                    api["uploaded"] = [(n, self.headers.get("Authorization")) for n, _ in self._parts(raw)]
                    return ok(self, {"success": True, "result": {"jwt": "done"}})
                if self.path.endswith("/subdomain"):
                    return ok(self, {"success": True, "result": {}})
                ok(self, {"success": False}, 404)
            def do_PUT(self):
                api["uas"].append(self.headers.get("User-Agent"))
                parts = dict(self._parts(self._body()))
                api["meta"], api["module"] = json.loads(parts["metadata"]), parts["worker.mjs"].decode()
                api["scripts"].append(self.path.rsplit("/", 1)[1])
                ok(self, {"success": True, "result": {"id": "kaim56-flow"}})
            def log_message(self, *a): pass

        def state_server(store, need_auth):
            class S(http.server.BaseHTTPRequestHandler):
                def _gate(self):
                    if need_auth:
                        auths.append((self.headers.get("Authorization", ""), self.headers.get("User-Agent", "")))
                        if self.headers.get("Authorization") != "Basic " + b64.b64encode(b"admin:the-secret").decode():
                            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return False
                    return True
                def do_GET(self):
                    if self._gate() and self.path.endswith("/_api/export"):
                        ok(self, {"entries": stores[store]})
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    if self._gate() and self.path.endswith("/_api/import"):
                        stores[store] = body["entries"] if body.get("replace") else stores[store] + body["entries"]
                        ok(self, {"ok": True, "total": len(stores[store])})
                def log_message(self, *a): pass
            return S
        servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (CFApi, state_server("cf", True), state_server("celld", False))]
        for srv in servers:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        api_srv, cf_srv, celld_srv = servers
        cf_url = f"http://127.0.0.1:{cf_srv.server_port}"
        settings = {"CF_ACCOUNT_ID": "acct", "CF_API_TOKEN": "cf-tok", "CF_APP_SECRET": "the-secret",
                    "CELLD_URL": f"http://127.0.0.1:{celld_srv.server_port}"}
        old = (m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, cl.CLOUD_FILE, cf.API, cl.WORKER_URL,
               m._auth.PW, m._guests.instance_by_ip, cl.wait_ready)
        try:
            m._settings.SITE["APPS_DIR"] = apps
            cl.CLOUD_FILE, cf.API, cl.WORKER_URL = os.path.join(tmp, "cloud.json"), f"http://127.0.0.1:{api_srv.server_port}", cf_url
            m._settings.load_settings = lambda: {}
            with self.assertRaises(ValueError):                                   # not set up: nothing happens
                cl.creds()
            m._settings.load_settings = lambda: dict(settings)
            cl.wait_ready = lambda url, secret, **k: ""
            cl._jobs["flow"] = {"done": False}
            self.assertEqual(cl.upload("flow"), cf_url)
            self.assertEqual(sorted(api["manifest"]), ["/app.json", "/index.html"])        # no dotfiles, no tests/
            self.assertEqual(api["manifest"]["/index.html"]["hash"], cf.asset_hash(b"<h1>flow</h1>", "/index.html"))
            self.assertEqual({a for _, a in api["uploaded"]}, {"Bearer sess"})              # buckets: the session token
            meta = api["meta"]
            self.assertEqual(meta["main_module"], "worker.mjs"); self.assertEqual(meta["assets"]["jwt"], "done")
            self.assertEqual(meta["migrations"], {"new_tag": "v1", "new_sqlite_classes": ["FlowState"]})
            self.assertEqual({(b["type"], b["name"]) for b in meta["bindings"]},
                             {("durable_object_namespace", "APP_DO"), ("secret_text", "CFDO_SECRET"), ("assets", "ASSETS")})
            self.assertEqual(next(b for b in meta["bindings"] if b["type"] == "secret_text")["text"], "the-secret")
            self.assertTrue(api["uas"] and all(u == cf.UA for u in api["uas"]))               # never Python's own UA
            w = api["module"]
            self.assertEqual(w.count("import "), 1)                               # one module: storage.js inlined
            self.assertIn("export class FlowState extends DurableObject", w)
            self.assertIn("async function storageExport", w); self.assertNotIn("export async function", w)
            token = re.search(r'const KAIM_TOKEN = "([^"]+)"', w).group(1)
            self.assertEqual(stores["cf"], stores["celld"])                       # the state moved
            self.assertEqual(cl.where("flow"), ("cloud", cf_url))
            self.assertTrue(all(a == "Basic " + b64.b64encode(b"admin:the-secret").decode() and u == cf.UA for a, u in auths))
            # a second upload of the same script needs no migration
            cl.restore("flow"); cl._jobs["flow"] = {"done": False}
            cl.upload("flow")
            self.assertNotIn("migrations", api["meta"])
            token = re.search(r'const KAIM_TOKEN = "([^"]+)"', api["module"]).group(1)
            # the token: the apps' API paths, while in the cloud
            self.assertEqual(cl.token_app(token, "GET", "/api/instances"), "flow")
            self.assertEqual(cl.token_app(token, "POST", "/api/tasks"), "flow")
            for meth, path in (("POST", "/api/settings"), ("GET", "/i/vm/term"), ("POST", "/api/secret-store"),
                               ("POST", "/api/update"), ("GET", "/api/security"), ("POST", "/api/apps/flow/restore")):
                self.assertIsNone(cl.token_app(token, meth, path), path)
            self.assertIsNone(cl.token_app("wrong", "GET", "/api/instances"))
            m._auth.PW = "admin-pw"
            m._guests.instance_by_ip = lambda ip: None
            h = self._handler("/api/instances", "10.0.0.9"); h.headers["Authorization"] = "Bearer " + token; h._do_GET()
            self.assertEqual(self._status(h), 200)
            h = self._post_handler("/api/settings", "10.0.0.9", b'{"X":"1"}'); h.headers["Authorization"] = "Bearer " + token; h._do_POST()
            self.assertEqual(self._status(h), 401)                                # never written with a token
            ba = "Basic " + b64.b64encode(b"admin:admin-pw").decode()
            h = self._handler("/apps/flow/", "10.0.0.9"); h.headers["Authorization"] = ba; h._do_GET()
            self.assertEqual(self._status(h), 302); self.assertIn(f"Location: {cf_url}/".encode(), h.wfile.getvalue())
            h = self._post_handler("/apps/flow/_api/repos/x", "10.0.0.9", b"{}", origin="http://127.0.0.1:8700")
            h.headers["Authorization"] = ba; h._do_POST()
            self.assertEqual(self._status(h), 409)                                # one copy changes: the cloud one
            # restore: changes made in the cloud come back
            stores["cf"] = stores["cf"] + [["repo:c", {"CODEFLOW_STATE": "analysing"}]]
            cl.restore("flow")
            self.assertEqual(len(stores["celld"]), 3)
            self.assertEqual(cl.where("flow"), ("local", ""))
            self.assertIsNone(cl.token_app(token, "GET", "/api/instances"))       # revoked
            with self.assertRaises(ValueError):
                cl.restore("flow")
        finally:
            (m._settings.SITE["APPS_DIR"], m._settings.load_settings, cl.CLOUD_FILE, cf.API, cl.WORKER_URL,
             m._auth.PW, m._guests.instance_by_ip, cl.wait_ready) = old
            for srv in servers:
                srv.shutdown()

    def test_agents_worker_for_cloudflare(self):
        """The agents app (celld/do/agents.js) assembled for Cloudflare: its own
        router (one object per agent, export/import over all), the LLM at
        OpenRouter with CF_LLM_KEY as a Worker secret — never in the code; no
        key, no upload. One-object apps keep the plain route."""
        import email, re as _re
        m = self.m
        cl, cf = m._cloud, m._cfapi
        apps = os.path.join(FC_DIR, "..", "apps")
        if not os.path.isfile(os.path.join(FC_DIR, "..", "celld", "do", "agents.js")):
            self.skipTest("no repo checkout (celld/do/agents.js)")
        old = m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, cf.request
        try:
            m._settings.SITE["APPS_DIR"] = apps
            w = cl.worker_source("agents", "tok", "https://m.example")
            self.assertIn('return agentsRoute(env.APP_DO, url, request, url.pathname.slice(5) || "/");', w)
            self.assertNotIn("idFromName(KAIM_APP)", w)
            self.assertEqual(len(_re.findall(r"^import ", w, _re.M)), 1)
            self.assertIn("async function agentsRoute(", w); self.assertNotIn("export async function", w)
            self.assertIn("export class AgentState extends DurableObject", w)
            self.assertIn('parts[0] === "export"', w); self.assertIn('parts[0] === "import"', w)
            self.assertIn("this.env.LLM_KEY", w)
            m._settings.load_settings = lambda: {}
            with self.assertRaises(ValueError):
                cl.llm_bindings("agents")
            m._settings.load_settings = lambda: {"CF_LLM_KEY": "sk-or-test"}
            llm = cl.llm_bindings("agents")
            self.assertEqual(llm, [{"type": "plain_text", "name": "LLM_URL", "text": "https://openrouter.ai/api/v1/chat/completions"},
                                   {"type": "secret_text", "name": "LLM_KEY", "text": "sk-or-test"}])
            self.assertNotIn("sk-or-test", w)
            self.assertEqual(cl.llm_bindings("codeflow"), [])
            self.assertIn("idFromName(KAIM_APP)", cl.worker_source("codeflow", "tok", "https://m.example"))
            sent = []
            cf.request = lambda method, path, token, body=None, ctype=None, **kw: sent.append((body, ctype)) or ([] if method == "GET" else {})
            cf.upload_script("acct", "kaim56-agents", "t", module_name="worker.mjs", module_src=w, cls="AgentState",
                             binding="APP_DO", secret="s", compat_date="2026-09-01", extra_bindings=llm)
            body, ctype = sent[-1]
            msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
            meta = json.loads(msg.get_payload()[0].get_payload(decode=True))
            self.assertEqual([(b["type"], b["name"]) for b in meta["bindings"]],
                             [("durable_object_namespace", "APP_DO"), ("secret_text", "CFDO_SECRET"),
                              ("plain_text", "LLM_URL"), ("secret_text", "LLM_KEY")])
            self.assertEqual(meta["migrations"], {"new_tag": "v1", "new_sqlite_classes": ["AgentState"]})
        finally:
            m._settings.SITE["APPS_DIR"], m._settings.load_settings, cf.request = old

    def test_cloudflare_lists_every_worker(self):
        """cloud.cf_workers: every script on the account — the ones deployed
        from here carry their app, the others (wrangler, cfdo, dashboard) app
        None; workers.dev URL only when on; cached; a failing API is an error
        text, not an exception; no settings -> not configured, no call."""
        m = self.m
        cl, cf = m._cloud, m._cfapi
        tmp = tempfile.mkdtemp(prefix="e2e-cflist-")
        os.makedirs(os.path.join(tmp, "corewar"))
        json.dump({"title": "Core War"}, open(os.path.join(tmp, "corewar", "app.json"), "w"))
        _wtext(os.path.join(tmp, "corewar", "index.html"), "x")
        calls = []
        def fake(method, path, token, *a, **k):
            calls.append(path)
            if path.endswith("/workers/scripts"):
                return [{"id": "shipsinker", "has_assets": True, "modified_on": "2026-10-01T19:00:53Z"},
                        {"id": "kaim56-corewar", "has_assets": True}, {"id": "hidden"}]
            if path.endswith("/workers/subdomain"):
                return {"subdomain": "kat56"}
            if path.endswith("/hidden/subdomain"):
                return {"enabled": False}
            return {"enabled": True}
        old = (m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, cf.request, dict(cl._cf_list))
        try:
            m._settings.SITE["APPS_DIR"] = tmp
            m._settings.load_settings = lambda: {}
            cf.request = fake
            self.assertFalse(cl.cf_workers(force=True)["configured"]); self.assertEqual(calls, [])
            m._settings.load_settings = lambda: {"CF_ACCOUNT_ID": "acct", "CF_API_TOKEN": "t", "CF_APP_SECRET": "s"}
            d = cl.cf_workers(force=True)
            self.assertEqual(d["error"], "")
            w = {x["name"]: x for x in d["workers"]}
            self.assertEqual(sorted(w), ["hidden", "kaim56-corewar", "shipsinker"])
            self.assertEqual(w["kaim56-corewar"]["app"], "corewar"); self.assertIsNone(w["shipsinker"]["app"])
            self.assertEqual(w["shipsinker"]["url"], "https://shipsinker.kat56.workers.dev")
            self.assertEqual(w["hidden"]["url"], "")
            n = len(calls); cl.cf_workers(); self.assertEqual(len(calls), n)          # cached
            def boom(*a, **k): raise cf.CFError(403, [{"code": 10000, "message": "Authentication error"}])
            cf.request = boom
            self.assertIn("Authentication error", cl.cf_workers(force=True)["error"])
        finally:
            m._settings.SITE["APPS_DIR"], m._settings.load_settings, cf.request = old[:3]
            cl._cf_list.clear(); cl._cf_list.update(old[3])

    def test_apps_delete_and_rename(self):
        """Delete: the worker on Cloudflare first (refused by Cloudflare ->
        nothing changed), the entry (token) goes, the folder moves to
        .trash/. Rename: folder + URL, the old unused worker is deleted; not
        for apps with a server side, not while on Cloudflare, not onto an
        existing name. A stray worker can be deleted, a local app's cannot."""
        m = self.m
        cl, cf, ap = m._cloud, m._cfapi, m._apps
        tmp = tempfile.mkdtemp(prefix="e2e-appdel-")
        apps, celld = os.path.join(tmp, "apps"), os.path.join(tmp, "celld")
        os.makedirs(os.path.join(celld, "do"))
        for n, server in (("board", False), ("flow", True), ("other", False)):
            os.makedirs(os.path.join(apps, n))
            json.dump({"title": n, "server": server}, open(os.path.join(apps, n, "app.json"), "w"))
            _wtext(os.path.join(apps, n, "index.html"), n)
        _wtext(os.path.join(celld, "do", "flow.js"), 'export class FlowState extends DurableObject {}\n')
        deleted, refuse = [], {"on": False}
        def fake(method, path, token, *a, **k):
            if method == "DELETE":
                if refuse["on"]:
                    raise cf.CFError(403, [{"code": 10000, "message": "Authentication error"}])
                name = path.split("/scripts/")[1].split("?")[0]
                if name in ("kaim56-board", "kaim56-flow", "stray"):
                    deleted.append(name); return None
                raise cf.CFError(404, [])
            return []
        old = (m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, cf.request, cl.CLOUD_FILE, dict(cl._jobs))
        try:
            cl._jobs.clear()
            m._settings.SITE["APPS_DIR"] = apps
            m._settings.load_settings = lambda: {"CF_ACCOUNT_ID": "a", "CF_API_TOKEN": "t", "CF_APP_SECRET": "s"}
            cf.request, cl.CLOUD_FILE = fake, os.path.join(tmp, "cloud.json")
            cl._save({"board": {"where": "local", "url": "https://kaim56-board.x", "token_sha": ""},
                      "flow": {"where": "cloud", "url": "https://kaim56-flow.x", "token_sha": "abc"}})
            # rename: refused for a server side, for an app on Cloudflare, onto an existing name, bad names
            for app, new, msg in (("flow", "flow2", "Cloudflare"), ("board", "other", "exists"), ("board", "Bad Name", "a-z")):
                with self.assertRaisesRegex(ValueError, msg):
                    cl.rename(app, new)
            cl._save({**cl.load(), "flow": {"where": "local", "url": "u"}})
            with self.assertRaisesRegex(ValueError, "server side"):
                cl.rename("flow", "flow2")
            r = cl.rename("board", "jobs")
            self.assertEqual(r, {"name": "jobs", "old_worker": "deleted"}); self.assertEqual(deleted, ["kaim56-board"])
            self.assertTrue(os.path.isfile(os.path.join(apps, "jobs", "index.html")))
            self.assertNotIn("board", cl.load())
            # delete: Cloudflare refuses -> nothing changed
            cl._save({**cl.load(), "flow": {"where": "cloud", "url": "u", "token_sha": "abc"}})
            refuse["on"] = True
            with self.assertRaises(cf.CFError):
                cl.remove("flow")
            self.assertTrue(os.path.isdir(os.path.join(apps, "flow"))); self.assertIn("flow", cl.load())
            refuse["on"] = False
            r = cl.remove("flow")
            self.assertEqual(r["worker"], "deleted"); self.assertTrue(r["celld_state_kept"])
            self.assertTrue(r["trashed"].startswith(os.path.join(apps, ".trash", "flow-")))
            self.assertTrue(os.path.isfile(os.path.join(r["trashed"], "index.html")))
            self.assertNotIn("flow", cl.load()); self.assertNotIn("flow", [a["name"] for a in ap.load_apps()])
            self.assertEqual(cl.remove("other")["worker"], "none")                 # never deployed: fine
            with self.assertRaises(ValueError):
                cl.remove("nope")
            # stray workers: deletable; a local app's worker only through the app
            self.assertEqual(cl.delete_worker("stray"), {"deleted": "stray"})
            with self.assertRaisesRegex(ValueError, "belongs to a local app"):
                cl.delete_worker("kaim56-jobs")
            for bad in ("../x", "", "Stray"):
                with self.assertRaises(ValueError):
                    cl.delete_worker(bad)
            # in the cloud without Cloudflare settings: refused, nothing moved
            cl._save({"jobs": {"where": "cloud", "url": "u"}})
            m._settings.load_settings = lambda: {}
            with self.assertRaisesRegex(ValueError, "not set up"):
                cl.remove("jobs")
            self.assertTrue(os.path.isdir(os.path.join(apps, "jobs")))
            cl._jobs["jobs"] = {"done": False}                                    # a running upload: hands off
            with self.assertRaisesRegex(ValueError, "still running"):
                cl.rename("jobs", "x")
        finally:
            m._settings.SITE["APPS_DIR"], m._settings.load_settings, cf.request, cl.CLOUD_FILE = old[:4]
            cl._jobs.clear(); cl._jobs.update(old[4])

    def test_cfdo_worker_export(self):
        """Download the Durable Objects of a worker built with cfdo: ping, list,
        export per object, with that worker's own secret (or the default one);
        a wrong secret and a non-cfdo worker say so."""
        import http.server, threading
        m = self.m
        cl = m._cloud
        seen = []
        class W(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("User-Agent")))
                if self.headers.get("x-cfdo-secret") != "own-secret":
                    self.send_response(401); self.end_headers(); self.wfile.write(b"unauthorized"); return
                body = {"/__cfdo/ping": {"ok": True, "class": "ChatRoom"},
                        "/__cfdo/list": {"objects": [{"name": "default", "id": "aa11"}, {"name": "lobby", "id": "bb22"}]}}.get(
                    self.path.split("?")[0], None)
                if self.path.startswith("/__cfdo/export?id="):
                    body = {"format": 1, "kv": [["k", self.path.rsplit("=", 1)[1]]]}
                b = json.dumps(body).encode()
                self.send_response(200 if body is not None else 404); self.end_headers(); self.wfile.write(b)
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), W)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}"
        old = (cl.cf_workers, m._settings.load_settings)
        try:
            cl.cf_workers = lambda force=False: {"workers": [{"name": "chat-room", "app": None, "url": url},
                                                             {"name": "nourl", "app": None, "url": ""}]}
            m._settings.load_settings = lambda: {"CF_ACCOUNT_ID": "a", "CF_API_TOKEN": "t", "CF_APP_SECRET": "default-secret"}
            with self.assertRaisesRegex(ValueError, "refused the secret"):      # empty -> default secret, refused
                cl.cf_worker_export("chat-room")
            d = cl.cf_worker_export("chat-room", "own-secret")
            self.assertEqual((d["worker"], d["class"]), ("chat-room", "ChatRoom"))
            self.assertEqual([(o["name"], o["data"]["kv"][0][1]) for o in d["objects"]], [("default", "aa11"), ("lobby", "bb22")])
            self.assertTrue(all(ua == m._cfapi.UA for _, ua in seen))
            for script, msg in (("nourl", "workers.dev"), ("missing", "no worker"), ("../x", "bad worker")):
                with self.assertRaisesRegex(ValueError, msg):
                    cl.cf_worker_export(script, "own-secret")
        finally:
            cl.cf_workers, m._settings.load_settings = old
            srv.shutdown()

    def test_manage_apps_grants(self):
        """MANAGE_APPS opens exactly the app/Cloudflare admin routes to a guest:
        none without it, read = list/state/export, write = + upload/restore/
        rename/app state POST, full = + delete; everything else stays admin-only."""
        from mgr import guestgrants as g
        m = self.m
        inst = lambda lv: {"name": "a", "config": ({"MANAGE_APPS": lv} if lv else {})}
        cases = [("GET", "/api/apps", "read"), ("GET", "/api/apps/cloudflare?refresh=1", "read"),
                 ("GET", "/apps/corewar/_api/changes", "read"), ("POST", "/api/cfworkers/chat-room/export", "read"),
                 ("POST", "/api/apps/corewar/upload", "write"), ("POST", "/api/apps/corewar/rename", "write"),
                 ("POST", "/apps/corewar/_api/changes", "write"),
                 ("POST", "/api/apps/corewar/delete", "full"), ("POST", "/api/cfworkers/x/delete", "full")]
        order = ["", "read", "write", "full"]
        for method, path, need in cases:
            for lv in order:
                self.assertEqual(g.allowed(inst(lv), method, path), order.index(lv) >= order.index(need), (method, path, lv))
        for method, path in (("GET", "/api/settings"), ("POST", "/api/settings"), ("GET", "/apps/corewar/index.html"),
                             ("POST", "/api/apps/corewar/../../settings"), ("POST", "/api/iroh"), ("GET", "/api/instances")):
            self.assertFalse(g.allowed(inst("full"), method, path), path)
        self.assertFalse(g.allowed(inst("FULL!"), "GET", "/api/apps"))            # unknown level = nothing
        # through the real handler: a guest without the key is refused, with read it lists
        old = (m._guests.instance_by_ip, m._apps.celld_status, m._apps.load_apps)
        try:
            m._apps.celld_status = lambda: {"configured": False, "reachable": False, "apps": {}, "url": ""}
            m._apps.load_apps = lambda: [{"name": "corewar", "title": "Core War", "server": True}]
            for lv, want in (("", 403), ("read", 200)):
                m._guests.instance_by_ip = lambda ip, lv=lv: inst(lv)
                h = self._handler("/api/apps", "172.30.9.2"); h._do_GET()
                self.assertEqual(self._status(h), want, lv)
            m._guests.instance_by_ip = lambda ip: inst("read")
            h = self._post_handler("/api/apps/corewar/delete", "172.30.9.2", b"{}"); h._do_POST()
            self.assertEqual(self._status(h), 403)                          # read may not delete
        finally:
            m._guests.instance_by_ip, m._apps.celld_status, m._apps.load_apps = old

    def test_celld_bucket_deploy(self):
        """celld with a bucket (mgr/celldeploy): nothing without ENV_FILE; with
        it, a change deploys the Worker files plus every app (no dotfiles, no
        .trash) through `celld deploy` in a container, once; the bucket is made
        first with a SigV4 PUT (409 = there already); a failed deploy is retried."""
        import http.server
        import subprocess
        m = self.m
        cd = m._celldeploy
        tmp = tempfile.mkdtemp(prefix="e2e-celld-")
        apps, proj = os.path.join(tmp, "apps"), os.path.join(tmp, "celld")
        os.makedirs(os.path.join(apps, "todo")); os.makedirs(os.path.join(apps, ".trash", "old"))
        _wtext(os.path.join(apps, "todo", "index.html"), "x"); _wtext(os.path.join(apps, "todo", ".env"), "S")
        _wtext(os.path.join(apps, ".trash", "old", "index.html"), "gone")
        os.makedirs(os.path.join(proj, "do")); _wtext(os.path.join(proj, "do", "a.js"), "//")
        _wtext(os.path.join(proj, "worker.js"), "//"); _wtext(os.path.join(proj, "wrangler.json"), "{}")
        puts = []
        class S3(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                puts.append((self.path, self.headers.get("Authorization", "")))
                self.send_response(200 if len(puts) == 1 else 409); self.end_headers()
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), S3)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = os.path.join(tmp, "celld.env")
        runs = []
        def fake_run(cmd, **kw):
            staged = cmd[cmd.index("-v") + 1].split(":")[0]
            runs.append((cmd, sorted(os.path.relpath(p, staged) for p in cd._walk(staged, skip_dot=False))))
            ok = len(runs) != 2
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "Current Version ID: v%d\n" % len(runs), "" if ok else "boom")
        old = (m._settings.SITE.get("APPS_DIR"), cd.ENV_FILE, cd.subprocess.run, cd.MIN_INTERVAL, dict(cd._state), m._paths.BASE)
        try:
            m._settings.SITE["APPS_DIR"] = apps
            m._paths.BASE = tmp
            cd.ENV_FILE, cd.subprocess.run, cd.MIN_INTERVAL = env, fake_run, 0
            cd._state.update(fingerprint="", checked=0.0)
            self.assertIsNone(cd.sync()); self.assertEqual(cd.status()["mode"], "dev")      # dev mode: nothing
            _wtext(env, f"AWS_ACCESS_KEY_ID=k\nAWS_SECRET_ACCESS_KEY=s\nCELLD_BUCKET=s3://celld\n"
                        f"S3_HOST_ENDPOINT=http://127.0.0.1:{srv.server_port}\n")
            r = cd.sync()
            self.assertEqual((r["ok"], r["version"]), (True, "v1"))
            cmd, files = runs[0]
            self.assertEqual(cmd[-3:], [cd.IMAGE, "deploy", "/project"]); self.assertIn("--env-file", cmd)
            self.assertEqual(files, ["do/a.js", "public/apps/todo/index.html", "worker.js", "wrangler.json"])
            self.assertEqual(puts[0][0], "/celld"); self.assertTrue(puts[0][1].startswith("AWS4-HMAC-SHA256 Credential=k/"))
            self.assertIsNone(cd.sync())                                                    # nothing changed
            _wtext(os.path.join(apps, "todo", "index.html"), "changed")
            os.utime(os.path.join(apps, "todo", "index.html"), ns=(1, 1))
            r = cd.sync()
            self.assertEqual((r["ok"], r["error"]), (False, "Current Version ID: v2\nboom"))  # 409 on the bucket is fine
            self.assertEqual((cd.status()["mode"], cd.status()["last"]["ok"]), ("bucket", False))
            self.assertEqual(cd.sync()["version"], "v3")                                     # failed: tried again
            self.assertEqual(len(runs), 3)
            self.assertEqual(os.listdir(os.path.join(tmp, "run")), [])                      # staging cleaned up
        finally:
            srv.shutdown(); srv.server_close()
            (m._settings.SITE["APPS_DIR"], cd.ENV_FILE, cd.subprocess.run, cd.MIN_INTERVAL, st, m._paths.BASE) = old
            cd._state.clear(); cd._state.update(st)

    def test_apps_publish_from_project_git(self):
        """An agent with MANAGE_APPS=write publishes a folder of its project's
        git repo, read from the base branch (not the work tree, not another
        branch): no symlink or dotfile is copied, app.json gets publisher and
        source, the app is served locked down (CSP: requests only to its own
        folder, no frames/forms/workers; COOP), an operator's app or another
        instance's is never replaced, its own update goes to .trash."""
        import subprocess as sp
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-pub-")
        apps, repo = os.path.join(tmp, "apps"), os.path.join(tmp, "repo")
        os.makedirs(os.path.join(apps, "corewar")); _wtext(os.path.join(apps, "corewar", "app.json"), "{}")
        _wtext(os.path.join(apps, "corewar", "index.html"), "op")
        g = lambda *a: sp.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout
        src = os.path.join(repo, "dist"); os.makedirs(os.path.join(src, "js"))
        g("init", "-q", "-b", "main"); g("config", "user.name", "op"); g("config", "user.email", "op@example.com")
        _wtext(os.path.join(src, "index.html"), "<h1>todo</h1>")
        _wtext(os.path.join(src, "app.json"), '{"title": "Todo", "server": true}')
        _wtext(os.path.join(src, "js", "a.js"), "1"); _wtext(os.path.join(src, ".env"), "SECRET")
        _wtext(os.path.join(repo, "secret.txt"), "SECRET")
        os.symlink("../secret.txt", os.path.join(src, "leak.txt"))
        g("add", "-A"); g("commit", "-q", "-m", "app")
        _wtext(os.path.join(src, "index.html"), "uncommitted")                       # the work tree is not published
        proj = {"name": "pp", "base": "main", "source": {"type": "host", "path": repo}, "members": {}}
        inst = lambda n, lv: {"name": n, "config": {"MANAGE_APPS": lv}}
        old = (m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, m._guests.instance_by_ip,
               m._projects.memberships, m._audit.audit_append)
        try:
            m._audit.audit_append = lambda *a, **k: None
            m._settings.SITE["APPS_DIR"] = apps
            m._settings.load_settings = lambda: {}
            m._projects.memberships = lambda n: [(proj, {"role": "writer"})] if n != "loner" else []
            def pub(name, who="dev", lv="write", rel="dist"):
                m._guests.instance_by_ip = lambda ip: inst(who, lv)
                h = self._post_handler(f"/api/apps/{name}/publish", "172.30.9.2", json.dumps({"src": rel}).encode()); h._do_POST()
                return self._status(h), h.wfile.getvalue()
            self.assertEqual(pub("todo", lv="read")[0], 403)                          # read may not publish
            self.assertEqual(pub("todo", who="loner")[0], 400)                        # no project, no publish
            st, body = pub("todo")
            self.assertEqual(st, 200, body)
            got = sorted(os.path.relpath(os.path.join(d, f), os.path.join(apps, "todo"))
                         for d, _, fs in os.walk(os.path.join(apps, "todo")) for f in fs)
            self.assertEqual(got, ["app.json", "index.html", "js/a.js"])              # no .env, no symlink
            with open(os.path.join(apps, "todo", "app.json")) as fh:
                man = json.load(fh)
            self.assertEqual((man["publisher"], man["title"], "server" in man), ("dev", "Todo", False))
            self.assertTrue(man["source"].startswith("pp@"), man)
            self.assertIn("todo", [a["name"] for a in m._apps.load_apps()])
            m._guests.instance_by_ip = lambda ip: None
            h = self._handler("/apps/todo/index.html", "10.0.0.9"); h.headers["Host"] = "localhost:8700"; h._do_GET()
            out = h.wfile.getvalue()
            self.assertIn(b"<h1>todo</h1>", out)
            csp = next(l for l in out.decode().split("\r\n") if l.startswith("Content-Security-Policy: "))
            self.assertIn("connect-src localhost:8700/apps/todo/;", csp)                 # its own folder, not /api
            for d in ("script-src 'self'", "frame-src 'none'", "form-action 'none'", "worker-src 'none'"):
                self.assertIn(d, csp)
            self.assertIn(b"Cross-Origin-Opener-Policy: same-origin", out)
            self.assertEqual(m._apps.agent_headers("todo", "evil host;")["Content-Security-Policy"].count("connect-src 'none'"), 1)
            h = self._handler("/apps/corewar/index.html", "10.0.0.9"); h._do_GET()
            self.assertNotIn(b"Content-Security-Policy", h.wfile.getvalue())             # the operator's own app: as before
            self.assertEqual(pub("corewar")[0], 400)                                  # an operator's app is never replaced
            self.assertEqual(pub("todo", who="other")[0], 400)                        # nor another instance's
            for bad in ("../x", "dist/.env", "nope", "dist/leak.txt"):
                self.assertEqual(pub("x" + str(len(bad)), rel=bad)[0], 400, bad)
            g("checkout", "-q", "-b", "feature"); g("commit", "-q", "-am", "on a branch")
            self.assertEqual(pub("todo")[0], 200)
            with open(os.path.join(apps, "todo", "index.html")) as fh:
                self.assertEqual(fh.read(), "<h1>todo</h1>")                          # only the base branch counts
            g("checkout", "-q", "main"); g("merge", "-q", "feature")
            st, body = pub("todo")
            self.assertEqual(st, 200); self.assertIn(b'"replaced": true', body)
            with open(os.path.join(apps, "todo", "index.html")) as fh:
                self.assertEqual(fh.read(), "uncommitted")
            self.assertEqual(len(os.listdir(os.path.join(apps, ".trash"))), 2)        # old versions kept, recoverable
        finally:
            (m._settings.SITE["APPS_DIR"], m._settings.load_settings, m._guests.instance_by_ip,
             m._projects.memberships, m._audit.audit_append) = old

    def test_squad_devops_member_may_publish_apps(self):
        """A squad member named or meant for devops gets MANAGE_APPS=write (and
        the apps tool in a fixed tool list); the others get nothing."""
        m = self.m
        made = {}
        old = (m._instances.load_instances, m._instances.create_instance, m._personas.load_personas)
        try:
            m._instances.load_instances = lambda: []
            m._instances.create_instance = lambda n, t, cfg, persist=False: made.__setitem__(n, cfg) or "created"
            m._personas.load_personas = lambda: [{"name": "p", "tools": ["shell"]}]
            proj = {"name": "pp", "members": {}}
            base = {"template": "openrouter", "role": "member", "skills": [], "persona": "", "purpose": "", "model": "", "model_tier": ""}
            m._squadgate._create(proj, {**base, "name": "pp-devops"}, [])
            m._squadgate._create(proj, {**base, "name": "pp-ops", "persona": "p", "purpose": "DevOps: CI"}, [])
            m._squadgate._create(proj, {**base, "name": "pp-coder"}, [])
            self.assertEqual(made["pp-devops"].get("MANAGE_APPS"), "write")
            self.assertEqual(made["pp-ops"].get("MANAGE_APPS"), "write"); self.assertIn("apps", made["pp-ops"]["AGENT_TOOLS"].split(","))
            self.assertNotIn("MANAGE_APPS", made["pp-coder"])
        finally:
            m._instances.load_instances, m._instances.create_instance, m._personas.load_personas = old

    def test_plugin_tools_are_in_the_tool_list(self):
        """Plugins appear in the policy's tool list (DESC read with ast, the code
        never runs in the manager) and a saved tool subset may contain them —
        before, saving a subset dropped every plugin silently."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-plugtools-")
        _wtext(os.path.join(tmp, "apps.py"), 'DESC = ("Control " "apps")\nraise SystemExit("must not run")\n')
        os.makedirs(os.path.join(tmp, "multi")); _wtext(os.path.join(tmp, "multi", "tool.py"), 'DESC = "multi tool"\n')
        os.makedirs(os.path.join(tmp, "__pycache__"))
        old = m._plugins.PLUGINS_SRC
        old_pins = m._plugins.PLUGIN_PINS_FILE
        try:
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = tmp, os.path.join(tmp, ".pins.json")
            m._plugins.plugin_pin("apps"); m._plugins.plugin_pin("multi")
            cat = {t["name"]: t for t in m._policy.tool_catalog()}
            self.assertEqual((cat["apps"]["desc"], cat["apps"].get("plugin")), ("Control apps", True))
            self.assertEqual(cat["multi"]["desc"], "multi tool")
            self.assertNotIn("__pycache__", cat)
            self.assertIn("bash", cat)
            self.assertIn("apps", m._policy.tool_names())
            inst = {"name": "pt", "config": {}}
            old_li, old_si, old_run = m._instances.load_instances, m._instances.save_instance, m._instances.is_running
            m._instances.load_instances = lambda: [inst]
            m._instances.save_instance = lambda i: None
            m._instances.is_running = lambda i: False
            try:
                m._instances.set_instance_tools("pt", ["bash", "apps"])
                self.assertEqual(inst["config"]["AGENT_TOOLS"], "apps,bash")      # the plugin is kept
            finally:
                m._instances.load_instances, m._instances.save_instance, m._instances.is_running = old_li, old_si, old_run
        finally:
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = old, old_pins

    def test_manage_apps_is_a_runtime_key_with_known_levels(self):
        """MANAGE_APPS: only read/write/full (or empty = off), applies at once,
        and the policy view reports it for the Apps & Cloudflare selector."""
        m = self.m
        inst = {"name": "ma", "config": {}}
        old = (m._instances.load_instances, m._instances.save_instance, m._instances.is_running)
        try:
            m._instances.load_instances = lambda: [inst]
            m._instances.save_instance = lambda i: None
            m._instances.is_running = lambda i: True
            self.assertIn("error", m._instances._set_config_key("ma", "MANAGE_APPS", "admin"))
            self.assertIn("applies at once", m._instances._set_config_key("ma", "MANAGE_APPS", "write"))
            self.assertEqual(inst["config"]["MANAGE_APPS"], "write")
            self.assertEqual(m._policy.effective_policy(inst)["manage_apps"], "write")
            m._instances._set_config_key("ma", "MANAGE_APPS", "")
            self.assertNotIn("MANAGE_APPS", inst["config"])
        finally:
            m._instances.load_instances, m._instances.save_instance, m._instances.is_running = old

    def test_only_approved_plugins_reach_a_vm(self):
        """approved(): pinned AND the content still matches the pin, checked on
        whatever directory it is given (the VM's copy). Unapproved or modified
        plugins are neither delivered nor offered in the tool list."""
        m = self.m
        tmp, vmcopy = tempfile.mkdtemp(prefix="e2e-plugpin-"), tempfile.mkdtemp(prefix="e2e-plugvm-")
        _wtext(os.path.join(tmp, "ok.py"), 'DESC = "fine"\n')
        _wtext(os.path.join(tmp, "new.py"), 'DESC = "never approved"\n')
        old = (m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE)
        try:
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = tmp, os.path.join(tmp, ".pins.json")
            m._plugins.plugin_pin("ok")
            self.assertTrue(m._plugins.approved("ok"))
            self.assertFalse(m._plugins.approved("new"))
            self.assertEqual([t["name"] for t in m._plugins.tool_entries()], ["ok"])
            _wtext(os.path.join(vmcopy, "ok.py"), 'DESC = "fine"\nimport os; os.system("evil")\n')   # swapped copy
            self.assertFalse(m._plugins.approved("ok", vmcopy))
            _wtext(os.path.join(tmp, "ok.py"), 'DESC = "changed"\n')                              # edited after Approve
            self.assertFalse(m._plugins.approved("ok"))
            self.assertEqual(m._plugins.tool_entries(), [])
        finally:
            m._plugins.PLUGINS_SRC, m._plugins.PLUGIN_PINS_FILE = old

    def test_apps_and_cloudflare_plugins(self):
        """The agent tools talk to the manager API and explain a missing grant."""
        import http.server, threading, importlib.util
        mods = {}
        sys.dont_write_bytecode, old_dwb = True, sys.dont_write_bytecode   # no __pycache__ in the live plugins dir
        for n in ("apps", "cloudflare"):
            spec = importlib.util.spec_from_file_location("plug_" + n, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", n + ".py"))
            mods[n] = importlib.util.module_from_spec(spec); spec.loader.exec_module(mods[n])
        grant = {"lv": "read"}
        class M(http.server.BaseHTTPRequestHandler):
            def _out(self, code, obj):
                b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self):
                if self.path.startswith("/api/apps/cloudflare"):
                    return self._out(200, {"configured": True, "workers": [{"name": "stray", "app": None, "url": "https://stray.x"}]})
                if self.path == "/api/apps":
                    return self._out(200, {"apps": [{"name": "corewar", "title": "Core War", "served_by": "celld",
                                                     "server_side": {"text": "3 changes"}, "cloud": {}, "job": {}}]})
                self._out(404, {})
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.endswith("/delete") and grant["lv"] != "full":
                    return self._out(403, {})
                self._out(200, {"ok": True, "deleted": "stray"})
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), M)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for mod in mods.values():
                mod._base = lambda: f"http://127.0.0.1:{srv.server_port}"
            self.assertIn("corewar (Core War): celld /apps/corewar/ — 3 changes", mods["apps"].run("list"))
            self.assertIn("stray: https://stray.x — created outside the manager", mods["cloudflare"].run("list"))
            self.assertIn("needs MANAGE_APPS (full)", mods["cloudflare"].run("delete", worker="stray"))
            self.assertIn("'app' is required", mods["apps"].run("upload"))
            grant["lv"] = "full"
            self.assertEqual(mods["cloudflare"].run("delete", worker="stray"), "worker stray deleted")
        finally:
            sys.dont_write_bytecode = old_dwb
            srv.shutdown()

    def test_apps_are_folders_served_behind_the_login(self):
        """mgr/apps: <APPS_DIR>/<name>/{app.json,index.html} is an app — listed
        by /api/apps and linked in the chat sidebar, served under /apps/<name>/
        with a bare /apps/<name> redirecting; traversal, dotfiles, symlink
        escapes, missing manifests and bad names are refused; guests get nothing."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-apps-")
        os.makedirs(os.path.join(tmp, "board", "js")); os.makedirs(os.path.join(tmp, "nomanifest")); os.makedirs(os.path.join(tmp, "Bad Name"))
        json.dump({"title": "Job board", "icon": "\U0001f4bc", "description": "d", "instance": "jobresearcher"}, open(os.path.join(tmp, "board", "app.json"), "w"))
        open(os.path.join(tmp, "board", "index.html"), "w").write("<h1>board</h1>")
        open(os.path.join(tmp, "board", "js", "a.js"), "w").write("x=1")
        open(os.path.join(tmp, "board", ".env"), "w").write("SECRET=1")
        open(os.path.join(tmp, "nomanifest", "index.html"), "w").write("x")
        open(os.path.join(tmp, "outside.txt"), "w").write("host file")
        os.symlink(os.path.join(tmp, "outside.txt"), os.path.join(tmp, "board", "link.txt"))
        old = m._settings.SITE.get("APPS_DIR")
        old_ls = m._settings.load_settings
        try:
            m._settings.SITE["APPS_DIR"] = tmp
            m._settings.load_settings = lambda: {}          # the manager's own copy (no CELLD_URL from the live settings)
            apps = m._apps.load_apps()
            self.assertEqual([a["name"] for a in apps], ["board"])
            self.assertEqual(apps[0]["title"], "Job board"); self.assertEqual(apps[0]["instance"], "jobresearcher")
            self.assertEqual(m._apps.file_of("board", "")[0], b"<h1>board</h1>")
            self.assertEqual(m._apps.file_of("board", "js/a.js")[1], "text/javascript; charset=utf-8")
            for bad in ("../outside.txt", "js/../../outside.txt", ".env", "link.txt", "js/", "nope.html"):
                self.assertIsNone(m._apps.file_of("board", bad)[0], bad)
            self.assertIsNone(m._apps.file_of("nomanifest", "")[0]); self.assertIsNone(m._apps.file_of("../board", "")[0])
            h = self._handler("/apps/board/", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 200); self.assertTrue(h.wfile.getvalue().endswith(b"<h1>board</h1>"))
            self.assertIn(b"text/html", h.wfile.getvalue())
            h = self._handler("/apps/board", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 302); self.assertIn(b"Location: /apps/board/", h.wfile.getvalue())
            h = self._handler("/apps/board/../outside.txt", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 404)
            old_ip = m._guests.instance_by_ip
            m._guests.instance_by_ip = lambda ip: {"name": "g", "config": {}} if ip == "172.30.1.2" else None
            try:
                h = self._handler("/apps/board/", "172.30.1.2"); h._do_GET()        # a VM is not the operator
            finally:
                m._guests.instance_by_ip = old_ip
            self.assertNotEqual(self._status(h), 200)
            h = self._handler("/api/apps", "10.0.0.9"); h._do_GET()
            self.assertIn(b'"name": "board"', h.wfile.getvalue())
            import chatui
            page = chatui.render([{"name": "a", "running": True}], "a", "", apps=m._apps.load_apps())
            self.assertIn("href='/apps/board/'", page); self.assertIn("Job board", page)
            m._settings.SITE["APPS_DIR"] = "/nonexistent"
            self.assertEqual(m._apps.load_apps(), [])
        finally:
            m._settings.load_settings = old_ls
            if old is None:
                m._settings.SITE.pop("APPS_DIR", None)
            else:
                m._settings.SITE["APPS_DIR"] = old

    def test_aicheck_assets_are_allowlisted_cached_and_served(self):
        """/aic/<name>: only the classifier's files, fetched once from the
        author's site into run/aicheck and served same-origin with the right
        type; anything else is 404, an unreachable upstream 502; the chat page
        carries the gauge and loads from /aic/."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-aic-")
        fetched = []
        old = m._paths.RUN_DIR, m._aicheck.fetch, m._guests.instance_by_ip
        try:
            m._paths.RUN_DIR = tmp
            m._aicheck.fetch = lambda name: (fetched.append(name), b"export const x=1;" if name.endswith(".mjs") else b'{"k":1}')[1]
            data, ct = m._aicheck.asset("classifier.mjs")
            self.assertEqual((data, ct), (b"export const x=1;", "text/javascript; charset=utf-8"))
            self.assertEqual(m._aicheck.asset("classifier.mjs")[0], data); self.assertEqual(fetched, ["classifier.mjs"])   # cached
            self.assertTrue(os.path.exists(os.path.join(tmp, "aicheck", "classifier.mjs")))
            self.assertEqual(m._aicheck.asset("langs/c_family.mjs")[1], "text/javascript; charset=utf-8")
            self.assertEqual(m._aicheck.asset("model.json")[1], "application/json")
            for bad in ("../secret.json", "langs/../../x.mjs", "index.html", "model.js", "langs/x.json"):
                self.assertIsNone(m._aicheck.asset(bad)[0], bad)
            self.assertEqual(fetched, ["classifier.mjs", "langs/c_family.mjs", "model.json"])
            h = self._handler("/aic/preprocess.mjs", "10.0.0.9"); h._do_GET()
            raw = h.wfile.getvalue()
            self.assertEqual(self._status(h), 200); self.assertIn(b"text/javascript", raw); self.assertTrue(raw.endswith(b"export const x=1;"))
            h = self._handler("/aic/evil.mjs", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 404)
            m._aicheck.fetch = lambda name: (_ for _ in ()).throw(OSError("down"))
            h = self._handler("/aic/extract.mjs", "10.0.0.9"); h._do_GET()
            self.assertEqual(self._status(h), 502)
            m._guests.instance_by_ip = lambda ip: {"name": "g", "config": {}} if ip == "172.30.1.2" else None
            h = self._handler("/aic/model.json", "172.30.1.2"); h._do_GET()      # a guest may not pull it
            self.assertNotEqual(self._status(h), 200)
        finally:
            m._paths.RUN_DIR, m._aicheck.fetch, m._guests.instance_by_ip = old
        import chatui
        page = chatui.render([{"name": "a", "running": True}], "a", "")
        self.assertIn('class=aib data-ai=', page); self.assertIn("/aic/classifier.mjs", page); self.assertIn("aiSchedule", page)
        self.assertIn("stickBottom();", page); self.assertIn("ResizeObserver", page)        # opens on the newest message

    def test_mail_route_is_guest_and_policy_gated(self):
        """POST /api/mail: a guest sends from its own plus address through the
        manager; an instance without the send_mail tool is refused; a stranger
        is not a guest and gets nothing."""
        m = self.m
        calls = []
        old = m._guests.instance_by_ip, m._mail.send, m._audit.audit_append, m._policy.tool_allowed
        try:
            m._guests.instance_by_ip = lambda ip: {"name": "jobresearcher", "config": {}} if ip == "172.30.1.2" else None
            m._mail.send = lambda to, subject, text, inst=None, **k: (calls.append((to, subject, text, inst["name"] if inst else None)), (True, "sent"))[1]
            m._audit.audit_append = lambda *a, **k: None
            m._policy.tool_allowed = lambda inst, tool: tool != "send_mail" or inst["name"] != "locked"
            h = self._post_handler("/api/mail", "172.30.1.2", json.dumps({"to": "boss@corp.test", "subject": "S", "text": "T"}).encode())
            h._do_POST()
            self.assertEqual(self._status(h), 200); self.assertEqual(calls, [("boss@corp.test", "S", "T", "jobresearcher")])
            m._guests.instance_by_ip = lambda ip: {"name": "locked", "config": {}} if ip == "172.30.1.2" else None
            h = self._post_handler("/api/mail", "172.30.1.2", json.dumps({"to": "boss@corp.test", "subject": "S", "text": "T"}).encode())
            h._do_POST()
            self.assertEqual(self._status(h), 403); self.assertEqual(len(calls), 1)
            old_pw = m._auth.PW
            m._auth.PW = "secret-for-this-test"           # with a password set, a stranger without auth is refused
            try:
                h = self._post_handler("/api/mail", "10.0.0.9", json.dumps({"to": "boss@corp.test", "subject": "S", "text": "T"}).encode())
                h._do_POST()
                self.assertEqual(self._status(h), 401); self.assertEqual(len(calls), 1)
            finally:
                m._auth.PW = old_pw
        finally:
            m._guests.instance_by_ip, m._mail.send, m._audit.audit_append, m._policy.tool_allowed = old

    def test_request_body_is_capped(self):
        """A body over the cap is answered 413 without being read; a bad or
        negative Content-Length is an empty body, not a hang."""
        m = self.m
        old = m._guests.instance_by_ip, m._auth.PW
        try:
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW = ""
            h = self._post_handler("/api/settings", "10.0.0.5", b"{}")
            h.headers.replace_header("Content-Length", str(m._routes.BODY_MAX + 1))
            h.do_POST()
            self.assertEqual(self._status(h), 413)
            self.assertTrue(h.close_connection)
            h = self._post_handler("/api/tasks", "10.0.0.5", b"")
            h.headers.replace_header("Content-Length", "-5")
            self.assertEqual(h._raw(), b"")
            h.headers.replace_header("Content-Length", "abc")
            self.assertEqual(h._raw(), b"")
            with self.assertRaises(m._util.BodyTooLarge):
                h.headers.replace_header("Content-Length", str(m._routes.BODY_MAX_AUDIO + 1)); h._raw(m._routes.BODY_MAX_AUDIO)
        finally:
            m._guests.instance_by_ip, m._auth.PW = old

    def test_cross_site_post_is_refused(self):
        """CSRF: a browser on another site (or a DNS-rebound name) sends its
        Origin and is refused; our own origins pass; clients without an Origin
        (app, desktop, curl) are untouched. Guests never carry one."""
        m = self.m
        old = m._guests.instance_by_ip, m._auth.PW, dict(m._auth._trusted_cache)
        try:
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW = ""
            m._auth._trusted_cache.update(ts=time.time() + 3600, hosts={"agents.example.com", "localhost", "192.168.1.10"})
            self.assertTrue(m._auth.origin_allowed(""))
            self.assertTrue(m._auth.origin_allowed("https://agents.example.com"))
            self.assertTrue(m._auth.origin_allowed("http://192.168.1.10:8700"))
            self.assertFalse(m._auth.origin_allowed("https://evil.example.org"))
            self.assertFalse(m._auth.origin_allowed("null"))
            h = self._post_handler("/api/settings", "10.0.0.5", b"{}", origin="https://evil.example.org")
            h.do_POST()
            self.assertEqual(self._status(h), 403)
            self.assertIn(b"cross-site", h.wfile.getvalue())
            h = self._post_handler("/api/nonexistent", "10.0.0.5", b"{}", origin="https://agents.example.com")
            h.do_POST()
            self.assertEqual(self._status(h), 404)          # passed the guard, no such route
        finally:
            m._guests.instance_by_ip, m._auth.PW = old[:2]
            m._auth._trusted_cache.update(old[2])

    def test_responses_carry_hardening_headers(self):
        m = self.m
        old = m._guests.instance_by_ip
        try:
            m._guests.instance_by_ip = lambda ip: None
            h = self._handler("/api/agents", "10.0.0.5")
            h._json({"ok": 1})
            head = h.wfile.getvalue().split(b"\r\n\r\n", 1)[0].lower()
            self.assertIn(b"x-content-type-options: nosniff", head)
            self.assertIn(b"x-frame-options: sameorigin", head)
        finally:
            m._guests.instance_by_ip = old

    def test_terminal_tunnel_forwards_only_upgrade_headers(self):
        """The browser's Authorization (Traefik's BasicAuth passes it on) and
        cookies must never reach a VM's webterm."""
        m = self.m
        items = [("Host", "agents.example.com"), ("Authorization", "Basic abc"), ("Cookie", "s=1"),
                 ("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Key", "k"),
                 ("Sec-WebSocket-Version", "13"), ("X-Forwarded-For", "1.2.3.4")]
        kept = dict(m._guestproxy.ws_forward_headers(items))
        self.assertEqual(set(kept), {"Host", "Upgrade", "Connection", "Sec-WebSocket-Key", "Sec-WebSocket-Version"})

    def test_chat_log_only_from_signal_guests_and_marked_in_inbox(self):
        """Only a Signal-transport VM may file user turns; the inbox marks them
        as relayed by that agent so the orchestrator knows who spoke."""
        m = self.m
        web = {"name": "web1", "template": "openrouter", "index": 3, "config": {"TRANSPORT": "web"}}
        old = m._guests.instance_by_ip, m._chats.load_chats, m._chats._inbox_wm, m._chats.INBOX_WM_FILE
        try:
            m._guests.instance_by_ip = lambda ip: web if ip == "172.30.3.2" else None
            h = self._post_handler("/api/chat-log", "172.30.3.2", b'{"sender":"x","user":"hi"}')
            h.do_POST()
            self.assertEqual(self._status(h), 403)
            m._chats.load_chats = lambda: [
                {"id": "sig-hass-4915", "instance": "hass", "title": "Signal", "updatedAt": 5000,
                 "messages": [{"user": True, "text": "Licht aus"}]},
                {"id": "web-1", "instance": "orchestrator", "title": "Web", "updatedAt": 5000,
                 "messages": [{"user": True, "text": "Plan"}]}]
            m._chats._inbox_wm = lambda: 0
            items = {i["id"]: i for i in m._chats.inbox_since(peek=True)}
            self.assertEqual(items["sig-hass-4915"]["via"], "signal:hass")
            self.assertTrue(items["sig-hass-4915"]["text"].startswith("[Signal message relayed by agent 'hass'] Licht aus"))
            self.assertNotIn("via", items["web-1"])
            self.assertEqual(items["web-1"]["text"], "Plan")
        finally:
            m._guests.instance_by_ip, m._chats.load_chats, m._chats._inbox_wm, m._chats.INBOX_WM_FILE = old

    def test_mount_validation_and_guest_mount_list(self):
        """A host folder never exposes the manager tree, the agent sources or
        ~/.ssh; a guest path is never a system directory. The guest fetches
        its own list by IP from /api/mounts instead of the shared workspace."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mounts-")
        share = os.path.join(tmp, "share"); os.makedirs(share)
        old = m._browse.BROWSE_ROOTS, m._guests.instance_by_ip, m._mounts.mount_specs, m._instances.load_instances
        try:
            m._browse.BROWSE_ROOTS = (tmp,)
            self.assertEqual(m._mounts.mount_error(share, "/home/node/data"), "")
            self.assertIn("not a directory", m._mounts.mount_error(os.path.join(tmp, "nope"), "/x"))
            self.assertIn("must be under", m._mounts.mount_error("/srv", "/x"))
            m._browse.BROWSE_ROOTS = ("/",)
            self.assertIn("would expose", m._mounts.mount_error(m._paths.BASE, "/x"))
            self.assertIn("would expose", m._mounts.mount_error(os.path.dirname(m._paths.BASE), "/x"))   # contains it
            for bad in ("/bin", "/usr/local", "/etc/x", "/app", "/harness", "/config", "/memory", "/", "rel", "/a/../etc"):
                self.assertTrue(m._mounts.mount_error(share, bad), bad)
            m._browse.BROWSE_ROOTS = (tmp,)
            self.assertIn("error:", m._mounts.set_mounts.__doc__ or "error:")     # documented below via the route
            inst = {"name": "vm1", "index": 4, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4", "mounts": []}
            m._instances.load_instances = lambda: [inst]
            m._mounts.mount_specs = lambda i: [{"sub": m._mounts.FCMNT_ROOT + "/vm1/0", "guest": "/home/node/data", "ro": True}]
            m._guests.instance_by_ip = lambda ip: inst if ip == "172.30.4.2" else None
            h = self._handler("/api/mounts", "172.30.4.2"); h._do_GET()
            self.assertIn(m._mounts.FCMNT_ROOT.encode() + b"/vm1/0|/home/node/data|ro\n", h.wfile.getvalue())
            h = self._handler("/api/mounts?instance=nope", "10.0.0.5"); h._do_GET()
            self.assertEqual(self._status(h), 404)
        finally:
            m._browse.BROWSE_ROOTS, m._guests.instance_by_ip, m._mounts.mount_specs, m._instances.load_instances = old

    def test_set_mounts_refuses_bad_folders(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-setm-")
        old = m._instances.load_instances, m._browse.BROWSE_ROOTS, m._paths.INST_DIR
        try:
            m._paths.INST_DIR = tmp
            m._browse.BROWSE_ROOTS = (tmp,)
            inst = {"name": "vm1", "index": 4, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4", "mounts": []}
            m._instances.load_instances = lambda: [inst]
            m._mounts.mount_specs = m._mounts.mount_specs
            r = m._mounts.set_mounts("vm1", [{"host": tmp, "guest": "/bin"}])
            self.assertTrue(r.startswith("error:"), r)
            self.assertFalse(os.path.exists(os.path.join(tmp, "vm1.json")))     # nothing saved
        finally:
            m._instances.load_instances, m._browse.BROWSE_ROOTS, m._paths.INST_DIR = old

    def test_js_json_and_download_name_and_rate(self):
        m = self.m
        self.assertNotIn("</script>", m._util.js_json({"d": "</script><img src=x>"}))
        self.assertEqual(json.loads(m._util.js_json({"d": "</script>"}))["d"], "</script>")
        self.assertEqual(m._util.download_name('a"b\r\nc.txt'), "a_b_c.txt")
        self.assertEqual(m._util.download_name(""), "file")
        key = ("t", "x")
        self.assertTrue(all(m._util.rate_ok(key, 3, 60) for _ in range(3)))
        self.assertFalse(m._util.rate_ok(key, 3, 60))

    def test_usage_report_accepted_for_local_models_only_when_proxied(self):
        """Key proxy on: the agent's figures are ignored — except a local model
        (LLAMA_ENDPOINT) called directly, whose report is flagged `direct`."""
        m = self.m
        old = m._settings.load_settings
        try:
            m._settings.load_settings = lambda: {"LLM_KEY_PROXY": "1"}
            llama = {"name": "u", "config": {"LLAMA_ENDPOINT": "http://10.0.0.5:8080/"}}
            self.assertTrue(m._routes_guest.usage_report_accepted(llama, {"direct": True}))
            self.assertFalse(m._routes_guest.usage_report_accepted(llama, {}))                       # old agent: not flagged
            self.assertFalse(m._routes_guest.usage_report_accepted({"name": "o", "config": {}}, {"direct": True}))  # proxied instance
            self.assertTrue(m._routes_guest.usage_report_accepted({"name": "c", "template": "claude", "config": {}}, {"direct": True}))  # claude = direct
            self.assertFalse(m._routes_guest.usage_report_accepted({"name": "c", "template": "claude", "config": {}}, {}))               # old bridge: not flagged
            m._settings.load_settings = lambda: {"LLM_KEY_PROXY": ""}
            self.assertTrue(m._routes_guest.usage_report_accepted({"name": "o", "config": {}}, {}))
        finally:
            m._settings.load_settings = old

    def test_hindsight_retains_user_turn_only(self):
        """A-1: the passive chat capture keeps the USER turn, never the agent's
        own reply (no memory poisoning), and HINDSIGHT_RETAIN=0 turns retention
        off for that instance; explicit notes obey the same switch."""
        m = self.m
        seen = []
        old = (m._instances.load_instances, m._guests.instance_by_ip, m._chats.save_chats, m._chats.load_chats, m._memfs.timeline_add,
               m._hindsight.retain_async)
        try:
            m._hindsight.retain_async = lambda inst, text, tags=(): seen.append((inst, text, tuple(tags)))
            m._memfs.timeline_add = lambda *a, **k: None
            m._chats.load_chats = lambda: []; m._chats.save_chats = lambda c: 1
            m._instances.load_instances = lambda: [{"name": "a", "config": {}}, {"name": "voice", "config": {"HINDSIGHT_RETAIN": "0"}}]
            self.assertTrue(m._instances.hindsight_retains("a")); self.assertFalse(m._instances.hindsight_retains("voice"))
            m._chats.chat_log_append("a", "", "mach das Radio an", "Das Radio ist an.", kind="voice")
            self.assertEqual(len(seen), 1)
            inst, text, tags = seen[0]
            self.assertEqual(inst, "a"); self.assertEqual(text, "mach das Radio an")   # user only
            self.assertNotIn("Das Radio ist an", text)                                  # NOT the agent reply
            self.assertIn("user", tags)
            seen.clear()
            m._chats.chat_log_append("voice", "", "hallo", "hi", kind="voice")                 # retention off
            self.assertEqual(seen, [])
        finally:
            (m._instances.load_instances, m._guests.instance_by_ip, m._chats.save_chats, m._chats.load_chats, m._memfs.timeline_add,
             m._hindsight.retain_async) = old

    def test_hindsight_second_memory(self):
        """Off without HINDSIGHT_URL (every call a no-op); on: retain sends the
        text to the instance's bank, recall hits merge into the memory search
        behind the semantic ones, reflect answers, and a dead server never
        raises."""
        import mgr.hindsight as hs
        m = self.m
        calls = []
        old = hs._settings, hs._call, m._store.sem_search, m._settings.load_settings
        try:
            hs.configure(lambda: {}, log=lambda *a, **k: None)
            self.assertFalse(hs.enabled()); self.assertFalse(hs.retain("vm1", "x")); self.assertEqual(hs.recall("vm1", "q"), [])
            self.assertIn("off", hs.reflect("vm1", "q"))
            hs.configure(lambda: {"HINDSIGHT_URL": "http://127.0.0.1:1/"}, log=lambda *a, **k: None)
            def fake(method, path, body=None, timeout=20):
                calls.append((method, path, body))
                if path.endswith("/recall"):
                    return {"results": [{"text": "Ulrich bikes to work", "score": 0.9}, {"content": "likes radio"}, {"text": ""}]}
                if path.endswith("/reflect"):
                    return {"text": "He commutes by bike."}
                return {"success": True}
            hs._call = fake
            self.assertTrue(hs.retain("vm 1", "User: hi\n\nAssistant: hello", ("chat",)))
            self.assertEqual(calls[-1][1], "/v1/default/banks/vm-1/memories")
            self.assertEqual(calls[-1][2]["items"][0]["tags"], ["chat"]); self.assertTrue(calls[-1][2]["async"])
            hits = hs.recall("vm1", "commute")
            self.assertEqual([h["text"] for h in hits], ["Ulrich bikes to work", "likes radio"])
            self.assertEqual(hits[0]["source"], "hindsight")
            self.assertEqual(hs.reflect("vm1", "how does he commute?"), "He commutes by bike.")
            self.assertEqual(hs.bank("a/b c"), "a-b-c")
            # merged into the manager's memory search, semantic first, no duplicates
            m._settings.load_settings = lambda: {"HINDSIGHT_URL": "http://127.0.0.1:1/"}
            m._hindsight.configure(m._settings.load_settings, log=lambda *a, **k: None); m._hindsight._call = fake
            m._store.sem_search = lambda inst, q, k=5: [{"score": 0.5, "text": "likes radio"}]
            h = self._post_handler("/api/memory-search", "10.0.0.9", json.dumps({"instance": "vm1", "query": "commute"}).encode())
            m._guests.instance_by_ip = lambda ip: None
            h._do_POST()
            d = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual([x["text"] for x in d["hits"]], ["likes radio", "Ulrich bikes to work"])
            def dead(method, path, body=None, timeout=20):
                raise OSError("connection refused")
            hs._call = dead
            self.assertFalse(hs.retain("vm1", "x", wait=True)); self.assertEqual(hs.recall("vm1", "q"), [])
            self.assertIn("error", hs.reflect("vm1", "q")); self.assertFalse(hs.health()[0])
        finally:
            hs._settings, hs._call, m._store.sem_search, m._settings.load_settings = old

    def test_internet_off_is_an_explicit_reject(self):
        """internet=False must not depend on the FORWARD policy: the tap gets
        its own chain that rejects everything outside the pool, jumped to
        first. With internet on, the chain ends in the usual ACCEPT."""
        m = self.m
        calls = []
        class R:
            def __init__(self, rc): self.returncode = rc
        def fake_sh(*a, **k):
            calls.append(a)
            return R(1 if "-C" in a else 0)
        old = m._util.sh, m._netfw.ensure_antispoof
        try:
            m._util.sh = fake_sh; m._netfw.ensure_antispoof = lambda inst: None
            inst = {"name": "vm1", "index": 3, "config": {}}
            m._netfw.apply_internet(inst, False)
            chain = m._netfw._fc_chain(inst)
            self.assertIn(("iptables", "-A", chain, "!", "-d", m._netfw.POOL, "-j", "REJECT"), calls)
            self.assertIn(("iptables", "-I", "FORWARD", "1", "-i", m._instances.net_of(inst)["tap"], "-j", chain), calls)
            self.assertFalse(any("ACCEPT" in a and chain in a for a in calls))
            calls.clear()
            m._netfw.apply_internet(inst, True)
            self.assertIn(("iptables", "-A", chain, "!", "-d", m._netfw.POOL, "-j", "ACCEPT"), calls)
            self.assertFalse(any(a[-1] == "REJECT" and "!" in a for a in calls))
        finally:
            m._util.sh, m._netfw.ensure_antispoof = old

    def test_browser_container_egress_is_internet_only(self):
        """The browser container may reach the internet but nothing private
        (LAN, host, VMs, hub). Rules sit in mangle/PREROUTING — ahead of the
        per-tap ACCEPTs in FORWARD — with the hub->browser MCP port and its
        replies first, and a re-run neither duplicates nor reorders them."""
        m = self.m
        rules = []
        class R:
            def __init__(self, rc): self.returncode = rc
        def fake_sh(*a, **k):
            spec = a[a.index("PREROUTING") + 1:]
            if "-C" in a:
                return R(0 if spec in rules else 1)
            if "-D" in a:
                rules.remove(spec)
            elif "-I" in a:
                rules.insert(int(spec[0]) - 1, spec[1:])
            return R(0)
        old = m._util.sh
        try:
            m._util.sh = fake_sh
            m._netfw.ensure_browser_rules()
            first = list(rules)
            m._netfw.ensure_browser_rules()
            self.assertEqual(rules, first)
            nf = m._netfw
            self.assertEqual(rules[0], ("-i", nf.BROWSER_BRIDGE, "-s", nf.BROWSER_HUB_IP, "-d", nf.BROWSER_IP,
                                        "-p", "tcp", "--dport", nf.BROWSER_PORT, "-j", "ACCEPT"))
            self.assertIn("ESTABLISHED", rules[1])
            drops = {r[3] for r in rules[2:] if r[-1] == "DROP"}
            self.assertEqual(len(drops), len(rules) - 2)
            for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                        "100.64.0.0/10", "127.0.0.0/8"):
                self.assertIn(net, drops)
        finally:
            m._util.sh = old

    def test_sandbox_through_the_task_route_and_the_queue(self):
        """A guest's spawn_subagent/create_task with a sandbox: the route turns
        it into {cfg, internet} and hands it to the run (wait) or stores it on
        the queued task; a sandbox on a named target or one that widens the
        caller's rights is refused; without a sandbox the old call shape."""
        m = self.m
        import mgr.store as st
        seen = []
        old = (m._guests.instance_by_ip, m._instances.load_instances, m._tasks._run_task_now, m._store.history_add, m._guests.guest_may_target,
               m._tasks._run_ephemeral, st.TASKS_FILE)
        try:
            caller = {"name": "orch", "config": {}}
            m._guests.instance_by_ip = lambda ip: caller if ip == "172.30.1.2" else None
            m._instances.load_instances = lambda: [caller, {"name": "hass"}]
            m._guests.guest_may_target = lambda inst, target: True
            m._store.history_add = lambda *a, **k: None
            m._tasks._run_task_now = lambda target, msg, model=None, timeout=600, sandbox=None: (seen.append((target, msg, sandbox)), (True, "ok"))[1]
            def post(body):
                h = self._post_handler("/api/task", "172.30.1.2", json.dumps(body).encode()); h._do_POST()
                return json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            d = post({"message": "x", "target": "ephemeral", "wait": True, "sandbox": {"tools": "bash", "egress": "none"}})
            self.assertEqual(d.get("result"), "ok")
            self.assertEqual(seen[-1], ("ephemeral", "x", {"cfg": {"AGENT_TOOLS": "bash"}, "internet": False}))
            d = post({"message": "x", "target": "ephemeral", "wait": True})
            self.assertEqual(seen[-1][2], None)                                                  # no cage: as before
            self.assertIn("ephemeral targets only", post({"message": "x", "target": "hass", "wait": True, "sandbox": {"tools": "bash"}})["error"])
            caller["config"] = {"AGENT_TOOLS": "read_file"}
            self.assertIn("does not hold", post({"message": "x", "target": "ephemeral", "wait": True, "sandbox": {"tools": "bash"}})["error"])
            caller["config"] = {}
            st.TASKS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-sbq-"), "tasks.json")
            d = post({"message": "later", "target": "ephemeral", "sandbox": {"skill": "nope"}})
            self.assertIn("unknown", d["error"])                                                 # refused at creation, not at 08:00
            d = post({"message": "later", "target": "ephemeral", "sandbox": {"egress": "api.example.com"}})
            t = next(x for x in st.load_tasks() if x["id"] == d["id"])
            self.assertEqual(t["sandbox"], {"cfg": {"EGRESS_ALLOW": "api.example.com"}, "internet": True})
            # the runner keeps the old call shape without a sandbox (tests and callers monkeypatch it)
            calls = []
            m._tasks._run_ephemeral = lambda *a, **k: (calls.append((a, k)), (True, "r"))[1]
            m._tasks._run_task_now = old[2]
            m._tasks._run_task_now("ephemeral", "do", None, sandbox={"cfg": {}, "internet": False})
            m._tasks._run_task_now("ephemeral", "do")
            self.assertEqual(len(calls[0][0]), 4); self.assertEqual(len(calls[1][0]), 3)
        finally:
            (m._guests.instance_by_ip, m._instances.load_instances, m._tasks._run_task_now, m._store.history_add, m._guests.guest_may_target,
             m._tasks._run_ephemeral, st.TASKS_FILE) = old

    def test_egress_allowlist_rules(self):
        """EGRESS_ALLOW: the chain accepts the resolved addresses and rejects
        everything else outside the pool; a host that does not resolve is
        skipped without opening the net."""
        m = self.m
        import socket as _socket
        calls = []
        class R:
            def __init__(self, rc): self.returncode = rc
        old = m._util.sh, m._netfw.ensure_antispoof, socket.getaddrinfo, m._netfw._mcp_endpoints, m._netfw._llama_endpoint
        try:
            m._util.sh = lambda *a, **k: (calls.append(a), R(1 if "-C" in a else 0))[1]
            m._netfw.ensure_antispoof = lambda inst: None
            m._netfw._mcp_endpoints = lambda inst: []; m._netfw._llama_endpoint = lambda inst: None
            def gai(host, *a, **k):
                if host == "api.example.com":
                    return [(2, 1, 6, "", ("93.184.216.34", 0))]
                raise OSError("no such host")
            socket.getaddrinfo = gai
            inst = {"name": "vm1", "index": 3, "config": {"EGRESS_ALLOW": "api.example.com, typo.invalid"}}
            m._netfw.apply_internet(inst, True)
            chain = m._netfw._fc_chain(inst)
            self.assertIn(("iptables", "-A", chain, "-d", "93.184.216.34", "-j", "ACCEPT"), calls)
            self.assertIn(("iptables", "-A", chain, "!", "-d", m._netfw.POOL, "-j", "REJECT"), calls)
            self.assertNotIn(("iptables", "-A", chain, "!", "-d", m._netfw.POOL, "-j", "ACCEPT"), calls)
            self.assertFalse(any("typo.invalid" in a for a in calls))
        finally:
            m._util.sh, m._netfw.ensure_antispoof, socket.getaddrinfo, m._netfw._mcp_endpoints, m._netfw._llama_endpoint = old

    def test_notification_text_lands_in_the_task_chat(self):
        """A guest's notification is also appended to the instance's task chat
        — the place a click on the notification opens; an admin notification
        touches no chat."""
        m = self.m
        seen = []
        old = m._guests.instance_by_ip, m._notify.notify_add, m._chats.chat_log_append, m._audit.audit_append
        try:
            m._guests.instance_by_ip = lambda ip: {"name": "orch"} if ip == "172.30.1.2" else None
            m._notify.notify_add = lambda inst, title, body, link="": ("id1", "")
            m._chats.chat_log_append = lambda inst, sender, u, r, kind="signal": seen.append((inst, u, r, kind)) or 1
            m._audit.audit_append = lambda *a, **k: None
            h = self._post_handler("/api/notify", "172.30.1.2", json.dumps({"title": "Saddler weekly", "message": "Failures 67 (was 20)"}).encode())
            h._do_POST()
            self.assertEqual(seen, [("orch", "", "🔔 Saddler weekly\n\nFailures 67 (was 20)", "task")])
            seen.clear()
            m._notify.notify_add = lambda inst, title, body, link="": ("id2", "")
            fake_key = "sk-" + "or-" + "v1-" + "de" * 32       # assembled at runtime so no literal key sits in the repo (GitHub push protection)
            h = self._post_handler("/api/notify", "172.30.1.2", json.dumps({"title": "t", "message": "key " + fake_key}).encode())
            h._do_POST()
            self.assertNotIn(fake_key, seen[0][2])                               # secret redacted in the chat copy too
            self.assertIn("[SECRET", seen[0][2])
            h = self._post_handler("/api/notify", "10.0.0.9", json.dumps({"title": "t", "message": "m"}).encode())
            h._do_POST()
            self.assertEqual(len(seen), 1)
        finally:
            m._guests.instance_by_ip, m._notify.notify_add, m._chats.chat_log_append, m._audit.audit_append = old

    def test_tool_allowlist_enforced_at_the_host(self):
        """A-2: a restricted instance is refused a capability it did not list;
        an instance with no allowlist keeps everything; admin is never gated."""
        m = self.m
        self.assertTrue(m._policy.tool_allowed({"config": {}}, "send_signal"))
        self.assertTrue(m._policy.tool_allowed({"config": {"AGENT_TOOLS": "bash,send_signal"}}, "send_signal"))
        self.assertFalse(m._policy.tool_allowed({"config": {"AGENT_TOOLS": "bash,read_file"}}, "send_signal"))
        self.assertFalse(m._policy.tool_allowed({"config": {"AGENT_TOOLS": "bash"}}, "ha_control"))
        seen = []
        old = m._guests.instance_by_ip, m._notify.notify_add, m._audit.audit_append, m._chats.chat_log_append
        try:
            m._notify.notify_add = lambda *a, **k: seen.append(a) or ("id", "")
            m._audit.audit_append = lambda *a, **k: None; m._chats.chat_log_append = lambda *a, **k: 1
            m._guests.instance_by_ip = lambda ip: {"name": "r", "config": {"AGENT_TOOLS": "bash"}} if ip == "172.30.1.2" else None
            h = self._post_handler("/api/notify", "172.30.1.2", json.dumps({"title": "t", "message": "m"}).encode())
            h._do_POST()
            self.assertEqual(self._status(h), 403); self.assertEqual(seen, [])
            m._guests.instance_by_ip = lambda ip: {"name": "o", "config": {}}
            h = self._post_handler("/api/notify", "172.30.1.2", json.dumps({"title": "t", "message": "m"}).encode())
            h._do_POST()
            self.assertEqual(self._status(h), 200); self.assertEqual(len(seen), 1)
        finally:
            m._guests.instance_by_ip, m._notify.notify_add, m._audit.audit_append, m._chats.chat_log_append = old

    def test_persona_carries_tools_and_model(self):
        """Feature 3: a persona keeps an optional recommended tool subset and
        model across save; unknown tools are dropped; empty stays absent."""
        m = self.m
        import mgr.store as st
        old = m._personas.load_personas, m._personas.save_personas
        saved = []
        try:
            m._personas.load_personas = lambda: []
            m._personas.save_personas = lambda items: saved.append(items)
            m._personas.upsert_persona("rev", "You review.", tools="read_file, bash, nope_tool", model=" google/gemini-2.5-flash ")
            ent = saved[-1][0]
            self.assertEqual(ent["name"], "rev")
            self.assertEqual(set(ent["tools"]), {"read_file", "bash"})     # unknown dropped
            self.assertEqual(ent["model"], "google/gemini-2.5-flash")
            saved.clear()
            m._personas.upsert_persona("plain", "hi")
            self.assertNotIn("tools", saved[-1][0]); self.assertNotIn("model", saved[-1][0])
            # regression: a web save (name+prompt only, tools/model = None) must
            # NOT wipe an existing persona's tools/model.
            saved.clear()
            m._personas.load_personas = lambda: [{"name": "rev", "prompt": "old", "tools": ["bash"], "model": "m/x"}]
            m._personas.upsert_persona("rev", "new prompt")                    # tools=None, model=None
            ent = saved[-1][0]
            self.assertEqual(ent["prompt"], "new prompt")
            self.assertEqual(ent["tools"], ["bash"]); self.assertEqual(ent["model"], "m/x")   # kept
            saved.clear()
            m._personas.upsert_persona("rev", "p", tools=[])                   # explicit empty -> clear
            self.assertNotIn("tools", saved[-1][0])
        finally:
            m._personas.load_personas, m._personas.save_personas = old

    def test_agent_package_invariants(self):
        """The agent package after the split: the root only imports the
        modules; a sibling is used as a module (`from . import x as _x`),
        never as an imported name — the tests rely on patching one place."""
        import ast as _ast
        pkg = os.path.dirname(AGENT_PATH)
        root = _ast.parse(open(AGENT_PATH).read())
        self.assertFalse([n for n in root.body if isinstance(n, (_ast.FunctionDef, _ast.ClassDef))],
                         "code in agent/__init__.py")
        for f in sorted(os.listdir(pkg)):
            if not f.endswith(".py"):
                continue
            for n in _ast.walk(_ast.parse(open(os.path.join(pkg, f)).read())):
                if isinstance(n, _ast.ImportFrom) and n.level >= 1:
                    self.assertIsNone(n.module, f"agent/{f}: `from .{n.module} import …` — use the module")

    def test_defense_baseline_prepended(self):
        """Feature 2: every instance's system prompt carries the prompt-defense
        baseline (untrusted-content rule) by default."""
        ag = _load("agent_defense", AGENT_PATH, {"OPENROUTER_API_KEY": "dummy"})
        self.assertIn("untrusted", ag._config.SYSTEM.lower())
        self.assertIn("outrank", ag._config.SYSTEM.lower())

    def test_sandbox_config_persona(self):
        """Feature 4: spawn_subagent(persona=) bakes the persona prompt, uses its
        recommended tools when none are requested, sets its model, and refuses an
        unknown persona."""
        m = self.m
        old = m._personas.load_personas, m._skills.load_skills
        try:
            m._personas.load_personas = lambda: [{"name": "assistant", "prompt": "You are helpful."},
                                       {"name": "code-reviewer", "prompt": "You review code.",
                                        "tools": ["bash", "read_file"], "model": "google/gemini-2.5-flash"}]
            m._skills.load_skills = lambda: []
            caller = {"name": "orch", "config": {}}
            cfg, net, err = m._policy.sandbox_config(caller, {"persona": "code-reviewer"})
            self.assertEqual(err, "")
            self.assertEqual(cfg["AGENT_TOOLS"], "bash,read_file")          # persona's tools
            self.assertEqual(cfg["AGENT_SYSTEM"], "You review code.")       # persona's prompt
            self.assertEqual(cfg["OPENROUTER_MODEL"], "google/gemini-2.5-flash")
            self.assertIn("unknown", m._policy.sandbox_config(caller, {"persona": "ghost"})[2])
            # an explicit tools list still overrides the persona's, and must stay a subset
            cfg2, _, err2 = m._policy.sandbox_config(caller, {"persona": "code-reviewer", "tools": "read_file"})
            self.assertEqual((cfg2["AGENT_TOOLS"], err2), ("read_file", ""))
            # an explicit model must win over the persona's model in the VM config
            seen = []
            old2 = m._instances.create_instance, m._instances.load_instances, m._guestchat.wait_web, m._tasks._chat_post, m._vm.stop, m._instances.delete_instance
            try:
                def create(name, tpl, cfg=None, mounts=None, internet=True):
                    seen.append(dict(cfg or {}))
                    m._instances.load_instances = lambda: [{"name": name, "template": "openrouter"}]
                    return "ok"
                m._instances.create_instance = create; m._guestchat.wait_web = lambda i, timeout=120: True
                m._tasks._chat_post = lambda i, msg, timeout=600: "r"; m._vm.stop = lambda i: None; m._instances.delete_instance = lambda n: None
                sb = {"cfg": {"OPENROUTER_MODEL": "persona/model", "AGENT_SYSTEM": "You review."}, "internet": True}
                m._tasks._run_ephemeral_vm("do", "explicit/model", 60, sb)
                self.assertEqual(seen[-1]["OPENROUTER_MODEL"], "explicit/model")
            finally:
                m._instances.create_instance, m._instances.load_instances, m._guestchat.wait_web, m._tasks._chat_post, m._vm.stop, m._instances.delete_instance = old2
        finally:
            m._personas.load_personas, m._skills.load_skills = old

    def test_sandbox_config_only_narrows(self):
        """A sandboxed sub-agent runs in an ephemeral VM with a NARROWER policy:
        a subset of the caller's tools (never the spawning/secret ones), an
        egress allowlist inside the caller's own, or no network, and one
        skill baked into the system prompt with the file/web tools only."""
        m = self.m
        old = m._skills.load_skills, m._personas.load_personas
        try:
            m._skills.load_skills = lambda: [{"name": "pdf-digest", "description": "d", "content": "Read the PDF, summarise."}]
            m._personas.load_personas = lambda: [{"name": "assistant", "prompt": "You are helpful."}]
            caller = {"name": "orch", "config": {}}
            self.assertEqual(m._policy.sandbox_config(caller, None), ({}, True, ""))
            cfg, net, err = m._policy.sandbox_config(caller, {"tools": "bash, read_file,spawn_subagent"})
            self.assertEqual((cfg["AGENT_TOOLS"], net, err), ("bash,read_file", True, ""))     # spawn never inherited
            self.assertIn("unknown tools", m._policy.sandbox_config(caller, {"tools": ["bash", "nope"]})[2])
            narrow = {"name": "n", "config": {"AGENT_TOOLS": "bash,read_file", "EGRESS_ALLOW": "api.example.com,cdn.example.com"}}
            self.assertIn("does not hold", m._policy.sandbox_config(narrow, {"tools": ["bash", "web_search"]})[2])
            self.assertNotIn("notify", m._policy.sandbox_config(caller, {"tools": "bash,notify"})[0]["AGENT_TOOLS"])   # notify never inherited
            self.assertIn("outside the caller", m._policy.sandbox_config(narrow, {"egress": "api.example.com,evil.example.org"})[2])
            cfg, net, err = m._policy.sandbox_config(narrow, {"egress": ["API.example.com"]})
            self.assertEqual((cfg["EGRESS_ALLOW"], net, err), ("api.example.com", True, ""))
            self.assertEqual(m._policy.sandbox_config(caller, {"egress": "none"})[1], False)
            self.assertIn("list hosts", m._policy.sandbox_config(caller, {"egress": [" "]})[2])
            cfg, net, err = m._policy.sandbox_config(caller, {"skill": "pdf-digest"})
            self.assertEqual(cfg["AGENT_TOOLS"], ",".join(sorted(m._policy.SANDBOX_DEFAULT_TOOLS)))
            self.assertIn("[Skill: pdf-digest]", cfg["AGENT_SYSTEM"]); self.assertTrue(cfg["AGENT_SYSTEM"].startswith("You are helpful."))
            self.assertIn("unknown", m._policy.sandbox_config(caller, {"skill": "ghost"})[2])
            self.assertIn("object", m._policy.sandbox_config(caller, "bash")[2])
        finally:
            m._skills.load_skills, m._personas.load_personas = old

    def test_sandbox_reaches_the_ephemeral_vm(self):
        """The sandbox travels from the task route into the VM's creation:
        the narrowed tools land in its config and 'egress none' creates it
        without internet; without a sandbox nothing changes."""
        m = self.m
        seen = []
        old = m._instances.create_instance, m._instances.load_instances, m._guestchat.wait_web, m._tasks._chat_post, m._vm.stop, m._instances.delete_instance
        try:
            m._instances.create_instance = lambda name, tpl, cfg=None, mounts=None, internet=True: seen.append((tpl, dict(cfg or {}), internet)) or "ok"
            m._instances.load_instances = lambda: [{"name": n, "template": "openrouter"} for n in ["x"]] if False else [{"name": seen[-1] and "task-x", "template": "openrouter"}]
            m._instances.load_instances = lambda: [{"name": next((k for k in ["any"]), ""), "template": "openrouter"}]
            m._guestchat.wait_web = lambda inst, timeout=120: True
            m._tasks._chat_post = lambda inst, message, timeout=600: "child says hi"
            m._vm.stop = lambda inst: None; m._instances.delete_instance = lambda name: None
            # load_instances must return the instance the run just created: match on prefix
            m._instances.load_instances = lambda: [{"name": "task-" + "".join(c for c in "0" * 6), "template": "openrouter"}]
            real_create = m._instances.create_instance
            def create(name, tpl, cfg=None, mounts=None, internet=True):
                m._instances.load_instances = lambda: [{"name": name, "template": "openrouter", "config": dict(cfg or {}), "internet": internet}]
                return real_create(name, tpl, cfg, mounts, internet)
            m._instances.create_instance = create
            ok, res = m._tasks._run_ephemeral_vm("do", None, 60, {"cfg": {"AGENT_TOOLS": "bash", "EGRESS_ALLOW": ""}, "internet": False})
            self.assertEqual((ok, res), (True, "child says hi"))
            self.assertEqual(seen[-1][1]["AGENT_TOOLS"], "bash"); self.assertFalse(seen[-1][2]); self.assertEqual(seen[-1][1]["NO_SPAWN"], "1")
            ok, res = m._tasks._run_ephemeral_vm("do", "google/gemini-2.5-flash", 60)
            self.assertNotIn("AGENT_TOOLS", seen[-1][1]); self.assertTrue(seen[-1][2]); self.assertEqual(seen[-1][1]["OPENROUTER_MODEL"], "google/gemini-2.5-flash")
            self.assertNotIn("AGENT_MAX_STEPS", seen[-1][1])
            ok, res = m._tasks._run_ephemeral_vm("do", None, 60, None, 7)             # the caller's tool-round cap
            self.assertEqual(seen[-1][1]["AGENT_MAX_STEPS"], "7")
        finally:
            m._instances.create_instance, m._instances.load_instances, m._guestchat.wait_web, m._tasks._chat_post, m._vm.stop, m._instances.delete_instance = old

    def test_admin_task_with_model_and_step_cap(self):
        """POST /api/tasks may carry model and max_steps — for an ephemeral VM
        only; the task keeps them and _run_task_now hands them to the VM."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-tkm-")
        old = m._store.TASKS_FILE, m._guests.instance_by_ip, m._tasks._run_ephemeral, m._instances.load_instances
        try:
            m._store.TASKS_FILE = os.path.join(tmp, "tasks.json")
            m._guests.instance_by_ip = lambda ip: None
            m._instances.load_instances = lambda: [{"name": "orchestrator", "config": {}}]
            def post(body):
                h = self._post_handler("/api/tasks", "127.0.0.1", json.dumps(body).encode(), origin="http://127.0.0.1:8700")
                h._do_POST()
                return json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1]).get("msg", "")
            self.assertIn("created", post({"instance": "ephemeral", "message": "m", "model": "x/y", "max_steps": 6}))
            t = m._store.load_tasks()[-1]
            self.assertEqual((t["model"], t["max_steps"]), ("x/y", 6))
            self.assertIn("ephemeral tasks only", post({"instance": "orchestrator", "message": "m", "model": "x/y"}))
            self.assertIn("1-50", post({"instance": "ephemeral", "message": "m", "max_steps": 99}))
            self.assertIn("whole number", post({"instance": "ephemeral", "message": "m", "max_steps": "ten"}))
            got = []
            m._tasks._run_ephemeral = lambda msg, model=None, timeout=600, sandbox=None, max_steps=0: got.append((model, max_steps)) or (True, "")
            m._tasks._run_task_now("ephemeral", "m", "x/y", max_steps=6)
            self.assertEqual(got, [("x/y", 6)])
        finally:
            m._store.TASKS_FILE, m._guests.instance_by_ip, m._tasks._run_ephemeral, m._instances.load_instances = old

    # ---- model router (Jev) ------------------------------------------------------------
    def _router_env(self, running):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-rt-")
        st, rt = m._store, m._router
        insts = [{"name": "cheapo", "template": "openrouter", "config": {"OPENROUTER_MODEL": "google/gemini-2.5-flash"}},
                 {"name": "big", "template": "openrouter", "config": {"ORCAROUTER_MODEL": "tencent/hy3", "MODEL_ROUTER": "default"}},
                 {"name": "big2", "template": "openrouter", "config": {"ORCAROUTER_MODEL": "tencent/hy3"}},
                 {"name": "local", "template": "llama", "config": {"LLAMA_ENDPOINT": "http://10.0.0.9:8080/", "LLAMA_MODEL": "local-model"}},
                 {"name": "claudy", "template": "claude", "config": {"ANTHROPIC_MODEL": "x"}},
                 {"name": "stopped", "template": "openrouter", "config": {"OPENROUTER_MODEL": "qwen/qwen3-coder"}}]
        old = (st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], rt.ROUTER_FILE, m._instances.load_instances,
               m._instances.is_running, m._settings.load_settings, rt.classify)
        st.HISTORY_DB, st._migrated[0], st._turns_migrated[0] = os.path.join(tmp, "history.db"), False, None
        rt.ROUTER_FILE = os.path.join(tmp, "router.json")
        m._instances.load_instances = lambda: [dict(i, config=dict(i["config"])) for i in insts]
        m._instances.is_running = lambda i: i["name"] in running
        m._settings.load_settings = lambda: {"JEV_URL": "http://127.0.0.1:8891"}
        rt._cache.clear()

        def restore():
            (st.HISTORY_DB, st._migrated[0], st._turns_migrated[0], rt.ROUTER_FILE, m._instances.load_instances,
             m._instances.is_running, m._settings.load_settings, rt.classify) = old
            rt._cache.clear()
        return insts, restore

    def test_router_candidates_premise_and_rules(self):
        """Candidates are the models of RUNNING instances (deduplicated, no
        claude); the premise is the last user turn without system/tool text;
        a rule needs every class above the floor and a tiered candidate."""
        m = self.m
        rt = m._router
        insts, restore = self._router_env({"cheapo", "big", "big2", "local", "claudy"})
        try:
            c = {x["key"]: x for x in rt.candidates()}
            self.assertEqual(sorted(c), ["llama/local-model", "openrouter/google/gemini-2.5-flash", "orcarouter/tencent/hy3"])
            self.assertEqual(c["orcarouter/tencent/hy3"]["instances"], ["big", "big2"])
            self.assertEqual(c["llama/local-model"]["upstream"], "http://10.0.0.9:8080/")
            p = rt.premise({"messages": [{"role": "system", "content": "SYSTEM SECRET"},
                                         {"role": "user", "content": "hi"},
                                         {"role": "assistant", "content": "⟦think⟧hm⟦/think⟧Hello"},
                                         {"role": "user", "content": [{"type": "text", "text": "fix my SQL"}]},
                                         {"role": "tool", "content": "TOOL OUTPUT"}]})
            self.assertEqual(p, "fix my SQL")                                    # the current request only
            self.assertEqual(rt.premise({"messages": [{"role": "system", "content": "x"}]}), "")
            pol = rt.DEFAULT_POLICY
            cheap = {"key": "openrouter/google/gemini-2.5-flash"}; strong = {"key": "orcarouter/tencent/hy3"}
            tiered = {"cheap": [cheap], "strong": [strong]}
            self.assertEqual(rt.pick(pol, {"difficulty": ("trivial", 0.9), "task": ("chat", 0.9)}, tiered), (cheap, 0))
            self.assertEqual(rt.pick(pol, {"difficulty": ("trivial", 0.4), "task": ("code", 0.9)}, tiered), (strong, 1))  # below floor
            self.assertEqual(rt.pick(pol, {"difficulty": ("moderate", 0.9), "task": ("text", 0.9)}, tiered), (None, None))
            self.assertEqual(rt.pick(pol, {"difficulty": ("trivial", 0.9)}, {"code": [strong]}), (None, None))  # no tier for the rule
        finally:
            restore()

    def test_router_route_is_opt_in_cached_per_turn_and_never_blocks(self):
        m = self.m
        rt, st = m._router, m._store
        insts, restore = self._router_env({"cheapo", "big", "local"})
        calls = []
        try:
            payload = {"messages": [{"role": "user", "content": "hello there"}]}
            big = next(i for i in insts if i["name"] == "big")
            rt.classify = lambda pol, text: (calls.append(text), ({"difficulty": ("trivial", 0.93), "task": ("chat", 0.9)}, 3100))[1]
            # no instance opted in -> nothing, not even a classification
            self.assertIsNone(rt.route(next(i for i in insts if i["name"] == "cheapo"), payload, "t1"))
            # opted in, but no model has a tier -> own model, Jev not asked
            self.assertIsNone(rt.route(big, payload, "t1"))
            self.assertEqual(calls, [])
            self.assertEqual(st.routes_read("big")[0]["why"], "no running model has a tier")
            rt._cache.clear()
            rt.save_tiers({"openrouter/google/gemini-2.5-flash": "cheap", "orcarouter/tencent/hy3": "strong", "x": "bogus"})
            self.assertEqual(rt.load()["tiers"], {"openrouter/google/gemini-2.5-flash": "cheap", "orcarouter/tencent/hy3": "strong"})
            t = rt.route(big, payload, "t2")
            self.assertEqual((t["backend"], t["model"]), ("openrouter", "google/gemini-2.5-flash"))
            self.assertEqual(rt.route(big, payload, "t2"), t)                       # the rest of the turn: cached
            self.assertEqual(len(calls), 1)
            r = st.routes_read("big", turn="t2")[0]
            self.assertEqual((r["own"], r["chosen"], r["ms"]), ("orcarouter/tencent/hy3", "openrouter/google/gemini-2.5-flash", 3100))
            self.assertEqual(r["classes"]["difficulty"], ["trivial", 0.93])
            # a stopped model is never a target, even with a tier
            rt.save_tiers({"openrouter/qwen/qwen3-coder": "cheap"})
            self.assertIsNone(rt.route(big, payload, "t3"))
            # Jev failing -> own model, reason recorded
            rt.save_tiers({"openrouter/google/gemini-2.5-flash": "cheap"})
            rt.classify = lambda pol, text: (_ for _ in ()).throw(TimeoutError("jev slow"))
            self.assertIsNone(rt.route(big, payload, "t4"))
            self.assertIn("jev slow", st.routes_read("big", turn="t4")[0]["why"])
            # the own model chosen -> no rewrite
            rt.save_tiers({"orcarouter/tencent/hy3": "cheap"})
            rt.classify = lambda pol, text: ({"difficulty": ("trivial", 0.9)}, 10)
            self.assertIsNone(rt.route(big, payload, "t5"))
            self.assertIn("own model", st.routes_read("big", turn="t5")[0]["why"])
            # the runtime key: policy-like names only, applies at once
            saved, old_save = [], m._instances.save_instance
            m._instances.save_instance = lambda i: saved.append(dict(i["config"]))
            try:
                self.assertIn("error", m._instances._set_config_key("big", "MODEL_ROUTER", "../x"))
                self.assertEqual(m._instances._set_config_key("big", "MODEL_ROUTER", "default"), "MODEL_ROUTER = default (applies at once)")
                self.assertEqual(m._instances._set_config_key("big", "MODEL_ROUTER", ""), "MODEL_ROUTER removed (applies at once)")
                self.assertNotIn("MODEL_ROUTER", saved[-1])
            finally:
                m._instances.save_instance = old_save
        finally:
            restore()

    def test_proxy_runs_the_turn_on_the_routed_model(self):
        """The key proxy rewrites model, upstream and key for a routed turn; a
        llama target goes to its endpoint without any key; a cloud target whose
        key is missing leaves the call on the instance's own model."""
        import types
        m = self.m
        lp = m._llmproxy
        seen = []
        inst = {"name": "big", "config": {"ORCAROUTER_MODEL": "tencent/hy3", "MODEL_ROUTER": "default"}}

        class FakeResp:
            status = 200
            headers = {"Content-Type": "application/json"}
            def read(self): return b'{"model":"m","usage":{"prompt_tokens":1,"completion_tokens":1}}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        old = (m._guests.instance_by_ip, m._settings.load_settings, m._router.route, lp.urllib.request.urlopen,
               m._store.usage_add, lp._guard_check)
        try:
            m._guests.instance_by_ip = lambda ip: inst if ip == "172.30.5.2" else None
            m._settings.load_settings = lambda: {"ORCAROUTER_API_KEY": "orc-key", "OPENROUTER_API_KEY": "or-key"}
            lp._guard_check = lambda i: (True, "")
            m._store.usage_add = lambda *a, **k: None
            lp.urllib.request.urlopen = lambda req, timeout=None: (seen.append(req), FakeResp())[1]
            def call():
                body = json.dumps({"model": "tencent/hy3", "messages": [{"role": "user", "content": "hi"}]}).encode()
                h = self._post_handler("/api/llm/orcarouter/chat/completions", "172.30.5.2", body)
                h.headers["X-Kaim-Turn"] = "t1"
                h._do_POST()
                r = seen[-1]
                return r.full_url, json.loads(r.data)["model"], r.get_header("Authorization")
            m._router.route = lambda i, body, turn: {"backend": "openrouter", "model": "google/gemini-2.5-flash", "upstream": None}
            self.assertEqual(call(), ("https://openrouter.ai/api/v1/chat/completions", "google/gemini-2.5-flash", "Bearer or-key"))
            m._router.route = lambda i, body, turn: {"backend": "llama", "model": "local-model", "upstream": "http://10.0.0.9:8080/"}
            self.assertEqual(call(), ("http://10.0.0.9:8080/v1/chat/completions", "local-model", None))
            m._router.route = lambda i, body, turn: None
            self.assertEqual(call(), ("https://api.orcarouter.ai/v1/chat/completions", "tencent/hy3", "Bearer orc-key"))
            m._settings.load_settings = lambda: {"ORCAROUTER_API_KEY": "orc-key"}           # openrouter key missing
            m._router.route = lambda i, body, turn: {"backend": "openrouter", "model": "google/gemini-2.5-flash", "upstream": None}
            self.assertEqual(call()[1:], ("tencent/hy3", "Bearer orc-key"))
            inst["config"].pop("MODEL_ROUTER")                                             # not opted in: never asked
            m._router.route = lambda *a: self.fail("router asked without opt-in")
            call()
        finally:
            (m._guests.instance_by_ip, m._settings.load_settings, m._router.route, lp.urllib.request.urlopen,
             m._store.usage_add, lp._guard_check) = old

    # ---- key proxy: max_tokens cap + job mode (2026-10-10) ----------------------
    def _llm_fake_env(self, settings):
        """A fake upstream (stdlib http.server) behind the openrouter backend;
        two guests by IP; usage bookings recorded. model "slow" waits for
        gate, "err" answers 500. -> (insts, seen bodies, booked, gate, undo)."""
        import http.server
        m, lp = self.m, self.m._llmproxy
        seen, booked, gate = [], [], threading.Event()

        class U(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                if body.get("model") == "slow":
                    gate.wait(10)
                if body.get("model") == "err":
                    out, code = b'{"error":{"message":"upstream broke"}}', 500
                else:
                    out, code = json.dumps({"model": body.get("model"), "choices": [{"message": {
                        "role": "assistant", "content": "hi"}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode(), 200
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), U)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        insts = {"172.30.5.2": {"name": "alpha", "config": {}}, "172.30.6.2": {"name": "beta", "config": {}}}
        old = (dict(lp.LLM_PROXY_UPSTREAMS), m._guests.instance_by_ip, m._settings.load_settings,
               m._store.usage_add, lp.JOB_TTL, lp.JOB_MAX_RUNNING, lp.used_today, m._notify.notify_add)
        lp.LLM_PROXY_UPSTREAMS["openrouter"] = (f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions",
                                                "OPENROUTER_API_KEY")
        m._guests.instance_by_ip = lambda ip: insts.get(ip)
        m._settings.load_settings = lambda: {"OPENROUTER_API_KEY": "k", **settings}
        m._store.usage_add = lambda name, model, pin, pout, cost=None, **k: booked.append((name, pin, pout, k.get("ok", True)))
        lp.used_today = lambda name: 0
        m._notify.notify_add = lambda *a, **k: None
        lp._guard_calls.clear()

        def undo():
            gate.set(); srv.shutdown(); srv.server_close()
            lp.LLM_PROXY_UPSTREAMS.clear(); lp.LLM_PROXY_UPSTREAMS.update(old[0])
            (m._guests.instance_by_ip, m._settings.load_settings, m._store.usage_add, lp.JOB_TTL,
             lp.JOB_MAX_RUNNING, lp.used_today, m._notify.notify_add) = old[1:]
            lp._jobs.clear(); lp._guard_calls.clear()
        return insts, seen, booked, gate, undo

    def _llm_req(self, path, ip, body=None):
        """POST (body given) or GET through the real handler -> (status, json)."""
        if body is None:
            h = self._handler(path, ip)
            h._do_GET()
        else:
            h = self._post_handler(path, ip, json.dumps(body).encode())
            h._do_POST()
        raw = h.wfile.getvalue()
        return self._status(h), json.loads(raw.split(b"\r\n\r\n", 1)[1] or b"null")

    def test_proxy_caps_max_tokens_per_instance_and_default(self):
        """LLM_MAX_TOKENS: the proxy clamps or adds max_tokens (instance key
        over the settings default; neither = untouched); the key is validated
        and runtime; the settings default reaches the guest env."""
        m = self.m
        insts, seen, booked, gate, undo = self._llm_fake_env({"LLM_MAX_TOKENS": "5000"})
        url = "/api/llm/openrouter/chat/completions"
        try:
            insts["172.30.5.2"]["config"]["LLM_MAX_TOKENS"] = "100"
            for extra, want in (({}, {"max_tokens": 100}), ({"max_tokens": 50}, {"max_tokens": 50}),
                                ({"max_tokens": 9999}, {"max_tokens": 100}), ({"max_tokens": -1}, {"max_tokens": 100}),
                                ({"max_completion_tokens": 999}, {"max_completion_tokens": 100})):
                st, d = self._llm_req(url, "172.30.5.2", {"model": "x", "messages": [], **extra})
                self.assertEqual(st, 200)
                self.assertEqual({k: seen[-1].get(k) for k in want}, want, extra)
            self.assertNotIn("max_tokens", seen[-1])                           # max_completion_tokens only
            self._llm_req(url, "172.30.6.2", {"model": "x", "messages": []})   # beta: the settings default
            self.assertEqual(seen[-1]["max_tokens"], 5000)
            m._settings.load_settings = lambda: {"OPENROUTER_API_KEY": "k"}
            self._llm_req(url, "172.30.6.2", {"model": "x", "messages": [], "max_tokens": 123456})
            self.assertEqual(seen[-1]["max_tokens"], 123456)                   # no cap anywhere: untouched
            self.assertEqual(booked[-1], ("beta", 7, 3, True))                 # sync path still books
            # validation: positive whole number up to the bound, runtime key
            saved, old_save, old_load = [], m._instances.save_instance, m._instances.load_instances
            m._instances.save_instance = lambda i: saved.append(dict(i["config"]))
            m._instances.load_instances = lambda: [{"name": "alpha", "config": {}}]
            try:
                for bad in ("0", "-5", "abc", "1.5", "131073"):
                    self.assertIn("error", m._instances._set_config_key("alpha", "LLM_MAX_TOKENS", bad), bad)
                self.assertEqual(m._instances._set_config_key("alpha", "LLM_MAX_TOKENS", "4096"),
                                 "LLM_MAX_TOKENS = 4096 (applies at once)")
            finally:
                m._instances.save_instance, m._instances.load_instances = old_save, old_load
            self.assertIn("LLM_MAX_TOKENS", m._settings.validate_decision({"LLM_MAX_TOKENS": "x"}))
            self.assertEqual(m._settings.validate_decision({"LLM_MAX_TOKENS": "16384"}), "")
            self.assertIn("LLM_MAX_TOKENS", [s["key"] for s in m._settings.SETTINGS_SCHEMA])
            m._settings.load_settings = lambda: {"LLM_MAX_TOKENS": "2048"}
            self.assertEqual(m._vm.guest_env({"name": "beta", "config": {}})["LLM_MAX_TOKENS"], "2048")
            self.assertEqual(m._vm.guest_env({"name": "alpha", "config": {"LLM_MAX_TOKENS": "64"}})["LLM_MAX_TOKENS"], "64")
        finally:
            undo()

    def test_proxy_jobs_submit_poll_error_ttl_owner_budget(self):
        """POST /api/llm/<backend>/jobs answers at once with an id; the manager
        holds the upstream call (never streamed, capped, booked like sync);
        GET /api/llm/jobs/<id> -> running, then the upstream answer or error;
        only the owner sees it; results expire; running jobs and the budget
        are bounded per instance."""
        lp = self.m._llmproxy
        insts, seen, booked, gate, undo = self._llm_fake_env({"LLM_MAX_TOKENS": "300"})
        jobs = "/api/llm/openrouter/jobs"

        def wait_done(jid, ip="172.30.5.2"):
            for _ in range(200):
                st, d = self._llm_req(f"/api/llm/jobs/{jid}", ip)
                if d != {"status": "running"}:
                    return st, d
                time.sleep(0.02)
            self.fail("job never finished")
        try:
            st, d = self._llm_req(jobs, "172.30.5.2", {"model": "slow", "messages": [], "stream": True,
                                                       "stream_options": {"include_usage": True}})
            self.assertEqual(st, 202)
            jid = d["job"]
            self.assertEqual(self._llm_req(f"/api/llm/jobs/{jid}", "172.30.5.2"), (200, {"status": "running"}))
            self.assertEqual(self._llm_req(f"/api/llm/jobs/{jid}", "172.30.6.2")[0], 404)   # beta: not its job
            self.assertEqual(self._llm_req("/api/llm/jobs/nope", "172.30.5.2")[0], 404)
            gate.set()
            st, d = wait_done(jid)
            self.assertEqual((st, d["choices"][0]["message"]["content"]), (200, "hi"))
            self.assertNotIn("stream", seen[-1]); self.assertNotIn("stream_options", seen[-1])
            self.assertEqual(seen[-1]["max_tokens"], 300)                      # the cap holds for jobs too
            self.assertEqual(booked[-1], ("alpha", 7, 3, True))                # booked by the manager
            self.assertEqual(self._llm_req(f"/api/llm/jobs/{jid}", "172.30.6.2")[0], 404)   # done: still alpha's only
            # upstream error: status + body through, a failed span booked
            st, d = self._llm_req(jobs, "172.30.5.2", {"model": "err", "messages": []})
            st, d = wait_done(d["job"])
            self.assertEqual((st, d["error"]["message"]), (500, "upstream broke"))
            self.assertEqual(booked[-1][0::3], ("alpha", False))
            # TTL: a finished result is swept
            lp.JOB_TTL = -1
            self.assertEqual(self._llm_req(f"/api/llm/jobs/{jid}", "172.30.5.2")[0], 404)
            lp.JOB_TTL = 600
            # bounded: running jobs per instance
            gate.clear(); lp.JOB_MAX_RUNNING = 1
            self.assertEqual(self._llm_req(jobs, "172.30.6.2", {"model": "slow", "messages": []})[0], 202)
            st, d = self._llm_req(jobs, "172.30.6.2", {"model": "slow", "messages": []})
            self.assertEqual(st, 429); self.assertIn("running", d["error"])
            self.assertEqual(self._llm_req(jobs, "172.30.5.2", {"model": "x", "messages": []})[0], 202)  # alpha unaffected
            gate.set()
            # budget guard applies to job submits exactly as to sync calls
            insts["172.30.5.2"]["config"]["BUDGET_TOKENS"] = "10"
            lp.used_today = lambda name: 11
            st, d = self._llm_req(jobs, "172.30.5.2", {"model": "x", "messages": []})
            self.assertEqual(st, 429); self.assertIn("budget", d["error"]["message"])
            self.assertEqual(self._llm_req("/api/llm/openrouter/jobz", "172.30.6.2", {})[0], 404)
        finally:
            undo()

    # ---- cost router: judge, ladder, escalation, savings ------------------------------
    def _cost_env(self, running=("cheapo", "big", "local")):
        """_router_env plus a catalog in the cache (no network) and the
        judge/settings swappable; `big` runs with MODEL_ROUTER=cheapest."""
        m = self.m
        insts, restore = self._router_env(set(running))
        next(i for i in insts if i["name"] == "big")["config"]["MODEL_ROUTER"] = "cheapest"
        md, dc = m._models, m._decision
        old = (dict(md._ormodels), dc.judge, m._tasks._run_named, m._tasks._run_task_now, m._squads.cost_routing_on)
        md._ormodels.update(ts=time.time(), data=[
            {"id": "google/gemini-2.5-flash", "pin": 0.3, "pout": 2.5, "tools": True, "vision": True, "ctx": 1_000_000},
            {"id": "tencent/hy3", "pin": 3.0, "pout": 15.0, "tools": True, "vision": False, "ctx": 200_000},
            {"id": "acme/cheap4", "pin": 0.1, "pout": 0.4, "tools": True, "vision": False, "ctx": 128_000}])
        m._squads.cost_routing_on = lambda project: False
        dc._judged.clear()

        def undo():
            md._ormodels.clear(); md._ormodels.update(old[0])
            dc.judge, m._tasks._run_named, m._tasks._run_task_now, m._squads.cost_routing_on = old[1:]
            dc._judged.clear(); m._router._plans.clear(); m._router._bump.clear()
            restore()
        return insts, undo

    def test_cost_judge_maps_jev_classes_and_parses_llm_json(self):
        m = self.m
        dc, rt = m._decision, m._router
        lv = dc.level_of
        self.assertEqual(lv({"task": ("chat", 0.9), "difficulty": ("trivial", 0.9)}), 1)
        self.assertEqual(lv({"task": ("chat", 0.9), "difficulty": ("moderate", 0.9)}), 2)
        self.assertEqual(lv({"task": ("code", 0.9), "difficulty": ("moderate", 0.8)}), 3)
        self.assertEqual(lv({"task": ("code", 0.9), "difficulty": ("moderate", 0.8), "scope": ("multi", 0.7)}), 4)
        self.assertEqual(lv({"task": ("reasoning", 0.9), "difficulty": ("hard", 0.9)}), 5)
        self.assertEqual(lv({"task": ("code", 0.3), "difficulty": ("hard", 0.2)}), 3)            # nothing sure
        old = (m._settings.load_settings, rt.jev_classify, dc.urllib.request.urlopen)
        try:
            dc._judged.clear()
            m._settings.load_settings = lambda: {"JEV_URL": "http://jev"}
            rt.jev_classify = lambda pol, text: ({"task": ("code", 0.9), "difficulty": ("hard", 0.8),
                                                  "scope": ("one", 0.6), "vision": ("yes", 0.9)}, 40)
            j = dc.judge("refactor the parser")
            self.assertEqual((j["level"], j["needs"]["vision"], j["needs"]["tools"], j["backend"]), (4, True, True, "jev"))
            rt.jev_classify = lambda *a: self.fail("cached per text")
            self.assertEqual(dc.judge("refactor the parser")["level"], 4)
            # the LLM backend: one strict-JSON question, think tags and prose around it tolerated
            dc._judged.clear()
            m._settings.load_settings = lambda: {"DECISION_BACKEND": "local-llm", "DECISION_ROUTER": "1",
                                                 "LLAMA_ENDPOINT": "http://10.0.0.9:8080"}
            sent = []

            class Resp:
                def __init__(self, body): self.body = body
                def read(self, n=-1): return self.body
                def __enter__(self): return self
                def __exit__(self, *a): return False
            content = '<think>hm</think>Sure: {"answers": {"level": {"2": 0.1, "5": 0.8, "9": 1}, "vision": {"no": 0.9}}}'
            dc.urllib.request.urlopen = lambda req, timeout=None: (sent.append(json.loads(req.data)), Resp(json.dumps(
                {"choices": [{"message": {"content": content}}]}).encode()))[1]
            j = dc.judge("prove the lemma")
            self.assertEqual((j["level"], j["backend"], j["needs"]["vision"]), (5, "local-llm", False))
            self.assertEqual(sorted(json.loads(sent[0]["messages"][1]["content"])["questions"]), ["level", "vision"])
            # the LLM failing -> Jev; both failing -> level 3 with a note, never an exception
            dc._judged.clear()
            dc.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
            m._settings.load_settings = lambda: {"DECISION_BACKEND": "local-llm", "DECISION_ROUTER": "1",
                                                 "LLAMA_ENDPOINT": "http://x", "JEV_URL": "http://jev"}
            rt.jev_classify = lambda pol, text: ({"task": ("chat", 0.9), "difficulty": ("trivial", 0.9)}, 5)
            j = dc.judge("hi there")
            self.assertEqual((j["level"], j["backend"]), (1, "jev")); self.assertIn("local-llm failed", j["note"])
            dc._judged.clear()
            rt.jev_classify = lambda *a: (_ for _ in ()).throw(TimeoutError("slow"))
            j = dc.judge("hi there")
            self.assertEqual(j["level"], 3); self.assertIn("level 3", j["note"])
            self.assertEqual(dc.judge("")["level"], 3)
            self.assertTrue(dc.sufficient("q", "a"))                                         # a failing check never escalates
        finally:
            m._settings.load_settings, rt.jev_classify, dc.urllib.request.urlopen = old
            dc._judged.clear()

    def test_cost_ladder_picks_cheapest_sufficient_model(self):
        m = self.m
        rt, st = m._router, m._store
        insts, undo = self._cost_env()
        try:
            rt.save_tiers({"openrouter/google/gemini-2.5-flash": "cheap", "orcarouter/tencent/hy3": "strong",
                           "llama/local-model": "local"})
            lad = {e["key"]: e for e in rt.ladder()}
            self.assertEqual({k: (e["level"], e["price"]) for k, e in lad.items()},
                             {"llama/local-model": (2, 0.0), "openrouter/google/gemini-2.5-flash": (2, 0.85),
                              "orcarouter/tencent/hy3": (5, 6.0)})
            L = list(lad.values())
            self.assertEqual(rt.choose(L, 1)["key"], "llama/local-model")                       # free wins
            self.assertEqual(rt.choose(L, 2, {"vision": True})["key"], "openrouter/google/gemini-2.5-flash")
            self.assertEqual(rt.choose(L, 3)["key"], "orcarouter/tencent/hy3")
            self.assertIsNone(rt.choose(L, 5, {"vision": True}))
            self.assertEqual(rt.choose(L, 2, {"long_context": True})["key"], "openrouter/google/gemini-2.5-flash")  # llama ctx unknown
            tie = [dict(lad["llama/local-model"], key="a/x", level=2), dict(lad["llama/local-model"], key="b/y", level=4),
                   dict(lad["llama/local-model"], key="c/z", level=3, price=None)]
            self.assertEqual(rt.choose(tie, 2)["key"], "b/y")                                    # tie -> higher level
            self.assertEqual(rt.choose(tie[2:], 3)["key"], "c/z")                                # unknown price, last resort
            # the operator's level beats the tier's; a catalog model only with "catalog": true
            rt.save_levels({"openrouter/google/gemini-2.5-flash": 3, "openrouter/acme/cheap4": 4, "x": 9})
            self.assertEqual(rt.load()["levels"], {"openrouter/google/gemini-2.5-flash": 3, "openrouter/acme/cheap4": 4})
            self.assertEqual(rt.load()["tiers"]["llama/local-model"], "local")                  # tiers kept
            self.assertEqual(rt.choose(rt.ladder(), 3)["key"], "openrouter/google/gemini-2.5-flash")
            self.assertEqual(rt.choose(rt.ladder(catalog=True), 3)["key"], "openrouter/acme/cheap4")
            # route(): MODEL_ROUTER=cheapest judges and runs the turn on the pick
            big = next(i for i in insts if i["name"] == "big")
            m._decision.judge = lambda text, context="": {"level": 1, "needs": {"vision": False}, "score": 0.9, "ms": 7}
            payload = {"messages": [{"role": "user", "content": "hello"}], "tools": [{"type": "function"}]}
            t = rt.route(big, payload, "c1")
            self.assertEqual((t["backend"], t["model"]), ("llama", "local-model"))
            r = st.routes_read("big", turn="c1")[0]
            self.assertEqual((r["level"], r["price"], r["escalated"], r["excerpt"]), (1, 0.0, 0, "hello"))
            self.assertIn("cheapest L2 llama/local-model", r["why"])
            # nothing fits -> own model, recorded
            m._decision.judge = lambda text, context="": {"level": 5, "needs": {"vision": True}, "score": 0.9, "ms": 7}
            self.assertIsNone(rt.route(big, payload, "c2"))
            self.assertIn("no ladder model fits", st.routes_read("big", turn="c2")[0]["why"])
            # the rule-based mode is untouched: policy default asks classify(), never judge()
            big["config"]["MODEL_ROUTER"] = "default"
            m._decision.judge = lambda *a, **k: self.fail("judge in rules mode")
            rt.classify = lambda pol, text: ({"difficulty": ("trivial", 0.9), "task": ("chat", 0.9)}, 3)
            self.assertEqual(rt.route(big, payload, "c3")["key"], "openrouter/google/gemini-2.5-flash")
            self.assertIn("rule 0", st.routes_read("big", turn="c3")[0]["why"])
            self.assertIn("cheapest", rt.policy_names())
        finally:
            undo()

    def test_cost_escalation_runs_once_and_records_both(self):
        m = self.m
        rt, st, tk = m._router, m._store, m._tasks
        insts, undo = self._cost_env()
        try:
            rt.save_tiers({"llama/local-model": "local", "openrouter/google/gemini-2.5-flash": "code",
                           "orcarouter/tencent/hy3": "strong"})
            big = next(i for i in insts if i["name"] == "big")
            replies, runs = [], []

            def run_named(instance, message, timeout=600, turn=None):
                # what the proxy does on the turn's first LLM call
                tgt = rt.route(big, {"messages": [{"role": "user", "content": message}]}, turn)
                runs.append((turn, (tgt or {}).get("key")))
                return replies.pop(0)
            tk._run_named = run_named
            replies[:] = [(True, "...\n(max tool steps reached)"), (True, "done")]
            ok, res, level = tk._run_routed({"id": "x", "instance": "big", "message": "fix it", "level": 2}, "fix it")
            self.assertEqual((ok, res, level), (True, "done", 2))
            self.assertEqual([k for _, k in runs], ["llama/local-model", "openrouter/google/gemini-2.5-flash"])  # L2 -> L3+: code (4)
            rows = {r["turn"]: r for r in st.routes_read("big")}
            first, second = rows[runs[0][0]], rows[runs[1][0]]
            self.assertEqual((first["level"], first["result"], first["escalated"]), (2, "failed", 0))
            self.assertEqual((second["level"], second["result"], second["escalated"]), (3, "ok", 1))
            # failing twice: still exactly one escalation
            runs.clear(); replies[:] = [(False, "error: boom"), (True, "⚠️ upstream 500"), (True, "never")]
            ok, res, _ = tk._run_routed({"id": "y", "instance": "big", "message": "fix it", "level": 2}, "fix it")
            self.assertEqual((len(runs), ok, res), (2, True, "⚠️ upstream 500"))
            self.assertEqual(replies, [(True, "never")])
            # not under cost routing: the old path, untouched
            seen = []
            tk._run_task_now = lambda *a, **k: (seen.append((a, k)), (True, "plain"))[1]
            big["config"]["MODEL_ROUTER"] = "default"
            self.assertEqual(tk._run_routed({"id": "z", "instance": "big", "message": "m"}, "m"), (True, "plain", None))
            self.assertEqual(seen[0][0][:2], ("big", "m"))
            # squad cost routing: on for an OpenAI-style member, never for claude
            m._squads.cost_routing_on = lambda project: project == "sq"
            self.assertIs(rt.cost_routing(big, True), rt.CHEAPEST)
            self.assertIsNone(rt.cost_routing({"name": "c", "template": "claude", "config": {"ANTHROPIC_MODEL": "x"}}, True))
            # a chat turn: a failure moves the NEXT turn one level up, once
            big["config"]["MODEL_ROUTER"] = "cheapest"
            m._decision.judge = lambda text, context="": {"level": 1, "needs": {}, "score": 0.9, "ms": 1}
            p = {"messages": [{"role": "user", "content": "hi"}]}
            self.assertEqual(rt.route(big, p, "h1")["key"], "llama/local-model")
            rt.outcome("big", "h1", "max_steps")
            self.assertEqual(st.routes_read("big", turn="h1")[0]["result"], "max_steps")
            self.assertEqual(rt.route(big, p, "h2")["key"], "openrouter/google/gemini-2.5-flash")  # above L2
            self.assertEqual(st.routes_read("big", turn="h2")[0]["escalated"], 1)
            rt.outcome("big", "h2", "error")                                                   # escalated: no further bump
            self.assertEqual(rt.route(big, p, "h3")["key"], "llama/local-model")
        finally:
            undo()

    def test_cost_ticket_level_stored_editable_and_used(self):
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        A, L = "172.30.3.2", "127.0.0.1"
        old_judge = m._decision.judge
        try:
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            m._squadgate.decide("sq", pid, True, "admin")
            kw = []
            m._squads.wake = lambda member, msg, project, depth, mid, **k: kw.append(k) or "t1"
            m._decision.judge = lambda text, context="": {"level": 4, "needs": {}, "score": 0.8, "note": ""}
            st, t = self._sq("/api/squad/ticket-create", A, {"title": "split the API", "assignee": "d", "level": 1})
            self.assertEqual((st, t["level"], t["level_by"]), (200, 4, "judge"))                   # the lead cannot set it
            self.assertEqual(kw[-1], {"level": 4})                                               # the wake carries it
            st, t2 = self._sq("/api/projects/sq/squad/ticket", L, {"id": t["id"], "level": 2})
            self.assertEqual((st, t2["level"]), (200, 2))
            self.assertEqual(self._sq("/api/projects/sq/squad/ticket", L, {"id": t["id"], "level": 7})[0], 400)
            self.assertEqual(next(x for x in m._squadgate.tickets("sq") if x["id"] == t["id"])["level"], 2)
            s, _ = m._squads.normalize_settings({"cost_routing": True}, {})
            self.assertTrue(s["cost_routing"])
            self.assertEqual(m._squads.wake.__name__, "<lambda>")
        finally:
            m._decision.judge = old_judge
            undo()
        # the task worker uses the wake's level for the planned turn
        insts, undo = self._cost_env()
        try:
            m._router.save_tiers({"llama/local-model": "local", "orcarouter/tencent/hy3": "strong"})
            big = next(i for i in insts if i["name"] == "big")
            big["config"].pop("MODEL_ROUTER")
            m._squads.cost_routing_on = lambda project: project == "sq"
            got = []
            m._tasks._run_named = lambda inst, msg, timeout=600, turn=None: (
                got.append(m._router.route(big, {"messages": [{"role": "user", "content": msg}]}, turn)), (True, "ok"))[1]
            m._decision.judge = lambda *a, **k: self.fail("the ticket's level is used, not judged again")
            self.assertEqual(m._tasks._run_routed({"id": "w", "instance": "big", "message": "m",
                                                   "squad": {"project": "sq", "level": 2}}, "m")[2], 2)
            self.assertEqual(got[0]["key"], "llama/local-model")
            self.assertTrue(m._router.planned("big", m._store.routes_read("big")[0]["turn"]))
        finally:
            undo()

    def test_cost_savings_vs_always_strong(self):
        m = self.m
        rt, st = m._router, m._store
        insts, undo = self._cost_env()
        try:
            self.assertIn("error", rt.savings())                                                # nothing priced yet
            rt.save_tiers({"llama/local-model": "local", "openrouter/google/gemini-2.5-flash": "cheap",
                           "orcarouter/tencent/hy3": "strong"})
            st.route_add("big", "s1", "orcarouter/tencent/hy3", "llama/local-model", {}, 0, "x", level=1)
            st.route_add("big", "s2", "orcarouter/tencent/hy3", "openrouter/google/gemini-2.5-flash", {}, 0, "x", level=2)
            st.usage_add("big", "local-model", 1_000_000, 100_000, 0, turn="s1")
            st.usage_add("big", "google/gemini-2.5-flash", 1_000_000, 0, 0.25, turn="s2")
            st.usage_add("big", "tencent/hy3", 5_000_000, 0, 9.0, turn="unrouted")               # not a routed turn
            s = rt.savings()
            self.assertEqual(s["strong"], "orcarouter/tencent/hy3")
            self.assertEqual(s["24h"], {"turns": 2, "in": 2_000_000, "out": 100_000, "actual": 0.25,
                                        "strong": 7.5, "saved": 7.25})
            self.assertEqual(s["30d"]["saved"], 7.25)
            self.assertEqual(rt.savings(now=time.time() + 3 * 86400)["24h"]["turns"], 0)
        finally:
            undo()

    def test_cost_router_ui_bits(self):
        import importlib
        js = importlib.import_module("mgr.ui_js").JS
        html = open(os.path.join(os.path.dirname(self.m._ui.__file__), "ui_html.py")).read()
        for s in ("function loadRouterSavings", "/api/router/savings", "/api/router/levels", "function setLvl",
                  "sq-cost_routing", "{level:+this.value}", "lvlSel('openrouter/'+m.id"):
            self.assertIn(s, js)
        for s in ("id=rtsave", "Routing: saved vs. always-strong", "id=sq-cost_routing", "<th style=\"width:110px\">Level</th>"):
            self.assertIn(s, html)

    def test_proxy_books_upstream_usage(self):
        m = self.m
        seen = []
        old = m._store.usage_add
        try:
            m._store.usage_add = lambda *a, **kw: seen.append(a)
            inst = {"name": "vm1"}
            m._llmproxy._proxy_usage(inst, "openrouter", b'{"model":"x/y","usage":{"prompt_tokens":10,"completion_tokens":5,"cost":0.001}}')
            m._llmproxy._proxy_usage(inst, "openrouter", b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n')
            m._llmproxy._proxy_usage(inst, "openrouter", b'data: [DONE]\n')
            m._llmproxy._proxy_usage(None, "openrouter", b'{"usage":{"prompt_tokens":10}}')
            self.assertEqual(seen, [("vm1", "x/y", 10, 5, 0.001), ("vm1", "openrouter", 1, 2, None)])
        finally:
            m._store.usage_add = old

    def test_nfs_exports_are_per_instance_and_squashed_to_the_guest_user(self):
        """No pool-wide root export: the workspace agent/<name> and each host
        folder are exported to that VM's address only, by absolute path, with
        every write squashed to the guest user; the config disk tells the guest
        where its workspace is; the old root export is retired once."""
        import types
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-nfs-")
        calls = []
        old = (m._mounts.AGENT_ROOT, m._mounts.FCMNT_ROOT, m._mounts.EXPORTS_D, m._mounts.AGENT_EXPORTS, m._util.sh, m._mounts.mount_specs,
               m._mounts.GUEST_UID, m._mounts.GUEST_GID, m._mounts.ensure_guest_user)
        try:
            m._mounts.AGENT_ROOT = os.path.join(tmp, "agent"); m._mounts.FCMNT_ROOT = os.path.join(m._mounts.AGENT_ROOT, ".fcmnt")
            m._mounts.EXPORTS_D = os.path.join(tmp, "exports.d"); m._mounts.AGENT_EXPORTS = os.path.join(m._mounts.EXPORTS_D, "agent.exports")
            os.makedirs(m._mounts.EXPORTS_D)
            with open(m._mounts.AGENT_EXPORTS, "w") as fh:
                fh.write(f"{m._mounts.AGENT_ROOT} {m._netfw.POOL}(rw,fsid=0,crossmnt)\n")
            m._util.sh = lambda *a, check=True: (calls.append(a), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
            m._mounts.GUEST_UID, m._mounts.GUEST_GID = 4242, 4243
            m._mounts.ensure_guest_user = lambda: True
            share = os.path.join(tmp, "share"); os.makedirs(share)
            inst = {"name": "vm1", "index": 7, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4",
                    "config": {}, "mounts": [{"host": share, "guest": "/home/node/data", "readonly": True}]}
            m._mounts.mount_specs = lambda i: [{"idx": 0, "host": share, "guest": "/home/node/data", "ro": True,
                                        "target": os.path.join(m._mounts.FCMNT_ROOT, "vm1", "0"),
                                        "sub": os.path.join(m._mounts.FCMNT_ROOT, "vm1", "0"), "fsid": 4000 + 7 * 16}]
            m._mounts.setup_mounts(inst)
            ex = open(os.path.join(m._mounts.EXPORTS_D, "fc-vm1.exports")).read()
            ws = os.path.join(m._mounts.AGENT_ROOT, "vm1")
            self.assertIn(f"{ws} 172.30.7.2(rw,sync,no_subtree_check,all_squash,anonuid=4242,anongid=4243,fsid={4000 + 7 * 16 + 14})", ex)
            self.assertIn(f"{m._mounts.FCMNT_ROOT}/vm1/0 172.30.7.2(ro,", ex)
            self.assertNotIn(m._netfw.POOL, ex)                                   # nothing for the whole pool
            self.assertTrue(os.path.isdir(ws))
            self.assertFalse(os.path.exists(m._mounts.AGENT_EXPORTS))              # root export retired …
            self.assertTrue(os.path.exists(m._mounts.AGENT_EXPORTS + ".bak"))      # … with a backup
            self.assertFalse(m._mounts.retire_root_export())                       # idempotent
            self.assertEqual(m._vm.guest_env(inst)["AGENT_EXPORT"], ws)
            self.assertEqual(m._vm.guest_env(inst)["MEMORY_DIR"], "/memory")
            self.assertEqual(m._vm.guest_env(inst)["GUEST_DNS"], m._netfw.GUEST_DNS)
            self.assertEqual(m._vm.guest_env(inst)["WEB_TIMEOUT"], os.environ.get("TASK_TIMEOUT", "1800"))   # Claude bridge = worker
            self.assertEqual(m._vm.guest_env({**inst, "config": {"WEB_TIMEOUT": "60"}})["WEB_TIMEOUT"], "60")
            # real mount_specs: absolute host paths as NFS subpaths, fsid slots 0..13 for folders
            m._mounts.mount_specs = old[5]
            sp = m._mounts.mount_specs({**inst, "index": 3, "template": "claude", "rootfs": "instances/claude-rootfs.ext4"})
            self.assertEqual(sp[0]["sub"], os.path.join(m._mounts.FCMNT_ROOT, "vm1", "0"))
            self.assertEqual(sp[0]["fsid"], 4000 + 3 * 16)
            self.assertEqual(m._mounts.workspace_fsid({"index": 3}), 4000 + 3 * 16 + 14)
            self.assertTrue(any(c[:2] == ("exportfs", "-ra") for c in calls))
        finally:
            (m._mounts.AGENT_ROOT, m._mounts.FCMNT_ROOT, m._mounts.EXPORTS_D, m._mounts.AGENT_EXPORTS, m._util.sh, m._mounts.mount_specs,
             m._mounts.GUEST_UID, m._mounts.GUEST_GID, m._mounts.ensure_guest_user) = old

    def test_guest_user_and_writability_hint(self):
        """Without root the squash user is not created (exports fall back to
        uid 1000); a rw share the guest user cannot write is flagged."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-gw-")
        old = m._mounts.GUEST_UID, m._mounts.GUEST_GID, m._mounts.GUEST_USER
        try:
            if os.geteuid() != 0:
                m._mounts.GUEST_USER = "kaim56-e2e-nonexistent"
                self.assertFalse(m._mounts.ensure_guest_user())
            m._mounts.GUEST_UID, m._mounts.GUEST_GID = 4242, 4243
            os.chmod(tmp, 0o755)
            self.assertFalse(m._mounts.guest_can_write(tmp))          # owned by us, not the guest user
            os.chmod(tmp, 0o777)
            self.assertTrue(m._mounts.guest_can_write(tmp))
            m._mounts.GUEST_UID = os.getuid(); os.chmod(tmp, 0o700)
            self.assertTrue(m._mounts.guest_can_write(tmp))           # owner = guest user
        finally:
            m._mounts.GUEST_UID, m._mounts.GUEST_GID, m._mounts.GUEST_USER = old

    # ---- OIDC login (mgr/oidc.py, mgr/sessions.py, mgr/routes_auth.py) ------------
    def _oidc_env(self):
        """A fake IdP (stdlib http.server, Rauthy-shaped issuer with a path and
        a trailing slash) plus OIDC switched on against it; temp sessions DB and
        audit dir. Returns (idp state dict, cleanup)."""
        import http.server, hashlib
        m = self.m
        st = {"codes": {}, "roles": ["kaim56_admin"], "rt": "rt-1", "n": 0, "refresh_fail": False,
              "device_ok": False, "sub": "u-1", "name": "alice"}

        def jwt(claims):
            enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
            return enc({"alg": "RS256"}) + "." + enc(claims) + ".sig"

        class IdP(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def _tokens(self, nonce=None):
                st["n"] += 1
                st["rt"] = f"rt-{st['n'] + 1}"            # rotation: every grant hands out a new one
                claims = {"iss": st["iss"], "aud": "kaim56", "sub": st["sub"]}
                if nonce:
                    claims["nonce"] = nonce
                return {"access_token": f"at-{st['n']}", "id_token": jwt(claims), "refresh_token": st["rt"]}

            def do_GET(self):
                if self.path == "/auth/v1/.well-known/openid-configuration":
                    b = st["iss"].rstrip("/")
                    return self._out({"issuer": st["iss"], "authorization_endpoint": b + "/oidc/authorize",
                                      "token_endpoint": b + "/oidc/token", "userinfo_endpoint": b + "/oidc/userinfo",
                                      "device_authorization_endpoint": b + "/oidc/device",
                                      "end_session_endpoint": b + "/oidc/logout"})
                if self.path == "/auth/v1/oidc/userinfo" and self.headers.get("Authorization", "").startswith("Bearer at-"):
                    return self._out({"sub": st["sub"], "preferred_username": st["name"], "roles": st["roles"]})
                self._out({"error": "nope"}, 401)

            def do_POST(self):
                f = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                if (f.get("client_id"), f.get("client_secret")) != ("kaim56", "csecret"):
                    return self._out({"error": "invalid_client"}, 401)
                if self.path == "/auth/v1/oidc/device":
                    return self._out({"device_code": "dc-1", "user_code": "WXYZ", "verification_uri": "u",
                                      "verification_uri_complete": "u?c=WXYZ", "expires_in": 600, "interval": 1})
                g = f.get("grant_type")
                if g == "authorization_code":
                    c = st["codes"].pop(f.get("code"), None)
                    ok = c and f.get("redirect_uri") == c["redirect"] and base64.urlsafe_b64encode(
                        hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode() == c["challenge"]
                    return self._out(self._tokens(c["nonce"])) if ok else self._out({"error": "invalid_grant"}, 400)
                if g == "refresh_token":
                    if st["refresh_fail"] or f.get("refresh_token") != st["rt"]:
                        return self._out({"error": "invalid_grant"}, 400)
                    return self._out(self._tokens())
                if g == "urn:ietf:params:oauth:grant-type:device_code":
                    if f.get("device_code") != "dc-1":
                        return self._out({"error": "invalid_grant"}, 400)
                    return self._out(self._tokens()) if st["device_ok"] else self._out({"error": "authorization_pending"}, 400)
                self._out({"error": "unsupported_grant_type"}, 400)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), IdP)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st["iss"] = f"http://127.0.0.1:{srv.server_port}/auth/v1/"
        tmp = tempfile.mkdtemp(prefix="e2e-oidc-")
        o = m._oidc
        old = (o.ISSUER, o.CLIENT_ID, o.CLIENT_SECRET, m._sessions.DB, m._audit.AUDIT_DIR,
               m._settings.PUBLIC_HOST, m._guests.instance_by_ip, m._auth.PW, m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE, m._store.TASKS_FILE)
        o.ISSUER, o.CLIENT_ID, o.CLIENT_SECRET = st["iss"], "kaim56", "csecret"
        o._disc.update(ts=0.0, doc=None); o._pending.clear(); o._devices.clear()
        m._sessions.DB = os.path.join(tmp, "sessions.db")
        m._audit.AUDIT_DIR = os.path.join(tmp, "audit")
        m._settings.PUBLIC_HOST = "agents.example.com"
        m._guests.instance_by_ip = lambda ip: None
        m._auth.PW = ""
        m._chats.CHATS_FILE = os.path.join(tmp, "chats.json")
        m._chats.TOMBSTONES_FILE = os.path.join(tmp, "tombs.json")
        m._store.TASKS_FILE = os.path.join(tmp, "tasks.json")
        m._auth._auth_fails.clear(); m._auth._rate.clear()

        def cleanup():
            srv.shutdown(); srv.server_close()
            (o.ISSUER, o.CLIENT_ID, o.CLIENT_SECRET, m._sessions.DB, m._audit.AUDIT_DIR,
             m._settings.PUBLIC_HOST, m._guests.instance_by_ip, m._auth.PW, m._chats.CHATS_FILE, m._chats.TOMBSTONES_FILE, m._store.TASKS_FILE) = old
            o._disc.update(ts=0.0, doc=None)
            m._auth._auth_fails.clear(); m._auth._rate.clear()
        self.addCleanup(cleanup)
        st["tmp"] = tmp
        return st

    def _oidc_req(self, path, method="GET", cookie=None, bearer=None, body=None):
        h = self._post_handler(path, "10.0.0.9", json.dumps(body or {}).encode()) if method == "POST" \
            else self._handler(path, "10.0.0.9")
        h.headers["Host"] = "agents.example.com"
        if cookie:
            h.headers["Cookie"] = cookie
        if bearer:
            h.headers["Authorization"] = f"Bearer {bearer}"
        (h.do_POST if method == "POST" else h.do_GET)()
        raw = h.wfile.getvalue()
        head, _, data = raw.partition(b"\r\n\r\n")
        return self._status(h), head.decode(), data

    def _oidc_web_login(self, st):
        """Run the browser part: /auth/login -> (fake authorize) -> /auth/callback."""
        code, head, _ = self._oidc_req("/auth/login")
        self.assertEqual(code, 302)
        loc = re.search(r"Location: (\S+)", head).group(1)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))
        self.assertEqual((q["code_challenge_method"], q["scope"], q["response_type"]), ("S256", "openid profile email", "code"))
        self.assertTrue(loc.startswith(st["iss"].rstrip("/") + "/oidc/authorize?"))
        bind = re.search(r"Set-Cookie: kaim56_login=([^;]+);[^\r\n]*", head)
        self.assertIn("HttpOnly", bind.group(0)); self.assertIn("SameSite=Lax", bind.group(0))
        self.assertEqual(bind.group(1), q["state"])
        st["codes"]["c-1"] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "redirect": q["redirect_uri"]}
        return q, self._oidc_req(f"/auth/callback?state={q['state']}&code=c-1", cookie=f"kaim56_login={q['state']}")

    def _idx_req(self, path, method="GET", cookie=None, auth=None, origin=None, host="index.example.com"):
        h = self._post_handler(path, "10.0.0.9", b"{}", origin=origin) if method == "POST" else self._handler(path, "10.0.0.9")
        h.headers["Host"] = host
        if auth:
            h.headers["Authorization"] = auth
        if cookie:
            h.headers["Cookie"] = cookie
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, data = h.wfile.getvalue().partition(b"\r\n\r\n")
        return self._status(h), head.decode(), data

    def _idx_login(self, st, code="c-2"):
        """The dashboard login on INDEX_HOST: own redirect_uri, back to the page it was for."""
        c, head, _ = self._idx_req("/indexer/open/sq")
        self.assertEqual(c, 302); self.assertIn("Location: /auth/login?next=%2Findexer%2Fopen%2Fsq\r\n", head + "\r\n")
        c, head, _ = self._idx_req("/auth/login?next=%2Findexer%2Fopen%2Fsq")
        self.assertEqual(c, 302)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(re.search(r"Location: (\S+)", head).group(1)).query))
        self.assertEqual(q["redirect_uri"], "https://index.example.com/auth/callback")
        self.assertIn("kaim56_next=/indexer/open/sq;", head)
        st["codes"][code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "redirect": q["redirect_uri"]}
        return self._idx_req(f"/auth/callback?state={q['state']}&code={code}",
                             cookie=f"kaim56_login={q['state']}; kaim56_next=/indexer/open/sq")

    def test_index_host_isolation(self):
        """INDEX_HOST (the code dashboard's own origin): an OIDC login there
        makes a "dash" session (admins only, own redirect_uri and host-only
        cookie) valid on that host only; manager sessions, Bearer and Basic are
        not accepted there, nor dash sessions on the manager; only the dashboard
        and login paths answer there; the manager has no /indexer/ui any more
        and refuses POSTs from that Origin; POSTs there are same-origin only;
        never framed, no Referer."""
        m = self.m
        st = self._oidc_env()
        old = m._settings.INDEX_HOST, m._indexer.dashboard_target, m._auth.PW
        m._settings.INDEX_HOST = "index.example.com"
        m._indexer.dashboard_target = lambda: None          # the dashboard answers 503 "not running"
        def undo():
            m._settings.INDEX_HOST, m._indexer.dashboard_target, m._auth.PW = old
            m._auth._trusted_cache.update(ts=0.0)
        self.addCleanup(undo)
        _, (_, head, _) = self._oidc_web_login(st)
        web = re.search(r"kaim56_sid=([^;]+)", head).group(1)
        code, head, body = self._idx_login(st)
        self.assertEqual(code, 200, body)
        ck = re.search(r"Set-Cookie: __Host-kaim56_dash=([^;]+);([^\r\n]*)", head)
        for flag in ("HttpOnly", "Secure", "Path=/", f"Max-Age={m._sessions.TTL['dash']}"):
            self.assertIn(flag, ck.group(2))
        self.assertNotIn("Domain", ck.group(2)); self.assertNotIn("kaim56_sid=", head)
        self.assertIn(b'url=/indexer/open/sq"', body)
        dash = ck.group(1)
        self.assertEqual(sorted(s["kind"] for s in m._sessions.list_all()), ["dash", "web"])
        D = f"__Host-kaim56_dash={dash}"
        # who gets onto INDEX_HOST: the dash session only (503 = past auth, the dashboard is down)
        self.assertEqual(self._idx_req("/indexer/ui/", cookie=D)[0], 503)
        m._auth.PW = "s3cret"
        for kw in ({"cookie": f"kaim56_sid={web}"}, {"cookie": f"__Host-kaim56_dash={web}"},
                   {"auth": f"Bearer {web}"}, {"auth": f"Bearer {dash}"},
                   {"auth": "Basic " + base64.b64encode(b"admin:s3cret").decode()}, {}):
            code, head, _ = self._idx_req("/indexer/ui/", **kw)
            self.assertEqual((code, "Location: /auth/login" in head), (302, True), kw)
        self.assertEqual(self._idx_req("/indexer/ui/api/x", "POST", auth="Basic " + base64.b64encode(b"admin:s3cret").decode(),
                                       origin="https://index.example.com")[0], 401)
        m._auth.PW = ""
        # the dash session is no good on the manager's host
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={dash}")[0], 401)
        self.assertEqual(self._oidc_req("/api/auth/me", bearer=dash)[0], 401)
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={web}")[0], 200)
        # nothing else answers on INDEX_HOST, with or without a session
        for meth, p in (("GET", "/"), ("GET", "/api/instances"), ("GET", "/api/auth/me"), ("GET", "/i/a1/"),
                        ("GET", "/katfs/"), ("GET", "/indexerx"), ("POST", "/api/auth/device"), ("POST", "/api/tasks")):
            self.assertEqual(self._idx_req(p, meth, cookie=D, origin="https://index.example.com")[0], 404, p)
        # the manager's host: no dashboard, the open goes over to INDEX_HOST
        ckw = f"kaim56_sid={web}"
        self.assertEqual(self._idx_req("/indexer/ui/", cookie=ckw, host="agents.example.com")[0], 404)
        code, head, _ = self._idx_req("/indexer/open/sq", cookie=ckw, host="agents.example.com")
        self.assertEqual(code, 302); self.assertIn("Location: https://index.example.com/indexer/open/sq\r\n", head + "\r\n")
        # CSRF: the manager refuses INDEX_HOST's Origin; INDEX_HOST takes its own only
        m._auth._trusted_cache.update(ts=0.0)                # built afresh with this test's PUBLIC_HOST
        self.assertFalse(m._auth.origin_allowed("https://index.example.com"))
        self.assertTrue(m._auth.origin_allowed("https://agents.example.com"))
        code, _, body = self._idx_req("/api/tasks", "POST", cookie=ckw, origin="https://index.example.com", host="agents.example.com")
        self.assertEqual((code, b"cross-site" in body), (403, True))
        for o in ("https://agents.example.com", None, "null"):
            self.assertEqual(self._idx_req("/indexer/ui/api/x", "POST", cookie=D, origin=o)[0], 403, o)
        self.assertEqual(self._idx_req("/indexer/ui/api/x", "POST", cookie=D, origin="https://index.example.com")[0], 503)
        # headers: never framed, no Referer out; the manager keeps SAMEORIGIN
        _, head, _ = self._idx_req("/indexer/ui/", cookie=D)
        for hd in ("X-Frame-Options: DENY", "Content-Security-Policy: frame-ancestors 'none'", "Referrer-Policy: no-referrer"):
            self.assertIn(hd, head)
        _, head, _ = self._idx_req("/api/auth/me", cookie=ckw, host="agents.example.com")
        self.assertIn("X-Frame-Options: SAMEORIGIN", head); self.assertNotIn("Referrer-Policy", head)
        # INDEX_HOST unset: no dashboard anywhere, no link
        m._settings.INDEX_HOST = ""
        self.assertEqual(self._idx_req("/indexer/open/sq", cookie=ckw, host="agents.example.com")[0], 404)
        self.assertIn("const INDEX_HOST=\"\";", m._ui.render())
        m._settings.INDEX_HOST = "index.example.com"
        self.assertIn("const INDEX_HOST=\"index.example.com\";", m._ui.render())
        # logout there ends the dash session only; role user gets no dash session
        code, head, _ = self._idx_req("/auth/logout", cookie=D)
        self.assertIn("__Host-kaim56_dash=;", head)
        self.assertEqual([s["kind"] for s in m._sessions.list_all()], ["web"])
        st["roles"] = ["kaim56_user"]
        code, head, _ = self._idx_login(st, "c-3")
        self.assertEqual(code, 403); self.assertNotIn("__Host-kaim56_dash=", head)
        self.assertEqual([s["kind"] for s in m._sessions.list_all()], ["web"])

    def _apps_login(self, st, code="c-4", nxt="/apps/board/"):
        """The apps login on APPS_HOST: own redirect_uri, back to the app it was for."""
        A = "apps.example.com"
        c, head, _ = self._idx_req(nxt, host=A)
        q_next = urllib.parse.quote(nxt, safe="")
        self.assertEqual(c, 302); self.assertIn(f"Location: /auth/login?next={q_next}\r\n", head + "\r\n")
        c, head, _ = self._idx_req(f"/auth/login?next={q_next}", host=A)
        self.assertEqual(c, 302)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(re.search(r"Location: (\S+)", head).group(1)).query))
        self.assertEqual(q["redirect_uri"], "https://apps.example.com/auth/callback")
        self.assertIn(f"kaim56_next={nxt};", head)
        st["codes"][code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "redirect": q["redirect_uri"]}
        return self._idx_req(f"/auth/callback?state={q['state']}&code={code}", host=A,
                             cookie=f"kaim56_login={q['state']}; kaim56_next={nxt}")

    def test_apps_host_isolation(self):
        """APPS_HOST (the browser apps' own origin): an OIDC login there makes
        an "apps" session (admin and role user, own redirect_uri and host-only
        cookie) valid on that host only; manager sessions, Bearer and Basic are
        not accepted there, nor apps sessions on the manager; there only the
        apps, the apps' API paths and the login answer; POSTs there are
        same-origin only, the manager refuses that Origin; the manager sends
        browser sessions' /apps/ there, while the APK (Basic/Bearer) and cloud
        app tokens keep working on the manager's host; unset = as before."""
        m, cl = self.m, self.m._cloud
        st = self._oidc_env()
        tmp = tempfile.mkdtemp(prefix="e2e-appshost-")
        os.makedirs(os.path.join(tmp, "board"))
        for f, body in (("app.json", '{"title": "Board"}'), ("index.html", "<h1>board</h1>")):
            with open(os.path.join(tmp, "board", f), "w") as fh:
                fh.write(body)
        old = (m._settings.APPS_HOST, m._settings.SITE.get("APPS_DIR"), m._settings.load_settings, cl.CLOUD_FILE, m._auth.PW)
        m._settings.SITE["APPS_DIR"] = tmp
        m._settings.load_settings = lambda: {}                  # no celld: the manager serves its copy, _api is 503
        cl.CLOUD_FILE = os.path.join(tmp, "cloud.json")
        def undo():
            (m._settings.APPS_HOST, m._settings.SITE["APPS_DIR"], m._settings.load_settings, cl.CLOUD_FILE, m._auth.PW) = old
            m._auth._trusted_cache.update(ts=0.0)
        self.addCleanup(undo)
        A, M = "apps.example.com", "agents.example.com"
        _, (_, head, _) = self._oidc_web_login(st)
        web = re.search(r"kaim56_sid=([^;]+)", head).group(1)
        W = f"kaim56_sid={web}"
        # off: the apps stay on the manager's host, the Apps tab links there
        m._settings.APPS_HOST = ""
        code, _, body = self._idx_req("/apps/board/", cookie=W, host=M)
        self.assertEqual((code, body), (200, b"<h1>board</h1>"))
        self.assertIn("const APPS_HOST=\"\";", m._ui.render())
        m._settings.APPS_HOST = A
        self.assertIn("const APPS_HOST=\"apps.example.com\";", m._ui.render())
        # the login there: an "apps" session in its own host-only cookie, back to the app
        code, head, body = self._apps_login(st)
        self.assertEqual(code, 200, body)
        ck = re.search(r"Set-Cookie: __Host-kaim56_apps=([^;]+);([^\r\n]*)", head)
        for flag in ("HttpOnly", "Secure", "Path=/", f"Max-Age={m._sessions.TTL['apps']}"):
            self.assertIn(flag, ck.group(2))
        self.assertNotIn("Domain", ck.group(2)); self.assertNotIn("kaim56_sid=", head)
        self.assertIn(b'url=/apps/board/"', body)
        apps = ck.group(1)
        self.assertEqual(sorted(s["kind"] for s in m._sessions.list_all()), ["apps", "web"])
        AP = f"__Host-kaim56_apps={apps}"
        # on APPS_HOST: the app, its _api, the apps' API paths
        self.assertEqual(self._idx_req("/apps/board/", cookie=AP, host=A)[2], b"<h1>board</h1>")
        self.assertEqual(self._idx_req("/apps/board/_api/x", cookie=AP, host=A)[0], 503)       # past auth: no celld
        self.assertEqual(self._idx_req("/api/instances", cookie=AP, host=A)[0], 200)
        # nobody else gets in: manager session, Bearer, Basic, nothing
        m._auth.PW = "s3cret"
        basic = "Basic " + base64.b64encode(b"admin:s3cret").decode()
        for kw in ({"cookie": W}, {"cookie": f"__Host-kaim56_apps={web}"}, {"auth": f"Bearer {web}"},
                   {"auth": f"Bearer {apps}"}, {"auth": basic}, {}):
            code, head, _ = self._idx_req("/apps/board/", host=A, **kw)
            self.assertEqual((code, "Location: /auth/login" in head), (302, True), kw)
            self.assertEqual(self._idx_req("/api/instances", host=A, **kw)[0], 401, kw)   # API: no login redirect
        self.assertEqual(self._idx_req("/apps/board/_api/x", "POST", auth=basic, origin="https://apps.example.com", host=A)[0], 401)
        # nothing else answers there, with or without a session
        for meth, p in (("GET", "/"), ("GET", "/api/auth/me"), ("GET", "/i/a1/"), ("GET", "/katfs/"),
                        ("GET", "/indexer/ui/"), ("GET", "/api/security"), ("POST", "/api/settings"),
                        ("POST", "/api/secret-store"), ("POST", "/api/apps/board/delete"), ("POST", "/api/auth/device")):
            self.assertEqual(self._idx_req(p, meth, cookie=AP, origin="https://apps.example.com", host=A)[0], 404, p)
        # the apps session is no good on the manager's host
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={apps}")[0], 401)
        self.assertEqual(self._oidc_req("/api/auth/me", bearer=apps)[0], 401)
        # CSRF: APPS_HOST takes its own Origin only; the manager refuses it
        for o in ("https://agents.example.com", None, "null"):
            self.assertEqual(self._idx_req("/apps/board/_api/x", "POST", cookie=AP, origin=o, host=A)[0], 403, o)
        self.assertEqual(self._idx_req("/apps/board/_api/x", "POST", cookie=AP, origin="https://apps.example.com", host=A)[0], 503)
        m._auth._trusted_cache.update(ts=0.0)
        self.assertFalse(m._auth.origin_allowed("https://apps.example.com"))
        self.assertTrue(m._auth.origin_allowed("https://agents.example.com"))
        code, _, body = self._idx_req("/api/tasks", "POST", cookie=W, origin="https://apps.example.com", host=M)
        self.assertEqual((code, b"cross-site" in body), (403, True))
        # the manager's host: browser sessions go to APPS_HOST, their app POSTs are refused
        code, head, _ = self._idx_req("/apps/board/x.js?v=2", cookie=W, host=M)
        self.assertEqual(code, 302); self.assertIn("Location: https://apps.example.com/apps/board/x.js?v=2\r\n", head + "\r\n")
        code, _, body = self._idx_req("/apps/board/_api/x", "POST", cookie=W, origin="https://agents.example.com", host=M)
        self.assertEqual((code, b"apps run on" in body), (403, True))
        # ... the APK (Basic, Bearer device session; also over iroh) keeps the path here
        self.assertEqual(self._idx_req("/apps/board/", auth=basic, host=M)[:3:2], (200, b"<h1>board</h1>"))
        h = self._handler("/apps/board/", "127.0.0.1", auth=basic); h.headers["Host"] = M; h.iroh_node = "ab" * 32
        h.do_GET(); self.assertEqual(self._status(h), 200)
        self.assertEqual(self._idx_req("/apps/board/_api/x", "POST", auth=basic, host=M)[0], 503)
        dev = m._sessions.create(m._oidc.Principal("u1", "u", "", "admin"), "device", "")
        self.assertEqual(self._idx_req("/apps/board/", auth=f"Bearer {dev}", host=M)[0], 200)
        # ... and a cloud app's token still reaches the apps' API on the manager
        cl._save({"board": {"where": "cloud", "url": "https://kaim56-board.x", "token_sha": cl._sha("cf-tok-1")}})
        self.assertEqual(self._idx_req("/api/instances", auth="Bearer cf-tok-1", host=M)[0], 200)
        cl._save({})
        m._auth.PW = ""
        # headers there: framed by itself only, no Referer out; the manager keeps SAMEORIGIN only
        _, head, _ = self._idx_req("/apps/board/", cookie=AP, host=A)
        for hd in ("X-Frame-Options: SAMEORIGIN", "Content-Security-Policy: frame-ancestors 'self'; object-src 'none'",
                   "Referrer-Policy: no-referrer"):
            self.assertIn(hd, head)
        _, head, _ = self._idx_req("/api/auth/me", cookie=W, host=M)
        self.assertNotIn("Referrer-Policy", head); self.assertNotIn("Content-Security-Policy", head)
        # logout there ends the apps session only
        code, head, _ = self._idx_req("/auth/logout", cookie=AP, host=A)
        self.assertIn("__Host-kaim56_apps=;", head)
        self.assertEqual(sorted(s["kind"] for s in m._sessions.list_all()), ["device", "web"])
        # role user: an apps session too, with the user's route table
        st["roles"] = ["kaim56_user"]
        code, head, _ = self._apps_login(st, "c-5")
        self.assertEqual(code, 200)
        U = "__Host-kaim56_apps=" + re.search(r"__Host-kaim56_apps=([^;]+)", head).group(1)
        self.assertEqual(self._idx_req("/apps/board/", cookie=U, host=A)[0], 200)
        self.assertEqual(self._idx_req("/api/instances", cookie=U, host=A)[0], 200)
        self.assertEqual(self._idx_req("/api/settings", cookie=U, host=A)[0], 403)          # an admin route
        # APPS_HOST off again: the manager's host serves browser sessions as before
        m._settings.APPS_HOST = ""
        self.assertEqual(self._idx_req("/apps/board/", cookie=W, host=M)[0], 200)
        self.assertEqual(self._idx_req("/apps/board/", cookie=AP, host=A)[0], 302)          # no side host: manager login

    def test_oidc_off_keeps_basic_auth(self):
        """Without OIDC_* nothing changes: no principal, Basic challenge, the
        /auth routes are 404."""
        m = self.m
        self.assertFalse(m._oidc.enabled())
        old = m._guests.instance_by_ip, m._auth.PW
        try:
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW = "s3cret"; m._auth._auth_fails.clear()
            h = self._handler("/api/auth/me", "10.0.0.9"); self.assertFalse(h._auth())
            self.assertEqual(self._status(h), 401); self.assertIn(b'Basic realm="kAIm56"', h.wfile.getvalue())
            h = self._handler("/api/auth/me", "10.0.0.9", auth="Basic " + base64.b64encode(b"admin:s3cret").decode())
            self.assertTrue(h._auth()); self.assertIsNone(h.principal)
            m._auth.PW = ""
            h = self._handler("/auth/login", "10.0.0.9"); h.do_GET()
            self.assertEqual(self._status(h), 404)
        finally:
            m._guests.instance_by_ip, m._auth.PW = old
            m._auth._auth_fails.clear()

    def test_oidc_web_login_code_pkce_and_cookie(self):
        """Discovery (issuer with path), code + PKCE S256 + nonce, the state bound
        to the browser, single-use; the session cookie is HttpOnly, Secure,
        SameSite=Strict; the login is in the access log; logout ends the session."""
        import hashlib, sqlite3
        from contextlib import closing
        m = self.m
        st = self._oidc_env()
        code, head, _ = self._oidc_req("/api/instances")
        self.assertEqual(code, 401)                                       # API: no redirect
        code, head, _ = self._oidc_req("/")
        self.assertEqual((code, "Location: /auth/login" in head), (302, True))   # page: to the login
        q, (code, head, body) = self._oidc_web_login(st)
        self.assertEqual(code, 200, body)
        sid = re.search(r"Set-Cookie: kaim56_sid=([^;]+);([^\r\n]*)", head)
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, sid.group(2))
        tok = sid.group(1)
        code, _, body = self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")
        self.assertEqual(json.loads(body), {"oidc": True, "name": "alice", "role": "admin"})
        # replay of the callback, and a callback without the binding cookie: no
        # session; the first failure restarts the login once, then it is refused
        code, head, _ = self._oidc_req(f"/auth/callback?state={q['state']}&code=c-1", cookie=f"kaim56_login={q['state']}")
        self.assertEqual((code, "Location: /auth/login" in head, "kaim56_sid=" in head), (302, True, False))
        self.assertEqual(self._oidc_req(f"/auth/callback?state={q['state']}&code=c-1",
                                        cookie=f"kaim56_login={q['state']}; kaim56_retry=1")[0], 400)
        code, head, _ = self._oidc_req(f"/auth/callback?state={q['state']}&code=c-1")
        self.assertEqual((code, "kaim56_retry=1" in head), (302, True))
        self.assertEqual(self._oidc_req(f"/auth/callback?state={q['state']}&code=c-1", cookie="kaim56_retry=1")[0], 400)
        with closing(sqlite3.connect(m._sessions.DB)) as c:                  # the token is stored as its hash only
            self.assertEqual(c.execute("SELECT hash FROM sessions").fetchone()[0], hashlib.sha256(tok.encode()).hexdigest())
        with open(os.path.join(m._audit.AUDIT_DIR, "access.log")) as fh:
            log = [json.loads(ln) for ln in fh]
        self.assertEqual((log[0]["event"], log[0]["name"], log[0]["sub"]), ("login", "alice", "u-1"))
        code, head, _ = self._oidc_req("/auth/logout", cookie=f"kaim56_sid={tok}")
        self.assertEqual(code, 302); self.assertIn("/oidc/logout", head); self.assertIn("kaim56_sid=;", head)
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 401)

    def test_oidc_login_without_role_is_refused(self):
        m = self.m
        st = self._oidc_env()
        st["roles"] = ["other_app"]
        _, (code, head, _) = self._oidc_web_login(st)
        self.assertEqual(code, 403); self.assertNotIn("kaim56_sid=", head)
        self.assertEqual(m._sessions.list_all(), [])

    def test_oidc_device_flow(self):
        """POST /api/auth/device -> user_code + handle (the device_code stays in
        the manager); poll is pending until approved, then the token once."""
        m = self.m
        st = self._oidc_env()
        code, _, body = self._oidc_req("/api/auth/device", "POST", body={"device_name": "pixel"})
        d = json.loads(body)
        self.assertEqual(code, 200); self.assertEqual(d["user_code"], "WXYZ"); self.assertNotIn("dc-1", body.decode())
        poll = lambda: self._oidc_req("/api/auth/device/poll", "POST", body={"handle": d["handle"]})
        self.assertEqual(json.loads(poll()[2]), {"status": "pending"})
        st["device_ok"] = True
        self.assertEqual(json.loads(poll()[2]), {"status": "pending"})          # faster than the interval: not asked
        m._oidc._devices[d["handle"]]["next"] = 0
        code, _, body = poll()
        r = json.loads(body)
        self.assertEqual((code, r["status"], r["name"], r["role"]), (200, "ok", "alice", "admin"))
        self.assertEqual(poll()[0], 400)                                         # once
        self.assertEqual(self._oidc_req("/api/auth/me", bearer=r["token"])[0], 200)
        s = m._sessions.list_all()[0]
        self.assertEqual((s["kind"], s["device_name"]), ("device", "pixel"))
        self.assertEqual(self._oidc_req("/api/auth/device/poll", "POST", body={"handle": "guess"})[0], 400)

    def test_oidc_refresh_rotates_and_ends_session(self):
        """After CHECK_EVERY the session is re-checked at the IdP: the refresh
        token rotates; a lost role or a failed refresh is a 401 and the
        session is gone."""
        import sqlite3
        from contextlib import closing
        m = self.m
        st = self._oidc_env()
        _, (_, head, _) = self._oidc_web_login(st)
        tok = re.search(r"kaim56_sid=([^;]+)", head).group(1)
        def age():                                                               # the last check lies 1000 s back
            with closing(sqlite3.connect(m._sessions.DB)) as c, c:
                c.execute("UPDATE sessions SET last_check=last_check-1000")
        rt_before = st["rt"]
        age()
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 200)
        with closing(sqlite3.connect(m._sessions.DB)) as c:
            self.assertEqual(c.execute("SELECT refresh FROM sessions").fetchone()[0], st["rt"])
        self.assertNotEqual(st["rt"], rt_before)
        st["roles"] = []                                                          # role taken away
        age()
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 401)
        self.assertEqual(m._sessions.list_all(), [])
        st["roles"] = ["kaim56_user"]
        _, (_, head, _) = self._oidc_web_login(st)
        tok = re.search(r"kaim56_sid=([^;]+)", head).group(1)
        st["refresh_fail"] = True
        age()
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 401)
        self.assertEqual(m._sessions.list_all(), [])

    def test_oidc_idle_session_keeps_its_refresh_token_alive(self):
        """keep_sessions refreshes sessions idle longer than KEEP_EVERY (the
        IdP token rotates) and ends one the IdP refuses; the cookie is Lax."""
        import sqlite3
        from contextlib import closing
        m = self.m
        st = self._oidc_env()
        _, (_, head, _) = self._oidc_web_login(st)
        self.assertIn("SameSite=Lax", re.search(r"kaim56_sid=[^\r\n]*", head).group(0))
        def age(sec):
            with closing(sqlite3.connect(m._sessions.DB)) as c, c:
                c.execute("UPDATE sessions SET last_check=last_check-?", (sec,))
        rt0 = st["rt"]
        m._auth.keep_sessions()                                                   # fresh: nothing to do
        self.assertEqual(st["rt"], rt0)
        age(m._auth.KEEP_EVERY + 10)
        m._auth.keep_sessions()
        self.assertNotEqual(st["rt"], rt0)
        with closing(sqlite3.connect(m._sessions.DB)) as c:
            self.assertEqual(c.execute("SELECT refresh FROM sessions").fetchone()[0], st["rt"])
        st["refresh_fail"] = True
        age(m._auth.KEEP_EVERY + 10)
        m._auth.keep_sessions()
        self.assertEqual(m._sessions.list_all(), [])

    def test_oidc_session_survives_idp_outage_and_browser_restart(self):
        """The web cookie is persistent (Max-Age = web TTL); an unreachable
        IdP postpones the re-check instead of ending the session; a session
        without a refresh token is not re-checked."""
        import sqlite3
        from contextlib import closing
        m = self.m
        st = self._oidc_env()
        _, (_, head, _) = self._oidc_web_login(st)
        self.assertIn(f"Max-Age={m._sessions.TTL['web']}", head)
        tok = re.search(r"kaim56_sid=([^;]+)", head).group(1)
        def age():
            with closing(sqlite3.connect(m._sessions.DB)) as c, c:
                c.execute("UPDATE sessions SET last_check=last_check-1000")
        real = m._oidc.refresh
        def down(rt):
            raise m._oidc.OIDCError("idp unreachable (URLError: test)")
        m._oidc.refresh = down
        try:
            age()
            self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 200)
            self.assertEqual(len(m._sessions.list_all()), 1)
        finally:
            m._oidc.refresh = real
        with closing(sqlite3.connect(m._sessions.DB)) as c, c:
            c.execute("UPDATE sessions SET refresh=''")
        st["refresh_fail"] = True                                                 # would end it — but there is nothing to refresh
        age()
        self.assertEqual(self._oidc_req("/api/auth/me", cookie=f"kaim56_sid={tok}")[0], 200)

    def test_oidc_user_role_table_and_attribution(self):
        """Role user: use, not configure (default deny); a write by a person is
        in the access log; chat pushes and tasks carry the speaker's name."""
        m = self.m
        st = self._oidc_env()
        st["roles"] = ["kaim56_user"]
        _, (_, head, _) = self._oidc_web_login(st)
        ck = "kaim56_sid=" + re.search(r"kaim56_sid=([^;]+)", head).group(1)
        ua = m._auth.user_allowed
        for meth, p in (("POST", "/api/instances/a1/start"), ("POST", "/api/chat/a1"), ("GET", "/api/instances"),
                        ("POST", "/api/tasks"), ("GET", "/i/a1/"), ("POST", "/apps/board/_api/x"), ("GET", "/api/auth/me")):
            self.assertTrue(ua(meth, p), p)
        for meth, p in (("POST", "/api/settings"), ("POST", "/api/create"), ("POST", "/api/instances/a1/delete"),
                        ("POST", "/api/instances/a1/config"), ("GET", "/i/a1/term"), ("GET", "/i/a1/te%72m/ws"),
                        ("POST", "/api/plugins/x/approve"), ("GET", "/api/secret-keys"), ("POST", "/api/apps/x/delete"),
                        ("GET", "/api/auth/sessions"), ("GET", "/api/new-route-nobody-listed")):
            self.assertFalse(ua(meth, p), p)
        self.assertEqual(self._oidc_req("/api/auth/sessions", cookie=ck)[0], 403)
        self.assertEqual(self._oidc_req("/api/settings", "POST", cookie=ck, body={})[0], 403)
        code, _, body = self._oidc_req("/api/tasks", "POST", cookie=ck, body={"instance": "ephemeral", "message": "m"})
        self.assertIn("created", body.decode())
        self.assertEqual(m._store.load_tasks()[-1]["by"], "alice")
        self._oidc_req("/api/chats", "POST", cookie=ck, body=[{"id": "c1", "updatedAt": 1, "messages": [{"user": True, "text": "hi"}]}])
        self.assertEqual(m._chats.load_chats()[0]["messages"][0]["by"], "alice")
        with open(os.path.join(m._audit.AUDIT_DIR, "access.log")) as fh:
            log = [json.loads(ln) for ln in fh]
        w = [r for r in log if r["method"] == "POST"]
        self.assertEqual([(r["path"], r["status"], r["role"]) for r in w],
                         [("/api/settings", 403, "user"), ("/api/tasks", 200, "user"), ("/api/chats", 200, "user")])


    def test_login_lockout(self):
        """Ten wrong passwords in a row lock the client for a while — the right
        password included; a success clears the counter. Behind the local proxy
        the client is the first X-Forwarded-For hop, not the proxy."""
        m = self.m
        old = m._guests.instance_by_ip, m._auth.PW, m._auth.USER
        try:
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW, m._auth.USER = "s3cret", "admin"
            m._auth._auth_fails.clear()
            good = "Basic " + base64.b64encode(b"admin:s3cret").decode()
            bad = "Basic " + base64.b64encode(b"admin:nope").decode()
            for i in range(m._auth.AUTH_FAILS_MAX):
                h = self._handler("/api/agents", "10.0.0.9", auth=bad)
                self.assertFalse(h._auth()); self.assertEqual(self._status(h), 401)
            h = self._handler("/api/agents", "10.0.0.9", auth=good)
            self.assertFalse(h._auth()); self.assertEqual(self._status(h), 429)     # locked
            h = self._handler("/api/agents", "10.0.0.10", auth=good)
            self.assertTrue(h._auth())                                                # another client
            m._auth._auth_fails.clear()
            h = self._handler("/api/agents", "10.0.0.9", auth=good)
            self.assertTrue(h._auth())
            self.assertEqual(m._auth.auth_client_key("127.0.0.1", "203.0.113.5, 10.0.0.1"), "203.0.113.5")
            self.assertEqual(m._auth.auth_client_key("172.17.0.3", "203.0.113.5"), "203.0.113.5")
            self.assertEqual(m._auth.auth_client_key("192.168.1.20", "203.0.113.5"), "192.168.1.20")   # LAN client: XFF ignored
        finally:
            m._guests.instance_by_ip, m._auth.PW, m._auth.USER = old
            m._auth._auth_fails.clear()

    def test_iroh_device_lockout(self):
        """iroh-gw puts "PROXY IROH <node-id>" before each connection's HTTP:
        believed from loopback only, first line only, allow-listed ids only.
        Then the lockout is per device — one phone's typos no longer lock
        every iroh client (all of them arrive from 127.0.0.1)."""
        m = self.m
        a, b, c = "a" * 64, "b" * 64, "c" * 64
        tmp = tempfile.mkdtemp(prefix="e2e-iroh-")
        allow = os.path.join(tmp, "allow.txt")
        _wtext(allow, f"{a}  # phone\n{b.upper()}\n")
        old = m._irohgw.ALLOW_FILE, m._guests.instance_by_ip, m._auth.PW, m._auth.USER

        def conn(ip, first, auth="", path="/api/agents"):
            h = self._handler(path, ip)
            req = f"GET {path} HTTP/1.1\r\n".encode()
            h.rfile = io.BytesIO((req if first.startswith(b"PROXY") else b"") + f"Authorization: {auth}\r\n\r\n".encode())
            h.raw_requestline = first
            return h, h.parse_request()
        try:
            m._irohgw.ALLOW_FILE = allow
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW, m._auth.USER = "s3cret", "admin"
            m._auth._auth_fails.clear()
            hdr = lambda n: f"PROXY IROH {n}\r\n".encode()
            h, ok = conn("127.0.0.1", hdr(a))
            self.assertTrue(ok); self.assertEqual((h.iroh_node, h.command, h.path), (a, "GET", "/api/agents"))
            h.raw_requestline = hdr(b)                      # a second header on the same connection
            self.assertFalse(h.parse_request())
            for ip, line in (("10.0.0.9", hdr(a)), ("172.17.0.3", hdr(a)), ("127.0.0.1", hdr(c)),   # not trusted / not allowed
                             ("127.0.0.1", hdr("A" * 64)), ("127.0.0.1", b"PROXY IROH " + a.encode() + b" x\r\n"),
                             ("127.0.0.1", b"PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n")):
                self.assertFalse(conn(ip, line)[1], (ip, line))
            h, ok = conn("127.0.0.1", b"GET /api/agents HTTP/1.1\r\n")    # plain local client: as before
            self.assertTrue(ok); self.assertEqual(h.iroh_node, "")
            self.assertEqual(m._auth.auth_client_key("127.0.0.1", "203.0.113.5", a), "iroh:" + a)   # XFF can't move it
            good = "Basic " + base64.b64encode(b"admin:s3cret").decode()
            bad = "Basic " + base64.b64encode(b"admin:nope").decode()
            for _ in range(m._auth.AUTH_FAILS_MAX):
                h, _ok = conn("127.0.0.1", hdr(a), bad)
                self.assertFalse(h._auth())
            h, _ok = conn("127.0.0.1", hdr(a), good)
            self.assertFalse(h._auth()); self.assertEqual(self._status(h), 429)    # that phone: locked
            h, _ok = conn("127.0.0.1", hdr(b), good)
            self.assertTrue(h._auth())                                               # the other phone: not
            h, _ok = conn("127.0.0.1", b"GET /api/agents HTTP/1.1\r\n", good)
            self.assertTrue(h._auth())                                               # nor a local client
        finally:
            m._irohgw.ALLOW_FILE, m._guests.instance_by_ip, m._auth.PW, m._auth.USER = old
            m._auth._auth_fails.clear()

    def test_instance_json_is_operator_readable(self):
        """save_instance leaves the JSON at 0640 whatever the umask; the tests
        and the operator read it, and harden_files relaxes old ones."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-instjson-")
        old = m._paths.INST_DIR, os.umask(0o077)
        try:
            m._paths.INST_DIR = tmp
            m._instances.save_instance({"name": "x", "template": "openrouter"})
            self.assertEqual(os.stat(os.path.join(tmp, "x.json")).st_mode & 0o777, 0o640)
            self.assertEqual(m._instances.load_instances()[0]["name"], "x")
            os.chmod(os.path.join(tmp, "x.json"), 0o600)
            os.makedirs(os.path.join(tmp, "instances")); os.rename(os.path.join(tmp, "x.json"), os.path.join(tmp, "instances", "x.json"))
            m._startup.harden_files(tmp)
            self.assertEqual(os.stat(os.path.join(tmp, "instances", "x.json")).st_mode & 0o777, 0o640)
        finally:
            m._paths.INST_DIR = old[0]; os.umask(old[1])

    def test_harden_files_makes_state_private(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-harden-")
        os.makedirs(os.path.join(tmp, "audit")); os.makedirs(os.path.join(tmp, "templates"))
        for f in ("chats.json", "missions.json", "history.db", "audit/hass.jsonl", "templates/x.json", "run.log"):
            with open(os.path.join(tmp, f), "w") as fh:
                fh.write("{}")
            os.chmod(os.path.join(tmp, f), 0o644)
        n = m._startup.harden_files(tmp)
        self.assertEqual(n, 4)
        for f in ("chats.json", "missions.json", "history.db", "audit/hass.jsonl"):
            self.assertEqual(os.stat(os.path.join(tmp, f)).st_mode & 0o777, 0o600, f)
        self.assertEqual(os.stat(os.path.join(tmp, "audit")).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(tmp, "templates", "x.json")).st_mode & 0o777, 0o644)   # untouched
        self.assertEqual(os.stat(os.path.join(tmp, "run.log")).st_mode & 0o777, 0o644)

    def test_config_disk_content_is_readable_by_the_agent(self):
        """The manager runs with umask 077; the config disk is read by uid
        1000 in the VM, so its tree must be made world-readable explicitly
        (a 0700 plugins folder killed the agent at start once)."""
        m = self.m
        if not shutil.which("mkfs.ext4", path="/usr/sbin:/sbin:" + os.environ.get("PATH", "")):
            self.skipTest("mkfs.ext4 not available")
        tmp = tempfile.mkdtemp(prefix="e2e-cfgdisk-")
        old = m._paths.RUN_DIR, None, os.umask(0o077)
        try:
            m._paths.RUN_DIR = tmp
            inst = {"name": "vm1", "index": 5, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4", "config": {}}
            m._vm.make_config_disk(inst)
            d = os.path.join(tmp, "vm1.cfgdir")
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o755)
            self.assertEqual(os.stat(os.path.join(d, "config.env")).st_mode & 0o777, 0o644)
            for root, dirs, files in os.walk(d):
                for x in dirs:
                    self.assertEqual(os.stat(os.path.join(root, x)).st_mode & 0o777, 0o755, x)
        finally:
            m._paths.RUN_DIR = old[0]
            os.umask(old[2])

    def test_notify_route_rejects_empty(self):
        """Empty notification -> 429, id null. Offline on purpose: as an HTTP
        test it ran against the live manager on every suite run and left one
        'admin/notify empty' line in the live audit each time (138 of them
        looked like a misbehaving admin instance)."""
        m = self.m
        old = m._guests.instance_by_ip, m._audit.audit_append, m._auth.PW
        try:
            m._guests.instance_by_ip = lambda ip: None
            m._audit.audit_append = lambda *a, **k: None
            m._auth.PW = ""
            h = self._post_handler("/api/notify", "10.0.0.5", b'{"title": "", "message": ""}')
            h.do_POST()
            self.assertEqual(self._status(h), 429)
            self.assertIsNone(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1]).get("id"))
        finally:
            m._guests.instance_by_ip, m._audit.audit_append, m._auth.PW = old

    def test_task_runs_carry_a_deadline_and_the_worker_timeout(self):
        """The bridge call carries deadline = now + timeout - margin; the worker
        gives a task TASK_TIMEOUT, a waiting guest keeps 600 s; a timeout reads
        as a timeout, not as a stack trace."""
        m = self.m
        sent = []
        old = urllib.request.urlopen, m._instances.net_of, m._instances.load_instances, m._instances.is_running, m._tasks._run_named

        class _R:
            def __init__(self, body): self.body = body
            def read(self): return self.body
        try:
            m._instances.net_of = lambda inst: {"guest": "172.30.9.2"}
            def fake_open(req, timeout=None):
                sent.append((json.loads(req.data.decode()), timeout))
                if timeout == 5:
                    raise TimeoutError("timed out")
                return _R(b'{"reply": "done"}')
            urllib.request.urlopen = fake_open
            inst = {"name": "vm1", "index": 9}
            self.assertEqual(m._tasks._chat_post(inst, "hi", timeout=1800), "done")
            body, to = sent[-1]
            self.assertEqual(to, 1800)
            self.assertAlmostEqual(body["deadline"], time.time() + 1770, delta=5)
            m._instances.load_instances = lambda: [inst]
            m._instances.is_running = lambda i: True
            ok, res = m._tasks._run_named("vm1", "hi", timeout=5)
            self.assertFalse(ok); self.assertIn("no answer within 5 s", res)
            got = []
            m._tasks._run_named = lambda instance, message, timeout=600: (got.append(timeout), (True, "x"))[1]
            m._tasks._run_task_now("vm1", "hi", None, timeout=m._tasks.TASK_TIMEOUT)
            m._tasks._run_task_now("vm1", "hi")
            self.assertEqual(got, [m._tasks.TASK_TIMEOUT, 600])
            self.assertGreaterEqual(m._tasks.TASK_TIMEOUT, 1800)
        finally:
            urllib.request.urlopen, m._instances.net_of, m._instances.load_instances, m._instances.is_running, m._tasks._run_named = old

    def test_trace_stitches_turn_llm_and_tool_spans(self):
        """One turn = one span tree. The agent sends a start marker, its LLM
        calls (usage with turn/step/ms), its tool calls (audit with turn/ms)
        and an end marker; GET /api/trace/<inst>?turn= returns them stitched
        and ordered, the audit output keeps its shape (plus ms), an old DB
        gains the span columns without a migration error."""
        import mgr.store as st
        import sqlite3
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-trace-")
        old = st.HISTORY_DB, m._audit.AUDIT_DIR, m._guests.instance_by_ip, m._settings.load_settings, list(st._migrated)
        try:
            st.HISTORY_DB = os.path.join(tmp, "history.db")
            m._audit.AUDIT_DIR = os.path.join(tmp, "audit")
            with sqlite3.connect(st.HISTORY_DB) as c:        # a DB from before the span columns
                c.execute("CREATE TABLE llm_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, "
                          "instance TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL)")
                c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,completion_tokens,cost) "
                          "VALUES(1,'vm1','old',1,1,0)")
            st._migrated[0] = False
            inst = {"name": "vm1", "index": 3, "template": "openrouter", "config": {}}
            m._guests.instance_by_ip = lambda ip: inst if ip == "172.30.3.2" else None
            m._settings.load_settings = lambda: {}                    # proxy off: the agent reports usage
            def post(path, body):
                h = self._post_handler(path, "172.30.3.2", json.dumps(body).encode()); h.do_POST()
                return self._status(h)
            self.assertEqual(post("/api/trace", {"turn": "t1", "event": "start", "kind": "task"}), 204)
            self.assertEqual(post("/api/usage", {"model": "x/y", "prompt_tokens": 100, "completion_tokens": 20,
                                                 "turn": "t1", "step": 1, "ms": 900}), 204)
            self.assertEqual(post("/api/audit", {"tool": "web_search", "target": "cronn", "ok": True,
                                                 "result": "1. cronn", "turn": "t1", "ms": 340}), 204)
            self.assertEqual(post("/api/audit", {"tool": "http_fetch", "target": "https://x", "ok": False,
                                                 "err": "HTTP 403", "turn": "t1", "ms": 1200}), 204)
            self.assertEqual(post("/api/usage", {"model": "x/y", "prompt_tokens": 0, "completion_tokens": 0,
                                                 "turn": "t1", "step": 2, "ms": 50, "ok": False, "err": "HTTP 429"}), 204)
            self.assertEqual(post("/api/trace", {"turn": "t1", "event": "end", "kind": "task",
                                                 "steps": 2, "ms": 2600, "outcome": "ok"}), 204)
            # an admin, not a guest, may not file markers; a guest gets no trace read
            h = self._post_handler("/api/trace", "10.0.0.5", b'{"turn":"t2","event":"start"}'); h.do_POST()
            self.assertEqual(self._status(h), 403)
            h = self._handler("/api/trace/vm1?turn=t1", "172.30.3.2"); h._do_GET()
            self.assertEqual(self._status(h), 403)
            h = self._handler("/api/trace/vm1?turn=t1", "10.0.0.5"); h._do_GET()
            tr = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(tr["turn"]["kind"], "task")
            self.assertEqual((tr["turn"]["steps"], tr["turn"]["ms"], tr["turn"]["outcome"]), (2, 2600, "ok"))
            self.assertEqual((tr["turn"]["llm_calls"], tr["turn"]["llm_failed"], tr["turn"]["in"]), (2, 1, 100))
            self.assertEqual([x["step"] for x in tr["llm"]], [1, 2])
            self.assertEqual([x["ms"] for x in tr["llm"]], [900, 50])
            self.assertEqual([x["ok"] for x in tr["llm"]], [True, False])
            self.assertEqual([x["tool"] for x in tr["tools"]], ["web_search", "http_fetch"])
            self.assertEqual([x["ms"] for x in tr["tools"]], [340, 1200])
            self.assertTrue(all(x["ms"] > 0 for x in tr["llm"] + tr["tools"]))
            h = self._handler("/api/trace/vm1", "10.0.0.5"); h._do_GET()
            lst = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["turns"]
            self.assertEqual([t["turn"] for t in lst], ["t1"])
            self.assertNotIn("answer", lst[0])
            # the audit reader is unchanged apart from ms
            recs = m._audit.audit_read("vm1")
            self.assertEqual(set(recs[0]) - {"ms"}, {"ts", "tool", "target", "ok", "err", "turn"})
            self.assertIsNone(tr["answer"])                            # an end without answer keeps none
            # the answer the client was sent: kept with the turn, only in the single-turn read
            self.assertEqual(post("/api/trace", {"turn": "t3", "event": "start", "kind": "stream"}), 204)
            self.assertEqual(post("/api/trace", {"turn": "t3", "event": "end", "kind": "stream", "steps": 1,
                                                 "ms": 5, "outcome": "ok", "answer": "⟦think⟧x⟦/think⟧OK"}), 204)
            h = self._handler("/api/trace/vm1?turn=t3", "10.0.0.5"); h._do_GET()
            tr3 = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual((tr3["answer"], tr3["turn"]["ts_end"] is not None), ("⟦think⟧x⟦/think⟧OK", True))
            h = self._handler("/api/trace/vm1", "10.0.0.5"); h._do_GET()
            self.assertNotIn("answer", json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["turns"][0])
            st.turn_end("vm1", "t4", answer="y" * (st.ANSWER_MAX + 10))
            self.assertEqual(len(st.turn_trace("vm1", "t4")["answer"]), st.ANSWER_MAX)
            # an end without a start still yields a full row; old rows have no turn
            st.turn_end("vm1", "t9", ms=10, steps=1, outcome="error")
            self.assertEqual(st.turns_read("vm1")[0]["turn"], "t9")
            self.assertEqual(st.usage_for("vm1")["calls"], 3)          # the old row still counts
            self.assertEqual(st.turns_prune(30), 0)
        finally:
            st.HISTORY_DB, m._audit.AUDIT_DIR, m._guests.instance_by_ip, m._settings.load_settings = old[:4]
            st._migrated[0] = old[4][0]

    def test_instance_proxy_forwards_the_turn_header(self):
        """The bridge names a turn in X-Kaim-Turn; the manager's /i/<name>/
        proxy passes it to the app on the streamed and on the JSON-unpacked
        chat path, so the app can fetch the trace."""
        m = self.m
        import email.message

        class _R:
            def __init__(self, body, ct, turn):
                self.status, self.body, self.pos = 200, body, 0
                self.headers = email.message.Message()
                self.headers["Content-Type"] = ct
                if turn:
                    self.headers["X-Kaim-Turn"] = turn
            def read(self, n=None):
                if n is None:
                    out, self.pos = self.body[self.pos:], len(self.body); return out
                out = self.body[self.pos:self.pos + n]; self.pos += len(out); return out
        old = urllib.request.urlopen, m._instances.load_instances, m._instances.is_running, m._instances.net_of, m._guests.instance_by_ip, m._auth.PW
        try:
            inst = {"name": "vm1", "index": 4, "config": {"TRANSPORT": "web"}}
            m._instances.load_instances = lambda: [inst]
            m._instances.is_running = lambda i: True
            m._instances.net_of = lambda i: {"guest": "172.30.4.2"}
            m._guests.instance_by_ip = lambda ip: None
            m._auth.PW = ""
            urllib.request.urlopen = lambda req, timeout=None: _R(b"Hallo", "text/plain; charset=utf-8", "t0ken001")
            h = self._post_handler("/i/vm1/api/chat/stream", "10.0.0.5", b'{"message":"hi"}')
            h._proxy("POST")
            raw = h.wfile.getvalue()
            self.assertIn(b"X-Kaim-Turn: t0ken001", raw.split(b"\r\n\r\n", 1)[0])
            self.assertTrue(raw.endswith(b"Hallo"))
            urllib.request.urlopen = lambda req, timeout=None: _R(b'{"reply": "Hi", "turn": "t0ken002"}', "application/json", "t0ken002")
            h = self._post_handler("/i/vm1/api/chat", "10.0.0.5", b'{"message":"hi"}')
            h._proxy("POST")
            raw = h.wfile.getvalue()
            self.assertIn(b"X-Kaim-Turn: t0ken002", raw.split(b"\r\n\r\n", 1)[0])
            self.assertTrue(raw.endswith(b"Hi"))                                  # unpacked for the app
            urllib.request.urlopen = lambda req, timeout=None: _R(b"<p>x</p>", "text/html", "")
            h = self._handler("/i/vm1/", "10.0.0.5"); h._proxy("GET")
            self.assertNotIn(b"X-Kaim-Turn", h.wfile.getvalue())                # nothing to forward
        finally:
            urllib.request.urlopen, m._instances.load_instances, m._instances.is_running, m._instances.net_of, m._guests.instance_by_ip, m._auth.PW = old

    def test_task_run_now(self):
        """The Tasks tab's play button: a scheduled task runs at the next tick
        and keeps its schedule, a finished one-off is pending again, a running
        one is left alone, an unknown id says so."""
        import mgr.store as st
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-runnow-")
        old = st.TASKS_FILE, m._guests.instance_by_ip, m._auth.PW
        try:
            st.TASKS_FILE = os.path.join(tmp, "tasks.json")
            later = int(time.time()) + 86400
            with open(st.TASKS_FILE, "w") as fh:
                json.dump([{"id": "s1", "instance": "vm1", "message": "daily", "schedule": "daily 07:00",
                            "status": "scheduled", "next_run": later, "target_warned": 5},
                           {"id": "o1", "instance": "vm1", "message": "once", "schedule": "",
                            "status": "error", "result": "error: x"},
                           {"id": "r1", "instance": "vm1", "message": "busy", "schedule": "", "status": "running"}], fh)
            self.assertIn("queued", m._tasks.run_task_now("s1"))
            self.assertIn("queued", m._tasks.run_task_now("o1"))
            self.assertIn("running already", m._tasks.run_task_now("r1"))
            self.assertEqual(m._tasks.run_task_now("nope"), "unknown")
            ts = {t["id"]: t for t in st.load_tasks()}
            self.assertEqual(ts["s1"]["status"], "scheduled")
            self.assertLessEqual(ts["s1"]["next_run"], int(time.time()))
            self.assertEqual(ts["s1"]["schedule"], "daily 07:00")
            self.assertNotIn("target_warned", ts["s1"])
            self.assertEqual(ts["o1"]["status"], "pending")
            self.assertEqual(ts["r1"]["status"], "running")
            # the route
            m._guests.instance_by_ip = lambda ip: None; m._auth.PW = ""
            h = self._post_handler("/api/tasks/o1/run", "10.0.0.5", b"{}"); h.do_POST()
            self.assertIn(b"queued", h.wfile.getvalue())
        finally:
            st.TASKS_FILE, m._guests.instance_by_ip, m._auth.PW = old

    def test_skill_proposals_wait_for_approval(self):
        """An agent's skill proposal is linted, stored, announced, and enters
        the catalog only when approved in the Skills tab; a newer proposal
        with the same name replaces the pending one; admins cannot file."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-skprop-")
        old = m._skills.SKILL_PROPOSALS_FILE, m._skills.SKILLS_FILE, m._notify.notify_add, m._guests.instance_by_ip, m._auth.PW
        notes = []
        try:
            m._skills.SKILL_PROPOSALS_FILE = os.path.join(tmp, "p.json"); m._skills.SKILLS_FILE = os.path.join(tmp, "s.json")
            m._notify.notify_add = lambda *a, **k: notes.append(a)
            vm = {"name": "vm1", "index": 3, "template": "openrouter", "config": {}}
            m._guests.instance_by_ip = lambda ip: vm if ip == "172.30.3.2" else None
            good = {"name": "Job-Search-NRW", "description": "Daily job search for a region",
                    "content": "# Purpose\nSearch job boards.\n\n## Steps\n1. web_search with the region\n2. http_fetch each hit\n3. notify a summary\n\n## Pitfalls\nIndeed blocks fetches.",
                    "turn": "t1"}
            h = self._post_handler("/api/skill-proposals", "172.30.3.2", json.dumps(good).encode()); h.do_POST()
            self.assertEqual(self._status(h), 200)
            self.assertEqual(len(notes), 1); self.assertIn("job-search-nrw", notes[0][1])
            bad = dict(good, content="too short"); h = self._post_handler("/api/skill-proposals", "172.30.3.2", json.dumps(bad).encode()); h.do_POST()
            self.assertEqual(self._status(h), 400)
            leak = dict(good, content=good["content"] + "\nuse Bearer abcdefghijklmnopqrstuvwxyz1234"); h = self._post_handler("/api/skill-proposals", "172.30.3.2", json.dumps(leak).encode()); h.do_POST()
            self.assertEqual(self._status(h), 400)
            again = dict(good, description="Daily job search, second try"); h = self._post_handler("/api/skill-proposals", "172.30.3.2", json.dumps(again).encode()); h.do_POST()
            self.assertEqual(self._status(h), 200)
            pending = [p for p in m._skills.load_proposals() if p["status"] == "proposed"]
            self.assertEqual([p["description"] for p in pending], ["Daily job search, second try"])   # replaced, not doubled
            self.assertFalse(pending[0]["update"])
            h = self._post_handler("/api/skill-proposals", "10.0.0.5", json.dumps(good).encode()); h.do_POST()
            self.assertEqual(self._status(h), 403)                                     # guests only
            self.assertEqual([x["name"] for x in m._skills.load_skills()], [])                 # nothing in the catalog yet
            m._auth.PW = ""
            h = self._post_handler(f"/api/skill-proposals/{pending[0]['id']}/approve", "10.0.0.5", b"{}"); h.do_POST()
            self.assertIn(b"saved", h.wfile.getvalue())
            self.assertEqual([x["name"] for x in m._skills.load_skills()], ["job-search-nrw"])
            self.assertEqual(m._skills.load_proposals()[0]["status"], "approved")
            self.assertEqual(m._skills.proposal_decide("nope", True), "unknown")
            # a proposal for an existing skill is flagged as an update
            pid, why = m._skills.proposal_add("vm1", "job-search-nrw", "Better", good["content"])
            self.assertTrue(pid); self.assertTrue(next(p for p in m._skills.load_proposals() if p["id"] == pid)["update"])
            self.assertIn("discarded", m._skills.proposal_decide(pid, False))
        finally:
            m._skills.SKILL_PROPOSALS_FILE, m._skills.SKILLS_FILE, m._notify.notify_add, m._guests.instance_by_ip, m._auth.PW = old

    def test_sessions_fulltext_search_scoped_per_guest(self):
        """FTS5 over chats and task runs: exact words find the session, guests
        see their own instance only, the orchestrator everything, the index
        follows chats.json by mtime."""
        import mgr.store as st
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-fts-")
        old = st.HISTORY_DB, m._chats.load_chats, m._chats.CHATS_FILE, m._guests.instance_by_ip, dict(st._fts_state)
        try:
            st.HISTORY_DB = os.path.join(tmp, "history.db"); st._fts_state["mtime"] = None
            m._chats.CHATS_FILE = os.path.join(tmp, "chats.json"); open(m._chats.CHATS_FILE, "w").write("[]")
            chats = [{"id": "c1", "instance": "vm1", "title": "Jobs", "updatedAt": 1788000000000,
                      "messages": [{"user": True, "text": "Was war mit Vaillant in Remscheid?"},
                                   {"user": False, "text": "Vaillant sucht einen Senior Project Manager Security."}]},
                     {"id": "c2", "instance": "vm2", "title": "Garten", "updatedAt": 1788000000000,
                      "messages": [{"user": True, "text": "Gartenhaus Licht an"}]}]
            m._chats.load_chats = lambda: chats
            st.history_add("vm2", "MSFT Kurs holen", "MSFT 512.30 — alert sent", True)
            hits = m._skills.sessions_search("Vaillant")
            self.assertEqual({(h["instance"], h["kind"]) for h in hits}, {("vm1", "chat")})
            self.assertIn("[Vaillant]", hits[0]["snippet"])
            self.assertEqual([h["kind"] for h in m._skills.sessions_search("MSFT alert")], ["task"])
            self.assertEqual(m._skills.sessions_search("Vaillant", instance="vm2"), [])
            self.assertEqual(m._skills.sessions_search('"; DROP TABLE x; --'), [])           # syntax cannot break it
            # guest scoping through the route
            vm1 = {"name": "vm1", "index": 3, "config": {}}; orch = {"name": m._guests.ORCH_INSTANCE, "index": 1, "config": {}}
            m._guests.instance_by_ip = lambda ip: {"172.30.3.2": vm1, "172.30.1.2": orch}.get(ip)
            h = self._post_handler("/api/sessions-search", "172.30.3.2", b'{"q": "Gartenhaus"}'); h.do_POST()
            self.assertEqual(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["hits"], [])     # vm2's chat
            h = self._post_handler("/api/sessions-search", "172.30.1.2", b'{"q": "Gartenhaus"}'); h.do_POST()
            self.assertEqual(len(json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])["hits"]), 1)
            # a changed chats.json is picked up
            chats[1]["messages"].append({"user": False, "text": "Licht im Gartenhaus ist an."})
            os.utime(m._chats.CHATS_FILE, (1, 1))
            self.assertEqual(len(m._skills.sessions_search("Gartenhaus")), 2)
        finally:
            st.HISTORY_DB, m._chats.load_chats, m._chats.CHATS_FILE, m._guests.instance_by_ip = old[:4]
            st._fts_state.clear(); st._fts_state.update(old[4])

    def test_session_panel_data_and_log(self):
        """The chat's session panel: runtime, uptime, login state, platform
        rows, the instance's MCP servers with their secret state; the log
        route returns the console tail; both admin-only, unknown instance 404;
        nothing secret in the answer."""
        import mgr.store as st
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-session-")
        old = (m._instances.load_instances, m._instances.is_running, m._instances.pidfile, m._settings.load_settings, m._secrets.secret_store, m._secrets.load_secret_policy,
               m._mcp.load_mcps, m._skills.load_skills, m._paths.RUN_DIR, m._guests.instance_by_ip, m._auth.PW, st.HISTORY_DB, m._vm.image_state)
        try:
            inst = {"name": "vm1", "index": 3, "template": "openrouter", "rootfs": "instances/openrouter-rootfs.ext4",
                    "config": {"OPENROUTER_MODEL": "x/y", "MCP_SERVERS": "caldav,homeassistant"}}
            m._instances.load_instances = lambda: [inst]
            m._instances.is_running = lambda i: True
            m._vm.image_state = lambda i: (False, 0, 0)
            m._paths.RUN_DIR = tmp; st.HISTORY_DB = os.path.join(tmp, "history.db")
            pf = os.path.join(tmp, "vm1.pid"); open(pf, "w").write("1"); os.utime(pf, (time.time() - 7500,) * 2)
            m._instances.pidfile = lambda i: pf
            with open(os.path.join(tmp, "vm1.log"), "w") as fh:
                fh.write("[init] harness from /dev/vdc\nagent ready\n")
            m._settings.load_settings = lambda: {"LLM_KEY_PROXY": "1", "BRAVE_API_KEY": "b"}
            m._secrets.secret_store = lambda: {"CALDAV_PASSWORD": "s3cret", "HA_TOKEN": "t"}
            m._secrets.load_secret_policy = lambda: {"by_template": {}, "by_instance": {"vm1": ["HA_TOKEN"]}, "guest_readable": []}
            m._mcp.load_mcps = lambda: [{"name": "caldav", "command": "c", "env": {"CALDAV_PASSWORD": "${CALDAV_PASSWORD}"}},
                                   {"name": "homeassistant", "command": "h", "args": ["Bearer ${HA_TOKEN}"]}]
            m._skills.load_skills = lambda: [{"name": "a"}, {"name": "b"}]
            m._guests.instance_by_ip = lambda ip: None; m._auth.PW = ""
            d = m._instances.session_info(inst)
            self.assertEqual((d["runtime"], d["login"], d["model"]), ("openrouter-agent", "key proxy", "x/y"))
            self.assertTrue(7400 < d["uptime"] < 7700)
            self.assertEqual({x["name"]: x["ready"] for x in d["mcps"]}, {"caldav": False, "homeassistant": True})
            self.assertEqual(d["need_secret"], 1)
            self.assertIn("2 in catalog", [p["state"] for p in d["platform"] if p["name"] == "Skills"][0])
            self.assertNotIn("s3cret", json.dumps(d))
            h = self._handler("/api/session/vm1", "10.0.0.5"); h._do_GET()
            body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(body["name"], "vm1")
            h = self._handler("/api/session/vm1/log", "10.0.0.5"); h._do_GET()
            self.assertTrue(h.wfile.getvalue().endswith(b"agent ready\n"))
            h = self._handler("/api/session/nope", "10.0.0.5"); h._do_GET()
            self.assertEqual(self._status(h), 404)
            m._guests.instance_by_ip = lambda ip: inst
            h = self._handler("/api/session/vm1", "172.30.3.2"); h._do_GET()
            self.assertEqual(self._status(h), 403)                                  # guests: no
            # a claude instance reports its host credential, not a key
            m._guests.instance_by_ip = lambda ip: None
            cl = {**inst, "template": "claude", "config": {}}
            self.assertRegex(m._instances.session_info(cl)["login"], r"^(ok · valid \d+h \d+m|expired on the host.*|missing \(log in on the host\))$")
            self.assertNotIn("commands", m._instances.session_info(cl))                       # the / picker lists them
        finally:
            (m._instances.load_instances, m._instances.is_running, m._instances.pidfile, m._settings.load_settings, m._secrets.secret_store, m._secrets.load_secret_policy,
             m._mcp.load_mcps, m._skills.load_skills, m._paths.RUN_DIR, m._guests.instance_by_ip, m._auth.PW, st.HISTORY_DB, m._vm.image_state) = old

    def test_chat_page_carries_panel_and_search(self):
        """The rendered chat page has the session panel, the search bar and
        the two header toggles from the design; the agent list is embedded."""
        import chatui
        page = chatui.render([{"name": "vm1", "running": True}], "vm1", "")
        for needle in ('id=panel', 'id=searchbar', 'id=searchBtn', 'id=panelBtn', "'/api/session/'",
                       'function searchApply', 'function panelLoad', '"vm1"',
                       "convs.filter(x=>x.agent===agent)"):                    # dropdown opens that instance's chat
            self.assertIn(needle, page, needle)
        self.assertNotIn("cell('Commands'", page)                              # the / picker lists them

    def test_settings_for_ui_masks_every_credential_shaped_value(self):
        """Not only the schema's key params: any setting named like a
        credential is replaced by the placeholder before the page embeds it;
        the placeholder never overwrites the stored value on save."""
        m = self.m
        old = m._settings.load_settings
        try:
            m._settings.load_settings = lambda: {"OPENROUTER_API_KEY": "sk-or-x", "HF_TOKEN": "hf_abc", "MY_PASSWORD": "p",
                                       "BRAVE_API_KEY": "b", "SIGNAL_NUMBER": "+49", "TTS_VOICE": "de-thorsten-high", "EMPTY_KEY": ""}
            ui = m._settings.settings_for_ui()
            self.assertEqual({k: v for k, v in ui.items() if v == m._settings.SETTINGS_KEEP},
                             {k: m._settings.SETTINGS_KEEP for k in ("OPENROUTER_API_KEY", "HF_TOKEN", "MY_PASSWORD", "BRAVE_API_KEY")})
            self.assertEqual((ui["SIGNAL_NUMBER"], ui["TTS_VOICE"], ui["EMPTY_KEY"]), ("+49", "de-thorsten-high", ""))
            self.assertNotIn("hf_abc", json.dumps(ui))
        finally:
            m._settings.load_settings = old

    def test_secret_store_edit_from_the_ui(self):
        """Values are added, replaced and deleted in the store file through
        admin routes: names validated, other lines and comments kept, the
        file stays 0600, values never come back through /api/secret-keys."""
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-secstore-")
        old = m._secrets.SECRETS_FILE, m._settings.load_settings, m._guests.instance_by_ip, m._auth.PW
        try:
            m._secrets.SECRETS_FILE = os.path.join(tmp, "secrets.env")
            with open(m._secrets.SECRETS_FILE, "w") as fh:
                fh.write("# my secrets\nHA_TOKEN=old\nMRMUSIC_TOKEN=m\n")
            os.chmod(m._secrets.SECRETS_FILE, 0o600)
            m._settings.load_settings = lambda: {"OPENROUTER_API_KEY": "sk"}
            self.assertEqual(m._secrets.secret_set("CALDAV_PASSWORD", "p4ss"), "CALDAV_PASSWORD added")
            self.assertEqual(m._secrets.secret_set("HA_TOKEN", "new"), "HA_TOKEN replaced")
            self.assertIn("invalid name", m._secrets.secret_set("bad-name", "x"))
            self.assertIn("one non-empty line", m._secrets.secret_set("X_KEY", "a\nb"))
            self.assertIn("one non-empty line", m._secrets.secret_set("X_KEY", "  "))
            txt = open(m._secrets.SECRETS_FILE).read()
            self.assertEqual(txt, "# my secrets\nHA_TOKEN=new\nMRMUSIC_TOKEN=m\nCALDAV_PASSWORD=p4ss\n")
            self.assertEqual(os.stat(m._secrets.SECRETS_FILE).st_mode & 0o777, 0o600)
            self.assertEqual(m._secrets.secret_delete("MRMUSIC_TOKEN"), "MRMUSIC_TOKEN deleted")
            self.assertEqual(m._secrets.secret_delete("MRMUSIC_TOKEN"), "MRMUSIC_TOKEN not in the store")
            self.assertEqual(set(m._secrets.load_secrets_file()), {"HA_TOKEN", "CALDAV_PASSWORD"})
            m._guests.instance_by_ip = lambda ip: None; m._auth.PW = ""
            h = self._handler("/api/secret-keys", "10.0.0.5"); h._do_GET()
            body = json.loads(h.wfile.getvalue().split(b"\r\n\r\n", 1)[1])
            self.assertEqual(body["sources"], {"CALDAV_PASSWORD": "store", "HA_TOKEN": "store", "OPENROUTER_API_KEY": "settings"})
            self.assertNotIn("p4ss", h.wfile.getvalue().decode()); self.assertNotIn("new", body["keys"])
            h = self._post_handler("/api/secret-store", "10.0.0.5", b'{"name": "NEW_KEY", "value": "v"}'); h.do_POST()
            self.assertIn(b"NEW_KEY added", h.wfile.getvalue())
            h = self._post_handler("/api/secret-store/NEW_KEY/delete", "10.0.0.5", b"{}"); h.do_POST()
            self.assertIn(b"NEW_KEY deleted", h.wfile.getvalue())
            guest = {"name": "vm1", "index": 3, "config": {}}
            m._guests.instance_by_ip = lambda ip: guest
            h = self._post_handler("/api/secret-store", "172.30.3.2", b'{"name": "X_KEY", "value": "v"}'); h.do_POST()
            self.assertEqual(self._status(h), 403)                                       # guests: never
        finally:
            m._secrets.SECRETS_FILE, m._settings.load_settings, m._guests.instance_by_ip, m._auth.PW = old

    def test_guest_get_denylist_covers_ui_proxy_and_terminal(self):
        """GET /i/<other>/term opened the shell of every other VM — only POST
        was gated. The denylist names the admin UI, chat, katfs and /i/."""
        m = self.m
        for p in ("/", "/chat", "/chat?i=x", "/katfs", "/katfs/", "/katfs/x.txt",
                  "/i/orchestrator/", "/i/orchestrator/term", "/i/x/term/ws?y=1"):
            self.assertTrue(m._guests.guest_get_blocked(p), p)
        for p in ("/api/agents", "/api/memory/self", "/api/skills?meta=1",
                  "/api/inbox", "/chatx", "/logo.svg"):
            self.assertFalse(m._guests.guest_get_blocked(p), p)

    def test_guest_gets_403_on_denied_paths_and_skips_basic_auth(self):
        """Guests carry no credentials (identity = source IP), so they pass
        _auth even with MANAGER_PASS set; an admin without credentials gets
        401. Denied GET paths answer 403 before any handler runs."""
        m = self.m
        guest = {"name": "hass", "index": 7, "config": {}}
        old_ibi, old_pw = m._guests.instance_by_ip, m._auth.PW
        try:
            m._guests.instance_by_ip = lambda ip: guest if ip == "172.30.7.2" else None
            m._auth.PW = "secret"
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
                f"{m._auth.USER}:secret".encode()).decode())
            self.assertTrue(h._auth())
        finally:
            m._guests.instance_by_ip, m._auth.PW = old_ibi, old_pw

    def test_guest_task_target_policy(self):
        """A guest may task itself or an ephemeral VM; other instances only via
        DELEGATE_TARGETS in its config ('*' = all); the orchestrator: all."""
        m = self.m
        me = {"name": "jobresearcher", "config": {}}
        for t in ("ephemeral", "", None, "jobresearcher"):
            self.assertTrue(m._guests.guest_may_target(me, t), repr(t))
        for t in ("orchestrator", "hass", "claudy"):
            self.assertFalse(m._guests.guest_may_target(me, t), t)
        me["config"]["DELEGATE_TARGETS"] = "hass, claudy"
        self.assertTrue(m._guests.guest_may_target(me, "hass"))
        self.assertTrue(m._guests.guest_may_target(me, "claudy"))
        self.assertFalse(m._guests.guest_may_target(me, "orchestrator"))
        me["config"]["DELEGATE_TARGETS"] = "*"
        self.assertTrue(m._guests.guest_may_target(me, "orchestrator"))
        orch = {"name": m._guests.ORCH_INSTANCE, "config": {}}
        self.assertTrue(m._guests.guest_may_target(orch, "anything"))

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
            hid = m._signal_mod.hitl_create("hass", "bash", "rm x")
            self.assertEqual(m._signal_mod.hitl_status(hid), "pending")            # admin
            self.assertEqual(m._signal_mod.hitl_status(hid, "hass"), "pending")    # owner
            self.assertEqual(m._signal_mod.hitl_status(hid, "uncensored"), "unknown")
        finally:
            sigmod.signal_send = old_send

    def test_guest_input_rules_only_manager_and_nfs(self):
        """Guest -> host is limited to :8700 and NFS; the DROP is inserted first
        so the ACCEPTs land above it. Per tap, a source other than the guest's
        own /30 address is dropped (the IP is the guest's identity)."""
        import types
        m = self.m
        calls = []
        old_sh = m._util.sh
        try:
            # every -C fails -> "rule missing" -> everything gets inserted
            m._util.sh = lambda *a, check=True: (calls.append(a), types.SimpleNamespace(returncode=1))[1]
            m._netfw.ensure_guest_input_rules()
            ins = [c for c in calls if c[1] == "-I"]
            self.assertEqual(ins[0][2:], ("INPUT", "1", "-i", "fc+", "-j", "DROP"))
            ports = {c[c.index("--dport") + 1] for c in ins if "--dport" in c}
            self.assertEqual(ports, {str(m._host.LISTEN[1]), "2049"})
            self.assertTrue(any("ESTABLISHED,RELATED" in c for c in ins))
            self.assertTrue(all(c[2] == "INPUT" and ("fc+" in c or m._netfw.POOL in c) for c in ins))
            # a pool source on the LAN interface is forged: dropped before any by-IP check
            self.assertIn(("iptables", "-I", "INPUT", "1", "-i", m._host.HOSTIF, "-s", m._netfw.POOL, "-j", "DROP"), ins)
            # an ACCEPT that already exists (maybe below the DROP) is removed and re-inserted on top
            calls.clear()
            state = {"nfs": 1}      # one stray copy of the NFS rule
            def sh2(*a, check=True):
                calls.append(a)
                if a[1] == "-D" and "2049" in a and state["nfs"] > 0:
                    state["nfs"] -= 1; return types.SimpleNamespace(returncode=0)
                return types.SimpleNamespace(returncode=1)
            m._util.sh = sh2
            m._netfw.ensure_guest_input_rules()
            nfs = [c for c in calls if "2049" in c]
            self.assertEqual([c[1] for c in nfs], ["-D", "-D", "-I"])      # delete until gone, then insert at 1
            self.assertEqual(nfs[-1][2:4], ("INPUT", "1"))
            calls.clear()
            m._netfw.ensure_antispoof({"name": "hass", "index": 7})
            spec = ("-i", "fc7", "!", "-s", "172.30.7.2", "-j", "DROP")
            self.assertEqual({c[2] for c in calls if c[1] == "-I"}, {"INPUT", "FORWARD"})
            for c in calls:
                if c[1] == "-I":
                    self.assertEqual(c[4:], spec)
        finally:
            m._util.sh = old_sh

    @staticmethod
    def _fake_iptables(state):
        """A tiny iptables over {(table, chain): [spec, …]}: -N -F -X -A -I -C -D."""
        import types
        def sh(*a, check=True):
            ok = types.SimpleNamespace(returncode=0, stdout="")
            bad = types.SimpleNamespace(returncode=1, stdout="")
            if a[0] != "iptables":
                return bad
            a = list(a[1:])
            table = "filter"
            if a[:1] == ["-t"]:
                table, a = a[1], a[2:]
            op, chain, rest = a[0], a[1], tuple(a[2:])
            key = (table, chain)
            if op == "-N":
                if key in state: return bad
                state[key] = []; return ok
            if key not in state:
                return bad
            rules = state[key]
            if op == "-F": rules.clear(); return ok
            if op == "-X": del state[key]; return ok
            if op == "-A": rules.append(rest); return ok
            if op == "-I": rules.insert(int(rest[0]) - 1, rest[1:]); return ok
            if op == "-C": return ok if rest in rules else bad
            if op == "-D":
                if rest in rules: rules.remove(rest); return ok
                return bad
            return bad
        return sh

    def test_host_firewall_opt_in_rules_idempotent_and_rollback(self):
        """HOST_FIREWALL (site.json, default off): off = not one iptables call.
        On: own chains that only RETURN or DROP (enabling can never open a
        path), SSH before any DROP, :8700 from the LAN only from
        HOST_FW_MANAGER_FROM, docker bridges -> host only :8700, NFS/rpcbind
        never from outside the taps, the taps left to the guest rules. A second
        run changes nothing; the rollback restores the exact earlier state."""
        import copy
        m, nf = self.m, self.m._netfw
        old_sh, site = m._util.sh, dict(m._settings.SITE)
        env = {k: os.environ.pop(k) for k in ("HOST_FIREWALL", "HOST_FW_MANAGER_FROM") if k in os.environ}
        calls = []
        try:
            m._util.sh = lambda *a, check=True: calls.append(a)
            m._settings.SITE.pop("HOST_FIREWALL", None)
            self.assertFalse(nf.ensure_host_firewall())
            self.assertEqual(calls, [])                          # off: untouched
            m._settings.SITE.update(HOST_FIREWALL="1", HOST_FW_MANAGER_FROM="192.168.56.198")
            rules = nf.host_firewall_rules()
            self.assertTrue(all(s[-1] in ("RETURN", "DROP") for _t, _c, s in rules))
            inp = [s for _t, c, s in rules if c == nf.FW_IN]
            self.assertEqual(inp[0], ("-i", "fc+", "-j", "RETURN"))          # taps: the guest rules
            first_drop = next(i for i, s in enumerate(inp) if s[-1] == "DROP")
            self.assertLess(inp.index(("-p", "tcp", "--dport", "22", "-j", "RETURN")), first_drop)
            port = str(m._host.LISTEN[1])
            allow = inp.index(("-i", m._host.HOSTIF, "-s", "192.168.56.198", "-p", "tcp", "--dport", port, "-j", "RETURN"))
            self.assertLess(allow, inp.index(("-p", "tcp", "--dport", port, "-j", "DROP")))
            self.assertLess(inp.index(("-i", "docker0", "-p", "tcp", "--dport", port, "-j", "RETURN")),
                            inp.index(("-i", "docker0", "-j", "DROP")))
            for d in ("111", "2049"):
                self.assertIn(("-p", "tcp", "--dport", d, "-j", "DROP"), inp)
            self.assertIn(("mangle", nf.FW_PRE, ("-i", m._host.HOSTIF, "-d", nf.POOL, "-m", "conntrack",
                                                  "--ctstate", "NEW", "-j", "DROP")), rules)
            # default source for :8700 over the LAN: the HOSTIF subnet
            m._settings.SITE.pop("HOST_FW_MANAGER_FROM")
            old_lan, nf._lan_net = nf._lan_net, lambda: "10.9.8.0/24"
            try:
                self.assertIn(("-i", m._host.HOSTIF, "-s", "10.9.8.0/24", "-p", "tcp", "--dport", port, "-j", "RETURN"),
                              [s for _t, _c, s in nf.host_firewall_rules()])
            finally:
                nf._lan_net = old_lan
            # idempotent on a modelled ruleset, rollback restores it exactly
            state = {("filter", "INPUT"): [("-i", "fc+", "-j", "DROP")],
                     ("filter", "FORWARD"): [("-j", "DOCKER-USER")], ("mangle", "PREROUTING"): []}
            before = copy.deepcopy(state)
            m._util.sh = self._fake_iptables(state)
            self.assertTrue(nf.ensure_host_firewall())
            once = copy.deepcopy(state)
            self.assertEqual(state[("filter", "INPUT")][0], ("-j", nf.FW_IN))
            self.assertEqual(state[("filter", "FORWARD")][-1], ("-j", nf.FW_FWD))
            self.assertEqual(state[("mangle", "PREROUTING")][0], ("-j", nf.FW_PRE))
            # subnet unreadable (the fake has no `ip`): :8700 is left open, never dropped for everyone
            self.assertNotIn(("-p", "tcp", "--dport", port, "-j", "DROP"), state[("filter", nf.FW_IN)])
            nf.ensure_host_firewall()
            self.assertEqual(state, once)
            nf.remove_host_firewall()
            self.assertEqual(state, before)
        finally:
            m._util.sh = old_sh
            m._settings.SITE.clear(); m._settings.SITE.update(site)
            os.environ.update(env)


    # ---- squad git remote: push, fetch, PRs + issues host-side; the token never in a VM --------
    SG_TOK = "ghp_SeCrEtToKeN0123456789"

    def _sg_env(self):
        """_squad_env + a worktree project 'wp' (a lead, b writer, d reader) on a
        git repo, a host secret store with the token, a local bare repo as the
        remote; every subprocess call recorded (argv, env)."""
        import subprocess as sp
        m = self.m
        woke, tasks, undo = self._squad_env()
        tmp = os.path.dirname(m._projects.PROJECTS_FILE)
        sg = m._squadgit
        old = (m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._secrets.SECRETS_FILE, sg.STATE_FILE, sg.git_url,
               sg.FILE_PROTOCOL, m._util.rate_ok, sp.run, m._secrets.load_secret_policy)
        m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT = os.path.join(tmp, "agent"), tmp
        m._secrets.SECRETS_FILE = os.path.join(tmp, "secrets.env")
        _wtext(m._secrets.SECRETS_FILE, f"SQUAD_GIT_TOKEN={self.SG_TOK}\nOTHER_KEY=x\n")
        sg.STATE_FILE = os.path.join(tmp, "squadgit.json")
        repo, g = self._git_repo(tmp)
        bare = os.path.join(tmp, "remote.git")
        sp.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        self._sg_git_url = sg.git_url
        sg.git_url, sg.FILE_PROTOCOL = (lambda cfg: bare), "always"
        m._util.rate_ok = lambda *a: True
        calls, real = [], sp.run
        sp.run = lambda args, *a, **k: (calls.append((list(args), dict(k.get("env") or {}))), real(args, *a, **k))[1]
        self.assertTrue(m._projects.upsert({"name": "wp", "strategy": "worktree", "source": {"type": "host", "path": repo},
                                            "members": {"a": "lead", "b": "writer", "d": "reader"}}).get("ok"))

        def undo2():
            (m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._secrets.SECRETS_FILE, sg.STATE_FILE, sg.git_url,
             sg.FILE_PROTOCOL, m._util.rate_ok, sp.run, m._secrets.load_secret_policy) = old
            undo()
        return tmp, repo, g, bare, calls, undo2

    def _sg_remote(self, provider="github", **kw):
        st, d = self._sq("/api/squad-git/wp", "127.0.0.1", {"provider": provider, "url": "https://github.com/own/rep",
                                                             "token_secret": "SQUAD_GIT_TOKEN", **kw})
        self.assertEqual(st, 200, d)
        return d

    def _sg_no_token(self, tmp, calls, *texts):
        """The token is in no argv, no stored file, no answer; only the git env carries it (as a header)."""
        tok = self.SG_TOK
        b64 = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        for argv, env in calls:
            self.assertFalse([a for a in argv if tok in a or b64 in a], argv)
            for k, v in env.items():
                if tok in v or b64 in v:
                    self.assertEqual(k, "GIT_CONFIG_VALUE_0"); self.assertTrue(v.startswith("Authorization: Basic "))
        for d, _dirs, files in os.walk(tmp):
            for f in files:
                p = os.path.join(d, f)
                if p == self.m._secrets.SECRETS_FILE or os.path.islink(p) or "/objects/" in p:
                    continue
                with open(p, "rb") as fh:
                    self.assertNotIn(tok.encode(), fh.read(), p)
        for t in texts:
            self.assertNotIn(tok, t)

    def test_squadgit_settings_admin_only_and_token_reserved(self):
        """Remote settings: admin only (a guest and role user are refused),
        validated, kept across a project save; the token secret is reserved —
        never released to an instance even when the policy lists it."""
        m = self.m
        tmp, repo, g, bare, calls, undo = self._sg_env()
        L, B = "127.0.0.1", "172.30.4.2"
        try:
            for body, why in (({"url": "http://github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN"}, "url:"),
                              ({"url": "https://tok@github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN"}, "url:"),
                              ({"url": "https://github.com/../r", "token_secret": "SQUAD_GIT_TOKEN"}, "url:"),
                              ({"url": "https://github.com/o/r", "token_secret": "NOPE"}, "token_secret"),
                              ({"url": "https://github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN", "provider": "svn"}, "provider"),
                              ({"url": "https://github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN", "base": "squad/x"}, "base"),
                              ({"url": "https://github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN", "daily_limit": 999}, "daily_limit")):
                st, d = self._sq("/api/squad-git/wp", L, body)
                self.assertEqual(st, 400, body); self.assertIn(why, d["error"], body)
            st, d = self._sq("/api/squad-git/wp", B, {"url": "https://github.com/o/r", "token_secret": "SQUAD_GIT_TOKEN"})
            self.assertEqual(st, 403)                                                   # a guest: admin route
            self.assertIsNone(m._squadgit.settings(m._projects.get("wp")))
            ua = m._auth.user_allowed
            for meth, path in (("GET", "/api/squad-git/wp"), ("POST", "/api/squad-git/wp"), ("POST", "/api/squad-git/wp/fetch")):
                self.assertFalse(ua(meth, path), path)                                  # OIDC role user: refused
            d = self._sg_remote(daily_limit=5)
            self.assertEqual((d["remote"]["base"], d["remote"]["label"], d["remote"]["daily_limit"]), ("main", "kaim56-squad", 5))
            p = m._projects.get("wp")
            m._projects.upsert({k: p[k] for k in ("name", "strategy", "source", "members", "base")})
            self.assertEqual(m._projects.get("wp")["remote"]["daily_limit"], 5)        # a project save keeps it
            st, d = self._sq("/api/squad-git/wp", L)
            self.assertEqual((st, d["remote"]["token_secret"], d["secrets"]), (200, "SQUAD_GIT_TOKEN", ["OTHER_KEY", "SQUAD_GIT_TOKEN"]))
            # reserved: a policy release does not reach the instance
            m._secrets.load_secret_policy = lambda: {"by_template": {"openrouter": ["SQUAD_GIT_TOKEN", "OTHER_KEY"]},
                                                     "by_instance": {}, "guest_readable": ["SQUAD_GIT_TOKEN", "OTHER_KEY"]}
            inst = next(i for i in self._sq_insts if i["name"] == "b")
            self.assertEqual(m._secrets.allowed_secret_keys(inst), {"OTHER_KEY"})
            self.assertEqual(m._secrets.guest_readable_keys(inst), {"OTHER_KEY"})
            # the real endpoints (tests replace them): GitHub, GitHub Enterprise, Gitea under a path
            sg = m._squadgit
            self.assertEqual(sg.api_base({"provider": "github", "url": "https://github.com/own/rep.git"}),
                             "https://api.github.com/repos/own/rep")
            self.assertEqual(sg.api_base({"provider": "github", "url": "https://ghe.example/own/rep"}),
                             "https://ghe.example/api/v3/repos/own/rep")
            self.assertEqual(sg.api_base({"provider": "gitea", "url": "https://git.kat56.de/own/rep"}),
                             "https://git.kat56.de/api/v1/repos/own/rep")
            self.assertEqual(self._sg_git_url({"url": "https://git.kat56.de/g/own/rep/"}), "https://git.kat56.de/g/own/rep.git")
            st, d = self._sq("/api/squad-git/wp", L, {"remove": True})
            self.assertEqual((st, m._squadgit.settings(m._projects.get("wp"))), (200, None))
            self._sg_no_token(tmp, calls)
        finally:
            undo()

    def test_squadgit_push_and_fetch_host_side(self):
        """push: the writer's branch to squad/<writer>/<name> only (base and
        other prefixes impossible, readers/non-members refused, the lead names
        a writer); fetch: refs/remotes/kaim56/* only. The token: env header,
        never argv, files, answers or the log; no remote URL stored in the repo."""
        import contextlib
        import subprocess as sp
        m = self.m
        tmp, repo, g, bare, calls, undo = self._sg_env()
        A, B, C, D, L = "172.30.3.2", "172.30.4.2", "172.30.5.2", "172.30.6.2", "127.0.0.1"
        gb = lambda *a: sp.run(["git", "--git-dir", bare, *a], capture_output=True, text=True).stdout
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                st, d = self._sq("/api/git/push", B, {})
                self.assertEqual(st, 404); self.assertIn("has a git remote", d["error"])
                self._sg_remote()
                _wtext(os.path.join(m._projwt.tree_dir("wp", "b"), "feat.txt"), "feature\n")
                st, d = self._sq("/api/git/push", B, {})
                self.assertEqual((st, d["branch"], d["repo"]), (200, "squad/b/wp", "own/rep"), d)
                self.assertEqual(gb("rev-parse", "refs/heads/squad/b/wp").strip(), g("rev-parse", "proj/wp/b").strip())
                self.assertEqual(gb("show", "squad/b/wp:feat.txt"), "feature\n")
                st, d = self._sq("/api/git/push", B, {"name": "main"})
                self.assertEqual((st, d["branch"]), (200, "squad/b/main"))
                self.assertEqual(gb("for-each-ref", "--format=%(refname)").split(), ["refs/heads/squad/b/main", "refs/heads/squad/b/wp"])
                for name in ("../main", "x/../../main", "a/b", "-x", "x.lock", "x" * 61):
                    self.assertEqual(self._sq("/api/git/push", B, {"name": name})[0], 400, name)
                self.assertEqual(self._sq("/api/git/push", D, {})[0], 403)             # a reader
                self.assertEqual(self._sq("/api/git/push", C, {"project": "wp"})[0], 403)   # no member
                self.assertIn("names the writer", self._sq("/api/git/push", A, {})[1]["error"])
                self.assertEqual(self._sq("/api/git/push", A, {"writer": "a"})[0], 403)     # the lead has no branch
                st, d = self._sq("/api/git/push", A, {"writer": "b", "name": "review"})
                self.assertEqual((st, d["branch"]), (200, "squad/b/review"))
                self.assertEqual(self._sq("/api/git/push", B, {"writer": "a"})[1]["branch"], "squad/b/wp")  # a writer: always itself
                # fetch: a branch someone else pushed arrives under refs/remotes/kaim56/, the checkout untouched
                gb("update-ref", "refs/heads/feature", gb("rev-parse", "squad/b/wp").strip())
                head = g("rev-parse", "HEAD").strip()
                st, d = self._sq("/api/squad-git/wp/fetch", L, {})
                self.assertEqual((st, d["namespace"]), (200, "refs/remotes/kaim56"), d)
                self.assertEqual(g("rev-parse", "refs/remotes/kaim56/feature").strip(), gb("rev-parse", "feature").strip())
                self.assertEqual((g("rev-parse", "HEAD").strip(), g("status", "--porcelain")), (head, ""))
                self.assertNotIn("feature", g("branch", "--list"))
                st, d = self._sq("/api/git/fetch", D, {})                                # any member may fetch
                self.assertEqual((st, d["refs"]), (200, 4))
                # the git process: the token as an env header only, no stored remote
                pushes = [(a, e) for a, e in calls if "push" in a]
                self.assertTrue(pushes)
                self.assertTrue(all(e.get("GIT_CONFIG_KEY_0") == "http.extraHeader" for a, e in pushes))
                with open(os.path.join(repo, ".git", "config")) as fh:
                    self.assertNotIn("[remote", fh.read())
            self._sg_no_token(tmp, calls, out.getvalue())
            with open(os.path.join(m._audit.AUDIT_DIR, "b.jsonl")) as fh:
                audit = fh.read()
            self.assertIn('"tool": "git_push_branch"', audit); self.assertIn("wp own/rep", audit)
        finally:
            undo()

    def test_squadgit_prs_and_issues_github_and_gitea(self):
        """PRs and issues against a fake GitHub and a fake Gitea: the right
        endpoints and auth headers, the label added (created when missing),
        PRs filtered out of issue lists, roles, the daily limit, no merge."""
        m = self.m
        tmp, repo, g, bare, calls, undo = self._sg_env()
        A, B, C, D = "172.30.3.2", "172.30.4.2", "172.30.5.2", "172.30.6.2"
        srv, url, log = _fake_forge()
        sg = m._squadgit
        old_api = sg.api_base
        sg.api_base = lambda cfg: url + ("/api/v1" if cfg["provider"] == "gitea" else "") + "/repos/own/rep"
        answers = []

        def q(path, ip, body):
            st, d = self._sq(path, ip, body)
            answers.append(json.dumps(d))
            return st, d
        try:
            for prov, auth in (("github", f"Bearer {self.SG_TOK}"), ("gitea", f"token {self.SG_TOK}")):
                log.clear()
                self._sg_remote(prov, daily_limit=3)
                st, d = q("/api/git/pr-create", B, {"title": "Add feature", "body": "does X"})
                self.assertEqual((st, d.get("head"), d.get("base"), d.get("state")), (200, "squad/b/wp", "main", "open"), d)
                n = d["number"]
                self.assertEqual(log[0][1:3], ("POST", "/repos/own/rep/pulls"))
                self.assertEqual(log[0][5]["head"], "squad/b/wp")
                self.assertIn("squad member `b`", log[0][5]["body"])
                self.assertTrue(all(e[4].get("authorization") == auth for e in log), prov)
                lab = [(e[1], e[2]) for e in log[1:]]
                if prov == "github":
                    self.assertEqual(lab, [("GET", "/repos/own/rep/labels/kaim56-squad"), ("POST", "/repos/own/rep/labels"),
                                           ("POST", f"/repos/own/rep/issues/{n}/labels")])
                    self.assertEqual(log[-1][5], {"labels": ["kaim56-squad"]})
                    self.assertEqual(log[0][4].get("x-github-api-version"), "2022-11-28")
                else:
                    self.assertEqual(lab, [("GET", "/repos/own/rep/labels"), ("POST", "/repos/own/rep/labels"),
                                           ("POST", f"/repos/own/rep/issues/{n}/labels")])
                    self.assertEqual(log[-1][5], {"labels": [7]})
                self.assertEqual(q("/api/git/pr-create", D, {"title": "x"})[0], 403)      # a reader
                st, d = q("/api/git/issue-create", D, {"title": "Bug", "body": "y" * 50000})
                self.assertEqual(st, 200, d)
                body = next(e[5]["body"] for e in reversed(log) if e[1:3] == ("POST", "/repos/own/rep/issues"))
                self.assertLessEqual(len(body), sg.BODY_MAX + 200)
                st, d = q("/api/git/prs", D, {"state": "all"})
                self.assertEqual([i["number"] for i in d["items"]], [n])
                st, d = q("/api/git/issues", D, {})
                self.assertEqual([i["title"] for i in d["items"]], ["Bug"])                  # the PR is not an issue
                if prov == "gitea":
                    self.assertIn("type=issues", log[-1][3])
                self.assertEqual(q("/api/git/issues", D, {"state": "nope"})[0], 400)
                st, d = q("/api/git/pr-comment", D, {"number": n, "body": "LGTM"})
                self.assertEqual((st, log[-1][1:3], log[-1][5]), (200, ("POST", f"/repos/own/rep/issues/{n}/comments"), {"body": "LGTM"}))
                st, d = q("/api/git/pr", D, {"number": n})
                self.assertEqual((st, d["body"].split("\n")[0], d["comments"][0]["body"]), (200, "does X", "LGTM"))
                st, d = q("/api/git/issue-comment", A, {"number": n + 1, "body": "on it"})
                self.assertEqual(st, 200)
                self.assertEqual(q("/api/git/issue-comment", A, {"number": "x", "body": "a"})[0], 400)
                self.assertEqual(q("/api/git/issues", C, {"project": "wp"})[0], 403)         # no member
                self.assertEqual(q("/api/git/pr-merge", B, {"number": n})[0], 404)           # merging: not offered
                # the daily limit: per member (b: 1 PR so far, limit 3)
                self.assertEqual(q("/api/git/issue-create", B, {"title": "2"})[0], 200)
                self.assertEqual(q("/api/git/issue-create", B, {"title": "3"})[0], 200)
                st, d = q("/api/git/issue-create", B, {"title": "4"})
                self.assertEqual(st, 429); self.assertIn("daily limit", d["error"])
                self.assertEqual(q("/api/git/issue-create", A, {"title": "lead's"})[0], 200)  # another member
                os.remove(sg.STATE_FILE)                                                      # next provider: fresh count
            self.assertTrue(set(sg.TOOLS.values()) <= m._policy.tool_names())               # in the catalog
            # an instance whose allowlist lacks the tool
            next(i for i in self._sq_insts if i["name"] == "d")["config"]["AGENT_TOOLS"] = "bash"
            self.assertEqual(q("/api/git/issues", D, {})[0], 403)
            self._sg_no_token(tmp, calls, *answers)
            with open(os.path.join(m._audit.AUDIT_DIR, "b.jsonl")) as fh:
                audit = fh.read()
            self.assertIn('"tool": "pr_create"', audit); self.assertIn("own/rep #1", audit)
        finally:
            sg.api_base = old_api
            srv.shutdown(); srv.server_close()
            undo()


    # ---- squad kickoff: repo URL + goal -> clone, project, remote, architect, goal posted -----
    SK_TOK = "ghp_KiCkOfFtOkEn9876543210"

    def _kick_env(self):
        """_gate_env + a squads dir under the browse root, a host secret
        store, a bare repo the kickoff clones (clone_url), the strong tier,
        persona code-architect; steps run in the caller's thread; every
        Popen recorded (argv, env)."""
        import subprocess as sp
        m = self.m
        woke, created, notes, merges, undo = self._gate_env()
        tmp = os.path.dirname(m._projects.PROJECTS_FILE)
        k, sg = m._squadkick, m._squadgit
        old = (m._settings.SITE, k._spawn, k.clone_url, sg.FILE_PROTOCOL, sg.STATE_FILE, m._secrets.SECRETS_FILE,
               m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._indexer.instance, m._instances.delete_instance,
               k.MAX_BYTES, sp.Popen)
        m._settings.SITE = {**old[0], "SQUADS_DIR": os.path.join(tmp, "squads"), "SQUAD_GITEA_HOSTS": "git.example.org"}
        k._jobs.clear()
        k._spawn = lambda fn, *a: fn(*a)
        repo, _g = self._git_repo(tmp)
        bare = os.path.join(tmp, "src.git")
        sp.run(["git", "clone", "-q", "--bare", repo, bare], check=True)
        k.clone_url, sg.FILE_PROTOCOL = (lambda job: bare), "always"
        sg.STATE_FILE = os.path.join(tmp, "squadgit.json")
        m._secrets.SECRETS_FILE = os.path.join(tmp, "secrets.env")
        _wtext(m._secrets.SECRETS_FILE, "OTHER_KEY=x\n")
        m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT = os.path.join(tmp, "agent"), tmp
        m._indexer.instance = lambda: None
        m._instances.delete_instance = lambda n: self._sq_insts.remove(next(i for i in self._sq_insts if i["name"] == n))
        m._router.load = lambda: {"tiers": {"openrouter/acme/big": "strong"}, "policies": {}}
        m._personas.load_personas = lambda: [{"name": "code-architect", "prompt": "You design.", "tools": ["bash"]}]
        calls, real = [], sp.Popen

        def popen(args, *a, **kw):
            calls.append((list(args), dict(kw.get("env") or {})))
            return real(args, *a, **kw)
        sp.Popen = popen

        def undo2():
            (m._settings.SITE, k._spawn, k.clone_url, sg.FILE_PROTOCOL, sg.STATE_FILE, m._secrets.SECRETS_FILE,
             m._mounts.AGENT_ROOT, m._projwt.STAGE_ROOT, m._indexer.instance, m._instances.delete_instance,
             k.MAX_BYTES, sp.Popen) = old
            k._jobs.clear()
            undo()
        return tmp, bare, woke, created, calls, undo2

    def _kick_dest(self, name="repo"):
        rec = self.m._squadrec.of_project(name)
        return self.m._squadrec.repo_dir(rec, name)

    def _kick_clean(self, tmp, name="repo"):
        m = self.m
        self.assertFalse(os.path.exists(os.path.join(tmp, "squads", name)))
        self.assertFalse([x for x in os.listdir(os.path.join(tmp, "squads")) if x.endswith("__" + name)])
        self.assertFalse(m._squadrec.name_taken(name))
        self.assertIsNone(m._projects.get(name))
        self.assertFalse([i for i in self._sq_insts if i["name"] == name + "-architect"])

    def test_squadkick_parse_hosts_and_names(self):
        """The first https URL is the repo, the rest the goal (both needed);
        github.com and the configured Gitea hosts only; the name comes from
        the repo, -2 when taken; the token secret is named per host."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k = m._squadkick
        try:
            f, _ = k.parse("Please https://github.com/Own/My_Repo.git, add dark mode.")
            self.assertEqual((f["url"], f["provider"], f["repo"], f["goal"]),
                             ("https://github.com/Own/My_Repo", "github", "My_Repo", "Please add dark mode."))
            f, _ = k.parse("https://git.example.org/team/app fix the login")
            self.assertEqual((f["provider"], f["goal"]), ("gitea", "fix the login"))
            for text, why in (("add dark mode", "https URL"), ("https://github.com/o/r", "goal"),
                              ("https://gitlab.com/o/r do it", "not allowed"), ("http://github.com/o/r x", "https URL"),
                              ("https://github.com/o/r/tree/main do it", "github.com/<owner>/<repo>"),
                              ("https://github.com/o do it", "no repository URL")):
                self.assertIn(why, k.parse(text)[1], text)
            self.assertEqual(k.derive_name("My_Repo.JS"), "my-repo-js")
            self.assertEqual(k.derive_name("---"), "squad")
            self.assertEqual(len(k.derive_name("x" * 60)), k.NAME_MAX)
            os.makedirs(os.path.join(tmp, "squads", "taken"))
            self.assertEqual(k.derive_name("taken"), "taken-2")
            self.assertEqual(k.derive_name("sq"), "sq-2")                          # a project of that name
            self.assertEqual((k.token_secret("github.com"), k.token_secret("git.example.org:3000")),
                             ("SQUAD_GIT_TOKEN_GITHUB", "SQUAD_GIT_TOKEN_GIT_EXAMPLE_ORG"))
            _wtext(m._secrets.SECRETS_FILE, "SQUAD_GIT_TOKEN_GITHUB=t\n")
            self.assertIn("SQUAD_GIT_TOKEN_GITHUB", m._squadgit.reserved_secrets())  # never to a VM
            st, d = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "no url here"})
            self.assertEqual(st, 400); self.assertIn("https URL", d["error"])
        finally:
            undo()

    def test_squadkick_happy_path_admin_only_and_idempotent(self):
        """Kickoff: clone under SQUADS_DIR, worktree project on the default
        branch, the remote with the host's token secret, <name>-architect
        (persona, strong tier, lead tools) as lead, squad enabled but not
        approved, the goal posted to the architect. The token is in no argv,
        log or file. Running the steps again changes nothing. Admin only."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k, L = m._squadkick, "127.0.0.1"
        _wtext(m._secrets.SECRETS_FILE, f"SQUAD_GIT_TOKEN_GITHUB={self.SK_TOK}\n")
        try:
            st, j = self._sq("/api/squads/kickoff", L, {"text": "https://github.com/own/repo add dark mode"})
            self.assertEqual((st, j["name"], j["arch"]), (200, "repo", "repo-architect"), j)
            st, j = self._sq("/api/squads/kickoff/" + j["id"], L)
            self.assertEqual(j["status"], "done", j["log"])
            dest = self._kick_dest()
            rec = m._squadrec.of_project("repo")
            self.assertEqual(dest, os.path.join(tmp, "squads", rec["id"] + "__repo", "projects", "repo", "repo"))
            self.assertEqual([(x["name"], x["repo"]) for x in rec["projects"]], [("repo", dest)])
            self.assertEqual([(x["instance"], x["role"], x["persona"]) for x in rec["members"]],
                             [("repo-architect", "lead", "code-architect"), ("repo-req", "reader", "")])
            self.assertTrue(os.path.isfile(os.path.join(m._squadrec.folder(rec), "squad.json")))
            self.assertTrue(os.path.isfile(os.path.join(dest, "top.txt")))
            self.assertEqual(os.stat(os.path.join(tmp, "squads")).st_mode & 0o777, 0o750)
            p = m._projects.get("repo")
            self.assertEqual((p["strategy"], p["base"], p["source"]["path"]), ("worktree", "main", dest))
            self.assertEqual(p["members"]["repo-architect"]["role"], "lead")
            r = m._squadgit.settings(p)
            self.assertEqual((r["provider"], r["url"], r["token_secret"], r["base"]),
                             ("github", "https://github.com/own/repo", "SQUAD_GIT_TOKEN_GITHUB", "main"))
            self.assertEqual([c[0] for c in created], ["repo-architect", "repo-req"])     # + the requirements engineer
            cfg = created[0][2]
            self.assertEqual(cfg["OPENROUTER_MODEL"], "acme/big")
            self.assertTrue(cfg["AGENT_SYSTEM"].startswith("You design."))
            self.assertTrue({"squad_propose", "ticket_create", "squad_post"} <= set(cfg["AGENT_TOOLS"].split(",")))
            s = m._squads.settings(p)
            self.assertEqual((s["enabled"], s["approved"], s["daily_token_budget"]), (True, False, 2_000_000))
            self.assertEqual([w[0] for w in woke], ["repo-req"])                         # the engineer interviews
            self.assertIn("add dark mode", woke[0][1]); self.assertIn("squad_propose", woke[0][1])
            self.assertIn("the architect repo-architect gets REQUIREMENTS.md", woke[0][1])
            self.assertEqual((s["engineer"], p["members"]["repo-req"]["role"]), ("repo-req", "reader"))
            self.assertIn("Repo profile:\n- main dirs: apps (1)", woke[0][1])          # computed after the clone
            self.assertTrue(any(x["text"].startswith("Repo profile: ") for x in j["log"]), j["log"])
            self.assertEqual(m._squads.read("repo")[0]["from"], "user:admin")
            sq = {x["name"]: x for x in self._sq("/api/squads", L)[1]["squads"]}
            self.assertEqual((sq["repo"]["status"], sq["repo"]["lead"]), ("waiting for approval", "repo-architect"))
            # the token: only as a header in git's environment
            b64 = base64.b64encode(f"x-access-token:{self.SK_TOK}".encode()).decode()
            carried = []
            for argv, env in calls:
                self.assertFalse([a for a in argv if self.SK_TOK in a or b64 in a], argv)
                carried += [x for x, v in env.items() if self.SK_TOK in v or b64 in v]
            self.assertEqual(carried, ["GIT_CONFIG_VALUE_0"])                    # the clone, as a header
            self.assertNotIn(self.SK_TOK, json.dumps(j))
            for d, _dirs, files in os.walk(tmp):
                for f in files:
                    fp = os.path.join(d, f)
                    if fp != m._secrets.SECRETS_FILE and not os.path.islink(fp) and "/objects/" not in fp:
                        with open(fp, "rb") as fh:
                            self.assertNotIn(self.SK_TOK.encode(), fh.read(), fp)
            # again: every step skips what is done
            k.run(j["id"])
            self.assertEqual((len(created), len(woke), k.get(j["id"])["status"]), (2, 1, "done"))     # architect + engineer, nothing new
            self.assertEqual(self._sq(f"/api/squads/kickoff/{j['id']}/retry", L, {})[0], 400)
            # the next kickoff of the same repo is a new squad
            st, j2 = self._sq("/api/squads/kickoff", L, {"text": "https://github.com/own/repo second team"})
            self.assertEqual((j2["name"], k.get(j2["id"])["status"]), ("repo-2", "done"))
            # admin only: the kickoff; role user reads the list and the channel, posts
            ua = m._auth.user_allowed
            for meth, path in (("POST", "/api/squads/kickoff"), ("GET", "/api/squads/kickoff"),
                               ("GET", "/api/squads/kickoff/abc"), ("POST", "/api/squads/kickoff/abc/retry")):
                self.assertFalse(ua(meth, path), path)
                self.assertTrue(m._routes.ROUTER.resolve(meth, path)[1])           # not from a guest
            self.assertTrue(ua("GET", "/api/squads")); self.assertTrue(ua("POST", "/api/projects/repo/squad/post"))
            self.assertEqual(self._sq("/api/squads/kickoff", "172.30.3.2", {"text": "x"})[0], 403)
        finally:
            undo()

    def _dumb_http(self, bare, token):
        """A git 'dumb HTTP' server for the bare repo that answers 401 unless
        the request carries the token as Basic auth -> (server, url, seen)."""
        import http.server
        import subprocess as sp
        from http.server import ThreadingHTTPServer
        sp.run(["git", "-C", bare, "update-server-info"], check=True)
        want = "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        seen = []

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=bare, **kw)

            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != want:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="git"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.path = self.path.split("?", 1)[0]
                return super().do_GET()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}/", seen

    def test_squadkick_auth_failure_names_the_secret_then_retry(self):
        """A private repo without a token: the chat names the secret to
        create and nothing is left behind; with the secret, Retry clones over
        HTTP with the token as a header only and finishes."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k = m._squadkick
        srv, url, seen = self._dumb_http(bare, self.SK_TOK)
        k.clone_url = lambda job: url
        try:
            st, j = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "https://github.com/own/repo ship it"})
            j = k.get(j["id"])
            self.assertEqual(j["status"], "failed", j["log"])
            self.assertIn("create the host secret SQUAD_GIT_TOKEN_GITHUB", j["log"][-2]["text"] + j["log"][-1]["text"])
            self._kick_clean(tmp)
            self.assertEqual((created, woke), ([], []))
            _wtext(m._secrets.SECRETS_FILE, f"SQUAD_GIT_TOKEN_GITHUB={self.SK_TOK}\n")
            st, j = self._sq(f"/api/squads/kickoff/{j['id']}/retry", "127.0.0.1", {})
            self.assertEqual(st, 200)
            j = k.get(j["id"])
            self.assertEqual(j["status"], "done", j["log"])
            self.assertTrue(os.path.isfile(os.path.join(self._kick_dest(), "top.txt")))
            self.assertTrue(any(a and a.startswith("Basic ") for _p, a in seen))
            self.assertNotIn(self.SK_TOK, json.dumps(j))
            with open(os.path.join(self._kick_dest(), ".git", "config")) as fh:
                cfg = fh.read()
            self.assertNotIn(self.SK_TOK, cfg); self.assertNotIn("Authorization", cfg)
            self.assertFalse([a for a, _e in calls if any(self.SK_TOK in x for x in a)])
        finally:
            srv.shutdown(); srv.server_close()
            undo()

    def test_squadkick_size_cap_and_failed_step_clean_up(self):
        """Over SQUAD_MAX_BYTES the clone is aborted and removed; a failing
        later step (the instance) removes the clone and the project this
        kickoff made — never what it found."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k = m._squadkick
        try:
            k.MAX_BYTES = 10
            st, j = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "https://github.com/own/repo ship it"})
            j = k.get(j["id"])
            self.assertEqual(j["status"], "failed")
            self.assertTrue(any("larger than" in x["text"] for x in j["log"]), j["log"])
            self._kick_clean(tmp)
            k.MAX_BYTES = 2 * 1024 ** 3
            m._instances.create_instance = lambda *a, **kw: "unknown template 'openrouter'"
            j = k.get(k.retry(j["id"])[0]["id"])
            self.assertEqual(j["status"], "failed")
            self.assertIn("creating repo-architect failed", " ".join(x["text"] for x in j["log"]))
            self._kick_clean(tmp)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "squads")))            # the root stays
        finally:
            undo()

    def test_squadkick_long_goal_goes_as_goal_md(self):
        """A goal over GOAL_INLINE: the whole text as GOAL.md in the engineer's
        workspace, its start and a pointer in the message (a message holds 8000);
        over GOAL_MAX refused (live 2026-10-16: 'longer than 4000 characters')."""
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k = m._squadkick
        try:
            goal = "Build the tool. " + "Requirement line with details. " * 400            # ~12k characters
            st, j = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "https://github.com/own/repo " + goal})
            self.assertEqual(st, 200, j)
            self.assertEqual(k.get(j["id"])["status"], "done", k.get(j["id"])["log"])
            with open(os.path.join(m._mounts.AGENT_ROOT, "repo-req", "GOAL.md")) as fh:
                self.assertEqual(fh.read(), goal.strip() + "\n")
            self.assertEqual(woke[-1][0], "repo-req")
            self.assertIn("read GOAL.md at the top of your workspace", woke[-1][1])
            self.assertLess(len(m._squads.read("repo")[0]["text"]), m._squads.TEXT_MAX)
            st, d = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "https://github.com/own/other " + "x" * 50001})
            self.assertIn("longer than 50000", d["error"])
        finally:
            undo()

    def test_squadkick_empty_repo_gets_a_first_commit(self):
        """An empty GitHub repo: the clone gets an empty first commit on main,
        the kickoff goes on with base main (live 2026-10-16: "the repository
        is empty" stopped it)."""
        import subprocess as sp
        m = self.m
        tmp, bare, woke, created, calls, undo = self._kick_env()
        k = m._squadkick
        try:
            empty = os.path.join(tmp, "empty.git")
            sp.run(["git", "init", "-q", "--bare", empty], check=True)
            k.clone_url = lambda job: empty
            st, j = self._sq("/api/squads/kickoff", "127.0.0.1", {"text": "https://github.com/own/repo build it"})
            j = k.get(j["id"])
            self.assertEqual(j["status"], "done", j["log"])
            self.assertTrue(any("empty first commit on main" in x["text"] for x in j["log"]), j["log"])
            self.assertEqual(m._projects.get("repo")["base"], "main")
            dest = self._kick_dest()
            self.assertEqual(m._projwt.git(dest, ["log", "--format=%s", "main"]).strip(), "Initial commit")
        finally:
            undo()

    # ---- squad records: stable IDs, the archive of every conversation (mgr/squadrec.py) -----
    SR_TOK = "ghp_ArChIvEtOkEn0123456789abcdefXYZ"

    def _sr_lines(self, path):
        with open(path) as fh:
            return [json.loads(x) for x in fh]

    def _sr_read(self, path):
        with open(path) as fh:
            return fh.read()

    def _sr_files(self, root):
        return [os.path.join(d, f) for d, _ds, fs in os.walk(root) for f in fs]

    def test_squadrec_ids_and_archive_of_everything(self):
        """A squad ID (16 x [a-z0-9]) and member IDs (<squad>-<16>) assigned
        once and kept; the same instance has another member ID in another
        squad; posts, ask/answer, wake turns, proposal decisions and ticket
        changes land in their files with the IDs; no secret in any file;
        long text capped; squad.json lists projects and members."""
        m = self.m
        sr = m._squadrec
        woke, created, notes, merges, undo = self._gate_env()
        A, B, L = "172.30.3.2", "172.30.4.2", "127.0.0.1"
        tmp = os.path.dirname(m._projects.PROJECTS_FILE)
        old_sec = m._secrets.SECRETS_FILE
        m._secrets.SECRETS_FILE = os.path.join(tmp, "secrets.env")
        _wtext(m._secrets.SECRETS_FILE, f"SQUAD_GIT_TOKEN_GITHUB={self.SR_TOK}\nSHORT=abc\n")
        try:
            self.assertEqual(len({sr.new_id() for _ in range(2000)}), 2000)
            self.assertNotEqual(sr.new_id({"x"}), "x")
            self.assertIsNone(sr.of_project("sq"))
            self._sq("/api/projects/sq/squad", L, {"enabled": True})                    # the first enable
            rec = sr.of_project("sq")
            self.assertRegex(rec["id"], r"^[a-z0-9]{16}$")
            mids = {x["instance"]: x["member_id"] for x in rec["members"]}
            self.assertEqual(sorted(mids), ["a", "b", "d"])
            for x in mids.values():
                self.assertRegex(x, "^" + rec["id"] + r"-[a-z0-9]{16}$"); self.assertEqual(len(x), 33)
            fold = sr.folder(rec)
            self.assertEqual(os.path.basename(fold), rec["id"] + "__sq")
            # immutable: settings saved, members read, the project saved again
            self._sq("/api/projects/sq/squad", L, {"approved": True})
            st, s = self._sq("/api/projects/sq/squad", L)
            self.assertEqual(s["squad_id"], rec["id"])
            self.assertEqual({x["name"]: x["member_id"] for x in s["members"]}, mids)
            # another squad: b has another member ID there
            self.assertTrue(m._projects.upsert({"name": "sq2", "source": {"type": "host", "path": os.path.join(tmp, "repo")},
                                                "members": {"b": "lead"}}).get("ok"))
            m._squads.save_settings("sq2", m._squads.normalize_settings({"enabled": True})[0])
            rec2 = sr.of_project("sq2")
            self.assertNotEqual(rec2["id"], rec["id"]); self.assertNotEqual(sr.member_id(rec2, "b"), mids["b"])
            # the channel: a post, an ask and its answer
            st, d = self._sq("/api/squad/post", A, {"to": "b", "project": "sq",
                                                    "text": f"token {self.SR_TOK} and Authorization: Bearer abcdefghijkl123"})
            self.assertEqual(st, 200, d)
            self.assertIn(f"(squad id {rec['id']}; your member id {mids['b']})", woke[-1][1])
            self.assertTrue(woke[-1][1].startswith("[squad sq] from a (msg "))
            st, q = self._sq("/api/squad/ask", A, {"to": "b", "question": "which branch?", "project": "sq"})
            st, ans = self._sq("/api/squad/post", B, {"text": "main", "reply_to": q["id"], "project": "sq"})
            day = time.strftime("%Y-%m-%d")
            ch = self._sr_lines(os.path.join(fold, "conversations", "channel", day + ".jsonl"))
            by = {e["msg_id"]: e for e in ch}
            self.assertEqual((by[d["id"]]["from_member_id"], by[d["id"]]["to_member_ids"], by[d["id"]]["delivered"]),
                             (mids["a"], {"b": mids["b"]}, ["b"]))
            self.assertEqual((by[q["id"]]["kind"], by[ans["id"]]["kind"], by[ans["id"]]["reply_to"], by[ans["id"]]["from_member_id"]),
                             ("ask", "answer", q["id"], mids["b"]))
            self.assertTrue(all(e["squad_id"] == rec["id"] for e in ch))
            # a wake turn: the worker runs the task, its result goes to b's file (capped)
            t = {"id": "t1", "instance": "b", "message": woke[-1][1], "squad": {"project": "sq", "depth": 1, "msg": q["id"]}}
            tk = m._tasks
            old_t = (tk._run_task_now, m._store.with_tasks, m._chats.chat_log_append, m._store.history_add, tk._mission_advance_fire)
            tk._run_task_now = lambda *a, **kw: (True, "x" * (sr.FIELD_MAX + 500))
            m._store.with_tasks = lambda fn: None
            m._chats.chat_log_append = m._store.history_add = tk._mission_advance_fire = lambda *a, **kw: None
            try:
                tk._execute(t)
            finally:
                (tk._run_task_now, m._store.with_tasks, m._chats.chat_log_append, m._store.history_add, tk._mission_advance_fire) = old_t
            turn = self._sr_lines(os.path.join(fold, "conversations", "members", f"{mids['b']}__b", day + ".jsonl"))[-1]
            self.assertEqual((turn["member_id"], turn["task_id"], turn["msg_id"], turn["status"], turn["incoming"]),
                             (mids["b"], "t1", q["id"], "done", woke[-1][1]))
            self.assertTrue(turn["result"].endswith(" …[truncated 500 chars]")); self.assertIn("duration_s", turn)
            # a proposal and its decision (with the operator's edits); tickets
            pid = self._sq("/api/squad/propose", A, self._SG_PLAN)[1]["id"]
            with open(os.path.join(fold, "proposals", pid + ".json")) as fh:
                self.assertEqual(json.load(fh)["status"], "pending")
            edits = {"members": self._SG_PLAN["members"], "budget_tokens_per_day": 400000, "tickets": self._SG_PLAN["tickets"]}
            self.assertEqual(self._sq("/api/projects/sq/squad/decide", L, {"id": pid, "approve": True, **edits})[0], 200)
            with open(os.path.join(fold, "proposals", pid + ".json")) as fh:
                pj = json.load(fh)
            self.assertEqual((pj["status"], pj["decided_by"], pj["edits"]["budget_tokens_per_day"], pj["squad_id"]),
                             ("approved", "admin", 400000, rec["id"]))
            self.assertTrue(pj["steps"])
            rec = sr.of_project("sq")
            self.assertEqual({x["instance"]: x["persona"] for x in rec["members"] if x["instance"] in ("e", "f")},
                             {"e": "coder", "f": "ghost"})
            self.assertEqual({x["instance"]: x["member_id"] for x in rec["members"] if x["instance"] in mids}, mids)
            st, tu = self._sq("/api/projects/sq/squad/ticket", L, {"id": 1, "status": "doing", "comment": "go"})
            self.assertEqual(st, 200, tu)
            tl = self._sr_lines(os.path.join(fold, "tickets.jsonl"))
            self.assertEqual([(e["event"], e["ticket"]["key"]) for e in tl], [("create", "T1"), ("create", "T2"), ("update", "T1")])
            self.assertEqual(tl[-1]["change"], {"status": "doing", "comment": "go"})
            # squad.json: projects, members, settings; modes; no secret anywhere
            with open(os.path.join(fold, "squad.json")) as fh:
                sj = json.load(fh)
            self.assertEqual(([p["name"] for p in sj["projects"]], sj["id"]), (["sq"], rec["id"]))
            self.assertEqual(sorted(x["instance"] for x in sj["members"]), ["a", "b", "d", "e", "f"])
            self.assertTrue(sj["settings"]["sq"]["approved"])
            files = self._sr_files(sr.squads_dir())
            self.assertGreater(len(files), 5)
            for f in files:
                self.assertEqual(os.stat(f).st_mode & 0o777, 0o640, f)
                with open(f) as fh:
                    body = fh.read()
                self.assertNotIn(self.SR_TOK, body, f); self.assertNotIn("abcdefghijkl123", body, f)
            for dd, _ds, _fs in os.walk(fold):
                self.assertEqual(os.stat(dd).st_mode & 0o777, 0o750, dd)
            self.assertIn("[secret]", json.dumps(by[d["id"]]))
            # a deleted project leaves the squad; the archive stays; a new one of that name is a new squad
            self.assertEqual(self._sq("/api/projects/sq2/delete", L, {})[0], 200)
            self.assertIsNone(sr.of_project("sq2")); self.assertTrue(os.path.isdir(sr.folder(rec2)))
        finally:
            m._secrets.SECRETS_FILE = old_sec
            undo()

    def test_squadrec_migration_idempotent_keeps_a_mounted_clone(self):
        """At start every squad project gets its record and folder; history
        from before is exported once (re-running adds nothing); a pre-ID clone
        SQUADS_DIR/<name> stays while a member runs (recorded), and moves
        into <id>__<name>/projects/<name>/repo — the project following — once
        nothing runs."""
        import subprocess as sp
        m = self.m
        sr = m._squadrec
        woke, created, notes, merges, undo = self._gate_env()
        A = "172.30.3.2"
        tmp = os.path.dirname(m._projects.PROJECTS_FILE)
        try:
            legacy = os.path.join(sr.squads_dir(), "old")
            os.makedirs(sr.squads_dir(), exist_ok=True)
            sp.run(["git", "init", "-q", "-b", "main", legacy], check=True)
            self.assertTrue(m._projects.upsert({"name": "old", "source": {"type": "host", "path": legacy},
                                                "members": {"a": "lead", "b": "writer"}}).get("ok"))
            # history from before IDs: the squad disabled -> no record yet
            m._projects.set_squad("old", m._squads.normalize_settings({"enabled": False})[0])
            m._squads.post(m._projects.get("old"), "user:admin", ["a"], "hello from before")
            m._squads.post(m._projects.get("old"), "a", ["b"], "second")
            m._squadgate._ticket_add("old", {"title": "old ticket"}, "a")
            m._squadgate._put({"id": "p0ld", "project": "old", "kind": "member", "status": "rejected", "by": "a"})
            m._projects.set_squad("old", m._squads.normalize_settings({"enabled": True, "approved": True})[0])
            self.assertIsNone(sr.of_project("old"))
            running = {"a"}
            m._instances.is_running = lambda i: i["name"] in running
            msgs = sr.migrate()
            rec = sr.of_project("old")
            self.assertRegex(rec["id"], r"^[a-z0-9]{16}$")
            self.assertTrue(any("running member" in x for x in msgs), msgs)
            self.assertTrue(os.path.isdir(os.path.join(legacy, ".git")))                  # not moved
            self.assertEqual(m._projects.get("old")["source"]["path"], legacy)
            self.assertEqual(rec["projects"][0]["repo"], legacy); self.assertTrue(rec["projects"][0]["move_pending"])
            fold = sr.folder(rec)
            ch = os.path.join(fold, "conversations", "channel")
            exported = [e for f in sorted(os.listdir(ch)) for e in self._sr_lines(os.path.join(ch, f))]
            self.assertEqual([e["text"] for e in exported], ["hello from before", "second"])
            self.assertTrue(all(e["migrated"] for e in exported))
            self.assertEqual(exported[1]["from_member_id"], sr.member_id(rec, "a"))
            self.assertTrue(os.path.isfile(os.path.join(fold, "proposals", "p0ld.json")))
            self.assertEqual(len(self._sr_lines(os.path.join(fold, "tickets.jsonl"))), 1)
            snap = {f: self._sr_read(f) for f in self._sr_files(fold)}
            # again: nothing new, same IDs
            sr.migrate()
            self.assertEqual(sr.of_project("old")["id"], rec["id"])
            self.assertEqual({f: self._sr_read(f) for f in self._sr_files(fold)}, snap)
            # nothing runs any more: the clone moves, the project follows
            running.clear()
            msgs = sr.migrate()
            target = sr.repo_dir(rec, "old")
            self.assertTrue(any("moved" in x for x in msgs), msgs)
            self.assertTrue(os.path.isdir(os.path.join(target, ".git"))); self.assertFalse(os.path.exists(legacy))
            self.assertEqual(m._projects.get("old")["source"]["path"], target)
            rec = sr.of_project("old")
            self.assertEqual(rec["projects"][0]["repo"], target); self.assertNotIn("move_pending", rec["projects"][0])
            self.assertEqual(sr.migrate(), [])
            self.assertEqual(sr.of_project("old")["id"], rec["id"])
            # a member woken now sees its IDs in the frame
            self._sq("/api/squad/post", A, {"to": "b", "project": "old", "text": "after"})
            self.assertIn(sr.member_id(rec, "b"), woke[-1][1])
        finally:
            undo()

    def test_personas_seed_adds_missing_only(self):
        """New built-in personas join an existing personas.json; a persona
        the user has (even of the same name) is never touched."""
        m = self.m
        p = m._personas
        old = p.PERSONAS_FILE
        p.PERSONAS_FILE = os.path.join(tempfile.mkdtemp(prefix="e2e-pers-"), "personas.json")
        try:
            with open(p.PERSONAS_FILE, "w") as fh:
                json.dump([{"name": "mine", "prompt": "x"}, {"name": "qa-engineer", "prompt": "my own"}], fh)
            self.assertEqual(p.seed_missing(), ["ui-developer"])
            got = {x["name"]: x["prompt"] for x in p.load_personas()}
            self.assertEqual((got["mine"], got["qa-engineer"]), ("x", "my own"))
            self.assertIn("accessible", got["ui-developer"])
            self.assertEqual(p.seed_missing(), [])
            os.remove(p.PERSONAS_FILE)
            self.assertTrue({"qa-engineer", "ui-developer"} <= {x["name"] for x in p.load_personas()})   # a new install
        finally:
            p.PERSONAS_FILE = old


def _fake_forge():
    """A fake GitHub (/repos/…) and Gitea (/api/v1/repos/…) REST API ->
    (server, base url, log of (provider, method, path, query, headers, body))."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    log, items, comments, labels = [], {}, {}, {"github": [], "gitea": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            path, _, qs = self.path.partition("?")
            prov = "gitea" if path.startswith("/api/v1/") else "github"
            path = path[len("/api/v1"):] if prov == "gitea" else path
            log.append((prov, self.command, path, qs, {k.lower(): v for k, v in self.headers.items()}, body))
            mine = {k[1]: v for k, v in items.items() if k[0] == prov}
            r = re.match(r"^/repos/own/rep(/.*)$", path)
            rest, meth = (r.group(1) if r else ""), self.command
            mm = re.match(r"^/(pulls|issues)/(\d+)(/comments|/labels)?$", rest)
            if rest in ("/pulls", "/issues") and meth == "POST":
                num = len(mine) + 1
                it = {"number": num, "title": body["title"], "body": body.get("body"), "state": "open",
                      "user": {"login": "squad-bot"}, "html_url": f"https://forge/own/rep/{rest[1:]}/{num}"}
                if rest == "/pulls":
                    it.update(head={"ref": body["head"]}, base={"ref": body["base"]})
                items[(prov, num)] = it
                return self._send(201, it)
            if rest in ("/pulls", "/issues") and meth == "GET":
                out = []
                for it in mine.values():
                    if rest == "/pulls" and "head" not in it:
                        continue
                    if rest == "/issues" and "head" in it:
                        if prov == "gitea":
                            continue                                  # type=issues
                        it = {**it, "pull_request": {"url": "x"}}
                    out.append(it)
                return self._send(200, out)
            if mm and not mm.group(3) and meth == "GET" and int(mm.group(2)) in mine:
                return self._send(200, mine[int(mm.group(2))])
            if mm and mm.group(3) == "/comments":
                key = (prov, int(mm.group(2)))
                if meth == "POST":
                    c = {"id": 99, "body": body["body"], "user": {"login": "squad-bot"}, "created_at": "now",
                         "html_url": f"https://forge/c/{mm.group(2)}"}
                    comments.setdefault(key, []).append(c)
                    return self._send(201, c)
                return self._send(200, comments.get(key, []))
            if mm and mm.group(3) == "/labels" and meth == "POST":
                return self._send(200, [])
            if rest == "/labels" and meth == "POST":
                labels[prov].append(body["name"])
                return self._send(201, {"id": 7, "name": body["name"]})
            if rest == "/labels" and meth == "GET" and prov == "gitea":
                return self._send(200, [{"id": 7, "name": x} for x in labels["gitea"]])
            if rest.startswith("/labels/") and meth == "GET" and prov == "github":
                name = urllib.parse.unquote(rest[len("/labels/"):])
                return self._send(200, {"name": name}) if name in labels["github"] else self._send(404, {"message": "Not Found"})
            return self._send(404, {"message": "Not Found"})

        do_GET = do_POST = _do

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", log


class IndexerService(unittest.TestCase):
    """agents/indexer/indexer_service.py without repowise: the index steps, the
    tool schema, the JSON-RPC dispatch, a reindex with faked commands, the
    process pool."""
    PATH = os.path.join(os.path.dirname(FC_DIR), "agents", "indexer", "indexer_service.py")

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(cls.PATH):
            raise unittest.SkipTest("agents/indexer not next to this tree")
        cls.s = _load("indexer_service_e2e", cls.PATH)

    def test_index_steps_clone_once_init_then_update(self):
        s = self.s
        first = s.index_steps("p", "main", cloned=False, indexed=False)
        self.assertEqual(first[0][:2], ["git", "clone"]); self.assertEqual(first[0][-2:], [s.SRC_ROOT + "/p", s.DATA + "/p"])
        self.assertEqual(first[1][-2:], ["origin", "main"])
        self.assertIn("-e", first[3]); self.assertEqual(first[3][-1], ".repowise")       # the index survives the clean
        self.assertEqual(first[-1][:3], ["repowise", "init", "--no-prose"])
        self.assertIn("--no-editor-setup", first[-1]); self.assertIn("--no-hook", first[-1])
        later = s.index_steps("p", "HEAD", cloned=True, indexed=True)
        self.assertEqual([a[:2] for a in later], [["git", "-C"]] * 3 + [["repowise", "update"]])
        self.assertEqual((s.ENV["DO_NOT_TRACK"], s.ENV["REPOWISE_TELEMETRY_DISABLED"]), ("1", "1"))

    def test_tools_get_a_required_project_and_lose_repo(self):
        tools = [{"name": "get_risk", "inputSchema": {"type": "object", "properties": {"targets": {}, "repo": {}},
                                                       "required": ["targets", "repo"]}}, {"name": "bare"}]
        out = self.s.with_project_arg(tools)
        self.assertEqual(list(out[0]["inputSchema"]["properties"]), ["project", "targets"])
        self.assertEqual(out[0]["inputSchema"]["required"], ["project", "targets"])
        self.assertEqual(out[1]["inputSchema"]["required"], ["project"])
        self.assertIn("repo", tools[0]["inputSchema"]["properties"])                    # the cache stays untouched

    def test_handle_rpc(self):
        s, seen = self.s, []
        h = lambda msg: s.handle_rpc(msg, tools=lambda: [{"name": "t"}],
                                     call=lambda p, n, a: (seen.append((p, n, a)), ({"content": []}, ""))[1])
        self.assertEqual(h({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"], "kaim56-indexer")
        self.assertIsNone(h({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(h({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"], [{"name": "t"}])
        self.assertEqual(h({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "t", "arguments": {}}})["error"]["code"], -32602)
        r = h({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "t", "arguments": {"project": "p", "repo": "x", "k": 1}}})
        self.assertEqual((r["result"], seen), ({"content": []}, [("p", "t", {"k": 1})]))
        self.assertEqual(h({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"], -32601)
        boom = s.handle_rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, tools=lambda: 1 / 0)
        self.assertIn("ZeroDivisionError", boom["error"]["message"])

    def test_reindex_runs_the_steps_and_records(self):
        s = self.s
        tmp = tempfile.mkdtemp(prefix="e2e-idxsvc-")
        old = (s.DATA, s.SRC_ROOT, s.STATE_FILE)
        s.DATA, s.SRC_ROOT, s.STATE_FILE = os.path.join(tmp, "data"), os.path.join(tmp, "project"), os.path.join(tmp, "data", "state.json")
        os.makedirs(os.path.join(s.SRC_ROOT, "p", ".git")); os.makedirs(s.DATA)
        ran = []

        class R:
            def __init__(self, rc=0, out=""):
                self.returncode, self.stdout, self.stderr = rc, out, "boom" if rc else ""

        def run(argv, **kw):
            ran.append(argv)
            if argv[:2] == ["git", "clone"]:
                os.makedirs(os.path.join(argv[-1], ".git"))
            return R(out="abc123\n") if argv[-2:] == ["rev-parse", "HEAD"] else R()
        try:
            out = s.reindex("p", "main", run=run, wait_src=0)
            self.assertEqual((out["ok"], out["commit"]), (True, "abc123"))
            self.assertEqual(ran[0][:2], ["git", "clone"]); self.assertEqual(ran[-2][:2], ["repowise", "init"])
            self.assertEqual(s.load_state()["p"]["commit"], "abc123")
            os.makedirs(os.path.join(s.DATA, "p", ".repowise")); open(os.path.join(s.DATA, "p", ".repowise", "state.json"), "w").close()
            ran.clear()
            self.assertTrue(s.reindex("p", "main", run=run, wait_src=0)["ok"])
            self.assertEqual(ran[-2][:2], ["repowise", "update"]); self.assertNotIn(["git", "clone"], [a[:2] for a in ran])
            self.assertEqual(s.indexed_projects(), ["p"])
            fail = s.reindex("p", "main", run=lambda argv, **kw: R(rc=1), wait_src=0)
            self.assertEqual(fail["ok"], False); self.assertIn("exit 1: boom", fail["error"])
            self.assertEqual(s.indexed_projects(), [])                                  # the last run failed
            self.assertIn("no git repository", s.reindex("q", run=run, wait_src=0)["error"])
            self.assertEqual(s.reindex("../x", run=run)["error"], "bad project name")
            self.assertEqual(s.reindex("p", "--upload-pack=x", run=run)["error"], "bad ref")
        finally:
            s.DATA, s.SRC_ROOT, s.STATE_FILE = old

    def test_dashboard_starts_once_per_project_and_stops(self):
        """API on loopback, UI on the VM's network with the API behind it; one
        project at a time; a dead process or no port in time -> stopped, error;
        only indexed projects; a reindex of the shown project stops it."""
        s = self.s
        tmp = tempfile.mkdtemp(prefix="e2e-idxdash-")
        old = (s.DATA, s.STATE_FILE, s.SRC_ROOT)
        s.DATA, s.STATE_FILE, s.SRC_ROOT = tmp, os.path.join(tmp, "state.json"), os.path.join(tmp, "src")
        os.makedirs(os.path.join(s.SRC_ROOT, "b", ".git"))
        made = []

        class P:
            def __init__(self, argv, **kw):
                self.argv, self.kw, self.rc, self.terminated = argv, kw, None, False
                made.append(self)

            def poll(self):
                return self.rc

            def terminate(self):
                self.terminated, self.rc = True, -15

            def wait(self, timeout=None):
                return self.rc
        up = {"ok": True}
        try:
            for p in ("a", "b"):
                os.makedirs(os.path.join(tmp, p, ".repowise"))
                open(os.path.join(tmp, p, ".repowise", "state.json"), "w").close()
            s._save_state({"a": {"ok": True}, "b": {"ok": True}, "c": {"ok": False}})
            api, ui = s.dashboard_cmds("a")
            self.assertEqual(api[0], os.path.join(tmp, "a"))
            self.assertEqual(api[1][:3] + api[1][4:6], ["repowise", "serve", "--no-ui", "127.0.0.1", "--port"])
            self.assertEqual((ui[2]["HOSTNAME"], ui[2]["PORT"], ui[2]["REPOWISE_API_URL"]),
                             ("0.0.0.0", str(s.DASH_PORT), f"http://127.0.0.1:{s.DASH_API_PORT}"))
            for env in (api[2], ui[2]):
                self.assertEqual((env["DO_NOT_TRACK"], env["REPOWISE_TELEMETRY_DISABLED"]), ("1", "1"))
            self.assertEqual(ui[2]["NEXT_TELEMETRY_DISABLED"], "1"); self.assertEqual(api[2]["REPOWISE_EMBEDDER"], "mock")
            # the UI's proxy sets X-Forwarded-For: uvicorn must not trust it, or repowise answers 403
            self.assertNotIn(api[2].get("FORWARDED_ALLOW_IPS", "127.0.0.1"), ("127.0.0.1", "*", "127.0.0.1,::1"))

            d = s.Dashboard(popen=P, ready=lambda port: up["ok"], wait=0.2)
            self.assertEqual(d.start("a"), {"ok": True, "project": "a", "port": s.DASH_PORT})
            self.assertEqual(([p.argv[0] for p in made], d.current()), (["repowise", "node"], "a"))
            self.assertTrue(d.start("a")["ok"]); self.assertEqual(len(made), 2)            # already up: reused
            self.assertTrue(d.start("b")["ok"])
            self.assertEqual(([p.terminated for p in made], d.current()), ([True, True, False, False], "b"))
            self.assertIn("not indexed", d.start("c")["error"]); self.assertIn("not indexed", d.start("zz")["error"])
            self.assertEqual(d.start("../a")["error"], "bad project name")
            made[-1].rc = 1                                                             # the UI died
            up["ok"] = False
            out = d.start("b")
            self.assertFalse(out["ok"]); self.assertEqual(d.current(), "")
            up["ok"] = True
            self.assertTrue(d.start("b")["ok"])
            old_dash, s.DASH = s.DASH, d
            try:
                s.reindex("b", run=lambda argv, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "x"})(), wait_src=0)
                self.assertEqual(d.current(), "")                                       # stopped by the reindex
            finally:
                s.DASH = old_dash
        finally:
            s.DATA, s.STATE_FILE, s.SRC_ROOT = old

    def test_pool_keeps_at_most_size_and_drops_the_least_recent(self):
        made = []

        class F:
            def __init__(self, project):
                self.project, self.used, self.dead = project, time.time(), False
                made.append(project)

            def alive(self):
                return not self.dead

            def kill(self):
                self.dead = True
        pool = self.s.Pool(factory=F, size=2)
        a = pool.get("a"); a.used -= 10
        pool.get("b"); pool.get("a")
        pool.get("c")                                                                   # a is the least recent
        self.assertEqual((pool.live(), a.dead), (["b", "c"], True))
        pool.drop("b")
        self.assertEqual(pool.live(), ["c"])
        self.assertEqual(made, ["a", "b", "c"])


# ===========================================================================
# HTTP: against the running manager
# ===========================================================================
class ClaudeGuest(unittest.TestCase):
    def test_claude_template_has_room_for_a_build(self):
        """1 GB without swap OOM-killed Claude Code (~280 MB) next to a Go/cgo build."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "claude.json")) as f:
            self.assertGreaterEqual(json.load(f)["mem_mib"], 2048)

    def test_claude_mcp_relays_catalog_mcps_through_the_hub(self):
        """The claude guest's kaim56 MCP server also exposes the instance's
        MCP_SERVERS (names from /api/mcp-config, calls via /api/mcp) as
        <server>__<tool>; a failing server is skipped, platform tools stay."""
        from unittest import mock
        src = next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py"),
                                "/home/ulrich/claude-signal-firecracker/kaim56_mcp.py") if os.path.exists(p)), None)
        if not src:
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_hub", src)
        sent = []
        km._get = lambda path: json.dumps({"mcpServers": {"portainer": {}, "broken": {}}})
        def post(path, payload, timeout=30):
            sent.append((path, payload["server"], payload["payload"]["method"]))
            if payload["server"] == "broken":
                return json.dumps({"error": "hub down"})
            m = payload["payload"]["method"]
            if m == "tools/list":
                return json.dumps({"result": {"tools": [{"name": "list_stacks", "description": "d"}]}})
            if m == "tools/call":
                return json.dumps({"result": {"content": [{"type": "text", "text": "3 stacks"}]}})
            return json.dumps({"result": {}})
        km._post = post
        out = io.StringIO()
        with mock.patch.object(km.sys, "stdin", io.StringIO(
                '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
                '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"portainer__list_stacks","arguments":{}}}\n')), \
             mock.patch.object(km.sys, "stdout", out), mock.patch.object(km.sys, "stderr", io.StringIO()):
            km.main()
        lst, call = [json.loads(x) for x in out.getvalue().splitlines()]
        names = [t["name"] for t in lst["result"]["tools"]]
        self.assertIn("memory_store", names)
        self.assertIn("portainer__list_stacks", names)
        self.assertFalse(any(n.startswith("broken__") for n in names))
        self.assertEqual(call["result"]["content"][0]["text"], "3 stacks")
        self.assertIn(("/api/mcp", "portainer", "tools/call"), sent)


    def test_claude_mcp_squad_tools(self):
        """The squad tools are in the claude guest's kaim56 MCP server; a refusal
        carries the manager's reason."""
        import urllib.error
        src = next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py"),
                                "/home/ulrich/claude-signal-firecracker/kaim56_mcp.py") if os.path.exists(p)), None)
        if not src:
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_squad", src)
        for t in ("squad_post", "squad_ask", "squad_read", "squad_members"):
            self.assertIn(t, km.TOOLS)
        km._post = lambda path, payload, timeout=30: json.dumps({"id": 3, "project": "sq", "delivered": ["b"]})
        self.assertEqual(km.TOOLS["squad_post"][0](text="hi", to="b"), "Posted #3 in sq (delivered to b).")
        def refuse(path):
            raise urllib.error.HTTPError(path, 403, "Forbidden", {}, io.BytesIO(b'{"error": "guests only"}'))
        km._get = refuse
        self.assertEqual(km.TOOLS["squad_read"][0](), "Error: guests only")
        for t in ("squad_propose", "squad_request_member", "squad_request_merge",
                  "ticket_list", "ticket_update", "ticket_create", "ticket_assign"):
            self.assertIn(t, km.TOOLS)
        km._post = lambda path, payload, timeout=30: json.dumps({"id": "p1", "project": "sq", "kind": "merge", "status": "pending"})
        self.assertIn("waiting for the user's approval", km.TOOLS["squad_request_merge"][0](writer="b", summary="done"))
        km._post = lambda path, payload, timeout=30: json.dumps({"id": "p1", "project": "sq", "kind": "squad", "status": "pending", "version": 3})
        self.assertIn("Proposal p1 is now v3, waiting for approval", km.TOOLS["squad_propose"][0](goal="g", members=[{"name": "e"}]))
        self.assertIn("Revise proposal", km.TOOLS["squad_propose"][1])
        km._get = lambda path: json.dumps({"project": "sq", "approved": False, "tickets": []})
        self.assertEqual(km.TOOLS["ticket_list"][0](), "Project sq (squad plan not approved yet):\nno tickets")


    def test_claude_mcp_toolchain_tools(self):
        """The claude guest's MCP server has toolchain_propose / toolchain_status, same wording."""
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py")
        if not os.path.exists(src):
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_tc", src)
        sent = []
        km._post = lambda path, payload, timeout=30: (sent.append((path, payload)), json.dumps(
            {"id": "p9", "project": "sq", "kind": "toolchain", "status": "pending"}))[1]
        self.assertIn("waiting for the user's approval", km.TOOLS["toolchain_propose"][0](apt=["gcc"], reason="C"))
        self.assertEqual(sent[-1][0], "/api/squad/toolchain-propose"); self.assertEqual(sent[-1][1]["apt"], ["gcc"])
        km._get = lambda path: json.dumps({"project": "sq", "version": 1, "active": False,
                                           "spec": {"apt": ["gcc"], "script": "make install", "env": {}}, "templates": []})
        out = km.TOOLS["toolchain_status"][0]()
        self.assertIn("does NOT have it yet", out); self.assertIn("make install", out)
        self.assertIn("apt", km.TOOLS["toolchain_propose"][2]["properties"])

    def test_claude_mcp_squad_catalog(self):
        """squad_catalog in the claude guest's MCP server: same listing; squad_propose documents template/new_persona."""
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py")
        if not os.path.exists(src):
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_cat", src)
        got = []
        km._get = lambda path: (got.append(path), json.dumps(_CATALOG))[1]
        out = km.TOOLS["squad_catalog"][0]()
        self.assertEqual(got, ["/api/squad/catalog?"])
        for line in ("- coder: You code. [tools: bash]", "new_persona", "- std (standard): s", "- claude: Claude Code"):
            self.assertIn(line, out)
        props = km.TOOLS["squad_propose"][2]["properties"]["members"]["items"]["properties"]
        self.assertEqual(props["template"]["enum"], ["openrouter", "claude"]); self.assertIn("new_persona", props)

    def test_claude_guest_init_account_token(self):
        """guest-init: an oauth_token from the manager becomes
        CLAUDE_CODE_OAUTH_TOKEN for the agent processes (no file, no log), a
        stale credential file goes first; the old file path stays."""
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "guest-init.sh")
        if not os.path.exists(src):
            self.skipTest("guest-init.sh not found")
        with open(src) as fh:
            text = fh.read()
        self.assertIn('export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_TOK"', text)
        self.assertIn('print(d["oauth_token"])', text)
        self.assertIn("rm -f /home/node/.claude/.credentials.json", text)
        self.assertIn('open("/home/node/.claude/.credentials.json", "w")', text)
        self.assertLess(text.index("export CLAUDE_CODE_OAUTH_TOKEN"), text.index("RUNAS="))   # inherited by bridge/webterm
        self.assertNotRegex(text, r"echo[^\n]*CLAUDE_TOK")

    def test_claude_guest_init_role_section(self):
        """guest-init.sh writes AGENT_SYSTEM into CLAUDE.md under "## Your role"
        between markers: replaced on the next boot, removed without one, the
        rest of the file kept, shell characters literal."""
        import subprocess as sp
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "guest-init.sh")
        if not os.path.exists(src):
            self.skipTest("guest-init.sh not found")
        with open(src) as fh:
            text = fh.read()
        block = text[text.index("# >>> kaim56-role"):text.index("# <<< kaim56-role")]
        self.assertIn('role_section "$PLATFORM_MD"', text)
        d = tempfile.mkdtemp(prefix="e2e-role-")
        md = os.path.join(d, "CLAUDE.md")
        _wtext(md, "# mine\n")
        run = lambda sysp: sp.run(["sh", "-c", block + '\nrole_section "$1"', "sh", md], check=True,
                                  env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "AGENT_SYSTEM": sysp})
        run("You code.\n\nYou are e, writer. $HOME `id` 100%")
        run("You review.")
        with open(md) as fh:
            self.assertEqual(fh.read(), "# mine\n<!-- kaim56-role -->\n## Your role\n\nYou review.\n<!-- /kaim56-role -->\n")
        run("You code.\n\nYou are e, writer. $HOME `id` 100%")
        with open(md) as fh:
            self.assertIn("You are e, writer. $HOME `id` 100%\n<!-- /kaim56-role -->", fh.read())
        run("")
        with open(md) as fh:
            self.assertEqual(fh.read(), "# mine\n")
        shutil.rmtree(d, ignore_errors=True)

    def test_guest_init_toolchain_layer_and_build_mode(self):
        """Both guest-inits: the layer (fc_layer=) mounted read-only and stacked
        under the upper, without it when it fails; build mode right after the
        config, skipping everything else; the env file sourced for the agent."""
        ag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")
        env_file = "/etc/kaim56-toolchain.env"                     # mgr/toolchain.ENV_FILE
        for init in ("claude/guest-init.sh", "openrouter/guest-init-openrouter.sh"):
            p = os.path.join(ag, init)
            if not os.path.exists(p):
                self.skipTest("agents/ not found")
            with open(p) as fh:
                text = fh.read()
            for s in ("s/.*fc_layer=\\([^ ]*\\).*/\\1/p", 'mount -o ro "$LY" /mnt/layer', 'LOWER="lowerdir=/mnt/layer/upper:/"',
                      'ov "lowerdir=/"', "toolchain layer $LY not mountable", '"${TOOLCHAIN_BUILD:-0}" = "1"',
                      "timeout 1800 sh /config/toolchain.sh", "rm -f /.fc-overlay; rm -rf /root/.cache /tmp/*",
                      "fsfreeze -f /oldroot/mnt", 'echo "KAIM56_TOOLCHAIN_RESULT=ok"', "echo b > /proc/sysrq-trigger",
                      f"[ -f {env_file} ] && . {env_file}", '*" fc_build=1 "*) MOPT=async'):
                self.assertIn(s, text, (init, s))
            start = text.index('if [ "${TOOLCHAIN_BUILD:-0}" = "1" ]')
            block = text[start:text.index("\nfi\n", start)]
            self.assertLess(text.index("/config/config.env"), start, init)          # after the config ...
            for later in ("AGENT_SUDO", "mount -t nfs4", "fc_reconcile", "webterm.py"):  # ... before everything else
                self.assertLess(start, text.index(later), (init, later))
                self.assertNotIn(later, block)
            self.assertNotIn("8700", block)
            self.assertLess(text.index(env_file), text.index("webterm.py"))
            self.assertTrue(block.rstrip().endswith("while true; do sleep 3600; done"), init)

    def test_guest_init_agent_sudo(self):
        """AGENT_SUDO=1 writes a NOPASSWD sudoers drop-in for node (0440,
        visudo-checked), otherwise removes it; both images ship sudo."""
        ag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")
        pairs = [("claude/guest-init.sh", "claude/Dockerfile.rootfs"),
                 ("openrouter/guest-init-openrouter.sh", "openrouter/Dockerfile.openrouter")]
        if not os.path.exists(os.path.join(ag, pairs[0][0])):
            self.skipTest("agents/ not found")
        for init, docker in pairs:
            with open(os.path.join(ag, init)) as fh:
                text = fh.read()
            for s in ('"${AGENT_SUDO:-0}" = "1"', 'echo "node ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/kaim56-agent',
                      "chmod 0440 /etc/sudoers.d/kaim56-agent", "/proc/sys/fs/inotify/max_user_watches", "visudo -cf /etc/sudoers.d/kaim56-agent",
                      "else rm -f /etc/sudoers.d/kaim56-agent", "[init] sudo:"):
                self.assertIn(s, text, init)
            self.assertLess(text.index("/config/config.env"), text.index("AGENT_SUDO:-0"), init)   # after the config
            with open(os.path.join(ag, docker)) as fh:
                self.assertRegex(fh.read(), r"apt-get install[^&]* sudo\b", docker)

    def test_claude_hitl_hook_and_ask_human(self):
        """kaim56_mcp.py --hook (Claude Code PreToolUse): in a squad, risky Bash and
        listed kaim56 tools are denied with "waiting for approval <id>"; approved,
        other tools, no squad or no manager: allowed; every call is reported to
        /api/audit first (the board's live tool). ask_human is an MCP tool. The
        guest-init block merges the hook into settings.json once, keeping the rest."""
        import subprocess as sp
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py")
        if not os.path.exists(src):
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_hitl", src)
        pol, sent, reply = {"squad": "sq", "tools": ["git_push_branch", "pr_create", "bash"], "bash_re": r"\bcurl\b"}, [], {"id": "abcabcabcabc", "status": "open"}
        km._get = lambda path: json.dumps(pol if path == "/api/hitl/policy" else {})
        km._post = lambda path, payload, timeout=30: (sent.append((path, payload)), json.dumps(reply))[1]
        ev = lambda name, inp: {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp}
        out = io.StringIO()
        self.assertEqual(km.hook(io.StringIO(json.dumps(ev("Bash", {"command": "curl x"}))), out), 0)
        d = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual((d["hookEventName"], d["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertEqual(d["permissionDecisionReason"], "waiting for approval abcabcabcabc — end your turn; you will be woken with the decision")
        self.assertEqual(sent[-1], ("/api/hitl", {"tool": "bash", "target": "curl x", "async": True}))
        self.assertEqual(sent[-2], ("/api/audit", {"tool": "bash", "target": "curl x", "ok": True, "result": "started"}))
        n = len(sent)
        self.assertEqual((km.hook(io.StringIO(json.dumps(ev("Read", {"file_path": "/w/a.go"}))), out), len(sent)), (0, n + 1))
        self.assertEqual(sent[-1], ("/api/audit", {"tool": "Read", "target": "/w/a.go", "ok": True, "result": "started"}))
        self.assertTrue(km.hook_decision(ev("mcp__kaim56__pr_create", {"title": "t"})))
        self.assertEqual(sent[-1][1]["tool"], "pr_create")
        n = len(sent)
        for e in (ev("Bash", {"command": "ls"}), ev("Read", {"file_path": "x"}), ev("mcp__kaim56__squad_post", {"text": "x"}),
                  ev("mcp__other__pr_create", {})):
            self.assertEqual(km.hook_decision(e), "")
        self.assertEqual(len(sent), n)
        reply["status"] = "approved"
        self.assertEqual(km.hook_decision(ev("Bash", {"command": "curl x"})), "")
        pol["squad"] = ""
        self.assertEqual(km.hook_decision(ev("Bash", {"command": "curl x"})), "")
        def down(path):
            raise OSError("no manager")
        km._get = down
        out = io.StringIO()
        self.assertEqual((km.hook(io.StringIO(json.dumps(ev("Bash", {"command": "curl x"}))), out), out.getvalue()), (0, ""))
        self.assertEqual((km.hook(io.StringIO("not json"), out), out.getvalue()), (0, ""))
        km._post = lambda path, payload, timeout=30: (sent.append((path, payload)), json.dumps({"id": "q", "ticket": "", "status": "open"}))[1]
        self.assertEqual(km.TOOLS["ask_human"][0](question="Which DB?"), "asked q — end your turn; the answer wakes you")
        self.assertEqual(sent[-1], ("/api/hitl/ask", {"question": "Which DB?", "options": [], "ticket": ""}))
        # guest-init: the settings.json block
        gi = os.path.join(os.path.dirname(src), "guest-init.sh")
        with open(gi) as fh:
            text = fh.read()
        block = text[text.index("# >>> kaim56-hitl-hook"):text.index("# <<< kaim56-hitl-hook")]
        self.assertIn("hitl_hook /home/node/.claude/settings.json", text)
        d = tempfile.mkdtemp(prefix="e2e-hook-")
        st = os.path.join(d, "settings.json")
        _wtext(st, json.dumps({"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "mine"}]}]}}))
        run = lambda: sp.run(["sh", "-c", block + '\nhitl_hook "$1"', "sh", st], check=True,
                             env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        run(); run()
        got = _readj(st)
        self.assertEqual(got["model"], "opus")
        pre = got["hooks"]["PreToolUse"]
        self.assertEqual([h["matcher"] for h in pre], ["Write", "*"])
        self.assertEqual(pre[1]["hooks"][0]["command"], "python3 /app/kaim56_mcp.py --hook")
        os.remove(st); run()
        self.assertEqual(len(_readj(st)["hooks"]["PreToolUse"]), 1)
        shutil.rmtree(d, ignore_errors=True)

    def test_claude_mcp_git_tools(self):
        """The squad git tools are in the claude guest's kaim56 MCP server and
        call /api/git/<op>; a refusal carries the manager's reason."""
        import urllib.error
        src = next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents", "claude", "kaim56_mcp.py"),
                                "/home/ulrich/claude-signal-firecracker/kaim56_mcp.py") if os.path.exists(p)), None)
        if not src:
            self.skipTest("kaim56_mcp.py not found")
        km = _load("kaim56_mcp_git", src)
        for t in ("git_push_branch", "git_fetch", "pr_create", "pr_list", "pr_view", "pr_comment",
                  "issue_create", "issue_list", "issue_comment"):
            self.assertIn(t, km.TOOLS)
        sent = []
        km._post = lambda path, payload, timeout=30: (sent.append((path, payload)), json.dumps(
            {"number": 2, "url": "https://git.kat56.de/o/r/issues/2#c", "project": "wp"}))[1]
        self.assertEqual(km.TOOLS["issue_comment"][0](number=2, body="on it"), "Commented on #2: https://git.kat56.de/o/r/issues/2#c")
        self.assertEqual(sent[-1], ("/api/git/issue-comment", {"number": 2, "body": "on it"}))
        def refuse(path, payload, timeout=30):
            raise urllib.error.HTTPError(path, 429, "Too Many", {}, io.BytesIO(b'{"error": "daily limit reached"}'))
        km._post = refuse
        self.assertEqual(km.TOOLS["issue_create"][0](title="x"), "Error: daily limit reached")

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

    def test_usage_by_model_folds_task_vms(self):
        """The Resources chart's data: one row per instance AND model; the
        one-shot task VMs are summed as "tasks"; `since` cuts by time."""
        import mgr.store as st
        old = st.HISTORY_DB, st._migrated[0]
        tmp = tempfile.mkdtemp(prefix="e2e-ubm-")
        try:
            st.HISTORY_DB = os.path.join(tmp, "history.db"); st._migrated[0] = False
            now = int(time.time())
            for inst, model, pt, ct, cost, ts in (("orch", "a/x", 100, 10, 0.01, now), ("orch", "b/y", 50, 5, 0.02, now),
                                                  ("task-0a1b2c", "a/x", 20, 2, 0.001, now), ("task-3d4e5f", "a/x", 30, 3, 0.002, now),
                                                  ("orch", "a/x", 1000, 1, 1.0, now - 100000)):
                with st._hist_lock, st._hist_conn() as c:
                    c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,completion_tokens,cost) VALUES(?,?,?,?,?,?)",
                              (ts, inst, model, pt, ct, cost))
            rows = {(r["instance"], r["model"]): r for r in st.usage_by_model(now - 3600)}
            self.assertEqual(set(rows), {("orch", "a/x"), ("orch", "b/y"), ("tasks", "a/x")})
            self.assertEqual((rows[("tasks", "a/x")]["in"], rows[("tasks", "a/x")]["calls"], rows[("tasks", "a/x")]["cost"]), (50, 2, 0.003))
            self.assertEqual(rows[("orch", "a/x")]["in"], 100)                   # the old row is outside the window
            self.assertEqual(st.usage_by_model(0)[0], {**rows[("orch", "a/x")], "in": 1100, "out": 11, "calls": 2, "cost": 1.01})
            st_, txt = _http("/api/usage-by-model?since=0")
            self.assertEqual(st_, 200); self.assertIn("rows", json.loads(txt))
        finally:
            st.HISTORY_DB, st._migrated[0] = old

    def test_usage_by_instance(self):
        st, txt = _http("/api/usage/orchestrator?since=0")
        self.assertEqual(st, 200)
        d = json.loads(txt)
        for k in ("calls", "in", "out", "cost"):
            self.assertIn(k, d)

    def test_claude_bridge_reports_usage_and_traces(self):
        """The claude bridge, on a real turn, logs one summary line and posts a
        /api/usage and a /api/trace start+end so Logs/Resources/Traces show it."""
        import io, types
        wb = _load("web_bridge_obs", os.path.join(os.path.dirname(os.path.dirname(AGENT_PATH)), "claude", "web_bridge.py")
                   if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(AGENT_PATH)), "claude", "web_bridge.py"))
                   else "/home/ulrich/claude-signal-firecracker/web_bridge.py")
        posts = []; logs = []
        old = wb._post, wb._log, wb.subprocess.run, wb._sync_credentials
        try:
            wb._post = lambda path, payload: posts.append((path, payload))
            wb._log = lambda *m: logs.append(" ".join(str(x) for x in m))
            wb._sync_credentials = lambda force=False: False
            fake = json.dumps({"result": "done", "session_id": "s1", "usage": {"input_tokens": 1200, "output_tokens": 80},
                               "total_cost_usd": 0.0, "num_turns": 3})
            wb.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=fake, stderr="")
            out = wb._claude_run("hello", None, True)
            self.assertEqual(out, "done")
            paths = [p for p, _ in posts]
            self.assertIn("/api/usage", paths); self.assertEqual(paths.count("/api/trace"), 2)
            usage = next(pl for pp, pl in posts if pp == "/api/usage")
            self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["direct"]), (1200, 80, True))
            self.assertTrue(usage["ok"])
            self.assertTrue(any("tokens 1200/80" in x for x in logs))
            self.assertTrue(wb._last_turn[0])                       # turn id set for the X-Kaim-Turn header
        finally:
            wb._post, wb._log, wb.subprocess.run, wb._sync_credentials = old

    def test_claude_bridge_syncs_the_host_login(self):
        """The claude VM works from a copy of the host's OAuth login that goes
        stale once the host rotates its refresh token. The bridge pulls the
        host's credential before a turn when it is newer, and once by force
        when Claude answers with the auth failure."""
        import io
        wb = _load("web_bridge_e2e", os.path.join(os.path.dirname(os.path.dirname(AGENT_PATH)), "claude", "web_bridge.py")
                   if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(AGENT_PATH)), "claude", "web_bridge.py"))
                   else "/home/ulrich/claude-signal-firecracker/web_bridge.py")
        tmp = tempfile.mkdtemp(prefix="e2e-cred-")
        wb.CRED_PATH = os.path.join(tmp, ".claude", ".credentials.json")
        host = {"claudeAiOauth": {"accessToken": "new", "refreshToken": "r2", "expiresAt": 2000}}
        old_open = urllib.request.urlopen                 # the bridge shares the global module: restore it
        wb.urllib.request.urlopen = lambda url, timeout=None: io.BytesIO(json.dumps(host).encode())
        self.addCleanup(setattr, urllib.request, "urlopen", old_open)
        self.assertTrue(wb._sync_credentials())                                   # no copy yet -> written
        self.assertEqual(json.load(open(wb.CRED_PATH))["claudeAiOauth"]["accessToken"], "new")
        self.assertEqual(oct(os.stat(wb.CRED_PATH).st_mode & 0o777), "0o600")
        self.assertFalse(wb._sync_credentials())                                  # same age -> untouched
        host["claudeAiOauth"] = {"accessToken": "newer", "expiresAt": 3000}
        self.assertTrue(wb._sync_credentials())
        host["claudeAiOauth"] = {"accessToken": "older", "expiresAt": 1000}
        self.assertFalse(wb._sync_credentials()); self.assertTrue(wb._sync_credentials(force=True))
        self.assertEqual(json.load(open(wb.CRED_PATH))["claudeAiOauth"]["accessToken"], "older")
        # the turn: auth failure -> forced sync -> one retry
        calls = []
        wb._claude_run = lambda msg, resume, keep: calls.append(msg) or ("Failed to authenticate: OAuth session expired" if len(calls) == 1 else "hi")
        self.assertEqual(wb._claude_once("hello", None, True), "hi")
        self.assertEqual(len(calls), 2)
        wb.urllib.request.urlopen = lambda url, timeout=None: (_ for _ in ()).throw(OSError("no manager"))
        self.assertFalse(wb._sync_credentials(force=True))                         # no manager: keep the copy, no crash

    def test_platform_release_skips_app_releases(self):
        """The app's releases (app-v5.38) share the repo: the manager's update
        check must pick the newest PLATFORM release, not GitHub's 'latest'."""
        from mgr import about as _about
        pick = _about._platform_release
        rel = [{"tag_name": "app-v5.38"}, {"tag_name": "v1.1.0", "prerelease": True},
               {"tag_name": "v1.0.9", "draft": True}, {"tag_name": "v1.0.1"}, {"tag_name": "v1.0.0"}]
        self.assertEqual(pick(rel)["tag_name"], "v1.0.1")
        self.assertIsNone(pick([{"tag_name": "app-v5.38"}]))
        self.assertIsNone(pick(None))

    def test_version_and_update_check(self):
        """The installed version comes from VERSION (dev without it), the newest
        release from GitHub through a cached check whose failure is remembered,
        and "available" only when both parse and the release is newer."""
        m = _load("manager_e2e_ver", MANAGER_PATH)
        tmp = tempfile.mkdtemp(prefix="e2e-ver-")
        old = m._about.VERSION_FILE, m._about._fetch_latest_release, dict(m._about._update), os.environ.get("UPDATE_CHECK")
        try:
            os.environ.pop("UPDATE_CHECK", None)
            m._about.VERSION_FILE = os.path.join(tmp, "VERSION")
            self.assertEqual(m._about.installed_version(), "dev")
            open(m._about.VERSION_FILE, "w").write("v1.0.0-4-gabc\n")
            self.assertEqual(m._about.installed_version(), "v1.0.0-4-gabc")
            m._about._fetch_latest_release = lambda: {"latest": "v1.0.1", "url": "https://x/r", "notes": "n"}
            m._about._update.update(ts=0.0, latest="", url="", notes="", error="")
            u = m._about.update_check()
            self.assertEqual((u["latest"], u["error"]), ("v1.0.1", ""))
            self.assertTrue(m._about.update_available("v1.0.0-4-gabc", "v1.0.1"))
            self.assertTrue(m._about.update_available("1.0.0", "v1.0.1"))
            self.assertFalse(m._about.update_available("v1.0.1", "v1.0.1"))
            self.assertFalse(m._about.update_available("dev", "v1.0.1"))
            self.assertFalse(m._about.update_available("v1.0.1", ""))
            def boom():
                raise OSError("offline")
            m._about._fetch_latest_release = boom
            self.assertEqual(m._about.update_check()["latest"], "v1.0.1")            # cached, not refetched
            e = m._about.update_check(force=True)
            self.assertEqual(e["latest"], "v1.0.1"); self.assertIn("offline", e["error"])  # kept, error noted
            st = m._about.update_status()
            for k in ("unit", "updating", "log"):
                self.assertIn(k, st)
            st_, txt = _http("/api/version")
            self.assertEqual(st_, 200)
            d = json.loads(txt)
            for k in ("installed", "latest", "available", "unit", "updating", "log"):
                self.assertIn(k, d)
        finally:
            m._about.VERSION_FILE, m._about._fetch_latest_release = old[0], old[1]
            m._about._update.clear(); m._about._update.update(old[2])
            if old[3] is not None:
                os.environ["UPDATE_CHECK"] = old[3]

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
