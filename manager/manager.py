#!/usr/bin/env python3
"""kAIm56 — Manager (Web-UI + API) für 1..x microVM-Instanzen (Firecracker).

Laeuft als root (braucht /dev/kvm, ip, iptables) — z. B. via systemd. Reine
Standardbibliothek, keine Extra-Pakete. Instanzen liegen als JSON unter
instances/<name>.json; Netz wird pro Instanz aus 'index' abgeleitet:
  host  172.30.<index>.1/30   guest 172.30.<index>.2/30   tap fc<index>
"""
import base64
import codecs
import html
import io
import json
import mimetypes
import os
import re
import shlex
import shutil
import signal
import socket
import ssl
import struct
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chatui   # Chat-Oberflaeche (/chat), liegt neben dieser Datei

WEB_GUEST_PORT = 8080   # Port der Web-Bridge in der microVM
TERM_GUEST_PORT = 7682  # Port des webterm (Browser-Terminal) in der microVM

BASE = os.path.dirname(os.path.abspath(__file__))

# mgr-Paket frueh laden: Injektionen (notify/sem) passieren weiter unten,
# sobald die jeweiligen Funktionen definiert sind.
from mgr import missions as _missions  # noqa: E402
_missions.configure(BASE)
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
    {"key": "ORCAROUTER_API_KEY", "label": "OrcaRouter API key (sk-orca-…)"},
    {"key": "ORCAROUTER_URL", "label": "OrcaRouter base URL (blank = https://api.orcarouter.ai/v1; set only when self-hosting OrcaRouter-Lite)"},
    {"key": "SIGNAL_NUMBER", "label": "Signal bot number"},
    {"key": "ALLOWED_SENDERS", "label": "Allowed Signal number(s)"},
    {"key": "SIGNAL_API", "label": "Signal REST API URL"},
    {"key": "LLAMA_ENDPOINT", "label": "llama.cpp endpoint (OpenAI-compatible base URL, e.g. http://10.0.0.50:8080/v1)"},
    {"key": "LLAMA_API_KEY", "label": "llama.cpp API key (optional, only if --api-key is set)"},
    {"key": "LLM_KEY_PROXY", "label": "LLM key injection proxy (1 = Keys bleiben auf dem Host, VMs proxern über den Manager)", "options": [
        {"value": "", "label": "— aus (Agent holt Key via Broker) —"},
        {"value": "1", "label": "an — Keys verlassen den Host nie"}]},
    {"key": "TTS_VOICE", "label": "TTS voice (Piper)", "options": [
        {"value": "", "label": "— default (de-thorsten-medium) —"},
        {"value": "de-thorsten-medium", "label": "Deutsch · Thorsten (medium)"},
        {"value": "de-eva_k-x_low", "label": "Deutsch · Eva K (x_low, schneller)"},
        {"value": "en-amy-medium", "label": "English · Amy (medium)"}]},
    {"key": "TTS_SPEED", "label": "TTS speed (0.5 slow … 2.0 fast, empty = 1.0)"},
]
# Diese Werte landen NIE in instances/<name>.json und nie auf der Config-Disk
# der microVM. Der Agent holt sie zur Laufzeit ueber den Secret-Broker
# (/api/secret/<name>, Gast per Source-IP erkannt, Allowlist per Policy).
SECRET_PARAMS = {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLAMA_API_KEY", "ORCAROUTER_API_KEY"}
# Schreibende Routen, die eine Agent-VM benutzen DARF. Alles andere ist
# Verwaltung und gehoert dem Admin. Ohne diese Positivliste kaeme eine
# kompromittierte VM ueber /api/instances/<n>/mounts an das Host-Dateisystem
# (der Manager laeuft als root und exportiert den Ordner per NFS in den Gast)
# oder legte sich ueber /api/create gleich eine neue Instanz an — die
# Secret-Allowlist, das Tool-Gating und die Egress-Regeln waeren damit egal.
# Positivliste statt Einzelpruefungen: eine neue Route ist dann standardmaessig
# zu, nicht standardmaessig offen.
VOICE_PORT = int(os.environ.get("VOICE_PORT", "8770"))   # Sprachdienst, Loopback
# Abo-Anmeldung des claude-Templates: das Credential des Nutzers auf dem Host.
# Der Manager laeuft als root und darf die 0600-Datei lesen; der Gast holt sie
# beim Boot ueber /api/claude-credentials (nur claude-Template, per Source-IP).
CLAUDE_CRED_SRC = os.environ.get("CLAUDE_CRED_SRC", "/home/ulrich/.claude/.credentials.json")
GUEST_POST_PATHS = ("/api/usage", "/api/audit", "/api/task", "/api/chat-log",
                    "/api/stt", "/api/tts", "/api/signal", "/api/mcp",
                    "/api/memory-search", "/api/task-delete", "/api/task-edit",
                    "/api/playbook-add", "/api/playbook-remove", "/api/hitl",
                    "/api/notify", "/api/mission-start", "/api/mission-update",
                    "/api/mission-finish")
GUEST_POST_PREFIXES = ("/api/memory/", "/api/llm/")
# Credential-Injection-Gateway (OneCLI-Muster): der Agent schickt seine
# Chat-Requests an /api/llm/<backend>/chat/completions statt direkt zum
# Router; der Manager haengt beim Weiterleiten den Authorization-Header aus
# den Settings an. So verlassen die LLM-Keys den Host NIE: eine kompromittierte
# VM kann hoechstens ueber den Manager Modelle rufen (sichtbar, drosselbar),
# aber keinen Key exfiltrieren und ausserhalb des Systems weiterbenutzen.
LLM_PROXY_UPSTREAMS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "orcarouter": ("https://api.orcarouter.ai/v1/chat/completions", "ORCAROUTER_API_KEY"),
}
# Gesetzte Geheimnisse verlassen den Manager nie im Klartext — die UI bekommt
# diesen Marker und schickt ihn beim Speichern unveraendert zurueck, wo er
# verworfen wird. Ein echter leerer Wert loescht den Eintrag weiterhin.
SETTINGS_KEEP = "__unchanged__"
# MCP_CONFIG trug die eingesetzten Secrets im Klartext (z. B. das HA-Bearer-
# Token). Gespeichert wird stattdessen MCP_SERVERS — nur die Katalognamen; die
# Werte holt der Agent zur Laufzeit ueber /api/mcp-config.
NEVER_PERSIST = SECRET_PARAMS | {"MCP_CONFIG"}

POOL = "172.30.0.0/16"
def _uplink_iface():
    """Interface der Default-Route ("… dev eno2 …")."""
    try:
        out = subprocess.run(["ip", "-o", "route", "show", "default"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        return out[out.index("dev") + 1]
    except Exception:
        return ""


def _pick_hostif():
    """Uplink fuer die MASQUERADE-Regel der Gaeste. Ein fest verdrahteter
    NIC-Name ist eine stille Falle: benennt ihn der Kernel um (Update, neue
    Hardware, Reboot), zeigt die NAT-Regel ins Leere — die microVMs erreichen
    dann weder DNS noch LLM, und nichts protokolliert einen Fehler. Deshalb
    zaehlt ein gesetzter Name nur, wenn es das Interface wirklich gibt;
    sonst gewinnt die Default-Route."""
    want = os.environ.get("HOSTIF", "")
    if want and os.path.exists(f"/sys/class/net/{want}"):
        return want
    auto = _uplink_iface()
    if want and auto:
        print(f"[net] HOSTIF={want} existiert nicht — nutze {auto} (Default-Route)",
              flush=True)
    return auto or want or "eno2"


HOSTIF = _pick_hostif()
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


# ---- Signal-Versand --------------------------------------------------------
# Agenten koennen dem Nutzer von sich aus schreiben (fertige Aufgabe, Fund,
# Rueckfrage). Der Versand laeuft ueber den Manager, nicht aus der VM:
#
#   * Der Empfaenger muss in ALLOWED_SENDERS stehen — also in genau der Liste,
#     die den Bot auch steuern darf. Ein Agent kann damit NUR an Leute
#     schreiben, die ihm ohnehin Befehle geben duerfen. Ohne diese Fessel
#     waere das Werkzeug ein Versandapparat fuer beliebige Nummern, und ein
#     uebernommener oder nur schlecht gelaunter Agent koennte in fremdem Namen
#     Nachrichten verschicken.
#   * Die Bot-Nummer und der API-Zugang bleiben im Host. Die VM sieht sie nie.
#   * Eine Drossel begrenzt den Schaden einer Schleife.
SIGNAL_DEFAULT_API = "https://signalapi.kat56.de"
SIGNAL_MAX_CHARS = 3500          # signal-cli nimmt mehr, Lesbarkeit nicht
SIGNAL_RATE = (10, 300)          # hoechstens 10 Nachrichten je 5 Minuten
_signal_sent = []                # Zeitstempel der letzten Sendungen
_signal_lock = threading.Lock()


def signal_recipients():
    """Erlaubte Empfaenger aus den Einstellungen (kommagetrennt)."""
    raw = (load_settings().get("ALLOWED_SENDERS") or "")
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def signal_send(text, to=None):
    """(ok, meldung). Schickt eine Nachricht ueber die signal-cli-REST-API."""
    s = load_settings()
    api = (s.get("SIGNAL_API") or SIGNAL_DEFAULT_API).rstrip("/")
    number = (s.get("SIGNAL_NUMBER") or "").strip()
    allowed = signal_recipients()
    if not number:
        return False, "SIGNAL_NUMBER is not configured (Settings)"
    if not allowed:
        return False, "ALLOWED_SENDERS is empty — no permitted recipient"
    to = (to or "").strip() or allowed[0]
    if to not in allowed:
        # Absichtlich mit Liste: der Agent soll den Fehler beheben koennen,
        # ohne dass ein Mensch nachsieht. Geheim ist daran nichts — es sind
        # die Nummern, die den Bot ohnehin steuern.
        return False, f"recipient {to} not permitted; allowed: {', '.join(allowed)}"

    text = (text or "").strip()
    if not text:
        return False, "empty message"
    text = text[:SIGNAL_MAX_CHARS]

    limit, window = SIGNAL_RATE
    now = time.time()
    with _signal_lock:
        _signal_sent[:] = [t for t in _signal_sent if now - t < window]
        if len(_signal_sent) >= limit:
            return False, f"rate limit: max {limit} messages per {window // 60} min"
        _signal_sent.append(now)

    body = json.dumps({"message": text, "number": number, "recipients": [to]}).encode()
    req = urllib.request.Request(f"{api}/v2/send", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=90).read()
        return True, f"sent to {to} ({len(text)} chars)"
    except urllib.error.HTTPError as e:
        return False, f"signal API HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:
        return False, f"signal API unreachable: {e!r}"


# ---- HITL: Freigabe riskanter Tool-Aufrufe per Signal ----------------------
# Ein Agent (opt-in per HITL=1) fragt vor einem riskanten Tool hier an; wir
# fragen den Nutzer per Signal ("ok <id>" / "nein <id>") und der Agent pollt den
# Status. Klappt der Signal-Versand nicht (kein Empfaenger konfiguriert), geben
# wir KEINE id zurueck -> der Agent blockiert dann nicht. In-Memory, kurzlebig.
_hitl_lock = threading.Lock()
_hitl = {}
HITL_TTL = 600


def hitl_create(instance, tool, target):
    hid = uuid.uuid4().hex[:8]
    now = time.time()
    with _hitl_lock:
        for k in [k for k, v in _hitl.items() if now - v["ts"] > HITL_TTL]:
            _hitl.pop(k, None)
        _hitl[hid] = {"tool": tool, "target": target, "instance": instance,
                      "status": "pending", "ts": now}
    msg = (f"🔒 Freigabe noetig: Agent '{instance}' will {tool}"
           + (f" ({target})" if target else "")
           + f".\nAntworte 'ok {hid}' zum Erlauben oder 'nein {hid}' zum Ablehnen.")
    ok, _m = signal_send(msg)
    if not ok:
        with _hitl_lock:
            _hitl.pop(hid, None)
        return None
    return hid


def hitl_status(hid):
    with _hitl_lock:
        v = _hitl.get(hid)
        return v["status"] if v else "unknown"


def hitl_resolve(hid, approve):
    with _hitl_lock:
        v = _hitl.get(hid)
        if not v or v["status"] != "pending":
            return False
        v["status"] = "approved" if approve else "denied"
        return True


# ---- Signal-Empfang (Long-Poll) --------------------------------------------
# signal-cli-rest (Modus "native") kennt keine Webhooks — es POSTet nicht zu
# uns. Also holen WIR ab: ein Long-Poll auf /v1/receive kommt in dem Moment
# zurueck, in dem eine Nachricht eintrifft. Jede Nachricht wird genau einmal
# geliefert (der Aufruf leert die Warteschlange). Eine erlaubte Nachricht
# landet im gemeinsamen Chat-Store und feuert denselben orchestrator_ping, den
# App/Web nutzen — der Orchestrator reagiert also binnen Sekunden statt erst
# beim naechsten Heartbeat. Antworten schickt er ueber send_signal zurueck.
def _signal_inbound(sender, text):
    chat_log_append("orchestrator", sender, f"[Signal] {text}", "", kind="signal")
    try:
        orchestrator_ping()
    except Exception:
        pass


SIGNAL_LOG = os.path.join(BASE, "signal-debug.log")
# native-mode: Empfang und Versand sperren dasselbe Konto. Nach einer
# eingegangenen Nachricht pausiert der Empfaenger kurz — ein kontentionsfreies
# Fenster, in dem der Orchestrator seine Antwort zuegig rausschicken kann.
# (Sauber loesen wuerde das der json-rpc-Modus des Gateways.)
SIGNAL_REPLY_WINDOW = int(os.environ.get("SIGNAL_REPLY_WINDOW", "60"))


def _slog(m):
    """Diagnose in eine LESBARE Datei — das systemd-Journal ist fuer den
    Nutzer nicht zugaenglich. Bei Bedarf einfach loeschen."""
    try:
        with open(SIGNAL_LOG, "a") as f:
            f.write(f"{time.strftime('%F %T')} {m}\n")
    except OSError:
        pass


def _handle_signal_envelope(env, allowed):
    """Eine Huelle verarbeiten: erlaubte Textnachricht -> Posteingang + Trigger."""
    e = (env.get("envelope") or {}) if isinstance(env, dict) else {}
    dm = e.get("dataMessage") or {}
    text = (dm.get("message") or "").strip()
    src = e.get("sourceNumber") or e.get("source") or ""
    uuid_ = e.get("sourceUuid") or ""
    grp = (dm.get("groupInfo") or {}).get("groupId")
    _slog(f"env srcNum={e.get('sourceNumber')!r} srcUuid={uuid_!r} source={e.get('source')!r} "
          f"group={grp!r} text={text[:60]!r} typ={'data' if dm else list(e.keys())}")
    if not text:
        return
    if allowed and src not in allowed and uuid_ not in allowed:
        _slog(f"ignoriert: Absender {src or uuid_} nicht in {sorted(allowed)}")
        return
    # HITL-Freigabe? Erlaubter Absender antwortet "ok <id>" / "nein <id>".
    mo = re.match(r"^(ok|ja|yes|approve|nein|no|deny|ablehnen)\s+([0-9a-f]{8})$",
                  text.lower().strip())
    if mo:
        approve = mo.group(1) in ("ok", "ja", "yes", "approve")
        done = hitl_resolve(mo.group(2), approve)
        _slog(f"HITL {mo.group(2)} -> {'approved' if approve else 'denied'} (found={done})")
        return
    _slog(f"-> inject von {src or uuid_}: {text[:60]!r}")
    _signal_inbound(src or uuid_, text)


# --- minimaler WebSocket-Client (stdlib) fuer den json-rpc-Empfang -----------
def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _ws_frame(sock):
    """Server-Frame lesen -> (opcode, payload). Server maskiert nicht."""
    hdr = _recvn(sock, 2)
    if hdr is None:
        return None, b""
    opcode = hdr[0] & 0x0F
    masked = hdr[1] & 0x80
    ln = hdr[1] & 0x7F
    if ln == 126:
        ext = _recvn(sock, 2); ln = struct.unpack(">H", ext)[0] if ext else 0
    elif ln == 127:
        ext = _recvn(sock, 8); ln = struct.unpack(">Q", ext)[0] if ext else 0
    mask = _recvn(sock, 4) if masked else b""
    data = _recvn(sock, ln) if ln else b""
    if data is None:
        return None, b""
    if masked and data:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data


def _ws_send(sock, opcode, data=b""):
    """Client->Server-Frame, maskiert (RFC-Pflicht)."""
    ln = len(data)
    out = bytes([0x80 | opcode])
    if ln < 126:
        out += bytes([0x80 | ln])
    elif ln < 65536:
        out += bytes([0x80 | 126]) + struct.pack(">H", ln)
    else:
        out += bytes([0x80 | 127]) + struct.pack(">Q", ln)
    m = os.urandom(4)
    out += m + bytes(c ^ m[i % 4] for i, c in enumerate(data))
    sock.sendall(out)


def _ws_connect(host, path, port=443):
    raw = socket.create_connection((host, port), timeout=30)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(1024)
        if not chunk:
            raise RuntimeError("handshake abgebrochen")
        resp += chunk
        if len(resp) > 8192:
            break
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise RuntimeError("kein 101: " + resp[:120].decode("latin1", "replace"))
    return sock


def _signal_receiver():
    """json-rpc-Modus: der Gateway pusht Nachrichten ueber einen WebSocket
    (Echtzeit). Empfang und Versand laufen jetzt parallel — keine Konto-Sperre,
    keine Sende-Pause mehr noetig."""
    _slog("receiver gestartet (json-rpc websocket)")
    while True:
        s = load_settings()
        number = (s.get("SIGNAL_NUMBER") or "").strip()
        allowed = set(signal_recipients())
        if not number:
            time.sleep(30)
            continue
        host = urllib.parse.urlparse(s.get("SIGNAL_API") or SIGNAL_DEFAULT_API).hostname or ""
        path = f"/v1/receive/{urllib.parse.quote(number)}"
        try:
            sock = _ws_connect(host, path)
            _slog("websocket verbunden")
        except Exception as e:
            _slog(f"ws-connect-fehler: {e!r:.150}")
            time.sleep(10)
            continue
        sock.settimeout(300)          # laengere Stille -> reconnect, haelt frisch
        try:
            while True:
                opcode, data = _ws_frame(sock)
                if opcode is None:
                    _slog("websocket zu -> reconnect"); break
                if opcode == 0x9:                     # ping -> pong
                    _ws_send(sock, 0xA, data); continue
                if opcode == 0x8:                     # close
                    _slog("websocket close"); break
                if opcode not in (0x1, 0x2) or not data:
                    continue
                try:
                    msg = json.loads(data.decode("utf-8", "replace"))
                except ValueError:
                    continue
                for env in (msg if isinstance(msg, list) else [msg]):
                    _handle_signal_envelope(env, allowed)
        except socket.timeout:
            pass                                       # still -> reconnect
        except Exception as e:
            _slog(f"ws-fehler: {e!r:.150}")
        try:
            sock.close()
        except Exception:
            pass
        time.sleep(2)


# ---- Security Gateway ------------------------------------------------------
# Pro Chat ankreuzbar. Zwei Dinge, beide am Manager, nicht im Gast:
#
#   Text   unsichtbare Zeichen raus — Tag-Zeichen (U+E0020..E007F), Zero-Width,
#          Bidi-Overrides, Homoglyph-Leerzeichen. Das ist der Kanal, ueber den
#          man einem Agenten Anweisungen unterschiebt, die im Chatfenster
#          schlicht nicht zu sehen sind. Gefiltert wird in BEIDE Richtungen:
#          eine Antwort landet in chats.json und wird spaeter wieder gelesen.
#   Bilder EXIF/XMP/C2PA raus, bevor das Bild den Host verlaesst. Ein Foto vom
#          Handy traegt GPS-Koordinaten, Geraetenummer und Aufnahmezeit mit.
#
# Der Zustand liegt bewusst HIER und nicht im Chat-Objekt: chats.json wird
# zwischen App und Web gemerged, und jedes zusaetzliche Feld dort hat sich
# bisher als Bruchstelle erwiesen.
GATEWAY_FILE = os.path.join(BASE, "gateway.json")
_gateway_lock = threading.Lock()

try:
    from text_unicode import clean_text as _clean_unicode
except Exception:            # Datei fehlt -> Gateway meldet sich als inaktiv
    _clean_unicode = None


def load_gateway():
    try:
        with open(GATEWAY_FILE) as fh:
            d = json.load(fh)
            return {"chats": d.get("chats") or {}, "stats": d.get("stats") or {}}
    except (FileNotFoundError, ValueError):
        return {"chats": {}, "stats": {}}


def save_gateway(d):
    with _gateway_lock:
        try:
            with open(GATEWAY_FILE, "w") as fh:
                json.dump(d, fh)
            return True
        except OSError:
            return False


def gateway_on(chat_id):
    """Ohne Chat-Kennung ist das Gateway aus — ein Aufrufer, der nicht sagt,
    zu welchem Chat er gehoert, kann auch nicht angehakt worden sein."""
    if not chat_id or _clean_unicode is None:
        return False
    return bool(load_gateway()["chats"].get(str(chat_id)))


def gateway_count(chat_id, key, n):
    """Entfernte Zeichen/Bilder mitzaehlen. Still zu filtern waere das
    Unangenehmste: man will sehen, dass etwas drin war."""
    if not n:
        return
    with _gateway_lock:
        d = load_gateway()
        s = d["stats"].setdefault(str(chat_id), {})
        s[key] = s.get(key, 0) + n
        try:
            with open(GATEWAY_FILE, "w") as fh:
                json.dump(d, fh)
        except OSError:
            pass


def gateway_clean(text, chat_id, key):
    """Text saeubern und zaehlen. Gibt den Text unveraendert zurueck, wenn das
    Gateway aus ist."""
    if not text or not gateway_on(chat_id):
        return text
    out, st = _clean_unicode(text)
    gateway_count(chat_id, key, st.get("removed_count", 0) + st.get("replaced_count", 0))
    return out


class StreamGuard:
    """Saeubert einen Token-Strom, ohne ihn zu stauen.

    Geschnitten wird an der Wortgrenze: Unicode-Kleber (ZWJ in Emoji-Ketten,
    Tag-Zeichen) haengt immer an einem Zeichen, nie an einem Leerzeichen. Wer
    stur alle 4 KB schneidet, zerreisst dagegen eine Emoji-Kette und der
    Filter sieht einen Verbinder ohne Vorzeichen — und wirft ihn weg."""

    def __init__(self, chat_id, key="out"):
        self.chat_id = chat_id
        self.key = key
        self.buf = ""
        self.removed = 0

    def feed(self, chunk):
        self.buf += chunk
        cut = max(self.buf.rfind(" "), self.buf.rfind("\n"))
        if cut < 0:
            return ""
        head, self.buf = self.buf[:cut + 1], self.buf[cut + 1:]
        return self._clean(head)

    def flush(self):
        head, self.buf = self.buf, ""
        out = self._clean(head)
        if self.removed:
            gateway_count(self.chat_id, self.key, self.removed)
            self.removed = 0
        return out

    def _clean(self, s):
        if not s:
            return ""
        out, st = _clean_unicode(s)
        self.removed += st.get("removed_count", 0) + st.get("replaced_count", 0)
        return out


def strip_image_meta(b64):
    """EXIF/XMP/C2PA aus einem Base64-Bild schneiden. (bereinigt, entfernte Bloecke)

    Von Hand statt mit Pillow: Pillow ist hier nicht installiert, und ein
    Neu-Kodieren wuerde das Bild ausserdem verlustbehaftet anfassen. Hier
    bleiben die Bilddaten Byte fuer Byte gleich, es fallen nur Metadaten weg.
    Bei allem, was nicht sicher erkannt wird, bleibt das Bild unangetastet —
    ein kaputtes Bild waere schlimmer als ein Zeitstempel darin."""
    if not b64:
        return b64, 0
    prefix = ""
    payload = b64
    if b64.startswith("data:"):
        head, _, payload = b64.partition(",")
        prefix = head + ","
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        return b64, 0

    out, n = raw, 0
    if raw[:2] == b"\xff\xd8":                       # JPEG
        out, n = _jpeg_strip(raw)
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":            # PNG
        out, n = _png_strip(raw)
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        out, n = _webp_strip(raw)
    if not n:
        return b64, 0
    return prefix + base64.b64encode(out).decode(), n


def _jpeg_strip(raw):
    """APP1..APP15 raus (Exif, XMP, C2PA/JUMBF) — APP0/JFIF bleibt, das ist
    der Bildkopf. Danach kommt SOS und der komprimierte Rest; ab dort wird
    nichts mehr angefasst."""
    out = bytearray(raw[:2])
    i, n = 2, 0
    while i + 4 <= len(raw):
        if raw[i] != 0xFF:
            break
        m = raw[i + 1]
        if m == 0xDA:                                # Start of Scan -> Rest 1:1
            out += raw[i:]
            return bytes(out), n
        ln = int.from_bytes(raw[i + 2:i + 4], "big")
        if ln < 2 or i + 2 + ln > len(raw):
            return raw, 0                            # unerwartet -> nicht anfassen
        if 0xE1 <= m <= 0xEF or m == 0xFE:           # APP1..APP15, COM
            n += 1
        else:
            out += raw[i:i + 2 + ln]
        i += 2 + ln
    if i < len(raw):
        out += raw[i:]
    return bytes(out), n


def _png_strip(raw):
    """Textbloecke und eXIf raus. PNG ist in Bloecken mit Laenge und Pruefsumme
    aufgebaut, das laesst sich sauber trennen."""
    drop = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME", b"caBX"}
    out = bytearray(raw[:8])
    i, n = 8, 0
    while i + 8 <= len(raw):
        ln = int.from_bytes(raw[i:i + 4], "big")
        typ = raw[i + 4:i + 8]
        end = i + 12 + ln
        if end > len(raw):
            return raw, 0
        if typ in drop:
            n += 1
        else:
            out += raw[i:end]
        i = end
        if typ == b"IEND":
            break
    return bytes(out), n


def _webp_strip(raw):
    """EXIF/XMP-Bloecke aus dem RIFF-Container. Die Gesamtlaenge im Kopf muss
    danach stimmen, sonst halten manche Betrachter die Datei fuer defekt."""
    out = bytearray(raw[:12])
    i, n = 12, 0
    while i + 8 <= len(raw):
        typ = raw[i:i + 4]
        ln = int.from_bytes(raw[i + 4:i + 8], "little")
        end = i + 8 + ln + (ln & 1)                  # Bloecke sind gerade lang
        if end > len(raw):
            return raw, 0
        if typ in (b"EXIF", b"XMP "):
            n += 1
        else:
            out += raw[i:end]
        i = end
    if not n:
        return raw, 0
    out[4:8] = (len(out) - 8).to_bytes(4, "little")
    return bytes(out), n


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


# ---- Notifications: ausgelagert nach mgr/notify.py -------------------------
from mgr import notify as _notify  # noqa: E402
_notify.configure(BASE)
from mgr.notify import (load_notifications, notify_add, notif_mark_read,  # noqa: E402,F401
                        wait_notifs, NOTIF_MAX, NOTIF_RATE, _notif_sent)
_missions.notify_add = notify_add   # Injektion (mgr/missions)


# ---- Posteingang (Wasserzeichen) — chat-gekoppelt, bleibt hier ---------------
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
    # Verbrauch je LLM-Aufruf. Die Agenten melden ihn nach jedem Call an
    # /api/usage; der Manager erkennt die Instanz an der Quell-IP. cost ist
    # das, was der Anbieter fuer diesen Aufruf abrechnet (OpenRouter liefert
    # es bei "usage":{"include":true} mit) — 0.0, wenn er nichts nennt.
    c.execute("""CREATE TABLE IF NOT EXISTS llm_usage(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, instance TEXT, model TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_usage_inst_ts ON llm_usage(instance, ts)")
    # Semantisches Langzeitgedaechtnis: je Erinnerung Text + Embedding-Vektor
    # (als JSON). Die Suche laedt die Vektoren einer Instanz und rechnet Cosinus
    # im Speicher — bei persoenlicher Groessenordnung (Hunderte) reicht das ohne
    # Vektor-DB. Vektoren sind normalisiert, Cosinus = Skalarprodukt.
    c.execute("""CREATE TABLE IF NOT EXISTS semantic_memory(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, instance TEXT, mkey TEXT, text TEXT, vec TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sem_inst ON semantic_memory(instance)")
    return c


def usage_add(instance, model, prompt_tokens, completion_tokens, cost):
    try:
        with _hist_lock, _hist_conn() as c:
            c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,"
                      "completion_tokens,cost) VALUES(?,?,?,?,?,?)",
                      (int(time.time()), str(instance)[:80], str(model)[:120],
                       int(prompt_tokens or 0), int(completion_tokens or 0),
                       float(cost or 0.0)))
        return "ok"
    except Exception as e:
        return f"error: {e!r}"


def usage_summary():
    """Verbrauch je Instanz: heute (lokale Mitternacht) und gesamt."""
    midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    out = {}
    try:
        with _hist_lock, _hist_conn() as c:
            for since, key in ((0, "total"), (midnight, "today")):
                for inst, calls, pt, ct, cost in c.execute(
                        "SELECT instance, COUNT(*), SUM(prompt_tokens), "
                        "SUM(completion_tokens), SUM(cost) FROM llm_usage "
                        "WHERE ts >= ? GROUP BY instance", (since,)):
                    out.setdefault(inst, {})[key] = {
                        "calls": calls, "in": pt or 0, "out": ct or 0,
                        "cost": round(cost or 0.0, 4)}
    except Exception:
        return {}
    empty = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
    for v in out.values():
        v.setdefault("today", dict(empty))
        v.setdefault("total", dict(empty))
    return out


def usage_for(instance, since=0):
    """Verbrauch EINER Instanz seit `since` (epoch). Fuers Activity-Panel:
    Tokens rein/raus, Kosten und Anzahl LLM-Aufrufe im gewaehlten Zeitfenster.
    Tokens fallen pro LLM-Turn an (nicht pro Tool-Aufruf) — darum eine Summe,
    keine Zuordnung zu einzelnen Audit-Zeilen."""
    try:
        with _hist_lock, _hist_conn() as c:
            row = c.execute(
                "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens), SUM(cost) "
                "FROM llm_usage WHERE instance=? AND ts>=?",
                (instance, int(since or 0))).fetchone()
        calls, pt, ct, cost = row or (0, 0, 0, 0)
        return {"calls": calls or 0, "in": pt or 0, "out": ct or 0,
                "cost": round(cost or 0.0, 4)}
    except Exception:
        return {"calls": 0, "in": 0, "out": 0, "cost": 0.0}


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


def update_task(task_id, message=None, schedule=None):
    """Aufgabe aendern. Ein geaenderter Zeitplan wird sofort neu terminiert —
    sonst liefe die Aufgabe noch einmal nach dem alten Plan. Ein leerer Plan
    macht aus der Wiederholung eine einmalige Aufgabe (faellig jetzt); eine
    gerade laufende Aufgabe wird nicht angefasst."""
    tasks = load_tasks()
    t = next((x for x in tasks if x.get("id") == task_id), None)
    if t is None:
        return "unknown"
    if t.get("status") == "running":
        return "task is running — try again when it is done"
    now = int(time.time())
    if message is not None and str(message).strip():
        t["message"] = str(message).strip()
    if schedule is not None:
        sched = str(schedule).strip()
        if sched != t.get("schedule", ""):
            t["schedule"] = sched
            t["next_run"] = _next_run(sched, now) if sched else now
            t["status"] = "scheduled" if sched else "pending"
    t["updated"] = now
    save_tasks(tasks)
    when = time.strftime("%d.%m. %H:%M", time.localtime(t["next_run"]))
    return f"task {task_id} updated (next run {when})"


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
    "/fresh "   # zustandslos: eigener Wegwerf-Kontext, kein Aufblaehen, kein
                # Wegwischen eines laufenden App-Chats (geteiltes _history).
    "Heartbeat (Sofort-Trigger): 1) read_inbox — neue Nutzer-Nachrichten. "
    "2) Fuer jede mit Handlungsbedarf: recall_tasks (keine Dubletten), dann "
    "list_agents und create_task an die FAEHIGE Instanz (z. B. hass fuer "
    "HomeAssistant) oder ephemeral. 3) Nachrichten, die mit [Signal] beginnen, "
    "kamen per Signal: schicke die Antwort bzw. Bestaetigung mit send_signal "
    "zurueck (kurz) — OHNE Nummer/Empfaenger anzugeben, sie geht automatisch an "
    "den Nutzer; erfinde KEINE Nummer. 4) missions pruefen: haengt ein Schritt auf "
    "doing, obwohl sein Task laengst fertig ist (recall_tasks)? Dann mission_update "
    "und den naechsten Schritt anstossen. Kurz halten. Nichts zu tun? Melde: nichts zu tun.")
MISSION_ADVANCE_MSG = (
    "/fresh Missions-Fortschritt (Sofort-Trigger nach Task-Abschluss): Der Task "
    "'{task_id}' zu Mission '{mid}' ({goal}) ist fertig. 1) recall_tasks nach dem "
    "Ergebnis dieses Tasks. 2) mission_update: Schritt {step} auf done/failed "
    "setzen, Ergebnis knapp eintragen. 3) Den NAECHSTEN offenen Schritt anstossen "
    "(create_task an die faehige Instanz oder ephemeral, task-id per "
    "mission_update am Schritt vermerken). 4) Kein offener Schritt mehr? "
    "mission_finish mit kurzem Fazit. Blockiert? notify an den Nutzer. Kurz halten.")


def _mission_advance_fire(task_id):
    """Nach Task-Abschluss: gehoert der Task zu einem Missionsschritt, den
    Orchestrator sofort einen Fortschritts-Vorstoss machen lassen (statt auf
    den naechsten Heartbeat zu warten). Best-effort im Hintergrund-Thread."""
    inst, m, st = mission_for_task(task_id)
    if not m or inst != ORCH_INSTANCE:
        return
    msg = MISSION_ADVANCE_MSG.format(task_id=task_id, mid=m["id"],
                                     goal=m["goal"][:80], step=st["n"])

    def go():
        try:
            _run_named(ORCH_INSTANCE, msg)
        except Exception as e:
            print("mission-advance:", repr(e), flush=True)
    threading.Thread(target=go, daemon=True).start()


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


_mi_sweep_ts = [0.0]


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
                # Missions-Sofort-Trigger: wartet ein Missionsschritt auf diesen
                # Task, macht der Orchestrator direkt den naechsten Vorstoss.
                try:
                    _mission_advance_fire(t["id"])
                except Exception:
                    pass
                ran = True
                break
        except Exception as e:
            print("task-worker:", repr(e), flush=True)
        if not ran:
            time.sleep(5)
            # TTL-Sweep im Leerlauf, hoechstens einmal pro Stunde.
            now = time.time()
            if now - _mi_sweep_ts[0] > 3600:
                _mi_sweep_ts[0] = now
                try:
                    mission_ttl_sweep()
                except Exception:
                    pass


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
    {"name": "offload_read", "desc": "Ausgelagerte (gekuerzte) Tool-Ausgabe nachlesen"},
    {"name": "http_fetch", "desc": "URL abrufen (HTTP)"},
    {"name": "read_pdf", "desc": "PDF-Text extrahieren (Datei oder URL)"},
    {"name": "web_search", "desc": "Websuche (DuckDuckGo) — braucht Internet"},
    {"name": "spawn_subagent", "desc": "Ephemeren Subagenten starten"},
    {"name": "create_task", "desc": "Aufgabe einreihen (faehige Instanz oder ephemer)"},
    {"name": "read_inbox", "desc": "Neue Nutzer-Nachrichten (Signal/App/Web) lesen"},
    {"name": "list_tasks", "desc": "Laufende/geplante Aufgaben mit IDs auflisten"},
    {"name": "delete_task", "desc": "Eine laufende/geplante Aufgabe per ID loeschen"},
    {"name": "edit_task", "desc": "Nachricht/Zeitplan einer Aufgabe per ID aendern"},
    {"name": "mission_start", "desc": "Mission anlegen: Ziel + Schritte (nur Orchestrator)"},
    {"name": "missions", "desc": "Offene Missionen mit Status auflisten (nur Orchestrator)"},
    {"name": "mission_update", "desc": "Missionsschritt fortschreiben (nur Orchestrator)"},
    {"name": "mission_finish", "desc": "Mission abschliessen (nur Orchestrator)"},
    {"name": "send_signal", "desc": "Signal-Nachricht an den Nutzer senden (nur erlaubte Nummern)"},
    {"name": "notify", "desc": "Push-Benachrichtigung an App + Web-Manager (Titel + Text)"},
    {"name": "oracle", "desc": "Zweitmeinung vor riskanten Aktionen (challenged Annahmen, handelt nie)"},
    {"name": "list_agents", "desc": "Verfuegbare Agenten + Faehigkeiten (Routing)"},
    {"name": "recall_tasks", "desc": "Fruehere Aufgaben/Ergebnisse abfragen (Stammwissen)"},
    {"name": "list_skills", "desc": "Verfügbare Skills auflisten"},
    {"name": "load_skill", "desc": "Skill in den Kontext laden"},
    {"name": "memory_store", "desc": "Wert dauerhaft merken"},
    {"name": "memory_recall", "desc": "Gemerkten Wert abrufen"},
    {"name": "playbook_add", "desc": "Dauerhafte Regel/Playbook festhalten (gilt immer)"},
    {"name": "playbooks", "desc": "Playbooks (feste Regeln) auflisten"},
    {"name": "playbook_forget", "desc": "Playbook per ID entfernen"},
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


# Reihenfolge = Anzeige-Logik in render()/list_agents: der erste vorhandene
# Schluessel ist das Modell der Instanz.
MODEL_KEYS = ("OPENROUTER_MODEL", "ORCAROUTER_MODEL", "ANTHROPIC_MODEL", "PI_MODEL", "PRIME_MODEL", "LLAMA_MODEL")
# Fuer den Provider-Wechsel per set_model("provider:model"): Providername -> Key.
PROVIDER_MODEL_KEY = {"openrouter": "OPENROUTER_MODEL", "orcarouter": "ORCAROUTER_MODEL",
                      "anthropic": "ANTHROPIC_MODEL", "pi": "PI_MODEL",
                      "prime": "PRIME_MODEL", "llama": "LLAMA_MODEL"}


def set_model(name, model):
    """Modell einer bestehenden Instanz wechseln. Setzt genau den Schluessel,
    den die Instanz bereits nutzt (kein neuer wird erfunden — sonst wuesste
    niemand, welcher Provider gemeint ist). Wirkt beim naechsten Start
    (env-basiert), wie die Werkzeug-Allowlist."""
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    model = str(model or "").strip()
    if not model:
        return "error: no model given"
    cfg = inst.setdefault("config", {})
    # Provider-Wechsel: "orcarouter:tencent/hy3" stellt zusaetzlich das Backend
    # um (setzt dessen MODEL_KEY, entfernt die anderen). Ohne Praefix bleibt es
    # beim vorhandenen Provider — nur das Modell wechselt. Der Doppelpunkt-Test
    # greift NUR bei bekanntem Providernamen, damit ":free"-Modellvarianten
    # (z. B. "mistralai/...:free") nicht faelschlich als Provider gelesen werden.
    if ":" in model and model.split(":", 1)[0] in PROVIDER_MODEL_KEY:
        prov, mdl = model.split(":", 1)
        key = PROVIDER_MODEL_KEY[prov]
        for k in MODEL_KEYS:
            cfg.pop(k, None)
        cfg[key] = mdl.strip()
        model = mdl.strip()
    else:
        key = next((k for k in MODEL_KEYS if k in cfg), None)
        if key is None:
            return (f"error: instance '{name}' has no model setting "
                    f"({'/'.join(MODEL_KEYS)})")
        cfg[key] = model
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    running = " (applies after stop/start)" if is_running(inst) else ""
    return f"model for '{name}' set to {model}{running}"


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


# Gaeste durften bisher mit internet=on ueberallhin — auch ins ganze LAN.
# Home Assistant und Portainer waren damit von JEDER VM erreichbar, ob ihr der
# MCP zugewiesen war oder nicht (die Tokens schuetzt der Broker, die Tuer
# stand trotzdem offen). Jetzt: Internet ja, LAN nein — ausser den Endpunkten
# der MCPs, die in MCP_SERVERS der Instanz stehen, und dem DNS der Gaeste.
# DNS fuer die Gaeste (landet via guest-init in resolv.conf). Site-spezifisch —
# auf fremden Installationen per Env setzen; 1.1.1.1 funktioniert ueberall.
GUEST_DNS = os.environ.get("GUEST_DNS", "10.0.0.245")
_PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def _mcp_endpoints(inst):
    """LAN-Ziele (ip, port), die diese Instanz laut MCP_SERVERS braucht.
    Aus dem Katalog gelesen, nicht aus der Instanz — dort stehen nur Namen.
    Nur IP-Literale: ein Hostname im Katalog, der ins LAN aufloest, wuerde
    hier NICHT freigeschaltet (bewusst; dann lieber die IP eintragen)."""
    names = {x for x in (inst.get("config", {}).get("MCP_SERVERS", "") or "").split(",") if x}
    if not names:
        return []
    out = []
    for m in load_mcps():
        if m.get("name") not in names:
            continue
        for scheme, host, port in re.findall(
                r"(https?)://(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?", json.dumps(m)):
            try:
                import ipaddress
                if not ipaddress.ip_address(host).is_private:
                    continue          # oeffentliche Ziele deckt die Internet-Regel
            except ValueError:
                continue
            out.append((host, int(port or (443 if scheme == "https" else 80))))
    return sorted(set(out))


def _llama_endpoint(inst):
    """(ip, port) des llama.cpp-Servers, falls die Instanz ihn nutzt UND er im
    privaten Netz liegt — dann muss das Gating ihn durchlassen. Ein Endpoint
    auf dem Host (ueber das Gateway erreichbar) oder im Internet braucht keine
    Sonderregel."""
    ep = (inst.get("config", {}).get("LLAMA_ENDPOINT") or "").strip()
    if not ep:
        return None
    m = re.search(r"(https?)://(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?", ep)
    if not m:
        return None
    import ipaddress
    scheme, host, port = m.group(1), m.group(2), m.group(3)
    try:
        if not ipaddress.ip_address(host).is_private:
            return None
    except ValueError:
        return None
    return (host, int(port or (443 if scheme == "https" else 80)))


def _fc_chain(inst):
    return "FC-" + re.sub(r"[^a-zA-Z0-9_.-]", "", inst["name"])[:24]


def apply_internet(inst, allow):
    """Egress-Regeln der Instanz setzen/entfernen. `allow=False` heisst: die VM
    darf ihr eigenes /30 nicht verlassen — kein LAN, kein Internet. Der
    Manager-Broker am Gateway (8700) bleibt erreichbar (host-lokal, INPUT).
    Damit auch der LLM-Endpunkt: ein Agent ohne Internet kann NICHT denken.

    Bei allow=True bekommt die Instanz eine eigene FORWARD-Kette:
      1. ihre MCP-Endpunkte (tcp, gezielt)     -> ACCEPT
      2. der Gast-DNS (53)                     -> ACCEPT
      3. private Netze                         -> REJECT (nicht DROP: der
         Agent soll sofort scheitern, nicht 30 s in einen Timeout laufen)
      4. alles ausserhalb des Pools (Internet) -> ACCEPT
    Der Rueckweg bleibt die generische Regel: Antworten sind durch NAT ohnehin
    nur fuer Verbindungen moeglich, die der Gast selbst geoeffnet hat."""
    n = net_of(inst)
    chain = _fc_chain(inst)

    # Altbestand abraeumen, idempotent: Sprungregel, Kette, alte Direktregel.
    sh("iptables", "-D", "FORWARD", "-i", n["tap"], "-j", chain, check=False)
    sh("iptables", "-F", chain, check=False)
    sh("iptables", "-X", chain, check=False)
    while sh("iptables", "-C", "FORWARD", "-i", n["tap"], "!", "-d", POOL,
             "-j", "ACCEPT", check=False).returncode == 0:
        sh("iptables", "-D", "FORWARD", "-i", n["tap"], "!", "-d", POOL,
           "-j", "ACCEPT", check=False)

    back = ["-o", n["tap"], "!", "-s", POOL]
    have_back = sh("iptables", "-C", "FORWARD", *back, "-j", "ACCEPT", check=False).returncode == 0
    if not allow:
        if have_back:
            sh("iptables", "-D", "FORWARD", *back, "-j", "ACCEPT", check=False)
        return

    sh("iptables", "-N", chain, check=False)
    allow = list(_mcp_endpoints(inst))
    lp = _llama_endpoint(inst)
    if lp:
        allow.append(lp)
    for ip, port in allow:
        sh("iptables", "-A", chain, "-d", ip, "-p", "tcp", "--dport", str(port),
           "-j", "ACCEPT", check=False)
    for proto in ("udp", "tcp"):
        sh("iptables", "-A", chain, "-d", GUEST_DNS, "-p", proto, "--dport", "53",
           "-j", "ACCEPT", check=False)
    for net in _PRIVATE_NETS:
        sh("iptables", "-A", chain, "-d", net, "-j", "REJECT", check=False)
    sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "ACCEPT", check=False)
    sh("iptables", "-I", "FORWARD", "1", "-i", n["tap"], "-j", chain, check=False)
    if not have_back:
        sh("iptables", "-I", "FORWARD", "1", *back, "-j", "ACCEPT", check=False)


def teardown_tap(inst):
    # Regeln zeigen auf den Tap-NAMEN und ueberleben das Loeschen des Geraets —
    # ohne Aufraeumen sammeln sich tote Ketten an.
    n = net_of(inst)
    chain = _fc_chain(inst)
    sh("iptables", "-D", "FORWARD", "-i", n["tap"], "-j", chain, check=False)
    sh("iptables", "-F", chain, check=False)
    sh("iptables", "-X", chain, check=False)
    sh("ip", "link", "del", n["tap"], check=False)


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
    if inst["name"] == ORCH_INSTANCE:   # nur der Orchestrator darf Tasks verwalten
        cfg["TASK_ADMIN"] = "1"
    # Key-Injection-Proxy aktiv? Dann schickt der Agent Chat-Requests an den
    # Manager statt direkt zum Router — die VM sieht so nie einen LLM-Key
    # (auch nicht per Secret-Broker). Der Schalter liegt in den geteilten
    # Settings, damit ALLE Instanzen konsistent umgestellt werden.
    if load_settings().get("LLM_KEY_PROXY") == "1":
        cfg["KEY_PROXY"] = "1"
    d = os.path.join(RUN_DIR, f"{inst['name']}.cfgdir")
    os.makedirs(d, exist_ok=True)
    # Tool-Plugins (firecracker/plugins/*.py) mit auf die Disk — der Agent laedt
    # sie beim Start aus /config/plugins. Neues Plugin = Datei + Stop/Start.
    pdst = os.path.join(d, "plugins")
    shutil.rmtree(pdst, ignore_errors=True)
    psrc = os.path.join(BASE, "plugins")
    if os.path.isdir(psrc):
        os.makedirs(pdst, exist_ok=True)
        for f0 in sorted(os.listdir(psrc)):
            if f0.endswith(".py"):
                shutil.copy2(os.path.join(psrc, f0), os.path.join(pdst, f0))
    with open(os.path.join(d, "config.env"), "w") as f:
        for k, v in cfg.items():
            # Werte quoten (EXTRA_MOUNTS u.a. enthalten Shell-Metazeichen wie | und ;)
            f.write(f"{k}={shlex.quote(str(v))}\n")
    img = os.path.join(RUN_DIR, f"{inst['name']}.config.ext4")
    with open(img, "wb") as f:
        f.truncate(16 * 1024 * 1024)
    sh("mkfs.ext4", "-F", "-q", "-d", d, img, check=False)
    return img


# ---- Overlay-Rootfs ---------------------------------------------------------
# Fuer Images in OVERLAY_ROOTFS bootet die VM mit der GETEILTEN Basis read-only
# (Firecracker blockt Schreibzugriffe auf Host-Ebene -> kein Journal-Konflikt)
# plus einem kleinen rw-Upper-Image je Instanz; der Gast-init legt daraus per
# overlayfs+pivot_root die Wurzel zusammen. Vorteil: keine 2-GB-Kopie je Start,
# und mit inst["persist_disk"]=true ueberlebt die Schreibschicht (Installationen!)
# Stop/Start. Andere Images laufen unveraendert ueber private_rootfs().
OVERLAY_ROOTFS = {"instances/openrouter-rootfs.ext4", "instances/claude-rootfs.ext4"}
UPPER_SIZE_MB = 1024          # Wegwerf-Schicht je Start
UPPER_PERSIST_SIZE_MB = 4096  # persistente Schicht (apt/pip brauchen Luft); sparse


def upper_path(inst):
    if inst.get("persist_disk"):
        return os.path.join(INST_DIR, f"{inst['name']}-upper.ext4")
    return os.path.join(RUN_DIR, f"{inst['name']}.upper.ext4")


def make_upper(inst):
    """Leeres (oder bei persist: vorhandenes) Upper-Image liefern."""
    p = upper_path(inst)
    if inst.get("persist_disk") and os.path.exists(p):
        return p
    size = UPPER_PERSIST_SIZE_MB if inst.get("persist_disk") else UPPER_SIZE_MB
    tmp = p + ".new"
    with open(tmp, "wb") as fh:          # sparse, ohne externes truncate
        fh.truncate(size * 1024 * 1024)
    mkfs = shutil.which("mkfs.ext4") or "/sbin/mkfs.ext4"
    sh(mkfs, "-F", "-q", "-L", "fcupper", tmp)
    os.replace(tmp, p)
    return p


def reset_upper(name):
    """Persistente Schreibschicht loeschen (Factory-Reset). Nur im Stillstand."""
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if is_running(inst):
        return "error: instance is running — stop it first"
    n = 0
    for p in (os.path.join(INST_DIR, f"{name}-upper.ext4"),
              os.path.join(RUN_DIR, f"{name}.upper.ext4")):
        try:
            os.remove(p); n += 1
        except OSError:
            pass
    return f"disk reset ({n} layer(s) removed)" if n else "nothing to reset"


def set_persist_disk(name, on):
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if inst.get("rootfs") not in OVERLAY_ROOTFS:
        return "error: this template's rootfs has no overlay support (yet)"
    inst["persist_disk"] = bool(on)
    with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
        json.dump(inst, fh, indent=2)
    running = " (applies after stop/start)" if is_running(inst) else ""
    return f"persistent disk for '{name}' {'ON' if on else 'off'}{running}"


def private_rootfs(inst):
    """Frische Rootfs-Kopie fuer genau diese VM anlegen und deren Pfad liefern.

    Alle Instanzen eines Templates zeigten auf DASSELBE ext4-Image, beschreibbar.
    Zwei gleichzeitig laufende VMs teilen sich dann ein Journal — das ging so
    lange gut, wie kaum geschrieben wurde, und endete am 15.08. mit 'error
    loading journal' beim Boot. Deshalb: je Start eine eigene Kopie (sparse,
    ~sekundenschnell). Nebeneffekt, und zwar der gewollte: ein Neustart bootet
    immer das aktuelle Template-Image, Rootfs-Updates greifen wie bisher mit
    Stop/Start. Zustand, der bleiben soll, liegt ohnehin nicht hier, sondern
    zentral (memory.json, chats.json, katfs)."""
    src = os.path.join(BASE, inst["rootfs"])
    dst = os.path.join(RUN_DIR, f"{inst['name']}.rootfs.ext4")
    tmp = dst + ".new"
    # --sparse=always: das 2-GB-Image traegt ~550 MB; die Kopie soll ebenso
    # wenig belegen. Erst .new, dann umbenennen — eine halbe Kopie darf nie
    # als Rootfs starten.
    sh("cp", "--sparse=always", src, tmp)
    os.replace(tmp, dst)
    return dst


def gen_config(inst):
    n = net_of(inst)
    boot = (f"console=ttyS0 reboot=k panic=1 pci=off "
            f"ip={n['guest']}::{n['host']}:{n['mask']}::eth0:off init=/init")
    overlay = inst.get("rootfs") in OVERLAY_ROOTFS
    if overlay:
        drives = [{"drive_id": "rootfs", "path_on_host": os.path.join(BASE, inst["rootfs"]),
                   "is_root_device": True, "is_read_only": True}]
    else:
        drives = [{"drive_id": "rootfs", "path_on_host": private_rootfs(inst),
                   "is_root_device": True, "is_read_only": False}]
    cfg_disk = os.path.join(RUN_DIR, f"{inst['name']}.config.ext4")
    if os.path.exists(cfg_disk):
        drives.append({"drive_id": "config", "path_on_host": cfg_disk,
                       "is_root_device": False, "is_read_only": True})
    for j, d in enumerate(inst.get("extra_drives", [])):
        drives.append({"drive_id": f"data{j}", "path_on_host": d["path"],
                       "is_root_device": False, "is_read_only": d.get("readonly", False)})
    if overlay:
        # Letztes Drive = Upper; Geraetename ergibt sich aus der Position
        # (virtio-blk: vda, vdb, ...). Der Gast liest ihn aus /proc/cmdline.
        drives.append({"drive_id": "upper", "path_on_host": make_upper(inst),
                       "is_root_device": False, "is_read_only": False})
        boot += f" fc_upper=/dev/vd{chr(ord('a') + len(drives) - 1)}"
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
    mcp_hub_kill(inst["name"])
    # Die private Rootfs-Kopie ist nach dem Stop wertlos (der naechste Start
    # zieht eine frische) — nur Plattenplatz, also weg damit.
    for f in (f"{inst['name']}.rootfs.ext4", f"{inst['name']}.upper.ext4"):
        try:
            os.remove(os.path.join(RUN_DIR, f))
        except OSError:
            pass
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


# ---- Playbooks + Prompt-Templates: ausgelagert nach mgr/rules.py -----------
from mgr import rules as _rules  # noqa: E402
_rules.configure(BASE)
from mgr.rules import (load_playbooks, pb_list, pb_add, pb_remove, PB_MAX,  # noqa: E402,F401
                       load_prompts, prompt_upsert, prompt_delete, PROMPTS_MAX)


# ---- Missionen: ausgelagert nach mgr/missions.py (Import frueh, siehe oben) -
from mgr.missions import (load_missions, mission_list, mission_start,  # noqa: E402,F401
                          mission_update, mission_finish, mission_admin,
                          mission_ttl_sweep, mission_for_task,
                          MISSION_MAX_ACTIVE, MISSION_MAX_STEPS, MISSION_TTL_DAYS)


# ---- Semantisches Langzeitgedaechtnis --------------------------------------
EMBED_URL = f"http://127.0.0.1:{os.environ.get('EMBED_PORT', '8772')}"


def _embed(texts, kind):
    """Texte -> Vektoren ueber den Embedding-Dienst. None, wenn er nicht
    erreichbar ist (dann faellt der Aufrufer auf das flache Gedaechtnis
    zurueck, statt zu scheitern)."""
    try:
        body = json.dumps({"texts": texts, "kind": kind}).encode()
        req = urllib.request.Request(EMBED_URL + "/embed", data=body,
                                     headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=30)
        return json.loads(r.read()).get("vectors")
    except Exception:
        return None


def sem_store(instance, text, key=""):
    """Eine Erinnerung einbetten und ablegen. Gleicher (instance,key) wird
    ersetzt statt verdoppelt — so aktualisiert der Agent Bestehendes."""
    text = (text or "").strip()
    if not instance or not text:
        return False
    # Den key mit einbetten und mit ablegen: ist der value knapp ("Watzmann"),
    # traegt "lieblingsberg: Watzmann" wenigstens etwas Kontext in Vektor UND
    # in den spaeter angezeigten Treffer.
    full = f"{key}: {text}" if key else text
    vecs = _embed([full], "passage")
    if not vecs:
        return False
    c = _hist_conn()
    with c:
        if key:
            c.execute("DELETE FROM semantic_memory WHERE instance=? AND mkey=?",
                      (instance, key))
        c.execute("INSERT INTO semantic_memory(ts,instance,mkey,text,vec) VALUES(?,?,?,?,?)",
                  (int(time.time()), instance, key or "", full, json.dumps(vecs[0])))
    c.close()
    return True


_missions.sem_store = sem_store     # Injektion (mgr/missions)


def sem_search(instance, query, k=5):
    """Die k inhaltlich naechsten Erinnerungen einer Instanz. Cosinus im
    Speicher; die Vektoren sind normalisiert, also genuegt das Skalarprodukt."""
    query = (query or "").strip()
    if not instance or not query:
        return []
    qv = _embed([query], "query")
    if not qv:
        return []
    q = qv[0]
    c = _hist_conn()
    rows = c.execute("SELECT text, vec FROM semantic_memory WHERE instance=?",
                     (instance,)).fetchall()
    c.close()
    scored = []
    for text, vec in rows:
        try:
            v = json.loads(vec)
            scored.append((sum(a * b for a, b in zip(q, v)), text))
        except (ValueError, TypeError):
            continue
    scored.sort(reverse=True)
    return [{"score": round(s, 3), "text": t} for s, t in scored[:max(1, int(k))]]


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


MCP_HUB = f"http://127.0.0.1:{os.environ.get('MCP_HUB_PORT', '8771')}"


def mcp_hub_call(inst, server, payload):
    """JSON-RPC eines Gastes an 'seinen' MCP-Server im Hub durchreichen.

    Autorisierung HIER, nicht im Hub: der Server muss in MCP_SERVERS der
    Instanz stehen. argv/env baut der Manager aus Katalog + Policy-Secrets —
    beides verlaesst den Host nie; die VM schickt nur Servernamen und Payload."""
    names = [n for n in (inst.get("config", {}).get("MCP_SERVERS", "") or "").split(",") if n]
    if server not in names:
        return 403, {"error": f"server '{server}' not assigned to this instance"}
    blob = build_mcp_config([server], allowed=allowed_secret_keys(inst))
    spec = (json.loads(blob).get("mcpServers") or {}).get(server) if blob else None
    if not spec or not spec.get("command"):
        return 500, {"error": f"no catalog entry for '{server}'"}
    body = json.dumps({
        "key": f"{inst['name']}:{server}",
        "argv": [spec["command"], *spec.get("args", [])],
        "env": spec.get("env") or {},
        "payload": payload,
    }).encode()
    req = urllib.request.Request(MCP_HUB + "/rpc", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return 200, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"error": f"hub HTTP {e.code}"}
    except Exception as e:
        return 502, {"error": f"mcp hub unreachable: {e!r}"}


def mcp_hub_kill(inst_name):
    """Prozesse dieser Instanz im Hub beenden (beim Stop). Bester Versuch —
    ein toter Hub darf keinen Instanz-Stop verhindern."""
    try:
        req = urllib.request.Request(MCP_HUB + "/kill",
                                     data=json.dumps({"prefix": inst_name + ":"}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


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


# ---- katfs: ausgelagert nach mgr/katfs.py ----------------------------------
from mgr.katfs import (KATFS_HOST, KATFS_PORT, KATFS_BASE, KATFS_MAX_WRITE,  # noqa: E402,F401
                       katfs_share_for, katfs_proxy_fs, katfs_zip, katfs_status,
                       KATFS_ZIP_MAX_FILES, KATFS_ZIP_MAX_BYTES)

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


# ---- web -------------------------------------------------------------------
# PAGE (HTML/JS der Manager-Oberflaeche) liegt jetzt in mgr/ui.py.
from mgr.ui import PAGE  # noqa: E402

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


def h(v):
    """HTML-Escape fuers serverseitige Rendern. Der Instanzname ist beim Anlegen
    auf [a-z0-9-_] beschnitten, alles andere kommt aber frei aus Formularen oder
    Vorlagen — Modell-ID (freies Textfeld), Beschreibung, Mount-Pfade, Werkzeug-
    Liste. Ohne Escape landet das roh im Markup: wer eine Mount-Zeile oder eine
    eigene Modell-ID setzt, schreibt sonst Skript in die Admin-Seite."""
    return html.escape(str(v if v is not None else ""), quote=True)


def _fmt_tok(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n/1000:.1f}k".replace(".0k", "k")
    return str(n)


def _fmt_cost(c):
    c = float(c or 0.0)
    return f"${c:.2f}" if c >= 0.01 else f"${c:.4f}"


def render():
    rows = ""
    usage = usage_summary()
    for inst in load_instances():
        n = net_of(inst)
        run = is_running(inst)
        name = inst["name"]
        transport = (inst.get("config") or {}).get("TRANSPORT", "signal")
        cfgm = inst.get("config") or {}
        model = next((cfgm[k] for k in MODEL_KEYS if cfgm.get(k)), "")
        sub = " · ".join(x for x in (inst.get("template", ""), transport) if x)
        _chip = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor '
                 'stroke-width=1.6 stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px">'
                 '<rect x=6 y=6 width=12 height=12 rx=1/><path d="M9 2v2M15 2v2M9 20v2M15 20v2'
                 'M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>')
        # Chip ist klickbar: oeffnet den Modellwechsel-Dialog (editModel im PAGE-JS).
        model_line = (f"<button class='mono' style=\"font-size:12px;color:var(--color-accent-700);"
                      f"display:inline-flex;align-items:center;gap:5px;background:none;border:none;"
                      f"padding:0;cursor:pointer;text-align:left\" title=\"Change model\" "
                      f"onclick=\"editModel('{name}')\">{_chip}{h(model)}</button>"
                      if model else "")
        u = usage.get(name) or {}
        ut, ud = u.get("total") or {}, u.get("today") or {}
        usage_line = f"<span class='text-muted' style='font-size:12px' data-usage='{name}'></span>"
        if ut.get("calls"):
            usage_line = (
                f"<span class='text-muted' style='font-size:12px' data-usage='{name}' "
                f"title='Von diesem Agenten gemeldeter LLM-Verbrauch "
                f"({ut['calls']} Aufrufe gesamt)'>"
                f"Tokens heute {_fmt_tok(ud.get('in'))}&nbsp;/&nbsp;{_fmt_tok(ud.get('out'))}"
                f" · {_fmt_cost(ud.get('cost'))}"
                f" &nbsp;·&nbsp; gesamt {_fmt_tok(ut['in'])}&nbsp;/&nbsp;{_fmt_tok(ut['out'])}"
                f" · {_fmt_cost(ut['cost'])}</span>")
        st = (f"<span class='tag tag-accent'>● running</span>" if run
              else f"<span class='tag tag-neutral'>○ off</span>")
        net = inst.get("internet", True)
        tools_cfg = (inst.get("config") or {}).get("AGENT_TOOLS", "")
        ntag = (f"<button class='tag {'tag-accent' if net else 'tag-neutral'}' "
                f"style='border:none;cursor:pointer' title='Toggle internet access' "
                f"onclick=\"toggleNet('{name}',{str(not net).lower()})\">"
                f"{'🌐 internet on' if net else '🚫 offline'}</button>")
        ttag = (f"<span class='tag tag-neutral' title='{h(tools_cfg)}'>🔧 {len(tools_cfg.split(','))} Tools</span>"
                if tools_cfg else "")
        ptag = ""
        if inst.get("rootfs") in OVERLAY_ROOTFS:
            pers = bool(inst.get("persist_disk"))
            ptag = (f"<button class='tag {'tag-accent' if pers else 'tag-neutral'}' "
                    f"style='border:none;cursor:pointer' "
                    f"title='Persistente Disk: Installationen ueberleben Stop/Start"
                    f"{' — Rechtsklick: Disk zuruecksetzen' if pers else ''}' "
                    f"onclick=\"togglePersist('{name}',{str(not pers).lower()})\" "
                    f"oncontextmenu=\"return diskReset('{name}')\">"
                    f"{'💾 persistent' if pers else '↺ frisch je Start'}</button>")
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
            mtxt += (f"<div class='text-muted' style='font-size:12px'>{IC_FILES2} {h(m.get('host'))} → "
                     f"{h(m.get('guest'))}{' (ro)' if m.get('readonly') else ''}</div>")
        rows += (f"<tr><td data-label=Instance>"
                 f"<div style='display:flex;flex-direction:column;gap:2px'>"
                 f"<span style=\"font-family:var(--font-heading);font-weight:600;font-size:16px\">{name}</span>"
                 f"<span class='text-muted' style='font-size:12px'>{h(sub)}</span>"
                 f"{model_line}"
                 f"{usage_line}"
                 f"<span class='text-muted' style='font-size:12px'>{h(inst.get('description',''))}</span>"
                 f"{mtxt}</div></td>"
                 f"<td data-label=Status><div style='display:flex;flex-direction:column;gap:4px;align-items:flex-start'>{st}{ntag} {ptag} {ttag}</div></td>"
                 f"<td data-label='vCPU / RAM' style='font-variant-numeric:tabular-nums'>"
                 f"{inst.get('vcpus',2)} / {inst.get('mem_mib',1024)} MiB</td>"
                 f"<td data-label='Guest IP' class=mono>{n['guest']}</td>"
                 f"<td data-label=Actions><div class=acts>{btn}</div></td></tr>")
    tpls = "".join(f"<option value='{h(t['template'])}'>{h(t['template'])} — {h(t.get('description',''))}</option>"
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
                .replace("__HOSTIF__", HOSTIF).replace("__POOL__", POOL)
                .replace("__HOME__", os.path.expanduser(
                    "~" + (os.environ.get("SUDO_USER") or "")))
                )


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

        chat_id = body.get("chat")
        msg, img = body.get("message", ""), body.get("image")
        if gateway_on(chat_id):
            msg = gateway_clean(msg, chat_id, "in")
            if img:
                img, k = strip_image_meta(img)
                gateway_count(chat_id, "img", k)
            guard = StreamGuard(chat_id)
            raw_emit, emit = emit, lambda t: raw_emit(guard.feed(t))
        else:
            guard = None
        try:
            guest_stream(inst, msg, img, emit)
        except Exception as e:
            emit(f"\n⚠️ {e!r}")
        finally:
            if guard:
                raw_emit(guard.flush())

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
        # Das Connect-Timeout bleibt sonst als READ-Timeout auf dem Socket —
        # nach 10 s Leerlauf riss recv() den Tunnel ab ("connection closed").
        # Ein Terminal darf beliebig lange still sein: Timeouts runter, dafuer
        # TCP-Keepalive, damit halbtote Verbindungen trotzdem sterben.
        up.settimeout(None)
        down_sock = self.connection
        try:
            down_sock.settimeout(None)
            for sk in (up, down_sock):
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
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

        # Die App chattet nicht ueber /api/chat/<inst>, sondern hier durch —
        # das Gateway muss also an beiden Eingaengen sitzen, nicht nur am
        # bequemeren.
        guard = None
        if method == "POST" and tail.split("?", 1)[0] in ("api/chat", "api/chat/stream"):
            try:
                b = json.loads(data or b"{}")
            except (ValueError, TypeError):
                b = None
            if isinstance(b, dict):
                chat_id = b.pop("chat", None)      # kennt der Gast nicht, bleibt hier
                if gateway_on(chat_id):
                    b["message"] = gateway_clean(b.get("message", ""), chat_id, "in")
                    if b.get("image"):
                        b["image"], k = strip_image_meta(b["image"])
                        gateway_count(chat_id, "img", k)
                    guard = StreamGuard(chat_id)
                if chat_id is not None:
                    data = json.dumps(b).encode()

        req = urllib.request.Request(url, data=data, method=method)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            r = urllib.request.urlopen(req, timeout=620)
            # Bridges ohne Streaming (das claude-Template) antworten mit
            # {"reply": …} und Content-Type application/json — auch auf
            # /api/chat/stream. Die App liest den Body aber als rohen Text und
            # zeigt sonst das nackte JSON samt \uXXXX. Also hier auspacken und
            # als text/plain weiterreichen, wie es guest_stream fuer den
            # Web-Chat laengst tut. Der Content-Type steht VOR dem Senden fest.
            chat_path = tail.split("?", 1)[0] in ("api/chat", "api/chat/stream")
            is_json = "json" in (r.headers.get("Content-Type") or "").lower()
            if chat_path and is_json:
                body = r.read()
                try:
                    reply = json.loads(body).get("reply", body.decode("utf-8", "replace"))
                except (ValueError, AttributeError):
                    reply = body.decode("utf-8", "replace")
                if guard is not None:
                    reply = gateway_clean(reply, guard.chat_id, "out")
                out = reply.encode("utf-8")
                self.send_response(r.status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(r.status)
            self.send_header("Content-Type", r.headers.get("Content-Type", "text/html; charset=utf-8"))
            self.end_headers()
            # Chunk-weise durchreichen + flushen -> Token-Streaming vom Agenten.
            # Mit Gateway laeuft dazwischen ein Dekodierer: 4-KB-Schnitte fallen
            # sonst mitten in ein Mehrbyte-Zeichen.
            dec = codecs.getincrementaldecoder("utf-8")() if guard is not None else None
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                if guard is not None:
                    chunk = guard.feed(dec.decode(chunk)).encode("utf-8")
                    if not chunk:
                        continue
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except Exception:
                    break
            if guard is not None:
                # Erst den Dekodierer leeren, dann den Puffer — umgekehrt kaeme
                # das letzte Zeichen ungefiltert durch.
                rest = (guard.feed(dec.decode(b"", True)) + guard.flush()).encode("utf-8")
                if rest:
                    try:
                        self.wfile.write(rest)
                        self.wfile.flush()
                    except Exception:
                        pass
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
        if self.path == "/api/claude-credentials":
            # Abo-Anmeldung fuer das claude-Template: der Gast holt beim Boot
            # das LEBENDE Credential des Hosts (folgt also dem naechsten /login
            # des Nutzers). Nur der claudeAiOauth-Block — die mcpOAuth-Tokens
            # (Atlassian usw.) gehen die VM nichts an. Streng gegated: nur ein
            # echter Gast, dessen Instanz das claude-Template faehrt.
            inst = instance_by_ip(self.client_address[0])
            ok = inst is not None and (inst.get("template") == "claude")
            if not ok:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"claude template guests only"}')
                return
            try:
                with open(CLAUDE_CRED_SRC) as fh:
                    full = json.load(fh)
                out = json.dumps({"claudeAiOauth": full["claudeAiOauth"]}).encode()
                code = 200
            except (OSError, ValueError, KeyError):
                out = b'{"error":"no host credential (run claude /login on the host)"}'
                code = 503
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
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
                mkey = next((k for k in MODEL_KEYS if cfg.get(k)), "")
                # Backend aus dem gesetzten Model-Key ableiten (NICHT aus dem
                # Template — das bleibt z. B. "openrouter", auch wenn per
                # set_model auf orcarouter/llama gewechselt wurde).
                backend = {v: k for k, v in PROVIDER_MODEL_KEY.items()}.get(
                    mkey, i.get("template", ""))
                if cfg.get("LLAMA_ENDPOINT"):
                    backend = "llama"
                roster.append({
                    "name": i["name"], "template": i.get("template", ""),
                    "backend": backend,
                    "running": is_running(i),
                    "model": cfg.get(mkey, "") if mkey else "",
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
        # LAUFENDE Aufgaben (nicht die History) — fuer list_tasks/delete_task des
        # Agenten. Gast-offen; Tasks tragen keine Secrets.
        if self.path.startswith("/api/missions"):
            # Gast: nur die eigenen (Orchestrator). Admin: ?instance= oder alle.
            g = instance_by_ip(self.client_address[0])
            if g is not None and g.get("name") != ORCH_INSTANCE:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"orchestrator only"}'); return
            if g is not None:
                data = {"missions": mission_list(g["name"])}
            else:
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                inst = q.get("instance", [""])[0]
                data = {"missions": mission_list(inst)} if inst else                     {"by_instance": load_missions()}
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.startswith("/api/playbooks"):
            g = instance_by_ip(self.client_address[0])
            inst = g["name"] if g else urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "").get("instance", [""])[0]
            body = json.dumps({"playbooks": pb_list(inst)}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.startswith("/api/tasks-open"):
            _g = instance_by_ip(self.client_address[0])
            if _g is not None and _g.get("name") != ORCH_INSTANCE:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"orchestrator only"}'); return
            rows = [{"id": t.get("id"), "instance": t.get("instance"),
                     "schedule": t.get("schedule", ""), "status": t.get("status", ""),
                     "next_run": t.get("next_run", 0),
                     "message": str(t.get("message", ""))[:200]} for t in load_tasks()]
            body = json.dumps({"tasks": rows}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
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
        if self.path.startswith("/api/usage/"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            nm = re.sub(r"[^a-zA-Z0-9_-]", "", self.path.split("/api/usage/", 1)[1].split("?")[0])
            try:
                since = int(q.get("since", ["0"])[0] or 0)
            except ValueError:
                since = 0
            out = json.dumps(usage_for(nm, since)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/policy" or self.path.startswith("/api/audit/"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            if self.path == "/api/policy":
                data = {"instances": [effective_policy(i) for i in load_instances()]}
            else:
                nm = re.sub(r"[^a-zA-Z0-9_-]", "", self.path.split("/api/audit/", 1)[1].split("?")[0])
                data = {"instance": nm, "events": audit_read(nm, limit=1000)}
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
            # allowed=set(): seit dem MCP-Hub laufen die Serverprozesse am
            # Host — der Gast braucht nur noch die NAMEN. Secrets bleiben als
            # ${PLATZHALTER} stehen und verlassen den Manager nicht mehr.
            # (Der lokale Rueckfall im Gast startet damit ohne Zugangsdaten
            # und scheitert am Ziel — sichtbar im Log, nicht still.)
            blob = build_mcp_config(names, allowed=set()) if names else ""
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
        if self.path.startswith("/api/katfs/zip"):
            # "Alles herunterladen": den aktuellen Ordner einer Freigabe als ZIP.
            # Admin-only wie der Browser darunter.
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            root = q.get("path", ["."])[0]
            share = q.get("share", [""])[0]
            try:
                data, stats = katfs_zip(share, root)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode()
                self.send_response(502); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body); return
            leaf = os.path.basename(root.rstrip("/")) if root not in (".", "") else "katfs"
            fn = (leaf or "katfs") + ".zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
            self.send_header("X-Katfs-Files", str(stats.get("files", 0)))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        if self.path.startswith("/api/katfs/browse") or self.path.startswith("/api/katfs/file"):
            # Datei-Browser im Sharing-Tab. Admin-only (Gaeste per Source-IP
            # gesperrt); der Knoten adressiert die aktuell verbundene Freigabe.
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            path = q.get("path", ["."])[0]
            share = q.get("share", [""])[0]
            op = "ls" if "/browse" in self.path else "read"
            try:
                st, ct, data = katfs_proxy_fs(op, share, path)
            except urllib.error.HTTPError as e:
                st, ct, data = e.code, "application/json", e.read()
            except Exception as e:
                st, ct, data = 503, "application/json", json.dumps({"error": str(e)}).encode()
            if op == "read" and st == 200:
                # Bilder/Text sollen im neuen Tab anzeigbar sein, sonst Download.
                ct = mimetypes.guess_type(path)[0] or "application/octet-stream"
                disp = "attachment" if q.get("dl", [""])[0] == "1" else "inline"
                fn = os.path.basename(path) or "file"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Disposition", f'{disp}; filename="{fn}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            self.send_response(st); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data); return
        if self.path.split("?", 1)[0] == "/api/prompts":
            body = json.dumps({"prompts": load_prompts()}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.startswith("/api/voice-health"):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{VOICE_PORT}/health", timeout=5) as r:
                    out = r.read()
            except Exception as e:
                out = json.dumps({"ready": False, "error": str(e)}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path.startswith("/api/hitl/"):
            hid = self.path[len("/api/hitl/"):].split("?", 1)[0].strip()
            out = json.dumps({"status": hitl_status(hid)}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
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
        if _p in ("/api/settings", "/api/instances", "/api/chats", "/api/tasks", "/api/usage",
                  "/api/gateway", "/api/notifications"):
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
        elif _p == "/api/notifications":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            if "since" in q or "wait" in q:
                try:
                    since = int(q.get("since", ["0"])[0] or 0)
                    wait = min(30.0, max(0.0, float(q.get("wait", ["25"])[0] or 0)))
                except ValueError:
                    since, wait = 0, 0.0
                rev, notifs = wait_notifs(since, wait)
                lst = notifs if notifs is not None else []
                unread = sum(1 for n in load_notifications() if not n.get("read"))
                body = json.dumps({"rev": rev, "notifications": notifs, "unread": unread}).encode()
            else:
                lst = load_notifications()
                body = json.dumps({"notifications": lst,
                                   "unread": sum(1 for n in lst if not n.get("read"))}).encode()
            ct = "application/json"
        elif self.path == "/api/tasks":
            body = json.dumps(load_tasks()).encode()
            ct = "application/json"
        elif _p == "/api/usage":
            body = json.dumps(usage_summary()).encode()
            ct = "application/json"
        elif _p == "/api/gateway":
            g = load_gateway()
            g["available"] = _clean_unicode is not None
            body = json.dumps(g).encode()
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
        if ct.startswith("text/html"):
            # Nie cachen: eine veraltete Manager-Seite nach einem Update erzeugt
            # Geister-Fehler (alte JS-Logik gegen neue API).
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _llm_proxy(self, _pp):
        """POST /api/llm/<backend>/chat/completions — Credential-Injection-
        Gateway. Der Body geht unveraendert zum Router; der Manager injiziert
        den Authorization-Header aus den Settings, damit der Key die VM nie
        erreicht. Streams (SSE) werden zeilenweise durchgereicht, Upstream-
        Fehler transparent (Status + Body). Bewusst KEINE Logs von Key oder
        Body — genau die sollen den Host ja nicht verlassen bzw. nirgends
        liegenbleiben."""
        parts = _pp.strip("/").split("/")      # api/llm/<backend>/chat/completions
        backend = parts[2] if len(parts) > 2 else ""
        if backend not in LLM_PROXY_UPSTREAMS or parts[3:] != ["chat", "completions"]:
            out = b'{"error":"unknown llm proxy path"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
        url, keyname = LLM_PROXY_UPSTREAMS[backend]
        st = load_settings()
        # Selbstgehostetes OrcaRouter-Lite: die geteilte Basis-URL gilt auch
        # fuer den Proxy — sonst liefe der Umweg ploetzlich gegen die Cloud,
        # waehrend der Direktmodus den eigenen Server spricht.
        if backend == "orcarouter" and (st.get("ORCAROUTER_URL") or "").strip():
            u = st["ORCAROUTER_URL"].strip().rstrip("/")
            if not u.endswith("/chat/completions"):
                u += "/chat/completions" if u.endswith("/v1") else "/v1/chat/completions"
            url = u
        key = (st.get(keyname) or "").strip()
        if not key:
            out = json.dumps({"error": f"{keyname} not configured on host"}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
        ln = int(self.headers.get("Content-Length", 0) or 0)
        payload = self.rfile.read(ln) if ln else b""
        try:
            want_stream = bool(json.loads(payload or b"{}").get("stream"))
        except (ValueError, AttributeError):
            want_stream = False
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://agents.kat56.de",
            "X-Title": "kat56-agent"})
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            # Upstream-Fehler 1:1 durchreichen: der Agent hat eigene Retry-
            # Logik fuer 429/5xx und zeigt 4xx-Bodies als Fehlermeldung an.
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        except Exception as e:
            data = json.dumps({"error": f"llm upstream unreachable: {e!r}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        with r:
            self.send_response(r.status)
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            if want_stream:
                # SSE zeilenweise weiterschreiben und flushen — Voll-Puffern
                # wuerde das Token-Streaming im Agenten toeten. readline()
                # blockiert nur bis zur naechsten Event-Zeile, nie bis zum
                # Stream-Ende. Ohne Content-Length endet die Antwort mit dem
                # Verbindungsschluss (HTTP/1.0), urllib im Gast liest bis EOF.
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    while True:
                        chunk = r.readline()
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass               # Client weg -> Upstream schliesst via with
            else:
                data = r.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

    def do_POST(self):
        if not self._auth():
            return
        _pp = self.path.split("?", 1)[0]
        if instance_by_ip(self.client_address[0]) is not None and not (
                _pp in GUEST_POST_PATHS or _pp.startswith(GUEST_POST_PREFIXES)):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"forbidden"}')
            return
        if _pp.startswith("/api/llm/"):
            # LLM-Key-Injection: eigener Zweig ganz vorn, weil die Antwort
            # gestreamt sein kann und nicht ins JSON-Schema der uebrigen
            # Routen passt.
            return self._llm_proxy(_pp)
        # Sprache: der Dienst lauscht auf dem Loopback und ist von aussen nicht
        # erreichbar. Der Manager ist die einzige Tuer — er kennt den Anrufer
        # bereits (Basic-Auth bzw. Quell-IP) und reicht Roh-Audio bzw. WAV
        # unveraendert durch, statt es umzupacken.
        if _pp in ("/api/stt", "/api/tts"):
            ln = int(self.headers.get("Content-Length", 0) or 0)
            payload = self.rfile.read(ln) if ln else b""
            if _pp == "/api/tts":
                # Stimme/Tempo aus den geteilten Settings einmischen — App und
                # Web schicken nur {"text"}; explizite Client-Werte gewinnen.
                try:
                    b = json.loads(payload or b"{}")
                    st = load_settings()
                    if st.get("TTS_VOICE") and not b.get("voice"):
                        b["voice"] = st["TTS_VOICE"]
                    if st.get("TTS_SPEED") and not b.get("speed"):
                        b["speed"] = float(str(st["TTS_SPEED"]).replace(",", "."))
                    payload = json.dumps(b).encode()
                except (ValueError, TypeError):
                    pass
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{VOICE_PORT}{_pp[len('/api'):]}",
                    data=payload, method="POST",
                    headers={"Content-Type": self.headers.get(
                        "Content-Type", "application/octet-stream")})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = r.read()
                    ct = r.headers.get("Content-Type", "application/json")
                code = 200
            except urllib.error.HTTPError as e:
                data, ct, code = e.read(), "application/json", e.code
            except Exception as e:
                data = json.dumps({"error": f"voice service unreachable: {e!r}"}).encode()
                ct, code = "application/json", 503
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/usage":
            # Verbrauchsmeldung eines Agenten. Wie /api/audit nur fuer echte
            # Gaeste: die Instanz kommt aus der Quell-IP, nicht aus dem Body —
            # sonst koennte eine VM den Verbrauch einer anderen faelschen.
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is not None:
                usage_add(inst["name"], body.get("model", ""),
                          body.get("prompt_tokens"), body.get("completion_tokens"),
                          body.get("cost"))
            self.send_response(204); self.end_headers(); return
        if self.path == "/api/mcp":
            # MCP-Aufruf eines Gastes -> Hub. Nur echte Gaeste: die Instanz
            # kommt aus der Quell-IP; der Admin kann zum Testen "instance"
            # im Body mitgeben.
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            inst = instance_by_ip(self.client_address[0])
            if inst is None and b.get("instance"):
                inst = next((i for i in load_instances()
                             if i["name"] == b["instance"]), None)
            if inst is None:
                st, out = 403, {"error": "unknown caller"}
            else:
                st, out = mcp_hub_call(inst, str(b.get("server") or ""),
                                       b.get("payload") or {})
                m = (b.get("payload") or {}).get("method", "")
                if m == "tools/call":
                    try:
                        audit_append(inst["name"], "mcp:" + str(b.get("server")),
                                     ((b.get("payload") or {}).get("params") or {}).get("name", ""),
                                     st == 200 and "error" not in out)
                    except Exception:
                        pass
            body = json.dumps(out).encode()
            self.send_response(st)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if self.path in ("/api/task-edit", "/api/task-delete"):
            _g = instance_by_ip(self.client_address[0])
            if _g is not None and _g.get("name") != ORCH_INSTANCE:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"orchestrator only"}'); return
        if self.path == "/api/task-edit":
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            msg = update_task(str(b.get("id") or ""), b.get("message"), b.get("schedule"))
            out = json.dumps({"result": msg}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/task-delete":
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            tid = str(b.get("id") or "")
            before = load_tasks()
            after = [x for x in before if x.get("id") != tid]
            gone = len(before) - len(after)
            if gone:
                save_tasks(after)
            out = json.dumps({"deleted": gone, "id": tid}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path in ("/api/playbook-add", "/api/playbook-remove"):
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            g = instance_by_ip(self.client_address[0])
            inst = g["name"] if g else (b.get("instance") or "")
            if self.path.endswith("add"):
                r = pb_add(inst, b.get("text") or b.get("rule") or "")
                out = {"id": r, "added": bool(r and r != "exists"), "note": r}
            else:
                out = {"removed": pb_remove(inst, b.get("id") or "")}
            data = json.dumps(out).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data); return
        if self.path == "/api/memory-search":
            # Semantische Suche im Langzeitgedaechtnis. Wie /api/memory ist die
            # Instanz die des Gastes (Quell-IP); der Admin darf "instance" im
            # Body angeben (zum Testen).
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            guest = instance_by_ip(self.client_address[0])
            target = guest["name"] if guest else (b.get("instance") or "")
            hits = sem_search(target, b.get("query", ""), b.get("k", 5)) if target else []
            out = json.dumps({"hits": hits}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
        if self.path in ("/api/mission-start", "/api/mission-update",
                         "/api/mission-finish"):
            # Missions-Schreibzugriff: nur der Orchestrator (Gast) oder Admin.
            g = instance_by_ip(self.client_address[0])
            if g is not None and g.get("name") != ORCH_INSTANCE:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"orchestrator only"}'); return
            inst = g["name"] if g else ORCH_INSTANCE
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if self.path.endswith("start"):
                mid, note = mission_start(inst, b.get("goal", ""), b.get("steps") or [])
                out = {"id": mid, "note": note}
            elif self.path.endswith("update"):
                out = {"msg": mission_update(inst, b.get("id", ""),
                                             step=b.get("step"), status=b.get("status"),
                                             result=b.get("result", ""),
                                             task_id=b.get("task_id", ""),
                                             add_step=b.get("add_step", ""),
                                             note=b.get("note", ""))}
            else:
                out = {"msg": mission_finish(inst, b.get("id", ""),
                                             summary=b.get("summary", ""),
                                             failed=bool(b.get("failed")))}
            body = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.startswith("/api/mission-admin"):
            # UI: pause/resume/abort — Admin only (Gaeste geblockt).
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            out = {"msg": mission_admin(b.get("instance") or ORCH_INSTANCE,
                                        b.get("id", ""), b.get("action", ""))}
            body = json.dumps(out).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path == "/api/prompts":
            # Verwaltung der Prompt-Templates: Admin only.
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if b.get("delete"):
                msg = prompt_delete(b.get("name", ""))
            else:
                msg = prompt_upsert(b.get("name", ""), b.get("text", ""))
            out = json.dumps({"msg": msg}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/notify":
            # Agent schickt eine Push-Benachrichtigung an App + Web. Instanz per IP.
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            inst = instance_by_ip(self.client_address[0])
            _nm = inst["name"] if inst else "admin"
            nid, note = notify_add(_nm, body.get("title", ""),
                                   body.get("body") or body.get("message", ""),
                                   link=("chat:" + _nm) if inst else "")
            try:
                audit_append(inst["name"] if inst else "admin", "notify",
                             (body.get("title") or "")[:60], bool(nid))
            except Exception:
                pass
            out = json.dumps({"id": nid, "note": note}).encode()
            self.send_response(200 if nid else 429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/notifications/read":
            # App/Web quittieren gelesene Benachrichtigungen (Admin, kein Gast).
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            n = notif_mark_read(body.get("id"), bool(body.get("all")))
            out = json.dumps({"marked": n}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/hitl":
            # Agent bittet um Freigabe eines riskanten Tools. Instanz per IP.
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            inst = instance_by_ip(self.client_address[0])
            hid = hitl_create(inst["name"] if inst else "admin",
                              str(body.get("tool", ""))[:40], str(body.get("target", ""))[:200])
            out = json.dumps({"id": hid}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/signal":
            # Signal-Versand fuer Agenten. Der Empfaenger wird gegen
            # ALLOWED_SENDERS geprueft, die Bot-Nummer kommt aus den
            # Einstellungen — die VM kennt beides nicht.
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            ok, note = signal_send(body.get("text") or body.get("message"), body.get("to"))
            inst = instance_by_ip(self.client_address[0])
            try:
                audit_append(inst["name"] if inst else "admin", "send_signal",
                             (body.get("to") or "default"), ok)
            except Exception:
                pass
            out = json.dumps({"ok": ok, "note": note}).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
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
            elif parts == ["api", "gateway"]:
                # {"chat": "<id>", "on": true} — Gaeste haben hier nichts zu
                # suchen, sonst haengt eine VM ihren eigenen Filter ab.
                if instance_by_ip(self.client_address[0]) is not None:
                    msg = "forbidden (admin only)"
                else:
                    ln = int(self.headers.get("Content-Length", 0) or 0)
                    b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
                    cid = str(b.get("chat") or "")
                    if not cid:
                        msg = "chat missing"
                    else:
                        d = load_gateway()
                        if b.get("on"):
                            d["chats"][cid] = True
                        else:
                            d["chats"].pop(cid, None)
                        save_gateway(d)
                        msg = f"gateway {'on' if b.get('on') else 'off'} for {cid}"
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
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "update":
                ln = int(self.headers.get("Content-Length", 0) or 0)
                b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
                msg = update_task(parts[2], b.get("message"), b.get("schedule"))
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
                key, value = b.get("key", ""), b.get("value", "")
                msg = mem_store(target, key, value)
                # Dasselbe zusaetzlich semantisch ablegen. Faellt der Embedder
                # aus, bleibt das flache Gedaechtnis oben trotzdem geschrieben.
                sem = sem_store(target, value, key)
                msg += " (+semantic)" if sem else " (semantic off)"
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
                elif action == "config":
                    # Einzelnen Config-Schluessel setzen/loeschen (Admin; Secrets
                    # bleiben draussen — die gehen nur ueber den Broker).
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    key = str(body.get("key", "")).strip()
                    val = body.get("value", "")
                    inst2 = next((i for i in load_instances() if i["name"] == name), None)
                    if not inst2:
                        msg = "unknown"
                    elif not re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", key) or key in NEVER_PERSIST:
                        msg = f"error: key '{key}' nicht erlaubt"
                    else:
                        cfg2 = inst2.setdefault("config", {})
                        if val in ("", None):
                            cfg2.pop(key, None)
                        else:
                            cfg2[key] = str(val)
                        with open(os.path.join(INST_DIR, f"{name}.json"), "w") as fh:
                            json.dump(inst2, fh, indent=2)
                        msg = f"{key} " + ("removed" if val in ("", None) else f"= {val}") +                               (" (applies after stop/start)" if is_running(inst2) else "")
                elif action == "persist":
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    msg = set_persist_disk(name, bool(body.get("on")))
                elif action == "diskreset":
                    msg = reset_upper(name)
                elif action == "model":
                    # Nicht in GUEST_POST_PATHS — Gast-VMs kommen hier nie an
                    # (Positivliste am Anfang von do_POST blockt sie mit 403).
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    msg = set_model(name, body.get("model", ""))
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
    threading.Thread(target=_signal_receiver, daemon=True).start()
    ThreadingHTTPServer(LISTEN, H).serve_forever()
