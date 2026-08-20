# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 Ulrich Neidel
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""MCP-Server-Katalog + Hub-Aufrufe (per-Instanz auswaehlbare MCP-Server).
Teil des mgr-Pakets. load_instances wird per configure() injiziert.
"""
import json
import os
import re
import urllib.request

MCP_CATALOG_FILE = None
MCP_HUB = "http://127.0.0.1:" + os.environ.get("MCP_HUB_PORT", "8771")
load_instances = lambda: []


def configure(base, load_instances_fn=None):
    global MCP_CATALOG_FILE, load_instances
    MCP_CATALOG_FILE = os.path.join(base, "mcp-catalog.json")
    if load_instances_fn:
        load_instances = load_instances_fn


# ---- MCP-Katalog (auswählbare MCP-Server je Instanz) -----------------------
_DEFAULT_MCPS = [
    {"name": "homeassistant",
     "description": "Home Assistant (Lesen/Assist via MCP)",
     "command": "mcp-remote",
     "args": ["http://10.0.0.10:8123/mcp_server/sse", "--header",
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


