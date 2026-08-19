#!/usr/bin/env python3
"""End-to-End- und Unit-Tests fuer kAIm56 (Manager + openrouter-Agent).

Stdlib-only (unittest), passend zur Philosophie des Projekts — keine Test-
Dependency. Drei Stufen, je nach Umgebung:

  * OFFLINE  — Agent-/Manager-Funktionen direkt (Import), ohne VM, ohne Netz.
               Laufen IMMER.
  * HTTP     — gegen den laufenden Manager auf 127.0.0.1:8700. Werden
               uebersprungen, wenn der Manager nicht erreichbar ist.
  * LIVE     — Roundtrip zu einer laufenden Agent-VM (Orchestrator). Nur der
               kostenlose /goal-Pfad (kein Modellaufruf). Uebersprungen, wenn
               die Instanz nicht laeuft.

Aufruf:  python3 tests/e2e.py            (oder ./run-tests.sh)
Nur eine Stufe:  python3 tests/e2e.py AgentLogic
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

# --- Pfade zu den zu testenden Modulen --------------------------------------
FC_DIR = os.environ.get("FC_DIR", "/home/ulrich/firecracker")
AGENT_PATH = os.environ.get("AGENT_PATH", "/home/ulrich/openrouter-agent/agent.py")
MANAGER_PATH = os.path.join(FC_DIR, "manager.py")
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8700")

# manager.py importiert das Nachbarmodul `chatui` -> dessen Verzeichnis muss auf
# den Suchpfad, sonst schlaegt der Import in den Manager-Unit-Tests fehl.
if FC_DIR not in sys.path:
    sys.path.insert(0, FC_DIR)


def _load(name, path, env=None):
    """Ein Modul aus einer Datei laden; optional vorher os.environ setzen."""
    if env:
        os.environ.update(env)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _http(path, method="GET", body=None, timeout=8):
    """(status, text) gegen den Manager. Wirft bei Verbindungsfehler."""
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
        cls.a.report_usage = lambda *a, **k: None   # kein Netz zum Manager

    # --- Backend-Auswahl (Kernstueck orcarouter/llama/openrouter) -----------
    def _select(self, env):
        """Den Backend-Auswahlblock aus agent.py mit env ausfuehren und die
        entstehenden Variablen zurueckgeben (isoliert, ohne Re-Import)."""
        src = open(AGENT_PATH).read()
        block = src[src.index("LLAMA_ENDPOINT = os.environ"):src.index("WORKDIR = os.environ")]
        g = {"os": type("O", (), {"environ": dict(env)})(),
             "OR_URL": "https://openrouter.ai/api/v1/chat/completions",
             "OR_MODEL": env.get("OPENROUTER_MODEL", "openai/gpt-4o")}
        # Der Block ruft os.environ.get(...) -> wir brauchen ein echtes Mapping.
        g["os"].environ = dict(env)
        exec(block, g)
        return g

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
        self.assertIn("nicht gefunden", self.a.t_offload_read(id="gibtsnicht"))

    def test_offload_read_always_enabled(self):
        old = self.a._TOOL_ALLOW
        try:
            self.a._TOOL_ALLOW = {"bash"}          # strikte Allowlist
            self.assertTrue(self.a.tool_enabled("offload_read"))
            self.assertFalse(self.a.tool_enabled("http_fetch"))
        finally:
            self.a._TOOL_ALLOW = old

    # --- Tool-Hook / Guardrails ---------------------------------------------
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
        self.assertFalse(self.a.HITL)   # opt-in, sonst blockiert es nichts

    def test_notify_tool_registered(self):
        self.assertIn("notify", self.a.BUILTIN)

    # --- /model: Laufzeit-Modellwechsel --------------------------------------
    def test_model_switch(self):
        a = self.a
        old = (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET, a.LLM_BACKEND, a.OR_KEY)
        try:
            self.assertIn("Modell:", a._set_model("/model"))
            a._set_model("/model orcarouter:foo/bar")
            self.assertEqual(a.OR_MODEL, "foo/bar")
            self.assertIn("orcarouter.ai", a.OR_URL)
            self.assertEqual(a.LLM_KEY_SECRET, "ORCAROUTER_API_KEY")
            a._set_model("/model nur-modell-id")          # ohne Provider: nur Modell
            self.assertEqual(a.OR_MODEL, "nur-modell-id")
            self.assertIn("orcarouter.ai", a.OR_URL)       # Backend unveraendert
        finally:
            (a.OR_MODEL, a.OR_URL, a.LLM_NAME, a.LLM_KEY_SECRET, a.LLM_BACKEND, a.OR_KEY) = old

    # --- Steering -------------------------------------------------------------
    def test_steering_queue(self):
        a = self.a
        self.assertFalse(a.steer_push("x"))               # idle -> ablehnen
        a._busy[0] = True
        try:
            self.assertTrue(a.steer_push("kurs halten"))
            hist = []
            self.assertTrue(a._drain_steer(hist))
            self.assertEqual(hist[0]["role"], "user")
            self.assertIn("kurs halten", hist[0]["content"])
            self.assertIn("[Steuerung", hist[0]["content"])
            self.assertFalse(a._drain_steer(hist))         # Queue leer
        finally:
            a._busy[0] = False

    # --- Prompt-Templates -----------------------------------------------------
    def test_prompt_expansion(self):
        a = self.a
        a._prompts_cache["map"] = {"daily": "Erstelle das Tagesbriefing."}
        a._prompts_cache["ts"] = __import__("time").time()
        self.assertEqual(a._expand_prompt("/daily"), "Erstelle das Tagesbriefing.")
        self.assertEqual(a._expand_prompt("/daily nur kurz"),
                         "Erstelle das Tagesbriefing. nur kurz")
        self.assertEqual(a._expand_prompt("/reset"), "/reset")     # eingebaut hat Vorrang
        self.assertEqual(a._expand_prompt("/gibtsnicht"), "/gibtsnicht")
        self.assertEqual(a._expand_prompt("normaler text"), "normaler text")

    # --- Plugin-Loader ----------------------------------------------------------
    def test_plugin_loader(self):
        a = self.a
        tmp = tempfile.mkdtemp(prefix="e2e-plug-")
        with open(os.path.join(tmp, "echoplug.py"), "w") as fh:
            fh.write('DESC="Echo"\nPARAMS={"t":{"type":"string"}}\nREQUIRED=["t"]\n'
                     'def run(t):\n    return "ECHO:" + t\n')
        with open(os.path.join(tmp, "bash.py"), "w") as fh:      # Kollision -> ignorieren
            fh.write('DESC="boese"\ndef run():\n    return "nein"\n')
        old_dir = a.PLUGIN_DIR
        try:
            a.PLUGIN_DIR = tmp
            a.load_plugins()
            self.assertIn("echoplug", a.BUILTIN)
            self.assertIn("echoplug", a.PLUGIN_TOOLS)
            self.assertEqual(a.BUILTIN["echoplug"][0]("hi"), "ECHO:hi")
            self.assertNotIn("bash", a.PLUGIN_TOOLS)              # Kollision abgewehrt
        finally:
            a.PLUGIN_DIR = old_dir
            a.BUILTIN.pop("echoplug", None)
            a.PLUGIN_TOOLS.discard("echoplug")

    # --- Goal-Kommando ------------------------------------------------------
    def test_goal_set_show_off(self):
        try:
            self.assertIn("Kein Ziel", self.a._set_goal("/goal show"))
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


# ===========================================================================
# OFFLINE: Manager-Funktionen
# ===========================================================================
class ManagerFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load("manager_e2e", MANAGER_PATH)

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
        """':free'-Modellvarianten duerfen NICHT als Provider gelesen werden."""
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
        old_send = m.signal_send
        try:
            m.signal_send = lambda text, to=None: (True, "sent")
            hid = m.hitl_create("orchestrator", "bash", "rm foo")
            self.assertIsNotNone(hid)
            self.assertEqual(m.hitl_status(hid), "pending")
            self.assertTrue(m.hitl_resolve(hid, True))
            self.assertEqual(m.hitl_status(hid), "approved")
            self.assertFalse(m.hitl_resolve(hid, True))     # nicht doppelt aufloesbar
            self.assertEqual(m.hitl_status("unbekannt"), "unknown")
        finally:
            m.signal_send = old_send

    def test_hitl_no_signal_no_block(self):
        """Kann der Signal-Versand nicht (kein Empfaenger), gibt hitl_create None
        zurueck -> der Agent blockiert dann nicht."""
        m = self.m
        old_send = m.signal_send
        try:
            m.signal_send = lambda text, to=None: (False, "no recipient")
            self.assertIsNone(m.hitl_create("x", "bash", "y"))
        finally:
            m.signal_send = old_send

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

        old = m.katfs_proxy_fs
        try:
            m.katfs_proxy_fs = fake_proxy
            data, stats = m.katfs_zip("share1", ".")
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = sorted(zf.namelist())
            self.assertEqual(names, ["a.txt", "sub/b.txt"])
            self.assertEqual(zf.read("sub/b.txt"), b"BBB")
            self.assertEqual(stats["files"], 2)
        finally:
            m.katfs_proxy_fs = old

    def test_tool_catalog_matches_agent(self):
        """Drift-Wache: jedes Tool im Agenten (BUILTIN) muss im Manager-Katalog
        (AGENT_TOOLS_CATALOG) stehen — sonst fehlt es im Create-Formular und
        eine Tool-Allowlist blockiert es stumm (passiert bei mission_start und
        offload_read). Und umgekehrt: kein Katalog-Eintrag ohne echtes Tool."""
        m = self.m
        a = _load("agent_cat_e2e", AGENT_PATH,
                  {"CLAUDE_WORKDIR": tempfile.mkdtemp(prefix="e2e-cat-"),
                   "OPENROUTER_API_KEY": "dummy"})
        agent_tools = set(a.BUILTIN.keys())
        catalog = set(m.AGENT_TOOL_NAMES)
        missing_in_catalog = agent_tools - catalog
        self.assertFalse(missing_in_catalog,
                         f"Tools im Agenten, aber nicht im Manager-Katalog: {sorted(missing_in_catalog)}")
        ghost_in_catalog = catalog - agent_tools
        self.assertFalse(ghost_in_catalog,
                         f"Katalog-Eintraege ohne echtes Agenten-Tool: {sorted(ghost_in_catalog)}")

    def test_provider_model_key_covers_all(self):
        m = self.m
        for k in m.PROVIDER_MODEL_KEY.values():
            self.assertIn(k, m.MODEL_KEYS)

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
            # persist: zweiter Aufruf nutzt die vorhandene Datei (kein Reset)
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
            self.skipTest("keine Overlay-Instanz vorhanden")
        old_mk = m.make_upper
        try:
            m.make_upper = lambda i: "/tmp/fake-upper.ext4"   # kein echtes mkfs im Test
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
        old_file = m.MISSIONS_FILE
        old_notify = m.notify_add
        try:
            m.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            m.notify_add = lambda *a, **k: ("x", "ok")     # kein echter Push im Test
            mid, note = m.mission_start("orchestrator", "Testziel", ["s1", "s2"])
            self.assertTrue(mid)
            self.assertEqual(m.mission_start("orchestrator", "", [])[0], None)
            self.assertEqual(m.mission_update("orchestrator", mid, step=1,
                                              status="doing", task_id="t-1"), "ok")
            inst, mi, st = m.mission_for_task("t-1")
            self.assertEqual((inst, mi["id"], st["n"]), ("orchestrator", mid, 1))
            self.assertEqual(m.mission_update("orchestrator", mid, step=1,
                                              status="done", result="ok"), "ok")
            self.assertIsNone(m.mission_for_task("t-1")[1])   # done -> kein Trigger mehr
            self.assertEqual(m.mission_admin("orchestrator", mid, "pause"), "ok")
            self.assertEqual(m.mission_admin("orchestrator", mid, "resume"), "ok")
            self.assertEqual(m.mission_finish("orchestrator", mid, "fertig"), "ok")
            done = m.mission_list("orchestrator")[0]
            self.assertEqual(done["status"], "done")
            self.assertIn("cannot", m.mission_admin("orchestrator", mid, "abort"))
        finally:
            m.MISSIONS_FILE = old_file
            m.notify_add = old_notify

    def test_mission_caps(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-mi2-")
        old_file = m.MISSIONS_FILE
        try:
            m.MISSIONS_FILE = os.path.join(tmp, "missions.json")
            for i in range(m.MISSION_MAX_ACTIVE):
                self.assertTrue(m.mission_start("o", f"g{i}", ["s"])[0])
            self.assertIsNone(m.mission_start("o", "zuviel", ["s"])[0])
        finally:
            m.MISSIONS_FILE = old_file

    def test_usage_for_shape(self):
        m = self.m
        d = m.usage_for("orchestrator", 0)
        self.assertEqual(set(d.keys()), {"calls", "in", "out", "cost"})
        self.assertIsInstance(d["calls"], int)
        z = m.usage_for("gibtsnichtxyz", 0)     # unbekannte Instanz -> Nullen
        self.assertEqual(z["calls"], 0)

    def test_prompt_store(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-pr-")
        old = m.PROMPTS_FILE
        try:
            m.PROMPTS_FILE = os.path.join(tmp, "prompts.json")
            self.assertEqual(m.prompt_upsert("Daily!", "Text"), "saved")   # Name normalisiert
            self.assertEqual(m.load_prompts()[0]["name"], "daily")
            self.assertEqual(m.prompt_upsert("daily", "Neu"), "saved")     # Update
            self.assertEqual(m.load_prompts()[0]["text"], "Neu")
            self.assertIn("eingebaut", m.prompt_upsert("reset", "x"))      # reserviert
            self.assertEqual(m.prompt_delete("daily"), "deleted")
            self.assertEqual(m.prompt_delete("daily"), "unknown")
        finally:
            m.PROMPTS_FILE = old

    def test_notify_store(self):
        m = self.m
        tmp = tempfile.mkdtemp(prefix="e2e-notif-")
        old_file, old_sent = m.NOTIF_FILE, list(m._notif_sent)
        try:
            m.NOTIF_FILE = os.path.join(tmp, "notifications.json")
            m._notif_sent.clear()
            nid, note = m.notify_add("orchestrator", "Titel", "Text")
            self.assertTrue(nid)
            lst = m.load_notifications()
            self.assertEqual(len(lst), 1)
            self.assertEqual(lst[0]["title"], "Titel")
            self.assertFalse(lst[0]["read"])
            self.assertEqual(m.notif_mark_read(mark_all=True), 1)
            self.assertTrue(m.load_notifications()[0]["read"])
            self.assertIsNone(m.notify_add("x", "", "")[0])   # leer -> nichts
        finally:
            m.NOTIF_FILE = old_file
            m._notif_sent[:] = old_sent


# ===========================================================================
# HTTP: gegen den laufenden Manager
# ===========================================================================
@unittest.skipUnless(_manager_up(), "Manager auf 127.0.0.1:8700 nicht erreichbar")
class ManagerHTTP(unittest.TestCase):
    def test_root_page(self):
        st, _ = _http("/")
        self.assertEqual(st, 200)

    def test_agents_have_backend_and_model(self):
        st, txt = _http("/api/agents")
        self.assertEqual(st, 200)
        agents = json.loads(txt).get("agents", [])
        self.assertTrue(agents)
        known = {"openrouter", "orcarouter", "anthropic", "pi", "prime", "llama"}
        for a in agents:
            self.assertIn("backend", a, f"{a.get('name')} ohne backend-Feld")
            self.assertIn("model", a)
            self.assertIn(a["backend"], known, f"unbekanntes backend {a['backend']}")

    def test_orchestrator_reports_orcarouter(self):
        """Der zuletzt behobene Bug: orcarouter-Agent darf nicht als
        'openrouter, ohne Modell' erscheinen. Nur pruefen, WENN der
        Orchestrator per ORCAROUTER_MODEL laeuft."""
        st, txt = _http("/api/agents")
        orch = next((a for a in json.loads(txt)["agents"] if a["name"] == "orchestrator"), None)
        if not orch:
            self.skipTest("kein orchestrator")
        if orch["backend"] == "orcarouter":
            self.assertTrue(orch["model"], "orcarouter-Backend, aber leeres Modell (der alte Bug)")

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

    def test_notify_route_rejects_empty(self):
        # Leere Benachrichtigung -> 429, id null: Route existiert, ohne den
        # Live-Store zu verschmutzen (kein Ping aufs Geraet).
        st, txt = _http("/api/notify", "POST", {"title": "", "message": ""})
        self.assertEqual(st, 429)
        self.assertIsNone(json.loads(txt).get("id"))

    def test_orcarouter_template_registered(self):
        st, txt = _http("/")
        self.assertEqual(st, 200)
        self.assertIn("orcarouter", txt)


# ===========================================================================
# LIVE: Roundtrip zur Agent-VM (nur kostenloser /goal-Pfad)
# ===========================================================================
@unittest.skipUnless(_orchestrator_running(), "Orchestrator-VM laeuft nicht")
class LiveAgent(unittest.TestCase):
    @staticmethod
    def _reply(txt):
        # Der Manager-Proxy liefert die Antwort als Klartext; faellt auf JSON
        # {"reply": ...} zurueck, falls sich das je aendert.
        txt = txt.strip()
        if txt.startswith("{"):
            try:
                return json.loads(txt).get("reply", txt)
            except Exception:
                return txt
        return txt

    def test_goal_command_roundtrip(self):
        """Beweist, dass der neue Agent-Code in der VM lebt — ohne Modellaufruf,
        also ohne Token-Kosten."""
        st, txt = _http("/i/orchestrator/api/chat", "POST",
                        {"message": "/goal show"}, timeout=30)
        self.assertEqual(st, 200)
        self.assertIn("Ziel", self._reply(txt))

    def test_reasoning_command_roundtrip(self):
        st, txt = _http("/i/orchestrator/api/chat", "POST",
                        {"message": "/reasoning"}, timeout=30)
        self.assertEqual(st, 200)
        # /reasoning ohne Argument zeigt den Status -> irgendein Text kommt zurueck
        self.assertTrue(self._reply(txt))


if __name__ == "__main__":
    # Kurzer Umgebungs-Report, dann unittest.
    print(f"Manager erreichbar: {_manager_up()} | Orchestrator laeuft: {_orchestrator_running()}",
          file=sys.stderr)
    unittest.main(verbosity=2)
