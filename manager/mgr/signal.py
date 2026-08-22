# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Signal: sending (signal-cli REST), HITL approvals, receiving (json-rpc
WebSocket), stdlib WS client. Part of the mgr package. Cross-references
(load_settings, chat_log_append, orchestrator_ping) are injected via configure().
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

from mgr.gateway import redact_secrets

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
# Agents can write to the user on their own (finished task, finding, follow-up
# question). Sending goes through the manager, not from the VM:
#
#   * The recipient must be in ALLOWED_SENDERS — i.e. exactly the list that may
#     also command the bot. So an agent can ONLY write to people who are allowed
#     to give it commands anyway. Without that leash the tool would be a mass
#     sender for arbitrary numbers, and a compromised or merely ill-tempered
#     agent could send messages in someone else's name.
#   * The bot number and the API access stay on the host. The VM never sees them.
#   * A throttle limits the damage of a loop.
SIGNAL_DEFAULT_API = "https://signal-api.example.com"
SIGNAL_MAX_CHARS = 3500          # signal-cli takes more, readability does not
SIGNAL_RATE = (10, 300)          # at most 10 messages per 5 minutes
_signal_sent = []                # timestamps of the last sends
_signal_lock = threading.Lock()


def signal_recipients():
    """Allowed recipients from the settings (comma-separated)."""
    raw = (load_settings().get("ALLOWED_SENDERS") or "")
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def signal_send(text, to=None):
    """(ok, message). Sends a message via the signal-cli REST API."""
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
        # Deliberately with the list: the agent should be able to fix the
        # error without a human looking. Nothing about it is secret — these are
        # the numbers that command the bot anyway.
        return False, f"recipient {to} not permitted; allowed: {', '.join(allowed)}"

    text = (text or "").strip()
    text, _leaks = redact_secrets(text)   # secrets must not leave the system
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


# ---- HITL: approval of risky tool calls via Signal -------------------------
# An agent (opt-in via HITL=1) asks here before a risky tool; we ask the user
# via Signal ("ok <id>" / "no <id>") and the agent polls the status. If the
# Signal send fails (no recipient configured) we return NO id -> the agent then
# does not block. In-memory, short-lived.
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
    msg = (f"🔒 Approval needed: agent '{instance}' wants to {tool}"
           + (f" ({target})" if target else "")
           + f".\nReply 'ok {hid}' to allow or 'no {hid}' to deny.")
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


# ---- Signal receiving (long-poll) ------------------------------------------
# signal-cli-rest ("native" mode) has no webhooks — it does not POST to us. So
# WE fetch: a long-poll on /v1/receive returns the moment a message arrives.
# Each message is delivered exactly once (the call drains the queue). An allowed
# message lands in the shared chat store and fires the same orchestrator_ping
# that app/web use — so the orchestrator reacts within seconds instead of at the
# next heartbeat. It sends replies back via send_signal.
def _signal_inbound(sender, text):
    chat_log_append("orchestrator", sender, f"[Signal] {text}", "", kind="signal")
    try:
        orchestrator_ping()
    except Exception:
        pass


# native mode: receiving and sending lock the same account. After an incoming
# message the receiver pauses briefly — a contention-free window in which the
# orchestrator can send its reply out promptly.
# (The gateway's json-rpc mode would solve this cleanly.)
SIGNAL_REPLY_WINDOW = int(os.environ.get("SIGNAL_REPLY_WINDOW", "60"))


def _slog(m):
    """Diagnostics into a READABLE file — the systemd journal is not
    accessible to the user. Just delete it if not needed."""
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
        _slog(f"ignored: sender {src or uuid_} not in {sorted(allowed)}")
        return
    # HITL approval? An allowed sender replies "ok <id>" / "no <id>" (German "ja"/"nein" also accepted).
    mo = re.match(r"^(ok|ja|yes|approve|nein|no|deny|ablehnen)\s+([0-9a-f]{8})$",
                  text.lower().strip())
    if mo:
        approve = mo.group(1) in ("ok", "ja", "yes", "approve")
        done = hitl_resolve(mo.group(2), approve)
        _slog(f"HITL {mo.group(2)} -> {'approved' if approve else 'denied'} (found={done})")
        return
    _slog(f"-> inject von {src or uuid_}: {text[:60]!r}")
    _signal_inbound(src or uuid_, text)


# --- minimal WebSocket client (stdlib) for the json-rpc receive -------------
def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _ws_frame(sock):
    """Read a server frame -> (opcode, payload). The server does not mask."""
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
            raise RuntimeError("handshake aborted")
        resp += chunk
        if len(resp) > 8192:
            break
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise RuntimeError("no 101: " + resp[:120].decode("latin1", "replace"))
    return sock


def _signal_receiver():
    """json-rpc mode: the gateway pushes messages over a WebSocket (real time).
    Receiving and sending now run in parallel — no account lock, no send pause
    needed anymore."""
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
        sock.settimeout(300)          # longer silence -> reconnect, keeps it fresh
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


