#!/usr/bin/env python3
"""Signal <-> Claude Code bridge.

Listens for Signal messages in the "katterbach" group (from an allow-listed
sender), hands each message to `claude -p` (headless Claude Code) running in a
mounted workspace, and sends the reply back into the group. A text-based
alternative to remote-control.

Security: this lets an allow-listed Signal sender run Claude Code with tool
access on the host. Guard rails:
  * only messages from ALLOWED_SENDERS are processed,
  * only from the configured group (ALLOWED_GROUP_ID),
  * messages from our own number are ignored (no loops).
Treat ALLOWED_SENDERS + the private group as the trust boundary.

Config via environment variables (see docker-compose / README).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --- config -----------------------------------------------------------------
SIGNAL_API = os.environ.get("SIGNAL_API", "https://signal-api.example.com").rstrip("/")
SIGNAL_VERIFY = os.environ.get("SIGNAL_VERIFY", "true").strip().lower() not in (
    "0", "false", "no", "off",
)
SIGNAL_NUMBER = os.environ.get("SIGNAL_NUMBER", "")            # bot account (katbot)
ALLOWED_SENDERS = [s.strip() for s in os.environ.get("ALLOWED_SENDERS", "").split(",") if s.strip()]
# Direct-chat mode (default): reply 1:1 to the sender, ignore group messages.
# Set ALLOWED_GROUP_ID (+ SIGNAL_GROUP_SEND) to operate inside a group instead.
ALLOWED_GROUP_ID = os.environ.get("ALLOWED_GROUP_ID", "")      # internal_id (base64); empty = direct chat
SIGNAL_GROUP_SEND = os.environ.get("SIGNAL_GROUP_SEND", "")    # group.<...> to reply to in group mode

CLAUDE_WORKDIR = os.environ.get("CLAUDE_WORKDIR", "/workspace")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "900"))  # seconds per request
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "")             # optional --model
ALLOW_ACTIONS = os.environ.get("ALLOW_ACTIONS", "true").strip().lower() not in (
    "0", "false", "no", "off",
)  # pass --dangerously-skip-permissions so Claude can actually act

POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "10"))       # receive long-poll seconds
MAX_REPLY_CHARS = int(os.environ.get("MAX_REPLY_CHARS", "3500"))  # per Signal message

# session id for conversation continuity (per running container)
_session_id = None
_ssl_ctx = None
if not SIGNAL_VERIFY:
    import ssl
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def _open(req):
    return urllib.request.urlopen(req, timeout=max(POLL_TIMEOUT + 15, 30), context=_ssl_ctx)


def signal_send(text, recipient):
    """Send a message (chunked) to a recipient (a number, or a group.<...> id)."""
    for i in range(0, len(text) or 1, MAX_REPLY_CHARS):
        chunk = text[i:i + MAX_REPLY_CHARS] or " "
        body = json.dumps({
            "message": chunk,
            "number": SIGNAL_NUMBER,
            "recipients": [recipient],
        }).encode()
        req = urllib.request.Request(f"{SIGNAL_API}/v2/send", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            _open(req).read()
        except urllib.error.HTTPError as e:
            log("send error", e.code, e.read()[:200])
        except Exception as e:
            log("send failed", repr(e))


def receive():
    """One-shot receive; returns list of envelopes."""
    url = f"{SIGNAL_API}/v1/receive/{SIGNAL_NUMBER}?timeout={POLL_TIMEOUT}"
    try:
        return json.loads(_open(urllib.request.Request(url)).read().decode())
    except urllib.error.HTTPError as e:
        log("receive HTTP", e.code)
    except Exception as e:
        log("receive error", repr(e))
    return []


def extract(env):
    """Return (source, group_id, text) for a real text message, else None."""
    e = env.get("envelope", env)
    dm = e.get("dataMessage") or {}
    text = dm.get("message")
    if not text:
        return None
    source = e.get("sourceNumber") or e.get("source")
    gi = dm.get("groupInfo") or {}
    return source, gi.get("groupId"), text


def authorized(source, group_id):
    if source == SIGNAL_NUMBER:            # ignore our own messages
        return False
    if source not in ALLOWED_SENDERS:
        return False
    if ALLOWED_GROUP_ID:                    # group mode: only that group
        return group_id == ALLOWED_GROUP_ID
    return group_id is None                 # direct mode: only 1:1 (ignore groups)


def reply_target(source, group_id):
    """Where to send the answer: the group in group mode, else back to the sender."""
    if ALLOWED_GROUP_ID and group_id == ALLOWED_GROUP_ID:
        return SIGNAL_GROUP_SEND
    return source


def run_claude(prompt):
    """Run headless Claude Code, keep session, return reply text."""
    global _session_id
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if _session_id:
        cmd += ["--resume", _session_id]
    if ALLOW_ACTIONS:
        cmd += ["--dangerously-skip-permissions"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    try:
        p = subprocess.run(cmd, cwd=CLAUDE_WORKDIR, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"⏱️ Time limit ({CLAUDE_TIMEOUT}s) reached — request aborted."
    out = (p.stdout or "").strip()
    if not out:
        return f"⚠️ No output (exit {p.returncode}).\n{(p.stderr or '')[:800]}"
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            if data.get("session_id"):
                _session_id = data["session_id"]
            res = data.get("result")
            if data.get("is_error"):
                return f"⚠️ Claude error: {res or data.get('subtype')}"
            return res or "(empty reply)"
    except json.JSONDecodeError:
        pass
    return out[:MAX_REPLY_CHARS]


def handle(text):
    global _session_id
    cmd = text.strip()
    low = cmd.lower()
    if low in ("/help", "help", "/start"):
        return ("🤖 katbot ↔ Claude Code\n"
                "Just write your request. Commands:\n"
                "  /reset – new conversation\n"
                "  /status – info\n"
                f"Workdir: {CLAUDE_WORKDIR}, actions: {'ON' if ALLOW_ACTIONS else 'off'}")
    if low == "/reset":
        _session_id = None
        return "🔄 New conversation started."
    if low == "/status":
        return (f"✅ running. session={'yes' if _session_id else 'new'} "
                f"workdir={CLAUDE_WORKDIR} actions={'on' if ALLOW_ACTIONS else 'off'}")
    return run_claude(cmd)


def main():
    missing = [k for k, v in (("SIGNAL_NUMBER", SIGNAL_NUMBER),
                              ("ALLOWED_SENDERS", ALLOWED_SENDERS)) if not v]
    if ALLOWED_GROUP_ID and not SIGNAL_GROUP_SEND:
        missing.append("SIGNAL_GROUP_SEND (group mode)")
    if missing:
        log("FATAL: missing config:", missing)
        sys.exit(1)
    mode = "group" if ALLOWED_GROUP_ID else "direct(1:1)"
    log(f"claude-signal-bridge start [{mode}]: api={SIGNAL_API} workdir={CLAUDE_WORKDIR} "
        f"actions={ALLOW_ACTIONS} senders={ALLOWED_SENDERS}")
    # drain/ignore backlog once so we don't replay old messages on startup
    receive()
    signal_send("🤖 katbot online — message me. (/help)", ALLOWED_SENDERS[0])
    while True:
        for env in receive() or []:
            got = extract(env)
            if not got:
                continue
            source, group_id, text = got
            if not authorized(source, group_id):
                log("ignored from", source, "group", (group_id or "-")[:10])
                continue
            target = reply_target(source, group_id)
            log("MSG from", source, ":", text[:80])
            try:
                signal_send("💭 …", target)
                reply = handle(text)
            except Exception as e:
                reply = f"⚠️ Bridge error: {e!r}"
            signal_send(reply, target)
            log("replied", len(reply), "chars")
        time.sleep(1)


if __name__ == "__main__":
    main()
