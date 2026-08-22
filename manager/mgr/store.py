# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""store: all data storage in one place — SQLite (task history, LLM usage,
semantic vectors), the flat agent memory (memory.json) and the embedding
client. Part of the mgr package; only needs BASE.
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

HISTORY_DB = None
MEMORY_FILE = None
TASKS_FILE = None
_hist_lock = threading.Lock()
_mem_lock = threading.Lock()
EMBED_URL = "http://127.0.0.1:" + os.environ.get("EMBED_PORT", "8772")


def configure(base):
    global HISTORY_DB, MEMORY_FILE, TASKS_FILE
    HISTORY_DB = os.path.join(base, "history.db")
    MEMORY_FILE = os.path.join(base, "memory.json")
    TASKS_FILE = os.path.join(base, "tasks.json")


# ---- History (SQLite, stdlib): queryable long-term memory of tasks ----------
# Every executed task lands here; agents query it via recall_tasks
# ("have we done this already?" -> no duplicates, base knowledge).


def _hist_conn():
    c = sqlite3.connect(HISTORY_DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS task_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, target TEXT, task TEXT, result TEXT, ok INTEGER,
        schedule TEXT, origin TEXT)""")
    # Usage per LLM call. The agents report it after every call to
    # /api/usage; the manager identifies the instance by source IP. cost is
    # what the provider bills for that call (OpenRouter reports it with
    # "usage":{"include":true}) — 0.0 if it reports nothing.
    c.execute("""CREATE TABLE IF NOT EXISTS llm_usage(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, instance TEXT, model TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_usage_inst_ts ON llm_usage(instance, ts)")
    # Semantic long-term memory: per memory a text + embedding vector
    # (as JSON). Search loads an instance's vectors and computes cosine in
    # memory — at a personal scale (hundreds) that is enough without
    # Vector DB. Vectors are normalised, cosine = dot product.
    c.execute("""CREATE TABLE IF NOT EXISTS semantic_memory(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, instance TEXT, mkey TEXT, text TEXT, vec TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sem_inst ON semantic_memory(instance)")
    return c


def usage_add(instance, model, prompt_tokens, completion_tokens, cost):
    try:
        with _hist_lock, _hist_conn() as c:
            c.execute("INSERT INTO llm_usage(ts,instance,model,prompt_tokens,"
                      "completion_tokens,cost) VALUES(?,?,?,?,?,?)",
                      (int(time.time()), str(instance)[:80], str(model)[:120],
                       int(prompt_tokens or 0), int(completion_tokens or 0),
                       float(cost or 0.0)))
        return "ok"
    except Exception as e:
        return f"error: {e!r}"


def usage_summary():
    """Usage per instance: today (local midnight) and total."""
    midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    out = {}
    try:
        with _hist_lock, _hist_conn() as c:
            for since, key in ((0, "total"), (midnight, "today")):
                for inst, calls, pt, ct, cost in c.execute(
                        "SELECT instance, COUNT(*), SUM(prompt_tokens), "
                        "SUM(completion_tokens), SUM(cost) FROM llm_usage "
                        "WHERE ts >= ? GROUP BY instance", (since,)):
                    out.setdefault(inst, {})[key] = {
                        "calls": calls, "in": pt or 0, "out": ct or 0,
                        "cost": round(cost or 0.0, 4)}
    except Exception:
        return {}
    empty = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
    for v in out.values():
        v.setdefault("today", dict(empty))
        v.setdefault("total", dict(empty))
    return out


def usage_for(instance, since=0):
    """Usage of ONE instance since `since` (epoch). For the Activity panel:
    tokens in/out, cost and number of LLM calls in the chosen time window.
    Tokens accrue per LLM turn (not per tool call) — hence a sum, not an
    attribution to individual audit lines."""
    try:
        with _hist_lock, _hist_conn() as c:
            row = c.execute(
                "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens), SUM(cost) "
                "FROM llm_usage WHERE instance=? AND ts>=?",
                (instance, int(since or 0))).fetchone()
        calls, pt, ct, cost = row or (0, 0, 0, 0)
        return {"calls": calls or 0, "in": pt or 0, "out": ct or 0,
                "cost": round(cost or 0.0, 4)}
    except Exception:
        return {"calls": 0, "in": 0, "out": 0, "cost": 0.0}


def history_add(target, task, result, ok, schedule="", origin=""):
    try:
        with _hist_lock, _hist_conn() as c:
            c.execute("INSERT INTO task_runs(ts,target,task,result,ok,schedule,origin) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (int(time.time()), str(target)[:80], str(task)[:2000],
                       str(result)[:8000], 1 if ok else 0, str(schedule)[:40], str(origin)[:80]))
    except Exception as e:
        print("history_add:", repr(e), flush=True)


def history_search(q="", limit=20):
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    try:
        with _hist_conn() as c:
            c.row_factory = sqlite3.Row
            if q:
                like = f"%{q}%"
                rows = c.execute(
                    "SELECT ts,target,task,result,ok,schedule FROM task_runs "
                    "WHERE task LIKE ? OR result LIKE ? OR target LIKE ? "
                    "ORDER BY ts DESC LIMIT ?", (like, like, like, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts,target,task,result,ok,schedule FROM task_runs "
                    "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print("history_search:", repr(e), flush=True)
        return []
_tasks_lock = threading.Lock()


def load_tasks():
    try:
        with open(TASKS_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return []


def save_tasks(tasks):
    with _tasks_lock:
        tmp = TASKS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(tasks, fh, indent=2)
        os.replace(tmp, TASKS_FILE)


def add_task(instance, message, schedule=""):
    schedule = (schedule or "").strip()
    t = {"id": uuid.uuid4().hex[:12], "instance": instance, "message": message,
         "schedule": schedule, "status": "scheduled" if schedule else "pending",
         "result": "", "created": int(time.time()), "updated": int(time.time()),
         "next_run": _next_run(schedule, int(time.time())) if schedule else int(time.time())}
    tasks = load_tasks()
    tasks.append(t)
    save_tasks(tasks)
    return t


def update_task(task_id, message=None, schedule=None):
    """Change a task. A changed schedule is re-scheduled immediately —
    otherwise the task would run once more on the old plan. An empty plan
    turns the repetition into a one-off task (due now); a task that is
    currently running is left untouched."""
    tasks = load_tasks()
    t = next((x for x in tasks if x.get("id") == task_id), None)
    if t is None:
        return "unknown"
    if t.get("status") == "running":
        return "task is running — try again when it is done"
    now = int(time.time())
    if message is not None and str(message).strip():
        t["message"] = str(message).strip()
    if schedule is not None:
        sched = str(schedule).strip()
        if sched != t.get("schedule", ""):
            t["schedule"] = sched
            t["next_run"] = _next_run(sched, now) if sched else now
            t["status"] = "scheduled" if sched else "pending"
    t["updated"] = now
    save_tasks(tasks)
    when = time.strftime("%d.%m. %H:%M", time.localtime(t["next_run"]))
    return f"task {task_id} updated (next run {when})"


def _next_run(schedule, from_ts):
    """Next run time (epoch) for a schedule spec.
    Formats: 'every 30m' | 'every 2h' | 'every 1d' | 'daily HH:MM' | 'hourly'."""
    s = (schedule or "").strip().lower()
    m = re.match(r"every\s+(\d+)\s*([mhd])", s)
    if m:
        mult = {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return from_ts + int(m.group(1)) * mult
    m = re.match(r"daily\s+(\d{1,2}):(\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        lt = time.localtime(from_ts)
        cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
        if cand <= from_ts:
            cand += 86400
        return int(cand)
    if s == "hourly":
        return from_ts + 3600
    return from_ts + 3600



# ---- Semantisches Langzeitgedaechtnis --------------------------------------


def _embed(texts, kind):
    """Texts -> vectors via the embedding service. None if it is not
    reachable (the caller then falls back to the flat memory instead of
    failing)."""
    try:
        body = json.dumps({"texts": texts, "kind": kind}).encode()
        req = urllib.request.Request(EMBED_URL + "/embed", data=body,
                                     headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=30)
        return json.loads(r.read()).get("vectors")
    except Exception:
        return None


def sem_store(instance, text, key=""):
    """Embed and store a memory. The same (instance,key) is replaced rather
    than duplicated — so the agent updates existing entries."""
    text = (text or "").strip()
    if not instance or not text:
        return False
    # Embed and store the key too: if the value is terse ("Watzmann"),
    # "favourite_mountain: Watzmann" carries at least some context into vector AND
    # in den spaeter angezeigten Treffer.
    full = f"{key}: {text}" if key else text
    vecs = _embed([full], "passage")
    if not vecs:
        return False
    c = _hist_conn()
    with c:
        if key:
            c.execute("DELETE FROM semantic_memory WHERE instance=? AND mkey=?",
                      (instance, key))
        c.execute("INSERT INTO semantic_memory(ts,instance,mkey,text,vec) VALUES(?,?,?,?,?)",
                  (int(time.time()), instance, key or "", full, json.dumps(vecs[0])))
    c.close()
    return True




def sem_search(instance, query, k=5):
    """The k most semantically similar memories of an instance. Cosine in
    memory; the vectors are normalised, so the dot product suffices."""
    query = (query or "").strip()
    if not instance or not query:
        return []
    qv = _embed([query], "query")
    if not qv:
        return []
    q = qv[0]
    c = _hist_conn()
    rows = c.execute("SELECT text, vec FROM semantic_memory WHERE instance=?",
                     (instance,)).fetchall()
    c.close()
    scored = []
    for text, vec in rows:
        try:
            v = json.loads(vec)
            scored.append((sum(a * b for a, b in zip(q, v)), text))
        except (ValueError, TypeError):
            continue
    scored.sort(reverse=True)
    return [{"score": round(s, 3), "text": t} for s, t in scored[:max(1, int(k))]]



# ---- Agent memory (persistent, per instance) ------------------------------


def load_memory():
    try:
        with open(MEMORY_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def mem_store(instance, key, value):
    if not instance or not key:
        return "instance/key missing"
    with _mem_lock:
        m = load_memory()
        m.setdefault(instance, {})[key] = value
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(m, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, MEMORY_FILE)
    return f"saved: {key}"


def mem_recall(instance, key=None):
    m = load_memory().get(instance, {})
    return m if key is None else m.get(key, "")


