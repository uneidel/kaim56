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
from mgr import util as _util
from mgr import vm as _vm

PAGE = CSS + HTML_TOP + ARCH_SVG + HTML_BOTTOM + JS


# ---- Brand ------------------------------------------------------------------
# logo.svg is kept as a file (favicon, the chat page's header mark). The navy
# inherits the text color so it carries on any ground; the turquoise stays the accent.
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

# Icons for the server-side rendered instance cards (Feather style).
_SVG = ('<svg width=%d height=%d viewBox="0 0 24 24" fill=none stroke=currentColor '
        'stroke-width=1.5 stroke-linecap=round stroke-linejoin=round>%s</svg>')
IC_TERM = _SVG % (14, 14, '<path d="m4 17 6-6-6-6M12 19h8"></path>')
IC_CHAT = _SVG % (14, 14, '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>')
IC_UPD = _SVG % (14, 14, '<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M8 16H3v5"></path>')
IC_AUDIT = _SVG % (16, 16, '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path>'
                   '<path d="M14 2v5h6M16 13H8M16 17H8M10 9H8"></path>')
IC_FILES = _SVG % (16, 16, '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
                   'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>')
IC_DEL = _SVG % (16, 16, '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>')


def h(v):
    """HTML escape for server-side rendering. The instance name is trimmed to
    [a-z0-9-_] on creation, but everything else comes freely from forms or
    templates — model ID (free text field), description, mount paths, tool
    list. Without escaping it lands raw in the markup: anyone who sets a mount
    row or a custom model ID would otherwise write script into the admin page."""
    return html.escape(str(v if v is not None else ""), quote=True)


def _card(inst):
    """One fleet card. Usage numbers (today/total, spend, bar) are filled by
    refreshUsage() in the page script, which also keeps them live."""
    name = inst["name"]
    cfg = inst.get("config") or {}
    run = _instances.is_running(inst)
    stale, built, started = _vm.image_state(inst)
    state = "old" if run and stale else "run" if run else "off"
    if state == "old":
        pill = ("<span class=ipill title='started " + time.strftime("%d.%m. %H:%M", time.localtime(started))
                + ", image rebuilt " + time.strftime("%d.%m. %H:%M", time.localtime(built))
                + " — runs the old agent until restarted'>Outdated</span>")
    else:
        pill = f"<span class=ipill>{'Running' if run else 'Stopped'}</span>"
    transport = cfg.get("TRANSPORT", "signal")
    tpl = inst.get("template", "")
    desc = inst.get("description") or " · ".join(x for x in (tpl, transport) if x)
    model = next((cfg[k] for k in _instances.MODEL_KEYS if cfg.get(k)), "")
    prov = h(" / ".join(x for x in (tpl, model) if x))
    # The model box opens the model-switch dialog (editModel in the page script).
    model_box = (f"<button class=ic-model title='Change model' onclick=\"editModel('{name}')\">{prov}</button>"
                 if model else f"<div class=ic-model>{prov}</div>")
    net = inst.get("internet", True)
    chips = (f"<button class=chip title='Toggle internet access' "
             f"onclick=\"toggleNet('{name}',{str(not net).lower()})\">{'Internet on' if net else 'Offline'}</button>")
    if _vm.is_overlay(inst):
        pers = bool(inst.get("persist_disk"))
        chips += (f"<button class=chip title='{'Persistent disk: installations survive stop/start — right-click: reset disk' if pers else 'Fresh disk on every start — click: make persistent'}' "
                  f"onclick=\"togglePersist('{name}',{str(not pers).lower()})\" "
                  f"oncontextmenu=\"return diskReset('{name}')\">{'Persistent' if pers else 'Fresh per start'}</button>")
    tools = cfg.get("AGENT_TOOLS", "")
    if tools:
        chips += f"<span class=chip title='{h(tools)}'>{len(tools.split(','))} tools</span>"
    mounts = "".join(
        f"<div class=ic-mnt>{h(m.get('host'))} → {h(m.get('guest'))}{' (ro)' if m.get('readonly') else ''}</div>"
        for m in inst.get("mounts", []) or [])
    acts = ""
    if state == "old":
        acts += f"<button class='btn btn-primary' onclick=\"act('{name}','restart')\">{IC_UPD}Update</button>"
    if transport == "web":
        acts += f"<a class='btn btn-secondary' href=\"/chat?i={name}\" title='Chat with the agent'>{IC_CHAT}Chat</a>"
    if run:
        acts += (f"<a class='btn btn-secondary' href=\"/i/{name}/term/\" target=_blank title='Browser terminal'>"
                 f"{IC_TERM}Terminal</a>")
    acts += (f"<button class='btn btn-{'secondary' if run else 'primary'}' "
             f"onclick=\"act('{name}','{'stop' if run else 'start'}')\">{'Stop' if run else 'Start'}</button>")
    acts += (f"<div class=ic-icons>"
             f"<button class='btn btn-icon' title=Activity aria-label=Activity onclick=\"openActivity('{name}')\">{IC_AUDIT}</button>"
             f"<button class='btn btn-icon' title=Folders aria-label=Folders onclick=\"editMounts('{name}')\">{IC_FILES}</button>"
             f"<button class='btn btn-icon' title=Delete aria-label=Delete onclick=\"del('{name}')\">{IC_DEL}</button></div>")
    return (f"<article class=icard data-name='{h(name)}' data-state={state} "
            f"data-cpu='{int(inst.get('vcpus', 2))}' data-mem='{int(inst.get('mem_mib', 1024))}'>"
            f"<div class=ic-body><div class=ic-head><div class=ic-id><div class=ic-av>{h(name[:1].upper())}</div>"
            f"<div style='min-width:0'><div class=ic-name>{h(name)}</div><div class=ic-desc>{h(desc)}</div></div></div>{pill}</div>"
            f"{model_box}"
            f"<div class=ic-stats><div><div class=ic-k>vCPU / RAM</div><div class=ic-v>{int(inst.get('vcpus', 2))} / {int(inst.get('mem_mib', 1024))} MiB</div></div>"
            f"<div><div class=ic-k>Guest IP</div><div class='ic-v mono'>{h(_instances.net_of(inst)['guest'])}</div></div>"
            f"<div><div class=ic-k>Spend</div><div class='ic-v ic-cost'>—</div></div></div>"
            f"<div class=ic-use><div class=ic-usel><span class=ic-today>Today 0</span><span class=ic-total>Total 0</span></div>"
            f"<div class=track><i class=ic-bar></i></div></div>"
            f"<div class=ic-chips>{chips}</div>{mounts}</div>"
            f"<div class=ic-acts>{acts}</div></article>")


def render():
    rows = "".join(_card(i) for i in _instances.load_instances())
    tpls = "".join(f"<option value='{h(t['template'])}'>{h(t['template'])} — {h(t.get('description',''))}</option>"
                   for t in _instances.load_templates())
    empty = "<p class=text-muted style='padding:18px 8px'>no instances yet — create one below</p>"
    return (PAGE.replace("__ROWS__", rows or empty)
                .replace("__HOSTMEM__", str(_host.mem_total_mib()))
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
                .replace("__INDEX_HOST__", _util.js_json(_settings.INDEX_HOST))
                .replace("__SIGNAL_HOST__", _settings.SIGNAL_HOST)
                .replace("__CODE_LINK__",
                         f'<a href="{html.escape(_settings.CODE_URL, quote=True)}" target="_blank" '
                         f'rel="noopener noreferrer">VS&nbsp;Code</a>' if _settings.CODE_URL else "")
                .replace("__HOME__", os.path.expanduser(
                    "~" + (os.environ.get("SUDO_USER") or "")))
                )
