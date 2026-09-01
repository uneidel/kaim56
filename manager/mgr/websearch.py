# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Web search for the agents, served by the manager.

Lives HERE and not in the agent for the same reason the LLM keys do: the Brave
API key must never enter a VM. The agents call ``GET /api/websearch``; the
manager holds the key, does the calls, and hands back plain results.

Backend order, best first:
- **Brave Search API** when ``BRAVE_API_KEY`` is set (free tier exists) — a
  real search API, no scraping, stable.
- **DuckDuckGo HTML** — free but as of 2026-09 behind a JS proof-of-work
  challenge for datacenter IPs (HTTP 202 + "anomaly" page); detected and
  reported, kept for the day it opens up again.
- **Bing HTML** — works unauthenticated, but softens rare query terms for
  datacenter callers; last resort.

A dead backend is NAMED in the error. "no results" strictly means: the query
ran and found nothing — the distinction the old agent-side tool blurred, which
made a model conclude that companies near Cologne do not exist.

Part of the mgr package: no imports from manager.py. The settings getter is
injected via configure().
"""
import base64
import json
import re
import urllib.parse
import urllib.request

get_setting = lambda key: ""      # injected: manager settings (holds the key)

_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
       "Gecko/20100101 Firefox/120.0")


def configure(settings_getter):
    global get_setting
    get_setting = settings_getter


def web_search(query, count=5):
    """Formatted result text, exactly what the agent tool used to return."""
    try:
        count = max(1, min(int(count), 10))
    except (TypeError, ValueError):
        count = 5
    errors = []
    for name, backend in (("brave", _brave), ("duckduckgo", _ddg), ("bing", _bing)):
        try:
            rows = backend(query, count)
        except Exception as e:
            errors.append(f"{name}: {e!r}")
            continue
        if rows is None:
            errors.append(f"{name}: " + ("no API key" if name == "brave"
                                         else "blocked (bot challenge)"))
            continue
        if rows:
            return "\n".join(f"{i+1}. {t}\n   {u}\n   {sn}"
                             for i, (t, u, sn) in enumerate(rows))
        return "no results"
    return ("⚠️ web search unavailable — every backend failed ("
            + "; ".join(errors) + "). This is an infrastructure problem, "
            "NOT an empty result: tell the user instead of concluding "
            "nothing exists.")


# ---- Brave -----------------------------------------------------------------

def _brave(query, count):
    key = (get_setting("BRAVE_API_KEY") or "").strip()
    if not key:
        return None
    req = urllib.request.Request(
        "https://api.search.brave.com/res/v1/web/search?"
        + urllib.parse.urlencode({"q": query, "count": count}),
        headers={"Accept": "application/json", "X-Subscription-Token": key})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return [( (r.get("title") or "").strip(),
              r.get("url") or "",
              re.sub(r"<[^>]+>", "", r.get("description") or "").strip())
            for r in (d.get("web") or {}).get("results", [])[:count]]


# ---- DuckDuckGo ------------------------------------------------------------

def _ddg(query, count):
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
        headers={"User-Agent": _UA})
    r = urllib.request.urlopen(req, timeout=30)
    html = r.read(1_000_000).decode("utf-8", "replace")
    if r.status != 200 or "anomaly" in html[:4000] or "challenge" in html[:4000]:
        return None
    hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)

    def real(h):
        m = re.search(r"uddg=([^&]+)", h)
        return urllib.parse.unquote(m.group(1)) if m else h

    out = []
    for i in range(len(hrefs)):
        u = real(hrefs[i])
        # Anzeigen fliegen raus: DDG routet sie ueber y.js-Klickzaehler. Ein
        # Werbelink als "Suchtreffer" ist schlimmer als einer weniger.
        if "duckduckgo.com/y.js" in u or "ad_provider=" in u:
            continue
        out.append((_clean(titles[i]) if i < len(titles) else "", u,
                    _clean(snips[i]) if i < len(snips) else ""))
        if len(out) >= count:
            break
    return out


# ---- Bing ------------------------------------------------------------------

def _bing(query, count):
    req = urllib.request.Request(
        "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        + "&count=" + str(max(count, 10)),
        headers={"User-Agent": _UA, "Accept-Language": "de,en;q=0.7"})
    html = urllib.request.urlopen(req, timeout=30).read(2_000_000).decode(
        "utf-8", "replace")
    out = []
    # Per result block: the snippet <p> sits at varying depths, one regex
    # across the page either misses it or bleeds between blocks.
    for block in html.split('<li class="b_algo"')[1:]:
        block = block.split("</li>", 1)[0]
        a = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                      block, re.S)
        if not a:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append((_clean(a.group(2)), bing_real_url(a.group(1)),
                    _clean(p.group(1)) if p else ""))
        if len(out) >= count:
            break
    return out


def bing_real_url(href):
    """Bing /ck/a redirect -> target URL (u=a1<urlsafe-base64>)."""
    h = urllib.parse.unquote(href.replace("&amp;", "&"))
    m = re.search(r"[&?]u=a1([A-Za-z0-9_\-]+)", h)
    if not m:
        return h
    raw = m.group(1)
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
            "utf-8", "replace")
    except Exception:
        return h


def _clean(s):
    return re.sub(r"<[^>]+>", "", s).replace("&amp;", "&") \
        .replace("&#x27;", "'").replace("&nbsp;", " ").strip()
