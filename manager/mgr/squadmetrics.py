# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad metrics: one row per squad event in history.db (table squad_events),
the backend of a later dashboard.

Kinds: turn_start, turn_end (outcome ok|failed|api_error; tokens/cost/model
from the instance's llm_usage rows inside the turn, level/escalated from its
route_log rows), message (one per channel post; outcome = the message kind),
wake_blocked (outcome = the guard's reason), ticket_status (detail from/to),
hitl_open, hitl_decided (duration_ms = the human wait), proposal (outcome
created|revised|changes_requested|approved|rejected; a decision's wait in
duration_ms), merge (an approved merge proposal), git (push|pr|issue), feedback (the operator's
notes on a message sent as one post; detail count).

Live rows come from the squad archive's writers (mgr/squadrec.py log_*: one
``ingest(stream, event)`` per archived event) and from mgr/squadgit.py (git).
``backfill()`` feeds the archive files through the same ``ingest`` — at start,
idempotent: per file the byte offset read so far is kept (squad_metrics_import),
and (squad_id, kind, ref) is unique, so nothing is counted twice. ``record``
never raises. Rows older than SQUAD_METRICS_DAYS (site setting, default 365)
are pruned by the hourly idle sweep and never imported.

Part of the mgr package: no import from manager.py.
"""
import contextlib
import csv
import io
import json
import os
import re
import sqlite3
import statistics
import time

from mgr import settings as _settings
from mgr import store as _store

COLS = ("id", "ts", "squad_id", "project", "kind", "member", "member_id", "ticket", "model", "level",
        "tokens_in", "tokens_out", "cost", "duration_ms", "outcome", "ref", "detail")
DETAIL_MAX = 1000
EVENTS_MAX = 5000
_TICKET_RE = re.compile(r"Ticket (\S{1,32}) \(id \d+\) is yours")
_made = [None]                       # the DB path the tables were made for (tests switch DBs)


def _conn():
    c = _store._hist_conn()
    if _made[0] != _store.HISTORY_DB:
        c.execute("""CREATE TABLE IF NOT EXISTS squad_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, squad_id TEXT, project TEXT, kind TEXT,
            member TEXT, member_id TEXT, ticket TEXT, model TEXT, level INTEGER, tokens_in INTEGER,
            tokens_out INTEGER, cost REAL, duration_ms INTEGER, outcome TEXT, ref TEXT, detail TEXT)""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_squad_ev ON squad_events(squad_id, kind, ref)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_squad_ev_ts ON squad_events(squad_id, ts)")
        c.execute("CREATE TABLE IF NOT EXISTS squad_metrics_import(path TEXT PRIMARY KEY, offset INTEGER)")
        _made[0] = _store.HISTORY_DB
    return c


@contextlib.contextmanager
def _db():
    """The history lock + a connection that is committed and closed."""
    with _store._hist_lock:
        c = _conn()
        try:
            with c:
                yield c
        finally:
            c.close()


def days():
    try:
        return max(1, int(_settings.SITE.get("SQUAD_METRICS_DAYS") or 365))
    except (TypeError, ValueError):
        return 365


def _detail(d):
    if not d:
        return None
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= DETAIL_MAX else json.dumps({"truncated": s[:DETAIL_MAX - 40]}, ensure_ascii=False)


def record(squad_id, kind, ref, ts=None, **f):
    """One event row; a duplicate (squad_id, kind, ref) or one older than the
    retention is ignored -> True when inserted. Never raises."""
    try:
        ts = int(ts or time.time())
        if not squad_id or not ref or ts < time.time() - days() * 86400:
            return False
        row = {"ts": ts, "squad_id": str(squad_id)[:32], "kind": str(kind)[:24], "ref": str(ref)[:160]}
        for k in ("project", "member", "member_id", "ticket", "model", "outcome"):
            row[k] = str(f.get(k) or "")[:200]
        for k in ("level", "tokens_in", "tokens_out", "duration_ms"):
            row[k] = None if f.get(k) is None else int(f[k])
        row["cost"] = None if f.get("cost") is None else float(f["cost"])
        row["detail"] = _detail(f.get("detail"))
        with _db() as c:
            return c.execute(f"INSERT OR IGNORE INTO squad_events({','.join(row)}) VALUES({','.join('?' * len(row))})",
                             tuple(row.values())).rowcount == 1
    except Exception as e:                       # metrics never break the squad
        print(f"[quiet] squad metrics {kind}: {e!r}", flush=True)
        return False


# ---- archive events -> rows (live and backfill alike) ---------------------------
def _channel(ev):
    sid, ref = ev["squad_id"], f"m{ev['msg_id']}"
    base = {"project": ev.get("project"), "member": ev.get("from"), "member_id": ev.get("from_member_id")}
    record(sid, "message", ref, ev.get("ts"), outcome=ev.get("kind") or "post",
           detail={"to": (ev.get("to") or [])[:20], "delivered": len(ev.get("delivered") or []),
                   "reply_to": ev.get("reply_to"), "depth": ev.get("depth")}, **base)
    if ev.get("blocked"):
        why = str(ev["blocked"])
        record(sid, "wake_blocked", ref, ev.get("ts"), outcome=why.split(":", 1)[0][:60], detail={"reason": why[:300]},
               **base)


def _usage(inst, t0, t1):
    """tokens in/out, cost and the main model of an instance's LLM calls in [t0, t1]."""
    with _db() as c:
        rows = c.execute("SELECT model, SUM(prompt_tokens), SUM(completion_tokens), SUM(cost) FROM llm_usage "
                         "WHERE instance=? AND ts BETWEEN ? AND ? GROUP BY model", (inst, int(t0), int(t1))).fetchall()
        rt = c.execute("SELECT MAX(level), MAX(escalated) FROM route_log WHERE instance=? AND ts BETWEEN ? AND ?",
                       (inst, int(t0), int(t1))).fetchone()
    if not rows:
        return {}, rt
    top = max(rows, key=lambda r: (r[1] or 0) + (r[2] or 0))
    return {"model": top[0] or "", "tokens_in": sum(r[1] or 0 for r in rows), "tokens_out": sum(r[2] or 0 for r in rows),
            "cost": round(sum(r[3] or 0.0 for r in rows), 6)}, rt


def _api_failure(res):
    from mgr import tasks as _tasks               # late: tasks imports the squad modules
    return _tasks._api_failure(res)


def turn_start(squad_id, project, task, member_id="", ts=None):
    m = _TICKET_RE.search(str(task.get("message") or ""))
    return record(squad_id, "turn_start", f"task:{task.get('id')}", ts, project=project, member=task.get("instance"),
                  member_id=member_id, ticket=m.group(1) if m else "",
                  detail={"msg": (task.get("squad") or {}).get("msg"), "depth": (task.get("squad") or {}).get("depth")})


def _member_turn(ev):
    """A wake turn's archive line -> turn_start + turn_end."""
    ts, dur = int(ev.get("ts") or 0), float(ev.get("duration_s") or 0)
    t0 = ts - dur
    task = {"id": ev.get("task_id"), "instance": ev.get("instance"), "message": ev.get("incoming"),
            "squad": {"msg": ev.get("msg_id"), "depth": ev.get("depth")}}
    turn_start(ev["squad_id"], ev.get("project"), task, ev.get("member_id"), int(t0))
    use, rt = _usage(ev.get("instance"), int(t0), ts + 1)
    ok = ev.get("status") == "done"
    outcome = "ok" if ok else ("api_error" if _api_failure(ev.get("result")) else "failed")
    m = _TICKET_RE.search(str(ev.get("incoming") or ""))
    detail = {"escalated": bool(rt and rt[1]), "msg": ev.get("msg_id")}
    if not use and ev.get("tokens") is not None:
        detail["tokens"] = ev["tokens"]           # the archive's total, when llm_usage has no rows
    record(ev["squad_id"], "turn_end", f"task:{ev.get('task_id')}", ts, project=ev.get("project"),
           member=ev.get("instance"), member_id=ev.get("member_id"), ticket=m.group(1) if m else "",
           level=rt[0] if rt else None, duration_ms=int(dur * 1000), outcome=outcome, detail=detail, **use)


def _hitl(ev):
    it = ev.get("item") or {}
    base = {"project": it.get("project"), "member": it.get("instance"), "member_id": ev.get("member_id"),
            "ticket": it.get("ticket")}
    if ev.get("event") == "create":
        record(ev["squad_id"], "hitl_open", it.get("id"), it.get("created") or ev.get("ts"), outcome=it.get("kind"),
               detail={"tool": it.get("tool"), "title": str(it.get("title") or "")[:120]}, **base)
    elif ev.get("event") == "decide" and it.get("decided"):
        wait = max(0, int(it["decided"]) - int(it.get("created") or it["decided"])) * 1000
        record(ev["squad_id"], "hitl_decided", it.get("id"), it["decided"], outcome=it.get("status"),
               duration_ms=wait, detail={"kind": it.get("kind"), "by": it.get("decided_by"), "wait_ms": wait}, **base)


def _ticket(ev):
    t = ev.get("ticket") or {}
    sid, proj, to = ev["squad_id"], t.get("project", ""), t.get("status", "")
    ts = int(ev.get("ts") or t.get("updated") or 0)
    tref = f"{proj}#{t.get('id')}"
    if ev.get("event") == "create":
        frm = ""
    else:
        with _db() as c:
            row = c.execute("SELECT detail FROM squad_events WHERE squad_id=? AND kind='ticket_status' AND substr(ref,1,?)=? "
                            "AND ts<=? ORDER BY ts DESC, id DESC LIMIT 1",
                            (sid, len(tref) + 1, tref + ":", ts)).fetchone()
        frm = (json.loads(row[0]) if row and row[0] else {}).get("to", "") if row else ""
        if frm == to:
            return
    record(sid, "ticket_status", f"{tref}:{ts}:{to}", ts, project=proj, member=t.get("assignee"), ticket=t.get("key"),
           level=t.get("level"), outcome=to, detail={"from": frm, "to": to, "by": ev.get("by"), "event": ev.get("event")})


def proposal(prop, squad_id=None):
    """Every state a proposal shows (versions, change requests, decision) -> rows; repeats are ignored."""
    sid, pid = squad_id or prop.get("squad_id"), prop.get("id")
    if not sid or not pid:
        return
    base = {"project": prop.get("project"), "member": prop.get("by")}
    kind = prop.get("kind")
    vers = [(v.get("version", 1), v.get("ts")) for v in prop.get("versions") or []]
    vers.append((prop.get("version", 1), prop.get("updated") or prop.get("created")))
    for v, ts in vers:
        record(sid, "proposal", f"{pid}:v{v}", ts, outcome="created" if v == 1 else "revised",
               detail={"kind": kind, "version": v}, **base)
    for cm in prop.get("comments") or []:
        record(sid, "proposal", f"{pid}:cr:{cm.get('ts')}", cm.get("ts"), outcome="changes_requested",
               detail={"kind": kind, "version": cm.get("version", 1), "by": cm.get("by")}, **base)
    if prop.get("status") in ("approved", "rejected") and prop.get("decided"):
        wait = max(0, int(prop["decided"]) - int(prop.get("updated") or prop.get("created") or prop["decided"])) * 1000
        record(sid, "proposal", f"{pid}:{prop['status']}", prop["decided"], outcome=prop["status"], duration_ms=wait,
               detail={"kind": kind, "version": prop.get("version", 1), "by": prop.get("decided_by"), "wait_ms": wait},
               **base)
        if kind == "merge" and prop["status"] == "approved":
            record(sid, "merge", pid, prop["decided"], project=prop.get("project"), member=prop.get("writer"),
                   outcome="approved", detail={"by": prop.get("decided_by")})


def _feedback(ev):
    record(ev["squad_id"], "feedback", f"f{ev['post_id']}", ev.get("ts"), project=ev.get("project"),
           member=ev.get("by"), outcome="sent",
           detail={"count": len(ev.get("notes") or []), "msg_id": ev.get("msg_id"), "to": ev.get("to")})


_STREAMS = {"channel": _channel, "feedback": _feedback, "member": _member_turn, "hitl": _hitl, "ticket": _ticket, "proposal": proposal}


def ingest(stream, ev):
    """One archived squad event (mgr/squadrec.py) -> its rows. Never raises."""
    try:
        _STREAMS[stream](ev)
    except Exception as e:
        print(f"[quiet] squad metrics {stream}: {e!r}", flush=True)


def git(project, member, op, ref, ts=None, detail=None, squad_id=None):
    """A remote operation of a member (mgr/squadgit.py): push | pr | issue. Never raises."""
    try:
        if squad_id is None:
            from mgr import squadrec as _squadrec     # late: squadrec imports this module
            rec = _squadrec.of_project(project)
            squad_id = rec["id"] if rec else ""
            member_id = _squadrec.member_id(rec, member)
        else:
            member_id = ""
        return record(squad_id, "git", f"{op}:{ref}", ts, project=project, member=member, member_id=member_id,
                      outcome=op, detail=detail)
    except Exception as e:
        print(f"[quiet] squad metrics git: {e!r}", flush=True)
        return False


# ---- backfill ----------------------------------------------------------------------
def _stream_of(rel):
    parts = rel.split(os.sep)
    if rel == "tickets.jsonl":
        return "ticket"
    if parts[:2] == ["conversations", "channel"]:
        return "channel"
    if parts[:2] == ["conversations", "hitl"]:
        return "hitl"
    if parts[:2] == ["conversations", "feedback"]:
        return "feedback"
    if parts[:2] == ["conversations", "members"] and len(parts) == 4:
        return "member"
    return None


def _offset(key):
    with _db() as c:
        row = c.execute("SELECT offset FROM squad_metrics_import WHERE path=?", (key,)).fetchone()
    return row[0] if row else 0


def _set_offset(key, off):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO squad_metrics_import(path, offset) VALUES(?,?)", (key, int(off)))


def _import_file(sid, path, rel, stream):
    key = f"{sid}/{rel}"
    off = _offset(key)
    if os.path.getsize(path) < off:              # rewritten: read it again (refs dedupe)
        off = 0
    n = 0
    with open(path, "rb") as fh:
        fh.seek(off)
        for line in fh:
            if not line.endswith(b"\n"):          # a line being written: next time
                break
            off += len(line)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                ingest(stream, {**ev, "squad_id": sid})
                n += 1
    _set_offset(key, off)
    return n


def backfill():
    """Import the squad archive (and the squad git record) once; safe to re-run -> {squad name: lines read}."""
    from mgr import squadgit as _squadgit         # late: squadgit and squadrec import this module
    from mgr import squadrec as _squadrec
    out = {}
    for rec in _squadrec.records():
        n, top = 0, _squadrec.folder(rec)
        try:
            for d, dirs, files in os.walk(top):
                if d == top:
                    dirs[:] = [x for x in dirs if x in ("conversations", "proposals")]   # not the repo clones
                dirs.sort()
                for f in sorted(files):
                    path = os.path.join(d, f)
                    rel = os.path.relpath(path, top)
                    if os.path.islink(path):
                        continue
                    if f.endswith(".jsonl") and _stream_of(rel):
                        n += _import_file(rec["id"], path, rel, _stream_of(rel))
                    elif rel.startswith("proposals" + os.sep) and f.endswith(".json"):
                        with open(path, encoding="utf-8") as fh:
                            proposal(json.load(fh), rec["id"])
            for e in _squadgit._load()["created"]:
                prec = _squadrec.of_project(e.get("project", ""))
                if prec and prec["id"] == rec["id"] and e.get("number"):
                    git(e["project"], e.get("by"), e.get("kind"), e["number"], e.get("ts"),
                        {"title": str(e.get("title") or "")[:120], "url": e.get("url")}, squad_id=rec["id"])
        except Exception as e:
            print(f"[quiet] squad metrics backfill {rec.get('name')}: {e!r}", flush=True)
        out[rec["name"]] = n
    return out


def prune(keep_days=None):
    try:
        with _db() as c:
            return c.execute("DELETE FROM squad_events WHERE ts < ?",
                             (int(time.time()) - (keep_days or days()) * 86400,)).rowcount
    except Exception as e:
        print(f"[quiet] squad metrics prune: {e!r}", flush=True)
        return 0


# ---- read -----------------------------------------------------------------------------
def events(squad_id, since=0, kind="", limit=1000):
    q, args = "SELECT * FROM squad_events WHERE squad_id=? AND ts>=?", [squad_id, int(since or 0)]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY ts, id LIMIT ?"
    args.append(max(1, min(int(limit or 1000), EVENTS_MAX)))
    with _db() as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, args)]


def to_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        # a cell starting with = + - @ is a formula in a spreadsheet
        w.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in r.items()})
    return buf.getvalue()


def summary(squad_id, since=0):
    with _db() as c:
        tok = c.execute("SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost),0), "
                        "COUNT(*) FROM squad_events WHERE squad_id=? AND kind='turn_end' AND ts>=?",
                        (squad_id, since)).fetchone()
        turns = dict(c.execute("SELECT outcome, COUNT(*) FROM squad_events WHERE squad_id=? AND kind='turn_end' "
                               "AND ts>=? GROUP BY outcome", (squad_id, since)).fetchall())
        counts = dict(c.execute("SELECT kind, COUNT(*) FROM squad_events WHERE squad_id=? AND ts>=? GROUP BY kind",
                                (squad_id, since)).fetchall())
        wait = c.execute("SELECT COALESCE(SUM(duration_ms),0) FROM squad_events WHERE squad_id=? AND ts>=? AND "
                         "(kind='hitl_decided' OR (kind='proposal' AND outcome IN ('approved','rejected')))",
                         (squad_id, since)).fetchone()[0]
        tix = c.execute("SELECT ref, ts, outcome, detail FROM squad_events WHERE squad_id=? AND kind='ticket_status' "
                        "ORDER BY ts, id", (squad_id,)).fetchall()
    created, done = {}, {}
    for ref, ts, to, det in tix:
        t = ref.rsplit(":", 2)[0]
        if (json.loads(det or "{}")).get("from") == "" and t not in created:
            created[t] = ts
        if to == "done" and t not in done:
            done[t] = ts
    done = {t: ts for t, ts in done.items() if ts >= since}
    cycles = [done[t] - created[t] for t in done if t in created]
    return {"squad_id": squad_id, "since": since, "tokens_in": tok[0], "tokens_out": tok[1], "cost": round(tok[2], 6),
            "turns": tok[3], "turns_ok": turns.get("ok", 0), "turns_failed": turns.get("failed", 0),
            "turns_api_error": turns.get("api_error", 0), "tickets_done": len(done),
            "median_cycle_s": statistics.median(cycles) if cycles else None,
            "human_wait_ms": wait, "counts": counts}
