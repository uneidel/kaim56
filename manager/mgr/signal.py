"""Signal: Versand (signal-cli REST), HITL-Freigaben, Empfang (json-rpc
WebSocket), stdlib-WS-Client. Teil des mgr-Pakets. Querbezuege (load_settings,
chat_log_append, orchestrator_ping) werden per configure() injiziert.
"""
import base64
import json
import os
import re
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid

SIGNAL_LOG = None
load_settings = lambda: {}
chat_log_append = lambda *a, **k: None
orchestrator_ping = lambda: None


def configure(base, settings_fn=None, chat_log_fn=None, ping_fn=None):
    global SIGNAL_LOG, load_settings, chat_log_append, orchestrator_ping
    SIGNAL_LOG = os.path.join(base, "signal-debug.log")
    if settings_fn:
        load_settings = settings_fn
    if chat_log_fn:
        chat_log_append = chat_log_fn
    if ping_fn:
        orchestrator_ping = ping_fn


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


