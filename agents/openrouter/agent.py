#!/usr/bin/env python3
"""OpenRouter-Agent mit Tool-Calling — modell-agnostisch, laeuft in der microVM.

Tools: bash, read_file, write_file, list_dir, http_fetch  + optional MCP-Server
(stdio), zur Laufzeit vom Manager geholt. Transports: signal | web (via TRANSPORT). Stdlib only.
"""
import json
import os
import socket
import subprocess
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
WORKDIR = os.environ.get("CLAUDE_WORKDIR", "/home/node/workspace")
BASH_TIMEOUT = int(os.environ.get("BASH_TIMEOUT", "120"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))
MAX_TOOL_OUT = int(os.environ.get("MAX_TOOL_OUT", "8000"))
SYSTEM = os.environ.get("AGENT_SYSTEM",
    "Du bist ein hilfreicher Agent mit Tools (Shell, Dateien, Web, MCP). "
    "Arbeite im Verzeichnis %s. Nutze Tools wenn nötig, antworte sonst direkt. "
    "Fasse dich kurz." % WORKDIR)


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
    """OPENROUTER_API_KEY beschaffen und im Speicher halten. Leerer Rueckgabewert
    heisst: weder Env noch Broker haben ihn — der Aufrufer muss das melden."""
    global OR_KEY
    if OR_KEY:
        return OR_KEY
    try:
        d = json.loads(_mgr_get(_manager_base(), "/api/secret/OPENROUTER_API_KEY"))
        OR_KEY = d.get("value", "") or ""
        if not OR_KEY:
            print(f"OPENROUTER_API_KEY: {d.get('error', 'vom Broker nicht freigegeben')}",
                  flush=True)
    except Exception as e:
        print(f"OPENROUTER_API_KEY nicht vom Manager zu bekommen: {e!r}", flush=True)
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
            out.append(f"{a['name']} [{st}] {a.get('template','')} {a.get('model','')}{mcp}")
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
    "list_skills": (t_list_skills, "Verfügbare Experten-Skills auflisten (name: Beschreibung). Vor Fachaufgaben prüfen, ob ein passender Skill existiert.",
                    {}, []),
    "load_skill": (t_load_skill, "Einen Experten-Skill (Wissens-Dokument) in den Kontext laden und befolgen.",
                   {"name": {"type": "string", "description": "Skill-Name aus list_skills"}}, ["name"]),
    "memory_store": (t_memory_store, "Einen Wert dauerhaft merken (überlebt Neustart/Instanz-Löschung).",
                     {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    "memory_recall": (t_memory_recall, "Gemerkten Wert abrufen; ohne key alle Einträge.",
                      {"key": {"type": "string"}}, []),
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


def tool_enabled(name):
    if name == "spawn_subagent" and os.environ.get("NO_SPAWN"):
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
    ok = True
    try:
        if name in BUILTIN:
            if not tool_enabled(name):
                audit(name, args, ok=False)
                return f"Tool '{name}' ist fuer diese Instanz nicht freigegeben."
            audit(name, args)
            return str(BUILTIN[name][0](**args))[:MAX_TOOL_OUT]
        if name in _mcp_tools:
            audit(name, args)
            srv, tool = _mcp_tools[name]
            return _mcp[srv].call(tool, args)[:MAX_TOOL_OUT]
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


# --- OpenRouter chat --------------------------------------------------------
def or_chat(messages, tools):
    body = json.dumps({"model": OR_MODEL, "messages": messages, "tools": tools,
                       "tool_choice": "auto",
                       "usage": {"include": True}}).encode()
    req = urllib.request.Request(OR_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {ensure_or_key()}", "Content-Type": "application/json",
        "HTTP-Referer": "https://agents.kat56.de", "X-Title": "kat56-agent"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read().decode())
        report_usage(d.get("usage"))
        return d["choices"][0]["message"]
    except urllib.error.HTTPError as e:
        return {"content": f"⚠️ OpenRouter HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"content": f"⚠️ OpenRouter-Fehler: {e!r}"}


TOOLS = []
_history = [{"role": "system", "content": SYSTEM}]


def run(user_message):
    if user_message.strip() == "/reset":
        del _history[1:]
        return "🔄 Kontext zurückgesetzt."
    _history.append({"role": "user", "content": user_message})
    for _ in range(MAX_STEPS):
        msg = or_chat(_history, TOOLS)
        _history.append(msg)
        tcs = msg.get("tool_calls")
        if not tcs:
            return msg.get("content") or "(leere Antwort)"
        for tc in tcs:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = exec_tool(fn["name"], args)
            log("tool", fn["name"], "->", "(redacted)" if fn["name"] == "get_secret" else out[:80].replace("\n", " "))
            _history.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    return "(max. Tool-Schritte erreicht)"


def or_chat_stream(messages, tools, on_token):
    """Wie or_chat, aber streamend: ruft on_token(text) je Delta. Baut die
    (assistant-)Nachricht inkl. evtl. tool_calls aus dem Stream zusammen."""
    body = json.dumps({"model": OR_MODEL, "messages": messages, "tools": tools,
                       "tool_choice": "auto", "stream": True,
                       "usage": {"include": True}}).encode()
    req = urllib.request.Request(OR_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {ensure_or_key()}", "Content-Type": "application/json",
        "HTTP-Referer": "https://agents.kat56.de", "X-Title": "kat56-agent"})
    content = ""
    tcs = {}
    try:
        r = urllib.request.urlopen(req, timeout=180)
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
            c = delta.get("content")
            if c:
                content += c
                on_token(c)
            for tc in delta.get("tool_calls") or []:
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
    except urllib.error.HTTPError as e:
        m = f"⚠️ OpenRouter HTTP {e.code}: {e.read().decode()[:300]}"
        on_token(m)
        return {"role": "assistant", "content": m}
    except Exception as e:
        m = f"⚠️ OpenRouter-Fehler: {e!r}"
        on_token(m)
        return {"role": "assistant", "content": m}
    msg = {"role": "assistant", "content": content or None}
    if tcs:
        msg["tool_calls"] = [tcs[i] for i in sorted(tcs)]
    return msg


def run_stream(user_message, on_token, image=None):
    """Wie run(), aber streamt die Antwort-Tokens ueber on_token. Tool-Runden
    erzeugen keinen Text; die finale Antwort wird gestreamt.
    image: optionales Base64-JPEG -> als Vision-Content an OpenRouter."""
    if user_message.strip() == "/reset":
        del _history[1:]
        on_token("🔄 Kontext zurückgesetzt.")
        return
    if image:
        content = [
            {"type": "text", "text": user_message or "Was ist auf dem Bild?"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
        ]
    else:
        content = user_message
    _history.append({"role": "user", "content": content})
    for _ in range(MAX_STEPS):
        msg = or_chat_stream(_history, TOOLS, on_token)
        _history.append(msg)
        tcs = msg.get("tool_calls")
        if not tcs:
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


def init():
    global TOOLS
    os.makedirs(WORKDIR, exist_ok=True)
    TOOLS = builtin_schema() + init_mcp()
    log(f"agent bereit: model={OR_MODEL} tools={len(TOOLS)} workdir={WORKDIR}")
