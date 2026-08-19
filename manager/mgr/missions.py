"""Missionen: Plan-/Fortschritts-Speicher fuer mehrstufige Auftraege.

Teil des mgr-Pakets. Abhaengigkeiten nach oben (notify_add, sem_store) werden
von manager.py per configure() injiziert — dieses Modul importiert nie aus
manager (keine Zyklen). Zustand liegt in missions.json unter BASE.
"""
import json
import os
import threading
import time
import uuid

MISSIONS_FILE = None          # via configure(BASE)
notify_add = lambda *a, **k: (None, "notify nicht konfiguriert")
sem_store = lambda *a, **k: False


def configure(base, notify=None, sem=None):
    global MISSIONS_FILE, notify_add, sem_store
    MISSIONS_FILE = os.path.join(base, "missions.json")
    if notify:
        notify_add = notify
    if sem:
        sem_store = sem


# ---- Missionen: Plan-/Fortschritts-Speicher fuer mehrstufige Auftraege ------
# Der Orchestrator plant eine Mission (Ziel + Schritte), arbeitet sie Schritt
# fuer Schritt ueber create_task ab und haelt den Fortschritt HIER fest — so
# ueberlebt der Arbeitsstand /reset, VM-Neustart und den zustandslosen
# Heartbeat. Ein fertiger Task, der zu einem Missionsschritt gehoert, triggert
# sofort den naechsten Vorstoss (siehe _task_worker).
_mi_lock = threading.Lock()
MISSION_MAX_ACTIVE = 5
MISSION_MAX_STEPS = 20
MISSION_MAX_LOG = 30
MISSION_TTL_DAYS = 7           # ohne Aktivitaet -> paused + Hinweis


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
        _mi_log(m, f"Mission gestartet: {goal}")
        lst.append(m)
        _save_missions(d)
    return mid, "ok"


def mission_update(instance, mid, step=None, status=None, result="",
                   task_id="", add_step="", note=""):
    """Einen Schritt fortschreiben (status: doing|done|failed|open), optional
    einen neuen Schritt anhaengen oder nur eine Log-Notiz setzen."""
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
            _mi_log(m, f"Schritt ergaenzt: {add_step}")
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
            _mi_log(m, f"Schritt {step} -> {status or '?'}"
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
    # Abschluss als dauerhafte Notiz ins Langzeitgedaechtnis + Push an den Nutzer.
    try:
        if summary:
            sem_store(instance, f"Mission '{m['goal']}' "
                      + ("fehlgeschlagen" if failed else "abgeschlossen")
                      + f": {summary}", key="mission-" + str(mid))
    except Exception:
        pass
    try:
        notify_add(instance, ("Mission fehlgeschlagen" if failed else "Mission abgeschlossen"),
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
            m["status"] = "failed"; m["summary"] = "abgebrochen (UI)"
            _mi_log(m, "Abgebrochen (UI)")
        else:
            return f"cannot {action} ({m.get('status')})"
        _save_missions(d)
        return "ok"


def mission_ttl_sweep():
    """Inaktive Missionen pausieren statt still weiterlaufen zu lassen."""
    cutoff = int(time.time()) - MISSION_TTL_DAYS * 86400
    with _mi_lock:
        d = load_missions()
        hit = []
        for inst, lst in d.items():
            for m in lst:
                if m.get("status") == "active" and m.get("updated", 0) < cutoff:
                    m["status"] = "paused"
                    _mi_log(m, f"Auto-pausiert ({MISSION_TTL_DAYS} Tage inaktiv)")
                    hit.append((inst, m["goal"]))
        if hit:
            _save_missions(d)
    for inst, goal in hit:
        try:
            notify_add(inst, "Mission pausiert", f"{goal} — {MISSION_TTL_DAYS} Tage keine Aktivitaet.", link="missions")
        except Exception:
            pass


def mission_for_task(task_id):
    """(instance, mission, step) der Mission, deren Schritt auf diesen Task
    wartet — fuer den Sofort-Trigger nach Task-Abschluss."""
    for inst, lst in load_missions().items():
        for m in lst:
            if m.get("status") != "active":
                continue
            for st in m.get("steps", []):
                if st.get("task_id") == str(task_id) and st.get("status") in ("doing", "open"):
                    return inst, m, st
    return None, None, None


