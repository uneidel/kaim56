# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Missions: plan/progress store for multi-step jobs.

Part of the mgr package. Upward dependencies (notify_add, sem_store) are
injected by manager.py via configure() — this module never imports from
manager (no cycles). State lives in missions.json under BASE.
"""
import json
import os
import threading
import time
import uuid

MISSIONS_FILE = None          # via configure(BASE)
notify_add = lambda *a, **k: (None, "notify not configured")
sem_store = lambda *a, **k: False


def configure(base, notify=None, sem=None):
    global MISSIONS_FILE, notify_add, sem_store
    MISSIONS_FILE = os.path.join(base, "missions.json")
    if notify:
        notify_add = notify
    if sem:
        sem_store = sem


# ---- Missions: plan/progress store for multi-step jobs ---------------------
# The orchestrator plans a mission (goal + steps), works through it step by step
# via create_task and records the progress HERE — so the working state survives
# /reset, VM restart and the stateless heartbeat. A finished task that belongs
# to a mission step immediately triggers the next push (see _task_worker).
_mi_lock = threading.Lock()
MISSION_MAX_ACTIVE = 5
MISSION_MAX_STEPS = 20
MISSION_MAX_LOG = 30
MISSION_TTL_DAYS = 7           # without activity -> paused + note


def load_missions():
    try:
        with open(MISSIONS_FILE) as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_missions(d):
    tmp = MISSIONS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, MISSIONS_FILE)


def _mi_log(m, text):
    m.setdefault("log", []).append(
        f"[{time.strftime('%m-%d %H:%M')}] {str(text)[:200]}")
    m["log"] = m["log"][-MISSION_MAX_LOG:]
    m["updated"] = int(time.time())


def mission_list(instance):
    return load_missions().get(instance, [])


def mission_start(instance, goal, steps):
    goal = (goal or "").strip()[:300]
    if not goal:
        return None, "goal missing"
    steps = [str(x).strip()[:200] for x in (steps or []) if str(x).strip()][:MISSION_MAX_STEPS]
    if not steps:
        return None, "steps missing"
    with _mi_lock:
        d = load_missions()
        lst = d.setdefault(instance, [])
        if sum(1 for m in lst if m.get("status") == "active") >= MISSION_MAX_ACTIVE:
            return None, f"max {MISSION_MAX_ACTIVE} active missions"
        mid = "m-" + uuid.uuid4().hex[:6]
        m = {"id": mid, "goal": goal, "status": "active",
             "created": int(time.time()), "updated": int(time.time()),
             "steps": [{"n": i + 1, "text": t, "status": "open"}
                       for i, t in enumerate(steps)],
             "log": []}
        _mi_log(m, f"Mission started: {goal}")
        lst.append(m)
        _save_missions(d)
    return mid, "ok"


def mission_update(instance, mid, step=None, status=None, result="",
                   task_id="", add_step="", note=""):
    """Advance a step (status: doing|done|failed|open), optionally append a new
    step or just set a log note."""
    with _mi_lock:
        d = load_missions()
        m = next((x for x in d.get(instance, []) if x.get("id") == str(mid)), None)
        if not m:
            return "unknown mission"
        if m.get("status") not in ("active", "paused"):
            return f"mission is {m.get('status')}"
        if add_step:
            if len(m["steps"]) >= MISSION_MAX_STEPS:
                return f"max {MISSION_MAX_STEPS} steps"
            m["steps"].append({"n": len(m["steps"]) + 1,
                               "text": str(add_step).strip()[:200], "status": "open"})
            _mi_log(m, f"Step added: {add_step}")
        if step is not None:
            st = next((x for x in m["steps"] if x.get("n") == int(step)), None)
            if not st:
                return f"unknown step {step}"
            if status in ("open", "doing", "done", "failed"):
                st["status"] = status
            if result:
                st["result"] = str(result)[:500]
            if task_id:
                st["task_id"] = str(task_id)[:40]
            _mi_log(m, f"Step {step} -> {status or '?'}"
                       + (f": {str(result)[:80]}" if result else ""))
        elif note:
            _mi_log(m, note)
        _save_missions(d)
        return "ok"


def mission_finish(instance, mid, summary="", failed=False):
    with _mi_lock:
        d = load_missions()
        m = next((x for x in d.get(instance, []) if x.get("id") == str(mid)), None)
        if not m:
            return "unknown mission"
        m["status"] = "failed" if failed else "done"
        m["summary"] = str(summary)[:600]
        _mi_log(m, ("Fehlgeschlagen: " if failed else "Abgeschlossen: ") + str(summary)[:150])
        _save_missions(d)
    # Completion as a permanent note into long-term memory + push to the user.
    try:
        if summary:
            sem_store(instance, f"Mission '{m['goal']}' "
                      + ("failed" if failed else "completed")
                      + f": {summary}", key="mission-" + str(mid))
    except Exception:
        pass
    try:
        notify_add(instance, ("Mission failed" if failed else "Mission completed"),
                   f"{m['goal']}\n{summary}"[:900], link="missions")
    except Exception:
        pass
    return "ok"


def mission_admin(instance, mid, action):
    """UI-Aktionen: pause | resume | abort."""
    with _mi_lock:
        d = load_missions()
        m = next((x for x in d.get(instance, []) if x.get("id") == str(mid)), None)
        if not m:
            return "unknown mission"
        if action == "pause" and m.get("status") == "active":
            m["status"] = "paused"; _mi_log(m, "Pausiert (UI)")
        elif action == "resume" and m.get("status") == "paused":
            m["status"] = "active"; _mi_log(m, "Fortgesetzt (UI)")
        elif action == "abort" and m.get("status") in ("active", "paused"):
            m["status"] = "failed"; m["summary"] = "aborted (UI)"
            _mi_log(m, "Abgebrochen (UI)")
        else:
            return f"cannot {action} ({m.get('status')})"
        _save_missions(d)
        return "ok"


def mission_ttl_sweep():
    """Pause inactive missions instead of letting them run on silently."""
    cutoff = int(time.time()) - MISSION_TTL_DAYS * 86400
    with _mi_lock:
        d = load_missions()
        hit = []
        for inst, lst in d.items():
            for m in lst:
                if m.get("status") == "active" and m.get("updated", 0) < cutoff:
                    m["status"] = "paused"
                    _mi_log(m, f"Auto-paused ({MISSION_TTL_DAYS} days inactive)")
                    hit.append((inst, m["goal"]))
        if hit:
            _save_missions(d)
    for inst, goal in hit:
        try:
            notify_add(inst, "Mission paused", f"{goal} — {MISSION_TTL_DAYS} days of no activity.", link="missions")
        except Exception:
            pass


def mission_for_task(task_id):
    """(instance, mission, step) of the mission whose step is waiting on this
    task — for the immediate trigger after task completion."""
    for inst, lst in load_missions().items():
        for m in lst:
            if m.get("status") != "active":
                continue
            for st in m.get("steps", []):
                if st.get("task_id") == str(task_id) and st.get("status") in ("doing", "open"):
                    return inst, m, st
    return None, None, None


