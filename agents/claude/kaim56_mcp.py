#!/usr/bin/env python3
"""MCP server for the kAIm56 platform APIs — runs in the Claude guest.

Claude Code has a native tool mechanism (MCP over stdio); curl recipes in the
CLAUDE.md were the stopgap. This server turns the manager APIs into real,
typed tools: memory_store, memory_recall, web_search, list_skills, load_skill,
notify. The manager is the host gateway (.1 of the /30) on :8700 — guests only
ever read/write their own memory, the manager decides by source IP.

Stdlib only, newline-delimited JSON-RPC as the MCP stdio transport demands.
"""
import json
import socket
import sys
import urllib.parse
import urllib.request


def manager_base():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return f"http://{ip.rsplit('.', 1)[0]}.1:8700"


BASE = manager_base()


def _get(path):
    return urllib.request.urlopen(BASE + path, timeout=30).read().decode("utf-8", "replace")


def _post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def t_memory_store(key, value):
    return _post("/api/memory/self", {"key": key, "value": value})


def t_memory_recall(key=None):
    tail = f"/{urllib.parse.quote(str(key), safe='')}" if key else ""
    return _get("/api/memory/self" + tail)


def t_web_search(query, count=5):
    q = urllib.parse.urlencode({"q": query, "count": count})
    return json.loads(_get("/api/websearch?" + q)).get("result", "")


def t_list_skills():
    arr = json.loads(_get("/api/skills?meta=1"))
    return "\n".join(f"- {s.get('name')}: {s.get('description', '')}" for s in arr)


def t_load_skill(name):
    return _get(f"/api/skills/{urllib.parse.quote(str(name), safe='')}")


def t_notify(title, message=""):
    return _post("/api/notify", {"title": title, "message": message})


TOOLS = {
    "memory_store": (t_memory_store,
        "Store a value in the platform's long-term memory (survives /reset and VM restarts).",
        {"type": "object",
         "properties": {"key": {"type": "string", "description": "short key, reused to update"},
                        "value": {"type": "string", "description": "complete, self-contained statement"}},
         "required": ["key", "value"]}),
    "memory_recall": (t_memory_recall,
        "Read from long-term memory. Without a key: all entries of this instance.",
        {"type": "object",
         "properties": {"key": {"type": "string", "description": "optional: one key"}},
         "required": []}),
    "web_search": (t_web_search,
        "Web search (Brave Search API via the manager; DDG/Bing fallback). Title + URL + snippet.",
        {"type": "object",
         "properties": {"query": {"type": "string"},
                        "count": {"type": "integer", "description": "1-10, default 5"}},
         "required": ["query"]}),
    "list_skills": (t_list_skills,
        "List the platform's expert skills (name + description).",
        {"type": "object", "properties": {}, "required": []}),
    "load_skill": (t_load_skill,
        "Load one expert skill document into context.",
        {"type": "object",
         "properties": {"name": {"type": "string", "description": "name from list_skills"}},
         "required": ["name"]}),
    "notify": (t_notify,
        "Push a notification to the user's devices (app + web bell).",
        {"type": "object",
         "properties": {"title": {"type": "string"}, "message": {"type": "string"}},
         "required": ["title"]}),
}


def reply(mid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        out["error"] = {"code": -32000, "message": str(error)}
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        mid = msg.get("id")
        if method == "initialize":
            reply(mid, {"protocolVersion": msg.get("params", {}).get(
                            "protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "kaim56", "version": "1.0"}})
        elif method == "tools/list":
            reply(mid, {"tools": [
                {"name": n, "description": d, "inputSchema": schema}
                for n, (_f, d, schema) in TOOLS.items()]})
        elif method == "tools/call":
            p = msg.get("params", {})
            name = p.get("name", "")
            args = p.get("arguments") or {}
            if name not in TOOLS:
                reply(mid, error=f"unknown tool: {name}")
                continue
            try:
                out = TOOLS[name][0](**args)
                reply(mid, {"content": [{"type": "text", "text": str(out)}]})
            except Exception as e:
                reply(mid, {"content": [{"type": "text", "text": f"Error: {e!r}"}],
                            "isError": True})
        elif mid is not None:
            reply(mid, {})           # answer unknown requests with an empty result
        # Notifications (no id) are ignored.


if __name__ == "__main__":
    main()
