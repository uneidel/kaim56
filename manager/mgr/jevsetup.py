# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Jev suggests a setup for a new instance: persona, skills, model tier.

Only a suggestion — the "New instance" form and the squad approval card show
it, the user changes or takes it; nothing here creates or changes anything.

Jev (OpenJev NLI, mgr/router.py) takes at most 16 hypotheses of at most 200
characters per call, and the time grows with their length (~0.8 s per short
hypothesis on the host, 2026-10-09; 101 skills in full took over a minute).
So the catalogs are pre-filtered by word overlap with the purpose and its
context — for a squad member the repo profile's keywords, so a skill the
purpose never names (durable-objects for a Cloudflare repo) still gets in (8
personas, 12 skills), hypotheses are cut to 100 characters, and all of them
(the router's task/difficulty questions, personas, skills) go out in calls
of 16, one after the other — Jev works one call at a time, parallel calls
only queue. ~24 s per suggestion. Jev not configured or unreachable -> an
empty suggestion with a note, never an exception.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import time
import urllib.request

from mgr import decision as _decision
from mgr import instances as _instances
from mgr import personas as _personas
from mgr import router as _router
from mgr import skills as _skills

PURPOSE_MAX = 1000
BATCH = 16                       # Jev's limit per call
HYP_MAX = 100                    # Jev allows 200; the cost grows with the length
MAX_PERSONAS, MAX_SKILLS = 8, 12  # candidates after the word pre-filter
FLOOR = 0.5
TOP_SKILLS = 8
SUGGEST_TIMEOUT = float(os.environ.get("JEV_SETUP_TIMEOUT", "60"))
_WORD = re.compile(r"[a-z0-9]{3,}")


def _stems(text):
    return {w[:5] for w in _WORD.findall(str(text or "").lower())}


def _prefilter(items, text_of, purpose, n):
    """The n items sharing the most word stems with the purpose (catalog
    order on ties)."""
    want = _stems(purpose)
    ranked = sorted(enumerate(items), key=lambda t: (-len(want & _stems(text_of(t[1]))), t[0]))
    return [x for _, x in ranked[:n]]


def persona_hyp(p):
    """'A <name> fits this task: <first sentence>' — the sentence from its
    description, or from the prompt when that opens with "You are"."""
    text = re.sub(r"\s+", " ", p.get("description") or "").strip()
    prompt = re.sub(r"\s+", " ", p.get("prompt") or "").strip()
    if not text and prompt.startswith("You are"):
        text = re.sub(r"^You are (an? |the )?", "", prompt)
    first = re.split(r"(?<=[.!?:])\s", text, maxsplit=1)[0]
    name = p.get("name", "").replace("-", " ")
    return (f"A {name} fits this task: {first}" if first else f"A {name} fits this task.")[:HYP_MAX]


def skill_hyp(s):
    return f"This task needs {s.get('name', '').replace('-', ' ')}: {s.get('description', '')}"[:HYP_MAX]


def _entail(purpose, hyps, deadline):
    """Entailment per hypothesis, in calls of BATCH, one after the other (Jev
    works one call at a time; parallel calls only queue)."""
    out = []
    for i in range(0, len(hyps), BATCH):
        left = deadline - time.time()
        if left <= 1:
            raise TimeoutError("Jev too slow")
        body = json.dumps({"premise": purpose, "hypotheses": hyps[i:i + BATCH]}).encode()
        req = urllib.request.Request(_router.jev_url() + "/classify", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=left) as r:
            out += [float(x) for x in json.load(r).get("entail") or []]
    return out


def model_for(tier):
    """(config key, model) of the first concrete model the Router tiers give
    this tier, else ("", "")."""
    key = next((k for k, t in sorted(_router.load()["tiers"].items()) if t == tier), "")
    backend, _, rest = key.partition("/")
    if backend in _instances.PROVIDER_MODEL_KEY and rest:
        return _instances.PROVIDER_MODEL_KEY[backend], rest
    return ("OPENROUTER_MODEL", key) if key else ("", "")


def tier_of(policy, classes):
    """The first rule whose classes hold (above the policy's floor) -> {name,
    score}: its first preferred tier that has a model, else its first tier."""
    floor = float(policy.get("floor", 0.55))
    have = set(_router.load()["tiers"].values())
    for rule in policy.get("rules") or []:
        cond = rule.get("if") or {}
        if cond and all(classes.get(q, ("", 0))[0] == w and classes[q][1] >= floor for q, w in cond.items()):
            prefer = rule.get("prefer") or []
            name = next((t for t in prefer if t in have), prefer[0] if prefer else "")
            if name:
                return {"name": name, "score": round(min(classes[q][1] for q in cond), 3)}
    return None


def empty(note):
    return {"persona": None, "skills": [], "tier": None, "model": "", "model_key": "",
            "classes": {}, "ms": 0, "note": note, "backend": "jev"}


TIER_TEXT = {"cheap": "cheap, fast model for simple, routine work",
             "strong": "strongest general model for hard reasoning, planning and review",
             "code": "model specialised in writing and fixing code",
             "local": "local model on the LAN, private, slower and weaker"}
MAX_LLM_SKILLS = 6


def _desc(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:_decision.DESC_MAX]


def llm_suggest(purpose, context, be):
    """The suggestion from an LLM decision backend (mgr/decision.py): ONE
    request with the full persona and skill catalogs (no word pre-filter, no
    16-hypothesis limit), the tiers, the purpose and the context. Every name
    is checked against the catalogs there; raises on failure."""
    pers = {p["name"]: _desc(p.get("description") or p.get("prompt", "")) for p in _personas.load_personas() if p.get("name")}
    sks = {s["name"]: _desc(s.get("description")) for s in _skills.unique(_skills.load_skills())
           if s.get("name") and not s.get("default")}
    have = set(_router.load()["tiers"].values())
    tiers = {t: TIER_TEXT.get(t, t) for t in _router.TIERS if t in have or not have}
    qs = {"persona": {"type": "choice", "instructions": "Which persona should this team member get?", "criteria": pers},
          "tier": {"type": "choice", "instructions": "Which model tier fits this member's work?", "criteria": tiers}}
    if sks:
        qs["skills"] = {"type": "multi", "max": MAX_LLM_SKILLS, "criteria": sks,
                        "instructions": f"Which skills (0 to {MAX_LLM_SKILLS}) does this member need?"}
    state = f"Team member purpose: {purpose}" + (f"\nContext: {context}" if context else "")
    ans, ms = _decision.ask({"state": state, "questions": qs}, be)
    best = lambda d: max(d.items(), key=lambda t: t[1]) if d else None
    p, t = best(ans.get("persona") or {}), best(ans.get("tier") or {})
    tier = {"name": t[0], "score": t[1]} if t else None
    key, model = model_for(tier["name"]) if tier else ("", "")
    return {"persona": {"name": p[0], "score": p[1]} if p else None,
            "skills": [{"name": k, "score": v} for k, v in (ans.get("skills") or {}).items()],
            "tier": tier, "model": model, "model_key": key, "classes": {}, "ms": ms, "backend": be,
            "note": f"{be}: {len(pers)} personas, {len(sks)} skills, {len(tiers)} tiers in one request"}


def suggest(purpose, context=""):
    """{persona, skills, tier, model, model_key, classes, ms, note, backend}
    for a purpose. `context` (the squad goal, the repo profile's keywords —
    mgr/repoprofile.py) goes into the premise after the purpose, within the
    cap, and into the word pre-filter. With an LLM decision backend
    (DECISION_BACKEND) llm_suggest() answers; its failure falls back to Jev,
    noted."""
    purpose = re.sub(r"\s+", " ", str(purpose or "")).strip()[:PURPOSE_MAX]
    if not purpose:
        return empty("no purpose given")
    context = re.sub(r"\s+", " ", str(context or "")).strip()
    be, fell = _decision.backend(), ""
    if be != "jev":
        try:
            return llm_suggest(purpose, context[:4000], be)
        except Exception as e:
            fell = f"{be} failed ({_decision.safe_err(e)}) — Jev used. "
            _decision._log_fallback(be, e)
    out = _jev_suggest(purpose, context)
    out["note"] = fell + out["note"]
    return out


def _jev_suggest(purpose, context):
    premise = (purpose + " Context: " + context)[:PURPOSE_MAX] if context else purpose
    words = purpose + " " + context
    if not _router.jev_url():
        return empty("Jev is not configured (JEV_URL in Settings)")
    pers = _prefilter([p for p in _personas.load_personas() if p.get("name")],
                      lambda p: p["name"] + " " + (p.get("description") or p.get("prompt", "")[:400]), words, MAX_PERSONAS)
    sks = _prefilter([s for s in _skills.unique(_skills.load_skills()) if s.get("name") and not s.get("default")],
                     lambda s: s["name"] + " " + s.get("description", ""), words, MAX_SKILLS)
    policy = _router.load()["policies"].get("default") or _router.DEFAULT_POLICY
    qs = [(q, c, h) for q, classes in policy["questions"].items() for c, h in classes.items()]
    hyps = [h for _, _, h in qs] + [persona_hyp(p) for p in pers] + [skill_hyp(s) for s in sks]
    t0 = time.time()
    try:
        e = _entail(premise, hyps, t0 + SUGGEST_TIMEOUT)
    except Exception as ex:
        return empty(f"Jev unreachable: {ex!r}"[:200])
    ms = int((time.time() - t0) * 1000)
    if len(e) != len(hyps):
        return empty("Jev answered with the wrong number of scores")
    classes = {}
    for (q, c, _), p in zip(qs, e):
        if q not in classes or p > classes[q][1]:
            classes[q] = (c, p)
    pe, se = e[len(qs):len(qs) + len(pers)], e[len(qs) + len(pers):]
    best = max(zip(pe, pers), key=lambda t: t[0], default=(0, None))
    skills = sorted(({"name": s["name"], "score": round(p, 3)} for p, s in zip(se, sks) if p >= FLOOR),
                    key=lambda x: -x["score"])[:TOP_SKILLS]
    tier = tier_of(policy, classes)
    key, model = model_for(tier["name"]) if tier else ("", "")
    return {"persona": {"name": best[1]["name"], "score": round(best[0], 3)} if best[1] and best[0] >= FLOOR else None,
            "skills": skills, "tier": tier, "model": model, "model_key": key,
            "classes": {q: [c, round(p, 3)] for q, (c, p) in classes.items()}, "ms": ms, "backend": "jev",
            "note": f"{len(pers)} personas, {len(sks)} skills (word pre-filter), {len(qs)} router questions"}
