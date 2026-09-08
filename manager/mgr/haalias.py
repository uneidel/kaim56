# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Home-Assistant-Alias-Lernen: der Manager haengt einen gesprochenen Namen
als Alias an eine HA-Entitaet, damit derselbe Hoerfehler beim naechsten Mal
NATIV matcht (STT hoert 'Decke' als 'denke').

Warum HIER und nicht im Gast: der HA-Token bleibt auf dem Host (wie jeder
Secret). Der Gast meldet nur (gesprochener Name, entity_id); der Manager
oeffnet den WebSocket, liest die bestehenden Aliase und ergaenzt den neuen.
Part of the mgr package: HA-Host aus dem MCP-Katalog, Token aus dem Secret-
Store — beides via configure() injiziert.
"""
import base64
import difflib
import json
import os
import re
import socket
import struct
import urllib.request

# via configure(): Callables, damit dieses Modul manager.py nicht importiert.
ha_ws_target = None      # () -> (host, port) | None  (aus dem MCP-Katalog)
ha_token = None          # () -> str | None           (aus dem Secret-Store)


def configure(ha_ws_target_fn, ha_token_fn) -> None:
    global ha_ws_target, ha_token
    ha_ws_target = ha_ws_target_fn
    ha_token = ha_token_fn


def _base_url():
    tgt = ha_ws_target() if ha_ws_target else None
    if not tgt:
        return None
    return f"http://{tgt[0]}:{tgt[1]}"


def _rest(path, payload=None):
    url = _base_url()
    tok = ha_token() if ha_token else None
    if not url or not tok:
        raise RuntimeError("Home Assistant not configured")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def _ws_send(sock, obj):
    d = json.dumps(obj).encode()
    ln = len(d)
    hdr = bytearray([0x81])                       # FIN + text
    if ln < 126:
        hdr.append(0x80 | ln)
    elif ln < 65536:
        hdr += bytes([0x80 | 126]) + struct.pack(">H", ln)
    else:
        hdr += bytes([0x80 | 127]) + struct.pack(">Q", ln)
    m = os.urandom(4)                             # Client-Frames MUESSEN maskiert sein
    hdr += m
    sock.sendall(bytes(hdr) + bytes(c ^ m[i % 4] for i, c in enumerate(d)))


def _ws_recv_json(sock, _buf):
    def need(n):
        while len(_buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise RuntimeError("websocket closed")
            _buf.extend(chunk)
    need(2)
    ln = _buf[1] & 0x7f
    i = 2
    if ln == 126:
        need(4); ln = struct.unpack(">H", _buf[2:4])[0]; i = 4
    elif ln == 127:
        need(10); ln = struct.unpack(">Q", _buf[2:10])[0]; i = 10
    need(i + ln)
    payload = bytes(_buf[i:i + ln])
    del _buf[:i + ln]
    return json.loads(payload)


def _ws_session():
    """Open + authenticate an HA WebSocket. Returns (sock, buf) or raises."""
    tgt = ha_ws_target() if ha_ws_target else None
    tok = ha_token() if ha_token else None
    if not tgt or not tok:
        raise RuntimeError("Home Assistant not configured")
    host, port = tgt
    raw = socket.create_connection((host, port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    raw.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {host}:{port}\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    hb = b""
    while b"\r\n\r\n" not in hb:
        hb += raw.recv(4096)
    buf = bytearray(hb.split(b"\r\n\r\n", 1)[1])
    if _ws_recv_json(raw, buf).get("type") != "auth_required":
        raw.close(); raise RuntimeError("unexpected HA handshake")
    _ws_send(raw, {"type": "auth", "access_token": tok})
    if _ws_recv_json(raw, buf).get("type") != "auth_ok":
        raw.close(); raise RuntimeError("HA auth failed (token?)")
    return raw, buf


def _entity_index():
    """friendly-name + aliases per switchable entity, plus area names. Used to
    match a spoken phrase against what HA actually has."""
    raw, buf = _ws_session()
    try:
        def call(mid, payload):
            _ws_send(raw, {**payload, "id": mid})
            while True:
                r = _ws_recv_json(raw, buf)
                if r.get("id") == mid:
                    return r
        ents = call(1, {"type": "config/entity_registry/list"})["result"]
        areas = call(2, {"type": "config/area_registry/list"})["result"]
        devs = {d["id"]: d.get("area_id")
                for d in call(3, {"type": "config/device_registry/list"})["result"]}
    finally:
        raw.close()
    states = {s["entity_id"]: s.get("attributes", {}).get("friendly_name", "")
              for s in _rest("/api/states")}
    CTRL = ("light", "switch", "fan", "cover", "lock", "climate", "media_player",
            "input_boolean", "scene", "script")
    items = []
    for e in ents:
        eid = e["entity_id"]
        if eid.split(".")[0] not in CTRL:
            continue
        names = [states.get(eid) or e.get("name") or ""] + list(e.get("aliases") or [])
        aid = e.get("area_id") or devs.get(e.get("device_id"))
        items.append({"entity_id": eid, "names": [n for n in names if n],
                      "area_id": aid})
    return {"entities": items,
            "areas": {a["area_id"]: a["name"] for a in areas}}


def control(spoken, action):
    """Turn a spoken target on/off, matching server-side (deterministic, no
    LLM in the loop). Order: exact name/alias, then area, then fuzzy. On a
    FUZZY entity hit the spoken phrase is learned as an alias, so the same
    mishearing matches instantly next time — this is the auto-learning."""
    spoken = (spoken or "").strip()
    action = (action or "").strip().lower()
    if action not in ("on", "off"):
        return "error: action must be 'on' or 'off'"
    if not spoken:
        return "error: no target given"
    try:
        idx = _entity_index()
    except Exception as e:
        return f"error: {e!r}"
    want = _norm(spoken)
    svc = "turn_on" if action == "on" else "turn_off"

    def switch(eid):
        _rest(f"/api/services/homeassistant/{svc}", {"entity_id": eid})

    # 1) exact name or alias -> that entity
    for it in idx["entities"]:
        if any(_norm(n) == want for n in it["names"]):
            switch(it["entity_id"])
            return f"{action}: {it['names'][0]} ({it['entity_id']})"

    # best fuzzy single entity (computed now, used at 2 and 4)
    best, best_it, best_name = 0.0, None, ""
    for it in idx["entities"]:
        for n in it["names"]:
            r = difflib.SequenceMatcher(None, want, _norm(n)).ratio()
            if r > best:
                best, best_it, best_name = r, it, n

    # area (group) candidate: area name present AND a group cue ("licht"/"lampe"/"alle")
    cue = any(w in want for w in ("licht", "lampe", "lampen", "alle", "lichter"))
    area_hit = None
    for aid, aname in idx["areas"].items():
        na = _norm(aname)
        if na and na in want and cue:
            eids = [it["entity_id"] for it in idx["entities"]
                    if it["area_id"] == aid and it["entity_id"].startswith("light.")]
            if eids:
                area_hit = (aname, eids)
                break

    # 2) a NEAR-EXACT single match beats the area — "Gartenhaus denke rechts"
    #    names one lamp, even though it contains the area word. A merely good
    #    fuzzy hit does not: "Gartenhauslicht" scored 0.76 against the relay
    #    "gartenhaus_switch L1" and switched the socket instead of the lights.
    if best_it and best >= 0.72 and not (area_hit and best < 0.9):
        switch(best_it["entity_id"])
        learned = learn_alias(spoken, best_it["entity_id"])
        return (f"{action}: {best_name} ({best_it['entity_id']}) "
                f"[fuzzy {best:.2f}] — {learned}")

    # 3) area (group)
    if area_hit:
        aname, eids = area_hit
        switch(eids)
        return f"{action}: {len(eids)} lights in area '{aname}'"

    # 4) weaker fuzzy single entity as a last resort; learn the alias
    if best_it and best >= 0.6:
        switch(best_it["entity_id"])
        learned = learn_alias(spoken, best_it["entity_id"])
        return (f"{action}: {best_name} ({best_it['entity_id']}) "
                f"[fuzzy {best:.2f}] — {learned}")
    return f"error: no target matched '{spoken}' (best {best:.2f})"


def learn_alias(spoken, entity_id):
    """Haenge `spoken` als Alias an `entity_id` (idempotent). Gibt eine kurze
    Statuszeile zurueck — nie eine Exception nach aussen, der Aufrufer ist ein
    Agent-Tool."""
    spoken = (spoken or "").strip()
    entity_id = (entity_id or "").strip()
    if not spoken or "." not in entity_id:
        return "error: need spoken text and a valid entity_id (e.g. light.foo)"
    tgt = ha_ws_target() if ha_ws_target else None
    tok = ha_token() if ha_token else None
    if not tgt or not tok:
        return "error: Home Assistant not configured (no MCP host or HA_TOKEN)"
    host, port = tgt
    buf = bytearray()
    try:
        raw = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        return f"error: HA unreachable: {e!r}"
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        raw.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {host}:{port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        hb = b""
        while b"\r\n\r\n" not in hb:
            hb += raw.recv(4096)
        _, rest = hb.split(b"\r\n\r\n", 1)
        buf.extend(rest)
        if _ws_recv_json(raw, buf).get("type") != "auth_required":
            return "error: unexpected HA handshake"
        _ws_send(raw, {"type": "auth", "access_token": tok})
        if _ws_recv_json(raw, buf).get("type") != "auth_ok":
            return "error: HA auth failed (token?)"

        def call(mid, payload):
            _ws_send(raw, {**payload, "id": mid})
            while True:
                r = _ws_recv_json(raw, buf)
                if r.get("id") == mid:
                    return r

        lst = call(1, {"type": "config/entity_registry/get", "entity_id": entity_id})
        if not lst.get("success"):
            return f"error: entity '{entity_id}' not found"
        aliases = list(lst["result"].get("aliases") or [])
        if any(str(a or "").lower() == spoken.lower() for a in aliases):
            return f"alias '{spoken}' already on {entity_id}"
        aliases.append(spoken)
        upd = call(2, {"type": "config/entity_registry/update",
                       "entity_id": entity_id, "aliases": aliases})
        if not upd.get("success"):
            return f"error: HA rejected update: {str(upd.get('error'))[:120]}"
        return f"learned: '{spoken}' -> {entity_id} ({len(aliases)} aliases)"
    except Exception as e:
        return f"error: {e!r}"
    finally:
        try:
            raw.close()
        except OSError:
            pass
