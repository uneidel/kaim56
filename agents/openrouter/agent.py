#!/usr/bin/env python3
"""OpenRouter-Agent mit Tool-Calling — modell-agnostisch, laeuft in der microVM.

Tools: bash, read_file, write_file, list_dir, http_fetch  + optional MCP-Server
(stdio), zur Laufzeit vom Manager geholt. Transports: signal | web (via TRANSPORT). Stdlib only.
"""
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

# --- config -----------------------------------------------------------------
# Der Key steht bewusst NICHT mehr in der Instanz-Config (und damit nicht auf
# der Config-Disk der microVM). Env bleibt als Fallback fuer Altbestand; sonst
# wird er beim ersten Bedarf einmal beim Manager geholt — der erkennt den Gast
# an der Source-IP und prueft die Allowlist aus secret-policy.json.
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OR_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o")
OR_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

# Selbst gehostetes LLM via llama.cpp (OpenAI-kompatibel). Ist LLAMA_ENDPOINT
# gesetzt, spricht der Agent den lokalen Server statt OpenRouter an — gleicher
# Code, nur andere Basis-URL, Modellname und (optionaler) Key. Der Endpoint
# kommt aus den geteilten Settings ueber die Instanz-Config, der Key als Secret
# ueber den Broker (LLAMA_API_KEY, darf fehlen -> ohne Auth).
LLAMA_ENDPOINT = os.environ.get("LLAMA_ENDPOINT", "").strip()
# OrcaRouter: OpenAI-kompatibles Gateway wie OpenRouter, nur andere Basis-URL
# und ein sk-orca-Key. Gesetzt ist ORCAROUTER_MODEL (oder eine eigene URL beim
# Selbsthosten von OrcaRouter-Lite), spricht der Agent OrcaRouter statt
# OpenRouter an. Der Key kommt als Secret ueber den Broker (ORCAROUTER_API_KEY).
ORCA_URL = os.environ.get("ORCAROUTER_URL", "").strip()
ORCA_MODEL = os.environ.get("ORCAROUTER_MODEL", "").strip()


def _openai_chat_url(base):
    """Basis-URL auf den vollen /chat/completions-Pfad bringen — egal ob
    ".../v1", ".../v1/chat/completions" oder nackter "host:port" reinkommt."""
    u = base.rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


LLM_BACKEND = "openrouter"
LLM_NAME = "OpenRouter"
LLM_KEY_SECRET = "OPENROUTER_API_KEY"
if LLAMA_ENDPOINT:
    LLM_BACKEND = "llama"
    LLM_NAME = "llama.cpp"
    LLM_KEY_SECRET = "LLAMA_API_KEY"
    OR_URL = _openai_chat_url(LLAMA_ENDPOINT)
    OR_MODEL = os.environ.get("LLAMA_MODEL") or os.environ.get("OPENROUTER_MODEL") or "local-model"
elif ORCA_MODEL or ORCA_URL:
    LLM_BACKEND = "orcarouter"
    LLM_NAME = "OrcaRouter"
    LLM_KEY_SECRET = "ORCAROUTER_API_KEY"
    OR_URL = _openai_chat_url(ORCA_URL or "https://api.orcarouter.ai/v1")
    OR_MODEL = ORCA_MODEL or os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o"
WORKDIR = os.environ.get("CLAUDE_WORKDIR", "/home/node/workspace")
BASH_TIMEOUT = int(os.environ.get("BASH_TIMEOUT", "120"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))
MAX_TOOL_OUT = int(os.environ.get("MAX_TOOL_OUT", "8000"))
SYSTEM = os.environ.get("AGENT_SYSTEM",
    "Du bist ein hilfreicher Agent mit Tools (Shell, Dateien, Web, MCP). "
    "Arbeite im Verzeichnis %s. Nutze Tools wenn nötig, antworte sonst direkt. "
    "Fasse dich kurz." % WORKDIR)

# Laufzeit-Selbstauskunft: der Agent soll wissen, WORAUF er selbst laeuft, damit
# er auf "welches Modell nutzt du?" korrekt antwortet und nicht faelschlich das
# Template (list_agents zeigt fuers Routing ANDERE Agenten) heranzieht.
if os.environ.get("TASK_ADMIN"):   # Missions-Tools hat nur der Orchestrator
    SYSTEM += (
    "\n\nMissionen: Gibt dir der Nutzer einen MEHRSTUFIGEN Auftrag (mehrere "
        "Tasks/Tage), lege SOFORT mit mission_start eine Mission mit klaren "
        "Schritten an. Je Vorstoss: einen Schritt per create_task anstossen und "
        "die task-id mit mission_update am Schritt vermerken (status doing). Ist "
        "ein Task fertig, wirst du automatisch getriggert: Ergebnis pruefen, "
        "Schritt auf done/failed, naechsten Schritt anstossen. Alle Schritte "
        "fertig -> mission_finish mit Fazit. Blockiert -> notify an den Nutzer. "
        "Einfache Einzelauftraege bleiben normale Tasks OHNE Mission.")

SYSTEM += (f"\n\nLaufzeit: Du laeufst ueber {LLM_NAME} mit dem Modell "
           f"'{OR_MODEL}'. Fragt jemand nach deinem Modell/Backend, nenne genau "
           f"das — verwende dafuer NICHT list_agents (das listet andere Agenten "
           f"zum Delegieren, nicht dich).")

# Haengt an JEDEN Systemprompt, auch an Personas: die Gedaechtnis-Werkzeuge
# sind eingebaut, also gehoert die Anweisung dazu hierher — nicht in jede
# Persona einzeln, wo sie beim naechsten Bearbeiten verloren ginge.
SYSTEM += (
    "\n\nGedaechtnis: Innerhalb eines Gespraechs erinnerst du dich ganz normal "
    "an das bisher Gesagte — nutze das selbstverstaendlich und erklaere dem "
    "Nutzer NICHT ungefragt, wie dein Gedaechtnis funktioniert oder dass es sich "
    "zuruecksetzt. Ueber Gespraeche und Neustarts hinweg bleibt nur, was du "
    "bewusst ablegst: was kuenftige Gespraeche brauchen — Vorlieben des Nutzers, "
    "getroffene Entscheidungen, laufende Vorhaben, gelernte Eigenheiten der "
    "Umgebung — merkst du dir sofort und still mit memory_store. Der key ist "
    "kurz (zum Aktualisieren); der value ist eine VOLLSTAENDIGE, fuer sich "
    "verstaendliche Aussage (ganzer Satz), denn er wird spaeter nach Bedeutung "
    "wieder hervorgeholt — 'Ulrichs Lieblingsberg zum Wandern ist der Watzmann', "
    "nicht bloss 'Watzmann'. Bestehendes unter gleichem key aktualisieren. "
    "Kein Protokoll fuehren: fluechtige Details nicht speichern. Passende "
    "fruehere Notizen werden dir automatisch eingeblendet; memory_recall liefert "
    "bei Bedarf mehr.")

SYSTEM += (
    "\n\nPlaybooks (feste Regeln): Sagt dir der Nutzer, WIE etwas zu tun ist, "
    "nennt eine dauerhafte Vorliebe ('immer …', 'fuer X nutze Y') oder korrigiert "
    "deinen Ansatz, halte das SOFORT und still mit playbook_add als kurze, "
    "konkrete Regel fest — so waechst dein Wissen mit seinen Wuenschen. Die unter "
    "[Playbooks] eingeblendeten Regeln befolgst du immer. Mit playbooks zeigst du "
    "sie, mit playbook_forget entfernst du eine.")

# Verhaltensleitplanken, sinngemaess aus Anthropics veroeffentlichten
# System-Prompts uebernommen (das Modell-agnostische daran) — gilt fuer jedes
# Modell hinter diesem Agenten, auch fuer Personas.
SYSTEM += (
    "\n\nArbeitsweise: Erfinde nichts. Bist du nicht sicher, ob etwas stimmt "
    "oder noch aktuell ist, sag das offen und pruefe es mit web_search/"
    "http_fetch, statt zu raten; erfinde keine Quellen, Zitate oder Links. "
    "Bevor du behauptest, etwas nicht zu koennen oder keinen Zugriff zu haben, "
    "sieh nach, ob ein Werkzeug dafuer da ist, und nutze es — selbst handeln "
    "geht vor darum bitten. Bei unklaren Anfragen triff eine sinnvolle Annahme "
    "und leg los; frag nur zurueck, wenn es ohne die Angabe wirklich nicht "
    "geht. Eine begonnene Aufgabe fuehrst du zu Ende statt auf halbem Weg "
    "aufzuhoeren.\n"
    "Ton: sachlich, ohne Schmeichelei und ohne uebertriebene Entschuldigungen; "
    "widersprich freundlich und begruendet, wenn du anderer Meinung bist, statt "
    "nachzugeben. Lass leere Fuellwoerter wie 'ehrlich gesagt', 'wirklich' oder "
    "'tatsaechlich' weg — sag es einfach direkt. Antworte knapp und in "
    "Fliesstext; Listen, Fettung und Ueberschriften nur, wenn der Inhalt es "
    "wirklich erfordert oder du danach gefragt wirst; Vorbehalte kurz halten, "
    "der Hauptteil ist die Antwort. Ueber Absichten oder Gemuetszustand anderer "
    "spekulierst du nicht.")


# Reasoning/Thinking des Modells (OpenRouter reasoning-Parameter). None = aus.
# --- /model: Modell (und optional Backend) zur Laufzeit wechseln ------------
# Wie bei pi.dev: mitten in der Session hochschalten ("/model orcarouter:
# anthropic/claude-sonnet-4.6") und wieder zurueck — ohne Neustart, Kontext
# bleibt. Wirkt nur bis zum Neustart; dauerhaft bleibt die Instanz-Config.
_MODEL_BACKENDS = {
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions",
                   "OPENROUTER_API_KEY"),
    "orcarouter": ("OrcaRouter", "https://api.orcarouter.ai/v1/chat/completions",
                   "ORCAROUTER_API_KEY"),
}


def _set_model(cmd):
    global OR_MODEL, OR_URL, LLM_NAME, LLM_KEY_SECRET, LLM_BACKEND, OR_KEY
    rest = cmd[len("/model"):].strip()
    if not rest or rest in ("show", "status"):
        return f"🧠 Modell: {OR_MODEL} ueber {LLM_NAME} ({OR_URL})"
    if ":" in rest and rest.split(":", 1)[0] in _MODEL_BACKENDS:
        prov, mdl = rest.split(":", 1)
        name, url, secret = _MODEL_BACKENDS[prov]
        LLM_BACKEND, LLM_NAME, OR_URL, LLM_KEY_SECRET = prov, name, url, secret
        OR_KEY = ""                      # Key des neuen Backends beim Broker holen
        OR_MODEL = mdl.strip()
    else:
        OR_MODEL = rest
    return f"🧠 Modell jetzt: {OR_MODEL} ueber {LLM_NAME} (bis zum Neustart)"


def _set_steps(cmd):
    """/steps [n] — max. Tool-Schritte pro Turn zur Laufzeit aendern (bis zum
    Neustart; dauerhaft: AGENT_MAX_STEPS in der Instanz-Config). Lange
    Recherchen brauchen mehr als die Standard-12, sonst enden sie mit
    '(max. Tool-Schritte erreicht)'."""
    global MAX_STEPS
    rest = cmd[len("/steps"):].strip()
    if not rest:
        return f"🔢 max. Tool-Schritte je Turn: {MAX_STEPS}"
    try:
        MAX_STEPS = min(60, max(1, int(rest)))
    except ValueError:
        return "Nutzung: /steps [1-60]"
    return f"🔢 max. Tool-Schritte jetzt: {MAX_STEPS} (bis zum Neustart)"


# Default aus Env (OPENROUTER_REASONING), zur Laufzeit per /reasoning umschaltbar.
_reasoning = (os.environ.get("OPENROUTER_REASONING", "").strip().lower() or None)
if _reasoning not in (None, "low", "medium", "high"):
    _reasoning = None


# Marker fuer den Denk-/Reasoning-Block im Token-Strom. Sichtbare Unicode-
# Klammern: kommen in normalem Text praktisch nie vor und werden vom Security
# Gateway NICHT entfernt (kein Zero-Width/Tag-Zeichen). Web und App klappen den
# Bereich zwischen den Markern als "Denken" ein.
THINK_START = "\u27E6think\u27E7"
THINK_END = "\u27E6/think\u27E7"


def _set_reasoning(cmd):
    """/reasoning [off|low|medium|high] — ohne Argument umschalten (aus <-> medium)."""
    global _reasoning
    arg = cmd[len("/reasoning"):].strip().lower()
    if arg in ("off", "aus", "0", "none", "false"):
        _reasoning = None
    elif arg in ("low", "medium", "high"):
        _reasoning = arg
    elif arg == "":
        _reasoning = None if _reasoning else "medium"
    else:
        return "Nutzung: /reasoning [off|low|medium|high]"
    return f"🧠 Reasoning {'aus' if _reasoning is None else 'an (' + _reasoning + ')'}."


def log(*a):
    import time
    print(time.strftime("%F %T"), *a, flush=True)


# --- built-in tools ---------------------------------------------------------
def t_bash(command):
    p = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                       text=True, timeout=BASH_TIMEOUT)
    return (p.stdout + p.stderr).strip() or f"(exit {p.returncode}, keine Ausgabe)"


def _safe(path):
    p = os.path.abspath(os.path.join(WORKDIR, path)) if not os.path.isabs(path) else path
    return p


def t_read_file(path):
    with open(_safe(path)) as f:
        return f.read(MAX_TOOL_OUT)


def t_write_file(path, content):
    p = _safe(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    return f"geschrieben: {p} ({len(content)} zeichen)"


def t_list_dir(path="."):
    return "\n".join(sorted(os.listdir(_safe(path)))) or "(leer)"


def t_http_fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "or-agent"})
    r = urllib.request.urlopen(req, timeout=30)
    return r.read(MAX_TOOL_OUT).decode("utf-8", "replace")


def t_read_pdf(path, pages=""):
    """PDF-Text extrahieren via pdftotext. `path` = Workspace-Datei ODER
    http(s)-URL. `pages` optional als Bereich, z. B. '1-5'."""
    import re
    import tempfile
    tmp = None
    try:
        if str(path).startswith(("http://", "https://")):
            data = urllib.request.urlopen(
                urllib.request.Request(path, headers={"User-Agent": "or-agent"}),
                timeout=30).read(50 * 1024 * 1024)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(data)
            tmp.close()
            src = tmp.name
        else:
            src = _safe(path)
        cmd = ["pdftotext", "-q"]
        m = re.match(r"(\d+)-(\d+)$", (pages or "").strip())
        if m:
            cmd += ["-f", m.group(1), "-l", m.group(2)]
        cmd += [src, "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        txt = (r.stdout or "").strip()
        if not txt:
            return ("PDF-Fehler: " + (r.stderr.strip()[:300] or "")
                    if r.returncode != 0
                    else "(kein Text im PDF — evtl. gescanntes Bild ohne Textebene)")
        return txt[:MAX_TOOL_OUT]
    except Exception as e:
        return f"Fehler: {e!r}"
    finally:
        if tmp:
            try:
                os.remove(tmp.name)
            except OSError:
                pass


def t_web_search(query, count=5):
    """Websuche via DuckDuckGo-HTML (kein API-Key). Liefert Titel + URL + Snippet."""
    import urllib.parse
    import re
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    q = urllib.parse.quote(query)
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/?q=" + q,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"})
    html = urllib.request.urlopen(req, timeout=30).read(1_000_000).decode("utf-8", "replace")
    hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)

    def clean(s):
        return re.sub(r"<[^>]+>", "", s).replace("&amp;", "&").replace("&#x27;", "'").strip()

    def real(h):
        m = re.search(r"uddg=([^&]+)", h)
        return urllib.parse.unquote(m.group(1)) if m else h

    out = []
    for i in range(min(count, len(hrefs))):
        t = clean(titles[i]) if i < len(titles) else ""
        s = clean(snips[i]) if i < len(snips) else ""
        out.append(f"{i+1}. {t}\n   {real(hrefs[i])}\n   {s}")
    return "\n".join(out) or "keine Ergebnisse"


def _manager_base():
    """Manager-URL vom Gast aus: Host-Gateway (.1 des /30) auf Port 8700."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return f"http://{ip.rsplit('.', 1)[0]}.1:8700"


def _mgr(base, path, payload=None, timeout=60):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def ensure_or_key():
    """LLM-Key beschaffen und im Speicher halten. Bei llama.cpp ist der Key
    optional — fehlt er, laeuft der Agent ohne Auth (leerer Bearer), das ist
    fuer einen Server ohne --api-key der Normalfall und kein Fehler."""
    global OR_KEY
    if OR_KEY:
        return OR_KEY
    try:
        d = json.loads(_mgr_get(_manager_base(), f"/api/secret/{LLM_KEY_SECRET}"))
        OR_KEY = d.get("value", "") or ""
        if not OR_KEY and LLM_BACKEND != "llama":
            print(f"{LLM_KEY_SECRET}: {d.get('error', 'vom Broker nicht freigegeben')}",
                  flush=True)
    except Exception as e:
        if LLM_BACKEND != "llama":
            print(f"{LLM_KEY_SECRET} nicht vom Manager zu bekommen: {e!r}", flush=True)
    return OR_KEY


def t_spawn_subagent(task, model=None):
    """Erstellt eine neue ephemere Agenten-Instanz, delegiert die Aufgabe,
    liefert das Ergebnis und löscht die Instanz danach wieder."""
    base = _manager_base()
    name = "sub-" + uuid.uuid4().hex[:6]
    cfg = {"TRANSPORT": "web", "NO_SPAWN": "1"}
    if model:
        cfg["OPENROUTER_MODEL"] = model
    try:
        _mgr(base, "/api/create", {"name": name, "template": "openrouter", "config": cfg})
        _mgr(base, f"/api/instances/{name}/start")
    except Exception as e:
        return f"Subagent-Start fehlgeschlagen: {e!r}"
    reply = None
    try:
        for _ in range(120):  # ~2 min, 1s-Granularitaet
            time.sleep(1)
            try:
                body = _mgr(base, f"/i/{name}/api/chat", {"message": task}, timeout=180)
                try:
                    reply = json.loads(body).get("reply", body)
                except ValueError:
                    reply = body
                break
            except Exception:
                continue
    finally:
        try:
            _mgr(base, f"/api/instances/{name}/stop")
            _mgr(base, f"/api/instances/{name}/delete")
        except Exception:
            pass
    return reply or "(Subagent lieferte kein Ergebnis)"


def t_create_task(task, target="ephemeral", schedule="", wait=False):
    """Eine Aufgabe zur Ausfuehrung einreihen — auf einer FAEHIGEN Instanz oder
    isoliert in einer ephemeren VM. Der Manager fuehrt sie aus; das Ergebnis
    erscheint in der gemeinsamen Chat-Historie (App/Web)."""
    payload = {"message": task, "target": (target or "ephemeral").strip(),
               "schedule": (schedule or "").strip(), "wait": bool(wait)}
    try:
        body = _mgr(_manager_base(), "/api/task", payload,
                    timeout=630 if wait else 30)
        d = json.loads(body)
        if d.get("error"):
            return f"⚠️ {d['error']}"
        if "result" in d:                      # wait=True -> Ergebnis direkt
            return str(d["result"])
        return (f"Aufgabe eingereiht (id {d.get('id')}, target {d.get('target')}, "
                f"{d.get('status')}). Ergebnis erscheint im Chat.")
    except Exception as e:
        return f"Fehler: {e!r}"


def t_mission_start(goal, steps):
    """Mehrstufigen Auftrag als Mission anlegen: Ziel + geplante Schritte.
    Der Fortschritt liegt im Manager und ueberlebt Neustart/Reset."""
    if isinstance(steps, str):
        steps = [x.strip() for x in steps.split("\n") if x.strip()]
    try:
        d = json.loads(_mgr(_manager_base(), "/api/mission-start",
                            {"goal": goal, "steps": steps}, timeout=10))
        return f"Mission {d['id']} angelegt." if d.get("id") else f"Nicht angelegt: {d.get('note','')}"
    except Exception as e:
        return f"Fehler: {e!r}"


def t_missions():
    """Aktive/pausierte Missionen mit Schritten und Status auflisten."""
    try:
        ms = json.loads(_mgr_get(_manager_base(), "/api/missions", timeout=8)).get("missions", [])
        if not ms:
            return "keine Missionen"
        out = []
        for m in ms:
            if m.get("status") in ("done", "failed"):
                continue
            steps = " | ".join(f"{st['n']}[{st['status']}] {st['text'][:60]}"
                               + (f" (task {st['task_id']})" if st.get("task_id") else "")
                               for st in m.get("steps", []))
            out.append(f"{m['id']} [{m['status']}] {m['goal'][:80]} :: {steps}")
        return "\n".join(out) or "keine offenen Missionen"
    except Exception as e:
        return f"Fehler: {e!r}"


def t_mission_update(id, step=None, status="", result="", task_id="", add_step="", note=""):
    """Missionsschritt fortschreiben: status open|doing|done|failed, result kurz,
    task_id des angestossenen Tasks vermerken; add_step haengt einen neuen
    Schritt an; note schreibt nur ins Log."""
    try:
        body = {"id": id, "status": status, "result": result,
                "task_id": task_id, "add_step": add_step, "note": note}
        if step is not None:
            body["step"] = int(step)
        d = json.loads(_mgr(_manager_base(), "/api/mission-update", body, timeout=10))
        return d.get("msg", "?")
    except Exception as e:
        return f"Fehler: {e!r}"


def t_mission_finish(id, summary, failed=False):
    """Mission abschliessen (oder mit failed=true als gescheitert beenden).
    Fazit wandert ins Langzeitgedaechtnis, der Nutzer bekommt eine Notification."""
    try:
        d = json.loads(_mgr(_manager_base(), "/api/mission-finish",
                            {"id": id, "summary": summary, "failed": bool(failed)}, timeout=10))
        return d.get("msg", "?")
    except Exception as e:
        return f"Fehler: {e!r}"


ORACLE_MODEL = os.environ.get("ORACLE_MODEL", "").strip()   # leer = aktuelles Modell
ORACLE_PROMPT = (
    "Du bist ein skeptischer Berater (Oracle): Zweitmeinung VOR einer Handlung. "
    "Du handelst NIE selbst. Hinterfrage die Annahmen: Passt die Aktion zum "
    "eigentlichen Auftrag? Ist das Ziel eindeutig identifiziert (ID + Inhalt, "
    "nicht nur Uhrzeit/Name)? Was waere der Schaden, wenn die Annahme falsch "
    "ist? Antworte knapp: erst 'EINWAND:' mit dem staerksten Gegenargument "
    "(oder 'KEIN EINWAND'), dann max. 3 Zeilen Begruendung/Empfehlung.")


def t_oracle(plan, kontext=""):
    """Zweitmeinung vor einer Handlung (pi.dev-Idee 'oracle'): challenge der
    Annahmen, ohne selbst zu handeln. Extra LLM-Aufruf ohne Tools; via
    ORACLE_MODEL optional ein staerkeres Modell."""
    msgs = [{"role": "system", "content": ORACLE_PROMPT},
            {"role": "user", "content": f"GEPLANTE AKTION:\n{plan}\n\nKONTEXT:\n{kontext or '(keiner)'}"}]
    r = or_chat(msgs, [], model=ORACLE_MODEL or None)
    return (r.get("content") or "").strip() or "(Oracle ohne Antwort — im Zweifel NICHT handeln)"


def t_notify(title, message=""):
    """Eine Push-Benachrichtigung an die Geraete des Nutzers schicken (App als
    Android-Systemnotification, Web-Manager als Glocke). Fuer wichtige
    Ereignisse/Ergebnisse, wenn der Nutzer nicht im Chat sitzt. Anders als
    send_signal (klingelt in Signal) ist das der App/Web-Kanal. Der Versand
    laeuft ueber den Manager."""
    try:
        body = _mgr(_manager_base(), "/api/notify",
                    {"title": title, "message": message}, timeout=15)
        d = json.loads(body)
        return "Benachrichtigung gesendet." if d.get("id") else \
            "⚠️ nicht gesendet: " + str(d.get("note", ""))
    except urllib.error.HTTPError as e:
        try:
            return "⚠️ nicht gesendet: " + str(json.loads(e.read()).get("note", e.code))
        except Exception:
            return f"⚠️ nicht gesendet (HTTP {e.code})"
    except Exception as e:
        return f"⚠️ Fehler: {e!r}"


def t_send_signal(text, to=""):
    """Dem Nutzer per Signal schreiben. Der Versand laeuft im Manager: die
    Bot-Nummer und der API-Zugang liegen dort, und der Empfaenger wird gegen
    die Liste der erlaubten Nummern geprueft. Von hier aus laesst sich also
    nicht an beliebige Nummern schreiben — mit Absicht."""
    try:
        body = _mgr(_manager_base(), "/api/signal",
                    {"text": text, "to": (to or "").strip()}, timeout=45)
        d = json.loads(body)
        return ("Signal gesendet: " if d.get("ok") else "⚠️ nicht gesendet: ") + str(d.get("note", ""))
    except urllib.error.HTTPError as e:
        try:
            return "⚠️ nicht gesendet: " + str(json.loads(e.read()).get("note", e.code))
        except Exception:
            return f"⚠️ nicht gesendet: HTTP {e.code}"
    except Exception as e:
        return f"Fehler: {e!r}"


def t_read_inbox(peek=False):
    """Neue Nutzer-Nachrichten (Signal/App/Web) seit dem letzten Lauf lesen —
    der Posteingang des Orchestrators. Standardmaessig wird jede Nachricht nur
    EINMAL geliefert (Wasserzeichen). peek=True liefert, ohne zu 'verbrauchen'."""
    try:
        body = _mgr_get(_manager_base(), "/api/inbox" + ("?peek=1" if peek else ""))
        msgs = json.loads(body).get("messages", [])
        if not msgs:
            return "Posteingang leer (nichts Neues)"
        out = []
        for m in msgs:
            who = m.get("instance") or m.get("title") or "?"
            out.append(f"[{who}] {str(m.get('text',''))[:200]}")
        return "\n".join(out)
    except Exception as e:
        return f"Fehler: {e!r}"


def t_list_agents():
    """Verfuegbare Agenten-Instanzen + Faehigkeiten (Modell, MCP) auflisten —
    fuers Routing: waehle als create_task-target den Agenten, der die noetigen
    Tools/MCP hat (z. B. den mit homeassistant-MCP fuer Licht/Heizung)."""
    try:
        rows = json.loads(_mgr_get(_manager_base(), "/api/agents")).get("agents", [])
        if not rows:
            return "keine Agenten"
        out = []
        for a in rows:
            mcp = (" mcp:" + ",".join(a["mcps"])) if a.get("mcps") else ""
            st = "läuft" if a.get("running") else "aus"
            out.append(f"{a['name']} [{st}] {a.get('backend') or a.get('template','')} {a.get('model','')}{mcp}")
        return "\n".join(out)
    except Exception as e:
        return f"Fehler: {e!r}"


def t_recall_tasks(query="", limit=10):
    """Frueher ausgefuehrte Aufgaben abfragen (Langzeitgedaechtnis / Stammwissen).
    Ohne query die letzten; mit query nach Text in Aufgabe/Ergebnis/Ziel suchen.
    Nutze das VOR dem Anlegen neuer Tasks, um Dubletten zu vermeiden."""
    try:
        q = urllib.parse.quote(query or "")
        body = _mgr_get(_manager_base(), f"/api/history?q={q}&limit={int(limit)}")
        rows = json.loads(body).get("rows", [])
        if not rows:
            return "keine passenden frueheren Aufgaben"
        out = []
        for r in rows:
            ts = time.strftime("%m-%d %H:%M", time.localtime(r.get("ts", 0)))
            ok = "" if r.get("ok") else "⚠️ "
            out.append(f"[{ts}] {ok}{r.get('target')}: {str(r.get('task',''))[:80]}"
                       f" -> {str(r.get('result','') or '')[:140]}")
        return "\n".join(out)
    except Exception as e:
        return f"Fehler: {e!r}"


def t_list_tasks():
    """Laufende/geplante Aufgaben mit IDs auflisten — noetig, um eine gezielt
    mit delete_task zu entfernen. (recall_tasks liefert dagegen die History
    erledigter Laeufe, nicht die aktiven mit ihren IDs.)"""
    try:
        body = _mgr_get(_manager_base(), "/api/tasks-open")
        tasks = json.loads(body).get("tasks", [])
        if not tasks:
            return "keine laufenden Aufgaben"
        out = []
        for t in tasks:
            sch = f" [{t['schedule']}]" if t.get("schedule") else ""
            out.append(f"{t.get('id')} @{t.get('instance')} ({t.get('status')}){sch}: "
                       f"{str(t.get('message',''))[:80]}")
        return "\n".join(out)
    except Exception as e:
        return f"Fehler: {e!r}"


def t_delete_task(id):
    """Eine laufende/geplante Aufgabe per ID entfernen. Die ID kommt aus
    list_tasks. Endgueltig; eine gerade laufende Aufgabe bricht das nicht ab,
    verhindert aber kuenftige Laeufe."""
    try:
        body = _mgr(_manager_base(), "/api/task-delete", {"id": str(id)})
        d = json.loads(body)
        return (f"Aufgabe {id} geloescht." if d.get("deleted")
                else f"Keine Aufgabe mit ID {id} gefunden.")
    except Exception as e:
        return f"Fehler: {e!r}"


def t_edit_task(id, message="", schedule=""):
    """Nachricht und/oder Zeitplan einer Aufgabe aendern (ID aus list_tasks).
    schedule z. B. 'every 2h', 'daily 08:00', 'hourly'; leerer schedule macht
    aus einer Wiederholung eine einmalige Aufgabe. Leere Felder bleiben
    unveraendert. Eine gerade LAUFENDE Aufgabe laesst sich nicht aendern."""
    try:
        payload = {"id": str(id)}
        if message:
            payload["message"] = message
        if schedule is not None:
            payload["schedule"] = schedule
        body = _mgr(_manager_base(), "/api/task-edit", payload)
        return str(json.loads(body).get("result", body))
    except Exception as e:
        return f"Fehler: {e!r}"


def _mgr_get(base, path, timeout=30):
    req = urllib.request.Request(base + path, method="GET")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def t_list_skills():
    """Verfügbare Experten-Skills (Wissens-Dokumente) auflisten."""
    try:
        arr = json.loads(_mgr_get(_manager_base(), "/api/skills"))
    except Exception as e:
        return f"Fehler: {e!r}"
    if not arr:
        return "Keine Skills verfügbar."
    return "\n".join(f"- {s.get('name')}: {s.get('description', '')}" for s in arr)


def t_load_skill(name):
    """Einen Skill in den Kontext laden (liefert das Wissens-Dokument)."""
    try:
        return _mgr_get(_manager_base(), f"/api/skills/{name}")
    except Exception as e:
        return f"Fehler: {e!r}"


def t_memory_store(key, value):
    """Wert dauerhaft merken (zentral im Manager, überlebt Instanz-Löschung)."""
    inst = os.environ.get("FC_INSTANCE", "default")
    try:
        return _mgr(_manager_base(), f"/api/memory/{inst}", {"key": key, "value": value})
    except Exception as e:
        return f"Fehler: {e!r}"


def t_memory_recall(key=None):
    """Gemerkten Wert abrufen (ohne key: alle Einträge dieser Instanz)."""
    inst = os.environ.get("FC_INSTANCE", "default")
    try:
        return _mgr_get(_manager_base(), f"/api/memory/{inst}" + (f"/{key}" if key else ""))
    except Exception as e:
        return f"Fehler: {e!r}"


def t_playbook_add(rule):
    """Eine dauerhafte Regel/ein Vorgehen festhalten (Playbook). Wird kuenftig
    IMMER eingeblendet und befolgt."""
    try:
        d = json.loads(_mgr(_manager_base(), "/api/playbook-add", {"text": rule}))
        if d.get("added"):
            return "Regel gemerkt."
        return "Regel gibt es schon." if d.get("note") == "exists" else "Nicht gemerkt."
    except Exception as e:
        return f"Fehler: {e!r}"


def t_playbooks():
    """Alle festen Regeln (Playbooks) mit IDs anzeigen."""
    try:
        pbs = json.loads(_mgr_get(_manager_base(), "/api/playbooks")).get("playbooks", [])
        if not pbs:
            return "keine Playbooks"
        return "\n".join(f"{p['id']}: {p['text']}" for p in pbs)
    except Exception as e:
        return f"Fehler: {e!r}"


def t_playbook_forget(id):
    """Eine Regel per ID entfernen (ID aus playbooks)."""
    try:
        d = json.loads(_mgr(_manager_base(), "/api/playbook-remove", {"id": str(id)}))
        return f"Regel {id} entfernt." if d.get("removed") else f"Keine Regel {id}."
    except Exception as e:
        return f"Fehler: {e!r}"


def t_list_secrets():
    """Zeigt, welche Secrets dieser Agent laut Allowlist abrufen darf (nur Namen)."""
    try:
        d = json.loads(_mgr_get(_manager_base(), "/api/secrets"))
        ks = d.get("allowed", [])
        return "Erlaubte Secrets: " + (", ".join(ks) if ks else "(keine)")
    except Exception as e:
        return f"Fehler: {e!r}"


def t_get_secret(name):
    """Holt ein erlaubtes Secret vom Manager (nur bei Bedarf; nicht loggen/weitergeben)."""
    try:
        d = json.loads(_mgr_get(_manager_base(), f"/api/secret/{name}"))
        return d.get("value", "") if "value" in d else f"⚠️ {d.get('error', 'nicht erlaubt')}"
    except urllib.error.HTTPError as e:
        return "⚠️ nicht erlaubt" if e.code == 403 else f"Fehler: HTTP {e.code}"
    except Exception as e:
        return f"Fehler: {e!r}"


def t_remote_ls(path="."):
    """Freigegebenes Remote-Verzeichnis auflisten (P2P-Browser-Freigabe)."""
    # Nicht mehr direkt an den katfs-Knoten (der ist seit dem Isolations-Fix
    # loopback-only), sondern ueber den Broker im Manager. Der erkennt die
    # Instanz an der Source-IP und adressiert NUR ihre zugewiesene Freigabe —
    # eine fremde kann der Agent nicht mehr ansprechen.
    try:
        return _mgr_get(_manager_base(), f"/api/katfs/ls?path={urllib.parse.quote(path)}")
    except Exception as e:
        return f"Fehler (Freigabe aktiv?): {e!r}"


def t_remote_read(path):
    """Datei aus dem freigegebenen Remote-Verzeichnis lesen."""
    try:
        return _mgr_get(_manager_base(),
                        f"/api/katfs/read?path={urllib.parse.quote(path)}", timeout=60)
    except Exception as e:
        return f"Fehler: {e!r}"


def _katfs_post(url, data=b""):
    """POST an den katfs-Knoten. Bei HTTP-Fehlern den Body mitnehmen — dort steht
    der eigentliche Grund ({"error": ...}); ohne ihn bleibt nur ein nacktes
    'Internal Server Error', mit dem weder Modell noch Mensch etwas anfangen."""
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return f"Fehler HTTP {e.code}: {body or e.reason}"
    except Exception as e:
        return f"Fehler: {e!r}"


def t_remote_write(path, content):
    """Datei ins freigegebene Remote-Verzeichnis schreiben."""
    return _katfs_post(
        _manager_base() + f"/api/katfs/write?path={urllib.parse.quote(path)}",
        (content or "").encode())


def t_remote_delete(path, recursive=False):
    """Datei/Ordner aus dem freigegebenen Remote-Verzeichnis loeschen."""
    q = f"/api/katfs/delete?path={urllib.parse.quote(path)}"
    if recursive:
        q += "&recursive=1"
    return _katfs_post(_manager_base() + q)


BUILTIN = {
    "bash": (t_bash, "Shell-Befehl im Workspace ausführen",
             {"command": {"type": "string", "description": "Befehl"}}, ["command"]),
    "read_file": (t_read_file, "Datei lesen",
                  {"path": {"type": "string"}}, ["path"]),
    "write_file": (t_write_file, "Datei schreiben",
                   {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "list_dir": (t_list_dir, "Verzeichnis auflisten",
                 {"path": {"type": "string"}}, []),
    "http_fetch": (t_http_fetch, "URL abrufen (HTTP)",
                   {"url": {"type": "string"}, "method": {"type": "string"}}, ["url"]),
    "read_pdf": (t_read_pdf, "Text aus einem PDF extrahieren — path ist eine Workspace-Datei ODER eine http(s)-URL; pages optional als Bereich (z. B. '1-5').",
                 {"path": {"type": "string", "description": "Datei im Workspace oder http(s)-URL"},
                  "pages": {"type": "string", "description": "optionaler Seitenbereich, z. B. '1-5'"}}, ["path"]),
    "web_search": (t_web_search, "Im Web suchen (DuckDuckGo) – liefert Titel, URL und Snippet; danach ggf. http_fetch zum Lesen der Seite",
                   {"query": {"type": "string", "description": "Suchbegriff"},
                    "count": {"type": "integer", "description": "Anzahl Treffer (Standard 5)"}}, ["query"]),
    "spawn_subagent": (t_spawn_subagent,
                       "Ephemeren Subagenten (neue Instanz) starten, Teilaufgabe delegieren, Ergebnis holen; Instanz wird danach automatisch gelöscht. Für parallele/abgegrenzte Teilaufgaben.",
                       {"task": {"type": "string", "description": "Aufgabe für den Subagenten"},
                        "model": {"type": "string", "description": "optionales OpenRouter-Modell"}}, ["task"]),
    "create_task": (t_create_task,
                    "Eine Aufgabe einreihen — WICHTIG: waehle target nach Faehigkeit. "
                    "Braucht die Aufgabe einen bestimmten MCP/Token (z. B. Home Assistant), "
                    "nimm die passende Instanz als target (z. B. 'hass'). Fuer allgemeine/"
                    "isolierte Arbeit 'ephemeral' (frische VM, danach geloescht). schedule "
                    "optional ('every 2h','daily 08:00','hourly'). wait=true wartet auf das "
                    "Ergebnis, sonst laeuft es im Hintergrund und erscheint im Chat.",
                    {"task": {"type": "string", "description": "Was getan werden soll"},
                     "target": {"type": "string", "description": "Instanzname (faehig) oder 'ephemeral'"},
                     "schedule": {"type": "string", "description": "optional: every Nm|Nh|Nd, daily HH:MM, hourly"},
                     "wait": {"type": "boolean", "description": "auf Ergebnis warten (Standard false)"}}, ["task"]),
    "mission_start": (t_mission_start,
                      "Mehrstufigen Auftrag als Mission anlegen (Ziel + Schritte). Fuer alles, "
                      "was mehrere Tasks/Tage braucht — der Fortschritt ueberlebt Neustarts.",
                      {"goal": {"type": "string", "description": "Ziel der Mission"},
                       "steps": {"type": "array", "items": {"type": "string"},
                                 "description": "geplante Schritte in Reihenfolge"}},
                      ["goal", "steps"]),
    "missions": (t_missions, "Offene Missionen mit Schritten/Status auflisten.", {}, []),
    "mission_update": (t_mission_update,
                       "Missionsschritt fortschreiben: status setzen (doing/done/failed), "
                       "Ergebnis + task_id des angestossenen Tasks vermerken, add_step haengt "
                       "einen Schritt an.",
                       {"id": {"type": "string", "description": "Mission-ID"},
                        "step": {"type": "integer", "description": "Schrittnummer"},
                        "status": {"type": "string", "description": "open|doing|done|failed"},
                        "result": {"type": "string", "description": "kurzes Ergebnis"},
                        "task_id": {"type": "string", "description": "ID des create_task-Tasks"},
                        "add_step": {"type": "string", "description": "neuen Schritt anhaengen"},
                        "note": {"type": "string", "description": "nur Log-Notiz"}}, ["id"]),
    "mission_finish": (t_mission_finish,
                       "Mission abschliessen; failed=true bei Scheitern. Kurzes Fazit angeben.",
                       {"id": {"type": "string"}, "summary": {"type": "string"},
                        "failed": {"type": "boolean"}}, ["id", "summary"]),
    "oracle": (t_oracle,
               "Zweitmeinung VOR einer riskanten/irreversiblen Aktion: challenged deine "
               "Annahmen, handelt nie selbst. plan = was du vorhast und warum; kontext = "
               "relevante Fakten (IDs, Wortlaute, Nutzerauftrag). Bei 'EINWAND' nicht "
               "handeln, sondern aufloesen oder rueckfragen.",
               {"plan": {"type": "string", "description": "geplante Aktion + Begruendung"},
                "kontext": {"type": "string", "description": "Fakten: IDs, Wortlaute, Auftrag"}},
               ["plan"]),
    "notify": (t_notify,
               "Push-Benachrichtigung an die Geraete des Nutzers (App-Systemnotification + "
               "Web-Manager-Glocke). Fuer wichtige Ereignisse/Ergebnisse, wenn er nicht im "
               "Chat sitzt. Anders als send_signal ist das der App/Web-Kanal, klingelt nicht "
               "in Signal.",
               {"title": {"type": "string", "description": "Kurzer Titel"},
                "message": {"type": "string", "description": "Text der Benachrichtigung"}},
               ["title"]),
    "send_signal": (t_send_signal,
                    "Dem Nutzer eine Signal-Nachricht schicken — fuer Ergebnisse, Funde "
                    "oder Rueckfragen, wenn er gerade nicht im Chat sitzt. NICHT fuer die "
                    "normale Antwort im laufenden Gespraech verwenden (die kommt ohnehin "
                    "an) und nicht ungefragt wiederholt: eine Nachricht klingelt auf einem "
                    "Telefon. Empfaenger nur aus der erlaubten Liste; 'to' leer lassen "
                    "heisst: an den Standardempfaenger.",
                    {"text": {"type": "string", "description": "Nachrichtentext"},
                     "to": {"type": "string", "description": "optional: Nummer im Format +49…"}},
                    ["text"]),
    "read_inbox": (t_read_inbox,
                  "Neue Nutzer-Nachrichten (Signal/App/Web) seit dem letzten Lauf lesen — "
                  "Posteingang des Orchestrators. Jede Nachricht kommt nur einmal (Wasserzeichen); "
                  "peek=true zum Vorschauen ohne Verbrauch.",
                  {"peek": {"type": "boolean", "description": "nur ansehen, nicht verbrauchen"}}, []),
    "list_agents": (t_list_agents,
                    "Verfuegbare Agenten-Instanzen + Faehigkeiten (Modell/MCP) auflisten. "
                    "Fuer Routing: create_task-target nach Faehigkeit waehlen.",
                    {}, []),
    "recall_tasks": (t_recall_tasks,
                     "Frueher ausgefuehrte Aufgaben + Ergebnisse abfragen (Langzeitgedaechtnis). "
                     "Ohne query die letzten, mit query gezielt suchen. VOR create_task nutzen, "
                     "um zu pruefen, ob etwas schon erledigt/geplant ist (keine Dubletten).",
                     {"query": {"type": "string", "description": "Suchbegriff (leer = letzte)"},
                      "limit": {"type": "integer", "description": "max. Treffer (Standard 10)"}}, []),
    "list_tasks": (t_list_tasks,
                   "LAUFENDE/geplante Aufgaben mit IDs auflisten — zum gezielten Loeschen. "
                   "(recall_tasks ist dagegen die History erledigter Laeufe.)", {}, []),
    "delete_task": (t_delete_task,
                    "Eine laufende/geplante Aufgabe per ID loeschen. Die ID zuerst mit "
                    "list_tasks holen. Endgueltig.",
                    {"id": {"type": "string", "description": "Task-ID aus list_tasks"}}, ["id"]),
    "edit_task": (t_edit_task,
                  "Nachricht und/oder Zeitplan einer Aufgabe aendern (ID aus list_tasks). "
                  "schedule z. B. 'every 2h', 'daily 08:00', 'hourly'; leer = einmalig.",
                  {"id": {"type": "string", "description": "Task-ID aus list_tasks"},
                   "message": {"type": "string", "description": "neuer Text (leer = unveraendert)"},
                   "schedule": {"type": "string", "description": "neuer Zeitplan (leer = einmalig/unveraendert)"}},
                  ["id"]),
    "list_skills": (t_list_skills, "Verfügbare Experten-Skills auflisten (name: Beschreibung). Vor Fachaufgaben prüfen, ob ein passender Skill existiert.",
                    {}, []),
    "load_skill": (t_load_skill, "Einen Experten-Skill (Wissens-Dokument) in den Kontext laden und befolgen.",
                   {"name": {"type": "string", "description": "Skill-Name aus list_skills"}}, ["name"]),
    "memory_store": (t_memory_store, "Einen Wert dauerhaft merken (überlebt Neustart/Instanz-Löschung).",
                     {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    "memory_recall": (t_memory_recall, "Gemerkten Wert abrufen; ohne key alle Einträge.",
                      {"key": {"type": "string"}}, []),
    "playbook_add": (t_playbook_add,
                     "Eine dauerhafte Regel/ein Vorgehen festhalten — gilt kuenftig IMMER. "
                     "Nutze das, wenn der Nutzer dir sagt WIE etwas zu tun ist, eine "
                     "dauerhafte Vorliebe nennt oder dich korrigiert.",
                     {"rule": {"type": "string", "description": "die Regel als kurzer, konkreter Satz"}}, ["rule"]),
    "playbooks": (t_playbooks, "Alle festen Regeln (Playbooks) mit IDs anzeigen.", {}, []),
    "playbook_forget": (t_playbook_forget, "Eine Regel per ID entfernen (ID aus playbooks).",
                        {"id": {"type": "string", "description": "Playbook-ID"}}, ["id"]),
    "remote_ls": (t_remote_ls,
                  "Den vom Nutzer freigegebenen Ordner auflisten (liegt auf SEINEM Rechner, "
                  "per P2P angebunden). Pfade sind relativ zur Wurzel der Freigabe.",
                  {"path": {"type": "string", "description": "relativ, Standard '.'"}}, []),
    "remote_read": (t_remote_read,
                    "Datei aus dem freigegebenen Ordner des Nutzers lesen (Pfad relativ zur Freigabe).",
                    {"path": {"type": "string"}}, ["path"]),
    "remote_write": (t_remote_write,
                     "Datei in den freigegebenen Ordner des Nutzers schreiben — LEGT AN und "
                     "UEBERSCHREIBT, fehlende Unterordner entstehen automatisch. Der Schreibzugriff "
                     "ist ausdruecklich erlaubt: wenn der Nutzer dort etwas ablegen, speichern oder "
                     "aendern will, RUFE DIESES TOOL AUF, statt zu behaupten, du koenntest nicht "
                     "schreiben. Nur wenn es einen Fehler zurueckgibt, ist es nicht moeglich.",
                     {"path": {"type": "string", "description": "relativ zur Freigabe, z. B. 'notiz.txt'"},
                      "content": {"type": "string", "description": "vollstaendiger neuer Dateiinhalt"}},
                     ["path", "content"]),
    "remote_delete": (t_remote_delete,
                      "Datei oder Ordner im freigegebenen Ordner des Nutzers loeschen. "
                      "Unwiderruflich — es gibt keinen Papierkorb. Nur loeschen, wenn der Nutzer "
                      "es verlangt, und im Zweifel vorher nachfragen. Ein nicht-leerer Ordner "
                      "scheitert absichtlich; dafuer recursive=true setzen.",
                      {"path": {"type": "string", "description": "relativ zur Freigabe"},
                       "recursive": {"type": "boolean",
                                     "description": "Ordner mitsamt Inhalt loeschen (Standard false)"}},
                      ["path"]),
    "list_secrets": (t_list_secrets, "Zeigt die für diesen Agenten freigegebenen Secret-Namen (keine Werte).",
                     {}, []),
    "get_secret": (t_get_secret, "Holt ein freigegebenes Secret (z. B. API-Key/Token) nur bei Bedarf. Werte niemals in Antworten/Logs ausgeben.",
                   {"name": {"type": "string"}}, ["name"]),
}


# Optionale Werkzeug-Allowlist pro Instanz (AGENT_TOOLS, kommagetrennt). Leer =
# alle. Filtert sowohl das an das Modell gemeldete Schema ALS AUCH die
# Ausfuehrung — ein Modell koennte sonst ein abgeschaltetes Tool trotzdem
# aufrufen. MCP-Tools sind davon unberuehrt (die steuert MCP_SERVERS/Policy).
_TOOL_ALLOW = {t.strip() for t in os.environ.get("AGENT_TOOLS", "").split(",") if t.strip()}


# Task-Verwaltung nur, wo der Manager TASK_ADMIN gesetzt hat (Orchestrator).
_TASK_ADMIN_TOOLS = {"list_tasks", "delete_task", "edit_task",
                     "mission_start", "missions", "mission_update", "mission_finish"}


def tool_enabled(name):
    if name == "offload_read":
        return True   # Systemhilfe: muss immer verfuegbar sein, sonst haengt eine Referenz in der Luft
    if name == "spawn_subagent" and os.environ.get("NO_SPAWN"):
        return False
    if name in _TASK_ADMIN_TOOLS and not os.environ.get("TASK_ADMIN"):
        return False
    return (not _TOOL_ALLOW) or name in _TOOL_ALLOW


def builtin_schema():
    out = []
    for name, (_fn, desc, props, req) in BUILTIN.items():
        if not tool_enabled(name):
            continue
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}})
    return out


# --- MCP (stdio) ------------------------------------------------------------
class MCP:
    def __init__(self, name, argv, env=None):
        self.name = name
        proc_env = {**os.environ, **(env or {})}
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, env=proc_env)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "or-agent", "version": "1"}})
        self._notify("notifications/initialized")

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _rpc(self, method, params):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        for _ in range(10000):
            line = self.proc.stdout.readline()
            if not line:
                return {}
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == self._id:
                return msg.get("result", {})
        return {}

    def tools(self):
        return self._rpc("tools/list", {}).get("tools", [])

    def call(self, tool, args):
        r = self._rpc("tools/call", {"name": tool, "arguments": args})
        parts = [c.get("text", "") for c in r.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts) or json.dumps(r)[:MAX_TOOL_OUT]


class HubMCP:
    """MCP ueber den Manager statt als eigener Prozess in der VM.

    Der Serverprozess laeuft im MCP-Hub am Host; hier geht nur noch JSON-RPC
    ueber /api/mcp hinaus. Damit braucht der Gast weder die Tokens (die setzt
    der Manager ein) noch LAN-Zugang (die Verbindung zum Zielsystem oeffnet
    der Hub). Gleiche Schnittstelle wie MCP: tools() und call()."""

    def __init__(self, name):
        self.name = name
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "or-agent", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _send(self, payload):
        body = json.dumps({"server": self.name, "payload": payload})
        req = urllib.request.Request(_manager_base() + "/api/mcp", data=body.encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=120).read() or b"{}")

    def _rpc(self, method, params):
        self._id += 1
        out = self._send({"jsonrpc": "2.0", "id": self._id,
                          "method": method, "params": params})
        if out.get("error"):
            raise RuntimeError(str(out["error"])[:300])
        return out.get("result", {})

    def tools(self):
        return self._rpc("tools/list", {}).get("tools", [])

    def call(self, tool, args):
        r = self._rpc("tools/call", {"name": tool, "arguments": args})
        parts = [c.get("text", "") for c in r.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts) or json.dumps(r)[:MAX_TOOL_OUT]


_mcp = {}      # server-name -> MCP
_mcp_tools = {}  # exposed-tool-name -> (server-name, mcp-tool-name)


def init_mcp():
    # MCP_CONFIG liegt nicht mehr in der Instanz-Config — es trug die Tokens im
    # Klartext. Der Manager baut sie zur Laufzeit aus MCP_SERVERS zusammen und
    # setzt dabei nur die Secrets ein, die die Policy dieser Instanz erlaubt.
    cfg = os.environ.get("MCP_CONFIG", "")
    if not cfg:
        try:
            body = _mgr_get(_manager_base(), "/api/mcp-config")
            d = json.loads(body)
            if d.get("unresolved"):
                log("MCP: nicht freigegebene Secrets, Server startet ggf. ohne Zugang:",
                    ", ".join(d["unresolved"]))
            if d.get("mcpServers"):
                cfg = json.dumps(d)
        except Exception as e:
            log("MCP-Konfiguration nicht vom Manager zu bekommen:", repr(e))
    if not cfg:
        p = os.path.join(WORKDIR, ".mcp.json")
        if os.path.exists(p):
            cfg = open(p).read()
    if not cfg:
        return []
    try:
        servers = json.loads(cfg).get("mcpServers", json.loads(cfg))
    except Exception as e:
        log("MCP config fehlerhaft:", e)
        return []
    schema = []
    for name, spec in servers.items():
        argv = [spec["command"], *spec.get("args", [])] if isinstance(spec, dict) else None
        if not argv:
            continue
        env = spec.get("env") if isinstance(spec, dict) else None
        try:
            # Hub zuerst: der Prozess laeuft am Host, der Gast braucht weder
            # argv noch env noch Secrets. Der Eigenprozess bleibt Rueckfall
            # fuer Manager ohne /api/mcp (aelterer Stand).
            try:
                srv = HubMCP(name)
            except Exception as hub_err:
                log(f"MCP '{name}': Hub nicht erreichbar ({hub_err!r:.120}), starte lokal")
                srv = MCP(name, argv, env={str(k): str(v) for k, v in (env or {}).items()})
            _mcp[name] = srv
            for t in srv.tools():
                fq = f"{name}__{t['name']}"[:64]
                _mcp_tools[fq] = (name, t["name"])
                schema.append({"type": "function", "function": {
                    "name": fq, "description": (t.get("description") or fq)[:400],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
            log(f"MCP '{name}': {len(srv.tools())} tools")
        except Exception as e:
            log(f"MCP '{name}' Start fehlgeschlagen:", repr(e))
    return schema


def _audit_target(name, args):
    """Aussagekraeftigstes Feld je Tool fuer das Audit-Log — nie ein Secret-Wert.
    Bei get_secret nur der Name, bei write NICHT der Inhalt."""
    a = args or {}
    if name in ("http_fetch",):
        return a.get("url", "")
    if name == "web_search":
        return a.get("query", "")
    if name in ("read_file", "write_file", "list_dir", "read_pdf",
                "remote_ls", "remote_read", "remote_write", "remote_delete"):
        return a.get("path", "")
    if name == "bash":
        return (a.get("command", "") or "")[:200]
    if name in ("get_secret", "load_skill", "memory_store", "memory_recall", "recall_tasks", "read_inbox"):
        return a.get("name", "") or a.get("key", "") or a.get("query", "")
    if name == "spawn_subagent":
        return (a.get("task", "") or "")[:120]
    if name == "create_task":
        return (a.get("target","") + ": " + (a.get("task","") or ""))[:160]
    return ""


def audit(name, args, ok=True):
    """Tool-Aufruf beim Manager protokollieren (pro Instanz, auf dem Host —
    ueberlebt VM-Neustarts). Best-effort: faellt der Broker aus, laeuft der
    Agent normal weiter. Enthaelt Tool, Zielfeld (URL/Pfad/Query) und ok-Flag,
    NIE Secret-Werte oder Dateiinhalte."""
    try:
        _mgr(_manager_base(), "/api/audit",
             {"tool": name, "target": _audit_target(name, args), "ok": bool(ok)}, timeout=5)
    except Exception:
        pass


def exec_tool(name, args):
    # Hook/Intervention: Denylist + optionale HITL-Freigabe VOR der Ausfuehrung.
    allow, reason = _hook_before_tool(name, args)
    if not allow:
        audit(name, args, ok=False)
        return f"Tool '{name}' nicht ausgefuehrt: {reason}"
    try:
        if name in BUILTIN:
            if not tool_enabled(name):
                audit(name, args, ok=False)
                return f"Tool '{name}' ist fuer diese Instanz nicht freigegeben."
            audit(name, args)
            return _finalize_output(name, str(BUILTIN[name][0](**args)))
        if name in _mcp_tools:
            audit(name, args)
            srv, tool = _mcp_tools[name]
            return _finalize_output(name, _mcp[srv].call(tool, args))
        audit(name, args, ok=False)
        return f"unbekanntes Tool: {name}"
    except Exception as e:
        return f"Tool-Fehler ({name}): {e!r}"


# --- Verbrauch melden -------------------------------------------------------
def report_usage(u):
    """Token/Kosten eines Aufrufs an den Manager melden (fire-and-forget).
    Die Instanz erkennt der Manager an der Quell-IP; wir schicken nur Zahlen.
    Faellt der Manager aus, darf das den Chat nicht stoeren -> alles schlucken."""
    if not isinstance(u, dict):
        return
    try:
        payload = json.dumps({
            "model": OR_MODEL,
            "prompt_tokens": u.get("prompt_tokens") or 0,
            "completion_tokens": u.get("completion_tokens") or 0,
            "cost": u.get("cost") or 0.0,
        }).encode()
        req = urllib.request.Request(f"{_manager_base()}/api/usage", data=payload,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


# ===== Harness-Muster (angelehnt an strands-agents/harness-sdk, Apache-2.0) ====
# Vier Bausteine, alle stdlib, ohne neue Abhaengigkeit:
#  1) Retry mit Backoff um den Modellaufruf
#  2) Kontext ZUSAMMENFASSEN statt Wegwerfen (summarizing conversation manager)
#  3) Grosse Tool-Ausgaben AUSLAGERN statt hart kappen (context offloader)
#  4) ZIEL-Schleife mit Judge (goal loop) + Tool-HOOK (interventions/HITL)

LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "3"))
_RETRY_CODES = {408, 409, 429, 500, 502, 503, 504}


def _retry_sleep(attempt):
    # 0.5s, 1s, 2s, 4s … gedeckelt auf 8s.
    time.sleep(min(8.0, 0.5 * (2 ** attempt)))


# --- 2) Kontext-Zusammenfassung --------------------------------------------
SUMMARY_TAG = "[Zusammenfassung]"
CTX_SUMMARY = os.environ.get("CTX_SUMMARY", "1") != "0"
CTX_PRESERVE_RECENT = int(os.environ.get("CTX_PRESERVE_RECENT", "10"))
SUMMARIZE_PROMPT = (
    "Du fasst einen Gespraechsverlauf zusammen. Erzeuge eine knappe, strukturierte "
    "Zusammenfassung in Stichpunkten. Antworte NICHT konversationell und sprich den "
    "Nutzer NICHT an. Enthalte: behandelte Themen und Fragen; wichtige Tool-Aufrufe "
    "und deren Ergebnisse; geteilte Fakten, Daten und Code; offene Punkte; zentrale "
    "Erkenntnisse. Schreibe in der dritten Person. Nimm nicht an, dass Tools "
    "fehlschlugen, sofern nicht ausdruecklich angegeben.")


def _msg_text(m):
    c = m.get("content")
    if isinstance(c, list):   # Vision-Content -> nur die Textteile
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def _summarize(msgs, prior=""):
    """Eine Nachrichtenliste (Gespraech, ohne System-Bloecke) zu einem kurzen
    Stichpunkt-Summary verdichten. Faellt der Aufruf aus -> '' (Aufrufer macht
    dann das alte Wegwerf-Verhalten)."""
    lines = []
    for m in msgs:
        role = m.get("role")
        txt = _msg_text(m)
        if role == "tool":
            lines.append(f"[Tool-Ergebnis] {txt[:1500]}")
        elif role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                names = ", ".join(t.get("function", {}).get("name", "?") for t in tcs)
                lines.append(f"[Assistant rief Tools: {names}] {txt[:800]}")
            else:
                lines.append(f"[Assistant] {txt[:1500]}")
        elif role == "user":
            lines.append(f"[Nutzer] {txt[:1500]}")
    joined = "\n".join(lines)
    if prior:
        joined = f"Bisherige Zusammenfassung:\n{prior}\n\nNeue Nachrichten:\n{joined}"
    msg = or_chat([{"role": "system", "content": SUMMARIZE_PROMPT},
                   {"role": "user", "content": joined}], [])
    out = (msg.get("content") or "").strip()
    return "" if out.startswith("⚠") else out   # Fehlermeldung zaehlt nicht


# --- 3) Context-Offloader ---------------------------------------------------
OFFLOAD_DIR = os.path.join(WORKDIR, ".offload")
OFFLOAD_MIN = int(os.environ.get("OFFLOAD_MIN", str(MAX_TOOL_OUT)))
OFFLOAD_PREVIEW = int(os.environ.get("OFFLOAD_PREVIEW", "2000"))
_offload_seq = 0


def _finalize_output(name, out):
    """Ist eine Tool-Ausgabe groesser als OFFLOAD_MIN, wird sie VOLLSTAENDIG in
    eine Datei ausgelagert und im Kontext nur eine Vorschau + Referenz gehalten
    (offload_read holt den Rest). So bleibt nichts verloren, ohne den Kontext zu
    fluten. Kleiner -> unveraendert."""
    out = out if isinstance(out, str) else str(out)
    if len(out) <= OFFLOAD_MIN:
        return out
    global _offload_seq
    _offload_seq += 1
    oid = f"{name}-{_offload_seq}-{uuid.uuid4().hex[:6]}"
    try:
        os.makedirs(OFFLOAD_DIR, exist_ok=True)
        with open(os.path.join(OFFLOAD_DIR, oid + ".txt"), "w") as fh:
            fh.write(out)
    except Exception:
        return out[:MAX_TOOL_OUT]   # Auslagern misslang -> alt: hart kappen
    preview = out[:OFFLOAD_PREVIEW]
    return (preview + f"\n\n[… {len(out) - len(preview)} weitere Zeichen ausgelagert. "
            f"Weiterlesen mit offload_read(id=\"{oid}\", offset={OFFLOAD_PREVIEW}). "
            f"Gesamtlaenge {len(out)} Zeichen.]")


def t_offload_read(id="", offset=0, length=None):
    """Ausgelagerte Tool-Ausgabe (siehe offload-Referenz) stueckweise lesen."""
    length = int(length) if length else MAX_TOOL_OUT
    offset = max(0, int(offset or 0))
    safe = os.path.basename(str(id))              # kein Pfad-Ausbruch
    fp = os.path.join(OFFLOAD_DIR, safe + ".txt")
    try:
        with open(fp) as fh:
            fh.seek(offset)
            data = fh.read(length)
    except FileNotFoundError:
        return f"offload '{id}' nicht gefunden."
    except Exception as e:
        return f"offload-Fehler: {e!r}"
    more = f"\n\n[… weiter mit offset={offset + len(data)} …]" if len(data) >= length else ""
    return data + more


# offload_read in den Werkzeugkatalog haengen (erst hier, weil t_offload_read
# nach dem BUILTIN-Literal definiert ist).
BUILTIN["offload_read"] = (
    t_offload_read,
    "Eine zuvor ausgelagerte, gekuerzte Tool-Ausgabe stueckweise nachlesen "
    "(die offload-Referenz nennt id und offset).",
    {"id": {"type": "string", "description": "offload-id aus der Referenz"},
     "offset": {"type": "integer", "description": "Startposition (Zeichen)"},
     "length": {"type": "integer", "description": "max. Zeichen (Standard 8000)"}},
    ["id"])


# --- 4a) Ziel-Schleife (goal loop) -----------------------------------------
GOAL_MAX_ATTEMPTS = int(os.environ.get("GOAL_MAX_ATTEMPTS", "3"))
_goal = (os.environ.get("AGENT_GOAL", "").strip() or None)
JUDGE_PROMPT = (
    "Du bist ein strenger Pruefer. Pruefe, ob die ANTWORT das ZIEL fuer die FRAGE "
    "erfuellt. Antworte AUSSCHLIESSLICH mit JSON, kein weiterer Text: "
    '{"meets": true|false, "feedback": "knappe Begruendung, was noch fehlt"}.')


def _set_goal(cmd):
    global _goal
    rest = cmd[len("/goal"):].strip()
    if rest in ("", "show", "status"):
        return f"\U0001f3af Ziel: {_goal}" if _goal else \
            "Kein Ziel gesetzt. /goal <Kriterium> setzt eines, /goal off entfernt es."
    if rest in ("off", "clear", "none", "aus"):
        _goal = None
        return "\U0001f3af Ziel entfernt."
    _goal = rest
    return f"\U0001f3af Ziel gesetzt (max. {GOAL_MAX_ATTEMPTS} Versuche): {_goal}"


def _judge(goal, question, answer):
    """(meets, feedback). Judge kaputt/unparsebar -> durchlassen (True)."""
    try:
        m = or_chat([{"role": "system", "content": JUDGE_PROMPT},
                     {"role": "user", "content": f"ZIEL:\n{goal}\n\nFRAGE:\n{question}\n\nANTWORT:\n{answer}"}], [])
        raw = (m.get("content") or "").strip()
        d = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        return bool(d.get("meets")), str(d.get("feedback", ""))[:500]
    except Exception:
        return True, ""


def _run_goal(hist, question):
    """Antwort erzeugen und gegen _goal pruefen; bei Nichterfuellung mit der
    Judge-Kritik nachbessern, bis max. GOAL_MAX_ATTEMPTS."""
    answer = _tool_loop(hist)
    for _ in range(GOAL_MAX_ATTEMPTS - 1):
        meets, fb = _judge(_goal, question, answer)
        if meets:
            break
        hist.append({"role": "system", "content":
                     f"Deine letzte Antwort erfuellt das Ziel noch nicht: {_goal}. "
                     f"Kritik: {fb}. Verbessere die Antwort entsprechend."})
        answer = _tool_loop(hist)
    return answer


# --- 4b) Tool-Hook: harte Denylist + optionale HITL-Freigabe ----------------
HITL = os.environ.get("HITL", "") not in ("", "0", "false", "False")
HITL_TOOLS = set(t for t in os.environ.get(
    "HITL_TOOLS", "bash,remote_delete,remote_write,delete_task,edit_task").split(",") if t)
HITL_TIMEOUT = int(os.environ.get("HITL_TIMEOUT", "120"))
# Immer aktiv, unabhaengig von HITL: offensichtlich zerstoererische bash-Muster.
_DENY_PATTERNS = ("rm -rf /", ":(){:|:&};:", "mkfs", "dd if=", "> /dev/sd", "chmod -R 000")


def _request_approval(name, args):
    """Beim Manager eine Freigabe anfragen (der fragt den Nutzer per Signal) und
    darauf pollen. Kann der Manager es nicht (alte Version/kein Signal) -> nicht
    blockieren (True). Zeitueberschreitung/Ablehnung -> False."""
    try:
        d = json.loads(_mgr(_manager_base(), "/api/hitl",
                            {"tool": name, "target": _audit_target(name, args)}, timeout=8))
        hid = d.get("id")
        if not hid:
            return True
    except Exception:
        return True
    deadline = time.time() + HITL_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        try:
            st = json.loads(_mgr_get(_manager_base(), f"/api/hitl/{hid}", timeout=6)).get("status")
        except Exception:
            continue
        if st == "approved":
            return True
        if st == "denied":
            return False
    return False


def _hook_before_tool(name, args):
    """(allow, reason). Denylist zuerst, dann optionale HITL-Freigabe."""
    if name == "bash":
        cmd = str(args.get("command", ""))
        for pat in _DENY_PATTERNS:
            if pat in cmd:
                return False, f"durch Sicherheitsregel blockiert ({pat})"
    if HITL and name in HITL_TOOLS:
        if not _request_approval(name, args):
            return False, "vom Nutzer nicht freigegeben (oder Zeitueberschreitung)"
    return True, ""


# --- OpenRouter chat --------------------------------------------------------
def or_chat(messages, tools, model=None):
    _b = {"model": model or OR_MODEL, "messages": messages, "usage": {"include": True}}
    if tools:                       # leere tools-Liste NICHT mitschicken (400)
        _b["tools"] = tools
        _b["tool_choice"] = "auto"
    if _reasoning:
        _b["reasoning"] = {"effort": _reasoning}
    body = json.dumps(_b).encode()
    last = ""
    for attempt in range(LLM_RETRIES + 1):
        req = urllib.request.Request(OR_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {ensure_or_key()}", "Content-Type": "application/json",
            "HTTP-Referer": "https://agents.kat56.de", "X-Title": "kat56-agent"})
        try:
            r = urllib.request.urlopen(req, timeout=120)
            d = json.loads(r.read().decode())
            report_usage(d.get("usage"))
            return d["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            last = f"⚠️ {LLM_NAME} HTTP {e.code}: {e.read().decode()[:300]}"
            if e.code in _RETRY_CODES and attempt < LLM_RETRIES:
                _retry_sleep(attempt); continue
            return {"content": last}
        except Exception as e:
            last = f"⚠️ {LLM_NAME}-Fehler: {e!r}"
            if attempt < LLM_RETRIES:
                _retry_sleep(attempt); continue
            return {"content": last}
    return {"content": last}


TOOLS = []
_history = [{"role": "system", "content": SYSTEM}]

# Semantisches Langzeitgedaechtnis: statt beim ersten Turn ALLE Fakten in den
# Prompt zu kippen (das waechst mit dem Gedaechtnis und kostet jeden Turn),
# holt der Agent pro Frage nur die inhaltlich naechsten Notizen. Kurzzeit ist
# _history (dieses Gespraech), Langzeit liegt semantisch im Manager.
RECALL_TAG = "[Gedaechtnis]"
RECALL_K = 4
# Schwelle fuer multilingual-e5: relevante Treffer liegen ~0.82+, thematisch
# fremde ~0.76. 0.78 trennt sauber. Tunbar, falls zu streng/locker.
RECALL_MIN = 0.78


def _recall(user_message):
    """Den Gedaechtnis-Block in _history durch die zu DIESER Frage passenden
    Langzeit-Notizen ersetzen. Genau EIN solcher Block bleibt stehen, frisch je
    Turn; /reset raeumt ihn mit weg. Faellt die Suche aus, gibt es diesen Turn
    eben keinen Langzeit-Kontext — die Notizen bleiben gespeichert."""
    _history[:] = [m for m in _history
                   if not (m.get("role") == "system"
                           and str(m.get("content", "")).startswith(RECALL_TAG))]
    try:
        body = _mgr(_manager_base(), "/api/memory-search",
                    {"query": user_message, "k": RECALL_K}, timeout=8)
        hits = [h for h in json.loads(body).get("hits", [])
                if h.get("score", 0) >= RECALL_MIN]
    except Exception:
        hits = []
    if hits:
        block = (RECALL_TAG + " Relevante Notizen aus frueheren Sitzungen "
                 "(nutze sie, wenn sie zur Frage passen):\n"
                 + "\n".join(f"- {h['text']}" for h in hits))
        _history.insert(1, {"role": "system", "content": block})


PLAYBOOK_TAG = "[Playbooks]"


def _inject_playbooks():
    """Feste Regeln jeden Turn frisch einblenden — anders als _recall gelten
    Playbooks IMMER. Genau EIN Block, /reset raeumt ihn mit weg."""
    _history[:] = [m for m in _history
                   if not (m.get("role") == "system"
                           and str(m.get("content", "")).startswith(PLAYBOOK_TAG))]
    try:
        pbs = json.loads(_mgr_get(_manager_base(), "/api/playbooks", timeout=6)).get("playbooks", [])
    except Exception:
        pbs = []
    if pbs:
        block = (PLAYBOOK_TAG + " Deine festen Regeln — IMMER befolgen:\n"
                 + "\n".join(f"- {p.get('text','')}" for p in pbs))
        _history.insert(1, {"role": "system", "content": block})


# --- Prompt-Templates: /name -> im Manager gepflegter Prompt -----------------
# Wiederkehrende Auftraege als Kommando (pi.dev-Idee "prompt templates").
# Expansion passiert HIER im Agenten — funktioniert damit in Web, App und
# Signal gleichermassen. "/daily bitte kurz" -> Template-Text + " bitte kurz".
_BUILTIN_SLASH = ("/reset", "/fresh", "/reasoning", "/goal", "/model", "/steps")
_prompts_cache = {"ts": 0.0, "map": {}}


def _prompt_templates():
    if time.time() - _prompts_cache["ts"] > 30:
        try:
            lst = json.loads(_mgr_get(_manager_base(), "/api/prompts", timeout=6)).get("prompts", [])
            _prompts_cache["map"] = {p["name"]: p.get("text", "") for p in lst if p.get("name")}
        except Exception:
            pass                       # alten Cache behalten
        _prompts_cache["ts"] = time.time()
    return _prompts_cache["map"]


def _expand_prompt(message):
    m = message.strip()
    if not m.startswith("/") or m.startswith(_BUILTIN_SLASH):
        return message
    name, _, rest = m[1:].partition(" ")
    tpl = _prompt_templates().get(name)
    if not tpl:
        return message
    return tpl + ((" " + rest.strip()) if rest.strip() else "")


MISSION_TAG = "[Missionen]"


def _inject_missions():
    """Aktive Missionen jeden Turn kompakt einblenden — der Arbeitsstand
    ueberlebt so /reset und Neustart. Nur fuer den Orchestrator (TASK_ADMIN).
    Genau EIN Block, /reset raeumt ihn mit weg."""
    _history[:] = [m for m in _history
                   if not (m.get("role") == "system"
                           and str(m.get("content", "")).startswith(MISSION_TAG))]
    if not os.environ.get("TASK_ADMIN"):
        return
    try:
        ms = json.loads(_mgr_get(_manager_base(), "/api/missions", timeout=6)).get("missions", [])
    except Exception:
        ms = []
    lines = []
    for m in ms:
        if m.get("status") != "active":
            continue
        cur = next((st for st in m.get("steps", []) if st.get("status") == "doing"),
                   None) or next((st for st in m.get("steps", []) if st.get("status") == "open"), None)
        done = sum(1 for st in m.get("steps", []) if st.get("status") == "done")
        lines.append(f"- {m['id']}: {m['goal'][:100]} ({done}/{len(m.get('steps', []))} Schritte) — "
                     + (f"aktuell Schritt {cur['n']}: {cur['text'][:80]} [{cur['status']}]"
                        if cur else "alle Schritte erledigt -> mission_finish!"))
    if lines:
        _history.insert(1, {"role": "system", "content":
                            MISSION_TAG + " Deine laufenden Missionen (Fortschritt liegt im "
                            "Manager, nutze mission_update/mission_finish):\n" + "\n".join(lines)})


# Obergrenze fuers Gespraechs-_history. Ohne die waechst der Kontext eines
# Dauerprozesses (Orchestrator: Heartbeat + App-Chats teilen sich EIN _history)
# unbegrenzt, und jeder Call schickt alles erneut. Geschnitten wird nur ZWISCHEN
# Turns (hier, vor der neuen Nutzernachricht) — nie mitten in einem Tool-Zyklus,
# sonst haengt ein tool-Ergebnis ohne sein tool_calls in der Luft (API-Fehler).
CTX_MAX_MSGS = int(os.environ.get("CTX_MAX_MSGS", "20"))


def _trim_history():
    """Bei Ueberlauf die aelteren Nachrichten ZUSAMMENFASSEN statt sie zu
    verwerfen (summarizing conversation manager). _history[0] (System) ist
    gepinnt; die letzten CTX_PRESERVE_RECENT Gespraechsnachrichten bleiben
    woertlich; alles davor wird zu einem [Zusammenfassung]-Systemblock verdichtet
    (bestehende Zusammenfassung wird eingefaltet). Transiente Bloecke
    (Playbooks/Gedaechtnis) werden hier verworfen — _inject/_recall setzen sie
    gleich neu. Nur ZWISCHEN Turns aufrufen, nie im Tool-Zyklus."""
    if len(_history) <= CTX_MAX_MSGS:
        return
    head = _history[0]
    prior, convo = "", []
    for m in _history[1:]:
        if m.get("role") == "system":
            c = str(m.get("content", ""))
            if c.startswith(SUMMARY_TAG):
                prior = c[len(SUMMARY_TAG):].strip()
            continue    # Playbook/Recall/Summary: nicht als Gespraech behandeln
        convo.append(m)

    def _boundary_keep(msgs, n):
        """Die letzten n Nachrichten, aber an einer user-Grenze beginnend, damit
        kein tool-Ergebnis ohne sein assistant/tool_calls verwaist."""
        k = msgs[-n:] if n < len(msgs) else msgs[:]
        while k and k[0].get("role") != "user":
            k.pop(0)
        return k

    def _prefix(sm):
        return [{"role": "system", "content": SUMMARY_TAG + " " + sm}] if sm else []

    if not CTX_SUMMARY or len(convo) <= CTX_PRESERVE_RECENT:
        # Zusammenfassen aus/zu wenig -> altes Verhalten, aber Summary behalten.
        _history[:] = [head] + _prefix(prior) + _boundary_keep(convo, CTX_MAX_MSGS - 1)
        return
    recent = _boundary_keep(convo, CTX_PRESERVE_RECENT)
    to_sum = convo[:len(convo) - len(recent)]
    new_summary = _summarize(to_sum, prior) if to_sum else prior
    if not new_summary:
        # Summarizer nicht verfuegbar -> nicht mehr Kontext riskieren: wegwerfen.
        _history[:] = [head] + _prefix(prior) + recent
        return
    _history[:] = [head] + _prefix(new_summary) + recent


# --- Steering: dem laufenden Agenten reinrufen -------------------------------
# Waehrend ein Turn laeuft (Tool-Schleife), kann der Nutzer Nachrichten
# nachschieben (run_agent: POST /api/steer). Sie werden zwischen zwei Tool-
# Schritten als User-Nachricht eingespeist — der Agent aendert den Kurs, statt
# stur zu Ende zu laufen.
_steer_lock = threading.Lock()
_steer_q = []
_busy = [False]


def steer_push(msg):
    """(angenommen?) True, wenn ein Turn laeuft und die Nachricht eingespeist
    wird; False -> Aufrufer soll sie als normale Nachricht senden."""
    with _steer_lock:
        if not _busy[0]:
            return False
        _steer_q.append(str(msg)[:2000])
        return True


def _drain_steer(hist, on_token=None):
    with _steer_lock:
        msgs, _steer_q[:] = _steer_q[:], []
    for m in msgs:
        hist.append({"role": "user", "content":
                     "[Steuerung — soeben vom Nutzer nachgeschoben, hat Vorrang] " + m})
        if on_token:
            on_token(f"\n\u21aa {m}\n")
    return bool(msgs)


def _tool_loop(hist):
    """Tool-Schleife auf einer beliebigen Nachrichtenliste. `hist` ist entweder
    das persistente _history (Gespraech) oder eine Wegwerf-Liste (Heartbeat)."""
    for _ in range(MAX_STEPS):
        _drain_steer(hist)
        msg = or_chat(hist, TOOLS)
        hist.append(msg)
        tcs = msg.get("tool_calls")
        if not tcs:
            # Kam waehrend der Antwort noch eine Steuerung rein? Dann weiter.
            if _drain_steer(hist):
                continue
            return msg.get("content") or "(leere Antwort)"
        for tc in tcs:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = exec_tool(fn["name"], args)
            log("tool", fn["name"], "->", "(redacted)" if fn["name"] == "get_secret" else out[:80].replace("\n", " "))
            hist.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    return "(max. Tool-Schritte erreicht)"


def run(user_message):
    user_message = _expand_prompt(user_message)
    if user_message.strip() == "/reset":
        del _history[1:]
        return "🔄 Kontext zurückgesetzt."
    if user_message.startswith("/reasoning"):
        return _set_reasoning(user_message)
    if user_message.startswith("/goal"):
        return _set_goal(user_message)
    if user_message.startswith("/model"):
        return _set_model(user_message)
    if user_message.startswith("/steps"):
        return _set_steps(user_message)
    # /fresh: zustandslos in einem Wegwerf-Kontext laufen — das Gespraechs-
    # _history bleibt unangetastet (sonst wischte ein Heartbeat einen laufenden
    # App-Chat weg, weil beide sich dasselbe _history teilen). Fuer den
    # Orchestrator-Heartbeat: schauen, delegieren, verwerfen.
    if user_message.startswith("/fresh"):
        m = user_message[len("/fresh"):].strip()
        hist = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": m}]
        return _tool_loop(hist)
    _trim_history()
    _inject_playbooks()
    _inject_missions()
    _recall(user_message)
    _history.append({"role": "user", "content": user_message})
    _busy[0] = True
    try:
        if _goal:
            return _run_goal(_history, user_message)
        return _tool_loop(_history)
    finally:
        _busy[0] = False


def or_chat_stream(messages, tools, on_token):
    """Wie or_chat, aber streamend: ruft on_token(text) je Delta. Baut die
    (assistant-)Nachricht inkl. evtl. tool_calls aus dem Stream zusammen."""
    _b = {"model": OR_MODEL, "messages": messages, "stream": True, "usage": {"include": True}}
    if tools:
        _b["tools"] = tools
        _b["tool_choice"] = "auto"
    if _reasoning:
        _b["reasoning"] = {"effort": _reasoning}
    body = json.dumps(_b).encode()
    content = ""
    tcs = {}
    reasoning_open = False
    # Nur den Verbindungsaufbau retryen (mitten im Stream nicht sinnvoll wieder-
    # holbar, da schon Tokens geflossen sein koennen).
    r = None
    for attempt in range(LLM_RETRIES + 1):
        req = urllib.request.Request(OR_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {ensure_or_key()}", "Content-Type": "application/json",
            "HTTP-Referer": "https://agents.kat56.de", "X-Title": "kat56-agent"})
        try:
            r = urllib.request.urlopen(req, timeout=180)
            break
        except urllib.error.HTTPError as e:
            m = f"⚠️ {LLM_NAME} HTTP {e.code}: {e.read().decode()[:300]}"
            if e.code in _RETRY_CODES and attempt < LLM_RETRIES:
                _retry_sleep(attempt); continue
            on_token(m); return {"role": "assistant", "content": m}
        except Exception as e:
            m = f"⚠️ {LLM_NAME}-Fehler: {e!r}"
            if attempt < LLM_RETRIES:
                _retry_sleep(attempt); continue
            on_token(m); return {"role": "assistant", "content": m}
    try:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            if chunk.get("usage"):          # letzter Chunk traegt die Abrechnung
                report_usage(chunk["usage"])
            try:
                delta = chunk["choices"][0]["delta"]
            except (KeyError, IndexError):
                continue
            rzn = delta.get("reasoning")
            if rzn:
                if not reasoning_open:
                    on_token(THINK_START); reasoning_open = True
                on_token(rzn)
            c = delta.get("content")
            if c:
                if reasoning_open:
                    on_token(THINK_END); reasoning_open = False
                content += c
                on_token(c)
            _tcs = delta.get("tool_calls") or []
            if _tcs and reasoning_open:
                on_token(THINK_END); reasoning_open = False
            for tc in _tcs:
                i = tc.get("index", 0)
                slot = tcs.setdefault(i, {"id": "", "type": "function",
                                         "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                f = tc.get("function") or {}
                if f.get("name"):
                    slot["function"]["name"] += f["name"]
                if f.get("arguments"):
                    slot["function"]["arguments"] += f["arguments"]
        if reasoning_open:
            on_token(THINK_END)
    except Exception as e:
        # Abbruch mitten im Stream: das bereits Gestreamte behalten, Rest melden.
        m = f"⚠️ {LLM_NAME}-Streamabbruch: {e!r}"
        on_token(m)
        content += ("\n" + m)
    msg = {"role": "assistant", "content": content or None}
    if tcs:
        msg["tool_calls"] = [tcs[i] for i in sorted(tcs)]
    return msg


def run_stream(user_message, on_token, image=None):
    """Wie run(), aber streamt die Antwort-Tokens ueber on_token. Tool-Runden
    erzeugen keinen Text; die finale Antwort wird gestreamt.
    image: optionales Base64-JPEG -> als Vision-Content an OpenRouter."""
    user_message = _expand_prompt(user_message)
    if user_message.strip() == "/reset":
        del _history[1:]
        on_token("🔄 Kontext zurückgesetzt.")
        return
    if user_message.startswith("/reasoning"):
        on_token(_set_reasoning(user_message))
        return
    if user_message.startswith("/goal"):
        on_token(_set_goal(user_message))
        return
    if user_message.startswith("/model"):
        on_token(_set_model(user_message))
        return
    if user_message.startswith("/steps"):
        on_token(_set_steps(user_message))
        return
    # /fresh: wie in run() zustandslos, Gespraech unangetastet. Heartbeats
    # brauchen kein Streaming — einmal die Antwort ausgeben.
    if user_message.startswith("/fresh"):
        m = user_message[len("/fresh"):].strip()
        on_token(_tool_loop([{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": m}]))
        return
    _trim_history()
    _inject_playbooks()
    _inject_missions()
    _recall(user_message)
    if image:
        content = [
            {"type": "text", "text": user_message or "Was ist auf dem Bild?"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
        ]
    else:
        content = user_message
    _history.append({"role": "user", "content": content})
    _busy[0] = True
    try:
        if _goal:
            # Mit aktivem Ziel wird die Antwort gegen den Judge verfeinert (nicht
            # gestreamt) und danach als Ganzes ausgegeben.
            on_token(_run_goal(_history, user_message))
            return
        for _ in range(MAX_STEPS):
            _drain_steer(_history, on_token)
            msg = or_chat_stream(_history, TOOLS, on_token)
            _history.append(msg)
            tcs = msg.get("tool_calls")
            if not tcs:
                if _drain_steer(_history, on_token):
                    continue
                return
            for tc in tcs:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = exec_tool(fn["name"], args)
                log("tool", fn["name"], "->", "(redacted)" if fn["name"] == "get_secret" else out[:80].replace("\n", " "))
                _history.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
        on_token("\n(max. Tool-Schritte erreicht)")
    finally:
        _busy[0] = False


# Tool-Plugins (pi.dev-Extension-Idee, uebersetzt): eine .py-Datei je Tool,
# vom Manager auf die Config-Disk gelegt (/config/plugins). Konvention:
#   DESC = "…"; PARAMS = {...}; REQUIRED = [...];  def run(**kwargs): ...
# Der Dateiname (ohne .py) wird der Tool-Name. Die microVM ist die Sandbox.
PLUGIN_TOOLS = set()
PLUGIN_DIR = os.environ.get("PLUGIN_DIR", "/config/plugins")


def load_plugins():
    import importlib.util
    if not os.path.isdir(PLUGIN_DIR):
        return
    for f in sorted(os.listdir(PLUGIN_DIR)):
        if not f.endswith(".py"):
            continue
        name = f[:-3]
        if name in BUILTIN and name not in PLUGIN_TOOLS:
            log(f"plugin '{name}' ignoriert: kollidiert mit eingebautem Tool")
            continue
        try:
            spec = importlib.util.spec_from_file_location("plugin_" + name,
                                                          os.path.join(PLUGIN_DIR, f))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            BUILTIN[name] = (mod.run, str(getattr(mod, "DESC", name))[:300],
                             getattr(mod, "PARAMS", {}), getattr(mod, "REQUIRED", []))
            PLUGIN_TOOLS.add(name)
            log(f"plugin geladen: {name}")
        except Exception as e:
            log(f"plugin '{name}' FEHLER: {e!r}")


def init():
    global TOOLS
    os.makedirs(WORKDIR, exist_ok=True)
    load_plugins()
    TOOLS = builtin_schema() + init_mcp()
    log(f"agent bereit: backend={LLM_BACKEND} url={OR_URL} model={OR_MODEL} tools={len(TOOLS)} workdir={WORKDIR}")
