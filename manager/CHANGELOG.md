# Changelog

## 2026-08-22 (Full German→English pass)
- The entire codebase is now English: comments, docstrings, and user-facing strings across the manager (`manager.py` + all `mgr/` modules), the web chat (`chatui.py`) and web UI (incl. the new Resources tab), the OpenRouter agent (`agent.py`), the Android app (all Kotlin + gradle/manifest/theme/README), the host services (voice/embed/mcp-hub), the Claude bridges, katfs (Rust node/client + web JS), all shell/build/guest-init scripts, Dockerfiles, systemd units, `install.sh`, `personas.json`, the CHANGELOG, and the READMEs. Behaviour is unchanged. Prompt-block tags the agent injects are now English too (`[Missions]`, `[Summary]`, `[Memory]`, `[Branch]`, `[Sidenote]`), and the default personas `assistent`/`uebersetzer` were renamed `assistant`/`translator`. Deliberately kept as input-tolerance literals (matched against user input, not source language): the Signal HITL `ja/nein` keywords, `/steps unbegrenzt`, `/reasoning aus`, `/back verwerfen`, and Indeed.de German date words in `indeed_filter`. The `ManagerSync` create-error detection in the app was updated to the manager's new English tokens (`exists`/`invalid`/`unknown`). All 69 tests green (test fixtures asserting on now-English agent output updated to match).

## 2026-08-20 (UI: Changelog + Architecture in the footer)
- New Resources tab: per-instance sizing (vCPU, configured RAM) plus live usage — actual RSS, CPU% (from /proc, per-core basis), and written overlay-disk size, with bars. Backed by a new /api/resources endpoint; test added.
- Android app v5.19: uploaded photos now show a real thumbnail in the message bubble (Msg gained an image field, persisted + synced) instead of just a 📷 icon; plus an on-device crash log — uncaught exceptions are written to crash.log and viewable/copyable under Settings › Diagnose.
- Security gateway (Layer A completed): the invisible-Unicode scrubber now also removes Unicode noncharacters (U+FDD0–FDEF, U+xFFFE/xFFFF per plane) and permanently-reserved default-ignorable code points (U+2065, U+FFF0–FFF8, U+E0000, tag/ignorable ranges) — the remaining deterministic watermark carriers from guillaumemeyer/watermarks-remover. Emoji/variation-selectors preserved; test added. Also: `manager/text_unicode.py` is now tracked (was missing, breaking the gateway on a fresh clone).
- Tool-plugin integrity: content-hash pinning (idea from Microsoft's APM). Uploading/creating a plugin records a SHA-256 over its files; if a plugin is later edited out-of-band the Plugins tab flags it 'changed since approve' with an Approve button to re-pin. Pins in gitignored manager/plugins/.pins.json; test covers upload→tamper→approve→delete.
- Policy tab: the 15s auto-refresh no longer wipes unsaved tool-checkbox changes (a dirty guard skips the re-render while you have pending edits), and Save shows real errors instead of a false '✓'. Added a set_instance_tools round-trip test (subset persists, all→cleared, unknown filtered).
- Chat sync robustness (web + Android v5.18): when two devices' histories DIVERGE (from an earlier corruption or an out-of-band edit) the prefix-append merge used to stall forever, leaving one side on an older state. Now, if the remote conversation is newer and not shorter, the client adopts the server state as the merge point instead of getting stuck.
- Android app v5.17: assistant messages now render basic Markdown (bold **, italic *, `code`, #-headings as bold, - / * bullets) via AnnotatedString — previously the app showed raw asterisks (only the web chat rendered Markdown).
- Android app v5.16: fix streamed reply landing in the USER bubble (raw ⟦think⟧ markers visible) after a mic-interrupt+re-record — chunks now target the assistant message by a stable key instead of a positional index that could drift. Msg gained a `key` (excluded from equals so multi-device merge is unaffected).
- Android app v5.15: FIX 'Socket is closed' on server chats (regression from v5.12). The 2s read-timeout used for interrupt-polling closed the socket whenever a model took >2s for the first token (e.g. gemini-2.5-pro thinking). Read-timeout is back to 600s and the mic interrupt now cancels via a disconnect handle instead — responsive AND no false timeouts.
- Android app v5.14: the 'Assist button opens instance' setting is now a dropdown of the live server instances (with '— active instance —' for the default) instead of a free-text field.
- Android app v5.13: new setting 'Assist button opens instance' — the device assistant button (ACTION_ASSIST) now opens/records against a configurable server instance (empty = current). Set it in Settings › Server connection.
- Android app v5.12 (voice): (1) recording no longer cuts off mid-sentence during natural pauses — silence hang raised 2.2s→3.5s and the keep-alive threshold lowered; (2) tapping the mic again while the agent is answering now cancels that turn (stream aborted within ~2s) and starts a fresh recording, so you can correct your previous statement. The mic button is enabled during a response.
- Tool plugins now support multi-file projects (each tool its own folder, put on sys.path) next to single .py files, plus a Plugins tab in the web UI: drag-and-drop a .py or .zip, generate a boilerplate, list/delete. Zip extraction is zip-slip-guarded and 5 MB-capped, admin-only. README documents the plugin convention (adapted from pi.dev).
- Web chat: your own messages now have a Copy button too (previously only assistant messages did) and bubble text is explicitly selectable — a background redraw during streaming/long-poll no longer forces you to re-select to copy.
- New tool plugin `indeed_filter` (adapted from gvfullstack/JobSearchAutomation): stdlib-only post-filter that narrows a found job list to genuinely NEW postings (parses 'today'/'N days ago'/ISO dates, filters by since_date) plus include/exclude keywords — no scraping. Wired into the jobresearcher task for precise dedup.
- Hardening: unhandled exceptions in a GET/POST route now return a clean HTTP 500 (and log a traceback) instead of dropping the connection — so a future route bug surfaces as an error the agent can read/retry, not a silent RemoteDisconnected.
- Fix: `playbook_add` failed with RemoteDisconnected in every agent — `mgr/rules.py` used `uuid`/`time` without importing them, so `/api/playbook-add` crashed mid-request and the manager dropped the connection (memory/list worked as they don't use those). Added the imports; regression test covers it.
- Slash typeahead: Esc now reliably closes the picker whenever it's visible and keeps it closed while you keep typing the same /command (reopens on a fresh /).
- Web slash typeahead: now lists all 8 agent commands (added /fresh, /branch, /back) and was restyled into a command-palette (rounded panel, header hint, bold command + muted description, full-row highlight).
- Graceful fallback when a local model emits invalid tool-call JSON: llama.cpp returns HTTP 500 ("Failed to parse tool call arguments as JSON") when a small model can't escape a large string argument (e.g. a whole file). The agent now retries that turn once WITHOUT tools so the model answers as text/code instead of losing the whole turn.
- Android app v5.11: chat delete-tombstones (parity with web). Deleting a chat records `{id: deletedAt}`, pushes it in `{chats, tombstones}`, applies incoming tombstones from the long-poll (removing the local chat), and never resurrects a tombstoned chat unless genuinely re-edited. Deletions now sync App↔Web in both directions.
- Chat deletions now sync across devices via tombstones: deleting a chat records a `{id: deletedAt}` marker that propagates through `/api/chats`; the server refuses to resurrect a tombstoned chat (even on an app re-push) unless it's genuinely edited afterwards (updatedAt > deletedAt, which clears the tombstone). Markers are TTL-pruned (60 days) and kept in the gitignored `chats_tombstones.json`.
- Multi-device chat sync fixed (web `/chat`): (1) the initial conversation was selected before the shared server store finished loading, so a second device opened an empty thread with a fresh id instead of the shared one — now it awaits the pull first; (2) live-appended messages from another device weren't redrawn in the open chat because the merge mutated the same object the `f!==cur` guard skipped — now the open conversation always repaints on a merge.
- Fix `_(empty reply)_` with local reasoning models (llama.cpp/Qwen3): the server streams thinking as `reasoning_content`, which the agent only recognised under the OpenRouter name `reasoning` and silently dropped — if the model emitted no plain `content`, the whole answer was lost. Now both field names stream as thinking, and a reasoning-only turn falls back to showing the reasoning instead of an empty reply.
- Web chat slash-command typeahead: fixed a latent bug (undefined `escT`) that made the picker throw on render so the dropdown never appeared — now it opens on `/`, navigable with ↑/↓, accept with Enter/Tab, Esc to close. Includes prompt templates (tagged), and reflects `/steps … unlimited`.
- /steps now takes any count 1..x (60-cap removed) plus `/steps unlimited` (0 = unbounded rounds; only the guardrails — token budget + rate-limit — then apply). Web-chat + app slash-command help updated.
- Fix: streamed chat could break mid-sentence with slow/local models — during tool execution no bytes flowed and an idle proxy/client timeout (Traefik 180s) cut the connection. Agent now streams a visible tool-status (🔧) plus a heartbeat (·, HEARTBEAT_SEC=30) while tools run, keeping the stream alive.
- Externalised all site-specific values (domains, LAN IPs, uplink NIC, guest DNS) into gitignored `site.json` + `mcp-catalog.json`; source now uses neutral placeholders (example.com / 1.1.1.1 / eth0). Repo HEAD is free of internal infrastructure.
- Leak-filter now also masks HuggingFace tokens (hf_…).
- Scrubbed phone-number defaults from templates/agent configs (now empty); merged the two page footers into a single row.
- The topbar nav was overloaded (the bell + "Missions" pushed Changelog and
    Architecture out of the visible row — the scrollbar is hidden, so they looked
    "gone"). Both are now in a footer; the topbar ends at "Settings" and
    fits on one line again. showTab now also highlights the footer links.


## 2026-08-20 (Guardrails: Budget, Rate-Limit, Egress-Allowlist, Leak-Filter)
- **Cost/frequency circuit breaker** at the key-injection proxy (where all
    LLM calls pass through): daily token budget per instance (default 2M, from
    llm_usage; override `BUDGET_TOKENS`) + frequency cap (default 60/min,
    `LLM_RATE_MIN`). Exceeding it -> 429 + at most one notify per hour.
    Motivation: the nightly heartbeat loop ran completely unthrottled.
- **Task-frequency cap in the worker**: >6 runs/h of the same task -> suspended
    for 1 h + notify (catches loops of any cause).
- **Egress allowlist per instance** (`EGRESS_ALLOW` = domains/IPs): the VM may
    then reach ONLY those (A-records resolved at start, otherwise as before "everything
    except private"). Network level via an iptables FC chain.
- **Leak-filter** on outbound channels (notify, send_signal): known
    key patterns (sk-or-/sk-ant-/sk-/ptr_/gh*_/AKIA/xox*/JWT) are replaced,
    NOT a generic 40-hex (git SHAs stay untouched — tested).
- 57 tests green.


## 2026-08-20 (Refactoring aftershocks: three worker bugs + notification click + UI)
- **Root cause found thanks to the new worker.log**: (1) `chat_log_append` had slipped
    into mgr/notify.py and couldn't find `load_chats` (NameError) -> task
    results never reached the chat (the job-search symptom). Moved back into
    manager.py (chat domain). (2) mgr/store.py was missing `import re` ->
    `_next_run` crashed, next_run stayed in the past -> **scheduled
    tasks ran in an endless loop** (heartbeat since that evening, job search
    multiple times). (3) On post-run errors the worker left "running" orphans.
- **Worker hardened**: each post-run step individually guarded (status
    guarantee), a runtime orphan watch (>30 min running -> requeue),
    diagnostic log run/worker.log (the journal is root-only).
- **Notification click opens the real chat** (App 5.10 + Web): prefers the
    agent's task chat, otherwise the most recent — instead of an empty window.
- **Bell: "Clear"** actually removes notifications (`{clear:true}`);
    opening still only marks them as read.
- **UI**: clunky system scrollbars in the manager removed (dialogs overflow-x
    hidden, slim theme-conform scrollbars globally).


## 2026-08-20 (App 5.9: error messages expire + license header)
- Status/error messages in the app stuck around permanently (e.g. DNS error
    "Unable to resolve agents.example.com" when the phone isn't on the home network/VPN).
    Now: errors expire after 8 s, hints after 4 s, and a
    successful task load clears an earlier error immediately.
- AGPL/SPDX header in all of the app's Kotlin sources (license catch-up).


## 2026-08-20 (Fix: App/Tasks empty after the mgr/ refactoring)
- Regression from the package split: `load_tasks`/`save_tasks` moved into
    mgr/store.py, but the constant `TASKS_FILE` stayed undefined (configure
    didn't set it). Consequence: `/api/tasks` crashed -> the app showed "no tasks",
    and the task worker died, so two scheduled tasks (heartbeat, job search)
    hung on "running" and never fired again.
- Fix: `TASKS_FILE` in `store.configure()` + re-export. Plus **crash recovery**
    `reclaim_stuck_tasks()` at worker start: orphaned "running" tasks are
    requeued to scheduled/pending (self-healing from now on).
- Regression tests: TASKS_FILE wired up, reclaim logic. 55 green.


## 2026-08-19 (manager.py -> mgr/ package — refactoring complete)
- Strangler-Fig refactoring done: **manager.py from 6,705 down to ~1,400 lines
    of core logic** (3,388 total incl. HTTP handler + main). Nine modules under
    `mgr/`: ui (web interface), store (SQLite history/usage/semantics + memory +
    task file), signal (send/HITL/receive), missions, notify, rules
    (playbooks+prompts), mcp, katfs, gateway.
- Pattern applied consistently: manager.py stays the systemd entry point + facade (re-exports)
    and composition layer (VM lifecycle, networking, secrets/instance_by_ip,
    the HTTP handler `class H` and main wire the modules together — deliberately kept
    here). mgr modules NEVER import from manager (cycle check clean);
    cross-references (notify_add, sem_store, load_settings, chat_log_append,
    orchestrator_ping, load_instances) via configure()/injection.
- After EVERY slice: import check, 53 tests green, service restarted and
    verified live (Signal receiver connected, katfs/gateway/hitl responding).
    install.sh copies mgr/ along too. Four stumbles fixed cleanly along the way
    (import order, stray injection lines, too large a cut, missing
    uuid import) — exactly what the test net is there for.
## 2026-08-19 (Tree chat + key-injection gateway ACTIVE)
- **Tree chat (branches)**: side questions no longer pollute the main topic.
    `/branch [topic]` opens a side branch (full inherited context,
    nestable); `/back` closes it: the branch is condensed into ONE
    `[side note]` line (or discarded without a trace with `/back drop`),
    and the context is back at the branch point. Web chat: ⑂ button,
    return bar, branch messages indented + collapsible; branch depth
    syncs into the shared store. Trimming pauses while a branch is open. Proven E2E:
    main topic intact, branch only as a side note.
- **Key-injection gateway (OneCLI pattern), built by the Fable subagent and
    ACTIVATED**: LLM keys no longer leave the host. Agents send chat
    completions to the manager (`/api/llm/<backend>/chat/completions`), which
    injects Authorization when forwarding (real SSE streaming, errors
    transparent). Toggle `LLM_KEY_PROXY` in the settings (on); agent boot
    shows `url=http://172.30.x.1:8700/api/llm/...`. `/model` switching stays
    proxy-capable; llama still direct. The secret broker remains for
    other secrets.
- Tests: 53 green (tree chat open/close/drop, proxy URL/no bearer, route).


## 2026-08-19 (Oracle tool + Playbooks panel)
- **oracle** (pi.dev idea "second opinion before acting"): a second opinion before
    risky actions — its own LLM call without tools, challenges the
    assumptions, never acts itself; via ORACLE_MODEL optionally a stronger model.
    A playbook forces the orchestrator to use it before delete_task & co.; on
    "OBJECTION" it does not act. Verified live with the real MSFT-deletion failure scenario:
    Oracle raised the correct objection, the agent refused + asked back.
- **Playbooks panel** in the Personas tab: view, add, and
    remove rules per agent (previously only possible via chat/API).


## 2026-08-19 (Installer verified END-TO-END — QEMU rig with nested KVM)
- Test rig: QEMU VM (Debian 12 Cloud, seed.iso instead of SMBIOS — that was the
    hang-up), nested KVM confirmed (/dev/kvm in the VM).
- **install.sh ran all the way through on the fresh machine**: preflight,
    layout, Firecracker download, rootfs build via Docker IN the VM,
    embed+mcp-hub containers, systemd service with a generated password.
- **Final proof: a Firecracker microVM booted INSIDE the test VM**
    (overlay boot incl. upper, web-bridge answers 200). The only expected
    deviation: NFS WARN (no NFS in the rig) — degrades correctly and continues.
- One installer bug found+fixed: the smoke test failed with 401 because it
    curled without the freshly generated password — 401 now counts as "alive".


## 2026-08-19 (Fix: "Thinking" kept popping open during streaming)
- paint() replaces the last message on every token completely during streaming — and
    always set the details element back to `open`. Now the
    user-chosen open/closed state is remembered before the repaint and restored
    afterwards; collapsed stays collapsed.


## 2026-08-19 (/steps + jobresearcher fix + config route)
- **/steps [n]** — change the max tool steps per turn at runtime (1-60,
    until restart; permanently via AGENT_MAX_STEPS). Reason: research runs of the
    jobresearcher ended with "(max tool steps reached)" at the default 12.
- **New admin route** POST /api/instances/<n>/config {key,value} — set/delete a single
    config value (secrets excluded). Used to put jobresearcher
    permanently on AGENT_MAX_STEPS=30; duplicate task (daily 08:00) deleted.
- /steps in the web slash hint and app picker (app entry comes with the next APK).


## 2026-08-19 (pi.dev ideas adopted: /model, steering, prompts, plugins)
- **/model** — switch model (and backend) mid-session, without
    restart, context preserved: `/model orcarouter:anthropic/claude-sonnet-4.6`,
    `/model <id>` (model only), `/model` (show). Effective until restart.
    Verified live incl. backend switch OrcaRouter->OpenRouter->back.
- **Steering** — call in to the running agent: a new message is injected between
    two tool steps as a `[steering]` user message instead of
    waiting. Web: Enter with text during a response (button ■ still aborts);
    App 5.8: send while busy. Guest endpoint `POST /api/steer`
    (queued=false if no turn is running -> send normally).
- **Prompt templates** — recurring jobs as a slash command:
    maintain them in the Personas tab, in chat `/name [extra]`; expansion happens in the
    agent -> works in Web, App AND Signal. Store prompts.json,
    `GET/POST /api/prompts`. Slash suggestions now in the web chat too.
- **Tool plugins** (pi.dev extension idea, translated) — one .py file per tool
    in `firecracker/plugins/` (DESC/PARAMS/REQUIRED + run()); the manager puts
    them on the config disk, the agent loads them at start (/config/plugins).
    Collisions with built-in tools are rejected. Example `wuerfel.py`;
    live: "tools=36", the call works. New tool = file + stop/start.
- 48 tests green (new: model-switch, steering-queue, prompt-expansion+store,
    plugin-loader incl. collision protection).


## 2026-08-19 (pi/prime decommissioned)
- Usage analysis (llm_usage, task_runs, instances): pi and prime were **never**
    used; their purpose (multi-provider) is better covered by openrouter/orcarouter/llama,
    and the bridges had no access to the kAIm56
    ecosystem (tools/memory/missions). Removed: templates, rootfs images
    (~7 GB), Docker images, secret-policy entries, OVERLAY_ROOTFS, installer
    references, repo directories. The source code stays in the git history.


## 2026-08-19 (Installer: curl | sh)
- **install.sh** in the kaim56 repo: installs the whole solution on a
    fresh machine (preflight incl. KVM check, runtime layout, Firecracker
    v1.16.1 from GitHub, guest kernel via VMLINUX_URL, rootfs+embed+mcp-hub
    builds, systemd unit with a generated password, offline tests as a smoke test).
    Flags: --check/--files-only/--no-build/--with-voice/--with-agents; idempotent.
- Portability for that: GUEST_DNS, CLAUDE_CRED_SRC, AGENT_DIR (NFS), FC_DIR
    (build scripts), test paths now via env; folder-picker HOME dynamic.
- Repo completed: embed/, mcp-hub/, tests/, run-tests.sh checked in;
    the complete live state synced.
- Verified: --check green; --files-only built a fresh target layout from
    which manager.py imports and **30 offline tests run green**; the real
    Firecracker release was fetched from GitHub. Open for curl|sh: push the repo to
    GitHub + vmlinux as a release asset (VMLINUX_URL).


## 2026-08-19 (Fix: tool-catalog drift + drift guard)
- The four mission tools and `offload_read` were missing from the manager tool catalog
    (`AGENT_TOOLS_CATALOG`) — so they didn't show up in the create-instance
    form and a tool allowlist would have silently blocked them. Added.
- **New drift test** (`test_tool_catalog_matches_agent`): agent BUILTIN and
    manager catalog must be congruent — a forgotten
    catalog entry (or a ghost entry without a tool) turns the suite red.


## 2026-08-19 (Fix: browser terminal broke after ~10 s idle)
- The terminal's WS tunnel opened the guest socket with
    `create_connection(timeout=10)` — the connect timeout stayed as a READ
    timeout on the socket, and after 10 s of silence `recv()` tore down the tunnel
    ("connection closed"). Fix: timeout set to None after connect, plus
    TCP keepalive on both sides (half-dead connections die anyway).
    Verified: survived 16 s idle on the open WS.


## 2026-08-19 (Overlay added for pi/prime/claude)
- The overlay boot block (fc_upper -> /mnt sync mount -> overlayfs ->
    pivot_root, with ro-base fallback) is now also in the guest inits of
    **pi, prime and claude**; all three images rebaked and verified via a
    throwaway test instance (bridge boots, no overlay WARN).
- `OVERLAY_ROOTFS` thus covers all four images — **every** instance boots
    from the shared ro base + its own upper, and the persist toggle
    ("💾 persistent") is available everywhere. private_rootfs() remains
    only as a fallback for unknown images.
- Disk savings per running instance: pi ~3 GB, prime ~4 GB, claude ~3 GB
    copy avoided (now ~35 MB upper).


## 2026-08-19 (Overlay rootfs + persistent disk per instance)
- **Overlay boot** for the openrouter rootfs: the base attaches READ-ONLY to
    all instances (Firecracker blocks writes -> the journal-sharing
    bug from 08-15 is structurally gone), plus per instance a small rw upper
    image; the guest init builds overlayfs + pivot_root. NO more 2-GB copy per start.
    If the overlay build fails, the VM boots degraded on the
    ro base and continues (fallback, never a boot refusal).
- **Persistent disk, selectable per instance (default off):** a tag in the
    instance table ("fresh each start" / "persistent"). On = the write layer
    (4 GB sparse, instances/<n>-upper.ext4) survives stop/start — apt/pip/npm
    installations persist. Right-click = factory reset of the layer.
    Routes: /api/instances/<n>/persist + /diskreset.
- **Learned & fixed:** (1) the ro root needs an existing mountpoint (/mnt
    instead of mkdir /ov). (2) stop() is a power plug (SIGTERM to Firecracker)
    — without a sync mount the upper lost the last writes (0-byte file
    in the debugfs proof); the upper now mounts with -o sync.
- **Proven E2E:** write a file -> stop/start -> content "SURVIVED-
    RESTART" still there. Kernel check beforehand: all container/overlay features
    active in the guest kernel (Docker/podman IN the VM thus possible; podman
    would be the next step). orchestrator+hass run on overlay
    (tools=35, tencent/hy3). 42 tests green. Other templates (claude/pi/
    prime/llama) unchanged on the old copy path.


## 2026-08-19 (Settings pane for TTS/STT)
- **Voice section in the Settings tab**: a status line (service up? STT model,
    available TTS voices via new `GET /api/voice-health`) + two new
    settings: **TTS voice** (selection) and **TTS speed** (0.5–2.0).
- **Voice service extended**: `/tts` accepts `voice` + `speed`
    (Piper `--length_scale`, clamped), `/health` lists the voices.
    Two new voices baked in: `de-eva_k-x_low` (German, female) and
    `en-amy-medium` (English) next to `de-thorsten-medium`.
- **Injection in the manager proxy**: app and web still send only `{"text"}` —
    the manager mixes voice/speed from the settings into the `/api/tts` body
    (explicit client values win). No client update needed.
- The settings UI can now do selection fields (options in the SETTINGS_SCHEMA).
- STT (Parakeet v3) has nothing meaningfully configurable (language automatic) —
    so only a status display.


## 2026-08-19 (Missions get their own tab/screen + clickable notifications)
- **Missions separated out**: their own "Missions" tab in the web manager (instead of in the
    Tasks tab; completed ones directly visible) and their own "Missions" screen
    in App 5.7 (drawer entry with a flag icon).
- **Notifications lead to action**: notification entries now carry
    a `link` field (missions | tasks | chat:<instance>). Web: a click in the
    bell dropdown jumps to the tab or opens the instance's chat
    (arrow indicator). App: tapping the system notification opens the app
    directly at the target (PendingIntent + notifLink extra, same pattern as the Assist intent).
    Sources: agent notify -> chat:<instance>, mission completion/TTL -> missions.


## 2026-08-19 (Missions — plan/progress store for multi-step jobs)
- **Missions**: for multi-step jobs the orchestrator lays out its own
    plan (`mission_start`: goal + steps), works through it via `create_task` and
    keeps the progress in the manager (`missions.json`) — the working state
    survives /reset, VM restart and the stateless heartbeat (injected as a
    `[missions]` block per turn).
- **Instant trigger**: once a task a mission step is waiting on finishes
    (task_id on the step), the task worker pokes the orchestrator directly toward the
    next push (`_mission_advance_fire`) — no waiting for the
    heartbeat; that now only checks stalled steps as a fallback.
- **Guardrails**: max 5 active missions / 20 steps / log capped;
    a TTL sweep pauses missions inactive for 7 days + notification; completion ->
    conclusion into the semantic memory + push (`notify`).
- **Tools** (orchestrator only, TASK_ADMIN): `mission_start/missions/
    mission_update/mission_finish`. Routes `/api/missions`, `/api/mission-*`
    (guest source-IP gated), `/api/mission-admin` (UI: pause/continue/abort).
- **UI**: a missions panel in the Tasks tab (Web: progress bar, current
    step, log, pause/abort) and in **App 5.6** (Tasks screen:
    mission cards, expandable with all steps + actions).
- **Verified live**: a 2-step mission ran fully autonomously in ~40 s —
    task done -> trigger -> step done -> next task -> trigger ->
    mission_finish + completion notification. 39 tests green (lifecycle, caps,
    mission_for_task, HTTP).


## 2026-08-19 (Chat UI beautified in the Industry design)
- The chat interface (/chat) cleanly pulled onto the **Industry** design system
    (claude.ai/design, project styles.css as the source):
  - **Blueprint objects**: composer and welcome panel with a hairline +
    registration corners (corners turn accent-blue on focus).
  - **Typography**: Barlow Condensed for headline, buttons, chips,
    agent select; kicker labels (MICROVM AGENT, CHATS) in uppercase.
  - **Token ramps** instead of ad-hoc values (accent-100…800, color-mix dividers);
    dark mode derived from the same ramps.
  - **Finishing touches**: agent chips as tag-accent, the active chat with an accent
    inset bar, bubbles with a hairline + elevation, a running dot with glow,
    styled scrollbars, ::selection, the Thinking block as an uppercase summary.
  - Verified via headless-Chrome screenshots (light/dark/conversation).
    JS logic unchanged; backup chatui.py.bak-design-*.


## 2026-08-19 (Fix: "Save password?" popup on chat switch)
- Chrome offered, when navigating to /chat, to save a "password for example.com" —
    with the katfs node-id as the supposed username and the
    masking marker `__unchanged__` as the password. Cause: the API-key fields
    in the Settings tab were `type=password`; Chrome's password manager pairs such a
    field with the next text field (katfs key) into a "login" and ignores
    `autocomplete=off`. Fix: masking via CSS (`-webkit-text-security:disc`,
    class `seckey`) instead of password semantics — no more popup, values stay
    hidden; the real keys never leave the manager anyway (marker instead of value).


## 2026-08-19 (Fix: dropdown reset in the new-instance form)
- Reported as "template dropdown jumps back on transport switch".
    An instrumented headless-Chrome test (CDP, setter trap on #tpl, 35 s with
    all intervals): the template select itself is never reset.
    What was found instead was an **async race in loadModels**: the fetch of the
    OpenRouter model list (upstream, seconds on a cold cache) remembered the
    value from the fetch START and rebuilt the model dropdown with it on arrival
    — a selection made in the meantime visibly jumped back to the initial value
    (coincidentally timed with the transport click). Fix: read the value on RESOLVE,
    no longer touch detached selects.
- **Cache-Control: no-store** for the manager page: stale pages after
    updates produced ghost errors (old JS logic against a new API).


## 2026-08-19 (Fix: model dialog for orcarouter/llama)
- Clicking the model of an orcarouter instance reported "This instance has no
    model setting": the key list in the model dialog (editModel) only knew
    OPENROUTER/ANTHROPIC/PI/PRIME. `ORCAROUTER_MODEL` and `LLAMA_MODEL`
    added — the dialog now opens with the current model as free text.


## 2026-08-19 (Activity panel: time filter + usage)
- **Time filter** in the Activity dialog (1h / 24h / 7d / All) — filters the
    audit actions client-side by `ts` (fetch limit 1000).
- **Usage total for the period** in the same header: actions,
    LLM calls, tokens (in->out) and cost from `llm_usage` via new
    `GET /api/usage/<name>?since=` + helper `usage_for`. Deliberately as a SUM,
    not per audit row: tokens accrue per LLM turn, not per tool call
    (one turn triggers 0..N tool calls) — an attribution would be invented.


## 2026-08-19 (App 5.5 — VAD fix for voice recording)
- **Recording cut off mid-speech after ~4 s.** Cause: the background noise
    was measured as the MAXIMUM of the first 400 ms — if you started talking immediately, speech flowed
    into the "floor" and the threshold `floor*3` became unreachable, every
    further measurement counted as silence. Fixes: floor as the MINIMUM over ~0.6 s
    (capped), an absolute margin instead of a factor, and **hysteresis** — once speaking
    is detected, a much lower threshold keeps the recording alive, so the
    amplitude valleys between words don't count as silence. `VAD_HANG`
    1800->2200 ms. Max recording duration unchanged `VAD_MAX = 120 s` (emergency brake).


## 2026-08-19 (Notifications)
- **Notification channel App + Web + Tool.** A new push channel next to Signal:
  - **Agent tool `notify(title, message)`** -> `POST /api/notify` (guest, by
    source IP; rate-limit 30/5min, audit). In the tool catalog.
  - **Manager store** `notifications.json` (rev + long-poll like chats, cap 200):
    `GET /api/notifications?since=&wait=` (admin, with `unread`),
    `POST /api/notifications/read` (`{id}`|`{all:true}`).
  - **Web manager:** a bell in the topbar with an unread badge + dropdown, long-poll,
    optional browser notification (permission on click); opening acknowledges.
  - **App (5.4):** its own poll loop -> Android system notification (channel
    `kaim56_agent`, uses the existing POST_NOTIFICATIONS permission) for new,
    unread entries since app start. `ManagerSync.pollNotifications/markNotifRead`.
  - Verified E2E: the orchestrator calls `notify` -> lands in the store (`tools=31`).


## 2026-08-18 (Playbooks, reasoning display, task management, Signal receiving, katfs)
- **Harness pattern adopted** (based on strands-agents/harness-sdk,
  Apache-2.0; ported, no new dependency — the agent stays stdlib-only):
  - **Summarizing Context:** `_trim_history` no longer throws old messages
    away, but condenses the oldest into a `[summary]` block
    (system pinned, the last `CTX_PRESERVE_RECENT`=10 verbatim, an existing
    summary folded in). Against both token runaway AND context loss.
  - **Context offloader:** tool outputs > `OFFLOAD_MIN` are moved entirely into
    `.offload/`; in the context a preview + reference remain, the rest is
    reloadable via the new tool `offload_read(id, offset)`. Instead of a hard cut.
  - **Goal loop:** `/goal <criterion>` sets a goal; a judge checks the
    answer and lets it improve up to `GOAL_MAX_ATTEMPTS`=3. `/goal off` turns it off.
  - **Tool hook + HITL:** a hard denylist (rm -rf /, fork bomb, mkfs …) always active;
    optional approval of risky tools via Signal (opt-in `HITL=1`,
    `HITL_TOOLS`) — the manager asks "ok <id>/no <id>", the agent polls
    `/api/hitl`/`/api/hitl/<id>`. No Signal receiver -> doesn't block.
  - **Activated:** rootfs rebaked, orchestrator + hass restarted
    (`tools=30`, offload_read live). **App 5.2:** `/goal` in the slash picker.
  - **Retry with backoff** around every model call (429/5xx, 0.5→8s), an empty
    tools list is no longer sent along (which caused a 400 at the summarizer/judge).
- **E2E test suite** (`tests/e2e.py`, `./run-tests.sh`) — stdlib unittest, no
  dependency. Three tiers: OFFLINE (agent/manager functions via import —
  backend choice, summarizing, offloader, hook denylist, goal, empty-tools fix,
  provider switch incl. the ":free" pitfall, HITL store, katfs ZIP walk),
  HTTP (against the running manager: /api/agents backend+model, /api/hitl,
  katfs-status, template registration), LIVE (a free /goal round-trip
  to the orchestrator VM). Missing tiers are cleanly skipped. 30 tests.
  Runs from now on with every change (like Changelog/Architecture).
- **Fix model self-report:** `/api/agents` only knew OPENROUTER/PI/PRIME
  as a model key and reported the *template* as the provider — an orcarouter
  agent thus appeared as "openrouter, without a model". Now: all MODEL_KEYS +
  a real `backend` field. Additionally the agent knows its own backend/
  model from the SYSTEM prompt (names it directly instead of via list_agents).
- **Provider switch via set_model** ("orcarouter:tencent/hy3" sets both backend AND
  model, removes the other MODEL_KEYS). **Orchestrator moved to production on
  OrcaRouter `tencent/hy3`** (backend=orcarouter verified, tool
  calling + key broker ok).
- **OrcaRouter as a second LLM backend next to OpenRouter.** An OpenAI-compatible
  gateway (`https://api.orcarouter.ai/v1`, key format `sk-orca-…`; or
  self-hosted via OrcaRouter-Lite). The same agent code as OpenRouter —
  chosen via env: if `ORCAROUTER_MODEL` (or `ORCAROUTER_URL`)
  is set, the agent talks to OrcaRouter, otherwise OpenRouter. New: template
  `orcarouter.json` (same rootfs), settings fields `ORCAROUTER_API_KEY`
  (secret, 0600, via the broker) + `ORCAROUTER_URL`, `ORCAROUTER_MODEL` in
  MODEL_KEYS, a secret-policy entry. **Set the key: Settings tab.**
- **Playbooks — fixed rules the orchestrator learns itself.** Unlike
  the semantic memory (surfaced by meaning), playbooks apply
  ALWAYS. When the user says HOW something is to be done / corrects the approach, the
  agent stores it via `playbook_add`; all rules are lifted into the prompt every turn.
  Tools `playbook_add`/`playbooks`/`playbook_forget`, store
  `playbooks.json` (per instance, cap 40), guest routes. Proven: rule "stock prices
  via http_fetch from Yahoo" → /reset → vague question → answered correctly, without
  naming the source again.
- **Reasoning controllable + visible.** Slash `/reasoning [low|medium|high|off]`
  toggles the OpenRouter reasoning parameter live (default off, via env
  `OPENROUTER_REASONING` as a persistent default). The thinking is streamed separately
  (markers in the token stream, kept out of the context) and shown in Web and App as
  an expandable "Thinking" block. Copy/read-aloud take only the answer.
- **Task management for the orchestrator.** `list_tasks`/`delete_task`/
  `edit_task` (list with IDs, delete, change message/schedule). Only the
  orchestrator gets them — via a flag `TASK_ADMIN` that the manager injects
  deliberately; gated in the menu AND at the route (403 "orchestrator only").
- **Signal: receiving + json-rpc.** The gateway switched from `native` to `json-rpc`
  (receiving and sending can now happen simultaneously — in native mode a
  long-poll locked the account and blocked sending). The receiver in the manager rebuilt onto
  a stdlib WebSocket client (real-time push). A new Signal message
  → orchestrator triggered immediately, reply back via send_signal.
- **katfs file browser** in the Sharing tab (click through folders, view,
  download; read-only, admin routes). Now with a **share selector**: with
  multiple active browser shares, click one in the status panel → the tree
  below switches to exactly that share (addressed via a `share` id). Also new
  is **"Download all"** — the current folder is collected recursively
  and delivered as a ZIP (route `/api/katfs/zip`, helper `katfs_zip`, cap
  2000 files / 512 MB). The standalone **CLI share**
  (`dist/katfs-share`) already existed — it shares a folder without a browser.
  Node paths/bind configurable via env; a portable standalone bundle under
  `katfs-standalone/` prepared.
- **App 4.9–5.1:** text in bubbles selectable/copyable (SelectionContainer),
  a slash-command picker above the input line (when you type "/"), thinking display.
- **MSFT task repaired:** the daily task failed because the model
  didn't use `http_fetch` and wrongly said "can't read pages".
  Now a concrete instruction with a fixed Yahoo URL + field — delivers a real
  quote.

## 2026-08-17 (Orchestrator token runaway fixed)
- **Cause:** the heartbeat ran `every 1m` (1,440 runs/day), and the
  orchestrator never reset its conversation context — heartbeat and
  app chats share ONE `_history`, which grew monotonically. Every call resent
  the whole accumulated transcript: on average 73k input tokens, largest
  call 183k, at ~5 tokens output. Total: 249M tokens, ~14 USD.
- **Fix 1 — cadence:** heartbeat from `every 1m` to `every 30m` (factor 30).
- **Fix 2 — stateless:** heartbeats now run with `/fresh` in a
  THROWAWAY context (`_tool_loop` on a local list), the conversation
  `_history` stays untouched. A hard reset was out of the question because it would have wiped
  a running app chat (shared `_history`). Both switched
  over: the scheduled task AND the instant trigger (`ORCH_HEARTBEAT_MSG`).
- **Fix 3 — cap:** a general sliding context cap (`CTX_MAX_MSGS=20`,
  cut only at clean turn boundaries, never mid tool cycle) — binds
  the cost for long direct chats too.
- Proven: 42+8=50 → /fresh heartbeat ("nothing to do") → "previous result times
  two" = 100 (context survives the heartbeat, which still runs stateless).
- Memory instruction defused: it made the orchestrator too meta
  ("my memory is reset after every interaction" — misleading,
  the short-term works). Now: remember normally within a conversation
  and don't explain the memory mechanics unprompted; quietly store
  only the durable via memory_store. Proven: "remember the test word Alpha" →
  confirmed without a lecture; "what was my test word?" → "Alpha".

## 2026-08-16 (Semantic memory: short-term + long-term)
- **Short-term** stays the running conversation (`_history` in VM RAM, `/reset`
  clears it). **Long-term** is now **semantic** instead of flat key/value:
  `memory_store` embeds every note (model multilingual-e5 on the CPU,
  a new `embed` container behind the manager on :8772) and stores text+vector in
  `history.db`. On every question the agent embeds the user message,
  the manager returns the **semantically nearest** notes (cosine), which come into the prompt as a
  fresh `[memory]` block — only what fits the question,
  not the whole store.
- **No LLM, no graph DB needed** — embeddings run well on the i5, so
  without waiting for the llama box and without Neo4j & co. (Graphiti/Cognee remain
  a possible later expansion.) If the embedder fails, there is no
  long-term context this turn instead of an error; the notes stay
  stored.
- Manager: table `semantic_memory`, `sem_store`/`sem_search`, guest route
  `POST /api/memory-search`; `memory_store` additionally writes semantically.
  Agent: injection per turn instead of a full dump on the first turn; an instruction to remember
  the value as a complete sentence (otherwise the retrieved snippet is
  useless — "… is the Watzmann", not just "Watzmann").
- Proven end-to-end: remember a fact → /reset (short-term gone) → ask in different words
  → correctly recalled (score 0.856, purely from long-term storage).

## 2026-08-16 (Passing slash commands through — App 4.8 + claude bridge)
- The app used to intercept EVERY /command and rejected unknown ones as "unknown
  command" — the agents' own commands were thus unreachable.
  Now the app keeps only its own (`/task`, `/agents`, `/help`), intercepts
  `/login` with a hint (not needed, the agent is logged in via the host)
  and **passes everything else through to the agent**.
- With that `/reset` (a new context without a VM restart) takes effect immediately for the
  OpenRouter/llama agents (they've long been able to) — and now also for the
  claude template: `/reset` retrofitted into the web bridge (clears the
  Claude Code session). Proven: set a code word → /reset → the agent no longer knows it;
  claudy answers "New conversation", orchestrator "Context
  reset".
- The app's `/help` now states that other /commands go to the agent.

## 2026-08-16 (Behavior guardrails from Anthropic's system prompts)
- Adopted, in spirit, the model-agnostic parts of Anthropic's published
  system prompts into the base prompt of the OpenRouter/llama agents
  (`agent.py`, applies to every model and to personas too):
  - **Don't hallucinate:** on uncertainty say so openly and check with web_search/
    http_fetch instead of guessing; no invented sources/citations/links.
  - **Tools first, then "can't":** before the agent claims incapacity or
    a lack of access, it checks whether a tool exists for it — act
    yourself before asking.
  - **Unclear requests:** make a reasonable assumption and get going, ask back only on
    a real blocker; finish tasks that were started.
  - **Tone:** factual, no flattery/exaggerated apologies,
    friendly reasoned disagreement instead of caving, no empty
    filler words ("honestly", "actually").
  - **Form:** concise and in prose; lists/bolding/headings only on
    real need or on request; no speculation about others' intentions.
- Not touched: the coding agents claude/pi/prime — they have their own, well-
  tuned prompts. Proven on a hallucination test (a fictional premise
  is named, not confirmed).

## 2026-08-16 (Self-hosted LLM via llama.cpp)
- **Settings tab:** new fields `LLAMA_ENDPOINT` (OpenAI-compatible base URL,
  e.g. `http://10.0.0.50:8080/v1`) and `LLAMA_API_KEY` (optional, only if
  the server runs with `--api-key`). The key is a secret (masked, via
  the broker, never into the instance config).
- **New template `llama`** (uses the existing openrouter rootfs — llama.cpp
  is OpenAI-compatible, same agent code). The endpoint is prefilled from the
  settings, the model name is a free-text field (whatever the server
  serves). Model chip and model switching work (`LLAMA_MODEL` in
  MODEL_KEYS).
- **Agent:** if `LLAMA_ENDPOINT` is set, it talks to the local server instead of
  OpenRouter — same tool loop, different base URL/model/key.
  URL normalization accepts `host:8080`, `…/v1` and `…/v1/chat/completions`.
  If the key is missing, it runs without auth (for llama.cpp without `--api-key` the
  normal case, not an error). Error messages and the startup log now name the
  active backend.
- **LAN gating:** if the llama server is on a private LAN IP, the
  manager opens exactly that target in the instance's FORWARD chain — like the
  MCP endpoints. Proven end-to-end against an OpenAI-compatible stub
  (settings → prefill → agent → backend → SSE streaming, app and
  web path).

## 2026-08-15 (claudy: subscription login + raw JSON in chat fixed)
- **"Not logged in":** the claude template runs Claude Code internally and
  had no login. A new guest route `GET /api/claude-credentials` (claude
  template only, by source IP): the guest fetches the **live**
  `claudeAiOauth` block from the host at boot — thereby automatically following the user's next
  `/login`; the short-lived accessToken is renewed per session by Claude
  Code itself (refresh token stable for 20 days). Only the subscription block is
  delivered, not the user's mcpOAuth tokens; the host file stays
  untouched. For non-guests 403.
- **Raw `{"reply": …}` in the app chat:** the claude bridge can't stream
  and answers with JSON — the app showed it bare including `·`. The
  manager proxy now, on JSON responses to `api/chat[/stream]`, unpacks the
  `reply` and passes it on as text/plain (as the web chat has long
  done). Proven: app path and web path deliver clean text, claudy answers
  logged in ("Yes, I am logged in and ready").

## 2026-08-15 (MCP hub on the host + LAN gating for the guests)
- **Finding first:** with `internet=on` every VM could reach the whole LAN — Home
  Assistant and Portainer were reachable by every agent, whether or not the MCP
  was assigned to it. Now a dedicated FORWARD chain per instance:
  MCP endpoints of the assigned servers (from the catalog) → ACCEPT, guest DNS
  (1.1.1.1:53) → ACCEPT, private networks → **REJECT** (immediate
  failure instead of a 30-s timeout), internet → ACCEPT. Proven in four directions
  (orchestrator↛HA, orchestrator→internet, hass→HA, hass↛Portainer).
- **MCP hub** (`mcp-hub/`, Docker, 127.0.0.1:8771): the MCP server processes
  now run ONCE per (instance, server) on the host instead of in every VM. Guests
  only speak JSON-RPC via `POST /api/mcp`; the manager authorizes
  by source IP against `MCP_SERVERS`, injects the secrets host-side and
  passes through to the hub. **Tokens and the LAN no longer reach the VM**;
  `/api/mcp-config` now delivers secrets only as placeholders. Every
  `tools/call` is in the audit (guest and manager side). The hub restarts dead
  processes and repeats their initialization; on instance stop their
  processes are terminated.
- Guest image: `mcp-portainer` removed (−104 MB); `mcp-remote` stays as a
  fallback for managers without `/api/mcp`. Proven end-to-end: hass queries
  the live state via the hub ("One light is currently on"),
  orchestrator gets a rejection for unassigned servers.

## 2026-08-15 (Architecture tab + one SVG parser trap)
- New tab **Architecture** next to the changelog: a diagram (SVG, follows the
  light/dark theme) plus 15 reference cards for all components including
  security boundaries.
- Two traps along the way, both only visible in real Chromium (jsdom forgives
  them): a `<style>` element **inside** the inline SVG ends the SVG context on HTML
  parsing — everything after it falls out invisibly; and with unquoted
  attributes `height=56/>` eats the slash into the value
  (`height="56/"` → the element draws nothing). Rules into the page CSS, attributes
  quoted, verified via headless screenshot.

## 2026-08-15 (Memory: instruction + injection; an ext4 incident with consequences)
- **Every system prompt** (personas too) gets a standing memory instruction
  appended in agent.py: remember important things immediately via `memory_store`,
  update existing ones, keep no log. On the **first turn after
  a restart** the agent injects its remembered facts into the
  prompt itself (not at boot — the guest network may not be up yet).
  Proven end-to-end: remember → VM restart → correctly recalled.
- **Incident during verification:** `EXT4 error loading journal` at boot.
  Cause: all instances of a template shared **the same**
  writable rootfs file — two running VMs, one journal. Fixed: each VM
  gets its own **fresh copy** at start (sparse, ~550 MB, deleted on stop);
  master image repaired with e2fsck. Side effect: a restart
  boots the current template image for sure.

## 2026-08-15 (Model switch for existing instances)
- The model chip in the instance row is now a button: a dialog with the same
  picker as at creation (OpenRouter shortlist, ⟳, free text). New route
  `POST /api/instances/<n>/model` + `set_model()`; effective after stop/start.
  Automatically locked for guest VMs (not in the POST allowlist).

## 2026-08-15 (Signal sending for agents)
- New tool **send_signal**: agents can write to the user
  (finished task, finding, follow-up question). Sending runs in the **manager**
  (`/api/signal` → signal-cli REST); the bot number and API stay in the host.
- **Leash:** recipients must be in `ALLOWED_SENDERS` — an agent can
  only write to people who are allowed to give it commands anyway. Plus throttling
  (10 per 5 min, rejections don't count) and an audit entry per call.
  `SIGNAL_API` new in the settings.

## 2026-08-15 (Security gateway, toggleable per chat)
- A shield icon in App and Web, state at the manager (`gateway.json`), filtering
  too — a guest VM can't turn it off. Removes, in **both**
  directions, invisible Unicode characters (tag characters U+E0020–E007F,
  zero-width, bidi overrides, homoglyph spaces) and from uploads
  EXIF/XMP/C2PA (JPEG/PNG/WEBP, byte-surgical, image data untouched).
- The stream filter cuts at word boundaries — emoji ZWJ chains survive,
  a smuggled command spread across chunks does not (test: 67 characters
  removed, the model answers the visible question). What was removed is counted
  visibly. Basis: `text_unicode.py` from watermarks-remover (MIT),
  **only** the Unicode layer — the project's watermark/C2PA removal
  stays deliberately out (SECURITY-GATEWAY.md).
- Without a `chat` field in the request everything stays as before — older clients run
  unchanged.

## 2026-08-15 (KatAgent 4.3–4.7: hands-free voice, assistant, small things)
- **4.3:** speech-pause detection — recording ends by itself (threshold from
  the room noise of the first tenths, stop only after speech has occurred);
  tapping the bubble stops the output (during which the whole bubble is the
  stop button), new voice input aborts it (a generation counter against
  synthesis arriving late).
- **4.4:** gateway toggle in the header, counter in the sync line.
- **4.5:** the version number next to the name in the drawer header (from the
  installed package, not BuildConfig).
- **4.6:** the app registers as a **digital assistant** (ASSIST/VOICE_COMMAND,
  singleTask): a long press on the power button starts recording immediately.
  Deliberately not over the lock screen. Assign it under Settings →
  Default apps → Digital assistant.
- **4.7:** trailing time before sending 1.2 → 1.8 s.

## 2026-08-15 (Voice stage 2: the app hears and speaks — KatAgent 4.2)
- Microphone button in the input line: record (AAC/M4A, 16 kHz mono), send to the
  manager, **submit the recognized text directly, hands-free**. New
  permission `RECORD_AUDIO` with a runtime request.
- A speaker under every agent response; **only what was asked by voice is
  read aloud automatically**.
- `ManagerSync.stt()` and `.tts()` talk to the manager routes from stage 1
  (basic auth like the rest of the traffic, 120 s read timeout).
- Recordings under 2 KB are discarded ("too short") instead of sending an empty
  recognition.
- Two Kotlin traps during the build: `send()` is a local function and must not be
  called before its declaration (now via a flag that a
  LaunchedEffect processes), and `VolumeUp` is not under `AutoMirrored`.

## 2026-08-15 (Voice: stage 1 — service, manager routes, microphone in the web)
- A new **voice service** (`voice/`): Parakeet TDT v3 (ONNX int8) for
  recognition, Piper with the Thorsten voice for output, plus ffmpeg for
  format conversion. One host container, loopback, ~700 MB of models once in
  memory instead of per microVM.
- The manager passes it through as `/api/stt` and `/api/tts` — the only door to the
  outside. Both are in the guest allowlist, so that later agents
  (e.g. the Signal bridge) can transcribe too.
- Web interface: a microphone button in the input line (recording in the browser,
  recognition in the manager, submitted directly hands-free) and a
  "Read aloud" per response. **Only what was asked by voice is read
  aloud** — otherwise it reads long explanations unprompted.
- Measured through the manager: speaking 0.38 s, recognizing 0.31 s; in the round trip
  6.5 s of audio produced in 0.67 s and recognized again in 0.61 s.
- A trap along the way: binding to 127.0.0.1 inside the container makes the service
  unreachable — Docker's port forwarding doesn't know the container loopback.
  The restriction belongs on the host side of the mapping.

## 2026-08-14 (Security review: a VM could mount the host file system)
- **Critical, fixed.** The GET routes were locked against guests, the
  writing ones **not**: an agent VM reaches the broker at the gateway (where
  it fetches its secrets) and could thereby call, among others,
  `POST /api/instances/<n>/mounts`. The manager runs as root and
  exports the requested folder via NFS into the guest — a compromised
  VM could thus mount `/` writable. Likewise open:
  `/api/create` (new instance with arbitrary mounts), `/api/settings`
  (ALLOWED_SENDERS/SIGNAL_NUMBER = control channel), `/api/tasks`, `/api/personas`,
  `/api/instances/<n>/{delete,internet,tools,start,stop}`. The secret allowlist,
  tool gating and egress rules were thereby circumventable.
- Fix: an **allowlist** right at the top of `do_POST` — guests may only reach `/api/usage`,
  `/api/audit`, `/api/task`, `/api/chat-log` and `/api/memory/…`, everything else
  403. Deliberately an allowlist instead of individual checks: a new route is then
  closed by default, not open by default.
- Verified: admin routes still 200, a real guest message still arrives
  (usage counter 45 -> 46 after a prompt).
- **Stored XSS, fixed.** In the server-side rendering there was not a single
  HTML escape. The model id (arbitrary since the free text field), description,
  mount paths and tool list landed raw in the markup of the admin page. A new
  `h()` (html.escape) in front; proven with a prepared instance: 0 raw,
  4 neutralized occurrences.
- **Open (configuration, not code):** `MANAGER_PASS` is empty, `_auth()` then lets
  everyone through. Protection hangs solely on Traefik — whoever reaches 10.0.0.10:8700
  directly is admin. See the note below.

## 2026-08-14 (Stale views: Tasks, Policy and usage catch up)
- **Finding from practice:** in the Tasks table the last result still showed
  a DNS error from 08:08, while `tasks.json` had long since carried "Nothing to do."
  and a successful run at 17:08. The table only loads
  once when the page is opened — whoever left the tab open saw arbitrarily old
  states and thought a fixed problem was current.
- Tasks and Policy now catch up every 15 s, but only for the visible
  tab and not in a background tab (`document.hidden`).
- The usage rows are no longer only server-side rendered: they carry
  a `data-usage` and are updated from `/api/usage`, as is the total in
  the footer. So the counter grows as you watch.
- On the side: the host's timezone was on `America/Chicago` (UTC and NTP
  were correct, only the zone wasn't) — 7 hours of offset in `daily HH:MM`,
  in the day boundary of the usage counter and in all server timestamps.
  After `timedatectl set-timezone Europe/Berlin` the manager needs a
  restart, glibc reads the zone only once per process.

## 2026-08-14 (Rest of the surfaces checked against Industry)
- **The web chat (`/chat`) was the biggest hole**: its own palette with an orange
  accent (#e8590c), 14 px rounding, system-ui. Now Industry tokens under the
  same variable names — slate blue, Barlow/Barlow Condensed, angular,
  hairlines. Own message filled like `.btn-primary`, reply as a card
  with a hairline — the same layout as in the app.
- **Finding in the manager:** `.radio .dot` was angular (2 px). In the system the
  radio dot is explicitly **round** (`border-radius: 50%`) — corrected. The
  status dot in the chat stays round for the same reason.
- **Browser terminal**: the console stays dark (there is no
  system component for it, and a light terminal background would be worse), but the bar
  and buttons now follow the dark band, slate blue, angular, Barlow.
- **Open: katfs share page** (`katfs/web/index.html`) — its own dark
  palette with blue/green, rounding 5/10/50 %, system-ui. Not touched,
  because the WASM bridge hangs there too.

## 2026-08-14 (App follows the "Industry" design system)
- Tokens read directly from the project (`DesignSync get_file` on
  `theme.json` + `styles.css`) instead of rebuilt — the app had so far only
  adopted colors and font family.
- **Angular without exception**: at the end `styles.css` overrides the `radius:4` from
  `theme.json` with `border-radius:0` for Card/Button/Input/Tag/Dialog. Shapes
  to 0, circular avatars and mode symbols as well as the 2/3/8-dp rounding removed.
- **Type scale 1:1**: h1 42 / h2 32 / h3 25 / h4 20 / h5 16, headings with
  line-height 1.12 and -0.015em, body 15/1.55; `labelSmall` now carries the
  h6 role (13 px, uppercase, 0.08em) instead of Material's default.
- **Grid** from `density: 0.85` as `IndustrySpacing` (3.4/6.8/10.2/13.6/20.4/27.2).
- New `IndustryComponents.kt`: `blueprintFrame()` draws a hairline plus the
  four register marks *outside* the box (in CSS `.corner tl|tr|bl|br`),
  plus `BlueprintBox`, `Tag` and `IndustryDivider`.
- **Chat bubbles** without Material tinting: own message filled like
  `.btn-primary`, the agent's reply transparent with a hairline like `.card`.
- Dark mode stays (the phone is used at night) — in the system it is not
  foreseen as `band: light`, so here a deliberate extension.
- KatAgent **3.9** (versionCode 39).

## 2026-08-14 (Fix: question from the app disappeared)
- **Regression from the live sync (3.7).** The merge replaced conversation
  objects (`byId[id] = r`). But `send()` holds a reference to
  `current.messages` and streams the reply there — if the object was
  swapped, question and answer landed in a detached list:
  gone from the app, never saved, never pushed.
- The merge **now fills the existing object** instead of replacing it, and
  messages are only **appended**: only if the local list is a prefix of
  the remote one is it adopted. So a newer state from the
  other side (Web/Manager, possibly with a leading clock) can no longer wipe a just-typed,
  not-yet-pushed question. The open conversation
  stays entirely untouched during a running turn.
- The same safeguard in the web interface (`chatui.py`) — there
  `send()` holds the reply object just the same.
- KatAgent **3.8** (versionCode 38).

## 2026-08-14 (Token counter per instance)
- A new table `llm_usage` in `history.db` and guest route **`POST /api/usage`**:
  the agent reports tokens and cost after every LLM call, the manager books
  them onto the instance. The attribution comes from the **source IP**, not from the
  body — one VM can't fake another's usage.
- `GET /api/usage` (admin, locked for guests) delivers per instance today and
  total: calls, tokens in/out, cost.
- The instance table shows the row under the model chip, the footer the
  total across all instances.
- `agent.py`: requests `usage:{include:true}` (so OpenRouter sends along the real
  cost per call), reports streaming as well as non-streaming, and
  `OPENROUTER_URL` is now settable via env. Reporting is fire-and-forget: if
  the manager is down, it doesn't disturb the chat.
- Rootfs `openrouter` rebuilt; verified: a prompt to the orchestrator
  booked 1313/29 tokens and $0.0005.
- Limit: only the `openrouter` template reports. `pi` and `prime` call their
  own CLIs, `claude` bills via the subscription — there are (still) no
  numbers there.

## 2026-08-14 (Guests without internet: HOSTIF pointed nowhere)
- **Finding:** since the reboot at 03:07 no microVM reached DNS or the LLM
  (`gaierror -3`) anymore, and the orchestrator heartbeat also ran into nothing. The cause was
  not the VM, but the host's NAT rule: the unit set
  `Environment=HOSTIF=eth0`, but the uplink is called `eth0`. MASQUERADE on
  a non-existent interface hits nothing — the guests' packets went out
  unmasqueraded and never came back. No log, no error message.
- **Fix:** `manager.py` no longer relies blindly on the name. A
  set `HOSTIF` applies only if `/sys/class/net/<name>` exists; otherwise
  the interface of the default route wins (with a hint in the log). The unit template
  now has the line commented out.
- Verified: after a manager and instance restart the orchestrator answers in
  1.6 s ("pong") instead of after a 20 s DNS timeout.
- Note: the installed unit under `/etc/systemd/system/` still carries
  `HOSTIF=eth0` (root-only). Thanks to the detection this is harmless, but should
  be cleaned up at some point.

## 2026-08-14 (Chats live between app and web)
- `/api/chats` can now **long-poll**: `?since=<rev>&wait=<sec>` blocks until
  someone writes, then answers with `{rev, chats}` (on timeout
  `chats:null`). Without the parameters the plain list unchanged — old clients
  keep running. Every write to the store increments a time-based monotone
  revision and wakes all waiters (`threading.Condition`).
- The web interface hangs permanently on this long-poll: new messages from
  the app appear without reloading (measured ~50 ms after the push). The currently
  streaming chat is skipped in the merge so no partial text is lost;
  the push after local changes was shortened from 1200 to 400 ms.
- On the side: the guest lock for `/api/chats` & co. compared the full path
  and would no longer have recognized a query string — it now compares the path
  without the query.
- **The app catches up (KatAgent 3.7, versionCode 37):** a new
  `ManagerSync.pollChats()` (long-poll with a suitably raised read timeout) and
  a continuous loop in `MainActivity` instead of the one-off `sync()` at start.
  While a reply is streaming (`busy`) nothing is merged; the merge writes
  only locally (`store.save`), so it doesn't push back -> no ping-pong.
  With that the sync is live in both directions. APK: `katagent-3.7.apk`.

## 2026-08-14 (Rebrand: kAIm56)
- **"Firecracker Manager" is gone from the UI — the product is `kAIm56`.**
  Renamed in the page title, the topbar wordmark, the footer, the chat UI's
  back-link, the basic-auth realm, the outgoing User-Agent and the startup log.
- New **`logo.svg`** (hexagon + triangle with side midpoints, incircle and
  centroid) served at `/logo.svg` and `/favicon.ico`, used as the browser-tab
  icon on both the manager and the chat page. The header mark inherits the text
  colour (`currentColor` for the navy) so it carries in light and dark theme;
  the teal stays the accent.
- The topbar no longer prints the hostname next to the wordmark (that was
  `location.host`, i.e. `agents.example.com` — never a hardcoded string). Header is
  now mark + name only.
- Unchanged on purpose: the `firecracker-manager.service` unit, the `~/firecracker`
  directory and the Firecracker binary path — those are the hypervisor, not the brand.

## 2026-08-14 (Model dropdown + UI now fully English)
- **Every template can now pick its LLM.** The `claude` template had no model
  field at all — new param `ANTHROPIC_MODEL` (dropdown: account default,
  `opus`/`sonnet`/`haiku` aliases, or a pinned id). It reaches Claude Code as an
  env var via the config disk (`set -a; . /config/config.env` in guest-init), so
  it applies to the signal, web and mail transports without a rootfs rebuild.
  Select options may now be `{value,label}` pairs, not just strings.
- **Model as a dropdown** for the `pi` and `prime` templates: `PI_MODEL` /
  `PRIME_MODEL` are `type: select, source: openrouter` (tool-capable models from
  the shortlist), just like `OPENROUTER_MODEL` — no more free-text typing.
- Every model dropdown gained **"— other model id… —"**, which swaps the select
  for a text field (direct Anthropic/OpenAI ids are not in the OpenRouter
  catalog); the ⟳ button switches back to the list. A stored value outside the
  shortlist stays selected instead of silently falling back to the first entry.
- **UI and API messages are English throughout** — create form, Policy, Tasks,
  Security/Changelog, the activity dialog, the instance rows, the chat UI
  (`chatui.py`) and every `msg` the API returns. Code comments stay German.

## 2026-08-14 (Instant trigger for the orchestrator)
- A new user message (Signal via /api/chat-log, App/Web via /api/chats)
  pokes the orchestrator **debounced (8 s)** — it reacts in seconds
  instead of only at the 2-h heartbeat. Coalesces bursts, one run at a time, fires only
  on genuinely new inbox (peek). Pure manager code, no rootfs build.
- Verified: message -> ~15 s later orchestrator auto-started ->
  read_inbox -> create_task(target=remote) for the request. The 2-h heartbeat
  stays as a fallback (time-based/recurring checks).


## 2026-08-14 (Orchestrator inbox: Signal/chat store as the source)
- A new tool **`read_inbox`** (guest route `/api/inbox`): new user messages
  from the shared chat store (Signal/App/Web) since the last run, with a
  **watermark** (each message only once; ?peek=1 = preview without consuming).
  Task conversations hidden.
- The orchestrator's heartbeat now reads the inbox first. Verified:
  a new Signal message "check basement door 22:00" -> read_inbox -> recall_tasks
  -> list_agents -> create_task(target=hass, daily 22:00). Correct routing to the
  capable instance, inbox consumed afterwards.


## 2026-08-14 (Orchestrator heartbeat "Lloyd")
- A new tool **`list_agents`** (guest route `/api/agents`): agent roster +
  capabilities (model/MCP) for routing — without secrets.
- **`orchestrator` persona** created: manages work instead of doing it —
  list_agents -> recall_tasks (duplicate check) -> create_task to the CAPABLE
  instance (or ephemeral). From existing building blocks, hardly any new code.
- **`orchestrator` instance** (gemini-2.5-flash) + a recurring **heartbeat
  task** (every 2h). The worker wakes the instance for the run and leaves it
  running afterwards.
- Verified: a heartbeat with a concrete occasion -> recall_tasks (no dupl.)
  -> create_task(daily 08:00) created correctly.
- Open: the heartbeat can so far use Web/HTTP and delegate to capable agents
  (e.g. hass), but NOT YET read host logs/email/the Signal chat store
  — for each of those it needs a small source tool (next step).


## 2026-08-14 (Queryable task history / base knowledge)
- A new **SQLite history** (`history.db`, stdlib — no dependency): every
  executed task (worker + synchronous create_task) is stored with target, task,
  result, ok, schedule, origin. WAL mode, thread-safe.
- Agent tool **`recall_tasks(query, limit)`** via `GET /api/history` — agents
  query the past ("have we done this already?"), BEFORE create_task
  against duplicates. So the (upcoming) orchestrator has its base knowledge.
- Verified: create_task -> result in history; recall_tasks(query) finds the
  run again (target/task/result + time).
- Idea adopted from the Symphony discussion (queryable memory), NOT the
  coding-specific Symphony itself.


## 2026-08-14 (Tasks from the VM: create_task)
- A new agent tool **`create_task(task, target, schedule, wait)`** — callable from any
  VM (gated via AGENT_TOOLS). Manager guest route `POST /api/task`.
- **Routing instead of a VM per task:** `target=<instance>` runs the task where
  its tools/MCP/secrets live (e.g. `hass` for Home Assistant);
  `target=ephemeral` spins up a fresh, isolated VM and tears it down afterwards.
- **Sync/async:** `wait=true` blocks and delivers the result directly;
  otherwise it runs in the background worker and the result lands in the
  shared chat history (`task-<target>`) — visible in App/Web/Signal.
- `schedule` (every Nm|Nh|Nd, daily HH:MM, hourly) for recurring tasks.
- Runaway protection: ephemeral children (task-*/sub-*) may not create tasks
  themselves. `spawn_subagent` poll tightened from 3 s to 1 s.
- Verified: ephemeral sync -> `TASKOK`; async -> worker done + chat entry;
  no ephemeral corpses. v1 serial (one task at a time) — parallelism
  with an N cap is the next step.


## 2026-08-13 (Signal into the shared chat sync)
- Signal messages used to run only in the VM's signal_loop to the Signal API and
  never landed in the shared store. Now every bridge turn (question+
  answer) reports to **`POST /api/chat-log`** (guest recognized by source IP); the manager
  appends it as a conversation `sig-<instance>-<sender>` to `chats.json`. So
  Signal chats appear in **App and Web** like normal chats.
- Implemented in all three bridges (openrouter/pi/prime), images rebuilt.
  Verified: guest POST -> a `Signal · remote` conversation in the store.
- Limit: the **claude** Signal bridge (claude-signal-firecracker) is a
  separate code state and NOT yet connected.


## 2026-08-10 (Audit + Policy view)

### Audit log per instance
- The agent reports every tool call to the manager (`/api/audit`,
  guest recognized by source IP) — **tool + target field** (URL for http_fetch, query
  for web_search, path for file/katfs tools, command head for bash) and an
  ok flag. **Never** secret values or file contents (get_secret logs only the
  name). Lands as `audit/<name>.jsonl` **on the host** — survives
  VM restarts, capped to the last 2000 lines. `GET /api/audit/<name>`
  is admin-only. Verified: `http_fetch -> https://example.com`,
  `web_search -> bumblebee`; a guest gets `forbidden` on reading.

### Policy tab
- One view **per instance** that pulls the scattered controls together:
  internet (live toggle), model, **an editable tool allowlist**
  (`POST /api/instances/<n>/tools`, effective after stop/start), allowed secrets,
  MCP servers, katfs share — plus an **Activity** button with the last
  tools/URLs called from the audit log. `GET /api/policy` (admin-only).

### Instance table
- Each row now shows the **model in use** (a chip instead of an emoji).

### Chat UI
- `/chat` sends `Cache-Control: no-store`; icons as inline SVG directly in the HTML
  (the grey emoji boxes were cached old pages + a missing emoji font).


## 2026-08-10 (Addendum: pi/prime fix)
- The credential switch had mined pi/prime: keys were stripped from the config,
  but only openrouter-agent fetched them via the broker. Now
  **pi and prime** also fetch missing provider keys at start from the broker
  (`ensure_provider_keys`), policy added for both templates. Verified
  (pi -> `piok` from the LLM, prime -> key from the broker, nothing on the config disk).
- Both build scripts got the same fixes as openrouter: `mkfs.ext4` PATH
  and an **atomic** rootfs install (mv instead of cp into the running file).


## 2026-08-10

### Agent capabilities (controllable per instance)
- **Internet toggle.** A new instance option `internet` (default on). Off =
  the VM may not leave its own /30: no LAN, no web — and thereby
  **no LLM**, the agent then can't think (labeled so in the UI).
  The manager broker at the gateway (8700, host-local) stays reachable.
  Toggleable **live** via the instance table (🌐/🚫), without a restart —
  `apply_internet` sets/removes the egress rules of the tap.
- **Tool allowlist.** A new option `tools` at creation (a checkbox list from
  `/api/agent-tools`). Lands as `AGENT_TOOLS=<names>` in the config; the agent
  filters with it both **schema AND execution** (`tool_enabled`) — a model can neither see
  nor force a disabled tool. Verified: a subset -> boot
  reports `tools=3` instead of 17, `list_dir` not present in the model.
  Note: `bash` is the master key — whoever really wants to lock file/web
  must also deselect `bash`.

### Chat UI

### Tasks tab
- The scheduled work (backend + worker + `tasks.json` had long existed, just without
  a UI) now has a **"Tasks" tab**: a job = a message to an
  instance, one-off or recurring (`every Nm|Nh|Nd`, `daily HH:MM`,
  `hourly`). A list with status/next run/last result, create
  and delete. A stopped instance is started for the run. `/api/tasks`
  is admin-only (guests blocked).

- Color emoji (📎 🖥️ 🔄 🤖) swapped for inline SVGs. Without an installed
  emoji font they appeared as grey boxes (tofu) — now the icons
  render everywhere. (Reported: a grey box next to the send button = 📎.)

### Diagnostics
- "gemma can't search the internet" was **not** a network problem (egress 200):
  the free gemma model simply doesn't call `web_search`, but talks
  about searching and leaks `<|channel>` tokens. Pick a stronger tool-capable model
  (Models tab) or force the tool in the prompt.


## 2026-08-09 (Security scan Agents + katfs)

### katfs — cross-tenant hole closed (critical)
- The node bound to `0.0.0.0:8790` and **never checked the caller**. Every
  microVM could enumerate `/shares` and, with `?share=<any>`, read/write/delete every active
  share — even that of another operator. Proven
  in the test: instance `remote` read `id_rsa` from a foreign share and wrote
  a backdoor into it.
- Fix: the node now binds only **127.0.0.1**. Guests go via the
  manager broker (`/api/katfs/ls|read|write|delete`), which recognizes the instance by
  source IP and enforces the share from **its** config. `&share=`
  from the guest is ignored. Write capped at 64 MiB. The agent no longer calls
  the node, but the broker.
- Verified from the attacker's position (a compromised agent with a shell):
  direct access to the node → connection refused; a foreign share via
  `&share=` → unreachable; its own share → works.

### Net — guest isolation
- microVMs could route among each other; an agent reached the unprotected
  ports 8080/7682 of another instance. Fix: tap ACCEPTs restricted to non-pool
  targets + `pool->pool` DROP. Test: `remote` → `gemma:8080` runs into a
  timeout (blocked), internet egress unchanged (200).

### Manager — further guest leaks closed
- `/api/chats` and `/api/tasks` added to the guest guard.
- `/api/memory/<instance>`: for guests the name comes from the source IP, not
  from the path — no more foreign memories.


Changes to the manager and the surroundings (katfs, openrouter agent). Newest first.

## 2026-08-09

### Credentials
- **API keys removed from the instances.** `SECRET_PARAMS` is discarded at creation and
  when building the config disk; the agent fetches `OPENROUTER_API_KEY` at
  runtime via `/api/secret/<name>`. The manager recognizes the guest by the
  source IP and checks the allowlist from `secret-policy.json`. Existing state is
  cleaned up at start.
- **`/api/settings` sealed.** The route handed out the keys in cleartext to every
  microVM. Now a guest guard on `/api/settings` and `/api/instances`,
  and set secrets go out only as `__unchanged__`.
- **MCP tokens removed from the instances.** Instead of `MCP_CONFIG` with inserted
  values, `MCP_SERVERS=<names>` is stored; the agent fetches the
  configuration via `/api/mcp-config`, and the manager inserts only the secrets released for
  this instance by policy. A migration lifts the server names out of
  the old blob and enters the necessary secrets per instance.
- `settings.json` to 0600.

### katfs
- **Multiple shares at once.** The node used to hold exactly one; each
  new one displaced the old one, two sharing machines flapped against each other. Each
  share now reports at `hello` a stable **share-id** plus folder name,
  platform and `readonly`. `GET /shares` lists them, `?share=<id>` selects.
- **Selection at creation.** In the create form the share stands as a dropdown,
  the value lands as `KATFS_SHARE` in the instance config.
- **`katfs-share`** — the provider as a native program (`iroh-fs/client/`).
  Solves the Firefox/Safari problem: there is no API there that writes into a real
  user folder. Stable share-id from hostname + path, auto-reconnect,
  `--ro`.
- **`delete`** as a fifth operation, with three locks: the root of the share,
  `..`, and non-empty directories without `recursive=1`.
- The share page passed through under **`/katfs/`** — same origin, same
  auth and thus HTTPS, which the File System Access API strictly requires.
  `?key=<node-id>` inserts a foreign node.
- Error messages now carry their cause (`Directory not empty` instead of just
  `delete <path>`), all the way into the agent too.

### Manager UI
- **Rebuilt** onto the design system *Industry*: Barlow, blueprint frames,
  tabs instead of one long page, hash-routed.
- **Folder browser** behind the 📁 in every mount row (`/api/browse`, only
  directory names, admin-only). The `prompt()` for existing instances has
  given way to a real dialog.
- **"Models" tab** — the full OpenRouter catalog live, from it the shortlist for the
  create form (`models.json`). Previously a constant in the source.
- **"Sharing" tab** — katfs status, shares, sharing key.
- `deepseek/deepseek-v4-flash-0731` added to the selection.

### Operations
- **The rootfs rebuild is atomic.** Previously `cp` into the target file — if a VM
  held it open, the next boot ended in an ext4 checksum panic. Now put beside
  and rehang via `mv`, plus a warning on a running instance.
- `KillMode=process` in the unit, so that a manager restart doesn't tear down all
  running microVMs. **Not installed yet** — see the open points.
- `node/build.sh` and `client/build.sh` for katfs (Docker, no local Rust).
- `build-openrouter-rootfs.sh` finds `mkfs.ext4` even without root in the PATH.
