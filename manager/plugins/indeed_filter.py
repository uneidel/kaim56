# Tool-Plugin: Jobliste auf NEUE Treffer filtern (Datum + Keywords).
# Adaptiert aus github.com/gvfullstack/JobSearchAutomation (tools/search_indeed.py,
# MIT-artig) — reine stdlib-Nachbearbeitung: KEIN Scraping. Die Roh-Jobs holt der
# Agent selbst via web_search/http_fetch; dieses Tool entfernt Alt-Treffer
# (gepostet vor since_date) und wendet Include/Exclude-Keywords an. So wird
# "neue Stelle" praezise ueber das Datum bestimmt statt geraten.
import json
import re
from datetime import date, datetime, timedelta

DESC = ("Filtert eine Jobliste auf NEUE, passende Treffer. Input `jobs` ist die "
        "Liste, die du selbst per web_search/http_fetch gefunden hast (je Job u.a. "
        "title, company, location, url, date_posted). `since_date` (YYYY-MM-DD): nur "
        "Jobs, die am/nach dem Datum gepostet wurden (fuer die taegliche Suche z.B. "
        "das gestrige oder heutige Datum). `keywords`: 'include:a,b exclude:c,d'. "
        "Versteht Indeed-Datumsformate ('today', 'vor 2 Tagen', ISO). Gibt die "
        "gefilterten Jobs + eine Zusammenfassung zurueck; unparsebare Daten bleiben drin.")

PARAMS = {
    "jobs": {"type": "array", "items": {"type": "object"},
             "description": "Gefundene Jobs (Objekte mit title/company/location/url/date_posted)"},
    "since_date": {"type": "string",
                   "description": "ISO-Datum YYYY-MM-DD; nur Jobs am/nach diesem Tag. Leer = kein Datumsfilter."},
    "keywords": {"type": "string",
                 "description": "optional 'include:python,sql exclude:praktikum'"},
}
REQUIRED = ["jobs"]

# "3 days ago" / "3 Tage her" (Suffix) ODER "vor 3 Tagen" (deutsches Praefix).
_DAYS_AGO = re.compile(r"(\d+)\+?\s*(?:days?|tag(?:e|en)?)\s+(?:ago|her|zuvor)", re.I)
_DAYS_AGO_DE = re.compile(r"vor\s+(\d+)\+?\s*tag(?:e|en)?", re.I)


def _parse_posted(s):
    """Indeed-Datumsstring -> date | None (None = 'behalten', nie still verwerfen)."""
    if not s:
        return None
    t = str(s).strip().lower()
    today = date.today()
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
    # Modelle liefern die Liste mal als JSON-String, mal als echte Liste.
    if isinstance(jobs, str):
        try:
            jobs = json.loads(jobs)
        except ValueError:
            return "Fehler: 'jobs' ist kein gueltiges JSON-Array."
    if not isinstance(jobs, list):
        return "Fehler: 'jobs' muss eine Liste von Job-Objekten sein."

    since = None
    if since_date and str(since_date).strip():
        try:
            since = datetime.strptime(str(since_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            since = None   # ungueltig -> Datumsfilter aus, statt alles zu verwerfen

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

    summary = (f"# {len(jobs)} gesamt, {skipped_old} zu alt (vor {since_date or '-'}), "
               f"{skipped_kw} per Keyword raus, {len(kept)} NEU/passend.")
    return summary + "\n" + json.dumps(kept, ensure_ascii=False, indent=2)
