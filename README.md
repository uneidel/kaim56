# kAIm56

Monorepo für die Agenten-Plattform: ein **Manager** verwaltet Agenten in eigenen
Firecracker-microVMs, eine **Android-App** und eine **Web-Oberfläche** chatten mit
ihnen, **katfs** reicht Ordner vom Browser per P2P an die Agenten durch.

```
Handy (App) ─┐
             ├─► Manager (:8700, Web-UI + API) ─► microVM je Agent ─► LLM / MCP / Tools
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


## Was liegt wo

| Pfad | Inhalt |
|---|---|
| `manager/` | Manager: `manager.py` (Web-UI + API + VM-Lebenszyklus), `chatui.py` (`/chat`), `webterm.py` (Browser-Terminal), `templates/` (Agenten-Vorlagen), systemd-Unit, `logo.svg` |
| `app/` | **KatAgent** (Android, Kotlin/Compose): Chat mit Server-Agenten + lokales Gemma-Modell. Build ohne lokales Android-SDK über `Dockerfile.build` |
| `katfs/` | Ordner-Freigabe vom Browser an die Agenten über iroh (P2P): `node/` + `client/` (Rust), `web/` (WASM-Bridge), `PROTOCOL.md` |
| `agents/` | Bauskripte und Gast-Bridges der microVM-Images: `claude/`, `openrouter/`, `pi/`, `prime/` |
| `examples/` | Vorlagen für die Dateien, die bewusst nicht im Repo liegen |

## Was bewusst NICHT im Repo ist

Nichts davon ist ein Versehen — siehe `.gitignore`:

- **Secrets**: `manager/settings.json` (API-Keys), `app/keystore/` (Signaturschlüssel
  der App), `katfs/node/secret.key` (iroh-Identität), `manager/traefik-agents.yml`
  (basicAuth-Hash → als Beispiel in `examples/`)
- **Laufzeitdaten**: Chat-Verlauf, Tasks, Memory, History-DB, Audit-Log, `run/`
- **Images**: `*.ext4` (rootfs, GB-Bereich) und `vmlinux` — werden gebaut, nicht versioniert
- **Releases**: `*.apk` gehören an die Gitea-Releases, nicht in die Historie

Enthalten sind dagegen `personas.json`, `skills.json`, `mcp-catalog.json` und
`secret-policy.json` — das ist Konfiguration bzw. selbst verfasster Inhalt, und die
MCP-Einträge referenzieren Secrets nur als `${NAME}`-Platzhalter.

> Hinweis: In `manager/templates/*.json` und `agents/*/config*.env` stehen echte
> Telefonnummern als Defaults (Signal-Bot und erlaubte Absender), und überall
> tauchen interne Hostnamen/IPs auf. Kein Geheimnis, aber personenbezogen —
> deshalb ist dieses Repo als **privat** gedacht.

## Manager starten

```bash
cd manager
cp ../examples/settings.example.json settings.json   # API-Keys eintragen (chmod 600)
sudo python3 manager.py                              # oder via firecracker-manager.service
```

Braucht auf dem Host: `bin/firecracker` + `bin/vmlinux`, die rootfs-Images unter
`instances/` und die NFS-Freigabe (`setup-nfs-host.sh`). Details in `manager/README.md`.

## App bauen

```bash
cd app
docker build -f Dockerfile.build -t katagent-build .
docker run --rm -v "$PWD":/project -v katagent-gradle:/root/.gradle katagent-build \
  gradle assembleDebug --no-daemon --console=plain
# -> app/build/outputs/apk/debug/app-debug.apk
```

## katfs

`katfs/node` (Host-Gateway) und `katfs/client` sind Rust-Crates, `katfs/web` die
WASM-Bridge für die Browser-Freigabe. Protokoll: `katfs/PROTOCOL.md`.

## Änderungen

`manager/CHANGELOG.md` führt die Historie der Plattform.


## Installation auf einer frischen Maschine

Voraussetzungen: Linux x86_64 **mit KVM** (`/dev/kvm`), Docker, Python ≥ 3.9, systemd.

```bash
# aus einem Klon:
./install.sh --check          # nur Voraussetzungen pruefen
VMLINUX_URL=<Release-Asset-URL> ./install.sh --with-voice

# oder klassisch (sobald das Repo public ist):
curl -fsSL https://raw.githubusercontent.com/<user>/kaim56/main/install.sh | sh
```

Der Installer legt das Laufzeit-Layout unter `$KAIM56_BASE` (Default `$HOME`) an,
laedt das Firecracker-Binary (v1.16.1) von GitHub, bezieht den Gast-Kernel
(`VMLINUX_URL` — als Release-Asset veroeffentlichen), baut das openrouter-Rootfs
plus Embedding-/MCP-Hub-Container (`--with-voice`, `--with-agents` optional),
richtet den systemd-Dienst mit generiertem Passwort ein und laeuft die
Offline-Testsuite als Smoke-Test. Idempotent — erneut ausfuehren = Update.
Danach: Web-UI Port 8700 → Settings-Tab → API-Key eintragen.


## Lizenz

kAIm56 steht unter der **GNU Affero General Public License v3.0 (AGPL-3.0-or-later)** — siehe [LICENSE](LICENSE).

Kurz: Du darfst die Software nutzen, verändern und selbst betreiben. **Betreibst du sie
(auch verändert) als über ein Netzwerk erreichbaren Dienst, musst du den Quellcode deiner
Version den Nutzern zugänglich machen.** Damit bleibt kAIm56 auch in Forks/Deployments offen —
ein geschlossenes SaaS auf dieser Basis ist nicht zulässig.

Copyright (C) 2026 Ulrich Neidel. Die Android-App (`app/`) steht unter derselben Lizenz.

### Drittanbieter / Attribution
Der Python-Teil (Manager + Agent) ist **stdlib-only**, ohne gebündelten Fremdcode.

**katfs** (Rust, `katfs/`) ist eigener Code (AGPL wie der Rest), nutzt aber Crates von
crates.io — nicht im Repo vendored, sondern beim Bauen bezogen: **iroh** (P2P), **tokio**,
**serde**/**serde_json**, **anyhow**, **tiny_http**. Alle stehen unter permissiven Lizenzen
(MIT bzw. Apache-2.0) und sind mit der AGPL vereinbar. Wer ein **gebautes katfs-Binary**
weitergibt, muss die Copyright-/Lizenzhinweise dieser Crates mitliefern (übliche MIT/Apache-
Pflicht bei Binär-Distribution); genaue Terms im jeweiligen Crate-Repo.

Adaptierte **Konzepte** (nicht 1:1 kopiert):
- Kontext-/Harness-Muster (Summarizing, Context-Offloader, Goal-Loop, Interventions) angelehnt
  an **strands-agents/harness-sdk** (Apache-2.0); der Summarization-Prompt ist die einzige
  textnahe Stelle.
- `/model`, Steering, Prompt-Templates, Tool-Plugins, das Oracle-Tool: Ideen aus **pi.dev**.
- Credential-Injection-Gateway: Muster aus **OneCLI**.
