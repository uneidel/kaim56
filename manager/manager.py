#!/usr/bin/env python3
"""kAIm56 — Manager (Web-UI + API) für 1..x microVM-Instanzen (Firecracker).

Laeuft als root (braucht /dev/kvm, ip, iptables) — z. B. via systemd. Reine
Standardbibliothek, keine Extra-Pakete. Instanzen liegen als JSON unter
instances/<name>.json; Netz wird pro Instanz aus 'index' abgeleitet:
  host  172.30.<index>.1/30   guest 172.30.<index>.2/30   tap fc<index>
"""
import base64
import codecs
import json
import os
import re
import shlex
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chatui   # Chat-Oberflaeche (/chat), liegt neben dieser Datei

WEB_GUEST_PORT = 8080   # Port der Web-Bridge in der microVM
TERM_GUEST_PORT = 7682  # Port des webterm (Browser-Terminal) in der microVM

BASE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(BASE, "bin", "firecracker")
KERNEL = os.path.join(BASE, "bin", "vmlinux")
INST_DIR = os.path.join(BASE, "instances")
TEMPLATE_DIR = os.path.join(BASE, "templates")
RUN_DIR = os.path.join(BASE, "run")
SETTINGS_FILE = os.path.join(BASE, "settings.json")
# Geteilte Secrets/Defaults, die im Config-UI gepflegt werden und leere
# Template-Parameter gleichen Namens vorbefuellen.
SETTINGS_SCHEMA = [
    {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API key"},
    {"key": "ANTHROPIC_API_KEY", "label": "Anthropic API key"},
    {"key": "OPENAI_API_KEY", "label": "OpenAI API key"},
    {"key": "SIGNAL_NUMBER", "label": "Signal bot number"},
    {"key": "ALLOWED_SENDERS", "label": "Allowed Signal number(s)"},
]
# Diese Werte landen NIE in instances/<name>.json und nie auf der Config-Disk
# der microVM. Der Agent holt sie zur Laufzeit ueber den Secret-Broker
# (/api/secret/<name>, Gast per Source-IP erkannt, Allowlist per Policy).
SECRET_PARAMS = {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
# Gesetzte Geheimnisse verlassen den Manager nie im Klartext — die UI bekommt
# diesen Marker und schickt ihn beim Speichern unveraendert zurueck, wo er
# verworfen wird. Ein echter leerer Wert loescht den Eintrag weiterhin.
SETTINGS_KEEP = "__unchanged__"
# MCP_CONFIG trug die eingesetzten Secrets im Klartext (z. B. das HA-Bearer-
# Token). Gespeichert wird stattdessen MCP_SERVERS — nur die Katalognamen; die
# Werte holt der Agent zur Laufzeit ueber /api/mcp-config.
NEVER_PERSIST = SECRET_PARAMS | {"MCP_CONFIG"}

POOL = "172.30.0.0/16"
HOSTIF = os.environ.get("HOSTIF", "eno2")
LISTEN = ("0.0.0.0", int(os.environ.get("PORT", "8700")))
USER = os.environ.get("MANAGER_USER", "admin")
PW = os.environ.get("MANAGER_PASS", "")   # leer => keine Auth (nur hinter Traefik!)

# ---- NFS / Host-Ordner -----------------------------------------------------
# Der Workspace-Ordner ist die NFSv4-Wurzel (fsid=0). Zusaetzliche Host-Ordner
# werden per bind-mount UNTER diese Wurzel gehaengt (.fcmnt/<instanz>/<idx>),
# mit 'crossmnt' pro Guest-IP freigegeben und im Gast explizit gemountet.
AGENT_ROOT = os.environ.get("AGENT_ROOT", "/home/ulrich/agent")
AGENT_EXPORTS = "/etc/exports.d/agent.exports"
EXPORTS_D = "/etc/exports.d"
FCMNT_ROOT = os.path.join(AGENT_ROOT, ".fcmnt")

os.makedirs(RUN_DIR, exist_ok=True)


def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


# ---- instances -------------------------------------------------------------
def load_instances():
    out = []
    for f in sorted(os.listdir(INST_DIR)) if os.path.isdir(INST_DIR) else []:
        if f.endswith(".json"):
            with open(os.path.join(INST_DIR, f)) as fh:
                out.append(json.load(fh))
    return out


_ormodels = {"ts": 0.0, "data": []}


# "Relevant" = kuratierte Flagship-Modelle (exakte IDs). Es werden nur die
# angezeigt, die aktuell im OpenRouter-Katalog existieren. Bei Bedarf ergaenzen.
CURATED = {
    "openai/gpt-4o", "openai/gpt-4o-mini", "openai/gpt-4.1", "openai/gpt-4.1-mini",
    "openai/o3", "openai/o4-mini", "openai/gpt-5", "openai/gpt-5-mini",
    "anthropic/claude-3.7-sonnet", "anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-haiku",
    "anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4.5", "anthropic/claude-opus-4.1",
    "google/gemini-2.0-flash-001", "google/gemini-2.5-pro", "google/gemini-2.5-flash",
    "deepseek/deepseek-chat", "deepseek/deepseek-r1", "deepseek/deepseek-chat-v3.1",
    "deepseek/deepseek-v4-flash-0731",
    "meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-4-maverick",
    "mistralai/mistral-large", "mistralai/mistral-small",
    "qwen/qwen-2.5-72b-instruct", "qwen/qwen3-coder", "x-ai/grok-3", "x-ai/grok-4",
}


MODELS_FILE = os.path.join(BASE, "models.json")
CHANGELOG_FILE = os.path.join(BASE, "CHANGELOG.md")
SECURITY_FILE = os.path.join(BASE, "security.json")


def load_changelog():
    try:
        with open(CHANGELOG_FILE) as fh:
            return fh.read()
    except OSError:
        return "# Changelog\n\n(no entries yet)"


def load_security():
    try:
        with open(SECURITY_FILE) as fh:
            d = json.load(fh)
        items = d.get("issues") if isinstance(d, dict) else d
        return items if isinstance(items, list) else []
    except (FileNotFoundError, ValueError):
        return []


def save_security(items):
    """Nur den Status umschalten — Text und Bewertung kommen aus der Datei, die
    UI soll keine Befunde umschreiben koennen."""
    cur = {i.get("id"): i for i in load_security()}
    n = 0
    for upd in items if isinstance(items, list) else []:
        it = cur.get(upd.get("id"))
        if it and upd.get("status") in ("open", "done") and it.get("status") != upd["status"]:
            it["status"] = upd["status"]
            n += 1
    with open(SECURITY_FILE, "w") as fh:
        json.dump({"issues": list(cur.values())}, fh, indent=2, ensure_ascii=False)
    return f"{n} entry/entries updated"


def load_curated():
    """Die kuratierte Auswahl fuers Anlege-Formular. Liegt als Datei vor, damit
    ein neues Modell ueber den Models-Tab hereinkommt statt ueber einen Edit an
    CURATED + Neustart. Fehlt die Datei, ist CURATED die Erstbefuellung."""
    try:
        with open(MODELS_FILE) as fh:
            data = json.load(fh)
        ids = data.get("curated") if isinstance(data, dict) else data
        if isinstance(ids, list):
            return {str(i) for i in ids if i}
    except (FileNotFoundError, ValueError, AttributeError):
        pass
    return set(CURATED)


def save_curated(ids):
    clean = sorted({str(i).strip() for i in ids if str(i).strip()})
    with open(MODELS_FILE, "w") as fh:
        json.dump({"curated": clean}, fh, indent=2)
    return f"{len(clean)} models in the shortlist"


def openrouter_models(force=False, tools_only=False, relevant_only=False):
    """OpenRouter-Modelle, preis-aufsteigend. 10 min gecacht; force umgeht Cache.
    tools_only -> nur Function/Tool-Calling; relevant_only -> nur die kuratierten."""
    if force or time.time() - _ormodels["ts"] >= 600 or not _ormodels["data"]:
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                         headers={"User-Agent": "kaim56"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
            rows = []
            for m in d.get("data", []):
                p = m.get("pricing", {}) or {}
                try:
                    pr, co = float(p.get("prompt", 0)), float(p.get("completion", 0))
                except (TypeError, ValueError):
                    continue
                if pr < 0 or co < 0:
                    continue  # Auto-Router / dynamische Preise ausblenden
                sp = m.get("supported_parameters") or []
                rows.append((pr + co, pr, co, m.get("id", ""), "tools" in sp,
                             m.get("name", ""), m.get("context_length") or 0))
            rows.sort(key=lambda r: r[0])
            out = []
            for tot, pr, co, mid, tools, name, ctx in rows:
                if mid:
                    tag = "free" if tot == 0 else f"${pr*1e6:.2f}/${co*1e6:.2f} /1M"
                    out.append({"id": mid, "label": f"{mid}  ({tag})", "tools": tools,
                                "name": name, "ctx": ctx, "price": tag})
            if out:
                _ormodels["ts"], _ormodels["data"] = time.time(), out
        except Exception:
            pass
    data = _ormodels["data"]
    if tools_only:
        data = [m for m in data if m.get("tools")]
    if relevant_only:
        cur = load_curated()
        data = [m for m in data if m["id"] in cur]
    return data


def load_settings():
    try:
        with open(SETTINGS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def settings_for_ui():
    d = dict(load_settings())
    for k in list(d):
        if k in SECRET_PARAMS and d[k]:
            d[k] = SETTINGS_KEEP
    return d


def save_settings(d):
    cur = load_settings()
    cur.update({k: v for k, v in d.items()
                if isinstance(k, str) and v != SETTINGS_KEEP})
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(cur, fh, indent=2)
    try:
        os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass
    return "saved"


# ---- Chat-Verlauf (Sync mit der App) ---------------------------------------
CHATS_FILE = os.path.join(BASE, "chats.json")


def load_chats():
    try:
        with open(CHATS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return []


def save_chats(data):
    if not isinstance(data, list):
        return -1
    try:
        with open(CHATS_FILE, "w") as fh:
            json.dump(data, fh)
        bump_chats_rev()          # wartende Long-Polls (App/Web) sofort wecken
        return len(data)
    except OSError:
        return -1


# Live-Sync: jedes Schreiben am Chat-Store zaehlt eine Revision hoch. App und
# Web haengen mit ?since=<rev>&wait=<sek> am Long-Poll und sehen die Nachricht
# der jeweils anderen Seite in Sekundenbruchteilen — ohne Neuladen, ohne
# Dauer-Polling. Ohne die Parameter antwortet /api/chats wie bisher (Liste),
# damit aeltere Clients unveraendert weiterlaufen.
_chats_cv = threading.Condition()
try:
    _chats_rev = int(os.path.getmtime(CHATS_FILE) * 1000)
except OSError:
    _chats_rev = 0


def bump_chats_rev():
    global _chats_rev
    with _chats_cv:
        # Zeitbasiert, aber streng monoton: ueberlebt einen Manager-Neustart,
        # ohne dass ein Client mit altem `since` haengen bleibt.
        _chats_rev = max(_chats_rev + 1, int(time.time() * 1000))
        _chats_cv.notify_all()


def wait_chats(since, timeout):
    """(rev, chats|None) — die Liste nur, wenn sich seit `since` etwas getan
    hat, sonst None (Zeitablauf). Blockiert hoechstens `timeout` Sekunden."""
    deadline = time.time() + max(0.0, timeout)
    with _chats_cv:
        while _chats_rev <= since:
            rest = deadline - time.time()
            if rest <= 0:
                break
            _chats_cv.wait(min(1.0, rest))
        rev = _chats_rev
    return rev, (load_chats() if rev > since else None)


def chat_log_append(inst_name, sender, user_text, reply_text, kind="signal"):
    """Einen Turn (Frage + Antwort) an die gemeinsame Chat-Historie haengen,
    damit er in App und Web auftaucht. `kind`='signal' -> eine Konversation pro
    (Instanz,Sender); 'task' -> eine Task-Konversation pro Instanz."""
    if kind == "task":
        cid = f"task-{inst_name}"
        title = f"Tasks · {inst_name}"
    else:
        sid = re.sub(r"[^a-zA-Z0-9]", "", (sender or "signal"))[:20] or "signal"
        cid = f"sig-{inst_name}-{sid}"
        title = f"Signal · {inst_name}"
    chats = load_chats()
    conv = next((c for c in chats if isinstance(c, dict) and c.get("id") == cid), None)
    now = int(time.time() * 1000)
    if conv is None:
        conv = {"id": cid, "title": title, "mode": "server",
                "instance": inst_name, "messages": [], "updatedAt": now}
        chats.append(conv)
    if user_text:
        conv["messages"].append({"user": True, "text": str(user_text)})
    if reply_text:
        conv["messages"].append({"user": False, "text": str(reply_text)})
    conv["messages"] = conv["messages"][-500:]
    conv["updatedAt"] = now
    return save_chats(chats)


INBOX_WM_FILE = os.path.join(BASE, "inbox_wm.json")


def _inbox_wm():
    try:
        with open(INBOX_WM_FILE) as fh:
            return int(json.load(fh).get("ts", 0))
    except (OSError, ValueError):
        return 0


def inbox_since(peek=False):
    """Neue Nutzer-Nachrichten aus dem gemeinsamen Chat-Store (Signal/App/Web)
    seit dem letzten Lauf — als Posteingang fuer den Orchestrator. Wasserzeichen
    ueber conversation.updatedAt: jede Konversation mit neuer Aktivitaet wird
    einmal geliefert (letzte Nutzer-Nachricht). Task-Konversationen (Ergebnisse)
    werden ausgeblendet. peek=True liefert, ohne das Wasserzeichen zu setzen."""
    wm = _inbox_wm()
    items, maxts = [], wm
    for c in load_chats():
        if not isinstance(c, dict) or str(c.get("id", "")).startswith("task-"):
            continue
        ut = int(c.get("updatedAt", 0) or 0)
        if ut <= wm:
            continue
        maxts = max(maxts, ut)
        last_user = next((m.get("text", "") for m in reversed(c.get("messages", []) or [])
                          if m.get("user")), "")
        if last_user:
            items.append({"instance": c.get("instance", ""), "title": c.get("title", ""),
                          "id": c.get("id", ""), "text": last_user})
    if not peek and maxts > wm:
        try:
            with open(INBOX_WM_FILE, "w") as fh:
                json.dump({"ts": maxts}, fh)
        except OSError:
            pass
    return items


def merge_chats(incoming):
    """Eingehende Chats mit dem Bestand VEREINEN statt zu ersetzen — damit ein
    Push von App ODER Web-UI die Chats des jeweils anderen nicht ueberschreibt.
    Zusammengefuehrt wird pro id, der neuere updatedAt gewinnt. Leere Chats
    (ohne Nachrichten) werden nicht gespeichert. Hinweis: geloeschte Chats
    kommen so nicht 'weg' — Loesch-Sync braeuchte Tombstones (bewusst offen)."""
    by_id = {}
    for c in load_chats():
        if isinstance(c, dict) and c.get("id") and c.get("messages"):
            by_id[str(c["id"])] = c
    for c in incoming if isinstance(incoming, list) else []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if not c.get("messages"):
            continue
        cid = str(c["id"])
        cur = by_id.get(cid)
        if cur is None or c.get("updatedAt", 0) >= cur.get("updatedAt", 0):
            by_id[cid] = c
    merged = sorted(by_id.values(), key=lambda x: x.get("updatedAt", 0), reverse=True)
    return save_chats(merged)


# ---- Hintergrundaufgaben (Task-Queue + Scheduler) --------------------------
TASKS_FILE = os.path.join(BASE, "tasks.json")

# ---- History (SQLite, stdlib): abfragbares Langzeitgedaechtnis ueber Aufgaben ----
# Jeder ausgefuehrte Task landet hier; Agenten fragen ihn per recall_tasks ab
# ("haben wir das schon gemacht?" -> keine Dubletten, Stammwissen).
HISTORY_DB = os.path.join(BASE, "history.db")
_hist_lock = threading.Lock()


def _hist_conn():
    c = sqlite3.connect(HISTORY_DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS task_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, target TEXT, task TEXT, result TEXT, ok INTEGER,
        schedule TEXT, origin TEXT)""")
    return c


def history_add(target, task, result, ok, schedule="", origin=""):
    try:
        with _hist_lock, _hist_conn() as c:
            c.execute("INSERT INTO task_runs(ts,target,task,result,ok,schedule,origin) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (int(time.time()), str(target)[:80], str(task)[:2000],
                       str(result)[:8000], 1 if ok else 0, str(schedule)[:40], str(origin)[:80]))
    except Exception as e:
        print("history_add:", repr(e), flush=True)


def history_search(q="", limit=20):
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    try:
        with _hist_conn() as c:
            c.row_factory = sqlite3.Row
            if q:
                like = f"%{q}%"
                rows = c.execute(
                    "SELECT ts,target,task,result,ok,schedule FROM task_runs "
                    "WHERE task LIKE ? OR result LIKE ? OR target LIKE ? "
                    "ORDER BY ts DESC LIMIT ?", (like, like, like, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts,target,task,result,ok,schedule FROM task_runs "
                    "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print("history_search:", repr(e), flush=True)
        return []
_tasks_lock = threading.Lock()


def load_tasks():
    try:
        with open(TASKS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return []


def save_tasks(tasks):
    with _tasks_lock:
        tmp = TASKS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(tasks, fh, indent=2)
        os.replace(tmp, TASKS_FILE)


def add_task(instance, message, schedule=""):
    schedule = (schedule or "").strip()
    t = {"id": uuid.uuid4().hex[:12], "instance": instance, "message": message,
         "schedule": schedule, "status": "scheduled" if schedule else "pending",
         "result": "", "created": int(time.time()), "updated": int(time.time()),
         "next_run": _next_run(schedule, int(time.time())) if schedule else int(time.time())}
    tasks = load_tasks()
    tasks.append(t)
    save_tasks(tasks)
    return t


def _next_run(schedule, from_ts):
    """Nächster Ausführungszeitpunkt (epoch) für eine Zeitplan-Angabe.
    Formate: 'every 30m' | 'every 2h' | 'every 1d' | 'daily HH:MM' | 'hourly'."""
    s = (schedule or "").strip().lower()
    m = re.match(r"every\s+(\d+)\s*([mhd])", s)
    if m:
        mult = {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return from_ts + int(m.group(1)) * mult
    m = re.match(r"daily\s+(\d{1,2}):(\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        lt = time.localtime(from_ts)
        cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
        if cand <= from_ts:
            cand += 86400
        return int(cand)
    if s == "hourly":
        return from_ts + 3600
    return from_ts + 3600


def _chat_post(inst, message, timeout=600):
    """Nicht-streamender Chat-Aufruf an die Bridge einer Instanz."""
    url = f"http://{net_of(inst)['guest']}:{WEB_GUEST_PORT}/api/chat"
    data = json.dumps({"message": message}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    try:
        return json.loads(body).get("reply", body)
    except ValueError:
        return body


def _run_named(instance, message):
    """Aufgabe auf einer BESTEHENDEN Instanz ausfuehren (dort leben ihre
    Tools/MCP/Secrets). Startet sie bei Bedarf und wartet, bis die Bridge da ist."""
    inst = next((i for i in load_instances() if i["name"] == instance), None)
    if not inst:
        return (False, f"Instanz '{instance}' unbekannt")
    if not is_running(inst):
        if not wait_web(inst, timeout=120):
            return (False, f"Instanz '{instance}' nicht bereit")
        inst = next((i for i in load_instances() if i["name"] == instance), None)
    try:
        return (True, _chat_post(inst, message))
    except Exception as e:
        return (False, f"error: {e!r}")


def _run_ephemeral(message, model=None):
    """Aufgabe in einer FRISCHEN, isolierten VM ausfuehren, die danach wieder
    geloescht wird. Fuer isolierte/unabhaengige Arbeit — nicht fuer Aufgaben,
    die einen bestimmten MCP/Token brauchen (die gehoeren auf ihre Instanz)."""
    name = "task-" + uuid.uuid4().hex[:6]
    cfg = {"TRANSPORT": "web", "NO_SPAWN": "1"}
    if model:
        cfg["OPENROUTER_MODEL"] = model
    msg = create_instance(name, "openrouter", cfg)
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return (False, f"Ephemere VM fehlgeschlagen: {msg}")
    try:
        if not wait_web(inst, timeout=120):
            return (False, "Ephemere VM nicht bereit")
        return (True, _chat_post(inst, message))
    except Exception as e:
        return (False, f"error: {e!r}")
    finally:
        try:
            stop(inst)
            delete_instance(name)
        except Exception:
            pass


def _run_task_now(instance, message):
    """Aufgabe ausfuehren — auf einer benannten Instanz (Routing zur Faehigkeit)
    oder in einer ephemeren VM (target == 'ephemeral')."""
    if instance == "ephemeral":
        return _run_ephemeral(message)
    return _run_named(instance, message)


# ---- Sofort-Trigger fuer den Orchestrator ----------------------------------
# Neue Nutzer-Nachricht (Signal/App/Web) -> Orchestrator laeuft debounced in
# Sekunden statt erst beim naechsten 2-h-Heartbeat. Coalesct Bursts, ein Lauf
# zur Zeit; kamen waehrend des Laufs neue Nachrichten, wird direkt nochmal
# gefeuert. Feuert nur, wenn der Posteingang wirklich Neues hat (peek).
ORCH_INSTANCE = "orchestrator"
ORCH_HEARTBEAT_MSG = (
    "Heartbeat (Sofort-Trigger): 1) read_inbox — neue Nutzer-Nachrichten. "
    "2) Fuer jede mit Handlungsbedarf: recall_tasks (keine Dubletten), dann "
    "list_agents und create_task an die FAEHIGE Instanz (z. B. hass fuer "
    "HomeAssistant) oder ephemeral. Kurz halten. Nichts zu tun? Melde: nichts zu tun.")
_orch_lock = threading.Lock()
_orch_timer = [None]
_orch_running = [False]
_orch_dirty = [False]


def orchestrator_ping():
    if not any(i.get("name") == ORCH_INSTANCE for i in load_instances()):
        return
    try:
        if not inbox_since(peek=True):   # nur feuern, wenn wirklich Neues da ist
            return
    except Exception:
        return
    with _orch_lock:
        if _orch_timer[0]:
            _orch_timer[0].cancel()
        t = threading.Timer(8.0, _orch_fire)
        t.daemon = True
        _orch_timer[0] = t
        t.start()


def _orch_fire():
    with _orch_lock:
        if _orch_running[0]:
            _orch_dirty[0] = True
            return
        _orch_running[0] = True
    try:
        _run_named(ORCH_INSTANCE, ORCH_HEARTBEAT_MSG)
    except Exception as e:
        print("orch-trigger:", repr(e), flush=True)
    finally:
        with _orch_lock:
            _orch_running[0] = False
            rerun = _orch_dirty[0]
            _orch_dirty[0] = False
        if rerun:
            orchestrator_ping()


def _task_worker():
    """Verarbeitet fällige/anstehende Tasks sequentiell im Hintergrund."""
    while True:
        ran = False
        try:
            tasks = load_tasks()
            now = int(time.time())
            for t in tasks:
                if t.get("status") == "running":
                    continue
                sched = bool(t.get("schedule"))
                if sched:
                    if t.get("next_run", 0) > now:
                        continue
                elif t.get("status") != "pending":
                    continue
                # Task beanspruchen
                t["status"] = "running"
                t["updated"] = now
                save_tasks(tasks)
                ok, res = _run_task_now(t["instance"], t["message"])
                fresh = load_tasks()
                tt = next((x for x in fresh if x["id"] == t["id"]), None)
                if tt is not None:
                    tt["updated"] = int(time.time())
                    tt["result"] = res
                    if sched:
                        tt["status"] = "scheduled"
                        tt["next_run"] = _next_run(tt["schedule"], int(time.time()))
                    else:
                        tt["status"] = "done" if ok else "error"
                    save_tasks(fresh)
                # Ergebnis in die gemeinsame Chat-Historie (App/Web/Signal sehen es).
                try:
                    chat_log_append(t.get("instance", "task"), "task",
                                    t.get("message", ""), res, kind="task")
                except Exception:
                    pass
                history_add(t.get("instance", ""), t.get("message", ""), res, ok,
                            t.get("schedule", ""), origin="worker")
                ran = True
                break
        except Exception as e:
            print("task-worker:", repr(e), flush=True)
        if not ran:
            time.sleep(5)


def load_templates():
    out = []
    for f in sorted(os.listdir(TEMPLATE_DIR)) if os.path.isdir(TEMPLATE_DIR) else []:
        if f.endswith(".json"):
            with open(os.path.join(TEMPLATE_DIR, f)) as fh:
                out.append(json.load(fh))
    return out


def next_index():
    used = {i.get("index", 0) for i in load_instances()}
    n = 1
    while n in used:
        n += 1
    return n


# Werkzeug-Katalog fuer die UI (spiegelt BUILTIN im openrouter-Agenten). Nur
# Anzeige/Allowlist — die Ausfuehrung filtert der Agent nochmal selbst.
AGENT_TOOLS_CATALOG = [
    {"name": "bash", "desc": "Shell-Befehle im Workspace ausführen"},
    {"name": "read_file", "desc": "Datei lesen"},
    {"name": "write_file", "desc": "Datei schreiben"},
    {"name": "list_dir", "desc": "Verzeichnis auflisten"},
    {"name": "http_fetch", "desc": "URL abrufen (HTTP)"},
    {"name": "read_pdf", "desc": "PDF-Text extrahieren (Datei oder URL)"},
    {"name": "web_search", "desc": "Websuche (DuckDuckGo) — braucht Internet"},
    {"name": "spawn_subagent", "desc": "Ephemeren Subagenten starten"},
    {"name": "create_task", "desc": "Aufgabe einreihen (faehige Instanz oder ephemer)"},
    {"name": "read_inbox", "desc": "Neue Nutzer-Nachrichten (Signal/App/Web) lesen"},
    {"name": "list_agents", "desc": "Verfuegbare Agenten + Faehigkeiten (Routing)"},
    {"name": "recall_tasks", "desc": "Fruehere Aufgaben/Ergebnisse abfragen (Stammwissen)"},
    {"name": "list_skills", "desc": "Verfügbare Skills auflisten"},
    {"name": "load_skill", "desc": "Skill in den Kontext laden"},
    {"name": "memory_store", "desc": "Wert dauerhaft merken"},
    {"name": "memory_recall", "desc": "Gemerkten Wert abrufen"},
    {"name": "remote_ls", "desc": "katfs-Freigabe auflisten"},
    {"name": "remote_read", "desc": "katfs-Datei lesen"},
    {"name": "remote_write", "desc": "katfs-Datei schreiben"},
    {"name": "remote_delete", "desc": "katfs-Datei/Ordner löschen"},
    {"name": "list_secrets", "desc": "Freigegebene Secret-Namen zeigen"},
    {"name": "get_secret", "desc": "Freigegebenes Secret holen"},
]
AGENT_TOOL_NAMES = {t["name"] for t in AGENT_TOOLS_CATALOG}


def create_instance(name, template, config=None, mounts=None, internet=True):
    name = "".join(c for c in name if c.isalnum() or c in "-_").lower()
    if not name:
        return "invalid name"
    if any(i["name"] == name for i in load_instances()):
        return f"'{name}' already exists"
    tpl = next((t for t in load_templates() if t.get("template") == template), None)
    if not tpl:
        return f"unknown template '{template}'"
    # defaults aus template.params, überschrieben von übergebener config,
    # leere Werte aus den geteilten Settings vorbelegen
    cfg = {p["key"]: p.get("default", "") for p in tpl.get("params", [])}
    cfg.update({k: v for k, v in (config or {}).items() if v != ""})
    settings = load_settings()
    for k in list(cfg):
        if cfg[k] == "" and settings.get(k):
            cfg[k] = settings[k]
    for k in NEVER_PERSIST:
        cfg.pop(k, None)
    inst = {"name": name, "index": next_index(), "vcpus": tpl.get("vcpus", 2),
            "mem_mib": tpl.get("mem_mib", 1024), "rootfs": tpl["rootfs"],
            "internet": bool(internet),
            "description": f"{tpl.get('description','')} ({cfg.get('TRANSPORT','signal')}"
                           + (f", {cfg.get('FABRIC_MODEL')}" if cfg.get("FABRIC_MODEL") else "") + ")",
            "template": template, "config": cfg}
    clean = [{"host": str(m.get("host", "")).strip(),
              "guest": str(m.get("guest", "")).strip(),
              "readonly": bool(m.get("readonly"))}
             for m in (mounts or []) if isinstance(m, dict) and m.get("host") and m.get("guest")]
    if clean:
        inst["mounts"] = clean
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    return f"instance '{name}' created from template '{template}'"


def set_instance_tools(name, tools):
    """Werkzeug-Allowlist einer Instanz setzen. Leere/alle Liste -> Feld raus
    (= alle Tools). Wirkt beim naechsten Start (env-basiert)."""
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    sel = [t for t in (tools or []) if t in AGENT_TOOL_NAMES]
    cfg = inst.setdefault("config", {})
    if sel and set(sel) != AGENT_TOOL_NAMES:
        cfg["AGENT_TOOLS"] = ",".join(sorted(sel))
    else:
        cfg.pop("AGENT_TOOLS", None)
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    running = " (applies after stop/start)" if is_running(inst) else ""
    return f"tools for '{name}' saved{running}"


def set_internet(name, on):
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    inst["internet"] = bool(on)
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    if is_running(inst):
        apply_internet(inst, on)   # sofort wirksam, kein Neustart noetig
    return f"internet for '{name}': {'on' if on else 'off'}"


def delete_instance(name):
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if is_running(inst):
        stop(inst)
    teardown_mounts(inst)   # evtl. Reste (Binds/Export) sicher entfernen
    p = os.path.join(INST_DIR, f"{name}.json")
    if os.path.exists(p):
        os.remove(p)
    return f"instance '{name}' deleted"


def net_of(inst):
    i = inst["index"]
    return dict(host=f"172.30.{i}.1", guest=f"172.30.{i}.2", tap=f"fc{i}",
                mac=f"AA:FC:00:00:{i:02x}:01", mask="255.255.255.252")


def pidfile(inst):
    return os.path.join(RUN_DIR, f"{inst['name']}.pid")


def is_running(inst):
    pf = pidfile(inst)
    if not os.path.exists(pf):
        return False
    try:
        pid = int(open(pf).read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


# ---- networking ------------------------------------------------------------
def ensure_net_base():
    sh("sysctl", "-w", "net.ipv4.ip_forward=1", check=False)
    r = sh("iptables", "-t", "nat", "-C", "POSTROUTING", "-s", POOL, "-o", HOSTIF,
           "-j", "MASQUERADE", check=False)
    if r.returncode != 0:
        sh("iptables", "-t", "nat", "-A", "POSTROUTING", "-s", POOL, "-o", HOSTIF,
           "-j", "MASQUERADE", check=False)
    # Gast-Isolation: microVMs duerfen NICHT untereinander routen. Ein
    # kompromittierter Agent koennte sonst die Chat-/Term-Ports (8080/7682, an
    # 0.0.0.0 gebunden, ohne Auth) einer anderen Instanz erreichen. Backstop-
    # DROP fuer pool->pool; die Tap-ACCEPTs unten sind zusaetzlich so gefasst,
    # dass sie Gast-zu-Gast gar nicht erst treffen. Gast->Gateway (8700-Broker)
    # ist host-lokal (INPUT) und davon unberuehrt.
    if sh("iptables", "-C", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP",
          check=False).returncode != 0:
        sh("iptables", "-A", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP", check=False)


def setup_tap(inst):
    n = net_of(inst)
    sh("ip", "link", "del", n["tap"], check=False)
    sh("ip", "tuntap", "add", n["tap"], "mode", "tap")
    sh("ip", "addr", "add", f"{n['host']}/30", "dev", n["tap"])
    sh("ip", "link", "set", n["tap"], "up")
    # Der Host hat FORWARD-Policy DROP + Docker-Ketten davor -> generische Regeln
    # greifen nicht zuverlässig. Deshalb Tap-Traffic GANZ OBEN (vor DROP/Docker)
    # erlauben — aber NUR nach/von ausserhalb des Pools. So kommt der Gast ins
    # Internet (Ziel nicht im Pool) und Antworten zurueck (Quelle nicht im Pool),
    # waehrend Gast-zu-Gast (beide im Pool) durch keine ACCEPT-Regel faellt und
    # am pool->pool-DROP bzw. der DROP-Policy haengenbleibt.
    # Alte, unbeschraenkte ACCEPTs derselben Tap zuerst wegraeumen (Tap-Name wird
    # bei Neustart wiederverwendet, sonst bliebe das alte Loch offen).
    for spec in (["-i", n["tap"]], ["-o", n["tap"]]):
        while sh("iptables", "-C", "FORWARD", *spec, "-j", "ACCEPT", check=False).returncode == 0:
            sh("iptables", "-D", "FORWARD", *spec, "-j", "ACCEPT", check=False)
    apply_internet(inst, inst.get("internet", True))


def apply_internet(inst, allow):
    """Egress-Regeln der Instanz setzen/entfernen. `allow=False` heisst: die VM
    darf ihr eigenes /30 nicht verlassen — kein LAN, kein Internet. Der
    Manager-Broker am Gateway (8700) bleibt erreichbar (host-lokal, INPUT).
    Damit auch der LLM-Endpunkt: ein Agent ohne Internet kann NICHT denken."""
    n = net_of(inst)
    egress = (["-i", n["tap"], "!", "-d", POOL], ["-o", n["tap"], "!", "-s", POOL])
    for spec in egress:
        have = sh("iptables", "-C", "FORWARD", *spec, "-j", "ACCEPT", check=False).returncode == 0
        if allow and not have:
            sh("iptables", "-I", "FORWARD", "1", *spec, "-j", "ACCEPT", check=False)
        elif not allow and have:
            sh("iptables", "-D", "FORWARD", *spec, "-j", "ACCEPT", check=False)


def teardown_tap(inst):
    sh("ip", "link", "del", net_of(inst)["tap"], check=False)


# ---- host-ordner (NFS bind-mounts) -----------------------------------------
def ensure_agent_crossmnt():
    """Workspace-Export braucht 'crossmnt', damit die Host-Ordner-Submounts
    ueber NFSv4 sichtbar sind. Idempotent, mit einmaligem Backup."""
    try:
        cur = open(AGENT_EXPORTS).read() if os.path.exists(AGENT_EXPORTS) else ""
    except OSError:
        return
    if AGENT_ROOT in cur and "crossmnt" in cur:
        return
    line = (f"{AGENT_ROOT} {POOL}(rw,sync,no_subtree_check,all_squash,"
            f"anonuid=1000,anongid=1000,fsid=0,crossmnt)\n")
    try:
        if cur and not os.path.exists(AGENT_EXPORTS + ".bak"):
            open(AGENT_EXPORTS + ".bak", "w").write(cur)
        os.makedirs(EXPORTS_D, exist_ok=True)
        open(AGENT_EXPORTS, "w").write(line)
        sh("exportfs", "-ra", check=False)
    except OSError:
        pass


def mount_specs(inst):
    """Normierte Host-Ordner-Mounts: bind-Ziel, NFS-Subpfad, fsid, Modus."""
    specs = []
    for j, m in enumerate(inst.get("mounts", []) or []):
        host = str(m.get("host", "")).strip()
        guest = str(m.get("guest", "")).strip()
        if not host or not guest:
            continue
        specs.append({
            "idx": j, "host": host, "guest": guest,
            "ro": bool(m.get("readonly", False)),
            "target": os.path.join(FCMNT_ROOT, inst["name"], str(j)),
            "sub": f"/.fcmnt/{inst['name']}/{j}",
            "fsid": 4000 + (inst.get("index", 0) % 200) * 16 + (j % 16),
        })
    return specs


def write_desired(inst):
    """desired.list unter .fcmnt/<inst>/ schreiben — der Reconciler im Gast liest
    sie (ueber den Workspace-Mount) und haelt die Mounts live aktuell."""
    d = os.path.join(FCMNT_ROOT, inst["name"])
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "desired.list"), "w") as f:
            for s in mount_specs(inst):
                f.write(f"{s['sub']}|{s['guest']}|{'ro' if s['ro'] else 'rw'}\n")
    except OSError:
        pass


def setup_mounts(inst):
    specs = mount_specs(inst)
    if not specs:
        return
    ensure_agent_crossmnt()
    n = net_of(inst)
    lines = []
    for s in specs:
        if not os.path.isdir(s["host"]):
            continue  # fehlender Host-Ordner -> ueberspringen (nicht anlegen)
        os.makedirs(s["target"], exist_ok=True)
        sh("umount", "-l", s["target"], check=False)   # evtl. alten Bind loesen
        if sh("mount", "--bind", s["host"], s["target"], check=False).returncode != 0:
            continue
        if s["ro"]:
            sh("mount", "-o", "remount,ro,bind", s["target"], check=False)
        perm = "ro" if s["ro"] else "rw"
        lines.append(f"{s['target']} {n['guest']}({perm},sync,no_subtree_check,"
                     f"all_squash,anonuid=1000,anongid=1000,fsid={s['fsid']})\n")
    if lines:
        os.makedirs(EXPORTS_D, exist_ok=True)
        open(os.path.join(EXPORTS_D, f"fc-{inst['name']}.exports"), "w").writelines(lines)
        sh("exportfs", "-ra", check=False)
    write_desired(inst)   # Reconciler im Gast holt sich die Mounts


def teardown_mounts(inst):
    ef = os.path.join(EXPORTS_D, f"fc-{inst['name']}.exports")
    if os.path.exists(ef):
        os.remove(ef)
        sh("exportfs", "-ra", check=False)
    d = os.path.join(FCMNT_ROOT, inst["name"])
    if os.path.isdir(d):
        # tatsaechlichen Inhalt scannen (robust gegen Reste): Sub-Binds loesen,
        # dann leere Verzeichnisse entfernen (rmdir schlaegt bei Busy/Mount fehl).
        for sub in os.listdir(d):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                sh("umount", "-l", p, check=False)
        try:
            os.remove(os.path.join(d, "desired.list"))
        except OSError:
            pass
        for sub in os.listdir(d):
            try:
                os.rmdir(os.path.join(d, sub))
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def set_mounts(name, mounts):
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    old_specs = mount_specs(inst)
    inst["mounts"] = [{"host": str(m.get("host", "")).strip(),
                       "guest": str(m.get("guest", "")).strip(),
                       "readonly": bool(m.get("readonly"))}
                      for m in (mounts or [])
                      if isinstance(m, dict) and m.get("host") and m.get("guest")]
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    note = ""
    if is_running(inst):
        # LIVE anwenden: entfernte Ordner abbauen, aktuelle (neu) exportieren.
        new_subs = {s["sub"] for s in mount_specs(inst)}
        for s in old_specs:
            if s["sub"] not in new_subs:
                sh("umount", "-l", s["target"], check=False)
                try:
                    os.rmdir(s["target"])
                except OSError:
                    pass
        setup_mounts(inst)            # bind+export der aktuellen Ordner (idempotent)
        write_desired(inst)           # laufender Gast mountet sie selbst (Reconciler)
        note = " (applied live)"
    return f"{len(inst['mounts'])} host folders saved{note}"


# ---- firecracker lifecycle -------------------------------------------------
def make_config_disk(inst):
    """Kleines ext4-Laufwerk mit der Instanz-Config (key=value) erzeugen -> vdb."""
    cfg = dict(inst.get("config", {}))
    # Zweiter Riegel: aeltere Instanz-JSONs koennen Key/MCP_CONFIG noch
    # enthalten, auf die Disk duerfen sie trotzdem nicht.
    for k in NEVER_PERSIST:
        cfg.pop(k, None)
    # Minimaler VM-Init hat /usr/local/bin nicht im PATH -> injizieren, damit
    # claude/fabric gefunden werden (guest-init sourced die Config-Disk).
    cfg.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    cfg["FC_INSTANCE"] = inst["name"]   # fuer den Host-Ordner-Reconciler im Gast
    d = os.path.join(RUN_DIR, f"{inst['name']}.cfgdir")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.env"), "w") as f:
        for k, v in cfg.items():
            # Werte quoten (EXTRA_MOUNTS u.a. enthalten Shell-Metazeichen wie | und ;)
            f.write(f"{k}={shlex.quote(str(v))}\n")
    img = os.path.join(RUN_DIR, f"{inst['name']}.config.ext4")
    with open(img, "wb") as f:
        f.truncate(16 * 1024 * 1024)
    sh("mkfs.ext4", "-F", "-q", "-d", d, img, check=False)
    return img


def gen_config(inst):
    n = net_of(inst)
    boot = (f"console=ttyS0 reboot=k panic=1 pci=off "
            f"ip={n['guest']}::{n['host']}:{n['mask']}::eth0:off init=/init")
    drives = [{"drive_id": "rootfs", "path_on_host": os.path.join(BASE, inst["rootfs"]),
               "is_root_device": True, "is_read_only": False}]
    cfg_disk = os.path.join(RUN_DIR, f"{inst['name']}.config.ext4")
    if os.path.exists(cfg_disk):
        drives.append({"drive_id": "config", "path_on_host": cfg_disk,
                       "is_root_device": False, "is_read_only": True})
    for j, d in enumerate(inst.get("extra_drives", [])):
        drives.append({"drive_id": f"data{j}", "path_on_host": d["path"],
                       "is_root_device": False, "is_read_only": d.get("readonly", False)})
    return {
        "boot-source": {"kernel_image_path": KERNEL, "boot_args": boot},
        "drives": drives,
        "network-interfaces": [{"iface_id": "eth0", "host_dev_name": n["tap"],
                                "guest_mac": n["mac"]}],
        "machine-config": {"vcpu_count": inst.get("vcpus", 2),
                           "mem_size_mib": inst.get("mem_mib", 1024)},
    }


def start(inst):
    if is_running(inst):
        return "already running"
    ensure_net_base()
    setup_tap(inst)
    setup_mounts(inst)
    make_config_disk(inst)
    cfg = os.path.join(RUN_DIR, f"{inst['name']}.config.json")
    json.dump(gen_config(inst), open(cfg, "w"))
    sock = os.path.join(RUN_DIR, f"{inst['name']}.sock")
    log = open(os.path.join(RUN_DIR, f"{inst['name']}.log"), "ab")
    if os.path.exists(sock):
        os.remove(sock)
    p = subprocess.Popen([BIN, "--api-sock", sock, "--config-file", cfg],
                         stdout=log, stderr=log, start_new_session=True)
    open(pidfile(inst), "w").write(str(p.pid))
    return f"started (pid {p.pid})"


def stop(inst):
    pf = pidfile(inst)
    if os.path.exists(pf):
        try:
            os.kill(int(open(pf).read().strip()), signal.SIGTERM)
            time.sleep(1)
        except (ValueError, ProcessLookupError):
            pass
        os.remove(pf)
    teardown_tap(inst)
    teardown_mounts(inst)
    return "stopped"


# ---- Personas / System-Prompts --------------------------------------------
PERSONAS_FILE = os.path.join(BASE, "personas.json")
_DEFAULT_PERSONAS = [
    {"name": "assistent",
     "prompt": "Du bist ein hilfreicher Agent mit Tools (Shell, Dateien, Web, MCP). "
               "Nutze Tools wenn nötig, antworte sonst direkt. Fasse dich kurz."},
    {"name": "researcher",
     "prompt": "Du bist ein gründlicher Rechercheur. Nutze web_search und http_fetch, prüfe mehrere "
               "Quellen und nenne URLs als Belege. Fasse strukturiert zusammen. Bei umfangreichen "
               "Aufgaben nutze spawn_subagent, um Teilfragen parallel zu recherchieren."},
    {"name": "coder",
     "prompt": "Du bist ein erfahrener Software-Entwickler. Nutze bash/read_file/write_file im "
               "Workspace, arbeite in kleinen Schritten, teste dein Ergebnis und erkläre knapp, was du tust."},
    {"name": "uebersetzer",
     "prompt": "Du bist ein präziser Übersetzer. Übersetze natürlich und idiomatisch, ohne Kommentare, "
               "außer der Nutzer fragt ausdrücklich danach."},
]


def load_personas():
    try:
        with open(PERSONAS_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, ValueError):
        save_personas(_DEFAULT_PERSONAS)
        return list(_DEFAULT_PERSONAS)
    return list(_DEFAULT_PERSONAS)


def save_personas(items):
    if not isinstance(items, list):
        return -1
    try:
        with open(PERSONAS_FILE, "w") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
        return len(items)
    except OSError:
        return -1


def upsert_persona(name, prompt):
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    if not name:
        return "invalid name (only a-z 0-9 _ -)"
    items = [p for p in load_personas() if p.get("name") != name]
    items.append({"name": name, "prompt": prompt or ""})
    save_personas(items)
    return f"persona '{name}' saved"


def delete_persona(name):
    save_personas([p for p in load_personas() if p.get("name") != name])
    return f"persona '{name}' deleted"


# ---- Skills-Bibliothek (Experten-Wissen, on-demand vom Agenten geladen) -----
SKILLS_FILE = os.path.join(BASE, "skills.json")


def load_skills():
    try:
        with open(SKILLS_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, ValueError):
        pass
    return []


def save_skills(items):
    if not isinstance(items, list):
        return -1
    try:
        with open(SKILLS_FILE, "w") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
        return len(items)
    except OSError:
        return -1


def upsert_skill(name, description, content):
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    if not name:
        return "invalid name (only a-z 0-9 _ -)"
    items = [s for s in load_skills() if s.get("name") != name]
    items.append({"name": name, "description": description or "", "content": content or ""})
    save_skills(items)
    return f"skill '{name}' saved"


def delete_skill(name):
    save_skills([s for s in load_skills() if s.get("name") != name])
    return f"skill '{name}' deleted"


# ---- Agenten-Gedächtnis (persistent, je Instanz) ---------------------------
MEMORY_FILE = os.path.join(BASE, "memory.json")
_mem_lock = threading.Lock()


def load_memory():
    try:
        with open(MEMORY_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def mem_store(instance, key, value):
    if not instance or not key:
        return "instance/key missing"
    with _mem_lock:
        m = load_memory()
        m.setdefault(instance, {})[key] = value
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(m, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, MEMORY_FILE)
    return f"saved: {key}"


def mem_recall(instance, key=None):
    m = load_memory().get(instance, {})
    return m if key is None else m.get(key, "")


# ---- Secrets-Broker (on-demand, Allowlist pro Template/Instanz) ------------
SECRETS_FILE = os.environ.get("SECRETS_FILE", "/home/ulrich/.config/kat56/secrets.env")
SECRET_POLICY_FILE = os.path.join(BASE, "secret-policy.json")


def load_secrets_file():
    out = {}
    try:
        with open(SECRETS_FILE) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def secret_store():
    """Alle brokerbaren Geheimnisse. Quelle 1 ist der Secret-Store (0600).
    Quelle 2 sind die Manager-Settings — dort werden die LLM-Keys gepflegt, und
    seit sie nicht mehr in die Instanz-Config wandern, muss der Broker sie
    ausliefern. Der Store gewinnt bei Namensgleichheit."""
    out = dict(load_secrets_file())
    for k, v in load_settings().items():
        if k in SECRET_PARAMS and v and not out.get(k):
            out[k] = v
    return out


def load_secret_policy():
    try:
        with open(SECRET_POLICY_FILE) as fh:
            p = json.load(fh)
        if isinstance(p, dict):
            return p
    except (FileNotFoundError, ValueError):
        pass
    return {"by_template": {}, "by_instance": {}}


def instance_by_ip(ip):
    for i in load_instances():
        try:
            if net_of(i).get("guest") == ip:
                return i
        except Exception:
            continue
    return None


def allowed_secret_keys(inst):
    """Effektive Allowlist = by_template[template] ∪ by_instance[name]. Default deny."""
    if not inst:
        return set()
    pol = load_secret_policy()
    keys = set(pol.get("by_template", {}).get(inst.get("template", ""), []))
    keys |= set(pol.get("by_instance", {}).get(inst.get("name", ""), []))
    return keys


def save_secret_policy(pol):
    """Policy speichern (nur {by_template,by_instance} mit String-Listen)."""
    if not isinstance(pol, dict):
        return "invalid"
    clean = {"by_template": {}, "by_instance": {}}
    for grp in ("by_template", "by_instance"):
        src = pol.get(grp, {})
        if isinstance(src, dict):
            for k, v in src.items():
                if isinstance(v, list):
                    clean[grp][str(k)] = [str(x) for x in v if isinstance(x, str)]
    try:
        with open(SECRET_POLICY_FILE, "w") as fh:
            json.dump(clean, fh, indent=2)
        return "saved"
    except OSError as e:
        return f"error: {e}"


# ---- MCP-Katalog (auswählbare MCP-Server je Instanz) -----------------------
MCP_CATALOG_FILE = os.path.join(BASE, "mcp-catalog.json")
_DEFAULT_MCPS = [
    {"name": "homeassistant",
     "description": "Home Assistant (Lesen/Assist via MCP)",
     "command": "mcp-remote",
     "args": ["http://10.0.0.54:8123/mcp_server/sse", "--header",
              "Authorization: Bearer ${HA_TOKEN}", "--transport", "sse-only", "--allow-http"]},
]


def load_mcps():
    try:
        with open(MCP_CATALOG_FILE) as fh:
            d = json.load(fh)
        if isinstance(d, list):
            return d
    except (FileNotFoundError, ValueError):
        save_mcps(_DEFAULT_MCPS)
        return list(_DEFAULT_MCPS)
    return list(_DEFAULT_MCPS)


def save_mcps(items):
    if not isinstance(items, list):
        return -1
    try:
        with open(MCP_CATALOG_FILE, "w") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
        return len(items)
    except OSError:
        return -1


def upsert_mcp(name, description, command, args, env=None):
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    if not name or not command:
        return "invalid (name/command required)"
    args = [str(a) for a in args] if isinstance(args, list) else []
    env = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
    items = [m for m in load_mcps() if m.get("name") != name]
    items.append({"name": name, "description": description or "", "command": command, "args": args, "env": env})
    save_mcps(items)
    return f"MCP '{name}' saved"


def delete_mcp(name):
    save_mcps([m for m in load_mcps() if m.get("name") != name])
    return f"MCP '{name}' deleted"


def mcp_required_secrets(names):
    """Welche ${SECRET} die gewaehlten Katalog-Eintraege ueberhaupt brauchen."""
    cat = {m.get("name"): m for m in load_mcps()}
    need = set()
    for n in names:
        m = cat.get(n) or {}
        blob = json.dumps([m.get("args", []), m.get("env", {})])
        need |= set(re.findall(r"\$\{([A-Z0-9_]+)\}", blob))
    return need


def build_mcp_config(names, allowed=None):
    """mcpServers-JSON aus Katalognamen. `allowed` begrenzt die Substitution auf
    die per Policy freigegebenen Secrets — nicht Freigegebenes bleibt als
    ${PLATZHALTER} stehen, damit der Aufrufer es merkt statt still zu scheitern."""
    if not names:
        return ""
    secrets = secret_store()

    def sub(s):
        def one(m):
            k = m.group(1)
            if allowed is not None and k not in allowed:
                return m.group(0)
            return secrets.get(k, m.group(0))
        return re.sub(r"\$\{([A-Z0-9_]+)\}", one, str(s))

    cat = {m.get("name"): m for m in load_mcps()}
    servers = {}
    for n in names:
        m = cat.get(n)
        if m:
            entry = {"command": m.get("command", ""), "args": [sub(a) for a in m.get("args", [])]}
            envd = m.get("env") or {}
            if envd:
                entry["env"] = {k: sub(v) for k, v in envd.items()}
            servers[n] = entry
    return json.dumps({"mcpServers": servers}) if servers else ""


# ---- Host-Ordner durchsuchen (Folder-Picker der UI) ------------------------
# Nur Verzeichnisnamen, nie Dateiinhalte. Der Manager laeuft als root, sieht
# also alles — die Route ist wie /api/secret-keys admin-only (Gaeste per
# Source-IP gesperrt) und liegt hinter derselben Auth wie die UI.

def list_dirs(path, show_hidden=False):
    p = os.path.abspath(path or "/") or "/"
    parent = "" if p == "/" else os.path.dirname(p)
    if not os.path.isdir(p):
        return {"path": p, "parent": parent, "dirs": [], "error": "kein Verzeichnis"}
    try:
        dirs = sorted((e.name for e in os.scandir(p)
                       if e.is_dir(follow_symlinks=False)
                       and (show_hidden or not e.name.startswith("."))),
                      key=str.lower)
    except OSError as e:
        return {"path": p, "parent": parent, "dirs": [], "error": f"kein Zugriff ({e.strerror})"}
    return {"path": p, "parent": parent, "dirs": dirs}


# ---- katfs (P2P-Ordnerfreigabe aus dem Browser) ----------------------------
# Der katfs-Host-Knoten laeuft auf dem Host (Port 8790) und haelt die iroh-
# Verbindung zum freigebenden Browser-Tab. Die Agenten greifen ueber
# remote_ls/remote_read/remote_write auf <gateway>:8790 zu — nichts davon wird
# in die VM gemountet, katfs ist kein Dateisystem.
#
# Der Manager reicht die Freigabe-Seite unter /katfs/ durch: gleiche Herkunft,
# gleiche Auth — und vor allem HTTPS, das die File System Access API im
# Browser zwingend braucht (secure context). Damit entfaellt der SSH-Tunnel
# bzw. die eigene Traefik-Route aus iroh-fs/README.md.
KATFS_HOST = os.environ.get("KATFS_HOST", "127.0.0.1")
KATFS_PORT = int(os.environ.get("KATFS_PORT", "8790"))
KATFS_BASE = f"http://{KATFS_HOST}:{KATFS_PORT}"


KATFS_MAX_WRITE = 64 * 1024 * 1024   # Deckel gegen Platten-DoS im Operator-Ordner

# ---- Audit-Log pro Instanz (Tool-Aufrufe, URLs) ----------------------------
# Liegt auf dem Host (ueberlebt VM-Neustarts). JSONL, pro Instanz eine Datei,
# hart auf die letzten N Zeilen begrenzt.
AUDIT_DIR = os.path.join(BASE, "audit")
AUDIT_MAX_LINES = 2000


def audit_append(inst_name, tool, target, ok):
    os.makedirs(AUDIT_DIR, exist_ok=True)
    p = os.path.join(AUDIT_DIR, f"{inst_name}.jsonl")
    rec = {"ts": int(time.time()), "tool": str(tool)[:64],
           "target": str(target)[:400], "ok": bool(ok)}
    with open(p, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # gelegentlich kappen, damit die Datei nicht unbegrenzt waechst
    try:
        with open(p) as fh:
            lines = fh.readlines()
        if len(lines) > AUDIT_MAX_LINES + 200:
            with open(p, "w") as fh:
                fh.writelines(lines[-AUDIT_MAX_LINES:])
    except OSError:
        pass


def effective_policy(inst):
    """Alles, was eine Instanz DARF, an einer Stelle: Netz, Werkzeuge, Secrets,
    MCP-Server, Modell. Zieht die verstreuten Kontrollen (Instanz-Config,
    secret-policy) zu einer Ansicht zusammen."""
    cfg = inst.get("config") or {}
    at = cfg.get("AGENT_TOOLS", "")
    tools_allowed = [t.strip() for t in at.split(",") if t.strip()] if at else None  # None = alle
    model = cfg.get("OPENROUTER_MODEL") or cfg.get("PI_MODEL") or cfg.get("PRIME_MODEL") or ""
    mcps = [n for n in (cfg.get("MCP_SERVERS", "") or "").split(",") if n]
    return {
        "name": inst["name"],
        "template": inst.get("template", ""),
        "running": is_running(inst),
        "internet": inst.get("internet", True),
        "model": model,
        "tools_all": tools_allowed is None,
        "tools": tools_allowed if tools_allowed is not None else [t["name"] for t in AGENT_TOOLS_CATALOG],
        "secrets": sorted(allowed_secret_keys(inst)),
        "mcps": mcps,
        "katfs_share": cfg.get("KATFS_SHARE", ""),
    }


def audit_read(inst_name, limit=200):
    p = os.path.join(AUDIT_DIR, f"{inst_name}.jsonl")
    try:
        with open(p) as fh:
            lines = fh.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return list(reversed(out))   # neueste zuerst


def katfs_share_for(inst):
    """Die Freigabe, die diese Instanz benutzen DARF. Genau die aus ihrer Config
    — nie eine vom Gast mitgegebene. Leer heisst: der Knoten entscheidet, was er
    nur kann, solange hoechstens eine Freigabe aktiv ist."""
    return (inst.get("config", {}).get("KATFS_SHARE", "") or "").strip()


def katfs_proxy_fs(op, share, path, recursive=False, body=None):
    """Eine Dateioperation an den (jetzt loopback-gebundenen) Knoten weiterreichen.
    Der Aufrufer hat die Instanz bereits per Source-IP verifiziert und die Freigabe
    aus deren Config gesetzt — der Gast kann keine fremde Freigabe adressieren."""
    q = f"?path={urllib.parse.quote(path)}"
    if share:
        q += f"&share={urllib.parse.quote(share)}"
    if op == "delete" and recursive:
        q += "&recursive=1"
    method = "POST" if op in ("write", "delete") else "GET"
    req = urllib.request.Request(KATFS_BASE + "/" + op + q,
                                 data=(body if op == "write" else b"") if method == "POST" else None,
                                 method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()


def katfs_status():
    out = {"up": False, "connected": False, "share": "", "node_id": "",
           "port": KATFS_PORT, "error": "", "shares": []}
    try:
        with urllib.request.urlopen(KATFS_BASE + "/status", timeout=3) as r:
            out.update(json.loads(r.read().decode()))
        out["up"] = True
    except Exception as e:
        out["error"] = f"{e}"
        return out
    try:
        with urllib.request.urlopen(KATFS_BASE + "/nodeid", timeout=3) as r:
            out["node_id"] = json.loads(r.read().decode()).get("node_id", "")
    except Exception:
        pass
    # /shares gibt es erst ab dem Multi-Share-Knoten; ein aelterer antwortet 404.
    try:
        with urllib.request.urlopen(KATFS_BASE + "/shares", timeout=3) as r:
            out["shares"] = json.loads(r.read().decode()).get("shares", [])
    except Exception:
        out["shares"] = []
    return out


# ---- web -------------------------------------------------------------------
PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>kAIm56</title>
<link rel=icon type="image/svg+xml" href="/logo.svg">
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600&display=swap');
/* ── Industry — design-system tokens (claude.ai/design) ─────────────────── */
:root{
  --color-bg:#f2f2f3; --color-surface:#e9e9ea; --color-text:#1d1f20;
  --color-accent:#5980a6; --color-accent-2:#728fab;
  --color-divider:color-mix(in srgb,#1d1f20 16%,transparent);
  --color-neutral-100:#f5f5f8;--color-neutral-200:#e7e7ea;--color-neutral-300:#d4d4d7;
  --color-neutral-400:#b7b7ba;--color-neutral-500:#98989b;--color-neutral-600:#7a7a7d;
  --color-neutral-700:#5d5d60;--color-neutral-800:#424244;--color-neutral-900:#2b2b2d;
  --color-accent-100:#eef6ff;--color-accent-200:#d6ebff;--color-accent-300:#b5d9fd;
  --color-accent-400:#94bce3;--color-accent-500:#749dc4;--color-accent-600:#597ea3;
  --color-accent-700:#416180;--color-accent-800:#2c455d;--color-accent-900:#1d2d3d;
  --font-heading:"Barlow Condensed",system-ui,sans-serif; --font-heading-weight:600;
  --font-body:"Barlow",system-ui,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --space-1:3.4px;--space-2:6.8px;--space-3:10.2px;--space-4:13.6px;--space-6:20.4px;--space-8:27.2px;
  --radius-sm:2px;--radius-md:4px;--radius-lg:7px;
  --shadow-sm:0 1px 2px color-mix(in srgb,#2b2b2d 14%,transparent);
  --shadow-md:0 3px 10px color-mix(in srgb,#2b2b2d 16%,transparent);
  --shadow-lg:0 12px 32px color-mix(in srgb,#2b2b2d 22%,transparent);
  --color-ok:#416180;
}
/* Dark rendering of the same system — same roles, ground flipped. */
@media(prefers-color-scheme:dark){:root{
  --color-bg:#141618; --color-surface:#1c1f22; --color-text:#e8e9ea;
  --color-accent:#94bce3; --color-accent-2:#9ebbd8;
  --color-divider:color-mix(in srgb,#e8e9ea 18%,transparent);
  --color-neutral-100:#212326;--color-neutral-200:#2b2d31;--color-neutral-300:#3a3d41;
  --color-neutral-400:#4e5155;--color-neutral-500:#6b6e73;--color-neutral-600:#8b8e93;
  --color-neutral-700:#a9acb1;--color-neutral-800:#c8cacd;--color-neutral-900:#e8e9ea;
  --color-accent-100:#1d2d3d;--color-accent-200:#2c455d;--color-accent-300:#416180;
  --color-accent-400:#597ea3;--color-accent-500:#749dc4;--color-accent-600:#94bce3;
  --color-accent-700:#b5d9fd;--color-accent-800:#d6ebff;--color-accent-900:#eef6ff;
  --color-ok:#b5d9fd;
}}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font-family:var(--font-body);font-size:15px;line-height:1.55;font-weight:400;
  background:var(--color-bg);color:var(--color-text);-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,h5,h6{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  line-height:1.12;letter-spacing:-.015em;margin:0 0 var(--space-2)}
h1{font-size:42px}h2{font-size:32px}h3{font-size:25px}h4{font-size:20px}h5{font-size:16px}
h6{font-size:13px;letter-spacing:.08em;text-transform:uppercase}
p{margin:0 0 var(--space-3)}
a{color:var(--color-accent);text-underline-offset:3px}
a:hover{color:var(--color-accent-700)}
.text-muted{color:color-mix(in srgb,var(--color-text) 55%,transparent)}
:focus{outline:none}
:focus-visible{outline:2px solid var(--color-accent);outline-offset:2px}
::selection{background:color-mix(in srgb,var(--color-accent) 30%,transparent)}
code{font-family:var(--font-mono);font-size:.88em;background:var(--color-neutral-100);
  border:1px solid var(--color-divider);padding:.02rem .3rem}
/* — blueprint frame — */
.blueprint{position:relative;border:1px solid var(--color-divider);border-radius:0}
.blueprint>.corner{position:absolute;width:11px;height:11px;
  color:color-mix(in srgb,var(--color-text) 55%,transparent)}
.blueprint>.corner::before,.blueprint>.corner::after{content:"";position:absolute;background:currentColor}
.blueprint>.corner::before{left:5px;top:0;width:1px;height:100%}
.blueprint>.corner::after{top:5px;left:0;width:100%;height:1px}
.blueprint>.corner.tl{top:-6px;left:-6px}.blueprint>.corner.tr{top:-6px;right:-6px}
.blueprint>.corner.bl{bottom:-6px;left:-6px}.blueprint>.corner.br{bottom:-6px;right:-6px}
/* — buttons — */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;cursor:pointer;
  text-decoration:none;font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:14px;line-height:1.2;color:var(--color-text);background:transparent;
  border:1px solid var(--color-divider);border-radius:0;
  padding:var(--space-2) calc(var(--space-3)*1.2)}
.btn svg{display:block}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-primary{background:var(--color-accent);color:var(--color-bg);border-color:var(--color-accent)}
.btn-primary:hover{background:var(--color-accent-600);border-color:var(--color-accent-600)}
.btn-primary:active{background:var(--color-accent-700)}
.btn-secondary:hover{background:color-mix(in srgb,var(--color-text) 7%,transparent)}
.btn-secondary:active{background:color-mix(in srgb,var(--color-text) 14%,transparent)}
.btn-ghost{color:var(--color-accent);border-color:transparent;padding-inline:var(--space-1)}
.btn-ghost:hover{background:color-mix(in srgb,var(--color-accent) 10%,transparent)}
.btn-icon{width:36px;height:36px;padding:0}
.btn-sm{font-size:13px;padding:5px 10px}
/* — forms — */
.field>label{display:block;font-size:12px;margin-bottom:5px;
  color:color-mix(in srgb,var(--color-text) 70%,transparent)}
.input{width:100%;min-height:36px;padding:6px 10px;font:inherit;font-size:14px;
  color:var(--color-text);caret-color:var(--color-accent);background:var(--color-surface);
  border:1px solid var(--color-divider);border-radius:0}
.input:hover{border-color:color-mix(in srgb,var(--color-text) 45%,transparent)}
.input:focus-visible{border-color:var(--color-accent);outline-offset:0}
textarea.input{min-height:90px;resize:vertical;line-height:1.5}
.radio{display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-size:14px}
.radio input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}
.radio .dot{width:16px;height:16px;flex:none;border-radius:2px;border:1.5px solid var(--color-divider)}
.radio:hover .dot{border-color:var(--color-accent)}
.radio input:checked+.dot{border-color:var(--color-accent);background:var(--color-accent);
  box-shadow:inset 0 0 0 4px var(--color-bg)}
.radio input:focus-visible+.dot{outline:2px solid var(--color-accent);outline-offset:2px}
input[type=checkbox]{accent-color:var(--color-accent)}
/* — cards — */
.card{display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-3);
  border-radius:0;background:transparent;border:1px solid var(--color-divider)}
.card-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:17px;line-height:1.2}
.card-body{margin:0;font-size:13px;opacity:.8;flex:1}
/* — tags — */
.tag{display:inline-flex;align-items:center;font-size:11px;letter-spacing:.02em;
  padding:3px 10px;border-radius:0;white-space:nowrap}
.tag-accent{background:var(--color-accent-100);color:var(--color-accent-800)}
.tag-neutral{background:var(--color-neutral-100);color:var(--color-neutral-800)}
/* — tables — */
.table{width:100%;border-collapse:collapse;font-size:14px}
.table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:color-mix(in srgb,var(--color-text) 60%,transparent);
  padding:var(--space-2);border-bottom:1px solid var(--color-divider)}
.table td{padding:var(--space-2);border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent);
  vertical-align:top}
.table tbody tr:hover{background:color-mix(in srgb,var(--color-text) 4%,transparent)}
/* — app shell — */
.shell{min-height:100vh;display:flex;flex-direction:column}
.topbar{border-bottom:1px solid var(--color-divider);background:var(--color-bg);
  position:sticky;top:0;z-index:10}
.topbar-in{max-width:1160px;margin:0 auto;padding:0 28px;display:flex;align-items:center;
  gap:32px;min-height:58px}
.brand{display:flex;align-items:center;gap:10px;margin-right:auto}
.brand b{line-height:1}
.brand .mark{flex:none;display:block}
.brand b{font-family:var(--font-heading);font-weight:600;font-size:19px;letter-spacing:.01em}
.brand span{font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.tabs{display:flex;gap:4px;align-self:stretch;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{display:flex;align-items:center;padding:0 14px;font-size:13.5px;letter-spacing:.03em;
  text-decoration:none;color:var(--color-text);white-space:nowrap;
  border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a:hover{color:var(--color-accent-700)}
.tabs a[aria-current=page]{color:var(--color-accent-700);border-bottom-color:var(--color-accent)}
main{max-width:1160px;width:100%;margin:0 auto;padding:36px 28px 64px;flex:1}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:14px}
.sec-head h6{color:var(--color-accent);margin:0 0 2px}
.sec-head h3{margin:0}
.sec-head .note{font-size:12.5px;text-align:right;max-width:520px}
.banner{display:flex;align-items:center;gap:12px;padding:10px 14px;margin-bottom:28px;
  background:var(--color-accent-100)}
.banner span{font-size:13px;color:var(--color-accent-800)}
.panel{padding:24px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px 28px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}
.span2{grid-column:1/-1}
.panel-foot{display:flex;justify-content:flex-end;align-items:center;gap:12px;
  margin-top:24px;padding-top:18px;border-top:1px solid var(--color-divider)}
.msg{font-size:13px;color:var(--color-accent-700)}
.mono{font-family:var(--font-mono);font-size:13px}
.cmd{font-family:var(--font-mono);font-size:12px;background:var(--color-neutral-100);
  border:1px solid var(--color-divider);padding:10px 12px;white-space:pre-wrap;line-height:1.6;
  overflow-x:auto}
.acts{display:flex;gap:6px;justify-content:flex-end;align-items:center;flex-wrap:wrap}
footer{border-top:1px solid var(--color-divider)}
.foot-in{max-width:1160px;margin:0 auto;padding:14px 28px;display:flex;gap:24px;
  flex-wrap:wrap;font-size:12px}
.screen{display:none}.screen.on{display:block}
.stack{display:flex;flex-direction:column;gap:18px}
.mrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}
/* — dialog — */
.dialog-backdrop{position:fixed;inset:0;display:grid;place-items:center;padding:var(--space-4);
  background:color-mix(in srgb,var(--color-neutral-900) 50%,transparent);z-index:50}
.dialog{width:min(440px,100%);max-height:88vh;overflow:auto;display:flex;flex-direction:column;
  gap:var(--space-3);padding:var(--space-6);border-radius:0;background:var(--color-bg);
  border:1px solid var(--color-divider);box-shadow:var(--shadow-lg)}
.dialog-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:20px}
.dialog-body{font-size:13px;opacity:.85}
.dialog-actions{display:flex;justify-content:flex-end;align-items:center;gap:var(--space-2);
  margin-top:var(--space-2);padding-top:var(--space-3);border-top:1px solid var(--color-divider)}
/* — folder picker — */
.pklist{max-height:46vh;overflow:auto;border:1px solid var(--color-divider);
  background:var(--color-surface);padding:4px;display:flex;flex-direction:column}
.pkrow{justify-content:flex-start;width:100%;border-color:transparent;color:var(--color-text);
  font-family:var(--font-body);font-size:13.5px;padding:5px 8px;gap:8px}
.pkrow:hover{background:color-mix(in srgb,var(--color-accent) 12%,transparent)}
.pkquick{display:flex;gap:6px;flex-wrap:wrap}
.kv{display:flex;gap:12px;align-items:baseline;font-size:13.5px;padding:7px 0;
  border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
.kv:last-child{border-bottom:none}
.sev{display:inline-flex;align-items:center;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;padding:3px 8px;white-space:nowrap;flex:none}
.sev-high{background:var(--color-accent-700);color:var(--color-bg)}
.sev-medium{background:var(--color-accent-200);color:var(--color-accent-900)}
.sev-low{background:var(--color-neutral-200);color:var(--color-neutral-800)}
.issue{display:grid;grid-template-columns:78px 1fr auto;gap:12px;align-items:start;
  padding:12px 0;border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
.issue:last-child{border-bottom:none}
.issue.done{opacity:.5}
.issue h5{margin:0 0 3px;font-size:15px}
.issue .meta{font-size:11.5px;font-family:var(--font-mono)}
.issue p{margin:4px 0 0;font-size:13px}
.md h2{font-size:22px;margin:26px 0 8px}
.md h3{font-size:16px;margin:18px 0 6px;color:var(--color-accent-700)}
.md ul{margin:0 0 10px;padding-left:18px}
.md li{font-size:13.5px;margin:3px 0}
.md p{font-size:13.5px}
.kv b{font-family:var(--font-heading);font-weight:600;font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;min-width:130px;color:color-mix(in srgb,var(--color-text) 60%,transparent)}
@media(max-width:860px){
  .grid2,.grid3{grid-template-columns:1fr}
  .topbar-in,main,.foot-in{padding-left:16px;padding-right:16px}
  .sec-head{flex-direction:column;align-items:flex-start}
  .sec-head .note{text-align:left}
  .table thead{display:none}
  .table,.table tbody,.table tr,.table td{display:block;width:100%}
  .table tr{border:1px solid var(--color-divider);margin:12px 0;padding:6px 4px}
  .table td{border:none;padding:6px 10px}
  .table td::before{content:attr(data-label);display:block;font-size:10px;
    letter-spacing:.08em;text-transform:uppercase;
    color:color-mix(in srgb,var(--color-text) 55%,transparent)}
  .acts{justify-content:flex-start}
}
</style></head><body>
<div class=shell>
<header class=topbar><div class=topbar-in>
  <div class=brand>__LOGO__<b>kAIm56</b></div>
  <nav class=tabs id=tabs>
    <a href="#instances">Instances</a>
    <a href="#personas">Personas</a>
    <a href="#skills">Skills</a>
    <a href="#mcp">MCP servers</a>
    <a href="#tasks">Tasks</a>
    <a href="#policy">Policy</a>
    <a href="#models">Models</a>
    <a href="#sharing">Sharing</a>
    <a href="#secrets">Secrets</a>
    <a href="#settings">Settings</a>
    <a href="#changelog">Changelog</a>
  </nav>
</div></header>
<main>

<section class="screen" id=s-instances>
  <div class=sec-head>
    <div><h6>microVM</h6><h3>Instances</h3></div>
    <span class="note text-muted">💬 Chat opens <a href="/chat">/chat</a> for every <code>TRANSPORT=web</code> instance · a stopped instance starts on the first prompt</span>
  </div>
  <table class=table>
    <thead><tr><th style="width:32%">Instance</th><th>Status</th><th>vCPU / RAM</th><th>Guest IP</th><th style="text-align:right">Actions</th></tr></thead>
    <tbody>__ROWS__</tbody>
  </table>

  <div style="margin-top:44px">
    <h6 style="color:var(--color-accent);margin:0 0 2px">Provision</h6>
    <h3 style="margin:0 0 18px">New instance from template</h3>
    <div class="panel blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <div class=grid2>
        <div class=field><label>Template</label><select class=input id=tpl onchange=renderParams()>__TPLS__</select></div>
        <div class=field><label>Instance name</label><input class=input id=nm placeholder="e.g. fabric-gpt4o"></div>
        <div class=field><label>Persona / system prompt (optional)</label>
          <select class=input id=persona><option value="">— Default —</option></select></div>
      </div>
      <div class=grid2 id=params style="margin-top:20px"></div>
      <div class=grid2 style="margin-top:20px">
        <div class="field span2"><label>MCP servers (optional)</label><div id=mcp-pick style="display:flex;gap:20px;flex-wrap:wrap;padding-top:2px"></div></div>
        <div class="field span2"><label>katfs share (optional)</label>
          <div id=katfs-new class=text-muted style="font-size:12px;margin-bottom:6px">…</div>
          <select class=input id=katfsshare onchange=katfsNewHint()></select>
          <span class=text-muted id=katfsurlhint style="font-size:12px"></span>
        </div>
        <div class="field span2"><label>Mount host folders (optional)</label>
          <div id=mounts></div>
          <div style="display:flex;align-items:center;gap:12px;margin-top:6px">
            <button type=button class="btn btn-secondary btn-sm" onclick="addMount()">+ Folder</button>
            <span class=text-muted style="font-size:12px">Host path → guest path · pick with 📁 · "ro" = read-only · no host folder? <a href="#sharing">share one from your browser via katfs</a></span>
          </div>
        </div>
        <div class="field span2"><label>Capabilities</label>
          <label class=radio style="margin:2px 0 8px"><input type=checkbox id=cap-net checked><span class=dot></span>Internet access (LAN + web) — without it the agent only reaches the manager broker and <b>cannot</b> query the LLM</label>
          <div style="display:flex;align-items:center;gap:10px;margin:2px 0 6px">
            <span class=text-muted style="font-size:12px">Agent tools:</span>
            <button type=button class="btn btn-ghost" style="font-size:12px" onclick="toolAll(1)">all</button>
            <button type=button class="btn btn-ghost" style="font-size:12px" onclick="toolAll(0)">none</button>
          </div>
          <div id=toolpick style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:4px 16px"></div>
        </div>
      </div>
      <div class=panel-foot><button class="btn btn-primary" onclick=create()>Create instance</button></div>
    </div>
  </div>
</section>

<section class="screen" id=s-personas>
  <div class=sec-head>
    <div><h6>System prompts</h6><h3>Personas</h3></div>
    <span class="note text-muted">Selectable as "Persona" when creating an instance → sets <code>AGENT_SYSTEM</code> (applied at start, no rebuild)</span>
  </div>
  <div class=grid2 id=personas></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New persona</h4>
    <div style="display:grid;grid-template-columns:280px 1fr;gap:20px" class=pgrid>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=pname placeholder="e.g. researcher"></div>
      <div class=field style="grid-row:span 2"><label>Prompt</label><textarea class=input id=pprompt style="min-height:110px"></textarea></div>
      <div style="align-self:end;display:flex;align-items:center;gap:12px">
        <button class="btn btn-primary" onclick=savePersona()>Save persona</button><span id=pmsg class=msg></span>
      </div>
    </div>
  </div>
</section>

<section class="screen" id=s-skills>
  <div class=sec-head>
    <div><h6>Expert knowledge</h6><h3>Skills</h3></div>
    <span class="note text-muted">Loaded into context on demand via <code>load_skill("name")</code> · <code>list_skills</code> shows them · central, no rebuild</span>
  </div>
  <div class=grid3 id=skills></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New skill</h4>
    <div class=grid2>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=skname placeholder="e.g. postgres-expert"></div>
      <div class=field><label>Short description</label><input class=input id=skdesc placeholder="When to load this skill?"></div>
      <div class="field span2"><label>Content (Markdown)</label><textarea class=input id=skcontent style="min-height:130px"></textarea></div>
    </div>
    <div class=panel-foot><span id=skmsg class=msg></span><button class="btn btn-primary" onclick=saveSkill()>Save skill</button></div>
  </div>
</section>

<section class="screen" id=s-mcp>
  <div class=sec-head>
    <div><h6>Catalog</h6><h3>MCP servers</h3></div>
    <span class="note text-muted">Attachable per instance · use <code>${SECRET_NAME}</code> in args to inject from the secret store at create time</span>
  </div>
  <div class=grid2 id=mcps></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New MCP server</h4>
    <div class=grid2>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=mcpname placeholder="e.g. homeassistant"></div>
      <div class=field><label>Description</label><input class=input id=mcpdesc placeholder="What is this MCP?"></div>
      <div class=field><label>Command</label><input class=input id=mcpcmd placeholder="e.g. mcp-remote  or  npx"></div>
      <div class=field><label>Args (one per line)</label><textarea class=input id=mcpargs style="min-height:80px;font-family:var(--font-mono);font-size:12.5px" placeholder="http://10.0.0.54:8123/mcp_server/sse&#10;--header&#10;Authorization: Bearer ${HA_TOKEN}"></textarea></div>
      <div class="field span2"><label>Env (KEY=VALUE per line; use ${SECRET} for secrets)</label><textarea class=input id=mcpenv style="min-height:64px;font-family:var(--font-mono);font-size:12.5px" placeholder="PORTAINER_URL=http://10.0.0.187:9000&#10;PORTAINER_API_KEY=${PORTAINER_API_KEY}"></textarea></div>
    </div>
    <div class=panel-foot><span id=mcpmsg class=msg></span><button class="btn btn-primary" onclick=saveMcp()>Save MCP</button></div>
  </div>
</section>

<section class="screen" id=s-models>
  <div class=sec-head>
    <div><h6>OpenRouter</h6><h3>Models</h3></div>
    <span class="note text-muted">The full catalog, fetched live · ticked models are the shortlist offered when creating an instance</span>
  </div>
  <div class="banner blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke="var(--color-accent-700)" stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=10></circle><path d="M12 8v4"></path><path d="M12 16h.01"></path></svg>
    <span>The openrouter template only offers models that can do tool calling — a model without it stays hidden even when ticked.</span>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
    <input class=input id=mdlq placeholder="filter by id or name…" style="flex:1;min-width:220px;width:auto" oninput=renderModels()>
    <label class=radio><input type=checkbox id=mdltools checked onchange=renderModels()><span class=dot></span>tool calling only</label>
    <label class=radio><input type=checkbox id=mdlsel onchange=renderModels()><span class=dot></span>selected only</label>
    <span class=text-muted id=mdlcount style="font-size:12px"></span>
    <button class="btn btn-secondary btn-sm" onclick="loadModels2(1)">Refresh catalog</button>
  </div>
  <div style="max-height:60vh;overflow:auto;border:1px solid var(--color-divider)">
    <table class=table id=mdltable><tbody id=mdlrows></tbody></table>
  </div>
  <div class=panel-foot><span id=mdlmsg class=msg></span><button class="btn btn-primary" onclick=saveModels()>Save shortlist</button></div>
</section>

<section class="screen" id=s-policy>
  <div class=sec-head>
    <div><h6>What each instance may do & does</h6><h3>Policy</h3></div>
    <span class="note text-muted">Network, tools, secrets and MCP in one place · per instance the tools and URLs called most recently</span>
  </div>
  <div id=policycards style="display:flex;flex-direction:column;gap:18px"></div>
</section>

<section class="screen" id=s-tasks>
  <div class=sec-head>
    <div><h6>Scheduled work</h6><h3>Tasks</h3></div>
    <span class="note text-muted">One message runs on one instance — once or recurring · a stopped instance is started for it</span>
  </div>
  <table class=table>
    <thead><tr><th style="width:20%">Instance</th><th>Job</th><th>Schedule</th><th>Status</th><th>Last result</th><th></th></tr></thead>
    <tbody id=taskrows></tbody>
  </table>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New task</h4>
    <div class=grid2>
      <div class=field><label>Instance</label><select class=input id=tk-inst></select></div>
      <div class=field><label>Schedule (empty = once, right away)</label>
        <input class=input id=tk-sched placeholder="every 30m · every 2h · daily 08:00 · hourly">
        <span class=text-muted style="font-size:12px">Formats: <code>every Nm|Nh|Nd</code>, <code>daily HH:MM</code>, <code>hourly</code></span>
      </div>
      <div class="field span2"><label>Job (the message sent to the agent)</label>
        <textarea class=input id=tk-msg style="min-height:90px" placeholder="e.g. Summarise the new Home Assistant events and report anything unusual."></textarea></div>
    </div>
    <div class=panel-foot><span id=tkmsg class=msg></span><button class="btn btn-primary" onclick=saveTask()>Create task</button></div>
  </div>
</section>

<section class="screen" id=s-sharing>
  <div class=sec-head>
    <div><h6>Browser → Agent</h6><h3>katfs sharing</h3></div>
    <span class="note text-muted">A folder from the browser you are sitting at, handed to the agents over P2P (iroh) · nothing is mounted into the microVM</span>
  </div>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=katfs-status><span class=text-muted style="font-size:13px">checking…</span></div>
    <div class=field style="margin-top:18px">
      <label>Sharing key — node-id of the katfs node the browser connects to</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <input class="input mono" id=katfskey spellcheck=false autocomplete=off
               placeholder="64 hex characters" style="flex:1;min-width:260px;width:auto"
               oninput=keyHint()>
        <button class="btn btn-secondary" onclick=copyKey()>Copy</button>
        <button class="btn btn-secondary" onclick=resetKey() title="Back to this host's node-id">Reset</button>
      </div>
      <span class=text-muted id=keyhint style="font-size:12px"></span>
    </div>
    <div class=panel-foot>
      <span class=text-muted style="font-size:12px;margin-right:auto">The share lives in the browser tab — close it and the agents lose access.</span>
      <button class="btn btn-secondary" onclick=loadKatfs()>Refresh</button>
      <button class="btn btn-primary" onclick=openShare()>Share a folder…</button>
    </div>
  </div>
  <div class=grid2 style="margin-top:32px">
    <div class="card blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <span class=card-title style="font-size:16px">How the agent reaches it</span>
      <p class=card-body>The share is not a filesystem — it is reachable only through the agent tools
      <code>remote_ls</code>, <code>remote_read(path)</code> and <code>remote_write(path, content)</code>,
      which talk to the katfs node on the host gateway. Paths are relative to the shared folder.
      Without an active share those tools answer <code>503 no browser connected</code>.</p>
    </div>
    <div class="card blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <span class=card-title style="font-size:16px">Host folders instead</span>
      <p class=card-body>A folder that already lives on this host belongs in
      <b>Mount host folders</b> when creating an instance, or behind 📁 in the instance table —
      that one is a real live mount (NFS) inside the guest and survives without a browser tab.</p>
    </div>
  </div>
</section>

<section class="screen" id=s-secrets>
  <div class=sec-head>
    <div><h6>Runtime access</h6><h3>Secrets access</h3></div>
    <span class="note text-muted">Which keys each template's agents may fetch via <code>get_secret(name)</code> · default is deny · values never written to disk</span>
  </div>
  <div class="banner blueprint" style="background:var(--color-neutral-100);margin:16px 0 20px;padding:9px 14px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <svg width=15 height=15 viewBox="0 0 24 24" fill=none stroke="var(--color-neutral-700)" stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>
    <span style="color:var(--color-neutral-800);font-size:12.5px">Keep dangerous keys (Docker / network admin) unchecked.</span>
  </div>
  <div id=secrets></div>
  <div class=panel-foot style="border:none;padding-top:0"><span id=secmsg class=msg></span><button class="btn btn-primary" onclick=saveSecrets()>Save access</button></div>
</section>

<section class="screen" id=s-changelog>
  <div class=sec-head>
    <div><h6>Open items</h6><h3>Security issues</h3></div>
    <span class="note text-muted">Findings from working on the system · text and rating live in <code>security.json</code>, only the status can be toggled here</span>
  </div>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=issues><span class=text-muted style="font-size:13px">…</span></div>
  </div>
  <div class=panel-foot><span id=secissuemsg class=msg></span>
    <label class=radio style="margin-right:auto"><input type=checkbox id=showdone onchange=renderIssues()><span class=dot></span>show fixed ones</label>
    <button class="btn btn-primary" onclick=saveIssues()>Save status</button></div>

  <div class=sec-head style="margin-top:44px">
    <div><h6>History</h6><h3>Changelog</h3></div>
    <span class="note text-muted">from <code>CHANGELOG.md</code></span>
  </div>
  <div class="panel blueprint md" id=changelog>
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
  </div>
</section>

<section class="screen" id=s-settings style="max-width:640px">
  <div style="margin-bottom:18px"><h6 style="color:var(--color-accent);margin:0 0 2px">Shared, persisted</h6><h3 style="margin:0">Settings</h3></div>
  <p class=text-muted style="font-size:13px;margin-bottom:22px">Values apply to all new instances; empty template fields are pre-filled from here.</p>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=stack id=settings></div>
    <div class=panel-foot><span id=setmsg class=msg></span><button class="btn btn-primary" onclick=saveSettings()>Save</button></div>
  </div>
</section>

</main>
<footer><div class="foot-in text-muted">
  <span>NAT via __HOSTIF__</span><span>Pool __POOL__</span>
  <span style="margin-left:auto">kAIm56</span>
</div></footer>
</div>

<div id=picker class=dialog-backdrop style="display:none;z-index:60">
  <div class="dialog blueprint" style="width:min(560px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Choose a host folder</div>
    <div style="display:flex;gap:6px">
      <input class=input id=pkpath spellcheck=false onkeydown="if(event.key==='Enter')pkGo(this.value)">
      <button class="btn btn-secondary" onclick="pkGo(document.getElementById('pkpath').value)">Go</button>
    </div>
    <div class=pkquick id=pkquick></div>
    <div class=pklist id=pklist></div>
    <div id=pkerr class=text-muted style="font-size:12px;min-height:1em"></div>
    <div class=dialog-actions>
      <span class=text-muted style="font-size:12px;margin-right:auto">Not on this host? <a href="#sharing" onclick=pkClose()>share it from your browser via katfs</a></span>
      <button class="btn btn-secondary" onclick=pkClose()>Cancel</button>
      <button class="btn btn-primary" onclick=pkChoose()>Use this folder</button>
    </div>
  </div>
</div>

<div id=mdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Host folders — <span id=mdlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>Host path → guest path, "ro" = read-only. Saved changes are picked up by the reconciler in the running guest.</div>
    <div id=mdlgrows></div>
    <div><button type=button class="btn btn-secondary btn-sm" onclick="addMount(null,'mdlgrows')">+ Folder</button></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=mdlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveMounts()>Save</button>
    </div>
  </div>
</div>

<div id=actdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Activity — <span id=actname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>Tools and targets called most recently (URLs/paths/queries). No secret values, no file contents.</div>
    <div style="max-height:56vh;overflow:auto;border:1px solid var(--color-divider)">
      <table class=table><tbody id=actrows></tbody></table>
    </div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=actClose()>Close</button>
      <button class="btn btn-secondary" onclick="openActivity(ACT_CUR)">Refresh</button>
    </div>
  </div>
</div>

<script>
const TEMPLATES=__TPLJSON__;
const SETTINGS=__SETTINGS__;
const SETTINGS_SCHEMA=__SETTINGS_SCHEMA__;
const PERSONAS=__PERSONAS__;
const SKILLS=__SKILLS__;
const CORNERS='<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>';
const I_EDIT='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>';
const I_DEL='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
function esc(s){return (s||'').replace(/"/g,'&quot;')}
function escT(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

/* — Werkzeug-Auswahl im Anlege-Formular — */
let TOOLS=[];
async function loadTools(){
  try{TOOLS=(await (await fetch('/api/agent-tools')).json()).tools||[];}catch(e){return;}
  document.getElementById('toolpick').innerHTML=TOOLS.map(t=>
    `<label class=radio style="font-size:13px"><input type=checkbox class=toolcb value="${esc(t.name)}" checked>`+
    `<span class=dot></span><span title="${esc(t.desc)}"><span class=mono style="font-size:12px">${escT(t.name)}</span></span></label>`).join('');
}
function toolAll(on){document.querySelectorAll('.toolcb').forEach(c=>c.checked=!!on)}

/* — Policy: was jede Instanz darf (Netz/Tools/Secrets/MCP) + was sie tut (Audit) — */
let POL_TOOLS=[];
async function loadPolicy(){
  let pol={instances:[]};
  try{
    pol=await (await fetch('/api/policy')).json();
    if(!POL_TOOLS.length)POL_TOOLS=(await (await fetch('/api/agent-tools')).json()).tools||[];
  }catch(e){document.getElementById('policycards').innerHTML='<span class=text-muted>not reachable</span>';return;}
  const tag=(on,y,n)=>`<span class="tag ${on?'tag-accent':'tag-neutral'}">${on?y:n}</span>`;
  document.getElementById('policycards').innerHTML=(pol.instances||[]).map(p=>{
    const toolset=new Set(p.tools||[]);
    const tools=POL_TOOLS.map(t=>
      `<label class=radio style="font-size:12.5px"><input type=checkbox data-pt="${esc(p.name)}" value="${esc(t.name)}" ${p.tools_all||toolset.has(t.name)?'checked':''}>`+
      `<span class=dot></span><span class=mono style="font-size:11.5px" title="${esc(t.desc)}">${escT(t.name)}</span></label>`).join('');
    const secrets=(p.secrets||[]).map(s=>`<span class="tag tag-neutral" style="font-size:11px">${escT(s)}</span>`).join(' ')||'<span class=text-muted style="font-size:12px">none</span>';
    const mcps=(p.mcps||[]).map(s=>`<span class="tag tag-accent" style="font-size:11px">${escT(s)}</span>`).join(' ')||'<span class=text-muted style="font-size:12px">none</span>';
    return `<div class="card blueprint" style="padding:16px">${CORNERS}`+
      `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">`+
        `<span class=card-title style="font-size:17px">${escT(p.name)}</span>`+
        `<span class=text-muted style="font-size:12px">${escT(p.template)}</span>`+
        tag(p.running,'● running','○ off')+
        `<span style="margin-left:auto;display:flex;gap:8px;align-items:center">`+
          `<button class="btn ${p.internet?'btn-secondary':'btn-primary'} btn-sm" onclick="toggleNet('${esc(p.name)}',${(!p.internet)})">${p.internet?'🌐 internet on':'🚫 offline'}</button>`+
          `<button class="btn btn-secondary btn-sm" onclick="openActivity('${esc(p.name)}')">Activity</button>`+
        `</span></div>`+
      (p.model?`<div class=kv><b>Model</b><span class=mono style="font-size:12px">${escT(p.model)}</span></div>`:'')+
      `<div class=kv><b>Secrets</b><span>${secrets}</span></div>`+
      `<div class=kv><b>MCP</b><span>${mcps}</span></div>`+
      (p.katfs_share?`<div class=kv><b>katfs</b><span class=mono style="font-size:12px">${escT(p.katfs_share)}</span></div>`:'')+
      `<div style="margin-top:8px"><div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">`+
        `<b style="font-family:var(--font-heading);font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:color-mix(in srgb,var(--color-text) 60%,transparent)">Tools</b>`+
        `<button class="btn btn-ghost" style="font-size:12px" onclick="polToolAll('${esc(p.name)}',1)">all</button>`+
        `<button class="btn btn-ghost" style="font-size:12px" onclick="polToolAll('${esc(p.name)}',0)">none</button>`+
        `<button class="btn btn-primary btn-sm" style="margin-left:auto" onclick="savePolTools('${esc(p.name)}')">Save tools</button>`+
        `<span class=msg data-tmsg="${esc(p.name)}"></span>`+
      `</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:2px 14px">${tools}</div></div>`+
    `</div>`;
  }).join('')||'<span class=text-muted style="font-size:13px">no instances</span>';
}
function polToolAll(name,on){document.querySelectorAll(`input[data-pt="${CSS.escape(name)}"]`).forEach(c=>c.checked=!!on)}
function savePolTools(name){
  const tools=[...document.querySelectorAll(`input[data-pt="${CSS.escape(name)}"]:checked`)].map(c=>c.value);
  fetch(`/api/instances/${name}/tools`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tools})}).then(r=>r.json()).then(d=>{
      const el=document.querySelector(`[data-tmsg="${CSS.escape(name)}"]`);if(el)el.textContent=(d.msg||'saved')+' ✓';});
}
let ACT_CUR='';
async function openActivity(name){
  ACT_CUR=name;
  document.getElementById('actname').textContent=name;
  document.getElementById('actdlg').style.display='grid';
  let ev=[];
  try{ev=(await (await fetch('/api/audit/'+encodeURIComponent(name))).json()).events||[];}catch(e){}
  const icon={http_fetch:'🌐',web_search:'🔎'};
  document.getElementById('actrows').innerHTML=ev.map(e=>{
    const d=new Date((e.ts||0)*1000).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
    return `<tr><td class=text-muted style="white-space:nowrap;font-size:12px">${d}</td>`+
      `<td class=mono style="font-size:12.5px">${escT(e.tool)}${e.ok===false?' <span class="tag tag-neutral" style="font-size:10px">denied</span>':''}</td>`+
      `<td class=mono style="font-size:12px;word-break:break-all;color:var(--color-accent-700)">${escT(e.target||'')}</td></tr>`;
  }).join('')||'<tr><td class=text-muted style="padding:12px">no calls logged yet</td></tr>';
}
function actClose(){document.getElementById('actdlg').style.display='none'}

/* — Tasks: geplante Arbeit pro Instanz (Backend: /api/tasks, Worker im Manager) — */
function fmtTs(t){if(!t)return '—';const d=new Date(t*1000);
  return d.toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
async function loadTasks(){
  let tasks=[],insts=[];
  try{
    tasks=await (await fetch('/api/tasks')).json();
    insts=await (await fetch('/api/instances')).json();
  }catch(e){document.getElementById('taskrows').innerHTML='<tr><td colspan=6 class=text-muted>not reachable</td></tr>';return;}
  const sel=document.getElementById('tk-inst');
  if(sel)sel.innerHTML=insts.map(i=>`<option value="${esc(i.name)}">${escT(i.name)}</option>`).join('')||'<option value="">— no instance —</option>';
  const tag={scheduled:'tag-accent',pending:'tag-accent',running:'tag-accent',done:'tag-neutral',error:'tag-neutral'};
  document.getElementById('taskrows').innerHTML=(tasks||[]).map(t=>{
    const nr=t.schedule?` · next ${fmtTs(t.next_run)}`:'';
    const res=(t.result||'').slice(0,120);
    return `<tr><td data-label=Instance class=mono>${escT(t.instance)}</td>`+
      `<td data-label=Job>${escT((t.message||'').slice(0,90))}${(t.message||'').length>90?'…':''}</td>`+
      `<td data-label=Schedule class=mono style="font-size:12px">${escT(t.schedule||'once')}${nr}</td>`+
      `<td data-label=Status><span class="tag ${tag[t.status]||'tag-neutral'}">${escT(t.status)}</span></td>`+
      `<td data-label=Result class=text-muted style="font-size:12px">${escT(res)}</td>`+
      `<td><button class="btn btn-icon btn-secondary" style="width:30px;height:30px" title=Delete onclick="delTask('${esc(t.id)}')">${I_DEL}</button></td></tr>`;
  }).join('')||'<tr><td colspan=6 class=text-muted style="padding:14px">no tasks yet</td></tr>';
}
function saveTask(){
  const instance=document.getElementById('tk-inst').value,
        message=document.getElementById('tk-msg').value.trim(),
        schedule=document.getElementById('tk-sched').value.trim();
  if(!instance||!message)return alert('Instance + job?');
  fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({instance,message,schedule})})
    .then(r=>r.json()).then(d=>{document.getElementById('tkmsg').textContent=(d.msg||'created')+' ✓';
      document.getElementById('tk-msg').value='';loadTasks();});
}
async function delTask(id){if(confirm('Delete task?')){await fetch('/api/tasks/'+encodeURIComponent(id)+'/delete',{method:'POST'});loadTasks();}}

/* — tabs (hash-routed, so a reload after an action keeps the screen) — */
const TABS=['instances','personas','skills','mcp','tasks','policy','models','sharing','secrets','settings','changelog'];
function showTab(t){
  if(TABS.indexOf(t)<0)t='instances';
  TABS.forEach(x=>document.getElementById('s-'+x).classList.toggle('on',x===t));
  document.querySelectorAll('#tabs a').forEach(a=>{
    if(a.getAttribute('href')==='#'+t)a.setAttribute('aria-current','page');
    else a.removeAttribute('aria-current');});
}
window.addEventListener('hashchange',()=>showTab(location.hash.slice(1)));

function renderPersonas(){
  document.getElementById('personas').innerHTML=PERSONAS.map(p=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:10px">`+
    `<span class=card-title style="font-size:16px">${escT(p.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:4px">`+
    `<button class="btn btn-ghost" style="font-size:12px" onclick="editPersona('${esc(p.name)}')">Edit</button>`+
    `<button class="btn btn-ghost" style="font-size:12px;color:var(--color-neutral-600)" onclick="delPersona('${esc(p.name)}')">Delete</button>`+
    `</span></div>`+
    `<p class=card-body style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${escT(p.prompt||'')}</p></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
  const sel=document.getElementById('persona');
  if(sel)sel.innerHTML='<option value="">— Default —</option>'+PERSONAS.map(p=>`<option value="${esc(p.name)}">${escT(p.name)}</option>`).join('');
}
function editPersona(n){const p=PERSONAS.find(x=>x.name===n);if(!p)return;
  document.getElementById('pname').value=p.name;document.getElementById('pprompt').value=p.prompt||'';
  document.getElementById('pname').scrollIntoView({behavior:'smooth'});}
function savePersona(){
  const name=document.getElementById('pname').value.trim(),prompt=document.getElementById('pprompt').value;
  if(!name)return alert('Name?');
  fetch('/api/personas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,prompt})})
    .then(()=>location.reload());
}
async function delPersona(n){if(confirm('Delete persona '+n+'?')){await fetch('/api/personas/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}

function renderSkills(){
  document.getElementById('skills').innerHTML=SKILLS.map(s=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:8px">`+
    `<span class=card-title style="font-size:15px;font-family:var(--font-mono);font-weight:500">${escT(s.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:2px">`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px" title=Edit onclick="editSkill('${esc(s.name)}')">${I_EDIT}</button>`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px;color:var(--color-neutral-600)" title=Delete onclick="delSkill('${esc(s.name)}')">${I_DEL}</button>`+
    `</span></div>`+
    `<p class=card-body style="font-size:12.5px">${escT(s.description||'')}</p></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
}
function editSkill(n){const s=SKILLS.find(x=>x.name===n);if(!s)return;
  document.getElementById('skname').value=s.name;document.getElementById('skdesc').value=s.description||'';
  document.getElementById('skcontent').value=s.content||'';document.getElementById('skname').scrollIntoView({behavior:'smooth'});}
function saveSkill(){
  const name=document.getElementById('skname').value.trim(),description=document.getElementById('skdesc').value,
        content=document.getElementById('skcontent').value;
  if(!name)return alert('Name?');
  fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description,content})})
    .then(()=>location.reload());
}
async function delSkill(n){if(confirm('Delete skill '+n+'?')){await fetch('/api/skills/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}

function renderSettings(){
  document.getElementById('settings').innerHTML=SETTINGS_SCHEMA.map(s=>
    `<div class=field><label>${escT(s.label)}</label><input class=input data-s="${esc(s.key)}" value="${esc(SETTINGS[s.key])}" `+
    `type="${s.key.indexOf('KEY')>=0?'password':'text'}" autocomplete=off></div>`).join('');
}
function saveSettings(){
  const d={};document.querySelectorAll('#settings input').forEach(i=>d[i.dataset.s]=i.value);
  fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
    .then(()=>{Object.assign(SETTINGS,d);document.getElementById('setmsg').textContent='saved ✓';renderParams()});
}
function fieldFor(p){
  const v=p.default||SETTINGS[p.key]||'';
  if(p.type==='select'&&p.options)
    // Ein Eintrag ist entweder ein String oder {value,label} — so kann eine
    // Option einen sprechenden Text tragen (z. B. "— account default —").
    return `<select class=input data-k="${p.key}" ${p.key==='TRANSPORT'?'onchange=applyTransport()':''}>`+p.options.map(o=>{
      const val=(o&&typeof o==='object')?(o.value||''):o, lbl=(o&&typeof o==='object')?(o.label||o.value||''):o;
      return `<option value="${esc(val)}"${val===v?' selected':''}>${escT(lbl)}</option>`;
    }).join('')+`</select>`;
  if(p.type==='select'&&p.source==='openrouter')
    return `<div style="display:flex;gap:6px">${orSelect(p.key,v,p.tools,p.relevant)}`
      +`<button type=button class="btn btn-secondary btn-icon" title="Refresh list" onclick="orRefresh(this)">⟳</button></div>`;
  return `<input class=input data-k="${p.key}" value="${esc(v)}">`;
}
/* — model pickers: a dropdown off the OpenRouter catalog, with "other model
     id…" as the way back to free text (direct providers are not in the list) — */
const OR_CUSTOM='__custom__';
function orSelect(key,v,tools,relevant){
  return `<select class=input style="flex:1;min-width:0" data-k="${esc(key)}" data-or=1 `
    +`${tools?'data-tools=1':''} ${relevant?'data-relevant=1':''} onchange="orPick(this)">`
    +`<option value="${esc(v)}">${esc(v)||'— loading… —'}</option></select>`;
}
function loadModels(sel,force){
  const cur=sel.value; sel.disabled=true;
  let q='?'; if(force)q+='refresh=1&'; if(sel.dataset.tools)q+='tools=1&'; if(sel.dataset.relevant)q+='relevant=1';
  fetch('/api/openrouter-models'+q).then(r=>r.json()).then(ms=>{
    sel.innerHTML=(ms.length?ms:[{id:cur,label:cur+' (list n/a)'}]).map(m=>
      `<option value="${m.id}"${m.id===cur?' selected':''}>${m.label}</option>`).join('')
      +`<option value="${OR_CUSTOM}">— other model id… —</option>`;
    // Ein Wert ausserhalb der Auswahl darf nicht still auf den ersten Eintrag
    // kippen — er bleibt als eigene Option stehen.
    if(cur&&!ms.some(m=>m.id===cur))
      sel.insertAdjacentHTML('afterbegin',`<option value="${esc(cur)}" selected>${escT(cur)} (not in the shortlist)</option>`);
    sel.disabled=false;
  }).catch(()=>{sel.disabled=false});
}
function orPick(sel){
  if(sel.value!==OR_CUSTOM)return;
  const box=sel.parentNode;
  box.dataset.tools=sel.dataset.tools||''; box.dataset.relevant=sel.dataset.relevant||'';
  sel.outerHTML=`<input class=input style="flex:1;min-width:0" data-k="${esc(sel.dataset.k)}" placeholder="provider/model-id">`;
  box.querySelector('button').title='Back to the list';
  box.querySelector('input[data-k]').focus();
}
function orRefresh(btn){
  const box=btn.parentNode, sel=box.querySelector('select[data-or]');
  if(sel)return loadModels(sel,1);
  const inp=box.querySelector('input[data-k]');
  if(!inp)return;
  inp.outerHTML=orSelect(inp.dataset.k,inp.value.trim(),box.dataset.tools,box.dataset.relevant);
  btn.title='Refresh list';
  loadModels(box.querySelector('select[data-or]'),0);
}
function renderParams(){
  const t=document.getElementById('tpl').value;
  const tpl=TEMPLATES.find(x=>x.template===t)||{params:[]};
  document.getElementById('params').innerHTML=(tpl.params||[]).map(p=>
    `<div class=field data-pk="${p.key}"><label>${escT(p.label||p.key)}</label>${fieldFor(p)}</div>`).join('');
  document.querySelectorAll('#params select[data-or]').forEach(s=>loadModels(s,0));
  applyTransport();
}
function applyTransport(){
  const sel=document.querySelector('#params [data-k="TRANSPORT"]');
  const web=sel&&sel.value==='web';
  ['SIGNAL_NUMBER','ALLOWED_SENDERS'].forEach(k=>{
    const el=document.querySelector('#params [data-pk="'+k+'"]');
    if(el)el.style.display=web?'none':'';
  });
}
function mountRow(m){m=m||{};return `<div class=mrow>`+
  `<input class="input mh" placeholder="/host/path" value="${esc(m.host)}" style="flex:2;min-width:150px;width:auto">`+
  `<button type=button class="btn btn-secondary btn-icon" style="width:32px;height:32px" title="Browse the host…" onclick="pkRow(this)">📁</button>`+
  `<input class="input mg" placeholder="/mnt/name" value="${esc(m.guest)}" style="flex:2;min-width:120px;width:auto">`+
  `<label class=radio><input type=checkbox class=mr ${m.readonly?'checked':''}><span class=dot></span>ro</label>`+
  `<button type=button class="btn btn-secondary btn-icon" style="width:32px;height:32px" onclick="this.parentNode.remove()">✕</button></div>`}
function addMount(m,target){document.getElementById(target||'mounts').insertAdjacentHTML('beforeend',mountRow(m))}

/* — folder picker: browses the host through /api/browse (directories only) — */
let PK={cb:null};
const PK_QUICK=['/home/ulrich','/mnt','/srv','/media','/opt','/'];
function pkOpen(start,cb){
  PK.cb=cb;
  document.getElementById('pkquick').innerHTML=PK_QUICK.map(p=>
    `<button type=button class="btn btn-secondary btn-sm" data-p="${esc(p)}">${escT(p)}</button>`).join('');
  document.getElementById('picker').style.display='grid';
  pkGo(start||'/home/ulrich');
}
function pkClose(){document.getElementById('picker').style.display='none';PK.cb=null}
async function pkGo(p){
  let d;
  try{d=await (await fetch('/api/browse?path='+encodeURIComponent(p||'/'))).json()}
  catch(e){d={path:p,parent:'',dirs:[],error:'not reachable'}}
  document.getElementById('pkpath').value=d.path||p;
  document.getElementById('pkerr').textContent=d.error||'';
  const up=d.parent?`<button type=button class="btn pkrow" data-p="${esc(d.parent)}">↑ ..</button>`:'';
  const base=(d.path==='/'?'':d.path);
  const rows=(d.dirs||[]).map(n=>
    `<button type=button class="btn pkrow" data-p="${esc(base+'/'+n)}">📁 ${escT(n)}</button>`).join('');
  document.getElementById('pklist').innerHTML=up+rows||
    '<span class=text-muted style="font-size:13px;padding:6px">no sub-folders</span>';
}
function pkChoose(){
  const p=document.getElementById('pkpath').value.trim(),cb=PK.cb;
  pkClose(); if(cb&&p)cb(p);
}
function pkRow(btn){
  const row=btn.closest('.mrow'),h=row.querySelector('.mh'),g=row.querySelector('.mg');
  pkOpen(h.value||'/home/ulrich',p=>{
    h.value=p;
    if(!g.value){const b=p.split('/').filter(Boolean).pop();if(b)g.value='/mnt/'+b}
  });
}
function collectMounts(scope){return [...scope.querySelectorAll('.mrow')].map(r=>({
  host:r.querySelector('.mh').value.trim(),guest:r.querySelector('.mg').value.trim(),
  readonly:r.querySelector('.mr').checked})).filter(m=>m.host&&m.guest)}
function create(){
  const t=document.getElementById('tpl').value,n=document.getElementById('nm').value;
  if(!n)return alert('Name?');
  const cfg={};document.querySelectorAll('#params [data-k]').forEach(i=>cfg[i.dataset.k]=i.value);
  const pn=document.getElementById('persona').value;
  if(pn){const p=PERSONAS.find(x=>x.name===pn);if(p)cfg.AGENT_SYSTEM=p.prompt;}
  const ks=document.getElementById('katfsshare').value;
  if(ks)cfg.KATFS_SHARE=ks;
  const mcps=[...document.querySelectorAll('#mcp-pick input[type=checkbox]:checked')].map(c=>c.value);
  const mounts=collectMounts(document.getElementById('mounts'));
  const internet=document.getElementById('cap-net').checked;
  const tools=[...document.querySelectorAll('.toolcb:checked')].map(c=>c.value);
  fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:n,template:t,config:cfg,mcps,mounts,internet,tools})}).then(()=>location.reload());
}
let MCPS=[];
async function renderMcps(){
  try{MCPS=await (await fetch('/api/mcps')).json();}catch(e){return;}
  const box=document.getElementById('mcps');
  if(box)box.innerHTML=MCPS.map(m=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:10px">`+
    `<span class=card-title style="font-size:16px">${escT(m.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:4px">`+
    `<button class="btn btn-ghost" style="font-size:12px" onclick="editMcp('${esc(m.name)}')">Edit</button>`+
    `<button class="btn btn-ghost" style="font-size:12px;color:var(--color-neutral-600)" onclick="delMcp('${esc(m.name)}')">Delete</button>`+
    `</span></div>`+
    `<p class=card-body>${escT(m.description||'')}</p>`+
    `<div class=cmd>${escT(m.command||'')} ${escT((m.args||[]).join(' '))}</div></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
  const pick=document.getElementById('mcp-pick');
  if(pick)pick.innerHTML=MCPS.map(m=>
    `<label class=radio><input type=checkbox value="${esc(m.name)}"><span class=dot></span>${escT(m.name)}</label>`)
    .join('')||'<span class=text-muted style="font-size:12px">no MCPs in catalog</span>';
}
function editMcp(n){const m=MCPS.find(x=>x.name===n);if(!m)return;
  document.getElementById('mcpname').value=m.name;document.getElementById('mcpdesc').value=m.description||'';
  document.getElementById('mcpcmd').value=m.command||'';document.getElementById('mcpargs').value=(m.args||[]).join('\\n');
  document.getElementById('mcpenv').value=Object.entries(m.env||{}).map(e=>e[0]+'='+e[1]).join('\\n');
  document.getElementById('mcpname').scrollIntoView({behavior:'smooth'});}
function saveMcp(){
  const name=document.getElementById('mcpname').value.trim(),description=document.getElementById('mcpdesc').value,
        command=document.getElementById('mcpcmd').value.trim(),
        args=document.getElementById('mcpargs').value.split('\\n').map(s=>s.replace(/\\r$/,'')).filter(s=>s.length);
  const env={};
  document.getElementById('mcpenv').value.split('\\n').forEach(l=>{l=l.replace(/\\r$/,'');const i=l.indexOf('=');if(i>0)env[l.slice(0,i).trim()]=l.slice(i+1);});
  if(!name||!command)return alert('Name + Command?');
  fetch('/api/mcps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description,command,args,env})})
    .then(()=>location.reload());
}
async function delMcp(n){if(confirm('Delete MCP '+n+'?')){await fetch('/api/mcps/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}
let MDLG='';
async function editMounts(name){
  const list=await (await fetch('/api/instances')).json();
  const inst=list.find(i=>i.name===name)||{};
  MDLG=name;
  document.getElementById('mdlgname').textContent=name;
  document.getElementById('mdlgrows').innerHTML='';
  (inst.mounts||[]).forEach(m=>addMount(m,'mdlgrows'));
  document.getElementById('mdlg').style.display='grid';
}
function mdlgClose(){document.getElementById('mdlg').style.display='none'}
async function saveMounts(){
  const mounts=collectMounts(document.getElementById('mdlgrows'));
  const r=await fetch(`/api/instances/${MDLG}/mounts`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({mounts})});
  const d=await r.json(); mdlgClose();
  alert(d.msg||'ok');location.reload();
}

/* — Changelog + Security: Befunde stehen in security.json, die UI schaltet nur
     den Status um. Markdown wird bewusst minimal gerendert (Ueberschriften,
     Listen, fett, code) — nach dem Escapen, damit nichts aus dem Text ausbricht. */
let ISSUES=[];
const SEVORDER={high:0,medium:1,low:2};
async function loadChangelog(){
  try{
    const [c,i]=await Promise.all([
      (await fetch('/api/changelog')).json(),
      (await fetch('/api/security')).json()]);
    document.getElementById('changelog').innerHTML=
      '<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>'+md(c.text||'');
    ISSUES=(i.issues||[]).slice().sort((a,b)=>
      (a.status===b.status?0:a.status==='open'?-1:1)||
      (SEVORDER[a.severity]??9)-(SEVORDER[b.severity]??9));
  }catch(e){document.getElementById('issues').textContent='not reachable';return}
  renderIssues();
}
function renderIssues(){
  const showDone=document.getElementById('showdone').checked;
  const rows=ISSUES.filter(i=>showDone||i.status!=='done');
  const open=ISSUES.filter(i=>i.status!=='done').length;
  document.getElementById('issues').innerHTML=rows.map(i=>
    `<div class="issue ${i.status==='done'?'done':''}">`+
    `<span class="sev sev-${esc(i.severity)}">${escT(i.severity)}</span>`+
    `<div><h5>${escT(i.title)}</h5>`+
    `<div class="meta text-muted">${escT(i.where||'')} · ${escT(i.kind||'')}</div>`+
    `<p>${escT(i.detail||'')}</p>`+
    (i.fix?`<p class=text-muted><b>Fix:</b> ${escT(i.fix)}</p>`:'')+`</div>`+
    `<label class=radio><input type=checkbox data-i="${esc(i.id)}" ${i.status==='done'?'checked':''}>`+
    `<span class=dot></span>done</label></div>`).join('')||
    '<span class=text-muted style="font-size:13px">nothing open</span>';
  document.getElementById('secissuemsg').textContent=open+' open';
}
function saveIssues(){
  document.querySelectorAll('#issues input[data-i]').forEach(c=>{
    const it=ISSUES.find(x=>x.id===c.dataset.i);
    if(it)it.status=c.checked?'done':'open';
  });
  fetch('/api/security',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({issues:ISSUES.map(i=>({id:i.id,status:i.status}))})})
    .then(r=>r.json()).then(d=>{document.getElementById('secissuemsg').textContent=(d.msg||'saved')+' ✓';
      loadChangelog();});
}
function md(src){
  const esc1=escT(src);
  const out=[];
  let inList=false;
  for(const raw of esc1.split('\\n')){
    const line=raw.replace(/`([^`]+)`/g,'<code>$1</code>')
                  .replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
    const li=line.match(/^\\s*[-*] (.*)$/);
    if(li){ if(!inList){out.push('<ul>');inList=true} out.push('<li>'+li[1]+'</li>'); continue }
    if(inList){out.push('</ul>');inList=false}
    if(/^### /.test(line)) out.push('<h3>'+line.slice(4)+'</h3>');
    else if(/^## /.test(line)) out.push('<h2>'+line.slice(3)+'</h2>');
    else if(/^# /.test(line)) out.push('');            // Titel steht schon im Tab
    else if(line.trim()) out.push('<p>'+line+'</p>');
  }
  if(inList)out.push('</ul>');
  return out.join('');
}

/* — Models: der volle Katalog live, daraus die Auswahl fuers Anlege-Formular.
     Die Haken liegen in CURATED (models.json), nicht mehr im Quelltext. — */
let CATALOG=[], PICKED=new Set();
async function loadModels2(force){
  const msg=document.getElementById('mdlmsg');
  msg.textContent=force?'fetching catalog…':'';
  try{
    const [cat,cur]=await Promise.all([
      (await fetch('/api/openrouter-models'+(force?'?refresh=1':''))).json(),
      (await fetch('/api/models')).json()]);
    CATALOG=cat; PICKED=new Set(cur.curated||[]);
  }catch(e){msg.textContent='catalog not reachable';return}
  msg.textContent='';
  renderModels();
}
function renderModels(){
  const q=document.getElementById('mdlq').value.trim().toLowerCase();
  const only=document.getElementById('mdltools').checked;
  const selOnly=document.getElementById('mdlsel').checked;
  const rows=CATALOG.filter(m=>
    (!only||m.tools)&&(!selOnly||PICKED.has(m.id))&&
    (!q||m.id.toLowerCase().includes(q)||(m.name||'').toLowerCase().includes(q)));
  document.getElementById('mdlcount').textContent=
    PICKED.size+' selected · '+rows.length+' of '+CATALOG.length+' shown';
  document.getElementById('mdlrows').innerHTML=rows.map(m=>
    `<tr><td style="width:34px;text-align:center"><input type=checkbox data-m="${esc(m.id)}" ${PICKED.has(m.id)?'checked':''}></td>`+
    `<td><div class=mono style="font-size:13px">${escT(m.id)}</div>`+
    `<div class=text-muted style="font-size:11.5px">${escT(m.name||'')}</div></td>`+
    `<td style="white-space:nowrap;font-variant-numeric:tabular-nums">${escT(m.price||'')}</td>`+
    `<td style="white-space:nowrap;font-variant-numeric:tabular-nums" class=text-muted>${m.ctx?(m.ctx/1000).toFixed(0)+'k':''}</td>`+
    `<td>${m.tools?'<span class="tag tag-accent">tools</span>':'<span class="tag tag-neutral">no tools</span>'}</td></tr>`)
    .join('')||'<tr><td class=text-muted style="padding:14px">nothing matches</td></tr>';
}
function saveModels(){
  fetch('/api/models',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({curated:[...PICKED]})})
    .then(r=>r.json()).then(d=>{
      document.getElementById('mdlmsg').textContent=(d.msg||'saved')+' ✓';
      renderParams();   // Modell-Dropdown im Anlege-Formular sofort nachziehen
    });
}

/* — katfs: status of the host node + the browser share it is holding — */
let KATFS_ID='';
async function loadKatfs(){
  const el=document.getElementById('katfs-status');
  let d;
  try{d=await (await fetch('/api/katfs/status')).json()}
  catch(e){d={up:false,error:'manager unreachable'}}
  const tag=(on,yes,no)=>`<span class="tag ${on?'tag-accent':'tag-neutral'}">${on?yes:no}</span>`;
  el.innerHTML=
    `<div class=kv><b>Host node</b>${tag(d.up,'● up on :'+(d.port||8790),'○ down')}`+
    (d.up?'':`<span class=text-muted style="font-size:12px">${escT(d.error||'')}</span>`)+`</div>`+
    `<div class=kv><b>Browser shares</b>${tag(d.connected,'● '+(d.count||1)+' active','○ nobody sharing')}</div>`+
    (d.shares||[]).map(s=>
      `<div class=kv><b>&nbsp;</b><span><span class=mono>${escT(s.id)}</span> · `+
      `<b style="font-family:var(--font-body);font-size:13.5px;text-transform:none;letter-spacing:0;min-width:0">${escT(s.name||'?')}</b>`+
      (s.device?` <span class=text-muted>${escT(s.device)}</span>`:'')+
      (s.readonly?' <span class="tag tag-neutral">read-only</span>':'')+`</span></div>`).join('');
  KATFS_SHARES=d.shares||[];
  renderShareOptions();
  KATFS_ID=d.node_id||'';
  const inp=document.getElementById('katfskey');
  if(!inp.value||inp.dataset.auto==='1'){inp.value=KATFS_ID;inp.dataset.auto='1'}
  keyHint();
  document.getElementById('katfs-new').innerHTML= d.connected
    ? '<span class="tag tag-accent">● '+(d.count||1)+' share'+((d.count||1)>1?'s':'')+' active</span> reachable via <code>remote_ls</code> / <code>remote_read</code> / <code>remote_write</code>.'
    : (d.up ? '<span class="tag tag-neutral">○ nobody sharing</span> node is up; open <a href="#sharing">Sharing</a> to hand it a folder.'
            : '<span class="tag tag-neutral">○ node down</span> the katfs node on this host is not answering.');
}
/* Der Key ist die node-id des Knotens; iroh parst sie als EndpointId — ein
   Ticket akzeptiert die WASM-Bruecke (noch) nicht, daher der harte Hinweis. */
function keyHint(){
  const inp=document.getElementById('katfskey'),v=inp.value.trim(),h=document.getElementById('keyhint');
  if(inp.value!==KATFS_ID)inp.dataset.auto='0';
  if(!v){h.textContent='Empty — the share page will use whatever the node injects.';return}
  if(!/^[0-9a-fA-F]{64}$/.test(v)){
    h.textContent='Not a node-id: iroh expects 64 hex characters (a ticket does not parse).';return;
  }
  h.textContent=(v.toLowerCase()===KATFS_ID.toLowerCase())
    ? 'This host — agents on this machine reach the share.'
    : 'Foreign node — the folder is served to that node, not to this host.';
}
/* Auswahl der Freigabe im Anlege-Formular. Der Wert ist die share-id, die der
   freigebende Browser meldet (stabil ueber Reload) — nicht die node-id. */
let KATFS_SHARES=[];
function renderShareOptions(){
  const sel=document.getElementById('katfsshare'),cur=sel.value;
  sel.innerHTML='<option value="">— none / decide at runtime —</option>'+
    KATFS_SHARES.map(s=>{
      const lbl=(s.name||s.id)+(s.device?' · '+s.device:'')+(s.readonly?' · read-only':'')+' — '+s.id;
      return `<option value="${esc(s.id)}"${s.id===cur?' selected':''}>${escT(lbl)}</option>`;
    }).join('');
  katfsNewHint();
}
function katfsNewHint(){
  const v=document.getElementById('katfsshare').value,h=document.getElementById('katfsurlhint');
  if(v){
    const s=KATFS_SHARES.find(x=>x.id===v)||{};
    h.innerHTML='Pinned to <span class=mono>'+escT(v)+'</span> ('+escT(s.name||'?')+') as <span class=mono>KATFS_SHARE</span>'+
      ' — survives a reload of that browser tab, but not clearing its storage.';
    return;
  }
  h.textContent=KATFS_SHARES.length>1
    ? 'With '+KATFS_SHARES.length+' shares active the node cannot guess — the agent must name one, so pick a share here.'
    : 'Fine while at most one share is active: the agent just takes the only one.';
}
function openShare(){
  const v=document.getElementById('katfskey').value.trim();
  window.open(v&&v!==KATFS_ID?'/katfs/?key='+encodeURIComponent(v):'/katfs/','_blank','noopener');
}
function resetKey(){
  const inp=document.getElementById('katfskey');
  inp.value=KATFS_ID;inp.dataset.auto='1';keyHint();
}
function copyKey(){
  const inp=document.getElementById('katfskey'),h=document.getElementById('keyhint');
  const done=()=>{h.textContent='copied ✓'};
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(inp.value).then(done,()=>{inp.select()});
  else{inp.select();try{document.execCommand('copy');done()}catch(e){h.textContent='select + copy manually'}}
}
async function act(n,a){await fetch(`/api/instances/${n}/${a}`,{method:'POST'});location.reload()}
async function toggleNet(n,on){
  await fetch(`/api/instances/${n}/internet`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({on})});location.reload();
}
async function del(n){if(confirm('Delete instance '+n+'?')){await fetch(`/api/instances/${n}/delete`,{method:'POST'});location.reload()}}

let SECPOL={by_template:{},by_instance:{}};
async function renderSecrets(){
  const el=document.getElementById('secrets');
  let keys=[];
  try{
    keys=(await (await fetch('/api/secret-keys')).json()).keys||[];
    SECPOL=await (await fetch('/api/secret-policy')).json();
  }catch(e){el.innerHTML='<span class=text-muted style="font-size:13px">not available</span>';return;}
  if(!keys.length){el.innerHTML='<span class=text-muted style="font-size:13px">No secret keys found in the store.</span>';return;}
  const tpls=(TEMPLATES||[]).map(t=>t.template), bt=SECPOL.by_template||{};
  const head=`<tr><th>Secret key</th>`+tpls.map(t=>`<th style="text-align:center">${escT(t)}</th>`).join('')+`</tr>`;
  const body=keys.map(k=>`<tr><td data-label="Secret key" class=mono style="font-size:12.5px">${escT(k)}</td>`+
    tpls.map(t=>`<td data-label="${esc(t)}" style="text-align:center"><input type=checkbox data-tpl="${esc(t)}" value="${esc(k)}" ${(bt[t]||[]).indexOf(k)>=0?'checked':''}></td>`).join('')+`</tr>`).join('');
  el.innerHTML=`<table class=table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}
function saveSecrets(){
  const bt={};
  (TEMPLATES||[]).forEach(t=>{bt[t.template]=[];});
  document.querySelectorAll('#secrets input[type=checkbox]').forEach(c=>{
    if(!bt[c.dataset.tpl])bt[c.dataset.tpl]=[];
    if(c.checked)bt[c.dataset.tpl].push(c.value);
  });
  fetch('/api/secret-policy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({by_template:bt,by_instance:(SECPOL.by_instance||{})})})
    .then(r=>r.json()).then(d=>{document.getElementById('secmsg').textContent=(d.msg||'saved')+' ✓';});
}
window.onload=()=>{
  showTab(location.hash.slice(1));
  renderSettings();renderParams();renderPersonas();renderSkills();renderSecrets();renderMcps();loadKatfs();loadModels2();loadChangelog();loadTools();loadTasks();loadPolicy();
  document.getElementById('actdlg').onclick=e=>{if(e.target.id==='actdlg')actClose()};
  // Haken merken, ohne bei jedem Klick die ganze Tabelle neu zu bauen.
  document.getElementById('mdlrows').onchange=e=>{
    const b=e.target.closest('[data-m]'); if(!b)return;
    b.checked?PICKED.add(b.dataset.m):PICKED.delete(b.dataset.m);
    document.getElementById('mdlcount').textContent=
      PICKED.size+' selected · '+document.querySelectorAll('#mdlrows tr').length+' shown';
  };
  // Folder-Picker: ein Handler fuer Liste + Schnellziele, damit Pfade mit
  // Anfuehrungszeichen/Apostroph nicht durch inline-onclick muessen.
  ['pklist','pkquick'].forEach(id=>document.getElementById(id).onclick=e=>{
    const b=e.target.closest('[data-p]'); if(b)pkGo(b.dataset.p);
  });
  // Klick auf den Hintergrund bzw. Esc schliesst den obersten Dialog.
  document.getElementById('picker').onclick=e=>{if(e.target.id==='picker')pkClose()};
  document.getElementById('mdlg').onclick=e=>{if(e.target.id==='mdlg')mdlgClose()};
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape')return;
    if(document.getElementById('picker').style.display!=='none')pkClose();
    else mdlgClose();
  });
};
</script></body></html>"""

# ---- Marke ------------------------------------------------------------------
# logo.svg liegt als Datei vor (Favicon, Freigabe an andere Stellen). Fuers
# Kopfzeilen-Zeichen erbt das Navy die Textfarbe, damit es im hellen wie im
# dunklen Theme traegt; das Tuerkis bleibt der Akzent.
BRAND = "kAIm56"
LOGO_FILE = os.path.join(BASE, "logo.svg")
try:
    with open(LOGO_FILE) as _fh:
        LOGO_SVG = _fh.read()
except OSError:
    LOGO_SVG = ""
LOGO_INLINE = (LOGO_SVG.replace("#1D2A4D", "currentColor")
                       .replace('width="512" height="512"',
                                'width="26" height="26" class=mark')
                       .replace("\n", "").strip())

# Icons für die serverseitig gerenderten Instanz-Zeilen (Feather-Stil, 14px).
_SVG = ('<svg width=14 height=14 viewBox="0 0 24 24" fill=none stroke=currentColor '
        'stroke-width=1.5 stroke-linecap=round stroke-linejoin=round>%s</svg>')
IC_TERM = _SVG % '<polyline points="4 17 10 11 4 5"></polyline><line x1=12 y1=19 x2=20 y2=19></line>'
IC_CHAT = _SVG % '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>'
IC_FILES = _SVG % ('<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
                   'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>')
IC_DEL = _SVG % ('<path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>'
                 '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>')
# kleines Ordner-Icon (12px) fuer die Mount-Zeilen — statt 📁 (Emoji tofut ohne Emoji-Schrift)
IC_AUDIT = _SVG % ('<path d="M4 5h16M4 12h16M4 19h10"></path>'
                   '<circle cx="19" cy="19" r="2.4"></circle><path d="M22 22l-1.3-1.3"></path>')
IC_FILES2 = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 '
             'stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px"><path d="M4 20h16a2 '
             '2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 '
             '2v13a2 2 0 0 0 2 2Z"></path></svg>')


def render():
    rows = ""
    for inst in load_instances():
        n = net_of(inst)
        run = is_running(inst)
        name = inst["name"]
        transport = (inst.get("config") or {}).get("TRANSPORT", "signal")
        cfgm = inst.get("config") or {}
        model = cfgm.get("OPENROUTER_MODEL") or cfgm.get("PI_MODEL") or cfgm.get("PRIME_MODEL") or ""
        sub = " · ".join(x for x in (inst.get("template", ""), transport) if x)
        _chip = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor '
                 'stroke-width=1.6 stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px">'
                 '<rect x=6 y=6 width=12 height=12 rx=1/><path d="M9 2v2M15 2v2M9 20v2M15 20v2'
                 'M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>')
        model_line = (f"<span class='mono' style='font-size:12px;color:var(--color-accent-700);"
                      f"display:inline-flex;align-items:center;gap:5px'>{_chip}{model}</span>"
                      if model else "")
        st = (f"<span class='tag tag-accent'>● running</span>" if run
              else f"<span class='tag tag-neutral'>○ off</span>")
        net = inst.get("internet", True)
        tools_cfg = (inst.get("config") or {}).get("AGENT_TOOLS", "")
        ntag = (f"<button class='tag {'tag-accent' if net else 'tag-neutral'}' "
                f"style='border:none;cursor:pointer' title='Toggle internet access' "
                f"onclick=\"toggleNet('{name}',{str(not net).lower()})\">"
                f"{'🌐 internet on' if net else '🚫 offline'}</button>")
        ttag = (f"<span class='tag tag-neutral' title='{tools_cfg}'>🔧 {len(tools_cfg.split(','))} Tools</span>"
                if tools_cfg else "")
        btn = ""
        if run:
            btn += (f"<a href=\"/i/{name}/term/\" target=_blank class=\"btn btn-secondary btn-sm\""
                    f" title=\"Browser terminal\">{IC_TERM}Terminal</a>")
        if transport == "web":
            btn += (f"<a href=\"/chat?i={name}\" class=\"btn btn-secondary btn-sm\""
                    f" title=\"Chat with the agent\">{IC_CHAT}Chat</a>")
        btn += (f"<button class=\"btn {'btn-secondary' if run else 'btn-primary'} btn-sm\""
                f" style=\"min-width:64px\" onclick=\"act('{name}','{'stop' if run else 'start'}')\">"
                f"{'Stop' if run else 'Start'}</button>")
        btn += (f"<button class=\"btn btn-icon btn-secondary\" style=\"width:32px;height:32px\""
                f" title=\"Audit / activity (tools & URLs called)\""
                f" onclick=\"openActivity('{name}')\">{IC_AUDIT}</button>")
        btn += (f"<button class=\"btn btn-icon btn-secondary\" style=\"width:32px;height:32px\""
                f" title=\"Host folders\" onclick=\"editMounts('{name}')\">{IC_FILES}</button>")
        btn += (f"<button class=\"btn btn-icon btn-secondary\" style=\"width:32px;height:32px;"
                f"color:var(--color-neutral-600)\" title=Delete onclick=\"del('{name}')\">{IC_DEL}</button>")
        mtxt = ""
        for m in inst.get("mounts", []) or []:
            mtxt += (f"<div class='text-muted' style='font-size:12px'>{IC_FILES2} {m.get('host')} → "
                     f"{m.get('guest')}{' (ro)' if m.get('readonly') else ''}</div>")
        rows += (f"<tr><td data-label=Instance>"
                 f"<div style='display:flex;flex-direction:column;gap:2px'>"
                 f"<span style=\"font-family:var(--font-heading);font-weight:600;font-size:16px\">{name}</span>"
                 f"<span class='text-muted' style='font-size:12px'>{sub}</span>"
                 f"{model_line}"
                 f"<span class='text-muted' style='font-size:12px'>{inst.get('description','')}</span>"
                 f"{mtxt}</div></td>"
                 f"<td data-label=Status><div style='display:flex;flex-direction:column;gap:4px;align-items:flex-start'>{st}{ntag}{ttag}</div></td>"
                 f"<td data-label='vCPU / RAM' style='font-variant-numeric:tabular-nums'>"
                 f"{inst.get('vcpus',2)} / {inst.get('mem_mib',1024)} MiB</td>"
                 f"<td data-label='Guest IP' class=mono>{n['guest']}</td>"
                 f"<td data-label=Actions><div class=acts>{btn}</div></td></tr>")
    tpls = "".join(f"<option value='{t['template']}'>{t['template']} — {t.get('description','')}</option>"
                   for t in load_templates())
    empty = ("<tr><td colspan=5 class=text-muted style='padding:18px 8px'>"
             "no instances yet — create one below</td></tr>")
    return (PAGE.replace("__LOGO__", LOGO_INLINE)
                .replace("__ROWS__", rows or empty)
                .replace("__TPLS__", tpls or "<option>no templates</option>")
                .replace("__TPLJSON__", json.dumps(load_templates()))
                .replace("__SETTINGS__", json.dumps(settings_for_ui()))
                .replace("__SETTINGS_SCHEMA__", json.dumps(SETTINGS_SCHEMA))
                .replace("__PERSONAS__", json.dumps(load_personas(), ensure_ascii=False))
                .replace("__SKILLS__", json.dumps(load_skills(), ensure_ascii=False))
                .replace("__HOSTIF__", HOSTIF).replace("__POOL__", POOL))


# ---- Chat (Oberflaeche unter /chat, siehe chatui.py) ------------------------
# Chatbar ist jede Instanz mit TRANSPORT=web: die Bridge in der microVM haelt
# unter :8080 /api/chat (und ggf. /api/chat/stream) bereit.

def web_instances():
    """Instanzen, mit denen man chatten kann (+ Laufzustand fuer die UI)."""
    return [{"name": i["name"], "running": is_running(i),
             "description": i.get("description", "")}
            for i in load_instances()
            if (i.get("config") or {}).get("TRANSPORT") == "web"]


def wait_web(inst, timeout=120):
    """Startet die Instanz bei Bedarf und wartet, bis die Bridge annimmt."""
    if not is_running(inst):
        start(inst)
    ip = net_of(inst)["guest"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection((ip, WEB_GUEST_PORT), 2).close()
            return True
        except OSError:
            time.sleep(1)
    return False


def guest_chat(inst, message, image=None, timeout=620):
    """Nicht-streamender Aufruf der Bridge in der microVM."""
    payload = {"message": message}
    if image:
        payload["image"] = image
    req = urllib.request.Request(
        f"http://{net_of(inst)['guest']}:{WEB_GUEST_PORT}/api/chat",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    body = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    try:
        return json.loads(body).get("reply", body)
    except ValueError:
        return body


def guest_stream(inst, message, image, on_token, timeout=620):
    """Streamt Tokens von /api/chat/stream. Bridges ohne Streaming antworten auf
    demselben Pfad mit JSON — das kommt dann als ein Stueck."""
    payload = {"message": message}
    if image:
        payload["image"] = image
    req = urllib.request.Request(
        f"http://{net_of(inst)['guest']}:{WEB_GUEST_PORT}/api/chat/stream",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        on_token(guest_chat(inst, message, image, timeout))
        return
    if "json" in (r.headers.get("Content-Type") or ""):
        body = r.read().decode("utf-8", "replace")
        try:
            on_token(json.loads(body).get("reply", body))
        except ValueError:
            on_token(body)
        return
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    while True:
        raw = r.read(256)
        if not raw:
            break
        tok = dec.decode(raw)
        if tok:
            on_token(tok)
    tail = dec.decode(b"", True)
    if tail:
        on_token(tail)


class H(BaseHTTPRequestHandler):
    def _auth(self):
        if not PW:
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                u, p = base64.b64decode(hdr[6:]).decode().split(":", 1)
                if u == USER and p == PW:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="kAIm56"')
        self.end_headers()
        return False

    def log_message(self, *a):
        pass

    def _chat_stream(self, name):
        """POST /api/chat/<instanz> -> Antwort-Tokens als roher Text (Stream)."""
        try:
            ln = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(ln) or b"{}")
        except (ValueError, json.JSONDecodeError):
            body = {}
        inst = next((i for i in load_instances() if i["name"] == name
                     and (i.get("config") or {}).get("TRANSPORT") == "web"), None)
        self.send_response(200 if inst else 404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(tok):
            try:
                self.wfile.write(tok.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        if not inst:
            return emit(f"⚠️ Keine Web-Instanz '{name}'.")
        if not wait_web(inst):
            return emit(f"⚠️ Instanz '{name}' startet nicht (Port {WEB_GUEST_PORT}).")
        try:
            guest_stream(inst, body.get("message", ""), body.get("image"), emit)
        except Exception as e:
            emit(f"\n⚠️ {e!r}")

    def _term_route(self, name, tail):
        """Route /i/<name>/term[/...] to the guest webterm (:7682). WS-aware."""
        inst = next((i for i in load_instances() if i["name"] == name), None)
        if not inst or not is_running(inst):
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<p>Instance '{name}' is not running (terminal unavailable).</p>".encode())
            return
        sub = tail[len("term"):].lstrip("/")  # "" | "ws"
        if "upgrade" in self.headers.get("Connection", "").lower() and \
           self.headers.get("Upgrade", "").lower() == "websocket":
            return self._ws_tunnel(net_of(inst)["guest"], TERM_GUEST_PORT, "/" + sub)
        return self._proxy("GET", port=TERM_GUEST_PORT, tail_override=sub)

    def _ws_tunnel(self, guest, port, path):
        """Raw bidirectional splice of a WebSocket between browser and guest."""
        try:
            up = socket.create_connection((guest, port), timeout=10)
        except OSError as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"terminal connect failed: {e!r}".encode())
            return
        # Replay the client's upgrade request verbatim to the guest webterm.
        req = f"GET {path} HTTP/1.1\r\n"
        for k, v in self.headers.items():
            req += f"{k}: {v}\r\n"
        req += "\r\n"
        up.sendall(req.encode())
        self.close_connection = True
        down = self.connection

        def pipe(a, b):
            try:
                while True:
                    data = a.recv(65536)
                    if not data:
                        break
                    b.sendall(data)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pipe, args=(up, down), daemon=True)
        t.start()
        pipe(down, up)   # blocks until browser->guest side ends
        t.join(timeout=1)
        for s in (up, down):
            try:
                s.close()
            except OSError:
                pass

    def _katfs_proxy(self):
        """Freigabe-Seite des katfs-Knotens unter /katfs/ durchreichen. Nur GET —
        die Seite laedt Assets relativ und spricht danach P2P (WASM/iroh), sie
        braucht vom Knoten sonst nichts. Zweck: gleiche Herkunft wie der Manager,
        also HTTPS hinter Traefik → File System Access API funktioniert."""
        rest, _, qs = (self.path[len("/katfs"):] or "/").partition("?")
        # ?key=… ersetzt die vom Knoten eingesetzte node-id im Feld #nodeid —
        # so kann dieser Browser einen Ordner auch an einen *fremden* katfs-
        # Knoten liefern. Streng gefiltert, der Wert landet in einem Attribut.
        key = re.sub(r"[^A-Za-z0-9._-]", "",
                     urllib.parse.parse_qs(qs).get("key", [""])[0])[:200]
        try:
            with urllib.request.urlopen(KATFS_BASE + rest, timeout=10) as r:
                body = r.read()
                ct = r.headers.get("Content-Type", "application/octet-stream")
            if key and ct.startswith("text/html"):
                body = re.sub(rb'(<input id="nodeid"[^>]*value=")[^"]*(")',
                              lambda m: m.group(1) + key.encode() + m.group(2),
                              body, count=1)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h3>katfs-Knoten nicht erreichbar</h3>"
                             f"<p>{KATFS_BASE} — {e}</p>".encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method, port=WEB_GUEST_PORT, tail_override=None):
        rest = self.path[3:]  # "/i/" strippen
        name, _, tail = rest.partition("/")
        if tail_override is not None:
            tail = tail_override
        inst = next((i for i in load_instances() if i["name"] == name), None)
        if not inst or not is_running(inst):
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<p>Instance '{name}' is not running (web UI unavailable).</p>".encode())
            return
        url = f"http://{net_of(inst)['guest']}:{port}/{tail}"
        data = None
        if method == "POST":
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = urllib.request.Request(url, data=data, method=method)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            r = urllib.request.urlopen(req, timeout=620)
            self.send_response(r.status)
            self.send_header("Content-Type", r.headers.get("Content-Type", "text/html; charset=utf-8"))
            self.end_headers()
            # Chunk-weise durchreichen + flushen -> Token-Streaming vom Agenten.
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except Exception:
                    break
            return
        except urllib.error.HTTPError as e:
            body, status, rct = e.read(), e.code, e.headers.get("Content-Type", "text/plain")
        except Exception as e:
            body, status, rct = f"proxy error: {e!r}".encode(), 502, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", rct)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth():
            return
        if self.path.split("?", 1)[0].rstrip("/") == "/chat":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            want = urllib.parse.parse_qs(q).get("i", [""])[0]
            body = chatui.render(web_instances(), want, LOGO_INLINE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Nicht cachen: sonst haelt der Browser eine alte Version fest (das
            # war die Ursache der grauen Emoji-Kaestchen nach dem Icon-Fix).
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?", 1)[0] == "/katfs":
            self.send_response(301)
            self.send_header("Location", "/katfs/")
            self.end_headers()
            return
        if self.path.startswith("/katfs/"):
            return self._katfs_proxy()
        if self.path.startswith("/i/"):
            name, _, tail = self.path[3:].partition("/")
            if tail.split("?", 1)[0].rstrip("/").split("/")[0] == "term":
                return self._term_route(name, tail.split("?", 1)[0])
            return self._proxy("GET")
        # Secrets-Broker: nur für Gäste (Instanz per Source-IP erkannt), Allowlist.
        if self.path == "/api/secrets":
            inst = instance_by_ip(self.client_address[0])
            keys = sorted(allowed_secret_keys(inst)) if inst else []
            b = json.dumps({"allowed": keys, "instance": inst.get("name") if inst else None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path.startswith("/api/secret/"):
            name = self.path.split("/api/secret/", 1)[1]
            inst = instance_by_ip(self.client_address[0])
            allowed = allowed_secret_keys(inst) if inst else set()
            if name not in allowed:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "nicht erlaubt"}).encode())
                return
            val = secret_store().get(name, "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"value": val}).encode())
            return
        if self.path == "/api/agent-tools":
            body = json.dumps({"tools": AGENT_TOOLS_CATALOG}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        # Agenten-Roster fuer Routing (Orchestrator/list_agents). Gast-erlaubt,
        # nur Faehigkeiten — keine Secrets. Ephemere Kinder ausgeblendet.
        if self.path == "/api/agents":
            roster = []
            for i in load_instances():
                if i["name"].startswith(("task-", "sub-")):
                    continue
                cfg = i.get("config") or {}
                roster.append({
                    "name": i["name"], "template": i.get("template", ""),
                    "running": is_running(i),
                    "model": cfg.get("OPENROUTER_MODEL") or cfg.get("PI_MODEL")
                             or cfg.get("PRIME_MODEL") or "",
                    "mcps": [n for n in (cfg.get("MCP_SERVERS", "") or "").split(",") if n],
                })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"agents": roster}, ensure_ascii=False).encode())
            return
        # Posteingang fuer den Orchestrator: neue Nutzer-Nachrichten (Signal/App/
        # Web) seit dem letzten Lauf. Gast-erlaubt; ?peek=1 setzt kein Wasserzeichen.
        if self.path.startswith("/api/inbox"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            data = {"messages": inbox_since(peek=(q.get("peek", ["0"])[0] == "1"))}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # Abfragbare Aufgaben-History (Stammwissen). Fuer Gaeste (recall_tasks)
        # UND Admin/UI offen — enthaelt operatives Wissen, keine Secrets.
        if self.path.startswith("/api/history"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            data = {"rows": history_search(q.get("q", [""])[0], q.get("limit", ["20"])[0])}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # Admin-only: konsolidierte Policy je Instanz + Audit-Log lesen.
        if self.path == "/api/policy" or self.path.startswith("/api/audit/"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            if self.path == "/api/policy":
                data = {"instances": [effective_policy(i) for i in load_instances()]}
            else:
                nm = re.sub(r"[^a-zA-Z0-9_-]", "", self.path.split("/api/audit/", 1)[1].split("?")[0])
                data = {"instance": nm, "events": audit_read(nm)}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        if self.path == "/api/models":
            body = json.dumps({"curated": sorted(load_curated())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        # Gegenstueck zu /api/secret/<name>, aber fuer MCP: nur der Gast selbst,
        # nur seine eigenen Server, und Secrets nur soweit die Policy sie ihm
        # erlaubt. Damit muss MCP_CONFIG nicht mehr in der Instanz liegen.
        # katfs-Dateizugriff fuer Gaeste: nur die eigene Instanz, nur die ihr
        # zugewiesene Freigabe. Der Knoten selbst ist seit dem Fix loopback-only,
        # also fuehrt der einzige Weg fuer Gaeste hier durch — mit erzwungener
        # Freigabe, keine Enumeration, kein Fremdzugriff.
        if self.path.split("?", 1)[0] in ("/api/katfs/ls", "/api/katfs/read"):
            inst = instance_by_ip(self.client_address[0])
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"nur fuer Gaeste"}'); return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            op = "ls" if self.path.split("?", 1)[0].endswith("/ls") else "read"
            try:
                st, ct, data = katfs_proxy_fs(op, katfs_share_for(inst), q.get("path", ["."])[0])
            except urllib.error.HTTPError as e:
                st, ct, data = e.code, "application/json", e.read()
            except Exception as e:
                st, ct, data = 503, "application/json", json.dumps({"error": str(e)}).encode()
            self.send_response(st); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data); return
        if self.path == "/api/mcp-config":
            inst = instance_by_ip(self.client_address[0])
            if inst is None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"nur fuer Gaeste"}')
                return
            names = [n for n in (inst.get("config", {}).get("MCP_SERVERS", "") or "").split(",") if n]
            allowed = allowed_secret_keys(inst)
            blob = build_mcp_config(names, allowed=allowed) if names else ""
            missing = sorted(mcp_required_secrets(names) - allowed)
            data = json.loads(blob) if blob else {"mcpServers": {}}
            if missing:
                data["unresolved"] = missing
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return
        # Admin-only (Gäste per Source-IP gesperrt): Ordner-Browser + katfs-Status.
        if self.path.startswith("/api/browse") or self.path.startswith("/api/katfs/status"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            if self.path.startswith("/api/browse"):
                data = list_dirs(q.get("path", ["/"])[0], q.get("hidden", [""])[0] == "1")
            else:
                data = katfs_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # Admin-only (Gäste per Source-IP gesperrt): Secret-Namen + Policy fürs UI.
        if self.path in ("/api/changelog", "/api/security"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            data = ({"text": load_changelog()} if self.path == "/api/changelog"
                    else {"issues": load_security()})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        if self.path in ("/api/secret-keys", "/api/secret-policy", "/api/mcps"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            if self.path == "/api/secret-keys":
                data = {"keys": sorted(secret_store().keys())}
            elif self.path == "/api/mcps":
                data = load_mcps()
            else:
                data = load_secret_policy()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # Nur fuer die Admin-UI: Gaeste haben hier nichts zu suchen. /api/settings
        # trug bis eben die API-Keys im Klartext aus — an Broker und Policy vorbei.
        _p = self.path.split("?", 1)[0]
        if _p in ("/api/settings", "/api/instances", "/api/chats", "/api/tasks"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
        if self.path == "/api/instances":
            body = json.dumps([{**i, "running": is_running(i)} for i in load_instances()]).encode()
            ct = "application/json"
        elif self.path == "/api/settings":
            body = json.dumps(settings_for_ui()).encode()
            ct = "application/json"
        elif _p == "/api/chats":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            if "since" in q or "wait" in q:
                try:
                    since = int(q.get("since", ["0"])[0] or 0)
                    wait = min(30.0, max(0.0, float(q.get("wait", ["25"])[0] or 0)))
                except ValueError:
                    since, wait = 0, 0.0
                rev, chats = wait_chats(since, wait)
                body = json.dumps({"rev": rev, "chats": chats}).encode()
            else:
                body = json.dumps(load_chats()).encode()
            ct = "application/json"
        elif self.path == "/api/tasks":
            body = json.dumps(load_tasks()).encode()
            ct = "application/json"
        elif self.path == "/api/personas":
            body = json.dumps(load_personas(), ensure_ascii=False).encode()
            ct = "application/json"
        elif self.path == "/api/skills":
            body = json.dumps(load_skills(), ensure_ascii=False).encode()
            ct = "application/json"
        elif self.path.startswith("/api/skills/"):
            nm = re.sub(r"[^a-z0-9_-]", "", self.path.split("/api/skills/", 1)[1].lower())
            s = next((x for x in load_skills() if x.get("name") == nm), None)
            body = (s.get("content", "") if s else f"Skill '{nm}' nicht gefunden").encode()
            ct = "text/plain; charset=utf-8"
        elif self.path.startswith("/api/memory/"):
            seg = self.path[len("/api/memory/"):].split("/")
            # Ein Gast darf nur sein EIGENES Gedaechtnis lesen — der Name kommt
            # dann aus der Source-IP, nicht aus dem Pfad. Nur der Host (Admin,
            # keine Instanz) darf einen fremden Namen im Pfad angeben.
            guest = instance_by_ip(self.client_address[0])
            inst = guest["name"] if guest else seg[0]
            if len(seg) >= 2 and seg[1]:
                body = json.dumps({"value": mem_recall(inst, seg[1])}, ensure_ascii=False).encode()
            else:
                body = json.dumps(mem_recall(inst), ensure_ascii=False).encode()
            ct = "application/json"
        elif self.path in ("/logo.svg", "/favicon.ico"):
            body = LOGO_SVG.encode()
            ct = "image/svg+xml"
        elif self.path.startswith("/api/openrouter-models"):
            body = json.dumps(openrouter_models("refresh=1" in self.path,
                                                "tools=1" in self.path,
                                                "relevant=1" in self.path)).encode()
            ct = "application/json"
        else:
            body = render().encode()
            ct = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._auth():
            return
        if self.path == "/api/audit":
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is not None:   # nur echte Gaeste protokollieren, still verwerfen sonst
                try:
                    audit_append(inst["name"], body.get("tool", ""),
                                 body.get("target", ""), body.get("ok", True))
                except Exception:
                    pass
            self.send_response(204); self.end_headers(); return
        # Aufgabe aus einer VM heraus einreihen (create_task-Tool). Der Aufrufer
        # wird per Source-IP erkannt; er waehlt das TARGET (faehige Instanz oder
        # 'ephemeral'), aber nicht die eigene Identitaet. Ephemere Kinder
        # (task-*/sub-*) duerfen selbst KEINE Tasks anlegen (kein Runaway).
        if self.path == "/api/task":
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"nur fuer Gaeste"}'); return
            if inst["name"].startswith(("task-", "sub-")):
                out = {"error": "ephemere VMs duerfen keine Tasks anlegen"}
            else:
                target = (body.get("target") or "ephemeral").strip()
                message = str(body.get("message", "")).strip()
                schedule = str(body.get("schedule", "")).strip()
                wait = bool(body.get("wait"))
                if not message:
                    out = {"error": "message fehlt"}
                elif wait and not schedule:
                    ok, res = _run_task_now(target, message)
                    history_add(target, message, res, ok, origin=inst["name"])
                    out = {"ok": ok, "result": res}
                else:
                    t = add_task(target, message, schedule)
                    out = {"id": t["id"], "status": t["status"], "target": target}
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(json.dumps(out, ensure_ascii=False).encode())
            return
        # Signal-Turn in die gemeinsame Chat-Historie (App+Web). Nur Gaeste, die
        # Instanz kommt aus der Source-IP — der Gast waehlt sie nicht selbst.
        if self.path == "/api/chat-log":
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is not None:
                try:
                    chat_log_append(inst["name"], body.get("sender", ""),
                                    body.get("user", ""), body.get("reply", ""))
                except Exception:
                    pass
                try:
                    orchestrator_ping()   # Signal-Nachricht -> Orchestrator sofort
                except Exception:
                    pass
            self.send_response(204); self.end_headers(); return
        if self.path.split("?", 1)[0] in ("/api/katfs/write", "/api/katfs/delete"):
            inst = instance_by_ip(self.client_address[0])
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"nur fuer Gaeste"}'); return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            path = q.get("path", [""])[0]
            share = katfs_share_for(inst)
            ln = int(self.headers.get("Content-Length", 0) or 0)
            if self.path.split("?", 1)[0].endswith("/write"):
                if ln > KATFS_MAX_WRITE:
                    self.send_response(413); self.send_header("Content-Type", "application/json")
                    self.end_headers(); self.wfile.write(b'{"error":"zu gross"}'); return
                body = self.rfile.read(ln) if ln else b""
                args = ("write", share, path, False, body)
            else:
                if ln:
                    self.rfile.read(ln)
                args = ("delete", share, path, q.get("recursive", ["0"])[0] == "1", None)
            try:
                st, ct, data = katfs_proxy_fs(*args)
            except urllib.error.HTTPError as e:
                st, ct, data = e.code, "application/json", e.read()
            except Exception as e:
                st, ct, data = 503, "application/json", json.dumps({"error": str(e)}).encode()
            self.send_response(st); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data); return
        if self.path.startswith("/api/chat/"):
            return self._chat_stream(
                urllib.parse.unquote(self.path[len("/api/chat/"):].split("?", 1)[0]))
        if self.path.startswith("/i/"):
            return self._proxy("POST")
        parts = self.path.strip("/").split("/")
        msg = "unknown"
        try:
            if parts == ["api", "settings"]:
                ln = int(self.headers.get("Content-Length", 0))
                msg = save_settings(json.loads(self.rfile.read(ln) or b"{}"))
            elif parts == ["api", "security"]:
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                msg = save_security(b.get("issues") or [])
            elif parts == ["api", "models"]:
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                msg = save_curated(b.get("curated") or [])
            elif parts == ["api", "chats"]:
                ln = int(self.headers.get("Content-Length", 0))
                n = merge_chats(json.loads(self.rfile.read(ln) or b"[]"))
                msg = f"{n} chats saved" if n >= 0 else "error while saving"
                try:
                    orchestrator_ping()   # neue App/Web-Nachricht -> Orchestrator sofort
                except Exception:
                    pass
            elif parts == ["api", "tasks"]:
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                if not b.get("instance") or not b.get("message"):
                    msg = "instance/message missing"
                else:
                    t = add_task(b.get("instance", ""), b.get("message", ""), b.get("schedule", ""))
                    msg = f"task {t['id']} created ({t['status']})"
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "delete":
                tid = parts[2]
                save_tasks([x for x in load_tasks() if x["id"] != tid])
                msg = f"task {tid} deleted"
            elif parts == ["api", "secret-policy"]:
                if instance_by_ip(self.client_address[0]) is not None:
                    msg = "forbidden (admin only)"
                else:
                    ln = int(self.headers.get("Content-Length", 0))
                    msg = save_secret_policy(json.loads(self.rfile.read(ln) or b"{}"))
            elif parts == ["api", "mcps"]:
                if instance_by_ip(self.client_address[0]) is not None:
                    msg = "forbidden (admin only)"
                else:
                    ln = int(self.headers.get("Content-Length", 0))
                    b = json.loads(self.rfile.read(ln) or b"{}")
                    msg = upsert_mcp(b.get("name", ""), b.get("description", ""), b.get("command", ""), b.get("args", []), b.get("env"))
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "mcps" and parts[3] == "delete":
                if instance_by_ip(self.client_address[0]) is not None:
                    msg = "forbidden (admin only)"
                else:
                    msg = delete_mcp(re.sub(r"[^a-z0-9_-]", "", parts[2].lower()))
            elif parts == ["api", "personas"]:
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                msg = upsert_persona(b.get("name", ""), b.get("prompt", ""))
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "personas" and parts[3] == "delete":
                msg = delete_persona(re.sub(r"[^a-z0-9_-]", "", parts[2].lower()))
            elif parts == ["api", "skills"]:
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                msg = upsert_skill(b.get("name", ""), b.get("description", ""), b.get("content", ""))
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "skills" and parts[3] == "delete":
                msg = delete_skill(re.sub(r"[^a-z0-9_-]", "", parts[2].lower()))
            elif len(parts) == 3 and parts[0] == "api" and parts[1] == "memory":
                ln = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(ln) or b"{}")
                guest = instance_by_ip(self.client_address[0])
                target = guest["name"] if guest else parts[2]
                msg = mem_store(target, b.get("key", ""), b.get("value", ""))
            elif parts == ["api", "create"]:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
                cfg = body.get("config", {}) or {}
                mcps = [str(m) for m in (body.get("mcps") or []) if m]
                if mcps:
                    cfg["MCP_SERVERS"] = ",".join(mcps)
                # Werkzeug-Allowlist: nur setzen, wenn es eine echte Teilmenge ist
                # (alle ausgewaehlt -> weglassen = alle). Unbekannte Namen raus.
                tools = [t for t in (body.get("tools") or []) if t in AGENT_TOOL_NAMES]
                if tools and set(tools) != AGENT_TOOL_NAMES:
                    cfg["AGENT_TOOLS"] = ",".join(tools)
                msg = create_instance(body.get("name", ""), body.get("template", ""),
                                      cfg, body.get("mounts", []),
                                      internet=body.get("internet", True))
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "instances":
                name, action = parts[2], parts[3]
                if action == "delete":
                    msg = delete_instance(name)
                elif action == "mounts":
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    msg = set_mounts(name, body.get("mounts", []))
                elif action == "internet":
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    msg = set_internet(name, bool(body.get("on", True)))
                elif action == "tools":
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    msg = set_instance_tools(name, body.get("tools") or [])
                else:
                    inst = next((i for i in load_instances() if i["name"] == name), None)
                    if inst:
                        msg = start(inst) if action == "start" else stop(inst) if action == "stop" else "??"
        except Exception as e:
            msg = f"error: {e!r}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"msg": msg}).encode())


def migrate_mcp_config_out_of_instances():
    """MCP_CONFIG enthielt die eingesetzten Secrets im Klartext. Die Servernamen
    stehen als Schluessel darin, lassen sich also verlustfrei nach MCP_SERVERS
    heben; die dafuer noetigen Secrets werden der Instanz gezielt freigegeben,
    damit nichts stehenbleibt, was vorher lief."""
    pol = load_secret_policy()
    by_inst = pol.setdefault("by_instance", {})
    touched = False
    for inst in load_instances():
        cfg = inst.get("config") or {}
        if "MCP_CONFIG" not in cfg:
            continue
        blob = cfg.get("MCP_CONFIG")
        try:
            servers = json.loads(blob).get("mcpServers", {})
            names = sorted(servers.keys())
        except (ValueError, AttributeError):
            names = []
        if not blob:
            names = []          # leerer Rest aus alten Anlagen — nur wegraeumen
        if names:
            cfg["MCP_SERVERS"] = ",".join(names)
            need = mcp_required_secrets(names)
            if need:
                cur = set(by_inst.get(inst["name"], []))
                if need - cur:
                    by_inst[inst["name"]] = sorted(cur | need)
                    touched = True
        cfg.pop("MCP_CONFIG", None)
        try:
            with open(os.path.join(INST_DIR, f"{inst['name']}.json"), "w") as fh:
                json.dump(inst, fh, indent=2)
            print(f"[migrate] {inst['name']}: MCP_CONFIG -> MCP_SERVERS={','.join(names) or '-'}"
                  f"{' + Policy ' + ','.join(sorted(mcp_required_secrets(names))) if names else ''}",
                  flush=True)
        except OSError as e:
            print(f"[migrate] {inst['name']}: {e}", flush=True)
    if touched:
        save_secret_policy(pol)


def migrate_secrets_out_of_instances():
    """Einmalige Bereinigung des Altbestands: Instanz-JSONs, die noch einen
    API-Key tragen, verlieren ihn hier. Der Agent holt ihn seit dem Umbau ueber
    den Broker; ein Key in der Instanz-Datei waere nur noch eine Kopie, die auf
    jede Config-Disk mitwandert. Laeuft als root, dem die Dateien gehoeren."""
    for inst in load_instances():
        cfg = inst.get("config") or {}
        hit = [k for k in SECRET_PARAMS if k in cfg]
        if not hit:
            continue
        for k in hit:
            cfg.pop(k)
        try:
            with open(os.path.join(INST_DIR, f"{inst['name']}.json"), "w") as fh:
                json.dump(inst, fh, indent=2)
            print(f"[migrate] {inst['name']}: {', '.join(hit)} removed", flush=True)
        except OSError as e:
            print(f"[migrate] {inst['name']}: {e}", flush=True)


if __name__ == "__main__":
    print(f"kAIm56 on http://{LISTEN[0]}:{LISTEN[1]}  (auth={'on' if PW else 'OFF'})",
          flush=True)
    migrate_secrets_out_of_instances()
    migrate_mcp_config_out_of_instances()
    threading.Thread(target=_task_worker, daemon=True).start()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
