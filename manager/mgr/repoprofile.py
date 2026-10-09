# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Repo profile: what a project's code is, read from its files — deterministic, no LLM.

{languages: [[lang, files]], frameworks, deps, entry_points, main_dirs:
[[dir, files]], readme (first paragraph), keywords (<= 25), files, truncated,
commit}. The squad kickoff gives it to the architect, squad_catalog shows it,
Jev (mgr/jevsetup.py) gets its keywords as context, the proposal card shows
it as what the choice was based on.

Bounded: .git/node_modules/vendor/dist/build/... skipped, at most MAX_FILES
files, manifests and README read up to MAX_READ bytes, never through a
symlink (O_NOFOLLOW; os.walk does not enter symlinked dirs), SCAN_SECONDS.
Cached per project in repoprofile.json (manager dir, root-only) keyed by the
source path and its HEAD commit — a merge or a new clone changes HEAD, so the
next use recomputes. Not a git repo -> computed on every use, not cached.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import stat
import threading
import time
import tomllib

from mgr import paths as _paths
from mgr import projects as _projects
from mgr import projwt as _projwt

CACHE_FILE = os.path.join(_paths.BASE, "repoprofile.json")
CACHE_KEEP = 50
MAX_FILES = 20000
MAX_READ = 256 * 1024
MAX_MANIFESTS = 40
MANIFEST_DEPTH = 2              # the root and two levels below (monorepos)
SCAN_SECONDS = 10.0
MAX_KEYWORDS = 25
README_MAX = 600
TEXT_MAX = 2000                 # the 'Repo profile:' block
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".next", ".nuxt", "out", "target", "__pycache__",
             ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".wrangler", ".cache", ".idea", ".vscode"}
EXT_LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
            ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go",
            ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".rb": "Ruby", ".php": "PHP",
            ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".swift": "Swift",
            ".sh": "Shell", ".bash": "Shell", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
            ".svelte": "Svelte", ".sql": "SQL", ".dart": "Dart", ".lua": "Lua", ".ex": "Elixir", ".exs": "Elixir",
            ".scala": "Scala", ".zig": "Zig"}
# dependency name -> a framework label worth a keyword
KNOWN = {"react": "React", "next": "Next.js", "vue": "Vue", "nuxt": "Nuxt", "svelte": "Svelte", "@sveltejs/kit": "SvelteKit",
         "express": "Express", "hono": "Hono", "fastify": "Fastify", "vite": "Vite", "tailwindcss": "Tailwind CSS",
         "commander": "CLI", "yargs": "CLI", "citty": "CLI", "cac": "CLI", "click": "CLI", "typer": "CLI", "cobra": "CLI",
         "clap": "CLI", "wrangler": "Cloudflare Workers", "@cloudflare/workers-types": "Cloudflare Workers",
         "vitest": "Vitest", "jest": "Jest", "playwright": "Playwright", "@playwright/test": "Playwright",
         "pytest": "pytest", "django": "Django", "flask": "Flask", "fastapi": "FastAPI", "pydantic": "Pydantic",
         "sqlalchemy": "SQLAlchemy", "torch": "PyTorch", "numpy": "NumPy", "pandas": "pandas", "prisma": "Prisma",
         "drizzle-orm": "Drizzle", "gin": "Gin", "echo": "Echo", "tokio": "Tokio", "axum": "Axum", "actix-web": "Actix",
         "laravel/framework": "Laravel", "rails": "Rails", "electron": "Electron", "graphql": "GraphQL"}
_lock = threading.Lock()
_DEP = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-\[\]]*)")


def _read(path):
    """Up to MAX_READ bytes of a regular file, never through a symlink -> str or ''."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        return os.read(fd, MAX_READ).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        os.close(fd)


def _dep_name(spec):
    m = _DEP.match(spec or "")
    return m.group(1).split("[")[0].lower() if m else ""


def _manifest(rel, text, out):
    """Fill out[deps|frameworks|entry] from one manifest file."""
    name, deps, fw, entry = os.path.basename(rel), out["deps"], out["frameworks"], out["entry"]
    where = os.path.dirname(rel)
    pre = where + "/" if where else ""
    try:
        if name == "package.json":
            d = json.loads(text)
            for k in ("dependencies", "devDependencies"):
                deps += list((d.get(k) or {}).keys()) if isinstance(d.get(k), dict) else []
            b = d.get("bin")
            entry += ([f"bin {d.get('name', '')}"] if isinstance(b, str) else [f"bin {k}" for k in b] if isinstance(b, dict) else [])
            if isinstance(d.get("scripts"), dict) and d["scripts"]:
                entry.append(f"{pre}npm scripts: " + ", ".join(list(d["scripts"])[:8]))
        elif name.startswith("requirements") and name.endswith(".txt"):
            deps += [_dep_name(x) for x in text.splitlines() if x.strip() and not x.lstrip().startswith(("#", "-"))]
        elif name in ("pyproject.toml", "Cargo.toml"):
            d = tomllib.loads(text)
            p = d.get("project") or {}
            deps += [_dep_name(x) for x in p.get("dependencies") or []]
            for v in (p.get("optional-dependencies") or {}).values():
                deps += [_dep_name(x) for x in v]
            poetry = (d.get("tool") or {}).get("poetry") or {}
            deps += [k for k in poetry.get("dependencies") or {} if k != "python"]
            entry += [f"script {k}" for k in list(p.get("scripts") or {}) + list(poetry.get("scripts") or {})]
            for k in ("dependencies", "dev-dependencies"):
                deps += list(d.get(k) or {})
            entry += [f"bin {b.get('name', '')}" for b in d.get("bin") or [] if isinstance(b, dict)]
            if name == "Cargo.toml":
                fw.append("Rust crate")
        elif name == "go.mod":
            deps += [ln.split()[0].rsplit("/", 1)[-1] for ln in re.findall(r"^\s*(?:require\s+)?([\w.\-]+/[\w.\-/]+\s+v\S+)",
                                                                            text, re.M)]
        elif name == "composer.json":
            d = json.loads(text)
            for k in ("require", "require-dev"):
                deps += [x for x in (d.get(k) or {}) if "/" in x]
        elif name == "Gemfile":
            deps += re.findall(r"""^\s*gem\s+['"]([^'"]+)""", text, re.M)
        elif name.startswith("wrangler."):
            fw.append("Cloudflare Workers")
            for key, label in (("durable_objects", "Durable Objects"), ("kv_namespaces", "Workers KV"),
                               ("d1_databases", "Cloudflare D1"), ("r2_buckets", "Cloudflare R2"), ("queues", "Cloudflare Queues")):
                if re.search(r"\b" + key + r"\b", text):
                    fw.append(label)
    except (ValueError, TypeError, AttributeError, tomllib.TOMLDecodeError):
        pass


def _readme(text):
    """The first prose paragraph of a README, markdown stripped, <= README_MAX."""
    text = re.sub(r"```.*?```", "\n\n", text, flags=re.S)
    text = re.sub(r"<!--.*?-->|<[^>]+>", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                  # images, badges
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)               # links -> their text
    for para in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip() and not re.match(r"^(#|=+$|-+$|\||>?\s*$)", ln.strip())]
        p = re.sub(r"`|\*\*|__|(?<!\w)[*_]|[*_](?!\w)", "", " ".join(lines))
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) >= 30:
            return p[:README_MAX]
    return ""


def _terms(text):
    """Proper names in prose: runs of capitalised words not opening a
    sentence, and acronyms ("Cloudflare Durable Objects", "CLI")."""
    out, run, start = [], [], True
    for tok in text.split():
        w = tok.strip(".,;:!?()[]\"'")
        proper = w[:1].isupper() and w[1:].replace("-", "").isalnum() and (not start or (len(w) > 1 and w.isupper()))
        if proper:
            run.append(w)
        if run and (not proper or tok[-1:] in ".,;:!?)"):
            out.append(" ".join(run))
            run = []
        start = tok[-1:] in ".!?"
    return out + ([" ".join(run)] if run else [])


def _dedupe(xs):
    seen, out = set(), []
    for x in xs:
        x = str(x).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def build(root):
    """The profile of the folder `root` (see the module doc)."""
    t0, nfiles, truncated = time.time(), 0, False
    langs, tops, out = {}, {}, {"deps": [], "frameworks": [], "entry": []}
    manifests, readme, docker, actions = [], "", False, False
    root = os.path.realpath(root)
    for d, dirs, files in os.walk(root):                    # followlinks=False: symlinked dirs are not entered
        rel = os.path.relpath(d, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not (x.startswith(".") and x != ".github"))
        if rel == ".github" + os.sep + "workflows" and any(f.endswith((".yml", ".yaml")) for f in files):
            actions = True
        for f in files:
            nfiles += 1
            if nfiles > MAX_FILES or time.time() - t0 > SCAN_SECONDS:
                truncated = True
                break
            lang = EXT_LANG.get(os.path.splitext(f)[1].lower())
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
            if depth:
                top = rel.split(os.sep)[0]
                tops[top] = tops.get(top, 0) + 1
            if f.startswith("Dockerfile") or f in ("docker-compose.yml", "compose.yaml", "docker-compose.yaml"):
                docker = True
            if depth == 0 and not readme and f.lower().split(".")[0] == "readme":
                readme = _readme(_read(os.path.join(d, f)))
            if depth == 0 and re.match(r"^(main|cli|index|__main__)\.\w+$", f) and lang:
                out["entry"].append(f)
            if depth == 1 and rel == "bin":
                out["entry"].append("bin/" + f)
            if depth <= MANIFEST_DEPTH and len(manifests) < MAX_MANIFESTS and (
                    f in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "composer.json", "Gemfile")
                    or (f.startswith("requirements") and f.endswith(".txt"))
                    or f in ("wrangler.toml", "wrangler.json", "wrangler.jsonc")):
                manifests.append(os.path.join(rel, f) if depth else f)
        if truncated:
            break
        if depth == 1 and rel == "cmd":
            out["entry"] += ["cmd/" + x for x in dirs]
    for rel in manifests:
        _manifest(rel, _read(os.path.join(root, rel)), out)
    deps = _dedupe(x for x in out["deps"] if x)
    fw = _dedupe(out["frameworks"] + [KNOWN[x.lower()] for x in deps if x.lower() in KNOWN]
                 + (["Docker"] if docker else []) + (["GitHub Actions"] if actions else []))
    languages = sorted(langs.items(), key=lambda t: (-t[1], t[0]))[:8]
    main = sorted(tops.items(), key=lambda t: (-t[1], t[0]))[:6]
    entry = _dedupe(out["entry"])[:12]
    plain = [re.sub(r"^@[^/]+/", "", x) for x in deps if not x.startswith("@types/")]
    kw = _dedupe(fw + _terms(readme)[:6] + [lang for lang, _ in languages[:4]] + plain)[:MAX_KEYWORDS]
    return {"languages": [list(x) for x in languages], "frameworks": fw, "deps": deps[:60], "entry_points": entry,
            "main_dirs": [list(x) for x in main], "readme": readme, "keywords": kw, "files": min(nfiles, MAX_FILES),
            "truncated": truncated}


# ---- per project, cached by HEAD ---------------------------------------------
def _head(repo):
    """HEAD of the git repo at `repo` (as its owner), '' when there is none."""
    try:
        if not os.path.isdir(os.path.join(repo, ".git")):
            return ""
        sha = _projwt.git(repo, ["rev-parse", "--verify", "-q", "HEAD"], check=False).strip()
        return sha if re.match(r"^[0-9a-f]{40,64}$", sha) else ""
    except Exception:                         # a timeout, an unreadable .git: no cache, never a failure
        return ""


def _load():
    try:
        with open(CACHE_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get(name, path, repo=""):
    """The profile of project `name` whose code is at `path` (git repo at
    `repo`, default `path`) — from the cache while HEAD is unchanged."""
    if not path or not os.path.isdir(path):
        return None
    commit = _head(repo or path)
    with _lock:
        c = _load().get(name) or {}
    if commit and c.get("commit") == commit and c.get("path") == path and isinstance(c.get("profile"), dict):
        return c["profile"]
    prof = {**build(path), "commit": commit}
    if commit:
        with _lock:
            d = _load()
            d[name] = {"commit": commit, "path": path, "ts": int(time.time()), "profile": prof}
            d = dict(sorted(d.items(), key=lambda t: -int((t[1] or {}).get("ts") or 0))[:CACHE_KEEP])
            tmp = CACHE_FILE + ".tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    json.dump(d, fh, ensure_ascii=False)
                os.replace(tmp, CACHE_FILE)
            except OSError as e:
                print(f"[quiet] repoprofile cache: {e!r}", flush=True)
    return prof


def of_project(proj):
    """The profile of a host project's source folder, None for katfs/none."""
    src = (proj or {}).get("source") or {}
    if src.get("type") != "host":
        return None
    return get(proj["name"], _projects.source_dir(proj), src.get("path", ""))


def context(prof):
    """Frameworks + keywords, one line — Jev's context."""
    return ", ".join(_dedupe((prof or {}).get("frameworks", []) + (prof or {}).get("keywords", [])))


def text(prof):
    """The compact 'Repo profile:' block for the architect."""
    if not prof:
        return ""
    lines = ["Repo profile:"]
    if prof.get("keywords"):
        lines.append("- keywords: " + ", ".join(prof["keywords"]))
    if prof.get("languages"):
        lines.append("- languages: " + ", ".join(f"{lang} ({n})" for lang, n in prof["languages"]))
    if prof.get("frameworks"):
        lines.append("- frameworks: " + ", ".join(prof["frameworks"]))
    if prof.get("entry_points"):
        lines.append("- entry points: " + "; ".join(prof["entry_points"]))
    if prof.get("main_dirs"):
        lines.append("- main dirs: " + ", ".join(f"{d} ({n})" for d, n in prof["main_dirs"]))
    if prof.get("readme"):
        lines.append("- README: " + prof["readme"])
    return "\n".join(lines)[:TEXT_MAX] if len(lines) > 1 else ""
