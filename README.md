# kAIm56

Monorepo for the agent platform: a **manager** runs agents in their own
Firecracker microVMs, an **Android app** and a **web UI** chat with them, and
**katfs** hands folders from the browser to the agents over P2P.

```
Phone (app) ─┐
             ├─► Manager (:8700, web UI + API) ─► microVM per agent ─► LLM / MCP / tools
Browser ─────┘         │                              ▲
                       └── katfs (iroh, P2P) ──────────┘
```

## Features

**Architecture**
- Self-hosted AI-agent platform — one hardened Firecracker microVM per agent (own kernel, fully isolated)
- Single-process manager (Python standard library only, no framework, no external services) serving the control API, web UI, and chat
- Copy-on-write overlay rootfs: a shared read-only base plus a per-instance write layer
- Optional persistent disk per instance — installed packages and files survive restarts; one-click factory reset
- Automatic per-agent networking: tap devices, /30 subnets, NAT, and LAN gating via iptables
- One-command installer (`curl … | sh`) that provisions the whole stack on a fresh KVM host

**Models & agents**
- Pluggable backends — OpenRouter, OrcaRouter, and self-hosted llama.cpp — selected per instance
- Switch model or provider live during a conversation (`/model`), no restart, context preserved
- Reasoning toggle with a collapsible "thinking" view in web and app
- Orchestrator agent that routes work to capable instances instead of doing it itself
- Claude Code available headless as a first-class agent

**Agent capabilities**
- Built-in tools: shell, files, HTTP fetch, web search, PDF extraction, sub-agents, task scheduling
- Missions — multi-step plans with progress that survive resets and restarts; a finished task instantly advances the next step
- Semantic long-term memory (embeddings, per-query recall) plus flat key/value memory
- Playbooks — standing rules the agent learns from your corrections and always follows
- Prompt templates as slash commands, auto-summarizing context, and large-output offloading
- Steering (interrupt a running turn), goal loops with a judge, and tree-chat (`/branch` … `/back` folds a side-question back into a one-line note)
- Drop-in tool plugins — one Python file per new tool, no image rebuild

**Communication**
- Signal integration — receive and send, incoming messages trigger the agent
- Push notifications to the mobile app and a web bell, clickable straight to the relevant chat
- Voice — speech-to-text and text-to-speech with selectable voices and speed
- Peer-to-peer folder sharing (iroh) from the browser or a native CLI, with ZIP download
- Per-instance MCP servers (e.g. Home Assistant) via a host-side hub

**Clients & UI**
- Web manager: instances, tasks, missions, personas, playbooks, prompt templates, policy, models, sharing, secrets, settings
- Android app (chat, voice, missions, tasks, plus an offline on-device Gemma mode)
- Streaming web chat with Markdown, vision/image input, and a slash-command picker
- Per-instance activity view with time filtering and token/cost usage

**Security & guardrails**
- Secret broker — API keys never touch a VM's disk, gated by source-IP allowlists
- Credential-injection gateway — LLM keys never enter a VM; the manager injects them on egress
- Human-in-the-loop approval for risky tools via Signal
- Hard shell denylist plus an "oracle" second opinion required before destructive actions
- Content gateway — strips invisible-Unicode injection and image metadata, per chat
- Cost and rate circuit breakers — per-instance daily token budget and calls-per-minute
- Task-frequency cap and orphaned-run recovery
- Per-instance egress allowlist and a secret leak-filter on outgoing messages
- Per-instance tool allowlists and a per-instance audit trail

## Layout

| Path | Contents |
|---|---|
| `manager/` | Manager: `manager.py` (web UI + API + VM lifecycle), `chatui.py` (`/chat`), `webterm.py` (browser terminal), `templates/` (agent templates), systemd unit, `logo.svg` |
| `app/` | **KatAgent** (Android, Kotlin/Compose): chat with server agents + a local Gemma model. Builds without a local Android SDK via `Dockerfile.build` |
| `katfs/` | Browser-to-agent folder sharing over iroh (P2P): `node/` + `client/` (Rust), `web/` (WASM bridge), `PROTOCOL.md` |
| `agents/` | Build scripts and guest bridges for the microVM images: `claude/`, `openrouter/`, … |
| `examples/` | Templates for the files intentionally kept out of the repo |

## What is intentionally NOT in the repo

None of this is an accident — see `.gitignore`:

- **Secrets**: `manager/settings.json` (API keys), `app/keystore/` (the app's signing
  key), `katfs/node/secret.key` (iroh identity), `manager/traefik-agents.yml`
  (basicAuth hash → provided as a sample in `examples/`)
- **Site config**: `manager/site.json` (this host's public domain, DNS, uplink NIC)
  and `manager/mcp-catalog.json` (real LAN endpoints of your MCP servers) — both
  shipped as templates in `examples/`. Source defaults are neutral placeholders
  (`example.com`, `1.1.1.1`, `eth0`), so the repo carries no internal addresses.
- **Runtime data**: chat history, tasks, memory, history DB, audit log, `run/`
- **Images**: `*.ext4` (rootfs, multi-GB) and `vmlinux` — built, not versioned
- **Releases**: `*.apk` belong in the release area, not in history

Included, on the other hand, are `personas.json`, `skills.json`, `mcp-catalog.json`
and `secret-policy.json` — that is configuration or authored content, and the MCP
entries reference secrets only as `${NAME}` placeholders.

> Note: `HEAD` carries no secrets and no internal addresses — phone-number fields
> ship **empty** and every host/IP/NIC lives in the gitignored `site.json` /
> `mcp-catalog.json`. Only the earlier **git history** still contains real phone
> numbers and addresses, so treat this repo as **private** until the history is
> squashed or scrubbed (a single-commit publish avoids it entirely).

## Running the manager

```bash
cd manager
cp ../examples/settings.example.json settings.json   # add API keys (chmod 600)
cp ../examples/site.example.json     site.json       # your domain / DNS / uplink NIC
cp ../examples/mcp-catalog.example.json mcp-catalog.json  # optional: your MCP endpoints
sudo python3 manager.py                              # or via firecracker-manager.service
```

Requires on the host: `bin/firecracker` + `bin/vmlinux`, the rootfs images under
`instances/`, and the NFS share (`setup-nfs-host.sh`). Details in `manager/README.md`.

## Building the app

```bash
cd app
docker build -f Dockerfile.build -t katagent-build .
docker run --rm -v "$PWD":/project -v katagent-gradle:/root/.gradle katagent-build \
  gradle assembleDebug --no-daemon --console=plain
# -> app/build/outputs/apk/debug/app-debug.apk
```

## katfs

`katfs/node` (host gateway) and `katfs/client` are Rust crates; `katfs/web` is the
WASM bridge for browser sharing. Protocol: `katfs/PROTOCOL.md`.

## Changes

`manager/CHANGELOG.md` tracks the platform's history.

## Installing on a fresh machine

Requirements: Linux x86_64 **with KVM** (`/dev/kvm`), Docker, Python ≥ 3.9, systemd.

```bash
# from a clone:
./install.sh --check          # only check prerequisites
VMLINUX_URL=<release-asset-url> ./install.sh --with-voice

# or the classic way (once the repo is public):
curl -fsSL https://raw.githubusercontent.com/<user>/kaim56/main/install.sh | sh
```

The installer lays out the runtime tree under `$KAIM56_BASE` (default `$HOME`),
downloads the Firecracker binary (v1.16.1) from GitHub, fetches the guest kernel
(`VMLINUX_URL` — publish it as a release asset), builds the openrouter rootfs plus
the embedding/MCP-hub containers (`--with-voice`, `--with-agents` optional), sets up
the systemd service with a generated password, and runs a smoke test. Idempotent —
run again to update. Then: web UI on port 8700 → Settings tab → add an API key.

## License

kAIm56 is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-or-later)** — see [LICENSE](LICENSE).

In short: you may use, modify, and self-host the software. **If you run it (modified
or not) as a network-accessible service, you must make your version's source
available to its users.** This keeps kAIm56 open across forks and deployments — a
closed-source SaaS built on it is not permitted.

Copyright (C) 2026 Ulrich Neidel. The Android app (`app/`) is under the same license.

### Third-party / attribution
The Python part (manager + agent) is **standard-library only**, with no bundled
third-party code.

**katfs** (Rust, `katfs/`) is original code (AGPL like the rest) but uses crates from
crates.io — not vendored in the repo, pulled at build time: **iroh** (P2P), **tokio**,
**serde**/**serde_json**, **anyhow**, **tiny_http**. These are permissively licensed
(MIT or Apache-2.0) and compatible with the AGPL. Anyone distributing a **built katfs
binary** must include those crates' copyright/license notices (the usual MIT/Apache
requirement for binary distribution); see each crate's repository for exact terms.

Adapted **concepts** (not copied verbatim):
- Context/harness patterns (summarizing, context offloader, goal loop, interventions)
  informed by **strands-agents/harness-sdk** (Apache-2.0); the summarization prompt is
  the only near-verbatim piece.
- `/model`, steering, prompt templates, tool plugins, and the oracle tool: ideas from **pi.dev**.
- Credential-injection gateway: pattern from **OneCLI**.
