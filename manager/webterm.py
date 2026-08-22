#!/usr/bin/env python3
"""webterm — browser terminal for a Firecracker agent microVM.

Dependency-free (stdlib only). Serves an xterm.js page and a WebSocket at /ws
that is bridged to a login shell running in a PTY. Started by guest-init on
TERM_PORT (default 7682); the host manager proxies /i/<name>/term/ to it.

Runs as whatever uid guest-init launches it under (the agent user, uid 1000),
so the shell sees the same workspace as the agent. No auth here — the manager
is the trust boundary (LAN/VPN only), same posture as the agent web UI.
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import struct
import termios
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TERM_PORT = int(os.environ.get("TERM_PORT", "7682"))
SHELL     = os.environ.get("TERM_SHELL", "/bin/bash")
WORKDIR   = os.environ.get("CLAUDE_WORKDIR", os.environ.get("HOME", "/root"))
HOME      = os.environ.get("HOME", "/home/node")
WS_MAGIC  = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Terminal</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css">
<style>
 /* The console stays dark — there is no component for it in the system and
    a light terminal background would be worse. Everything around it follows Industry:
    dark band, slate blue as the accent, square, Barlow. */
 html,body{height:100%;margin:0;background:#141618}
 body{display:flex;flex-direction:column;height:100vh}
 #bar{font:13px "Barlow",system-ui,sans-serif;color:rgba(232,233,234,.55);
      padding:6px 10px;background:#1c1f22;flex:none}
 #bar b{color:#94bce3}
 #keys{display:flex;gap:6px;padding:6px 8px;background:#1c1f22;overflow-x:auto;
       white-space:nowrap;flex:none;-webkit-overflow-scrolling:touch}
 #keys button{font:13px "Barlow Condensed",system-ui,sans-serif;font-weight:600;
              color:#e8e9ea;background:transparent;border:1px solid rgba(232,233,234,.16);
              border-radius:0;padding:8px 12px;flex:none;min-width:40px;touch-action:manipulation}
 #keys button:active{background:#94bce3;color:#141618}
 #keys button.on{background:#94bce3;border-color:#94bce3;color:#141618}
 #term{flex:1;min-height:0;padding:4px 6px}
</style>
<div id=bar>🖥️ <b id=st>connecting…</b></div>
<div id=keys></div>
<div id=term></div>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<script>
const st=document.getElementById('st');
const term=new Terminal({cursorBlink:true,fontSize:14,theme:{background:'#0f1115'}});
const fit=new FitAddon.FitAddon();term.loadAddon(fit);
term.open(document.getElementById('term'));fit.fit();
const proto=location.protocol==='https:'?'wss':'ws';
const ws=new WebSocket(proto+'://'+location.host+location.pathname.replace(/\\/?$/,'/')+'ws');
ws.binaryType='arraybuffer';
function sendResize(){try{ws.send(JSON.stringify({type:'resize',cols:term.cols,rows:term.rows}))}catch(e){}}
function snd(s){if(ws.readyState===1)ws.send(s)}
ws.onopen=()=>{st.textContent='connected';st.style.color='#c8e6c9';sendResize();term.focus()};
ws.onclose=()=>{st.textContent='disconnected';st.style.color='#ff8a80';term.write('\\r\\n\\x1b[31m[connection closed]\\x1b[0m\\r\\n')};
ws.onerror=()=>{st.textContent='error';st.style.color='#ff8a80'};
ws.onmessage=e=>{term.write(typeof e.data==='string'?e.data:new Uint8Array(e.data))};
let ctrl=false,ctrlBtn=null;
function setCtrl(v){ctrl=v;if(ctrlBtn)ctrlBtn.classList.toggle('on',ctrl)}
term.onData(d=>{
 if(ctrl&&d.length===1){const c=d.toLowerCase().charCodeAt(0);
   if(c>=97&&c<=122){snd(String.fromCharCode(c-96));setCtrl(false);return}}
 snd(d);
});
const KEYS=[['Esc','\\x1b'],['Tab','\\t'],['Ctrl','CTRL'],
 ['←','\\x1b[D'],['↓','\\x1b[B'],['↑','\\x1b[A'],['→','\\x1b[C'],
 ['⌃C','\\x03'],['⌃D','\\x04'],['⌃Z','\\x1a'],['⌃L','\\x0c'],
 ['/','/'],['|','|'],['~','~'],['-','-']];
const kb=document.getElementById('keys');
KEYS.forEach(([label,seq])=>{
 const b=document.createElement('button');b.textContent=label;
 b.onclick=()=>{if(seq==='CTRL'){setCtrl(!ctrl);term.focus();return}snd(seq);setCtrl(false);term.focus()};
 if(seq==='CTRL')ctrlBtn=b;
 kb.appendChild(b);
});
addEventListener('resize',()=>{fit.fit();sendResize()});
if(window.visualViewport)visualViewport.addEventListener('resize',()=>{fit.fit();sendResize()});
</script></html>"""


# --- minimal WebSocket framing (RFC 6455) -----------------------------------
def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()


def ws_frame(payload, opcode=0x1):
    """Server->client frame (unmasked)."""
    if isinstance(payload, str):
        payload = payload.encode()
    n = len(payload)
    hdr = bytes([0x80 | opcode])
    if n < 126:
        hdr += bytes([n])
    elif n < 65536:
        hdr += bytes([126]) + struct.pack(">H", n)
    else:
        hdr += bytes([127]) + struct.pack(">Q", n)
    return hdr + payload


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def ws_read(sock):
    """Read one client frame -> (opcode, payload) or None on close/EOF."""
    h = recv_exact(sock, 2)
    if not h:
        return None
    opcode = h[0] & 0x0F
    masked = h[1] & 0x80
    ln = h[1] & 0x7F
    if ln == 126:
        ext = recv_exact(sock, 2)
        if not ext:
            return None
        ln = struct.unpack(">H", ext)[0]
    elif ln == 127:
        ext = recv_exact(sock, 8)
        if not ext:
            return None
        ln = struct.unpack(">Q", ext)[0]
    mask = recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
    if mask is None:
        return None
    data = recv_exact(sock, ln) if ln else b""
    if data is None:
        return None
    if masked:
        data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
    return opcode, data


def set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


class Term(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.rstrip("/").endswith("ws"):
            return self._serve_ws()
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in (self.headers.get("Upgrade", "").lower()):
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept(key))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        self._pty_bridge(self.connection)

    def _pty_bridge(self, sock):
        pid, fd = pty.fork()
        if pid == 0:  # child -> shell
            os.environ["TERM"] = "xterm-256color"
            os.environ["HOME"] = HOME
            try:
                os.chdir(WORKDIR)
            except OSError:
                pass
            os.execvp(SHELL, [SHELL, "-l"])
            os._exit(127)
        # parent: pump pty <-> websocket
        alive = True
        lock = threading.Lock()

        def send(payload, opcode=0x1):
            with lock:
                try:
                    sock.sendall(ws_frame(payload, opcode))
                except OSError:
                    pass

        def pty_to_ws():
            nonlocal alive
            while alive:
                try:
                    r, _, _ = select.select([fd], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if fd in r:
                    try:
                        data = os.read(fd, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    send(data, 0x2)  # binary
            alive = False
            try:
                sock.sendall(ws_frame(b"", 0x8))  # close
            except OSError:
                pass

        t = threading.Thread(target=pty_to_ws, daemon=True)
        t.start()
        try:
            while alive:
                frame = ws_read(sock)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping -> pong
                    send(data, 0xA)
                    continue
                if opcode in (0x1, 0x2):
                    # control message? (resize)
                    if data[:1] == b"{":
                        try:
                            msg = json.loads(data.decode())
                            if msg.get("type") == "resize":
                                set_winsize(fd, int(msg["rows"]), int(msg["cols"]))
                                continue
                        except (ValueError, KeyError):
                            pass
                    try:
                        os.write(fd, data)
                    except OSError:
                        break
        finally:
            alive = False
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (OSError, ChildProcessError):
                pass


def main():
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    srv = ThreadingHTTPServer(("0.0.0.0", TERM_PORT), Term)
    print(f"webterm on :{TERM_PORT} shell={SHELL} cwd={WORKDIR}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
