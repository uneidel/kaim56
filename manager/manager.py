#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""kAIm56 — Manager (web UI + API) for 1..x microVM instances (Firecracker).

Runs as root (needs /dev/kvm, ip, iptables) under systemd. Pure standard
library. This file is the composition root and nothing else: it imports the
mgr package, wires the few cross-references the modules cannot derive
themselves, and starts the worker threads and the HTTP server. Everything
else lives in mgr/, one concern per module:

  security boundaries
    auth          login (Basic, OIDC sessions), the user-role route table, lockout, trusted Origins
    oidc          OIDC client (Rauthy): web code+PKCE, device flow, refresh; off unless OIDC_* set
    sessions      login sessions after an OIDC login (SQLite, token hashes only)
    guests        a VM is identified by its source IP; what a guest may reach
    secrets       the host secret store and the per-instance release policy
    policy        tool catalog, per-instance tool gating, sandbox config
    llmproxy      the key proxy: LLM keys never enter a VM; budget/rate guard
    guestproxy    admin-to-VM relays: chat stream, terminal, katfs, /i/ proxy
    netfw         taps, anti-spoof, host input rules, egress per instance
    mounts        guest squash user, per-instance NFS exports, mount deny list
    projects      one folder set joined by instances with a role (shared / worktree)
    projwt        the worktree strategy's git: a branch + folder per writer, diff, merge
    squads        a project's members talk in one channel; addressed members are woken (guarded)
    squadgate     the lead proposes (squad plan, member, merge, toolchain), the user approves; tickets
    toolchain     a squad's shared toolchain: built in a throwaway builder VM, attached read-only to members
    squadgit      a squad's git remote: push, fetch, PRs + issues host-side (token never in a VM)
    squadmetrics  one row per squad event in history.db (squad_events): turns, tokens, tickets, waits; backfilled from the archive
    hitlq         human in the loop: approvals, questions, reviews that wait for the operator
    router        model router: per-turn model choice via Jev (opt-in per instance)
    jevsetup      Jev suggests persona, skills and model tier for a new instance (only a suggestion)
    celldeploy    celld with a bucket: deploys the apps and the Worker to S3
    cloud         apps to Cloudflare and back, the app tokens
    cfapi         the Cloudflare Workers API (cfdo's upload path in Python)
    routes_guest  every HTTP route a VM may call
    routes_admin  every HTTP route only the admin may call
    routes_auth   /auth/* login and logout, device flow, who-am-I, session list
    routes_squadgit  /api/git/* (members) and /api/squad-git/* (admin)
  the VMs
    instances     instances/<name>.json, templates, network, running state
    vm            images, harness drive, write layer, start and stop
    tasks         task queue and worker, ephemeral VMs, orchestrator ping
    guestchat     talking to a VM's web bridge
    resources     CPU, memory and disk per instance
  HTTP
    routes        the route table (ROUTER), route helpers, body caps
    httpd         the handler class H: auth, guest gating, dispatch
    ui            the admin page (render) with ui_css / ui_html / ui_js
  data and integrations
    settings, about, models, chats, skills, personas, plugins, audit, voice,
    browse, store, memfs, hindsight, missions, rules, mcp, katfs, irohgw,
    signal, gateway, notify, saddler, websearch, extract, haalias
  small
    paths, host, util, startup

Conventions: an mgr module never imports this file. A module uses a sibling
as ``_name.func`` (module attribute, not ``from mgr.x import f``), so a test
can replace one definition in one place; the tests reach every module as
``manager._name`` — that is why every module is imported here, used or not.
"""
import os
import threading
from http.server import ThreadingHTTPServer

from mgr import about as _about              # noqa: F401
from mgr import aicheck as _aicheck          # noqa: F401
from mgr import apps as _apps                # noqa: F401
from mgr import audit as _audit
from mgr import auth as _auth
from mgr import browse as _browse            # noqa: F401
from mgr import checkout as _checkout        # noqa: F401
from mgr import cfapi as _cfapi              # noqa: F401
from mgr import chats as _chats              # noqa: F401
from mgr import claudeaccts as _claudeaccts  # noqa: F401
from mgr import celldeploy as _celldeploy    # noqa: F401
from mgr import cloud as _cloud              # noqa: F401
from mgr import decision as _decision        # noqa: F401
from mgr import gateway as _gateway
from mgr import guestchat as _guestchat      # noqa: F401
from mgr import guestproxy as _guestproxy    # noqa: F401
from mgr import guests as _guests            # noqa: F401
from mgr import haalias as _haalias
from mgr import hindsight as _hindsight
from mgr import hitlq as _hitlq              # noqa: F401
from mgr import host as _host
from mgr import httpd as _httpd
from mgr import indexer as _indexer            # noqa: F401  (registers the 'repowise' MCP hook)
from mgr import instances as _instances
from mgr import irohgw as _irohgw
from mgr import jevsetup as _jevsetup        # noqa: F401
from mgr import katfs as _katfs              # noqa: F401
from mgr import llmproxy as _llmproxy        # noqa: F401
from mgr import mcp as _mcp
from mgr import memfs as _memfs
from mgr import missions as _missions
from mgr import models as _models            # noqa: F401
from mgr import mounts as _mounts
from mgr import netfw as _netfw              # noqa: F401
from mgr import notify as _notify
from mgr import oidc as _oidc
from mgr import paths as _paths
from mgr import personas as _personas        # noqa: F401
from mgr import plugins as _plugins          # noqa: F401
from mgr import policy as _policy            # noqa: F401
from mgr import projects as _projects        # noqa: F401
from mgr import projwt as _projwt            # noqa: F401
from mgr import repoprofile as _repoprofile  # noqa: F401
from mgr import resources as _resources      # noqa: F401
from mgr import router as _router            # noqa: F401
from mgr import routes as _routes            # noqa: F401
from mgr import routes_admin as _routes_admin  # noqa: F401  (registers its routes in ROUTER on import)
from mgr import routes_auth as _routes_auth  # noqa: F401  (registers its routes in ROUTER on import)
from mgr import routes_guest as _routes_guest  # noqa: F401  (registers its routes in ROUTER on import)
from mgr import routes_squadgit as _routes_squadgit  # noqa: F401  (registers its routes in ROUTER on import)
from mgr import routes_squadkick as _routes_squadkick  # noqa: F401  (registers its routes in ROUTER on import)
from mgr import rules as _rules
from mgr import saddler as _saddler_mod
from mgr import secrets as _secrets
from mgr import sessions as _sessions        # noqa: F401
from mgr import settings as _settings
from mgr import signal as _signal_mod
from mgr import mail as _mail
from mgr import skills as _skills            # noqa: F401
from mgr import squadgate as _squadgate
from mgr import squadgit as _squadgit        # noqa: F401
from mgr import squadkick as _squadkick      # noqa: F401
from mgr import squadmetrics as _squadmetrics
from mgr import squadrec as _squadrec
from mgr import squads as _squads            # noqa: F401
from mgr import startup as _startup
from mgr import store as _store
from mgr import tasks as _tasks
from mgr import toolchain as _toolchain        # noqa: F401
from mgr import ui as _ui                    # noqa: F401
from mgr import util as _util                # noqa: F401
from mgr import vm as _vm                    # noqa: F401
from mgr import voice as _voice              # noqa: F401
from mgr import websearch as _websearch_mod
from mgr import wsfiles as _wsfiles          # noqa: F401

# ---- wiring: what a module cannot derive itself ------------------------------
_missions.configure(_paths.BASE)
_mcp.configure(_paths.BASE, _instances.load_instances, _secrets.allowed_secret_keys, _secrets.secret_store)
_memfs.configure(_paths.BASE)
_hindsight.configure(lambda: _settings.load_settings(), log=print)
_signal_mod.configure(_paths.BASE)
_mail.configure(_paths.BASE)
_mcp.HUB_TZ = _host.HOST_TZ          # hub processes (caldav-mcp …) format dates in this zone
os.makedirs(_paths.RUN_DIR, exist_ok=True)
_gateway.configure(_paths.BASE)
_notify.configure(_paths.BASE)
_missions.notify_add = _notify.notify_add   # injection (mgr/missions)
_store.configure(_paths.BASE)
_missions.sem_store = _store.sem_store   # injection (mgr/missions)
_rules.configure(_paths.BASE)
_irohgw.configure(_paths.BASE)
_saddler_mod.configure(_audit.AUDIT_DIR, _store.HISTORY_DB)
_websearch_mod.configure(lambda key: (_settings.load_settings().get(key) or ""))


def _ha_ws_target():
    """(host, port) of the Home Assistant WebSocket, from the MCP catalog: the
    'homeassistant' entry carries the URL in args[0]; the WS address derives
    from it (no second place to configure)."""
    for m in _mcp.load_mcps():
        if m.get("name") == "homeassistant":
            for a in m.get("args", []):
                a = str(a)
                if a.startswith(("http://", "https://")):
                    hostport = a.split("//", 1)[1].split("/", 1)[0]
                    host, _, port = hostport.partition(":")
                    return host, int(port or "8123")
    return None


_haalias.configure(_ha_ws_target, lambda: _secrets.secret_store().get("HA_TOKEN"))

if __name__ == "__main__":
    print(f"kAIm56 on http://{_host.LISTEN[0]}:{_host.LISTEN[1]}  (auth={'oidc' if _oidc.enabled() else 'on' if _auth.PW else 'OFF'})",
          flush=True)
    os.umask(0o077)                  # new files are root's; the few others read get a mode below
    _startup.harden_files()
    _startup.warn_if_open()
    if _mounts.ensure_guest_user():
        _memfs.OWNER = (_mounts.GUEST_UID, _host.ADMIN_GID)
        _mounts.own_guest_dir(_mounts.AGENT_ROOT, 0o755)
        _mounts.own_guest_dir(_mounts.FCMNT_ROOT, 0o755)
    _mounts.retire_root_export()
    _netfw.ensure_browser_rules()    # also without a VM start (ensure_net_base): the browser runs at boot
    _netfw.ensure_host_firewall()    # opt-in (site.json HOST_FIREWALL=1); off = no iptables call
    _startup.migrate_secrets_out_of_instances()
    _startup.migrate_mcp_config_out_of_instances()
    _personas.seed_missing()         # new built-in personas join an existing personas.json
    try:
        _squadrec.migrate()          # squad IDs, archive folders, history once; a running member's clone stays put
        _squadmetrics.backfill()     # squad_events from the archive; per-file offsets, (kind, ref) dedupe: re-runs add nothing
        if _squads.migrate_wakes_default():
            print("[migrate] squads: old default 20 wakes/hour -> 60", flush=True)
        n = _squads.migrate_depth_default()
        if n:
            print(f"[migrate] squads: {n} squad(s) reply depth 3 -> 8", flush=True)
        n = _squads.migrate_approval_default()
        if n:
            print(f"[migrate] squads: {n} squad(s) switched to 'no approvals by default'", flush=True)
        _squadgate.repair_tools()    # squad members with a fixed tool list get the squad tools they lack
    except Exception as e:
        print(f"[migrate] squads: {e!r}", flush=True)
    try:
        n = _skills.migrate_categories()   # a category for every skill that has none (backup first)
        if n:
            print(f"[migrate] skills: {n} categories set", flush=True)
    except Exception as e:
        print(f"[migrate] skills: {e!r}", flush=True)
    threading.Thread(target=_tasks._task_worker, daemon=True).start()
    threading.Thread(target=_signal_mod._signal_receiver, daemon=True).start()
    threading.Thread(target=_mail._mail_receiver, daemon=True).start()
    ThreadingHTTPServer(_host.LISTEN, _httpd.H).serve_forever()
