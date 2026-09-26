#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""JobSpy as an MCP server for the hub: one tool, search_jobs.

JobSpy (github.com/speedyapply/JobSpy, pip python-jobspy) scrapes Indeed,
LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, Bayt, Naukri and BDJobs in
one call. It needs pandas and a TLS-fingerprinting HTTP client, which is why
it runs HERE in the hub container and not as a stdlib plugin inside a VM:
an instance that has "jobspy" in MCP_SERVERS gets the tool jobspy__search_jobs
through the manager (/api/mcp), like every other hub server.

Stdio JSON-RPC, one message per line, the subset of MCP the agent uses:
initialize, notifications/initialized, ping, tools/list, tools/call. The
server is lenient about a missing initialize — after a hub restart the
first message may well be a tools/call.
"""
import json
import os
import sys

SITES = ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google", "bayt", "naukri", "bdjobs"]
DEFAULT_SITES = [s for s in os.environ.get("JOBSPY_SITES", "indeed,linkedin").split(",") if s]
DEFAULT_COUNTRY = os.environ.get("JOBSPY_COUNTRY", "germany")
MAX_RESULTS = 100
DESC_CHARS = int(os.environ.get("JOBSPY_DESC_CHARS", "600"))

TOOL = {
    "name": "search_jobs",
    "description": (
        "Search job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, …) "
        "with JobSpy. Returns a list of postings: title, company, location, date_posted, "
        "job_url, site, salary, remote, description (trimmed). Use hours_old to get only "
        "recent postings (72 = last three days). Defaults: sites " + ",".join(DEFAULT_SITES)
        + f", country_indeed {DEFAULT_COUNTRY}, 20 results per site."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "search_term": {"type": "string", "description": "What to search for, e.g. 'python developer'"},
            "location": {"type": "string", "description": "City or region, e.g. 'Köln' or 'Bonn, Germany'"},
            "site_name": {"type": "array", "items": {"type": "string", "enum": SITES},
                          "description": "Boards to query (default: " + ",".join(DEFAULT_SITES) + ")"},
            "results_wanted": {"type": "integer", "description": "Results per site (default 20, max 100)"},
            "hours_old": {"type": "integer", "description": "Only postings newer than this many hours (e.g. 24, 72)"},
            "distance": {"type": "integer", "description": "Radius around the location in miles (default 50)"},
            "job_type": {"type": "string", "enum": ["fulltime", "parttime", "internship", "contract"]},
            "is_remote": {"type": "boolean", "description": "Remote jobs only"},
            "country_indeed": {"type": "string",
                               "description": f"Country for Indeed/Glassdoor (default '{DEFAULT_COUNTRY}')"},
            "google_search_term": {"type": "string",
                                   "description": "Google Jobs takes only this free-text query, e.g. 'data engineer jobs in Köln since yesterday'"},
            "linkedin_fetch_description": {"type": "boolean",
                                           "description": "Fetch the full LinkedIn description (slower)"},
            "include_description": {"type": "boolean",
                                    "description": f"Return the description text (trimmed to {DESC_CHARS} chars). Default true."},
        },
        "required": ["search_term"],
    },
}


def _clean(v):
    """pandas cells -> JSON: NaN/None -> None, numpy scalars -> python."""
    if v is None:
        return None
    try:
        if v != v:            # NaN
            return None
    except Exception:
        pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def rows_to_jobs(rows, include_description=True):
    """A list of row dicts (DataFrame.to_dict('records')) -> compact postings."""
    out = []
    for r in rows:
        g = lambda k: _clean(r.get(k))
        salary = None
        if g("min_amount") is not None or g("max_amount") is not None:
            salary = {"min": g("min_amount"), "max": g("max_amount"),
                      "currency": g("currency"), "interval": g("interval")}
        job = {"title": g("title"), "company": g("company"), "location": g("location"),
               "date_posted": g("date_posted"), "job_url": g("job_url_direct") or g("job_url"),
               "site": g("site"), "job_type": g("job_type"), "is_remote": g("is_remote"),
               "salary": salary}
        if include_description:
            d = g("description")
            job["description"] = (str(d)[:DESC_CHARS] + ("…" if d and len(str(d)) > DESC_CHARS else "")) if d else None
        out.append({k: v for k, v in job.items() if v is not None})
    return out


def search_jobs(args):
    """tools/call search_jobs -> (text, is_error)."""
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        return f"python-jobspy is not installed in the hub image: {e}", True
    term = str(args.get("search_term") or "").strip()
    if not term:
        return "search_term is required", True
    sites = [s for s in (args.get("site_name") or DEFAULT_SITES) if s in SITES] or DEFAULT_SITES
    kw = {"site_name": sites, "search_term": term,
          "results_wanted": max(1, min(int(args.get("results_wanted") or 20), MAX_RESULTS)),
          "country_indeed": str(args.get("country_indeed") or DEFAULT_COUNTRY),
          "verbose": 0}
    for k in ("location", "google_search_term", "job_type"):
        if args.get(k):
            kw[k] = str(args[k])
    for k in ("hours_old", "distance"):
        if args.get(k):
            kw[k] = int(args[k])
    for k in ("is_remote", "linkedin_fetch_description"):
        if args.get(k) is not None:
            kw[k] = bool(args[k])
    try:
        df = scrape_jobs(**kw)
    except Exception as e:
        return f"scrape failed: {e!r}"[:800], True
    rows = df.to_dict("records") if df is not None and len(df) else []
    jobs = rows_to_jobs(rows, include_description=args.get("include_description", True))
    return json.dumps({"count": len(jobs), "sites": sites, "search_term": term,
                       "location": kw.get("location", ""), "jobs": jobs},
                      ensure_ascii=False, default=str), False


def handle(msg):
    """One JSON-RPC message -> response dict, or None for notifications."""
    method, mid, params = msg.get("method", ""), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None                                   # notification (initialized, cancelled)
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "jobspy", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
        text, err = search_jobs(params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
