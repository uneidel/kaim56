#!/usr/bin/env python3
"""Pi Coding Agent bridge (signal | web) for Firecracker microVMs.

Runs the Pi coding agent (pi.dev) headless via `pi --print`, wired to either a
Signal direct-chat transport or a small web chat UI. Mirrors the claude bridge.

Trust boundary (signal): only ALLOWED_SENDERS in 1:1 chat may drive the agent.
Provider keys (OPENROUTER/ANTHROPIC/OPENAI_API_KEY) come from the environment,
which guest-init exports from the config-disk. Model/provider are passed on the
CLI so pi runs fully non-interactively.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- config -----------------------------------------------------------------
TRANSPORT     = os.environ.get("TRANSPORT", "signal").strip().lower()
WORKDIR       = os.environ.get("CLAUDE_WORKDIR", "/home/node/workspace")
PI_MODEL      = os.environ.get("PI_MODEL", "").strip()
PI_PROVIDER   = os.environ.get("PI_PROVIDER", "").strip()
PI_EXTRA_ARGS = os.environ.get("PI_EXTRA_ARGS", "").strip()
PI_TIMEOUT    = int(os.environ.get("PI_TIMEOUT", "900"))
PI_CONTINUE   = os.environ.get("PI_CONTINUE", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_REPLY     = int(os.environ.get("MAX_REPLY_CHARS", "3500"))

# signal transport
SIGNAL_API    = os.environ.get("SIGNAL_API", "https://signalapi.kat56.de").rstrip("/")
SIGNAL_VERIFY = os.environ.get("SIGNAL_VERIFY", "true").strip().lower() not in ("0", "false", "no", "off")
SIGNAL_NUMBER = os.environ.get("SIGNAL_NUMBER", "")
ALLOWED_SENDERS = [s.strip() for s in os.environ.get("ALLOWED_SENDERS", "").split(",") if s.strip()]
POLL_TIMEOUT  = int(os.environ.get("POLL_TIMEOUT", "10"))

_started = False   # for --continue after the first successful run
_ssl_ctx = None
if not SIGNAL_VERIFY:
    import ssl
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


# --- Pi invocation ----------------------------------------------------------
def run_pi(prompt):
    """Run headless Pi (`pi --print`), keep the session, return reply text."""
    global _started
    cmd = ["pi"]
    if PI_PROVIDER:
        cmd += ["--provider", PI_PROVIDER]
    if PI_MODEL:
        cmd += ["--model", PI_MODEL]
    if PI_CONTINUE and _started:
        cmd += ["-c"]
    if PI_EXTRA_ARGS:
        cmd += PI_EXTRA_ARGS.split()
    cmd += ["--print", prompt]
    try:
        p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=PI_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"⏱️ Zeitlimit ({PI_TIMEOUT}s) erreicht — Anfrage abgebrochen."
    except FileNotFoundError:
        return "⚠️ 'pi' nicht gefunden (npm-Binary fehlt im Rootfs)."
    out = (p.stdout or "").strip()
    if not out:
        return f"⚠️ Keine Ausgabe (exit {p.returncode}).\n{(p.stderr or '')[:800]}"
    _started = True
    return out[:MAX_REPLY] if TRANSPORT == "signal" else out


def handle(text):
    """Command router shared by both transports."""
    global _started
    low = text.strip().lower()
    if low in ("/help", "help", "/start"):
        return ("🤖 katbot ↔ Pi (pi.dev)\n"
                "Schreib einfach deine Anfrage. Befehle:\n"
                "  /reset – neue Session\n  /status – Info\n"
                f"model={PI_MODEL or 'default'} provider={PI_PROVIDER or 'auto'} workdir={WORKDIR}")
    if low == "/reset":
        _started = False
        return "🔄 Neue Session gestartet."
    if low == "/status":
        return (f"✅ läuft. session={'ja' if _started else 'neu'} "
                f"model={PI_MODEL or 'default'} provider={PI_PROVIDER or 'auto'} workdir={WORKDIR}")
    return run_pi(text.strip())


# --- signal transport -------------------------------------------------------
def _open(req):
    return urllib.request.urlopen(req, timeout=max(POLL_TIMEOUT + 15, 30), context=_ssl_ctx)


def signal_send(text, recipient):
    for i in range(0, len(text) or 1, MAX_REPLY):
        body = json.dumps({"message": text[i:i + MAX_REPLY] or " ",
                           "number": SIGNAL_NUMBER, "recipients": [recipient]}).encode()
        req = urllib.request.Request(f"{SIGNAL_API}/v2/send", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            _open(req).read()
        except urllib.error.HTTPError as e:
            log("send error", e.code, e.read()[:200])
        except Exception as e:
            log("send failed", repr(e))


def receive():
    url = f"{SIGNAL_API}/v1/receive/{SIGNAL_NUMBER}?timeout={POLL_TIMEOUT}"
    try:
        return json.loads(_open(urllib.request.Request(url)).read().decode())
    except urllib.error.HTTPError as e:
        log("receive HTTP", e.code)
    except Exception as e:
        log("receive error", repr(e))
    return []


def extract(env):
    e = env.get("envelope", env)
    dm = e.get("dataMessage") or {}
    text = dm.get("message")
    if not text:
        return None
    source = e.get("sourceNumber") or e.get("source")
    gi = dm.get("groupInfo") or {}
    return source, gi.get("groupId"), text


def authorized(source, group_id):
    if source == SIGNAL_NUMBER:          # ignore our own messages (no loops)
        return False
    if source not in ALLOWED_SENDERS:
        return False
    return group_id is None              # direct 1:1 only



def _log_signal_turn(sender, user_text, reply_text):
    """Signal-Turn in die gemeinsame Chat-Historie (App+Web) spiegeln."""
    try:
        base = "http://%s:8700" % _mgr_gateway()
        data = json.dumps({"sender": sender, "user": user_text, "reply": reply_text}).encode()
        req = urllib.request.Request(base + "/api/chat-log", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def signal_loop():
    if not SIGNAL_NUMBER or not ALLOWED_SENDERS:
        log("FATAL: SIGNAL_NUMBER/ALLOWED_SENDERS fehlen")
        sys.exit(1)
    log(f"pi-signal-bridge start: api={SIGNAL_API} workdir={WORKDIR} "
        f"model={PI_MODEL or 'default'} senders={ALLOWED_SENDERS}")
    receive()  # drain backlog once
    signal_send("🤖 katbot (Pi) online — schreib mir. (/help)", ALLOWED_SENDERS[0])
    while True:
        for env in receive() or []:
            got = extract(env)
            if not got:
                continue
            source, group_id, text = got
            if not authorized(source, group_id):
                log("ignored from", source, "group", (group_id or "-")[:10])
                continue
            log("MSG from", source, ":", text[:80])
            try:
                signal_send("💭 …", source)
                reply = handle(text)
            except Exception as e:
                reply = f"⚠️ Bridge-Fehler: {e!r}"
            signal_send(reply, source)
            _log_signal_turn(source, text, reply)
            log("replied", len(reply), "chars")
        time.sleep(1)


# --- web transport ----------------------------------------------------------
PAGE = """<!doctype html><html lang=de><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Pi Agent</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:12px 16px;background:#171a21;font-weight:600}
 #log{padding:12px 16px;max-width:820px;margin:0 auto}
 .m{margin:10px 0;white-space:pre-wrap;word-wrap:break-word}
 .u{color:#7fb0ff}.a{color:#c8e6c9}
 form{position:sticky;bottom:0;display:flex;gap:8px;padding:12px 16px;background:#171a21;max-width:820px;margin:0 auto}
 input{flex:1;padding:10px;border-radius:8px;border:1px solid #333;background:#0f1115;color:#e6e6e6}
 button{padding:10px 16px;border:0;border-radius:8px;background:#3a6df0;color:#fff}
</style>
<header>🤖 Pi Coding Agent</header>
<div id=log></div>
<form id=f><input id=i placeholder="Nachricht… (/help)" autocomplete=off><button>Senden</button></form>
<script>
const log=document.getElementById('log'),i=document.getElementById('i'),f=document.getElementById('f');
function add(t,c){const d=document.createElement('div');d.className='m '+c;d.textContent=t;log.appendChild(d);window.scrollTo(0,document.body.scrollHeight)}
f.onsubmit=async e=>{e.preventDefault();const m=i.value.trim();if(!m)return;i.value='';add('› '+m,'u');const b=document.createElement('div');b.className='m a';b.textContent='…';log.appendChild(b);
 try{const r=await fetch('api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})});b.textContent=(await r.json()).reply||'(leer)'}catch(e){b.textContent='⚠️ '+e}
 window.scrollTo(0,document.body.scrollHeight)}
</script></html>"""


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def do_POST(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(ln) or b"{}")
        except (ValueError, json.JSONDecodeError):
            d = {}
        reply = handle(d["message"]) if d.get("message") else ""
        b = json.dumps({"reply": reply}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


def web_loop():
    log(f"pi-web-bridge auf :8080  model={PI_MODEL or 'default'} provider={PI_PROVIDER or 'auto'} workdir={WORKDIR}")
    ThreadingHTTPServer(("0.0.0.0", 8080), Web).serve_forever()



# --- Provider-Keys ueber den Manager-Broker --------------------------------
# Aus Sicherheitsgruenden liegen OPENROUTER/ANTHROPIC/OPENAI_API_KEY NICHT mehr
# in der Instanz-Config (und damit nicht auf der Config-Disk der microVM). Was
# im Env fehlt, wird beim Start einmal vom Secret-Broker des Managers geholt:
# der erkennt den Gast an der Source-IP und prueft die Allowlist in
# secret-policy.json (Default deny). Ohne Freigabe bleibt der Key leer.
def _mgr_gateway():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip.rsplit(".", 1)[0] + ".1"


def ensure_provider_keys():
    base = "http://%s:8700" % _mgr_gateway()
    for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            continue
        try:
            body = urllib.request.urlopen(base + "/api/secret/" + name, timeout=15).read().decode()
            val = json.loads(body).get("value", "")
            if val:
                os.environ[name] = val
                log("Provider-Key %s vom Broker bezogen" % name)
        except Exception:
            pass


if __name__ == "__main__":
    ensure_provider_keys()
    (web_loop if TRANSPORT == "web" else signal_loop)()
