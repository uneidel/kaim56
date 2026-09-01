# CLAUDE.md — working notes for this repo

Guidance for Claude Code (and other agents) working on **kAIm56**.

## Ground rules (from the maintainer)

- **Change only what is necessary and asked for.** No drive-by refactors or unrelated edits.
- **Add a test when it makes sense** (regressions, non-trivial logic, security-relevant paths).
- **If something is unclear, ask** before making assumptions.

## What this is

Monorepo for a self-hosted agent platform: a **manager** runs each agent in its own
Firecracker microVM, an **Android app** (`app/`) and a **web UI** (`manager/chatui.py`)
chat with them, and **katfs** (`katfs/`) shares folders from the browser to agents over P2P (iroh).

## Two-tree setup (important)

Work happens in two locations:

- **`/home/ulrich/firecracker/`** — the **live** system (systemd `firecracker-manager`, runs as root on port 8700). Edit here, restart to apply: `sudo systemctl restart firecracker-manager`.
- **`/home/ulrich/kaim56/`** — the **git repo** (this tree). Mirror live changes here to commit.

The repo layout maps to the live tree: `manager/manager.py` ↔ `firecracker/manager.py`,
`manager/mgr/` ↔ `firecracker/mgr/`, `manager/chatui.py` ↔ `firecracker/chatui.py`, etc.
The agent source that gets baked into the rootfs lives at `/home/ulrich/openrouter-agent/agent.py`
and mirrors to `agents/openrouter/agent.py`.

> Note: `app/` and `app/app/` are duplicated trees in the repo — keep both in sync when
> editing app sources. The live app project is `/home/ulrich/katagent/`.

## Standing update cycle (after every change)

1. **CHANGELOG** — add an entry to `manager/CHANGELOG.md`.
2. **Architecture tab** — if the change is architecture-relevant, update the cards/SVG in `manager/mgr/ui.py`.
3. **Tests** — `cd /home/ulrich/firecracker && ./run-tests.sh` must be green (stdlib `unittest` at `tests/e2e.py`).
4. **Push** (see Publishing).
5. if changes can be validated with an test - create that test

## Manager conventions

- **Standard library only.** No third-party Python deps in the manager/agent. No framework.
- `BASE = os.path.dirname(os.path.abspath(__file__))` — everything is location-independent.
- **`mgr/` package** (Strangler-Fig split): `ui, store, signal, gateway, missions, notify, rules, mcp, katfs`.
  - Modules **never import from `manager.py`**. Cross-references are injected via `configure()` / module-global assignment from the composition root (`manager.py`).
  - `manager.py` stays the facade + composition root (HTTP handler `class H`, net, vm, secrets, `main`).
- Every source file carries the SPDX header (`AGPL-3.0-or-later`, `Copyright (C) 2026 the kAIm56 authors`).
- A route that throws returns a clean **HTTP 500** (the GET/POST dispatch is wrapped) — never a dropped connection.

## Agents (microVMs)

- Overlay rootfs: shared read-only base + per-instance write layer; `persist_disk` keeps it across restarts.
- **Config is env-based**, read at agent start from the config disk (`/config/config.env`). Changing an
  instance's config (model, `AGENT_TOOLS` allowlist, …) **applies after stop/start**, not live.
- **Credential-injection gateway** (pattern from OneCLI): with `LLM_KEY_PROXY=1` agents POST to
  `/api/llm/<backend>/…` and the manager injects the API key on egress — keys never enter a VM.
- Agent changes require a **rootfs rebuild** to take effect: `cd /home/ulrich/openrouter-agent && ./build-openrouter-rootfs.sh` (atomic, non-disruptive), then restart the instance.

## Tool plugins

A tool = a single `.py` **or** a folder `plugins/<name>/` with entry `tool.py` (multi-file; the folder
is put on `sys.path`). Convention: `DESC` / `PARAMS` / `REQUIRED` / `def run(...) -> str`. Managed via the
web **Plugins** tab (drag-drop `.py`/`.zip`, boilerplate). Runs in the VM sandbox, stdlib only.

## Chat sync

One shared store (`chats.json`) for app/web/Signal; clients long-poll `/api/chats?since=&wait=`.
Merges append when the remote is a prefix of local; on **divergence** the newer/not-shorter server
state is adopted (so it can't stall). Deletions use **tombstones** (`{id: deletedAt}`, TTL-pruned,
gitignored `chats_tombstones.json`); a tombstoned chat is not resurrected unless genuinely re-edited.

## Android app

- The **live build tree is `/home/ulrich/katagent`** (root project + `app/` module), NOT the repo `app/`/`app/app/` mirror (which isn't standalone-buildable). Build there: `docker run --rm -v /home/ulrich/katagent:/project -v katagent-gradle:/root/.gradle katagent-build gradle assembleDebug --no-daemon --console=plain` → `app/build/outputs/apk/debug/app-debug.apk`.
- Bump `versionCode` + `versionName` in `app/build.gradle.kts` for each shipped APK.
- **APK naming convention: copy the built `app-debug.apk` to `/home/ulrich/katagent/katagent-<versionName>.apk`** (e.g. `katagent-5.20.apk`) and ship THAT — never the raw `app-debug.apk`.
- R8 kotlin-metadata warnings are benign.

## Publishing

- **Gitea** (`git.kat56.de/ulrich/kaim56`, private): normal commits, full history.
- **GitHub** (`github.com/uneidel/kaim56`, public): **single squashed commit, no history** —
  run `./push-github.sh` (orphan branch from HEAD, force-push, author
  `uneidel <uneidel@users.noreply.github.com>`; refuses a dirty tree and scans for
  secret patterns first). History is squashed because earlier commits contained
  real phone numbers/addresses. The agent's auto mode cannot force-push — the
  maintainer runs the script.

## Not in the repo (gitignored)

`manager/settings.json` (API keys), `manager/site.json` (host/domain/DNS/NIC), `manager/mcp-catalog.json`
(real LAN endpoints), `manager/traefik-agents.yml`, `app/keystore/`, `katfs/node/secret.key`,
`chats*.json`/tasks/memory/`run/`, `*.ext4`, `vmlinux`, `*.apk`. Source defaults are neutral placeholders
(`example.com` / `1.1.1.1` / `eth0`); templates for the gitignored files live in `examples/`.
