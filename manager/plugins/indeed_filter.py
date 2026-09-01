# Tool plugin: filter a job list down to NEW matches (date + keywords).
# Adapted from github.com/gvfullstack/JobSearchAutomation (tools/search_indeed.py,
# MIT-style) — pure stdlib post-processing: NO scraping. The agent fetches the raw
# jobs itself via web_search/http_fetch; this tool drops stale hits (posted before
# since_date) and applies include/exclude keywords. That way "new job" is decided
# precisely by date instead of guessed.
import json
import re
from datetime import date, datetime, timedelta

DESC = ("Filters a job list down to NEW, matching hits. Input `jobs` is the list "
        "you found yourself via web_search/http_fetch (each job has e.g. title, "
        "company, location, url, date_posted). `since_date` (YYYY-MM-DD): only jobs "
        "posted on/after that date (for the daily search e.g. yesterday's or today's "
        "date). `keywords`: 'include:a,b exclude:c,d'. Understands Indeed date formats "
        "('today', '2 days ago', German 'vor 2 Tagen', ISO). Returns the filtered "
        "jobs + a summary; unparseable dates are kept.")

PARAMS = {
    "jobs": {"type": "array", "items": {"type": "object"},
             "description": "Found jobs (objects with title/company/location/url/date_posted)"},
    "since_date": {"type": "string",
                   "description": "ISO date YYYY-MM-DD; only jobs on/after this day. Empty = no date filter."},
    "keywords": {"type": "string",
                 "description": "optional 'include:python,sql exclude:internship'"},
}
REQUIRED = ["jobs"]

# "3 days ago" (suffix) OR German "vor 3 Tagen" (prefix). The German literals are
# kept on purpose: Indeed.de posts dates in German, so parsing them is functional.
_DAYS_AGO = re.compile(r"(\d+)\+?\s*(?:days?|tag(?:e|en)?)\s+(?:ago|her|zuvor)", re.I)
_DAYS_AGO_DE = re.compile(r"vor\s+(\d+)\+?\s*tag(?:e|en)?", re.I)


def _parse_posted(s):
    """Indeed date string -> date | None (None = 'keep', never silently drop)."""
    if not s:
        return None
    t = str(s).strip().lower()
    today = date.today()
    # English + German ('heute'/'gestern') Indeed wordings — kept for the .de locale.
    if t in ("just posted", "today", "active today", "posted today", "heute", "gerade eben"):
        return today
    if t in ("yesterday", "gestern"):
        return today - timedelta(days=1)
    m = _DAYS_AGO.search(t) or _DAYS_AGO_DE.search(t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_keywords(raw):
    out = {"include": [], "exclude": []}
    for part in (raw or "").strip().split():
        if part.startswith("include:"):
            out["include"] = [k.strip().lower() for k in part[8:].split(",") if k.strip()]
        elif part.startswith("exclude:"):
            out["exclude"] = [k.strip().lower() for k in part[8:].split(",") if k.strip()]
    return out


def run(jobs=None, since_date="", keywords=""):
    # Models sometimes pass the list as a JSON string, sometimes as a real list.
    if isinstance(jobs, str):
        try:
            jobs = json.loads(jobs)
        except ValueError:
            return "Error: 'jobs' is not a valid JSON array."
    if not isinstance(jobs, list):
        return "Error: 'jobs' must be a list of job objects."

    since = None
    if since_date and str(since_date).strip():
        try:
            since = datetime.strptime(str(since_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            since = None   # invalid -> date filter off, instead of dropping everything

    kw = _parse_keywords(keywords)

    def _hay(job):
        return " ".join(str(job.get(k, "")) for k in
                        ("title", "company", "location", "description")).lower()

    kept, skipped_old, skipped_kw = [], 0, 0
    for job in jobs:
        if not isinstance(job, dict):
            continue
        hay = _hay(job)
        if kw["exclude"] and any(x in hay for x in kw["exclude"]):
            skipped_kw += 1
            continue
        if kw["include"] and not any(x in hay for x in kw["include"]):
            skipped_kw += 1
            continue
        posted = _parse_posted(job.get("date_posted") or job.get("date") or "")
        if since is not None and posted is not None and posted < since:
            skipped_old += 1
            continue
        kept.append(job)

    summary = (f"# {len(jobs)} total, {skipped_old} too old (before {since_date or '-'}), "
               f"{skipped_kw} dropped by keyword, {len(kept)} NEW/matching.")
    return summary + "\n" + json.dumps(kept, ensure_ascii=False, indent=2)
