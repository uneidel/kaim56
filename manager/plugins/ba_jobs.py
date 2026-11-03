# Tool plugin: job search in the Bundesagentur für Arbeit database (Jobbörse).
# Idea and endpoint from github.com/umutyesildal/berlin-job-search (MIT,
# scripts/search.py): the public v6 API with its fixed client key — no account,
# no key of ours. Covers what LinkedIn/Indeed (jobspy) miss: the Mittelstand and
# employers that post only to the Arbeitsagentur.
import json
import urllib.parse
import urllib.request

DESC = ("Searches the Bundesagentur für Arbeit job database (Germany's largest; employers that "
        "LinkedIn/Indeed miss). `what`: title or keywords, e.g. 'Head of IT' or 'CISO'. `where`: city, "
        "`radius_km` around it. `days`: published within the last n days. Returns title, employer, "
        "place, date, salary range when given, the BA detail URL and the employer's URL.")
PARAMS = {
    "what": {"type": "string", "description": "title or keywords"},
    "where": {"type": "string", "description": "city, e.g. Köln; empty = all of Germany"},
    "radius_km": {"type": "integer", "description": "radius around `where` (default 50)"},
    "days": {"type": "integer", "description": "published within the last n days (default 7, max 100)"},
    "limit": {"type": "integer", "description": "max results (default 50, max 200)"},
}
REQUIRED = ["what"]
API = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?"
KEY = "jobboerse-jobsuche"            # the Jobbörse web app's public client id
TIMEOUT, MAX_BYTES = 20, 5 << 20


def _get(params):
    req = urllib.request.Request(API + urllib.parse.urlencode(params), headers={"X-API-Key": KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(MAX_BYTES))


def _salary(it):
    lo, hi = it.get("gehaltsspanneVon"), it.get("gehaltsspanneBis")
    if lo or hi:
        return "–".join(f"{int(x):,}".replace(",", ".") for x in (lo, hi) if x) + " €"
    return ""


def parse(payload):
    out = []
    for it in payload.get("ergebnisliste") or []:
        places = [", ".join(str(x) for x in ((loc.get("adresse") or {}).get("ort"), (loc.get("adresse") or {}).get("plz")) if x)
                  for loc in it.get("stellenlokationen") or []]
        ref = str(it.get("referenznummer") or "")
        out.append({k: v for k, v in {
            "title": it.get("stellenangebotsTitel"), "company": it.get("firma"),
            "location": " / ".join(p for p in places if p), "date_posted": it.get("datumErsteVeroeffentlichung"),
            "salary": _salary(it), "contract": str(it.get("vertragsdauer") or "").lower().replace("keine_angabe", ""),
            "remote": it.get("homeofficemoeglich"), "id": ref, "source": "arbeitsagentur",
            "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/" + urllib.parse.quote(ref, safe="-"),
            "employer_url": it.get("externeURL"),
        }.items() if v not in (None, "")})
    return out


def run(what, where="", radius_km=50, days=7, limit=50):
    what = str(what or "").strip()
    if not what:
        return "what missing"
    limit = max(1, min(int(limit or 50), 200))
    base = {"was": what, "veroeffentlichtseit": str(max(0, min(int(days or 7), 100))), "size": "100"}
    if str(where or "").strip():
        base.update(wo=str(where).strip(), umkreis=str(max(0, min(int(radius_km or 50), 200))))
    jobs, total = [], 0
    for page in (1, 2):
        try:
            d = _get({**base, "page": str(page)})
        except Exception as e:                      # network / HTTP: say so, keep what came
            if not jobs:
                return f"Arbeitsagentur not reachable: {e}"
            break
        got, total = parse(d), int(d.get("maxErgebnisse") or 0)
        jobs += got
        if not got or len(jobs) >= min(total, limit):
            break
    return json.dumps({"total": total, "returned": len(jobs[:limit]), "jobs": jobs[:limit]}, ensure_ascii=False)
