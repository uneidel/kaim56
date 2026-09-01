#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""kAIm56 — Manager (web UI + API) for 1..x microVM instances (Firecracker).

Runs as root (needs /dev/kvm, ip, iptables) — e.g. via systemd. Pure standard
library, no extra packages. Instances are stored as JSON under
instances/<name>.json; the network is derived per instance from 'index':
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
import hashlib
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

import chatui   # chat interface (/chat), lives next to this file

WEB_GUEST_PORT = 8080   # port of the web bridge in the microVM
TERM_GUEST_PORT = 7682  # port of the webterm (browser terminal) in the microVM

BASE = os.path.dirname(os.path.abspath(__file__))

# Load the mgr package early: injections (notify/sem) happen further down,
# as soon as the respective functions are defined.
from mgr import missions as _missions  # noqa: E402
_missions.configure(BASE)
from mgr import mcp as _mcp  # noqa: E402
_mcp.configure(BASE)
from mgr import signal as _signal_mod  # noqa: E402
_signal_mod.configure(BASE)
BIN = os.path.join(BASE, "bin", "firecracker")
KERNEL = os.path.join(BASE, "bin", "vmlinux")
INST_DIR = os.path.join(BASE, "instances")
TEMPLATE_DIR = os.path.join(BASE, "templates")
RUN_DIR = os.path.join(BASE, "run")
SETTINGS_FILE = os.path.join(BASE, "settings.json")
# Shared secrets/defaults, maintained in the config UI, which pre-fill empty
# template parameters of the same name.
SETTINGS_SCHEMA = [
    {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API key"},
    {"key": "BRAVE_API_KEY", "label": "Brave Search API key (web search for the agents; free tier at brave.com/search/api)"},
    {"key": "ANTHROPIC_API_KEY", "label": "Anthropic API key"},
    {"key": "OPENAI_API_KEY", "label": "OpenAI API key"},
    {"key": "ORCAROUTER_API_KEY", "label": "OrcaRouter API key (sk-orca-…)"},
    {"key": "ORCAROUTER_URL", "label": "OrcaRouter base URL (blank = https://api.orcarouter.ai/v1; set only when self-hosting OrcaRouter-Lite)"},
    {"key": "SIGNAL_NUMBER", "label": "Signal bot number"},
    {"key": "ALLOWED_SENDERS", "label": "Allowed Signal number(s)"},
    {"key": "SIGNAL_API", "label": "Signal REST API URL"},
    {"key": "LLAMA_ENDPOINT", "label": "llama.cpp endpoint (OpenAI-compatible base URL, e.g. http://10.0.0.50:8080/v1)"},
    {"key": "LLAMA_API_KEY", "label": "llama.cpp API key (optional, only if --api-key is set)"},
    {"key": "LLM_KEY_PROXY", "label": "LLM key injection proxy (1 = keys stay on the host, VMs proxy through the manager)", "options": [
        {"value": "", "label": "— off (agent fetches key via broker) —"},
        {"value": "1", "label": "on — keys never leave the host"}]},
    {"key": "TTS_VOICE", "label": "TTS voice (Piper)", "options": [
        {"value": "", "label": "— default (de-thorsten-medium) —"},
        {"value": "de-thorsten-medium", "label": "German · Thorsten (medium)"},
        {"value": "de-eva_k-x_low", "label": "German · Eva K (x_low, faster)"},
        {"value": "en-amy-medium", "label": "English · Amy (medium)"}]},
    {"key": "TTS_SPEED", "label": "TTS speed (0.5 slow … 2.0 fast, empty = 1.0)"},
]
# These values NEVER end up in instances/<name>.json and never on the microVM's
# config disk. The agent fetches them at runtime via the secret broker
# (/api/secret/<name>, guest identified by source IP, allowlist per policy).
SECRET_PARAMS = {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLAMA_API_KEY", "ORCAROUTER_API_KEY"}
# Write routes that an agent VM IS ALLOWED to use. Everything else is
# administration and belongs to the admin. Without this allowlist a
# compromised VM could reach the host filesystem via /api/instances/<n>/mounts
# (the manager runs as root and exports the folder into the guest via NFS)
# or create a fresh instance for itself via /api/create — the secret allowlist,
# the tool gating and the egress rules would then be moot.
# An allowlist instead of individual checks: a new route is then closed by
# default, not open by default.
VOICE_PORT = int(os.environ.get("VOICE_PORT", "8770"))   # voice service, loopback
# Subscription login of the claude template: the user's credential on the host.
# The manager runs as root and may read the 0600 file; the guest fetches it at
# boot via /api/claude-credentials (claude template only, by source IP).
CLAUDE_CRED_SRC = os.environ.get("CLAUDE_CRED_SRC", "/home/ulrich/.claude/.credentials.json")
GUEST_POST_PATHS = ("/api/usage", "/api/audit", "/api/task", "/api/chat-log",
                    "/api/stt", "/api/tts", "/api/signal", "/api/mcp",
                    "/api/memory-search", "/api/task-delete", "/api/task-edit",
                    "/api/playbook-add", "/api/playbook-remove", "/api/hitl",
                    "/api/notify", "/api/mission-start", "/api/mission-update",
                    "/api/mission-finish")
GUEST_POST_PREFIXES = ("/api/memory/", "/api/llm/")
# Credential injection gateway (OneCLI pattern): the agent sends its chat
# requests to /api/llm/<backend>/chat/completions instead of directly to the
# router; when forwarding, the manager appends the Authorization header from
# the settings. This way the LLM keys NEVER leave the host: a compromised VM
# can at most call models through the manager (visible, throttleable), but
# cannot exfiltrate a key and reuse it outside the system.
LLM_PROXY_UPSTREAMS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "orcarouter": ("https://api.orcarouter.ai/v1/chat/completions", "ORCAROUTER_API_KEY"),
}
# Configured secrets never leave the manager in plain text — the UI gets this
# marker and sends it back unchanged on save, where it is discarded. A genuinely
# empty value still deletes the entry.
SETTINGS_KEEP = "__unchanged__"
# MCP_CONFIG carried the substituted secrets in plain text (e.g. the HA bearer
# token). Instead MCP_SERVERS is stored — only the catalog names; the agent
# fetches the values at runtime via /api/mcp-config.
NEVER_PERSIST = SECRET_PARAMS | {"MCP_CONFIG"}

# ---- Site config (site.json): non-secret, host-specific values ----
# Domains/IPs/interface of this installation in ONE place, kept out via
# .gitignore. If the file is missing, public defaults apply (example.com /
# 1.1.1.1 / eth0) — this keeps the repo free of internal infrastructure.
SITE_FILE = os.path.join(BASE, "site.json")
def load_site():
    try:
        with open(SITE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}
SITE = load_site()
PUBLIC_HOST = SITE.get("PUBLIC_HOST") or "example.com"
SIGNAL_HOST = SITE.get("SIGNAL_HOST") or "signal-api.example.com"
# Editor on the host (code-server/openvscode). Only a LINK in the footer, no
# embedding: the manager runs behind HTTPS, the editor usually on plain HTTP in
# the LAN — an iframe would be blocked as mixed content. Empty = no link.
CODE_URL = SITE.get("CODE_URL") or ""

POOL = "172.30.0.0/16"
def _uplink_iface():
    """Interface of the default route ("… dev eth0 …")."""
    try:
        out = subprocess.run(["ip", "-o", "route", "show", "default"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        return out[out.index("dev") + 1]
    except Exception:
        return ""


def _pick_hostif():
    """Uplink for the guests' MASQUERADE rule. A hard-wired NIC name is a silent
    trap: if the kernel renames it (update, new hardware, reboot), the NAT rule
    points nowhere — the microVMs then reach neither DNS nor the LLM, and nothing
    logs an error. That's why a configured name only counts if the interface
    really exists; otherwise the default route wins."""
    want = os.environ.get("HOSTIF") or SITE.get("HOSTIF") or ""
    if want and os.path.exists(f"/sys/class/net/{want}"):
        return want
    auto = _uplink_iface()
    if want and auto:
        print(f"[net] HOSTIF={want} does not exist — using {auto} (default route)",
              flush=True)
    return auto or want or "eth0"


HOSTIF = _pick_hostif()
LISTEN = ("0.0.0.0", int(os.environ.get("PORT", "8700")))
USER = os.environ.get("MANAGER_USER", "admin")
PW = os.environ.get("MANAGER_PASS", "")   # empty => no auth (only behind Traefik!)

# ---- NFS / host folders ----------------------------------------------------
# The workspace folder is the NFSv4 root (fsid=0). Additional host folders are
# bind-mounted UNDER this root (.fcmnt/<instance>/<idx>), exported with
# 'crossmnt' per guest IP and mounted explicitly inside the guest.
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


# "Relevant" = curated flagship models (exact IDs). Only those currently
# present in the OpenRouter catalog are shown. Extend as needed.
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


_mcp.configure(BASE, load_instances)   # injection (mgr/mcp)

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
    """Only toggle the status — text and assessment come from the file; the UI
    must not be able to rewrite findings."""
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
    """The curated selection for the create form. Kept as a file so a new model
    comes in via the Models tab instead of via an edit to CURATED + restart.
    If the file is missing, CURATED is the initial seed."""
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
    """OpenRouter models, price ascending. Cached for 10 min; force bypasses the
    cache. tools_only -> only function/tool calling; relevant_only -> only curated."""
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
                    continue  # hide auto-router / dynamic pricing
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


# ---- Signal (send/HITL/receive): moved out to mgr/signal.py ---------------
from mgr.signal import (signal_send, signal_recipients, hitl_create, hitl_status,  # noqa: E402,F401
                        hitl_resolve, _signal_receiver, _signal_inbound,
                        SIGNAL_MAX_CHARS, SIGNAL_RATE)


# ---- Security gateway: moved out to mgr/gateway.py -------------------------
from mgr import gateway as _gateway  # noqa: E402
_gateway.configure(BASE)
from mgr.gateway import (load_gateway, gateway_on, gateway_clean, gateway_count,  # noqa: E402,F401
                         StreamGuard, strip_image_meta, _clean_unicode)


# ---- Chat history (sync with the app) --------------------------------------
CHATS_FILE = os.path.join(BASE, "chats.json")
TOMBSTONES_FILE = os.path.join(BASE, "chats_tombstones.json")
TOMB_TTL_MS = 60 * 24 * 3600 * 1000   # discard deletion markers after 60 days


def load_tombstones():
    try:
        with open(TOMBSTONES_FILE) as fh:
            d = json.load(fh)
        return {str(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, TypeError):
        return {}


def save_tombstones(t):
    now = int(time.time() * 1000)
    t = {k: v for k, v in t.items() if now - v < TOMB_TTL_MS}   # TTL prune
    try:
        with open(TOMBSTONES_FILE, "w") as fh:
            json.dump(t, fh)
    except OSError:
        pass
    return t


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
        bump_chats_rev()          # immediately wake waiting long-polls (app/web)
        return len(data)
    except OSError:
        return -1


# Live sync: every write to the chat store bumps a revision. App and web hang
# on the long-poll with ?since=<rev>&wait=<sec> and see the other side's message
# within fractions of a second — no reload, no constant polling. Without the
# parameters, /api/chats responds as before (a list), so older clients keep
# working unchanged.
_chats_cv = threading.Condition()
try:
    _chats_rev = int(os.path.getmtime(CHATS_FILE) * 1000)
except OSError:
    _chats_rev = 0


def bump_chats_rev():
    global _chats_rev
    with _chats_cv:
        # Time-based, but strictly monotonic: survives a manager restart without
        # leaving a client with an old `since` stuck.
        _chats_rev = max(_chats_rev + 1, int(time.time() * 1000))
        _chats_cv.notify_all()


def wait_chats(since, timeout):
    """(rev, chats|None) — the list only if something changed since `since`,
    otherwise None (timeout). Blocks at most `timeout` seconds."""
    deadline = time.time() + max(0.0, timeout)
    with _chats_cv:
        while _chats_rev <= since:
            rest = deadline - time.time()
            if rest <= 0:
                break
            _chats_cv.wait(min(1.0, rest))
        rev = _chats_rev
    return rev, (load_chats() if rev > since else None)


# ---- Notifications: moved out to mgr/notify.py -----------------------------
from mgr import notify as _notify  # noqa: E402
_notify.configure(BASE)
from mgr.notify import (load_notifications, notify_add, notif_mark_read, notif_clear,  # noqa: E402,F401
                        wait_notifs, NOTIF_MAX, NOTIF_RATE, _notif_sent)
_missions.notify_add = notify_add   # injection (mgr/missions)


# ---- Inbox (watermark) — coupled to chat, stays here -----------------------
INBOX_WM_FILE = os.path.join(BASE, "inbox_wm.json")


def _inbox_wm():
    try:
        with open(INBOX_WM_FILE) as fh:
            return int(json.load(fh).get("ts", 0))
    except (OSError, ValueError):
        return 0


def inbox_since(peek=False):
    """New user messages from the shared chat store (Signal/app/web) since the
    last run — as an inbox for the orchestrator. Watermark over
    conversation.updatedAt: every conversation with new activity is delivered
    once (last user message). Task conversations (results) are hidden.
    peek=True delivers without setting the watermark."""
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


def chat_log_append(inst_name, sender, user_text, reply_text, kind="signal"):
    """Append a turn (question + answer) to the shared chat history so it shows
    up in the app and web. `kind`='signal' -> one conversation per
    (instance, sender); 'task' -> one task conversation per instance."""
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


# ---- Manage tool plugins (drag & drop in the web manager) ------------------
# Each tool = a folder plugins/<name>/ with an entry file tool.py (convention
# DESC/PARAMS/REQUIRED/run). Single .py files are stored as plugins/<name>/tool.py.
# The folder is copied onto the config disk when the instance starts and loaded
# inside the VM (sandbox). stdlib-only.
PLUGINS_SRC = os.path.join(BASE, "plugins")
PLUGIN_MAX_BYTES = 5 * 1024 * 1024
PLUGIN_BOILERPLATE = (
    "# Tool plugin for kAIm56. Convention: DESC / PARAMS / REQUIRED / run().\n"
    "# Runs in the agent VM (sandbox), stdlib-only. Multiple files? Put more\n"
    "# .py files in this folder and import them here (e.g. `import helper`).\n"
    "DESC = \"Short: what the tool does (shown to the model as the tool description).\"\n"
    "PARAMS = {\n"
    "    \"text\": {\"type\": \"string\", \"description\": \"example parameter\"},\n"
    "}\n"
    "REQUIRED = []\n"
    "\n"
    "def run(text=\"\"):\n"
    "    # ... your logic; return a string ...\n"
    "    return f\"ok: {text}\"\n"
)


def _safe_tool_name(name):
    return re.sub(r"[^a-z0-9_-]", "", (name or "").strip().lower())[:40]


# ---- Plugin integrity: content-hash pinning (idea from MS "APM") -----------
# On upload/creation the SHA-256 over all of the tool's files is recorded as
# "approved". If a plugin file is later changed directly (bypassing the UI),
# the hash diverges -> the UI shows "modified" and you must deliberately re-pin
# the change via "Approve". Runtime state, gitignored.
PLUGIN_PINS_FILE = os.path.join(PLUGINS_SRC, ".pins.json")


def _plugin_hash(name):
    """SHA-256 over (relpath\0content\0) of all a tool's files, sorted."""
    name = _safe_tool_name(name)
    folder = os.path.join(PLUGINS_SRC, name)
    single = os.path.join(PLUGINS_SRC, name + ".py")
    if os.path.isdir(folder):
        files, base = [], folder
        for root, _d, fs in os.walk(folder):
            for f in fs:
                files.append(os.path.join(root, f))
        files.sort()
    elif os.path.isfile(single):
        files, base = [single], PLUGINS_SRC
    else:
        return None
    h = hashlib.sha256()
    for fp in files:
        h.update(os.path.relpath(fp, base).encode()); h.update(b"\0")
        try:
            with open(fp, "rb") as fh:
                h.update(fh.read())
        except OSError:
            return None
        h.update(b"\0")
    return h.hexdigest()


def load_plugin_pins():
    try:
        with open(PLUGIN_PINS_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_plugin_pins(d):
    try:
        with open(PLUGIN_PINS_FILE, "w") as fh:
            json.dump(d, fh, indent=2)
    except OSError:
        pass


def plugin_pin(name):
    """Record the current state as approved (upload or 'Approve')."""
    name = _safe_tool_name(name)
    h = _plugin_hash(name)
    pins = load_plugin_pins()
    if h:
        pins[name] = h
    else:
        pins.pop(name, None)
    _save_plugin_pins(pins)
    return h


def list_plugins():
    out = []
    if not os.path.isdir(PLUGINS_SRC):
        return out
    for entry in sorted(os.listdir(PLUGINS_SRC)):
        if entry.startswith((".", "__")):        # __pycache__, hidden
            continue
        path = os.path.join(PLUGINS_SRC, entry)
        if os.path.isdir(path):
            files = []
            for root, _dirs, fs in os.walk(path):
                for f in fs:
                    rel = os.path.relpath(os.path.join(root, f), path)
                    files.append(rel)
            out.append({"name": entry, "kind": "folder", "files": sorted(files)})
        elif entry.endswith(".py"):
            out.append({"name": entry[:-3], "kind": "file", "files": [entry]})
    pins = load_plugin_pins()
    for e in out:
        cur = _plugin_hash(e["name"]); pin = pins.get(e["name"])
        e["sha"] = (cur or "")[:12]
        e["pinned"] = bool(pin)
        e["modified"] = bool(pin) and cur is not None and cur != pin
    return out


def plugin_write_py(name, code):
    name = _safe_tool_name(name)
    if not name:
        return "invalid name"
    dest = os.path.join(PLUGINS_SRC, name)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "tool.py"), "w", encoding="utf-8") as fh:
        fh.write(code or PLUGIN_BOILERPLATE)
    plugin_pin(name)
    return None


def plugin_write_zip(name, raw):
    import zipfile
    import io
    name = _safe_tool_name(name)
    if not name:
        return "invalid name"
    dest = os.path.join(PLUGINS_SRC, name)
    dest_abs = os.path.abspath(dest)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return "broken zip"
    names = [n for n in z.namelist() if not n.endswith("/")]
    tops = {n.split("/", 1)[0] for n in names}
    strip = len(tops) == 1 and any("/" in n for n in names)
    top = next(iter(tops)) if strip else None
    for m in z.infolist():
        if m.is_dir():
            continue
        rel = m.filename[len(top) + 1:] if strip else m.filename
        if not rel or rel.startswith("__MACOSX"):
            continue
        target = os.path.normpath(os.path.join(dest, rel))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
            continue   # zip-slip protection
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(m) as fsrc, open(target, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
    if not any(os.path.isfile(os.path.join(dest, c))
               for c in ("tool.py", "__init__.py", name + ".py")):
        return "no entry (tool.py/__init__.py) found in the zip"
    plugin_pin(name)
    return None


def plugin_delete(name):
    name = _safe_tool_name(name)
    pins = load_plugin_pins()
    if pins.pop(name, None) is not None:
        _save_plugin_pins(pins)
    dfolder = os.path.join(PLUGINS_SRC, name)
    dfile = os.path.join(PLUGINS_SRC, name + ".py")
    if os.path.isdir(dfolder):
        shutil.rmtree(dfolder, ignore_errors=True)
        return True
    if os.path.isfile(dfile):
        os.remove(dfile)
        return True
    return False


def merge_chats(incoming):
    """MERGE chats (newer updatedAt wins) plus DELETION TOMBSTONES:
    `incoming` is either a bare list (old: chats only) or an object
    {chats:[...], tombstones:{id:deletedAt}}. A tombstoned chat does not come
    back — not even through a re-push from the app — as long as its updatedAt is
    not NEWER than the deletion (a genuine edit after the deletion resurrects it
    and discards the tombstone). Tombstones have a TTL."""
    if isinstance(incoming, dict):
        chats_in = incoming.get("chats") or []
        tombs_in = incoming.get("tombstones") or {}
    else:
        chats_in = incoming if isinstance(incoming, list) else []
        tombs_in = {}

    tombs = load_tombstones()
    if isinstance(tombs_in, dict):
        for k, v in tombs_in.items():
            try:
                tombs[str(k)] = max(tombs.get(str(k), 0), int(v))
            except (TypeError, ValueError):
                continue

    by_id = {}
    for c in load_chats():
        if isinstance(c, dict) and c.get("id") and c.get("messages"):
            by_id[str(c["id"])] = c
    for c in chats_in if isinstance(chats_in, list) else []:
        if not isinstance(c, dict) or not c.get("id") or not c.get("messages"):
            continue
        cid = str(c["id"])
        cur = by_id.get(cid)
        if cur is None or c.get("updatedAt", 0) >= cur.get("updatedAt", 0):
            by_id[cid] = c

    # Apply tombstones
    for cid, dat in list(tombs.items()):
        c = by_id.get(cid)
        if c is not None and c.get("updatedAt", 0) > dat:
            tombs.pop(cid, None)        # chat is newer -> resurrection ok
        else:
            by_id.pop(cid, None)        # deleted stays deleted

    save_tombstones(tombs)
    merged = sorted(by_id.values(), key=lambda x: x.get("updatedAt", 0), reverse=True)
    return save_chats(merged)





# ---- Background jobs (task queue + scheduler) ------------------------------

# ---- store: SQLite history/usage/semantics + memory -> mgr/store.py -------
from mgr import store as _store  # noqa: E402
_store.configure(BASE)
from mgr.store import (HISTORY_DB, MEMORY_FILE, TASKS_FILE, EMBED_URL, _hist_lock, _hist_conn,  # noqa: E402,F401
                       usage_add, usage_summary, usage_for, history_add, history_search,
                       load_tasks, save_tasks, add_task, update_task, _next_run,
                       _embed, sem_store, sem_search, load_memory, mem_store, mem_recall)
_missions.sem_store = sem_store   # injection (mgr/missions)


def _chat_post(inst, message, timeout=600):
    """Non-streaming chat call to an instance's bridge."""
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
    """Run a task on an EXISTING instance (its tools/MCP/secrets live there).
    Starts it if needed and waits until the bridge is up."""
    inst = next((i for i in load_instances() if i["name"] == instance), None)
    if not inst:
        return (False, f"instance '{instance}' unknown")
    if not is_running(inst):
        if not wait_web(inst, timeout=120):
            return (False, f"instance '{instance}' not ready")
        inst = next((i for i in load_instances() if i["name"] == instance), None)
    try:
        return (True, _chat_post(inst, message))
    except Exception as e:
        return (False, f"error: {e!r}")


def _run_ephemeral(message, model=None):
    """Run a task in a FRESH, isolated VM that is deleted afterwards. For
    isolated/independent work — not for tasks that need a specific MCP/token
    (those belong on their instance)."""
    name = "task-" + uuid.uuid4().hex[:6]
    cfg = {"TRANSPORT": "web", "NO_SPAWN": "1"}
    if model:
        cfg["OPENROUTER_MODEL"] = model
    msg = create_instance(name, "openrouter", cfg)
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return (False, f"ephemeral VM failed: {msg}")
    try:
        if not wait_web(inst, timeout=120):
            return (False, "ephemeral VM not ready")
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
    """Run a task — on a named instance (routing to the capability) or in an
    ephemeral VM (target == 'ephemeral')."""
    if instance == "ephemeral":
        return _run_ephemeral(message)
    return _run_named(instance, message)


# ---- Instant trigger for the orchestrator ----------------------------------
# New user message (Signal/app/web) -> the orchestrator runs debounced within
# seconds instead of only at the next 2-h heartbeat. Coalesces bursts, one run
# at a time; if new messages arrived during the run, it fires again right away.
# Fires only if the inbox really has something new (peek).
ORCH_INSTANCE = "orchestrator"
ORCH_HEARTBEAT_MSG = (
    "/fresh "   # stateless: own throwaway context, no bloat, no wiping out a
                # running app chat (shared _history).
    "Heartbeat (instant trigger): 1) read_inbox — new user messages. "
    "2) For each one that needs action: recall_tasks (no duplicates), then "
    "list_agents and create_task to the CAPABLE instance (e.g. hass for "
    "HomeAssistant) or ephemeral. 3) Messages starting with [Signal] came in "
    "via Signal: send the reply or confirmation back with send_signal "
    "(briefly) — WITHOUT specifying a number/recipient, it goes to the user "
    "automatically; do NOT invent a number. 4) Check missions: is a step stuck "
    "on doing even though its task finished long ago (recall_tasks)? Then "
    "mission_update and kick off the next step. Keep it short. Nothing to do? "
    "Report: nothing to do.")
MISSION_ADVANCE_MSG = (
    "/fresh Mission progress (instant trigger after task completion): the "
    "following tasks are done:\n{done}\n"
    "For EACH of them: 1) recall_tasks for the result. 2) mission_update: set "
    "the step to done/failed, record the result briefly. 3) Kick off the NEXT "
    "open step (create_task to the capable instance or ephemeral, note the "
    "task-id on the step via mission_update). 4) No open step left? "
    "mission_finish with a short summary. Blocked? notify the user. Keep it short.")


# Collect mode for the advance push (idea from OpenClaw's queue modes): every
# push is a full /fresh turn and costs its fixed ~5k input tokens before any
# work happens. When several tasks finish close together — exactly what the
# cross-instance missions produce — one push handling all of them does the same
# work for one fixed cost. Completions are therefore collected per OWNER for a
# short window and flushed as a single message.
MISSION_COLLECT_SECS = float(os.environ.get("MISSION_COLLECT_SECS", "8"))
_madv_lock = threading.Lock()
_madv_pending = {}        # owner -> [ "task 'id' (mission 'mid', goal, step N)" ]
_madv_timer = {}          # owner -> threading.Timer


def _mission_advance_flush(inst):
    with _madv_lock:
        _madv_timer.pop(inst, None)
        lines = _madv_pending.pop(inst, [])
    if not lines:
        return
    if not any(i.get("name") == inst for i in load_instances()):
        return          # owner deleted -> nothing to push to (TTL sweep pauses it)
    msg = MISSION_ADVANCE_MSG.format(done="\n".join("- " + x for x in lines))
    try:
        _run_named(inst, msg)
    except Exception as e:
        print("mission-advance:", repr(e), flush=True)


def _mission_advance_fire(task_id):
    """After task completion: if the task belongs to a mission step, note it for
    the mission's OWNER and (re)arm that owner's collect window. The owner is
    whichever agent planned the mission — the step itself may have run on a
    completely different instance."""
    inst, m, st = mission_for_task(task_id)
    if not m or not inst:
        return
    line = f"task '{task_id}' (mission '{m['id']}', {m['goal'][:80]}, step {st['n']})"
    with _madv_lock:
        _madv_pending.setdefault(inst, []).append(line)
        t = _madv_timer.get(inst)
        if t:
            t.cancel()
        t = threading.Timer(MISSION_COLLECT_SECS, _mission_advance_flush, args=(inst,))
        t.daemon = True
        _madv_timer[inst] = t
        t.start()


_orch_lock = threading.Lock()
_orch_timer = [None]
_orch_running = [False]
_orch_dirty = [False]


def orchestrator_ping():
    if not any(i.get("name") == ORCH_INSTANCE for i in load_instances()):
        return
    try:
        if not inbox_since(peek=True):   # only fire if there is really something new
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


# Supply the signal module with its cross-references (all now defined).
_signal_mod.load_settings = load_settings
_signal_mod.chat_log_append = chat_log_append
_signal_mod.orchestrator_ping = orchestrator_ping

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


WORKER_LOG = os.path.join(RUN_DIR, "worker.log")


def _wlog(msg):
    """Worker diagnostics into a file — journalctl is only accessible to root,
    and that is exactly why the exception was missing in the orphaned-task bug
    (Aug 20)."""
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    print("[worker]", msg, flush=True)
    try:
        with open(WORKER_LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def reclaim_stuck_tasks():
    """Reset orphaned 'running' tasks at startup. Exactly ONE worker runs — what
    is still 'running' at startup belongs to a crashed run (e.g. the store bug
    on Aug 20) and would otherwise never fire again."""
    tasks = load_tasks()
    n = 0
    for t in tasks:
        if t.get("status") == "running":
            t["status"] = "scheduled" if t.get("schedule") else "pending"
            n += 1
    if n:
        save_tasks(tasks)
        print(f"[worker] {n} orphaned 'running' task(s) reset", flush=True)


def _task_worker():
    """Processes due/pending tasks sequentially in the background."""
    reclaim_stuck_tasks()
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
                # Frequency cap: more than 6 runs/h of the same task is ALWAYS
                # a defect (loop bug Aug 20) — pause it for an hour.
                runs = [x for x in t.get("recent_runs", []) if now - x < 3600]
                if len(runs) >= 6:
                    t["recent_runs"] = runs
                    t["next_run"] = now + 3600
                    save_tasks(tasks)
                    _wlog(f"{t['id']}: >6 runs/h — paused for 1 h (loop protection)")
                    try:
                        notify_add("guardrail", f"Task loop throttled: {t['id']}",
                                   str(t.get("message", ""))[:120] + " — ran >6x/h, paused 1 h.",
                                   link="tasks")
                    except Exception:
                        pass
                    continue
                t["recent_runs"] = runs + [now]
                # Claim the task
                t["status"] = "running"
                t["updated"] = now
                save_tasks(tasks)
                # From here on EVERYTHING is guarded individually: an error
                # anywhere must never leave the task as a "running" orphan
                # (bug Aug 20: exception in the follow-up -> outer except ->
                # the task never fired again and the chat entry was missing).
                try:
                    ok, res = _run_task_now(t["instance"], t["message"])
                except Exception as e:
                    ok, res = False, f"worker-exception (run): {e!r}"
                    _wlog(f"{t['id']}: {res}")
                try:
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
                except Exception as e:
                    _wlog(f"{t['id']}: status update failed: {e!r}")
                try:
                    chat_log_append(t.get("instance", "task"), "task",
                                    t.get("message", ""), res, kind="task")
                except Exception as e:
                    _wlog(f"{t['id']}: chat_log_append: {e!r}")
                try:
                    history_add(t.get("instance", ""), t.get("message", ""), res, ok,
                                t.get("schedule", ""), origin="worker")
                except Exception as e:
                    _wlog(f"{t['id']}: history_add: {e!r}")
                try:
                    _mission_advance_fire(t["id"])
                except Exception as e:
                    _wlog(f"{t['id']}: mission-advance: {e!r}")
                ran = True
                break
        except Exception as e:
            _wlog(f"worker-loop: {e!r}")
        if not ran:
            time.sleep(5)
            # Orphan watch: if a task hangs on "running" for more than 30 min,
            # its run is lost (the timeout is 10 min) -> reset it.
            try:
                tasks2 = load_tasks()
                cut = int(time.time()) - 1800
                dirty = False
                for t2 in tasks2:
                    if t2.get("status") == "running" and t2.get("updated", 0) < cut:
                        t2["status"] = "scheduled" if t2.get("schedule") else "pending"
                        _wlog(f"{t2.get('id')}: running orphan reset")
                        dirty = True
                if dirty:
                    save_tasks(tasks2)
            except Exception as e:
                _wlog(f"orphan-watch: {e!r}")
            # TTL sweep while idle, at most once per hour.
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


# Tool catalog for the UI (mirrors BUILTIN in the openrouter agent). Display/
# allowlist only — the agent filters execution again itself.
AGENT_TOOLS_CATALOG = [
    {"name": "bash", "desc": "Run shell commands in the workspace"},
    {"name": "read_file", "desc": "Read a file"},
    {"name": "write_file", "desc": "Write a file"},
    {"name": "list_dir", "desc": "List a directory"},
    {"name": "offload_read", "desc": "Re-read offloaded (truncated) tool output"},
    {"name": "http_fetch", "desc": "Fetch a URL (HTTP)"},
    {"name": "read_pdf", "desc": "Extract PDF text (file or URL)"},
    {"name": "web_search", "desc": "Web search (DuckDuckGo) — needs internet"},
    {"name": "spawn_subagent", "desc": "Start an ephemeral subagent"},
    {"name": "create_task", "desc": "Queue a task (capable instance or ephemeral)"},
    {"name": "read_inbox", "desc": "Read new user messages (Signal/app/web)"},
    {"name": "list_tasks", "desc": "List running/scheduled tasks with IDs"},
    {"name": "delete_task", "desc": "Delete a running/scheduled task by ID"},
    {"name": "edit_task", "desc": "Change a task's message/schedule by ID"},
    {"name": "mission_start", "desc": "Create a mission: goal + steps (orchestrator only)"},
    {"name": "missions", "desc": "List open missions with status (orchestrator only)"},
    {"name": "mission_update", "desc": "Advance a mission step (orchestrator only)"},
    {"name": "mission_finish", "desc": "Complete a mission (orchestrator only)"},
    {"name": "send_signal", "desc": "Send a Signal message to the user (allowed numbers only)"},
    {"name": "notify", "desc": "Push notification to app + web manager (title + text)"},
    {"name": "oracle", "desc": "Second opinion before risky actions (challenges assumptions, never acts)"},
    {"name": "list_agents", "desc": "Available agents + capabilities (routing)"},
    {"name": "recall_tasks", "desc": "Query earlier tasks/results (institutional knowledge)"},
    {"name": "list_skills", "desc": "List available skills"},
    {"name": "load_skill", "desc": "Load a skill into the context"},
    {"name": "memory_store", "desc": "Remember a value permanently"},
    {"name": "memory_recall", "desc": "Retrieve a remembered value"},
    {"name": "playbook_add", "desc": "Record a permanent rule/playbook (always applies)"},
    {"name": "playbooks", "desc": "List playbooks (fixed rules)"},
    {"name": "playbook_forget", "desc": "Remove a playbook by ID"},
    {"name": "remote_ls", "desc": "List a katfs share"},
    {"name": "remote_read", "desc": "Read a katfs file"},
    {"name": "remote_write", "desc": "Write a katfs file"},
    {"name": "remote_delete", "desc": "Delete a katfs file/folder"},
    {"name": "list_secrets", "desc": "Show granted secret names"},
    {"name": "get_secret", "desc": "Fetch a granted secret"},
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
    # defaults from template.params, overridden by the passed config,
    # empty values pre-filled from the shared settings
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
    """Set an instance's tool allowlist. Empty/all list -> drop the field
    (= all tools). Takes effect at the next start (env-based)."""
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


# Order = display logic in render()/list_agents: the first present key is the
# instance's model.
MODEL_KEYS = ("OPENROUTER_MODEL", "ORCAROUTER_MODEL", "ANTHROPIC_MODEL", "PI_MODEL", "PRIME_MODEL", "LLAMA_MODEL")
# For switching provider via set_model("provider:model"): provider name -> key.
PROVIDER_MODEL_KEY = {"openrouter": "OPENROUTER_MODEL", "orcarouter": "ORCAROUTER_MODEL",
                      "anthropic": "ANTHROPIC_MODEL", "pi": "PI_MODEL",
                      "prime": "PRIME_MODEL", "llama": "LLAMA_MODEL"}


def set_model(name, model):
    """Switch an existing instance's model. Sets exactly the key the instance
    already uses (no new one is invented — otherwise nobody would know which
    provider is meant). Takes effect at the next start (env-based), like the
    tool allowlist."""
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    model = str(model or "").strip()
    if not model:
        return "error: no model given"
    cfg = inst.setdefault("config", {})
    # Provider switch: "orcarouter:tencent/hy3" additionally switches the backend
    # (sets its MODEL_KEY, removes the others). Without a prefix it stays with
    # the existing provider — only the model changes. The colon test triggers
    # ONLY for a known provider name, so ":free" model variants
    # (e.g. "mistralai/...:free") are not misread as a provider.
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
        apply_internet(inst, on)   # takes effect immediately, no restart needed
    return f"internet for '{name}': {'on' if on else 'off'}"


def delete_instance(name):
    inst = next((i for i in load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if is_running(inst):
        stop(inst)
    teardown_mounts(inst)   # safely remove any leftovers (binds/export)
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


# ---- Resource overview per instance (Resources tab) ------------------------
def _read_pid(inst):
    try:
        return int(open(pidfile(inst)).read().strip())
    except (OSError, ValueError):
        return None


def _proc_cpu_jiffies(pid):
    """utime+stime from /proc/<pid>/stat, robust against spaces in comm."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            after = fh.read().rpartition(")")[2].split()
        return int(after[11]) + int(after[12])   # utime (field 14) + stime (field 15)
    except (OSError, ValueError, IndexError):
        return None


def _proc_rss_kb(pid):
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def resource_stats():
    """Per instance: configured size (vCPU/RAM) + live usage (RSS, CPU%,
    overlay disk). CPU% via a short sample; percentages relative to ONE core
    (a 2-vCPU guest can reach up to ~200%)."""
    insts = load_instances()
    clk = os.sysconf("SC_CLK_TCK") or 100
    pids = {i["name"]: _read_pid(i) for i in insts}
    pids = {n: p for n, p in pids.items() if p is not None and os.path.exists("/proc/%d" % p)}
    t0 = {n: _proc_cpu_jiffies(p) for n, p in pids.items()}
    dt = 0.3
    time.sleep(dt)
    t1 = {n: _proc_cpu_jiffies(p) for n, p in pids.items()}
    out = []
    for i in insts:
        name = i["name"]
        running = name in pids
        rss = _proc_rss_kb(pids[name]) if running else None
        j0, j1 = t0.get(name), t1.get(name)
        cpu_pct = round(100.0 * (j1 - j0) / (clk * dt), 1) if (j0 is not None and j1 is not None) else None
        try:
            st = os.stat(upper_path(i)); upper_used_mb = round(st.st_blocks * 512 / 1048576.0, 1)
        except OSError:
            upper_used_mb = None
        out.append({
            "name": name, "running": running,
            "vcpus": i.get("vcpus", 2), "mem_mib": i.get("mem_mib", 1024),
            "rss_mb": round(rss / 1024.0, 1) if rss else None,
            "cpu_pct": cpu_pct,
            "persist": bool(i.get("persist_disk")),
            "upper_used_mb": upper_used_mb,
        })
    return out


# ---- networking ------------------------------------------------------------
def ensure_net_base():
    sh("sysctl", "-w", "net.ipv4.ip_forward=1", check=False)
    r = sh("iptables", "-t", "nat", "-C", "POSTROUTING", "-s", POOL, "-o", HOSTIF,
           "-j", "MASQUERADE", check=False)
    if r.returncode != 0:
        sh("iptables", "-t", "nat", "-A", "POSTROUTING", "-s", POOL, "-o", HOSTIF,
           "-j", "MASQUERADE", check=False)
    # Guest isolation: microVMs must NOT route to each other. A compromised
    # agent could otherwise reach another instance's chat/term ports (8080/7682,
    # bound to 0.0.0.0, no auth). Backstop DROP for pool->pool; the tap ACCEPTs
    # below are additionally scoped so they never even match guest-to-guest.
    # Guest->gateway (8700 broker) is host-local (INPUT) and unaffected by this.
    if sh("iptables", "-C", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP",
          check=False).returncode != 0:
        sh("iptables", "-A", "FORWARD", "-s", POOL, "-d", POOL, "-j", "DROP", check=False)


def setup_tap(inst):
    n = net_of(inst)
    sh("ip", "link", "del", n["tap"], check=False)
    sh("ip", "tuntap", "add", n["tap"], "mode", "tap")
    sh("ip", "addr", "add", f"{n['host']}/30", "dev", n["tap"])
    sh("ip", "link", "set", n["tap"], "up")
    # The host has FORWARD policy DROP + Docker chains in front of it -> generic
    # rules don't apply reliably. So allow tap traffic RIGHT AT THE TOP (before
    # DROP/Docker) — but ONLY to/from outside the pool. This lets the guest reach
    # the internet (destination not in the pool) and replies back (source not in
    # the pool), while guest-to-guest (both in the pool) matches no ACCEPT rule
    # and gets caught by the pool->pool DROP or the DROP policy.
    # Clear old, unrestricted ACCEPTs of the same tap first (the tap name is
    # reused on restart, otherwise the old hole would stay open).
    for spec in (["-i", n["tap"]], ["-o", n["tap"]]):
        while sh("iptables", "-C", "FORWARD", *spec, "-j", "ACCEPT", check=False).returncode == 0:
            sh("iptables", "-D", "FORWARD", *spec, "-j", "ACCEPT", check=False)
    apply_internet(inst, inst.get("internet", True))


# Until now, guests with internet=on could go anywhere — including the whole
# LAN. Home Assistant and Portainer were thus reachable from EVERY VM, whether
# the MCP was assigned to it or not (the broker protects the tokens, but the
# door stood open anyway). Now: internet yes, LAN no — except the endpoints of
# the MCPs listed in the instance's MCP_SERVERS, and the guests' DNS.
# DNS for the guests (ends up in resolv.conf via guest-init). Site-specific —
# set it via env on other installations; 1.1.1.1 works everywhere.
GUEST_DNS = os.environ.get("GUEST_DNS") or SITE.get("GUEST_DNS") or "1.1.1.1"
_PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def _mcp_endpoints(inst):
    """LAN targets (ip, port) that this instance needs according to MCP_SERVERS.
    Read from the catalog, not from the instance — the latter only holds names.
    IP literals only: a hostname in the catalog that resolves into the LAN would
    NOT be allowed here (deliberately; enter the IP instead)."""
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
                    continue          # public targets are covered by the internet rule
            except ValueError:
                continue
            out.append((host, int(port or (443 if scheme == "https" else 80))))
    return sorted(set(out))


def _llama_endpoint(inst):
    """(ip, port) of the llama.cpp server, if the instance uses it AND it is on
    the private network — then the gating must let it through. An endpoint on the
    host (reachable via the gateway) or on the internet needs no special rule."""
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
    """Set/remove the instance's egress rules. `allow=False` means: the VM may
    not leave its own /30 — no LAN, no internet. The manager broker at the
    gateway (8700) stays reachable (host-local, INPUT). And with it the LLM
    endpoint: an agent without internet CANNOT think.

    With allow=True the instance gets its own FORWARD chain:
      1. its MCP endpoints (tcp, targeted)     -> ACCEPT
      2. the guest DNS (53)                     -> ACCEPT
      3. private networks                       -> REJECT (not DROP: the
         agent should fail immediately, not run into a 30 s timeout)
      4. everything outside the pool (internet) -> ACCEPT
    The return path stays the generic rule: through NAT, replies are only
    possible for connections the guest opened itself."""
    n = net_of(inst)
    chain = _fc_chain(inst)

    # Clear out leftovers, idempotent: jump rule, chain, old direct rule.
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
    # Egress allowlist (guardrail): if EGRESS_ALLOW is in the instance config
    # (comma list of domains/IPs), the VM may go ONLY there — instead of
    # "everything except private". Domains are resolved at start (A records); a
    # stop/start is needed if the target's DNS changes. Empty = as before.
    egress = (inst.get("config", {}).get("EGRESS_ALLOW", "") or "").strip()
    if egress:
        seen = set()
        for host in [h.strip() for h in egress.split(",") if h.strip()]:
            try:
                infos = socket.getaddrinfo(host, None, socket.AF_INET)
                ips = sorted({i[4][0] for i in infos})
            except OSError:
                print(f"[egress] {inst['name']}: '{host}' not resolvable — skipped",
                      flush=True)
                continue
            for ip in ips:
                if ip not in seen:
                    seen.add(ip)
                    sh("iptables", "-A", chain, "-d", ip, "-j", "ACCEPT", check=False)
        sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "REJECT", check=False)
    else:
        sh("iptables", "-A", chain, "!", "-d", POOL, "-j", "ACCEPT", check=False)
    sh("iptables", "-I", "FORWARD", "1", "-i", n["tap"], "-j", chain, check=False)
    if not have_back:
        sh("iptables", "-I", "FORWARD", "1", *back, "-j", "ACCEPT", check=False)


def teardown_tap(inst):
    # Rules point at the tap NAME and survive deletion of the device — without
    # cleanup, dead chains pile up.
    n = net_of(inst)
    chain = _fc_chain(inst)
    sh("iptables", "-D", "FORWARD", "-i", n["tap"], "-j", chain, check=False)
    sh("iptables", "-F", chain, check=False)
    sh("iptables", "-X", chain, check=False)
    sh("ip", "link", "del", n["tap"], check=False)


# ---- host folders (NFS bind-mounts) ----------------------------------------
def ensure_agent_crossmnt():
    """The workspace export needs 'crossmnt' so the host-folder submounts are
    visible over NFSv4. Idempotent, with a one-time backup."""
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
    """Normalized host-folder mounts: bind target, NFS subpath, fsid, mode."""
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
    """Write desired.list under .fcmnt/<inst>/ — the reconciler in the guest
    reads it (via the workspace mount) and keeps the mounts up to date live."""
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
            continue  # missing host folder -> skip (do not create)
        os.makedirs(s["target"], exist_ok=True)
        sh("umount", "-l", s["target"], check=False)   # release any old bind
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
    write_desired(inst)   # the reconciler in the guest picks up the mounts


def teardown_mounts(inst):
    ef = os.path.join(EXPORTS_D, f"fc-{inst['name']}.exports")
    if os.path.exists(ef):
        os.remove(ef)
        sh("exportfs", "-ra", check=False)
    d = os.path.join(FCMNT_ROOT, inst["name"])
    if os.path.isdir(d):
        # scan the actual contents (robust against leftovers): release
        # sub-binds, then remove empty directories (rmdir fails on busy/mount).
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
        # apply LIVE: tear down removed folders, export the current (new) ones.
        new_subs = {s["sub"] for s in mount_specs(inst)}
        for s in old_specs:
            if s["sub"] not in new_subs:
                sh("umount", "-l", s["target"], check=False)
                try:
                    os.rmdir(s["target"])
                except OSError:
                    pass
        setup_mounts(inst)            # bind+export of the current folders (idempotent)
        write_desired(inst)           # the running guest mounts them itself (reconciler)
        note = " (applied live)"
    return f"{len(inst['mounts'])} host folders saved{note}"


# ---- firecracker lifecycle -------------------------------------------------
def make_config_disk(inst):
    """Create a small ext4 drive with the instance config (key=value) -> vdb."""
    cfg = dict(inst.get("config", {}))
    # Second guard: older instance JSONs may still contain a key/MCP_CONFIG;
    # they still must not reach the disk.
    for k in NEVER_PERSIST:
        cfg.pop(k, None)
    # A minimal VM init does not have /usr/local/bin in PATH -> inject it so
    # claude/fabric are found (guest-init sources the config disk).
    cfg.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    cfg["FC_INSTANCE"] = inst["name"]   # for the host-folder reconciler in the guest
    if inst["name"] == ORCH_INSTANCE:   # only the orchestrator may manage tasks
        cfg["TASK_ADMIN"] = "1"
    # Key injection proxy active? Then the agent sends chat requests to the
    # manager instead of directly to the router — so the VM never sees an LLM key
    # (not even via the secret broker). The switch lives in the shared settings
    # so that ALL instances are switched over consistently.
    if load_settings().get("LLM_KEY_PROXY") == "1":
        cfg["KEY_PROXY"] = "1"
    d = os.path.join(RUN_DIR, f"{inst['name']}.cfgdir")
    os.makedirs(d, exist_ok=True)
    # Put tool plugins (firecracker/plugins/*.py) on the disk too — the agent
    # loads them at start from /config/plugins. New plugin = file + stop/start.
    pdst = os.path.join(d, "plugins")
    shutil.rmtree(pdst, ignore_errors=True)
    psrc = os.path.join(BASE, "plugins")
    if os.path.isdir(psrc):
        os.makedirs(pdst, exist_ok=True)
        for f0 in sorted(os.listdir(psrc)):
            sp = os.path.join(psrc, f0)
            if os.path.isdir(sp):                 # multi-file tool: whole folder
                shutil.copytree(sp, os.path.join(pdst, f0), dirs_exist_ok=True)
            elif f0.endswith(".py"):              # single .py (backwards compatible)
                shutil.copy2(sp, os.path.join(pdst, f0))
    with open(os.path.join(d, "config.env"), "w") as f:
        for k, v in cfg.items():
            # quote values (EXTRA_MOUNTS and others contain shell metacharacters like | and ;)
            f.write(f"{k}={shlex.quote(str(v))}\n")
    img = os.path.join(RUN_DIR, f"{inst['name']}.config.ext4")
    with open(img, "wb") as f:
        f.truncate(16 * 1024 * 1024)
    sh("mkfs.ext4", "-F", "-q", "-d", d, img, check=False)
    return img


# ---- Overlay rootfs ---------------------------------------------------------
# For images in OVERLAY_ROOTFS the VM boots with the SHARED base read-only
# (Firecracker blocks writes at the host level -> no journal conflict) plus a
# small rw upper image per instance; the guest init assembles the root from
# them via overlayfs+pivot_root. Advantage: no 2-GB copy per start, and with
# inst["persist_disk"]=true the write layer (installations!) survives a
# stop/start. Other images run unchanged via private_rootfs().
OVERLAY_ROOTFS = {"instances/openrouter-rootfs.ext4", "instances/claude-rootfs.ext4"}
UPPER_SIZE_MB = 1024          # throwaway layer per start
UPPER_PERSIST_SIZE_MB = 4096  # persistent layer (apt/pip need room); sparse


def upper_path(inst):
    if inst.get("persist_disk"):
        return os.path.join(INST_DIR, f"{inst['name']}-upper.ext4")
    return os.path.join(RUN_DIR, f"{inst['name']}.upper.ext4")


def make_upper(inst):
    """Provide an empty (or, with persist, existing) upper image."""
    p = upper_path(inst)
    if inst.get("persist_disk") and os.path.exists(p):
        return p
    size = UPPER_PERSIST_SIZE_MB if inst.get("persist_disk") else UPPER_SIZE_MB
    tmp = p + ".new"
    with open(tmp, "wb") as fh:          # sparse, without external truncate
        fh.truncate(size * 1024 * 1024)
    mkfs = shutil.which("mkfs.ext4") or "/sbin/mkfs.ext4"
    sh(mkfs, "-F", "-q", "-L", "fcupper", tmp)
    os.replace(tmp, p)
    return p


def reset_upper(name):
    """Delete the persistent write layer (factory reset). Only while stopped."""
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
    """Create a fresh rootfs copy for exactly this VM and return its path.

    All instances of a template pointed at the SAME ext4 image, writable. Two
    VMs running simultaneously then share one journal — that worked as long as
    barely anything was written, and ended on Aug 15 with 'error loading
    journal' at boot. Hence: a separate copy per start (sparse, ~seconds fast).
    Side effect, and a deliberate one: a restart always boots the current
    template image, rootfs updates take effect as before with stop/start. State
    that should persist doesn't live here anyway, but centrally (memory.json,
    chats.json, katfs)."""
    src = os.path.join(BASE, inst["rootfs"])
    dst = os.path.join(RUN_DIR, f"{inst['name']}.rootfs.ext4")
    tmp = dst + ".new"
    # --sparse=always: the 2-GB image carries ~550 MB; the copy should occupy
    # just as little. First .new, then rename — a half copy must never start as
    # a rootfs.
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
        # Last drive = upper; the device name follows from the position
        # (virtio-blk: vda, vdb, ...). The guest reads it from /proc/cmdline.
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
    # The private rootfs copy is worthless after stopping (the next start pulls
    # a fresh one) — just disk space, so remove it.
    for f in (f"{inst['name']}.rootfs.ext4", f"{inst['name']}.upper.ext4"):
        try:
            os.remove(os.path.join(RUN_DIR, f))
        except OSError:
            pass
    return "stopped"


# ---- Personas / system prompts --------------------------------------------
PERSONAS_FILE = os.path.join(BASE, "personas.json")
_DEFAULT_PERSONAS = [
    {"name": "assistant",
     "prompt": "You are a helpful agent with tools (shell, files, web, MCP). "
               "Use tools when needed, otherwise answer directly. Keep it brief."},
    {"name": "researcher",
     "prompt": "You are a thorough researcher. Use web_search and http_fetch, check multiple "
               "sources and cite URLs as evidence. Summarize in a structured way. For large "
               "tasks, use spawn_subagent to research sub-questions in parallel."},
    {"name": "coder",
     "prompt": "You are an experienced software developer. Use bash/read_file/write_file in the "
               "workspace, work in small steps, test your result and briefly explain what you do."},
    {"name": "translator",
     "prompt": "You are a precise translator. Translate naturally and idiomatically, without "
               "comments, unless the user explicitly asks for them."},
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


# ---- Skills library (expert knowledge, loaded on demand by the agent) -------
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


# ---- Playbooks + prompt templates: moved out to mgr/rules.py ---------------
from mgr import rules as _rules  # noqa: E402
_rules.configure(BASE)
from mgr.rules import (load_playbooks, pb_list, pb_add, pb_remove, PB_MAX,  # noqa: E402,F401
                       load_prompts, prompt_upsert, prompt_delete, PROMPTS_MAX)


# ---- Missions: moved out to mgr/missions.py (imported early, see above) ----
from mgr.missions import (load_missions, mission_list, mission_start,  # noqa: E402,F401
                          mission_update, mission_finish, mission_admin,
                          mission_ttl_sweep, mission_for_task, mission_owner,
                          MISSION_MAX_ACTIVE, MISSION_MAX_STEPS, MISSION_TTL_DAYS)


# ---- Secrets broker (on-demand, allowlist per template/instance) -----------
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
    """All brokerable secrets. Source 1 is the secret store (0600). Source 2 is
    the manager settings — the LLM keys are maintained there, and since they no
    longer flow into the instance config, the broker has to deliver them. The
    store wins on a name collision."""
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
    """Effective allowlist = by_template[template] ∪ by_instance[name]. Default deny."""
    if not inst:
        return set()
    pol = load_secret_policy()
    keys = set(pol.get("by_template", {}).get(inst.get("template", ""), []))
    keys |= set(pol.get("by_instance", {}).get(inst.get("name", ""), []))
    return keys


def save_secret_policy(pol):
    """Save the policy (only {by_template,by_instance} with string lists)."""
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


# ---- MCP catalog + hub: moved out to mgr/mcp.py ----------------------------
from mgr.mcp import (MCP_HUB, MCP_CATALOG_FILE, load_mcps, save_mcps, upsert_mcp,  # noqa: E402,F401
                     delete_mcp, mcp_required_secrets, mcp_hub_call, mcp_hub_kill,
                     build_mcp_config)


# ---- Browse host folders (the UI's folder picker) --------------------------
# Directory names only, never file contents. The manager runs as root and thus
# sees everything — the route is admin-only like /api/secret-keys (guests
# blocked by source IP) and sits behind the same auth as the UI.

def list_dirs(path, show_hidden=False):
    p = os.path.abspath(path or "/") or "/"
    parent = "" if p == "/" else os.path.dirname(p)
    if not os.path.isdir(p):
        return {"path": p, "parent": parent, "dirs": [], "error": "not a directory"}
    try:
        dirs = sorted((e.name for e in os.scandir(p)
                       if e.is_dir(follow_symlinks=False)
                       and (show_hidden or not e.name.startswith("."))),
                      key=str.lower)
    except OSError as e:
        return {"path": p, "parent": parent, "dirs": [], "error": f"no access ({e.strerror})"}
    return {"path": p, "parent": parent, "dirs": dirs}


# ---- katfs: moved out to mgr/katfs.py --------------------------------------
from mgr.katfs import (KATFS_HOST, KATFS_PORT, KATFS_BASE, KATFS_MAX_WRITE,  # noqa: E402,F401
                       katfs_share_for, katfs_proxy_fs, katfs_zip, katfs_status,
                       KATFS_ZIP_MAX_FILES, KATFS_ZIP_MAX_BYTES)

from mgr import irohgw as _irohgw  # noqa: E402
_irohgw.configure(BASE)
from mgr.irohgw import (status as irohgw_status,  # noqa: E402,F401
                        allow_add as irohgw_allow_add, allow_remove as irohgw_allow_remove)

# ---- Audit log per instance (tool calls, URLs) -----------------------------
# Lives on the host (survives VM restarts). JSONL, one file per instance,
# hard-capped to the last N lines.
AUDIT_DIR = os.path.join(BASE, "audit")
AUDIT_MAX_LINES = 2000


def audit_append(inst_name, tool, target, ok):
    os.makedirs(AUDIT_DIR, exist_ok=True)
    p = os.path.join(AUDIT_DIR, f"{inst_name}.jsonl")
    rec = {"ts": int(time.time()), "tool": str(tool)[:64],
           "target": str(target)[:400], "ok": bool(ok)}
    with open(p, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # trim occasionally so the file doesn't grow without bound
    try:
        with open(p) as fh:
            lines = fh.readlines()
        if len(lines) > AUDIT_MAX_LINES + 200:
            with open(p, "w") as fh:
                fh.writelines(lines[-AUDIT_MAX_LINES:])
    except OSError:
        pass


def effective_policy(inst):
    """Everything an instance IS ALLOWED to do in one place: network, tools,
    secrets, MCP servers, model. Pulls the scattered controls (instance config,
    secret-policy) together into one view."""
    cfg = inst.get("config") or {}
    at = cfg.get("AGENT_TOOLS", "")
    tools_allowed = [t.strip() for t in at.split(",") if t.strip()] if at else None  # None = all
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
    return list(reversed(out))   # newest first


# ---- web -------------------------------------------------------------------
# PAGE (HTML/JS of the manager UI) now lives in mgr/ui.py.
from mgr.ui import PAGE  # noqa: E402

# ---- Brand ------------------------------------------------------------------
# logo.svg is kept as a file (favicon, shared elsewhere). For the header mark
# the navy inherits the text color so it carries in both the light and the dark
# theme; the turquoise stays the accent.
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

# Icons for the server-side rendered instance rows (Feather style, 14px).
_SVG = ('<svg width=14 height=14 viewBox="0 0 24 24" fill=none stroke=currentColor '
        'stroke-width=1.5 stroke-linecap=round stroke-linejoin=round>%s</svg>')
IC_TERM = _SVG % '<polyline points="4 17 10 11 4 5"></polyline><line x1=12 y1=19 x2=20 y2=19></line>'
IC_CHAT = _SVG % '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>'
IC_FILES = _SVG % ('<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
                   'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>')
IC_DEL = _SVG % ('<path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>'
                 '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>')
# small folder icon (12px) for the mount rows — instead of 📁 (emoji tofus without an emoji font)
IC_AUDIT = _SVG % ('<path d="M4 5h16M4 12h16M4 19h10"></path>'
                   '<circle cx="19" cy="19" r="2.4"></circle><path d="M22 22l-1.3-1.3"></path>')
IC_FILES2 = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 '
             'stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px"><path d="M4 20h16a2 '
             '2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 '
             '2v13a2 2 0 0 0 2 2Z"></path></svg>')


def h(v):
    """HTML escape for server-side rendering. The instance name is trimmed to
    [a-z0-9-_] on creation, but everything else comes freely from forms or
    templates — model ID (free text field), description, mount paths, tool
    list. Without escaping it lands raw in the markup: anyone who sets a mount
    row or a custom model ID would otherwise write script into the admin page."""
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
        # The chip is clickable: opens the model-switch dialog (editModel in PAGE JS).
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
                f"title='LLM usage reported by this agent "
                f"({ut['calls']} calls total)'>"
                f"Tokens today {_fmt_tok(ud.get('in'))}&nbsp;/&nbsp;{_fmt_tok(ud.get('out'))}"
                f" · {_fmt_cost(ud.get('cost'))}"
                f" &nbsp;·&nbsp; total {_fmt_tok(ut['in'])}&nbsp;/&nbsp;{_fmt_tok(ut['out'])}"
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
                    f"title='Persistent disk: installations survive stop/start"
                    f"{' — right-click: reset disk' if pers else ''}' "
                    f"onclick=\"togglePersist('{name}',{str(not pers).lower()})\" "
                    f"oncontextmenu=\"return diskReset('{name}')\">"
                    f"{'💾 persistent' if pers else '↺ fresh per start'}</button>")
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
                # Only name + description into the page: with an imported
                # catalog the contents are ~1 MB, and the UI needs them only
                # when editing (then it fetches GET /api/skills/<name>).
                .replace("__SKILLS__", json.dumps(
                    [{"name": x.get("name", ""), "description": x.get("description", "")}
                     for x in load_skills()], ensure_ascii=False))
                .replace("__HOSTIF__", HOSTIF).replace("__POOL__", POOL)
                .replace("__PUBLIC_HOST__", PUBLIC_HOST)
                .replace("__SIGNAL_HOST__", SIGNAL_HOST)
                .replace("__CODE_LINK__",
                         f'<a href="{html.escape(CODE_URL, quote=True)}" target="_blank" '
                         f'rel="noopener noreferrer">VS&nbsp;Code</a>' if CODE_URL else "")
                .replace("__HOME__", os.path.expanduser(
                    "~" + (os.environ.get("SUDO_USER") or "")))
                )


# ---- Chat (UI under /chat, see chatui.py) ----------------------------------
# Chattable is every instance with TRANSPORT=web: the bridge in the microVM
# serves /api/chat (and optionally /api/chat/stream) on :8080.

def web_instances():
    """Instances you can chat with (+ running state for the UI)."""
    return [{"name": i["name"], "running": is_running(i),
             "description": i.get("description", "")}
            for i in load_instances()
            if (i.get("config") or {}).get("TRANSPORT") == "web"]


def wait_web(inst, timeout=120):
    """Starts the instance if needed and waits until the bridge accepts."""
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
    """Non-streaming call to the bridge in the microVM."""
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
    """Streams tokens from /api/chat/stream. Bridges without streaming answer on
    the same path with JSON — that then arrives as a single piece."""
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


# ---- Guardrails: budget + rate limit for LLM calls --------------------------
# Enforcement at the key injection proxy: all of the VMs' router calls pass
# through there. Budget per instance and day (tokens, from llm_usage) and a
# frequency cap per minute. Override per instance via config: BUDGET_TOKENS
# (0 = off), LLM_RATE_MIN. On exceedance: 429 + at most one notify per hour.
GUARD_BUDGET_TOKENS = int(os.environ.get("GUARD_BUDGET_TOKENS", "5000000"))
GUARD_LLM_RATE_MIN = int(os.environ.get("GUARD_LLM_RATE_MIN", "60"))
_guard_lock = threading.Lock()
_guard_calls = {}          # instance -> [timestamps]
_guard_notified = {}       # instance -> ts of the last budget notify


def _guard_check(inst):
    """(allowed, reason). inst = instance dict or None (admin/host: always ok)."""
    if inst is None:
        return True, ""
    name = inst["name"]
    cfg = inst.get("config") or {}
    now = time.time()
    # 1) Frequency per minute
    try:
        rate = int(cfg.get("LLM_RATE_MIN", GUARD_LLM_RATE_MIN))
    except ValueError:
        rate = GUARD_LLM_RATE_MIN
    with _guard_lock:
        lst = _guard_calls.setdefault(name, [])
        lst[:] = [t for t in lst if now - t < 60]
        if rate > 0 and len(lst) >= rate:
            return False, f"rate limit: {rate} LLM calls/min reached"
        lst.append(now)
    # 2) Daily budget (tokens since local midnight)
    try:
        budget = int(cfg.get("BUDGET_TOKENS", GUARD_BUDGET_TOKENS))
    except ValueError:
        budget = GUARD_BUDGET_TOKENS
    if budget > 0:
        midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
        u = usage_for(name, midnight)
        used = (u.get("in") or 0) + (u.get("out") or 0)
        if used >= budget:
            with _guard_lock:
                last = _guard_notified.get(name, 0)
                fire = now - last > 3600
                if fire:
                    _guard_notified[name] = now
            if fire:
                try:
                    notify_add("guardrail", f"Budget reached: {name}",
                               f"{used:,} tokens today (limit {budget:,}). LLM calls "
                               f"pause until midnight. Override: BUDGET_TOKENS in the "
                               f"instance config.", link="tasks")
                except Exception:
                    pass
            return False, f"budget: {used:,}/{budget:,} tokens used today"
    return True, ""


# ---- Routing table ---------------------------------------------------------
# Erster Schritt weg von der if-Kette (Strangler wie beim mgr/-Paket): wer hier
# steht, wird ueber die Tabelle zugestellt; alles andere faellt weiter durch die
# Kette. Eine Route liefert (body, content_type) und ueberlaesst das Senden dem
# Verteiler — oder None, wenn sie selbst geantwortet hat.
from mgr import websearch as _websearch_mod  # noqa: E402
_websearch_mod.configure(lambda key: (load_settings().get(key) or ""))

from mgr.routes import Router  # noqa: E402
ROUTER = Router()


@ROUTER.get("/api/instances", admin=True)
def _rt_instances(h):
    return (json.dumps([{**i, "running": is_running(i)} for i in load_instances()]).encode(),
            "application/json")


@ROUTER.get("/api/settings", admin=True)
def _rt_settings(h):
    return json.dumps(settings_for_ui()).encode(), "application/json"


@ROUTER.get("/api/tasks", admin=True)
def _rt_tasks(h):
    return json.dumps(load_tasks()).encode(), "application/json"


@ROUTER.get("/api/usage", admin=True)
def _rt_usage(h):
    return json.dumps(usage_summary()).encode(), "application/json"


@ROUTER.get("/api/gateway", admin=True)
def _rt_gateway(h):
    g = load_gateway()
    g["available"] = _clean_unicode is not None
    return json.dumps(g).encode(), "application/json"


@ROUTER.get("/api/personas")
def _rt_personas(h):
    return json.dumps(load_personas(), ensure_ascii=False).encode(), "application/json"


@ROUTER.get("/api/skills")
def _rt_skills(h):
    # ?meta=1: name + description only. The full catalog is ~870 KB with the
    # bodies — the agents call this on every list_skills and never need them.
    q = urllib.parse.parse_qs(h.path.partition("?")[2])
    items = load_skills()
    if q.get("meta", ["0"])[0] == "1":
        items = [{"name": x.get("name", ""), "description": x.get("description", "")}
                 for x in items]
    return json.dumps(items, ensure_ascii=False).encode(), "application/json"


@ROUTER.get("/api/skills/", prefix=True)
def _rt_skill(h):
    nm = re.sub(r"[^a-z0-9_-]", "", h.path.split("/api/skills/", 1)[1].lower())
    sk = next((x for x in load_skills() if x.get("name") == nm), None)
    return ((sk.get("content", "") if sk else f"Skill '{nm}' not found").encode(),
            "text/plain; charset=utf-8")


@ROUTER.get("/api/websearch")
def _rt_websearch(h):
    # Web search for the agents: the Brave key stays on the host, the VM only
    # ever sees results. Same principle as the LLM key proxy.
    from mgr import websearch
    q = urllib.parse.parse_qs(h.path.partition("?")[2])
    query = q.get("q", [""])[0].strip()
    if not query:
        return json.dumps({"error": "q missing"}).encode(), "application/json"
    count = q.get("count", ["5"])[0]
    out = {"result": websearch.web_search(query, count)}
    return json.dumps(out, ensure_ascii=False).encode(), "application/json"


@ROUTER.post("/api/extract", admin=True)
def _rt_extract(h):
    # Chat attachment: PDF/DOCX/text in, extracted text out. The app puts the
    # text into the message; the model never sees the binary. Admin-only: this
    # is a client feature, agents extract inside their VM (read_pdf).
    from mgr.extract import extract_document
    name = urllib.parse.parse_qs(h.path.partition("?")[2]).get("name", ["upload"])[0]
    ln = int(h.headers.get("Content-Length", 0) or 0)
    if ln > 50 * 1024 * 1024:
        return json.dumps({"error": "file larger than 50 MB"}).encode(), "application/json"
    data = h.rfile.read(ln)
    try:
        text, note = extract_document(name, data)
        out = {"name": name, "text": text, "chars": len(text)}
        if note:
            out["note"] = note
    except ValueError as e:
        out = {"error": str(e)}
    except Exception as e:
        out = {"error": f"extraction failed: {e!r}"}
    return json.dumps(out, ensure_ascii=False).encode(), "application/json"


@ROUTER.get("/logo.svg")
@ROUTER.get("/favicon.ico")
def _rt_logo(h):
    return LOGO_SVG.encode(), "image/svg+xml"


class H(BaseHTTPRequestHandler):
    # Protection layer: an unhandled exception in a route must NOT tear the
    # connection down hard (the agent would otherwise see "RemoteDisconnected").
    # If no header has been sent yet, we respond cleanly with HTTP 500; otherwise
    # the response is just ended. The error lands in the journal.
    def end_headers(self):
        self._sent = True
        return super().end_headers()

    def _fail500(self):
        import traceback
        tb = traceback.format_exc()
        print(f"[http] unhandled in {self.command} {self.path}:\n{tb}", flush=True)
        if getattr(self, "_sent", False):
            return
        try:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"internal server error"}')
        except Exception:
            pass

    def do_GET(self):
        self._sent = False
        try:
            self._do_GET()
        except Exception:
            self._fail500()

    def do_POST(self):
        self._sent = False
        try:
            self._do_POST()
        except Exception:
            self._fail500()

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
        """POST /api/chat/<instance> -> response tokens as raw text (stream)."""
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
            return emit(f"⚠️ No web instance '{name}'.")
        if not wait_web(inst):
            return emit(f"⚠️ Instance '{name}' does not start (port {WEB_GUEST_PORT}).")

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
        # Otherwise the connect timeout stays as a READ timeout on the socket —
        # after 10 s of idling recv() tore the tunnel down ("connection closed").
        # A terminal may be silent arbitrarily long: timeouts off, but TCP
        # keepalive instead, so half-dead connections still die.
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
        """Pass through the katfs node's share page under /katfs/. GET only —
        the page loads assets relatively and then speaks P2P (WASM/iroh), it
        needs nothing else from the node. Purpose: same origin as the manager,
        i.e. HTTPS behind Traefik → the File System Access API works."""
        rest, _, qs = (self.path[len("/katfs"):] or "/").partition("?")
        # ?key=… replaces the node-id inserted by the node in the #nodeid field —
        # so this browser can also deliver a folder to a *foreign* katfs node.
        # Strictly filtered, the value lands in an attribute.
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
            self.wfile.write(f"<h3>katfs node not reachable</h3>"
                             f"<p>{KATFS_BASE} — {e}</p>".encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method, port=WEB_GUEST_PORT, tail_override=None):
        rest = self.path[3:]  # strip "/i/"
        name, _, tail = rest.partition("/")
        if tail_override is not None:
            tail = tail_override
        inst = next((i for i in load_instances() if i["name"] == name), None)
        if not inst or not is_running(inst):
            # The app puts the body of an API answer straight into the chat bubble —
            # HTML would show up there as raw <p>…</p>. So: markup only for the
            # browser paths, plain text for /api/….
            api = tail.split("?", 1)[0].startswith("api/")
            msg = f"Instance '{name}' is not running (web UI unavailable)."
            self.send_response(503)
            self.send_header("Content-Type",
                             "text/plain; charset=utf-8" if api else "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((msg if api else f"<p>{msg}</p>").encode())
            return
        url = f"http://{net_of(inst)['guest']}:{port}/{tail}"
        data = None
        if method == "POST":
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        # The app doesn't chat via /api/chat/<inst> but through here — so the
        # gateway has to sit at both entrances, not just the more convenient one.
        guard = None
        if method == "POST" and tail.split("?", 1)[0] in ("api/chat", "api/chat/stream"):
            try:
                b = json.loads(data or b"{}")
            except (ValueError, TypeError):
                b = None
            if isinstance(b, dict):
                chat_id = b.pop("chat", None)      # the guest doesn't know it, stays here
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
            # Bridges without streaming (the claude template) answer with
            # {"reply": …} and Content-Type application/json — even on
            # /api/chat/stream. But the app reads the body as raw text and would
            # otherwise show the bare JSON including \uXXXX. So unpack it here and
            # forward it as text/plain, as guest_stream has long done for the web
            # chat. The Content-Type is fixed BEFORE sending.
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
            # Pass through chunk by chunk + flush -> token streaming from the
            # agent. With the gateway a decoder runs in between: otherwise 4-KB
            # cuts would fall in the middle of a multi-byte character.
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
                # Flush the decoder first, then the buffer — the other way round
                # the last character would come through unfiltered.
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

    def _do_GET(self):
        if not self._auth():
            return
        # Tabelle zuerst. Was dort steht, kann von keiner spaeteren Praefix-
        # Verzweigung mehr verdeckt werden — das ist der ganze Zweck.
        hit = ROUTER.resolve("GET", self.path)
        if hit is not None:
            fn, admin_only = hit
            if admin_only and instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            out = fn(self)
            if out is None:
                return                      # die Route hat selbst geantwortet
            body, ct = out
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?", 1)[0].rstrip("/") == "/chat":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            want = urllib.parse.parse_qs(q).get("i", [""])[0]
            body = chatui.render(web_instances(), want, LOGO_INLINE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Don't cache: otherwise the browser holds on to an old version (that
            # was the cause of the gray emoji boxes after the icon fix).
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
        # Secrets broker: guests only (instance identified by source IP), allowlist.
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
            # Subscription login for the claude template: the guest fetches the
            # LIVE credential of the host at boot (so it follows the user's next
            # /login). Only the claudeAiOauth block — the mcpOAuth tokens
            # (Atlassian etc.) are none of the VM's business. Strictly gated:
            # only a real guest whose instance runs the claude template.
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
                self.wfile.write(json.dumps({"error": "not allowed"}).encode())
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
        # Agent roster for routing (orchestrator/list_agents). Guest-allowed,
        # capabilities only — no secrets. Ephemeral children hidden.
        if self.path == "/api/agents":
            roster = []
            for i in load_instances():
                if i["name"].startswith(("task-", "sub-")):
                    continue
                cfg = i.get("config") or {}
                mkey = next((k for k in MODEL_KEYS if cfg.get(k)), "")
                # Derive the backend from the set model key (NOT from the
                # template — that stays e.g. "openrouter" even after switching to
                # orcarouter/llama via set_model).
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
        # Inbox for the orchestrator: new user messages (Signal/app/web) since
        # the last run. Guest-allowed; ?peek=1 sets no watermark.
        if self.path.startswith("/api/inbox"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            data = {"messages": inbox_since(peek=(q.get("peek", ["0"])[0] == "1"))}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # RUNNING tasks (not the history) — for the agent's list_tasks/delete_task.
        # Guest-open; tasks carry no secrets.
        if self.path.startswith("/api/missions"):
            # Guest: only its OWN missions (every agent may own missions, not
            # just the orchestrator). Admin: ?instance= or all.
            g = instance_by_ip(self.client_address[0])
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
        # Queryable task history (institutional knowledge). Open to guests
        # (recall_tasks) AND admin/UI — holds operational knowledge, no secrets.
        if self.path.startswith("/api/history"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            data = {"rows": history_search(q.get("q", [""])[0], q.get("limit", ["20"])[0])}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
            return
        # Admin-only: read the consolidated policy per instance + audit log.
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
        # Counterpart to /api/secret/<name>, but for MCP: only the guest itself,
        # only its own servers, and secrets only as far as the policy allows it.
        # This way MCP_CONFIG no longer has to live in the instance.
        # katfs file access for guests: only the own instance, only the share
        # assigned to it. The node itself is loopback-only since the fix, so the
        # only path for guests leads through here — with an enforced share, no
        # enumeration, no foreign access.
        if self.path.split("?", 1)[0] in ("/api/katfs/ls", "/api/katfs/read"):
            inst = instance_by_ip(self.client_address[0])
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"guests only"}'); return
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
                self.wfile.write(b'{"error":"guests only"}')
                return
            names = [n for n in (inst.get("config", {}).get("MCP_SERVERS", "") or "").split(",") if n]
            allowed = allowed_secret_keys(inst)
            # allowed=set(): since the MCP hub the server processes run on the
            # host — the guest only needs the NAMES anymore. Secrets stay as
            # ${PLACEHOLDER} and no longer leave the manager.
            # (The local fallback in the guest thus starts without credentials
            # and fails at the target — visible in the log, not silently.)
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
        # Admin-only (guests blocked by source IP): folder browser + katfs status.
        if self.path.startswith("/api/katfs/zip"):
            # "Download everything": the current folder of a share as a ZIP.
            # Admin-only like the browser below it.
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
            # File browser in the Sharing tab. Admin-only (guests blocked by
            # source IP); the node addresses the currently connected share.
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
                # Images/text should be viewable in the new tab, otherwise download.
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
        if self.path.split("?", 1)[0] == "/api/resources":
            body = json.dumps({"resources": resource_stats()}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.split("?", 1)[0] == "/api/iroh":
            body = json.dumps(irohgw_status()).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.split("?", 1)[0] == "/api/plugins":
            body = json.dumps({"plugins": list_plugins()}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
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
        # Admin-only (guests blocked by source IP): secret names + policy for the UI.
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
        # Admin UI only: guests have no business here. /api/settings served the
        # API keys in plain text until just now — bypassing broker and policy.
        _p = self.path.split("?", 1)[0]
        # Rest der Admin-Liste; die migrierten Pfade tragen ihr admin=True
        # inzwischen an der Route selbst (siehe ROUTER oben).
        if _p in ("/api/chats", "/api/notifications"):
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
        if _p == "/api/chats":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            if "since" in q or "wait" in q:
                try:
                    since = int(q.get("since", ["0"])[0] or 0)
                    wait = min(30.0, max(0.0, float(q.get("wait", ["25"])[0] or 0)))
                except ValueError:
                    since, wait = 0, 0.0
                rev, chats = wait_chats(since, wait)
                body = json.dumps({"rev": rev, "chats": chats,
                                   "tombstones": load_tombstones()}).encode()
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
        elif self.path.startswith("/api/memory/"):
            # Path segments are URL-decoded: keys may carry spaces/umlauts, and
            # a slash inside a key stays one key (everything after the instance).
            seg = [urllib.parse.unquote(x)
                   for x in self.path[len("/api/memory/"):].split("/")]
            if len(seg) > 2:
                seg = [seg[0], "/".join(seg[1:])]
            # A guest may only read its OWN memory — the name then comes from the
            # source IP, not from the path. Only the host (admin, not an instance)
            # may specify a foreign name in the path.
            guest = instance_by_ip(self.client_address[0])
            inst = guest["name"] if guest else seg[0]
            if len(seg) >= 2 and seg[1]:
                body = json.dumps({"value": mem_recall(inst, seg[1])}, ensure_ascii=False).encode()
            else:
                body = json.dumps(mem_recall(inst), ensure_ascii=False).encode()
            ct = "application/json"
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
            # Never cache: a stale manager page after an update produces ghost
            # errors (old JS logic against a new API).
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _llm_proxy(self, _pp):
        """POST /api/llm/<backend>/chat/completions — credential injection
        gateway. The body goes unchanged to the router; the manager injects the
        Authorization header from the settings so the key never reaches the VM.
        Streams (SSE) are passed through line by line, upstream errors
        transparently (status + body). Deliberately NO logs of key or body —
        those are exactly what should not leave the host or linger anywhere."""
        parts = _pp.strip("/").split("/")      # api/llm/<backend>/chat/completions
        backend = parts[2] if len(parts) > 2 else ""
        if backend not in LLM_PROXY_UPSTREAMS or parts[3:] != ["chat", "completions"]:
            out = b'{"error":"unknown llm proxy path"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
        ok_g, why = _guard_check(instance_by_ip(self.client_address[0]))
        if not ok_g:
            out = json.dumps({"error": {"message": f"guardrail: {why}", "code": 429}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers(); self.wfile.write(out); return
        url, keyname = LLM_PROXY_UPSTREAMS[backend]
        st = load_settings()
        # Self-hosted OrcaRouter-Lite: the shared base URL applies to the proxy
        # too — otherwise the detour would suddenly run against the cloud while
        # direct mode talks to the own server.
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
            "HTTP-Referer": f"https://{PUBLIC_HOST}",
            "X-Title": "kat56-agent"})
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            # Pass upstream errors through 1:1: the agent has its own retry
            # logic for 429/5xx and shows 4xx bodies as an error message.
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
                # Write SSE on line by line and flush — full buffering would kill
                # the token streaming in the agent. readline() blocks only until
                # the next event line, never until the end of the stream. Without
                # Content-Length the response ends with the connection close
                # (HTTP/1.0), urllib in the guest reads until EOF.
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
                    pass               # client gone -> upstream closes via with
            else:
                data = r.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

    def _do_POST(self):
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
        hit = ROUTER.resolve("POST", _pp)
        if hit is not None:
            fn, admin_only = hit
            if admin_only and instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            out = fn(self)
            if out is None:
                return
            body, ct = out
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if _pp.startswith("/api/llm/"):
            # LLM key injection: its own branch right up front, because the
            # response may be streamed and doesn't fit the JSON schema of the
            # other routes.
            return self._llm_proxy(_pp)
        if _pp == "/api/iroh":
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            act = b.get("action")
            if act == "add":
                ok, msg = irohgw_allow_add(b.get("id", ""), b.get("label", ""))
            elif act == "remove":
                ok, msg = irohgw_allow_remove(b.get("id", ""))
            else:
                ok, msg = False, "unknown action"
            out = json.dumps({"ok": ok, "msg": msg, **irohgw_status()}).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        # Voice: the service listens on loopback and is not reachable from
        # outside. The manager is the only door — it already knows the caller
        # (basic auth or source IP) and passes raw audio or WAV through unchanged
        # instead of repackaging it.
        if _pp in ("/api/stt", "/api/tts"):
            ln = int(self.headers.get("Content-Length", 0) or 0)
            payload = self.rfile.read(ln) if ln else b""
            if _pp == "/api/tts":
                # Mix in voice/speed from the shared settings — app and web send
                # only {"text"}; explicit client values win.
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
            # An agent's usage report. Like /api/audit, only for real guests:
            # the instance comes from the source IP, not from the body —
            # otherwise a VM could forge another one's usage.
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is not None:
                usage_add(inst["name"], body.get("model", ""),
                          body.get("prompt_tokens"), body.get("completion_tokens"),
                          body.get("cost"))
            self.send_response(204); self.end_headers(); return
        if self.path == "/api/mcp":
            # A guest's MCP call -> hub. Real guests only: the instance comes
            # from the source IP; the admin can pass "instance" in the body for
            # testing.
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
            # Semantic search in long-term memory. Like /api/memory, the instance
            # is the guest's (source IP); the admin may specify "instance" in the
            # body (for testing).
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
            # Mission write access: every persistent agent (its own missions)
            # or admin. Ephemeral VMs are excluded — they are deleted after the
            # task, their mission would dangle without an owner.
            g = instance_by_ip(self.client_address[0])
            if g is not None and g["name"].startswith(("task-", "sub-")):
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"ephemeral VMs may not own missions"}'); return
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
                                             note=b.get("note", ""),
                                             target=b.get("target", ""))}
            else:
                out = {"msg": mission_finish(inst, b.get("id", ""),
                                             summary=b.get("summary", ""),
                                             failed=bool(b.get("failed")))}
            body = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.startswith("/api/mission-admin"):
            # UI: pause/resume/abort — admin only (guests blocked).
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            ln = int(self.headers.get("Content-Length", 0) or 0)
            b = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            # Without an instance mission_admin resolves the owner itself —
            # web UI and app only know the mission id, and the owner can be any
            # agent since missions are no longer orchestrator-only.
            out = {"msg": mission_admin(b.get("instance", ""),
                                        b.get("id", ""), b.get("action", ""))}
            body = json.dumps(out).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.split("?", 1)[0].startswith("/api/plugins"):
            # Manage tool plugins (upload/boilerplate/delete): admin only.
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            pp = self.path.split("?", 1)[0]
            ln = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(ln) if ln else b""
            if ln > PLUGIN_MAX_BYTES:
                out = json.dumps({"error": "file too large (max 5 MB)"}).encode()
            else:
                try:
                    b = json.loads(raw_body or b"{}")
                except ValueError:
                    b = {}
                parts = pp.strip("/").split("/")
                if len(parts) == 4 and parts[3] == "delete":
                    ok = plugin_delete(parts[2])
                    out = json.dumps({"msg": "deleted" if ok else "not found"}).encode()
                elif len(parts) == 4 and parts[3] == "pin":
                    h = plugin_pin(parts[2])
                    out = json.dumps({"msg": "approved" if h else "not found", "sha": (h or "")[:12]}).encode()
                elif pp == "/api/plugins/new":
                    err = plugin_write_py(b.get("name", ""), PLUGIN_BOILERPLATE)
                    out = json.dumps({"error": err} if err else {"msg": "created"}).encode()
                else:
                    kind = b.get("kind"); name = b.get("name", "")
                    if kind == "zip":
                        try:
                            raw = base64.b64decode(b.get("data_b64", ""))
                        except Exception:
                            raw = b""
                        err = plugin_write_zip(name, raw)
                    else:
                        code = b.get("code")
                        if code is None and b.get("data_b64"):
                            code = base64.b64decode(b.get("data_b64", "")).decode("utf-8", "replace")
                        err = plugin_write_py(name, code or PLUGIN_BOILERPLATE)
                    out = json.dumps({"error": err} if err else {"msg": "saved"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/prompts":
            # Management of prompt templates: admin only.
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
            # The agent sends a push notification to app + web. Instance by IP.
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
            # App/web acknowledge read notifications (admin, not guest).
            if instance_by_ip(self.client_address[0]) is not None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"forbidden"}'); return
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if body.get("clear"):
                n = notif_clear()
            else:
                n = notif_mark_read(body.get("id"), bool(body.get("all")))
            out = json.dumps({"marked": n}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out); return
        if self.path == "/api/hitl":
            # The agent asks for approval of a risky tool. Instance by IP.
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
            # Signal send for agents. The recipient is checked against
            # ALLOWED_SENDERS, the bot number comes from the settings — the VM
            # knows neither.
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
            if inst is not None:   # only log real guests, silently discard otherwise
                try:
                    audit_append(inst["name"], body.get("tool", ""),
                                 body.get("target", ""), body.get("ok", True))
                except Exception:
                    pass
            self.send_response(204); self.end_headers(); return
        # Queue a task from within a VM (create_task tool). The caller is
        # identified by source IP; it chooses the TARGET (capable instance or
        # 'ephemeral'), but not its own identity. Ephemeral children
        # (task-*/sub-*) may NOT create tasks themselves (no runaway).
        if self.path == "/api/task":
            inst = instance_by_ip(self.client_address[0])
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"guests only"}'); return
            if inst["name"].startswith(("task-", "sub-")):
                out = {"error": "ephemeral VMs may not create tasks"}
            else:
                target = (body.get("target") or "ephemeral").strip()
                message = str(body.get("message", "")).strip()
                schedule = str(body.get("schedule", "")).strip()
                wait = bool(body.get("wait"))
                if not message:
                    out = {"error": "message missing"}
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
        # Signal turn into the shared chat history (app+web). Guests only, the
        # instance comes from the source IP — the guest doesn't choose it.
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
                    orchestrator_ping()   # Signal message -> orchestrator immediately
                except Exception:
                    pass
            self.send_response(204); self.end_headers(); return
        if self.path.split("?", 1)[0] in ("/api/katfs/write", "/api/katfs/delete"):
            inst = instance_by_ip(self.client_address[0])
            if inst is None:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b'{"error":"guests only"}'); return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            path = q.get("path", [""])[0]
            share = katfs_share_for(inst)
            ln = int(self.headers.get("Content-Length", 0) or 0)
            if self.path.split("?", 1)[0].endswith("/write"):
                if ln > KATFS_MAX_WRITE:
                    self.send_response(413); self.send_header("Content-Type", "application/json")
                    self.end_headers(); self.wfile.write(b'{"error":"too large"}'); return
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
                # {"chat": "<id>", "on": true} — guests have no business here,
                # otherwise a VM could switch off its own filter.
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
                    orchestrator_ping()   # new app/web message -> orchestrator immediately
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
                # Additionally store the same thing semantically. If the embedder
                # fails, the flat memory above stays written anyway.
                sem = sem_store(target, value, key)
                msg += " (+semantic)" if sem else " (semantic off)"
            elif parts == ["api", "create"]:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
                cfg = body.get("config", {}) or {}
                mcps = [str(m) for m in (body.get("mcps") or []) if m]
                if mcps:
                    cfg["MCP_SERVERS"] = ",".join(mcps)
                # Tool allowlist: only set it if it's a real subset (all
                # selected -> omit = all). Drop unknown names.
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
                    # Set/delete a single config key (admin; secrets stay out —
                    # those only go through the broker).
                    ln = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(ln) or b"{}")
                    key = str(body.get("key", "")).strip()
                    val = body.get("value", "")
                    inst2 = next((i for i in load_instances() if i["name"] == name), None)
                    if not inst2:
                        msg = "unknown"
                    elif not re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", key) or key in NEVER_PERSIST:
                        msg = f"error: key '{key}' not allowed"
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
                    # Not in GUEST_POST_PATHS — guest VMs never reach here
                    # (the allowlist at the start of do_POST blocks them with 403).
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
    """MCP_CONFIG contained the substituted secrets in plain text. The server
    names are its keys, so they can be lifted losslessly into MCP_SERVERS; the
    secrets needed for that are granted to the instance specifically, so nothing
    that worked before stops working."""
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
            names = []          # empty remnant from old setups — just clean up
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
    """One-time cleanup of the legacy state: instance JSONs that still carry an
    API key lose it here. Since the rework the agent fetches it via the broker;
    a key in the instance file would only be a copy that travels onto every
    config disk. Runs as root, who owns the files."""
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
