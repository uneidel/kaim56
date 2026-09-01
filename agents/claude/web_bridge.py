#!/usr/bin/env python3
"""Web transport bridge: small chat UI + /api/chat, agent-aware (claude|fabric).

Runs in the microVM on 0.0.0.0:WEB_PORT. Reached ONLY through the manager proxy
(agents.example.com/i/<name>/), hence no auth of its own. Stdlib only.
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT = os.environ.get("AGENT", "claude")
WORKDIR = os.environ.get("CLAUDE_WORKDIR", "/root/workspace")
PORT = int(os.environ.get("WEB_PORT", "8080"))
TIMEOUT = int(os.environ.get("WEB_TIMEOUT", "600"))
ALLOW_ACTIONS = os.environ.get("ALLOW_ACTIONS", "true").strip().lower() not in (
    "0", "false", "no", "off")
FABRIC_DEFAULT_PATTERN = os.environ.get("FABRIC_DEFAULT_PATTERN", "ai")
_session = None


def run_claude(msg):
    global _session
    # /reset clears the Claude Code session (new context) — the same the
    # OpenRouter agent can do. The app now passes /reset through.
    if msg.strip() == "/reset":
        _session = None
        return "🔄 Neue Unterhaltung."
    cmd = ["claude", "-p", msg, "--output-format", "json"]
    if _session:
        cmd += ["--resume", _session]
    if ALLOW_ACTIONS:
        cmd += ["--dangerously-skip-permissions"]
    p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=TIMEOUT)
    try:
        d = json.loads(p.stdout)
        if d.get("session_id"):
            _session = d["session_id"]
        return d.get("result") or "(empty reply)"
    except json.JSONDecodeError:
        return (p.stdout or p.stderr or "(no output)")[:4000]


def run_fabric(msg):
    pattern, body = FABRIC_DEFAULT_PATTERN, msg
    if msg.startswith("/p "):
        parts = msg[3:].split(None, 1)
        pattern = parts[0]
        body = parts[1] if len(parts) > 1 else ""
    p = subprocess.run(["fabric", "-p", pattern], input=body, capture_output=True,
                       text=True, timeout=TIMEOUT)
    return (p.stdout or p.stderr or "(empty reply)").strip()


def run(msg):
    try:
        return run_fabric(msg) if AGENT == "fabric" else run_claude(msg)
    except subprocess.TimeoutExpired:
        return f"⏱️ Time limit ({TIMEOUT}s) reached."
    except Exception as e:
        return f"⚠️ Error: {e!r}"


PAGE = """<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>__AGENT__ chat</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;height:100dvh;display:flex;flex-direction:column;background:#fafafa;color:#222}
header{padding:.7rem 1rem;font-weight:600;border-bottom:1px solid #ddd;background:#fff}
#log{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.6rem}
.msg{max-width:85%;padding:.55rem .8rem;border-radius:12px;white-space:pre-wrap;line-height:1.4;word-wrap:break-word}
.me{align-self:flex-end;background:#0a7cff;color:#fff}
.bot{align-self:flex-start;background:#eee}
form{display:flex;gap:.5rem;padding:.7rem;border-top:1px solid #ddd;background:#fff}
textarea{flex:1;padding:.6rem;font-size:1rem;border:1px solid #ccc;border-radius:10px;resize:none;min-height:44px;max-height:140px}
button{padding:.6rem 1rem;font-size:1rem;border:none;border-radius:10px;background:#0a7cff;color:#fff;cursor:pointer}
@media(prefers-color-scheme:dark){body{background:#111;color:#e2e2e2}header,form{background:#1a1a1a;border-color:#333}
.bot{background:#242424}textarea{background:#1b1b1b;color:#e2e2e2;border-color:#444}}
</style></head><body>
<header>🤖 __AGENT__</header>
<div id=log></div>
<form id=f><textarea id=t placeholder="Message… (Enter sends)" autofocus></textarea><button>➤</button></form>
<script>
const log=document.getElementById('log'),t=document.getElementById('t');
function add(txt,cls){const d=document.createElement('div');d.className='msg '+cls;d.textContent=txt;log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
async function send(){const m=t.value.trim();if(!m)return;t.value='';add(m,'me');const b=add('…','bot');
  try{const r=await fetch('api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})});
    const j=await r.json();b.textContent=j.reply||'(empty)';}catch(e){b.textContent='⚠️ '+e}log.scrollTop=log.scrollHeight}
document.getElementById('f').onsubmit=e=>{e.preventDefault();send()};
t.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE.replace("__AGENT__", AGENT).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(ln) or b"{}")
        except json.JSONDecodeError:
            d = {}
        reply = run(d.get("message", "")) if d.get("message") else ""
        b = json.dumps({"reply": reply}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


if __name__ == "__main__":
    print(f"web_bridge agent={AGENT} workdir={WORKDIR} on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
