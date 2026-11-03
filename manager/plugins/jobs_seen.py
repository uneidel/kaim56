# Tool plugin: remember the jobs already found — by employer + title, not by ad id.
# Idea from github.com/umutyesildal/berlin-job-search (MIT, scripts/search.py
# mark_seen_jobs): a re-posted ad gets a new id, so the id alone calls it new
# every time. The key here is the employer without its legal form plus the title
# without (m/w/d) and punctuation; the URL stays a second key.
import json
import os
import re
import tempfile
from datetime import date

DESC = ("Remembers the jobs already found, by employer + title (a re-posted ad with a new id counts as "
        "known). action 'check': split `jobs` into new and known (known ones carry first_seen). action "
        "'mark': remember `jobs` as seen today (do this after the run). Each job needs title and company; "
        "url optional. The memory is a file in your workspace.")
PARAMS = {
    "action": {"type": "string", "enum": ["check", "mark"]},
    "jobs": {"type": "array", "items": {"type": "object"}, "description": "objects with title, company, url"},
    "file": {"type": "string", "description": "memory file in the workspace (default jobs-seen.json)"},
}
REQUIRED = ["action", "jobs"]
WORKDIR = os.environ.get("CLAUDE_WORKDIR", "/home/node/workspace")
_LEGAL = re.compile(r"\b(gmbh|ag|se|kg|kgaa|ohg|ug|mbh|co|inc|ltd|llc|plc|holding|group|gruppe|deutschland|germany)\b")
_GENDER = re.compile(r"\((?:[mwfdxi]\s*/\s*)+[mwfdxi]\)|\b[mwfd]\s*/\s*[mwfd](?:\s*/\s*[mwfdx])?\b|\*innen|:innen", re.I)


def _norm(s):
    return " ".join(re.sub(r"[^\w]+", " ", str(s or "").casefold()).split())


def keys(job):
    co = " ".join(_LEGAL.sub(" ", _norm(job.get("company"))).split())
    ti = _norm(_GENDER.sub(" ", str(job.get("title") or "")))
    out = [f"t:{co}|{ti}"] if co and ti else []
    url = str(job.get("url") or "").split("?")[0].rstrip("/")
    return out + ([f"u:{url}"] if url else [])


def _path(name):
    name = os.path.basename(str(name or "jobs-seen.json")) or "jobs-seen.json"
    return os.path.join(WORKDIR, name)


def _load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("seen", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def run(action, jobs, file=""):
    if not isinstance(jobs, list):
        return "jobs must be a list"
    p, seen = _path(file), None
    seen = _load(p)
    if action == "check":
        new, known = [], []
        for j in jobs:
            hit = next((seen[k] for k in keys(j) if k in seen), None) if isinstance(j, dict) else None
            (known if hit else new).append({**j, "first_seen": hit} if hit else j)
        return json.dumps({"new": new, "known": known, "summary": f"{len(new)} new, {len(known)} known"}, ensure_ascii=False)
    if action == "mark":
        today, n = date.today().isoformat(), 0
        for j in jobs:
            for k in keys(j) if isinstance(j, dict) else []:
                if k not in seen:
                    seen[k], n = today, n + 1
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".seen.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "seen": seen}, fh, ensure_ascii=False)
        os.replace(tmp, p)
        return f"{n} new keys remembered, {len(seen)} in {os.path.basename(p)}"
    return "action must be check or mark"
