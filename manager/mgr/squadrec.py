# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad records and the squad archive: stable IDs and every conversation on disk.

The record (squadrec.json in the manager dir, root-only) owns what outlives a
project: the squad ID (16 x [a-z0-9] from `secrets`, assigned once, never
changed), the member IDs (``<squadId>-<16 x [a-z0-9]>``, one per squad
membership), the squad's projects (a list: a squad will work on several
repos; today the kickoff's one) and its folder. The squad's settings stay
where they were — in the project (projects.json, key ``squad``); the record
snapshots them into squad.json.

The archive, under SQUADS_DIR (site setting; default ~<manager owner>/squads)::

  <squadId>__<name>/
    squad.json                                    the record + settings snapshot
    projects/<project>/repo/                      the kickoff's clone
    conversations/channel/YYYY-MM-DD.jsonl        every channel message
    conversations/members/<memberId>__<instance>/YYYY-MM-DD.jsonl   every wake turn
    conversations/hitl/YYYY-MM-DD.jsonl           every human-in-the-loop item + decision (mgr/hitlq.py)
    proposals/<proposalId>.json                   proposal, edits, decision, steps
    tickets.jsonl                                 every ticket create / update

Append-only JSONL, one event per line, written when it happens; squad.db and
squadgate.json stay the fast index. Day files by local date. Owned by the
owner of SQUADS_DIR, dirs 0750, files 0640. Known secret values and token
patterns are replaced, every text field is capped at FIELD_MAX. A failed
write is logged and never breaks the squad.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import pwd
import re
import secrets
import shutil
import string
import threading
import time

from mgr import gateway as _gateway
from mgr import instances as _instances
from mgr import llmproxy as _llmproxy
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import secrets as _secrets
from mgr import settings as _settings

REC_FILE = os.path.join(_paths.BASE, "squadrec.json")
ID_LEN = 16
ID_CHARS = string.ascii_lowercase + string.digits
ID_RE = re.compile(r"^[a-z0-9]{16}$")
MEMBER_ID_RE = re.compile(r"^[a-z0-9]{16}-[a-z0-9]{16}$")
FIELD_MAX = 64 * 1024
_AUTH = re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*\S+(\s+\S+)?|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
                   r"|\b(basic)\s+[A-Za-z0-9+/=]{12,}|(x-access-token|oauth2):[^@\s]+")
_lock = threading.RLock()


# ---- ids, the root -----------------------------------------------------------
def new_id(taken=()):
    while True:
        s = "".join(secrets.choice(ID_CHARS) for _ in range(ID_LEN))
        if s not in taken:
            return s


def squads_dir():
    d = str(_settings.SITE.get("SQUADS_DIR") or "").strip()
    if d:
        return os.path.realpath(d)
    try:
        home = pwd.getpwuid(os.stat(_paths.BASE).st_uid).pw_dir
    except (KeyError, OSError):
        home = os.path.expanduser("~")
    return os.path.join(home, "squads")


def root():
    """SQUADS_DIR (made 0750, owned like its parent) -> (path, uid, gid)."""
    r = squads_dir()
    if not os.path.isdir(r):
        st = os.stat(os.path.dirname(r))
        os.makedirs(r, 0o750, exist_ok=True)
        os.chmod(r, 0o750)
        if os.geteuid() == 0:
            os.chown(r, st.st_uid, st.st_gid)
    st = os.stat(r)
    return r, st.st_uid, st.st_gid


def _own(target, mode):
    """chmod + chown (a path or an fd) to the root's owner; never through a planted symlink."""
    if not isinstance(target, int) and os.path.islink(target):
        raise OSError(f"{target} is a symlink")
    os.chmod(target, mode)
    if os.geteuid() == 0:
        _r, uid, gid = root()
        os.chown(target, uid, gid)


def mkdirs(path):
    """Every missing dir from SQUADS_DIR down to `path`, 0750, the root's owner."""
    r = root()[0]
    rel = os.path.relpath(path, r)
    if rel.startswith(".."):
        raise ValueError(f"{path} is outside {r}")
    cur = r
    for part in ([] if rel == "." else rel.split(os.sep)):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise OSError(f"{cur} is a symlink")
        if not os.path.isdir(cur):
            os.mkdir(cur, 0o750)
            _own(cur, 0o750)
    return path


# ---- the records -------------------------------------------------------------
def _load():
    try:
        with open(REC_FILE) as fh:
            d = json.load(fh)
        return [r for r in d if isinstance(r, dict) and ID_RE.match(str(r.get("id")))] if isinstance(d, list) else []
    except (FileNotFoundError, ValueError):
        return []
    except OSError as e:          # root-only; only a non-root caller (a test) lands here
        print(f"[quiet] squadrec.json unreadable: {e!r}", flush=True)
        return []


def _save(recs):
    tmp = REC_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(recs, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, REC_FILE)


def records():
    return _load()


def _active(rec):
    return [p for p in rec.get("projects") or [] if not p.get("removed")]


def of_project(project):
    """The squad record a project belongs to (None: none)."""
    return next((r for r in _load() if any(p["name"] == project for p in _active(r))), None)


def name_taken(name):
    return any(r["name"] == name and _active(r) for r in _load())


def folder(rec):
    return os.path.join(squads_dir(), rec["folder"])


def repo_dir(rec, project):
    return os.path.join(folder(rec), "projects", project, "repo")


def member_id(rec, inst):
    return next((m["member_id"] for m in (rec or {}).get("members") or [] if m["instance"] == inst), "")


def _put(rec):
    recs = [r for r in _load() if r["id"] != rec["id"]]
    _save(recs + [rec])


def create(name, project, repo=""):
    """A new squad (the kickoff): ID, folder, squad.json -> the record."""
    with _lock:
        taken = {r["id"] for r in _load()}
        sid = new_id(taken)
        now = int(time.time())
        rec = {"id": sid, "name": name, "created": now, "folder": f"{sid}__{name}",
               "projects": [{"name": project, "repo": repo, "added": now}], "members": [], "migrated": now}
        _put(rec)
        mkdirs(folder(rec))
        _snapshot(rec)
    return rec


def drop(sid):
    """Remove a record and its folder (a failed kickoff removes what it made)."""
    with _lock:
        rec = next((r for r in _load() if r["id"] == sid), None)
        if rec is None:
            return
        _save([r for r in _load() if r["id"] != sid])
    shutil.rmtree(folder(rec), ignore_errors=True)


def detach(project):
    """A deleted project leaves its squad; the archive stays. A squad without
    projects is closed (a new project of that name is a new squad)."""
    with _lock:
        rec = of_project(project)
        if rec is None:
            return
        now = int(time.time())
        for p in rec["projects"]:
            if p["name"] == project:
                p["removed"] = now
        if not _active(rec):
            rec["closed"] = now
        _put(rec)
    _snapshot(rec)


def set_repo(rec, project, repo, pending_move=False):
    cur = next((p for p in rec["projects"] if p["name"] == project), {})
    if cur.get("repo") == repo and bool(cur.get("move_pending")) == pending_move:
        return
    with _lock:
        for p in rec["projects"]:
            if p["name"] == project:
                p["repo"] = repo
                if pending_move:
                    p["move_pending"] = True
                else:
                    p.pop("move_pending", None)
        _put(rec)
    _snapshot(rec)


def _indexers():
    return {i["name"] for i in _instances.load_instances() if i.get("template") == "indexer"}


def ensure(proj, personas=None):
    """The squad record of a squad project (created on first use, with its
    members' IDs in sync; history exported once) -> the record."""
    personas = personas or {}
    with _lock:
        rec = of_project(proj["name"])
        new = rec is None
        if new:
            sid = new_id({r["id"] for r in _load()})
            now = int(time.time())
            rec = {"id": sid, "name": proj["name"], "created": now, "folder": f"{sid}__{proj['name']}",
                   "projects": [{"name": proj["name"], "repo": _projects.source_dir(proj), "added": now}],
                   "members": []}
        changed = new
        projs = [proj if p["name"] == proj["name"] else _projects.get(p["name"]) for p in _active(rec)]
        cur, idx = {}, _indexers()
        for p in projs:
            for n, m in ((p or {}).get("members") or {}).items():
                if n not in idx:
                    cur.setdefault(n, m.get("role") or "")
        now = int(time.time())
        have = {m["instance"]: m for m in rec["members"]}
        for n, role in sorted(cur.items()):
            m = have.get(n)
            if m is None:
                taken = {x["member_id"].split("-", 1)[1] for x in rec["members"]}
                rec["members"].append({"member_id": f"{rec['id']}-{new_id(taken)}", "instance": n, "role": role,
                                       "persona": personas.get(n, ""), "joined": now})
                changed = True
            elif m.get("role") != role or m.get("left") or (personas.get(n) and not m.get("persona")):
                m["role"] = role
                m.pop("left", None)
                m["persona"] = m.get("persona") or personas.get(n, "")
                changed = True
        for m in rec["members"]:
            if m["instance"] not in cur and not m.get("left"):
                m["left"] = now
                changed = True
        if changed:
            _put(rec)
    if new:
        mkdirs(folder(rec))
    _snapshot(rec)
    if not rec.get("migrated"):
        _export(rec)
    return rec


def ids(proj):
    """{squad_id, members: {instance: member_id}} of a project ({} without a squad)."""
    rec = of_project(proj["name"])
    if rec is None:
        return {}
    return {"squad_id": rec["id"], "members": {m["instance"]: m["member_id"] for m in rec["members"]}}


# ---- writing -------------------------------------------------------------------
def _clean(v, sec):
    if isinstance(v, str):
        for s in sec:
            v = v.replace(s, "[secret]")
        v = _AUTH.sub("[secret]", _gateway.redact_secrets(v)[0])
        if len(v) > FIELD_MAX:
            v = v[:FIELD_MAX] + f" …[truncated {len(v) - FIELD_MAX} chars]"
        return v
    if isinstance(v, dict):
        return {str(k): _clean(x, sec) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x, sec) for x in v]
    return v


def _secret_values():
    try:
        vals = list(_secrets.secret_store().values())
    except Exception:
        vals = []
    return sorted({str(v) for v in vals if len(str(v)) >= 8}, key=len, reverse=True)


def _file(path, mode=0o640):
    """Open for append, created with `mode` and the root's owner."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, mode)
    if os.fstat(fd).st_size == 0:
        _own(fd, mode)
    return os.fdopen(fd, "a", encoding="utf-8")


def _append(path, event):
    line = json.dumps(_clean(event, _secret_values()), ensure_ascii=False)
    with _lock:
        mkdirs(os.path.dirname(path))
        with _file(path) as fh:
            fh.write(line + "\n")


def _write_json(path, obj):
    data = json.dumps(_clean(obj, _secret_values()), indent=1, ensure_ascii=False)
    with _lock:
        mkdirs(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640), "w", encoding="utf-8") as fh:
            _own(fh.fileno(), 0o640)
            fh.write(data)
        os.replace(tmp, path)


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:                      # the archive never breaks the squad
        print(f"[quiet] squad archive: {fn.__name__}: {e!r}", flush=True)


def _snapshot(rec):
    def snap():
        sett = {}
        for p in _active(rec):
            pr = _projects.get(p["name"]) or {}
            sett[p["name"]] = pr.get("squad") or {}
        doc = {k: rec.get(k) for k in ("id", "name", "created", "folder", "closed", "projects", "members")}
        doc["settings"] = sett
        path = os.path.join(folder(rec), "squad.json")
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh) == _clean(doc, []):
                    return
        except (OSError, ValueError):
            pass
        _write_json(path, doc)
    _safe(snap)


def snapshot(project):
    rec = of_project(project)
    if rec:
        _snapshot(rec)


def _channel_event(rec, m, migrated=False):
    mids = {x["instance"]: x["member_id"] for x in rec["members"]}
    ev = {"ts": m["ts"], "squad_id": rec["id"], "project": m["project"], "msg_id": m["id"], "from": m["from"],
          "from_member_id": mids.get(m["from"], ""), "to": m["to"],
          "to_member_ids": {t: mids[t] for t in m["to"] if t in mids}, "text": m["text"], "kind": m["kind"],
          "reply_to": m["reply_to"], "depth": m.get("depth"), "delivered": m.get("delivered") or [],
          "blocked": m.get("blocked") or ""}
    if migrated:
        ev["migrated"] = True
    return ev


def log_channel(project, msg):
    """One channel message (after its wakes: delivered / blocked are final)."""
    rec = of_project(project)
    if rec:
        _safe(_append, os.path.join(folder(rec), "conversations", "channel", _day(msg["ts"]) + ".jsonl"),
              _channel_event(rec, msg))


def turn_begin(task):
    """Before a squad-woken task runs -> what turn_end needs (None: no squad task)."""
    if not task.get("squad"):
        return None
    try:
        tok = _llmproxy.used_today(task.get("instance", ""))
    except Exception:
        tok = None
    return time.time(), tok


def turn_end(task, begun, ok, result):
    """A wake turn's outcome into the member's day file."""
    if not begun:
        return
    sq = task.get("squad") or {}
    rec = of_project(sq.get("project", ""))
    if rec is None:
        return
    inst = task.get("instance", "")
    mid = member_id(rec, inst)
    if not mid:
        return
    t0, tok0 = begun
    tokens = None
    if tok0 is not None:
        try:
            d = _llmproxy.used_today(inst) - tok0
            tokens = d if d >= 0 else None
        except Exception:
            pass
    now = time.time()
    ev = {"ts": int(now), "squad_id": rec["id"], "project": sq.get("project"), "member_id": mid, "instance": inst,
          "task_id": task.get("id"), "msg_id": sq.get("msg"), "depth": sq.get("depth"),
          "incoming": task.get("message", ""), "result": str(result if result is not None else ""),
          "status": "done" if ok else "error", "duration_s": round(now - t0, 1), "tokens": tokens}
    _safe(_append, os.path.join(folder(rec), "conversations", "members", f"{mid}__{inst}", _day(now) + ".jsonl"), ev)


def log_proposal(prop):
    rec = of_project(prop.get("project", ""))
    if rec and re.match(r"^[A-Za-z0-9_-]{1,64}$", str(prop.get("id"))):
        _safe(_write_json, os.path.join(folder(rec), "proposals", f"{prop['id']}.json"),
              {**prop, "squad_id": rec["id"]})


def log_ticket(project, event, ticket, by, change=None):
    rec = of_project(project)
    if rec:
        ev = {"ts": int(time.time()), "squad_id": rec["id"], "event": event, "by": by,
              "by_member_id": member_id(rec, by), "ticket": ticket}
        if change:
            ev["change"] = change
        _safe(_append, os.path.join(folder(rec), "tickets.jsonl"), ev)


def log_hitl(project, event, item):
    """A human-in-the-loop item (mgr/hitlq.py): created, decided."""
    rec = of_project(project)
    if rec:
        now = int(time.time())
        _safe(_append, os.path.join(folder(rec), "conversations", "hitl", _day(now) + ".jsonl"),
              {"ts": now, "squad_id": rec["id"], "event": event, "member_id": member_id(rec, item.get("instance", "")),
               "item": item})


# ---- export + migration --------------------------------------------------------
def _export(rec):
    """Once per record: what squad.db and squadgate.json hold already."""
    from mgr import squadgate as _squadgate     # late: both import this module
    from mgr import squads as _squads

    def run():
        for p in _active(rec):
            for m in _squads.read(p["name"], None, 0, limit=10 ** 6):
                _append(os.path.join(folder(rec), "conversations", "channel", _day(m["ts"]) + ".jsonl"),
                        _channel_event(rec, m, migrated=True))
            for prop in _squadgate.proposals(p["name"]):
                log_proposal(prop)
            for t in _squadgate.tickets(p["name"]):
                _append(os.path.join(folder(rec), "tickets.jsonl"),
                        {"ts": t.get("updated") or int(time.time()), "squad_id": rec["id"], "event": "migrated",
                         "by": t.get("by", ""), "by_member_id": "", "ticket": t})
        with _lock:
            cur = next((r for r in _load() if r["id"] == rec["id"]), rec)
            cur["migrated"] = rec["migrated"] = int(time.time())
            _put(cur)
    _safe(run)


def _mounted(path):
    """A running member VM reaches `path` (or it is a mount point's source)."""
    real = os.path.realpath(path)
    for p in _projects.load():
        src = _projects.source_dir(p)
        if src and (os.path.realpath(src) + "/").startswith(real + "/"):
            for n in p.get("members") or {}:
                inst = next((i for i in _instances.load_instances() if i["name"] == n), None)
                if inst and _instances.is_running(inst):
                    return True
    try:
        with open("/proc/self/mountinfo") as fh:
            return any(real in line.split() for line in fh)
    except OSError:
        return False


def _move(rec, proj):
    """A pre-ID kickoff clone SQUADS_DIR/<name> -> <folder>/projects/<name>/repo,
    only when no running member reaches it; else the path is recorded."""
    name = proj["name"]
    src = (proj.get("source") or {}).get("path", "")
    legacy = os.path.join(squads_dir(), name)
    target = repo_dir(rec, name)
    if (proj.get("source") or {}).get("type") != "host" or not src:
        return ""
    if os.path.realpath(src) != os.path.realpath(legacy) or not os.path.isdir(os.path.join(legacy, ".git")):
        set_repo(rec, name, src)
        return ""
    if os.path.lexists(target):
        set_repo(rec, name, src, pending_move=True)
        return f"{name}: {target} exists — the clone stays at {src}"
    if _mounted(legacy):
        set_repo(rec, name, src, pending_move=True)
        return f"{name}: a running member uses {src} — moved at a later start"
    mkdirs(os.path.dirname(target))
    os.rename(legacy, target)
    r = _projects.upsert({**proj, "source": {**proj["source"], "path": target}})
    if "error" in r:
        os.rename(target, legacy)
        set_repo(rec, name, src, pending_move=True)
        return f"{name}: project update failed ({r['error']}) — the clone stays at {src}"
    set_repo(rec, name, target)
    return f"{name}: clone moved to {target}"


def migrate():
    """At start, idempotent: every squad-enabled project gets its record, IDs,
    folder and exported history; a pre-ID clone is moved when nothing runs on it."""
    out = []
    for p in _projects.load():
        if not ((p.get("squad") or {}).get("enabled") or of_project(p["name"])):
            continue
        try:
            rec = ensure(p)
            msg = _move(rec, p)
        except Exception as e:
            msg = f"{p['name']}: {e!r}"
        if msg:
            out.append(msg)
            print(f"[migrate] squad {msg}", flush=True)
    return out
