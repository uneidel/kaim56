# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI, assembled from its parts.

Was a single 2.100-line constant — CSS, markup, the architecture SVG and the
whole client script in one string nobody could navigate. The parts live in
ui_css / ui_html (ARCH_SVG separate, because the update cycle edits it) /
ui_js; this module keeps the public contract: ``PAGE``, consumed by
manager.py's render(). Pure data — logic stays in manager.py."""
import html
import os
import time
from mgr.ui_css import CSS
from mgr.ui_html import HTML_TOP, ARCH_SVG, HTML_BOTTOM
from mgr.ui_js import JS
from mgr import host as _host
from mgr import instances as _instances
from mgr import netfw as _netfw
from mgr import paths as _paths
from mgr import personas as _personas
from mgr import settings as _settings
from mgr import skills as _skills
from mgr import store as _store
from mgr import util as _util
from mgr import vm as _vm

PAGE = CSS + HTML_TOP + ARCH_SVG + HTML_BOTTOM + JS


# ---- Brand ------------------------------------------------------------------
# logo.svg is kept as a file (favicon, shared elsewhere). For the header mark
# the navy inherits the text color so it carries in both the light and the dark
# theme; the turquoise stays the accent.
BRAND = "kAIm56"
LOGO_FILE = os.path.join(_paths.BASE, "logo.svg")
try:
    with open(LOGO_FILE) as _fh:
        LOGO_SVG = _fh.read()
except OSError:
    LOGO_SVG = ""
LOGO_INLINE = (LOGO_SVG.replace("#1D2A4D", "currentColor")
                       .replace('width="512" height="512"',
                                'width="26" height="26" class=mark')
                       .replace("\n", "").strip())

# Icons for the server-side rendered instance rows (Feather style, 14px).
_SVG = ('<svg width=14 height=14 viewBox="0 0 24 24" fill=none stroke=currentColor '
        'stroke-width=1.5 stroke-linecap=round stroke-linejoin=round>%s</svg>')
IC_TERM = _SVG % '<polyline points="4 17 10 11 4 5"></polyline><line x1=12 y1=19 x2=20 y2=19></line>'
IC_CHAT = _SVG % '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>'
IC_FILES = _SVG % ('<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
                   'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>')
IC_DEL = _SVG % ('<path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>'
                 '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>')
# small folder icon (12px) for the mount rows — instead of 📁 (emoji tofus without an emoji font)
IC_AUDIT = _SVG % ('<path d="M4 5h16M4 12h16M4 19h10"></path>'
                   '<circle cx="19" cy="19" r="2.4"></circle><path d="M22 22l-1.3-1.3"></path>')
IC_FILES2 = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 '
             'stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px"><path d="M4 20h16a2 '
             '2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 '
             '2v13a2 2 0 0 0 2 2Z"></path></svg>')


def h(v):
    """HTML escape for server-side rendering. The instance name is trimmed to
    [a-z0-9-_] on creation, but everything else comes freely from forms or
    templates — model ID (free text field), description, mount paths, tool
    list. Without escaping it lands raw in the markup: anyone who sets a mount
    row or a custom model ID would otherwise write script into the admin page."""
    return html.escape(str(v if v is not None else ""), quote=True)


def _fmt_tok(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n/1000:.1f}k".replace(".0k", "k")
    return str(n)


def _fmt_cost(c):
    c = float(c or 0.0)
    return f"${c:.2f}" if c >= 0.01 else f"${c:.4f}"


def render():
    rows = ""
    usage = _store.usage_summary()
    for inst in _instances.load_instances():
        n = _instances.net_of(inst)
        run = _instances.is_running(inst)
        name = inst["name"]
        transport = (inst.get("config") or {}).get("TRANSPORT", "signal")
        cfgm = inst.get("config") or {}
        model = next((cfgm[k] for k in _instances.MODEL_KEYS if cfgm.get(k)), "")
        sub = " · ".join(x for x in (inst.get("template", ""), transport) if x)
        _chip = ('<svg width=12 height=12 viewBox="0 0 24 24" fill=none stroke=currentColor '
                 'stroke-width=1.6 stroke-linecap=round stroke-linejoin=round style="vertical-align:-1px">'
                 '<rect x=6 y=6 width=12 height=12 rx="1"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2'
                 'M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>')
        # The chip is clickable: opens the model-switch dialog (editModel in PAGE JS).
        model_line = (f"<button class='mono' style=\"font-size:12px;color:var(--color-accent-700);"
                      f"display:inline-flex;align-items:center;gap:5px;background:none;border:none;"
                      f"padding:0;cursor:pointer;text-align:left\" title=\"Change model\" "
                      f"onclick=\"editModel('{name}')\">{_chip}{h(model)}</button>"
                      if model else "")
        u = usage.get(name) or {}
        ut, ud = u.get("total") or {}, u.get("today") or {}
        usage_line = (f"<span class=imeta data-usage='{name}' title='LLM usage reported by this agent "
                      f"({ut.get('calls', 0)} calls; in / out today {_fmt_tok(ud.get('in'))} / {_fmt_tok(ud.get('out'))}, "
                      f"total {_fmt_tok(ut.get('in'))} / {_fmt_tok(ut.get('out'))})'>"
                      + (f"Today {_fmt_tok((ud.get('in') or 0) + (ud.get('out') or 0))} tokens · {_fmt_cost(ud.get('cost'))}"
                         f" · Total {_fmt_tok((ut.get('in') or 0) + (ut.get('out') or 0))} · {_fmt_cost(ut.get('cost'))}"
                         if ut.get("calls") else "") + "</span>")
        # Hierarchy: name + state first, then resources, usage, actions. Colour
        # only for state: running = accent, an outdated image = warn (with the
        # fix next to it); red stays reserved for real errors.
        stale, built, started = _vm.image_state(inst)
        st = ("<span class='ipill on'>● Running</span>" if run else "<span class=ipill>○ Stopped</span>")
        upd = ""
        if run and stale:
            upd = ("<button class='ichip warn' title='started "
                   + time.strftime("%d.%m. %H:%M", time.localtime(started))
                   + ", image rebuilt " + time.strftime("%d.%m. %H:%M", time.localtime(built))
                   + f" — runs the old agent until restarted. Click: restart with the update' "
                     f"onclick=\"act('{name}','restart')\">↻ Update available</button>")
        net = inst.get("internet", True)
        tools_cfg = (inst.get("config") or {}).get("AGENT_TOOLS", "")
        chips = (f"<button class=ichip title='Toggle internet access' "
                 f"onclick=\"toggleNet('{name}',{str(not net).lower()})\">{'Internet' if net else 'Offline'}</button>")
        if _vm.is_overlay(inst):
            pers = bool(inst.get("persist_disk"))
            chips += (f"<button class=ichip title='{'Persistent disk: installations survive stop/start — right-click: reset disk' if pers else 'Fresh disk on every start — click: make persistent'}' "
                      f"onclick=\"togglePersist('{name}',{str(not pers).lower()})\" "
                      f"oncontextmenu=\"return diskReset('{name}')\">{'Persistent' if pers else 'Fresh per start'}</button>")
        if tools_cfg:
            chips += f"<span class=ichip title='{h(tools_cfg)}'>{len(tools_cfg.split(','))} tools</span>"
        mem = inst.get("mem_mib", 1024)
        res = (f"{inst.get('vcpus', 2)} CPU · {mem / 1024:.1f}".rstrip("0").rstrip(".") + f" GB · {n['guest']}")
        # Two direct actions (Chat first: it is what the instance is for), the
        # rest in the ⋮ menu. A stopped instance shows Start instead of Terminal.
        btn = ""
        if transport == "web":
            btn += (f"<a href=\"/chat?i={name}\" class=\"btn btn-primary btn-sm\" title=\"Chat with the agent\">"
                    f"{IC_CHAT}Chat</a>")
        if run:
            btn += (f"<a href=\"/i/{name}/term/\" target=_blank class=\"btn btn-ghost btn-sm\" title=\"Browser terminal\">"
                    f"{IC_TERM}Terminal</a>")
        else:
            btn += f"<button class=\"btn btn-secondary btn-sm\" onclick=\"act('{name}','start')\">Start</button>"
        menu = ""
        if run:
            menu += f"<button onclick=\"act('{name}','stop')\">Stop instance</button>"
            menu += (f"<button onclick=\"act('{name}','restart')\">"
                     f"{'Restart with update' if stale else 'Restart'}</button>")
        menu += f"<button onclick=\"openActivity('{name}')\">{IC_AUDIT}Activity</button>"
        menu += f"<button onclick=\"editMounts('{name}')\">{IC_FILES}Folders</button>"
        menu += f"<button class=danger onclick=\"del('{name}')\">{IC_DEL}Delete</button>"
        btn += (f"<details class=imenu><summary class=\"btn btn-ghost btn-sm\" aria-label=\"More actions\" "
                f"title=\"More actions\">⋮</summary><div class=imenu-pop>{menu}</div></details>")
        mtxt = "".join(
            f"<span class=imeta>{IC_FILES2} {h(m.get('host'))} → {h(m.get('guest'))}{' (ro)' if m.get('readonly') else ''}</span>"
            for m in inst.get("mounts", []) or [])
        rows += (f"<article class=irow>"
                 f"<div class=imain>"
                 f"<div class=ititle><span class=iname>{name}</span>{st}{upd}</div>"
                 f"<div class=isub>{h(inst.get('description', '') or sub)}"
                 f"{(' · ' + h(sub)) if inst.get('description') else ''}</div>"
                 f"<div class=ichips>{chips}{model_line}</div>"
                 f"{usage_line}{mtxt}</div>"
                 f"<div class=iside><span class='ires mono'>{res}</span>"
                 f"<div class=acts>{btn}</div></div></article>")
    tpls = "".join(f"<option value='{h(t['template'])}'>{h(t['template'])} — {h(t.get('description',''))}</option>"
                   for t in _instances.load_templates())
    empty = "<p class=text-muted style='padding:18px 8px'>no instances yet — create one below</p>"
    return (PAGE.replace("__LOGO__", LOGO_INLINE)
                .replace("__ROWS__", rows or empty)
                .replace("__TPLS__", tpls or "<option>no templates</option>")
                .replace("__TPLJSON__", _util.js_json(_instances.load_templates()))
                .replace("__SETTINGS__", _util.js_json(_settings.settings_for_ui()))
                .replace("__SETTINGS_SCHEMA__", _util.js_json(_settings.settings_schema()))
                .replace("__PERSONAS__", _util.js_json(_personas.load_personas(), ensure_ascii=False))
                # Only name + description into the page: with an imported
                # catalog the contents are ~1 MB, and the UI needs them only
                # when editing (then it fetches GET /api/skills/<name>).
                .replace("__SKILLS__", _util.js_json(_skills.meta(), ensure_ascii=False))
                .replace("__HOSTIF__", _host.HOSTIF).replace("__POOL__", _netfw.POOL)
                .replace("__PUBLIC_HOST__", _settings.PUBLIC_HOST)
                .replace("__SIGNAL_HOST__", _settings.SIGNAL_HOST)
                .replace("__CODE_LINK__",
                         f'<a href="{html.escape(_settings.CODE_URL, quote=True)}" target="_blank" '
                         f'rel="noopener noreferrer">VS&nbsp;Code</a>' if _settings.CODE_URL else "")
                .replace("__HOME__", os.path.expanduser(
                    "~" + (os.environ.get("SUDO_USER") or "")))
                )
