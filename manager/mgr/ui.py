# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI: the complete HTML/CSS/JS as a single constant.
Pure data — logic (render/substitutions) stays in manager.py."""

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>kAIm56</title>
<link rel=icon type="image/svg+xml" href="/logo.svg">
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600&display=swap');
/* ── Industry — design-system tokens (claude.ai/design) ─────────────────── */
:root{
  --color-bg:#f2f2f3; --color-surface:#e9e9ea; --color-text:#1d1f20;
  --color-accent:#5980a6; --color-accent-2:#728fab;
  --color-divider:color-mix(in srgb,#1d1f20 16%,transparent);
  --color-neutral-100:#f5f5f8;--color-neutral-200:#e7e7ea;--color-neutral-300:#d4d4d7;
  --color-neutral-400:#b7b7ba;--color-neutral-500:#98989b;--color-neutral-600:#7a7a7d;
  --color-neutral-700:#5d5d60;--color-neutral-800:#424244;--color-neutral-900:#2b2b2d;
  --color-accent-100:#eef6ff;--color-accent-200:#d6ebff;--color-accent-300:#b5d9fd;
  --color-accent-400:#94bce3;--color-accent-500:#749dc4;--color-accent-600:#597ea3;
  --color-accent-700:#416180;--color-accent-800:#2c455d;--color-accent-900:#1d2d3d;
  --font-heading:"Barlow Condensed",system-ui,sans-serif; --font-heading-weight:600;
  --font-body:"Barlow",system-ui,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --space-1:3.4px;--space-2:6.8px;--space-3:10.2px;--space-4:13.6px;--space-6:20.4px;--space-8:27.2px;
  --radius-sm:2px;--radius-md:4px;--radius-lg:7px;
  --shadow-sm:0 1px 2px color-mix(in srgb,#2b2b2d 14%,transparent);
  --shadow-md:0 3px 10px color-mix(in srgb,#2b2b2d 16%,transparent);
  --shadow-lg:0 12px 32px color-mix(in srgb,#2b2b2d 22%,transparent);
  --color-ok:#416180;
}
/* Dark rendering of the same system — same roles, ground flipped. */
@media(prefers-color-scheme:dark){:root{
  --color-bg:#141618; --color-surface:#1c1f22; --color-text:#e8e9ea;
  --color-accent:#94bce3; --color-accent-2:#9ebbd8;
  --color-divider:color-mix(in srgb,#e8e9ea 18%,transparent);
  --color-neutral-100:#212326;--color-neutral-200:#2b2d31;--color-neutral-300:#3a3d41;
  --color-neutral-400:#4e5155;--color-neutral-500:#6b6e73;--color-neutral-600:#8b8e93;
  --color-neutral-700:#a9acb1;--color-neutral-800:#c8cacd;--color-neutral-900:#e8e9ea;
  --color-accent-100:#1d2d3d;--color-accent-200:#2c455d;--color-accent-300:#416180;
  --color-accent-400:#597ea3;--color-accent-500:#749dc4;--color-accent-600:#94bce3;
  --color-accent-700:#b5d9fd;--color-accent-800:#d6ebff;--color-accent-900:#eef6ff;
  --color-ok:#b5d9fd;
}}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font-family:var(--font-body);font-size:15px;line-height:1.55;font-weight:400;
  background:var(--color-bg);color:var(--color-text);-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,h5,h6{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  line-height:1.12;letter-spacing:-.015em;margin:0 0 var(--space-2)}
h1{font-size:42px}h2{font-size:32px}h3{font-size:25px}h4{font-size:20px}h5{font-size:16px}
h6{font-size:13px;letter-spacing:.08em;text-transform:uppercase}
p{margin:0 0 var(--space-3)}
a{color:var(--color-accent);text-underline-offset:3px}
a:hover{color:var(--color-accent-700)}
.text-muted{color:color-mix(in srgb,var(--color-text) 55%,transparent)}
:focus{outline:none}
:focus-visible{outline:2px solid var(--color-accent);outline-offset:2px}
::selection{background:color-mix(in srgb,var(--color-accent) 30%,transparent)}
code{font-family:var(--font-mono);font-size:.88em;background:var(--color-neutral-100);
  border:1px solid var(--color-divider);padding:.02rem .3rem}
/* — blueprint frame — */
.blueprint{position:relative;border:1px solid var(--color-divider);border-radius:0}
.blueprint>.corner{position:absolute;width:11px;height:11px;
  color:color-mix(in srgb,var(--color-text) 55%,transparent)}
.blueprint>.corner::before,.blueprint>.corner::after{content:"";position:absolute;background:currentColor}
.blueprint>.corner::before{left:5px;top:0;width:1px;height:100%}
.blueprint>.corner::after{top:5px;left:0;width:100%;height:1px}
.blueprint>.corner.tl{top:-6px;left:-6px}.blueprint>.corner.tr{top:-6px;right:-6px}
.blueprint>.corner.bl{bottom:-6px;left:-6px}.blueprint>.corner.br{bottom:-6px;right:-6px}
/* — buttons — */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;cursor:pointer;
  text-decoration:none;font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:14px;line-height:1.2;color:var(--color-text);background:transparent;
  border:1px solid var(--color-divider);border-radius:0;
  padding:var(--space-2) calc(var(--space-3)*1.2)}
.btn svg{display:block}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-primary{background:var(--color-accent);color:var(--color-bg);border-color:var(--color-accent)}
.btn-primary:hover{background:var(--color-accent-600);border-color:var(--color-accent-600)}
.btn-primary:active{background:var(--color-accent-700)}
.btn-secondary:hover{background:color-mix(in srgb,var(--color-text) 7%,transparent)}
.btn-secondary:active{background:color-mix(in srgb,var(--color-text) 14%,transparent)}
.btn-ghost{color:var(--color-accent);border-color:transparent;padding-inline:var(--space-1)}
.btn-ghost:hover{background:color-mix(in srgb,var(--color-accent) 10%,transparent)}
.btn-icon{width:36px;height:36px;padding:0}
.btn-sm{font-size:13px;padding:5px 10px}
/* — forms — */
.field>label{display:block;font-size:12px;margin-bottom:5px;
  color:color-mix(in srgb,var(--color-text) 70%,transparent)}
.input{width:100%;min-height:36px;padding:6px 10px;font:inherit;font-size:14px;
  color:var(--color-text);caret-color:var(--color-accent);background:var(--color-surface);
  border:1px solid var(--color-divider);border-radius:0}
.input:hover{border-color:color-mix(in srgb,var(--color-text) 45%,transparent)}
.input:focus-visible{border-color:var(--color-accent);outline-offset:0}
textarea.input{min-height:90px;resize:vertical;line-height:1.5}
.radio{display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-size:14px}
.radio input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}
.radio .dot{width:16px;height:16px;flex:none;border-radius:50%;border:1.5px solid var(--color-divider)}
.radio:hover .dot{border-color:var(--color-accent)}
.radio input:checked+.dot{border-color:var(--color-accent);background:var(--color-accent);
  box-shadow:inset 0 0 0 4px var(--color-bg)}
.radio input:focus-visible+.dot{outline:2px solid var(--color-accent);outline-offset:2px}
input[type=checkbox]{accent-color:var(--color-accent)}
/* — cards — */
.plugdrop{border:2px dashed var(--color-divider);border-radius:14px;padding:34px;text-align:center;color:var(--color-neutral-600);transition:.15s}
.plugdrop.drag{border-color:var(--color-accent);background:color-mix(in srgb,var(--color-accent) 7%,transparent);color:var(--color-accent)}
.pluglnk{color:var(--color-accent);cursor:pointer;text-decoration:underline}
.card{display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-3);
  border-radius:0;background:transparent;border:1px solid var(--color-divider)}
.card-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:17px;line-height:1.2}
.card-body{margin:0;font-size:13px;opacity:.8;flex:1}
/* — tags — */
.tag{display:inline-flex;align-items:center;font-size:11px;letter-spacing:.02em;
  padding:3px 10px;border-radius:0;white-space:nowrap}
.tag-accent{background:var(--color-accent-100);color:var(--color-accent-800)}
.tag-neutral{background:var(--color-neutral-100);color:var(--color-neutral-800)}
/* — tables — */
.table{width:100%;border-collapse:collapse;font-size:14px}
.table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:color-mix(in srgb,var(--color-text) 60%,transparent);
  padding:var(--space-2);border-bottom:1px solid var(--color-divider)}
.table td{padding:var(--space-2);border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent);
  vertical-align:top}
.table tbody tr:hover{background:color-mix(in srgb,var(--color-text) 4%,transparent)}
/* — app shell — */
.shell{min-height:100vh;display:flex;flex-direction:column}
.topbar{border-bottom:1px solid var(--color-divider);background:var(--color-bg);
  position:sticky;top:0;z-index:10}
.topbar-in{max-width:1160px;margin:0 auto;padding:0 28px;display:flex;align-items:center;
  gap:32px;min-height:58px}
.brand{display:flex;align-items:center;gap:10px;margin-right:auto}
.brand b{line-height:1}
.brand .mark{flex:none;display:block}
.brand b{font-family:var(--font-heading);font-weight:600;font-size:19px;letter-spacing:.01em}
.brand span{font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.appfoot{display:flex;align-items:center;gap:18px;padding:14px 28px;margin-top:24px;
  border-top:1px solid var(--color-divider);color:var(--color-neutral-600)}
.af-brand{font-family:var(--font-heading);font-weight:600;font-size:13px;letter-spacing:.02em}
.af-stat{font-size:12px;color:var(--color-neutral-600);white-space:nowrap}
.af-nav{display:flex;gap:18px;margin-left:auto}
.af-nav a{color:var(--color-neutral-600);text-decoration:none;font-size:13px}
.af-nav a:hover,.af-nav a[aria-current=page]{color:var(--color-accent)}
.nbell{position:relative;flex:none;margin-left:14px;width:38px;height:38px;display:flex;align-items:center;justify-content:center;border:1px solid var(--color-divider);border-radius:10px;background:var(--color-surface);color:var(--color-neutral-700);cursor:pointer}
.nbell:hover{border-color:var(--color-accent);color:var(--color-accent)}
.nbadge{position:absolute;top:-6px;right:-6px;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:var(--color-accent);color:#fff;font-size:11px;font-weight:700;line-height:17px;text-align:center}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--color-text) 18%,transparent);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:color-mix(in srgb,var(--color-text) 32%,transparent)}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-corner{background:transparent}
.npanel{position:absolute;top:58px;right:16px;width:340px;max-width:calc(100vw - 32px);max-height:60vh;overflow:auto;background:var(--color-surface);border:1px solid var(--color-divider);border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.18);z-index:60}
.nhead{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--color-divider);position:sticky;top:0;background:var(--color-surface)}
.nitem{padding:10px 14px;border-bottom:1px solid var(--color-divider)}
.nitem.unread{background:var(--color-neutral-100)}
.nitem[data-link]:hover{background:var(--color-neutral-200)}
.nitem .nt{font-weight:600;font-size:13.5px;display:flex;gap:8px;align-items:baseline}
.nitem .nb{font-size:13px;color:var(--color-neutral-700);margin-top:2px;white-space:pre-wrap;word-break:break-word}
.nitem .nm{font-size:11px;color:var(--color-neutral-500);margin-top:4px}
.actwin{display:inline-flex;border:1px solid var(--color-divider);border-radius:9px;overflow:hidden}
.actwin button{border:0;background:var(--color-surface);color:var(--color-neutral-700);font-size:12px;padding:5px 12px;cursor:pointer;border-right:1px solid var(--color-divider)}
.actwin button:last-child{border-right:0}
.actwin button:hover{color:var(--color-accent)}
.actwin button.on{background:var(--color-accent);color:#fff}
.seckey{-webkit-text-security:disc}
.tabs{display:flex;gap:4px;align-self:stretch;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{display:flex;align-items:center;padding:0 14px;font-size:13.5px;letter-spacing:.03em;
  text-decoration:none;color:var(--color-text);white-space:nowrap;
  border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a:hover{color:var(--color-accent-700)}
.tabs a[aria-current=page]{color:var(--color-accent-700);border-bottom-color:var(--color-accent)}
main{max-width:1160px;width:100%;margin:0 auto;padding:36px 28px 64px;flex:1}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:14px}
.sec-head h6{color:var(--color-accent);margin:0 0 2px}
.sec-head h3{margin:0}
.sec-head .note{font-size:12.5px;text-align:right;max-width:520px}
.banner{display:flex;align-items:center;gap:12px;padding:10px 14px;margin-bottom:28px;
  background:var(--color-accent-100)}
.banner span{font-size:13px;color:var(--color-accent-800)}
.panel{padding:24px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px 28px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}
.span2{grid-column:1/-1}
.panel-foot{display:flex;justify-content:flex-end;align-items:center;gap:12px;
  margin-top:24px;padding-top:18px;border-top:1px solid var(--color-divider)}
.msg{font-size:13px;color:var(--color-accent-700)}
.mono{font-family:var(--font-mono);font-size:13px}
.cmd{font-family:var(--font-mono);font-size:12px;background:var(--color-neutral-100);
  border:1px solid var(--color-divider);padding:10px 12px;white-space:pre-wrap;line-height:1.6;
  overflow-x:auto}
.acts{display:flex;gap:6px;justify-content:flex-end;align-items:center;flex-wrap:wrap}
footer{border-top:1px solid var(--color-divider)}
.foot-in{max-width:1160px;margin:0 auto;padding:14px 28px;display:flex;gap:24px;
  flex-wrap:wrap;font-size:12px}
.screen{display:none}.screen.on{display:block}
.fbrow{display:flex;align-items:center;gap:10px;padding:7px 8px;border-bottom:1px solid var(--color-divider);font-size:13.5px}
.fbrow:hover{background:var(--color-neutral-100)}
.fbrow.dir{cursor:pointer}
.fbico{width:1.2em;flex:none;text-align:center}
.fbn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fbsharerow{cursor:pointer;border-radius:8px;padding:4px 8px;transition:background .12s ease}
.fbsharerow:hover{background:var(--color-neutral-100)}
.fbsharerow.sel{background:var(--color-neutral-100);box-shadow:inset 3px 0 0 var(--color-accent)}
.fbsz{color:var(--color-neutral-500);font-size:12px;font-variant-numeric:tabular-nums}
.fbact{font-size:12px;padding:2px 8px}
/* Architecture diagram. The rules live HERE and not as a <style> inside the SVG:
   a style element in inline SVG ends the SVG context during HTML parsing, and
   everything after it drops invisibly out of the image (Chrome; jsdom forgives it). */
#archsvg .bx{fill:var(--color-surface);stroke:var(--color-divider)}
#archsvg .bx2{fill:none;stroke:var(--color-accent)}
#archsvg .tt{fill:var(--color-text);font-size:13px;font-weight:600}
#archsvg .ss{fill:var(--color-neutral-600);font-size:11px}
#archsvg .ln{stroke:var(--color-neutral-500);stroke-width:1.2;marker-end:url(#arw);fill:none}
#archsvg .lb{fill:var(--color-neutral-600);font-size:10px}
.stack{display:flex;flex-direction:column;gap:18px}
.mrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}
/* — dialog — */
.dialog-backdrop{position:fixed;inset:0;display:grid;place-items:center;padding:var(--space-4);
  background:color-mix(in srgb,var(--color-neutral-900) 50%,transparent);z-index:50}
.dialog{width:min(440px,100%);max-height:88vh;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;
  gap:var(--space-3);padding:var(--space-6);border-radius:0;background:var(--color-bg);
  border:1px solid var(--color-divider);box-shadow:var(--shadow-lg)}
.dialog-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:20px}
.dialog-body{font-size:13px;opacity:.85}
.dialog-actions{display:flex;justify-content:flex-end;align-items:center;gap:var(--space-2);
  margin-top:var(--space-2);padding-top:var(--space-3);border-top:1px solid var(--color-divider)}
/* — folder picker — */
.pklist{max-height:46vh;overflow:auto;border:1px solid var(--color-divider);
  background:var(--color-surface);padding:4px;display:flex;flex-direction:column}
.pkrow{justify-content:flex-start;width:100%;border-color:transparent;color:var(--color-text);
  font-family:var(--font-body);font-size:13.5px;padding:5px 8px;gap:8px}
.pkrow:hover{background:color-mix(in srgb,var(--color-accent) 12%,transparent)}
.pkquick{display:flex;gap:6px;flex-wrap:wrap}
.kv{display:flex;gap:12px;align-items:baseline;font-size:13.5px;padding:7px 0;
  border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
.kv:last-child{border-bottom:none}
.sev{display:inline-flex;align-items:center;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;padding:3px 8px;white-space:nowrap;flex:none}
.sev-high{background:var(--color-accent-700);color:var(--color-bg)}
.sev-medium{background:var(--color-accent-200);color:var(--color-accent-900)}
.sev-low{background:var(--color-neutral-200);color:var(--color-neutral-800)}
.issue{display:grid;grid-template-columns:78px 1fr auto;gap:12px;align-items:start;
  padding:12px 0;border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
.issue:last-child{border-bottom:none}
.issue.done{opacity:.5}
.issue h5{margin:0 0 3px;font-size:15px}
.issue .meta{font-size:11.5px;font-family:var(--font-mono)}
.issue p{margin:4px 0 0;font-size:13px}
.md h2{font-size:22px;margin:26px 0 8px}
.md h3{font-size:16px;margin:18px 0 6px;color:var(--color-accent-700)}
.md ul{margin:0 0 10px;padding-left:18px}
.md li{font-size:13.5px;margin:3px 0}
.md p{font-size:13.5px}
.kv b{font-family:var(--font-heading);font-weight:600;font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;min-width:130px;color:color-mix(in srgb,var(--color-text) 60%,transparent)}
@media(max-width:860px){
  .grid2,.grid3{grid-template-columns:1fr}
  .topbar-in,main,.foot-in{padding-left:16px;padding-right:16px}
  .sec-head{flex-direction:column;align-items:flex-start}
  .sec-head .note{text-align:left}
  .table thead{display:none}
  .table,.table tbody,.table tr,.table td{display:block;width:100%}
  .table tr{border:1px solid var(--color-divider);margin:12px 0;padding:6px 4px}
  .table td{border:none;padding:6px 10px}
  .table td::before{content:attr(data-label);display:block;font-size:10px;
    letter-spacing:.08em;text-transform:uppercase;
    color:color-mix(in srgb,var(--color-text) 55%,transparent)}
  .acts{justify-content:flex-start}
}
</style></head><body>
<div class=shell>
<header class=topbar><div class=topbar-in>
  <div class=brand>__LOGO__<b>kAIm56</b></div>
  <nav class=tabs id=tabs>
    <a href="#instances">Instances</a>
    <a href="#personas">Personas</a>
    <a href="#skills">Skills</a>
    <a href="#plugins">Plugins</a>
    <a href="#mcp">MCP servers</a>
    <a href="#tasks">Tasks</a>
    <a href="#missions">Missions</a>
    <a href="#policy">Policy</a>
    <a href="#models">Models</a>
    <a href="#resources">Resources</a>
    <a href="#sharing">Sharing</a>
    <a href="#secrets">Secrets</a>
    <a href="#settings">Settings</a>
  </nav>
  <button class=nbell id=nbell onclick=notifToggle() title="Benachrichtigungen" aria-label=Benachrichtigungen>
    <svg width=19 height=19 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.7 stroke-linecap=round stroke-linejoin=round><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"></path><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"></path></svg>
    <span class=nbadge id=nbadge hidden>0</span>
  </button>
  <div class=npanel id=npanel hidden>
    <div class=nhead><b>Benachrichtigungen</b><button class="btn btn-ghost" style="font-size:12px" onclick=notifClear()>Leeren</button></div>
    <div id=nlist><span class=text-muted style="font-size:13px;padding:12px;display:block">…</span></div>
  </div>
</div></header>
<main>

<section class="screen" id=s-instances>
  <div class=sec-head>
    <div><h6>microVM</h6><h3>Instances</h3></div>
    <span class="note text-muted">💬 Chat opens <a href="/chat">/chat</a> for every <code>TRANSPORT=web</code> instance · a stopped instance starts on the first prompt</span>
  </div>
  <table class=table>
    <thead><tr><th style="width:32%">Instance</th><th>Status</th><th>vCPU / RAM</th><th>Guest IP</th><th style="text-align:right">Actions</th></tr></thead>
    <tbody>__ROWS__</tbody>
  </table>

  <div style="margin-top:44px">
    <h6 style="color:var(--color-accent);margin:0 0 2px">Provision</h6>
    <h3 style="margin:0 0 18px">New instance from template</h3>
    <div class="panel blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <div class=grid2>
        <div class=field><label>Template</label><select class=input id=tpl onchange=renderParams()>__TPLS__</select></div>
        <div class=field><label>Instance name</label><input class=input id=nm placeholder="e.g. fabric-gpt4o"></div>
        <div class=field><label>Persona / system prompt (optional)</label>
          <select class=input id=persona><option value="">— Default —</option></select></div>
      </div>
      <div class=grid2 id=params style="margin-top:20px"></div>
      <div class=grid2 style="margin-top:20px">
        <div class="field span2"><label>MCP servers (optional)</label><div id=mcp-pick style="display:flex;gap:20px;flex-wrap:wrap;padding-top:2px"></div></div>
        <div class="field span2"><label>katfs share (optional)</label>
          <div id=katfs-new class=text-muted style="font-size:12px;margin-bottom:6px">…</div>
          <select class=input id=katfsshare onchange=katfsNewHint()></select>
          <span class=text-muted id=katfsurlhint style="font-size:12px"></span>
        </div>
        <div class="field span2"><label>Mount host folders (optional)</label>
          <div id=mounts></div>
          <div style="display:flex;align-items:center;gap:12px;margin-top:6px">
            <button type=button class="btn btn-secondary btn-sm" onclick="addMount()">+ Folder</button>
            <span class=text-muted style="font-size:12px">Host path → guest path · pick with 📁 · "ro" = read-only · no host folder? <a href="#sharing">share one from your browser via katfs</a></span>
          </div>
        </div>
        <div class="field span2"><label>Capabilities</label>
          <label class=radio style="margin:2px 0 8px"><input type=checkbox id=cap-net checked><span class=dot></span>Internet access (LAN + web) — without it the agent only reaches the manager broker and <b>cannot</b> query the LLM</label>
          <div style="display:flex;align-items:center;gap:10px;margin:2px 0 6px">
            <span class=text-muted style="font-size:12px">Agent tools:</span>
            <button type=button class="btn btn-ghost" style="font-size:12px" onclick="toolAll(1)">all</button>
            <button type=button class="btn btn-ghost" style="font-size:12px" onclick="toolAll(0)">none</button>
          </div>
          <div id=toolpick style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:4px 16px"></div>
        </div>
      </div>
      <div class=panel-foot><button class="btn btn-primary" onclick=create()>Create instance</button></div>
    </div>
  </div>
</section>

<section class="screen" id=s-personas>
  <div class=sec-head>
    <div><h6>System prompts</h6><h3>Personas</h3></div>
    <span class="note text-muted">Selectable as "Persona" when creating an instance → sets <code>AGENT_SYSTEM</code> (applied at start, no rebuild)</span>
  </div>
  <div class=grid2 id=personas></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New persona</h4>
    <div style="display:grid;grid-template-columns:280px 1fr;gap:20px" class=pgrid>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=pname placeholder="e.g. researcher"></div>
      <div class=field style="grid-row:span 2"><label>Prompt</label><textarea class=input id=pprompt style="min-height:110px"></textarea></div>
      <div style="align-self:end;display:flex;align-items:center;gap:12px">
        <button class="btn btn-primary" onclick=savePersona()>Save persona</button><span id=pmsg class=msg></span>
      </div>
    </div>
  </div>

  <div class=sec-head style="margin-top:36px">
    <div><h6>Recurring jobs</h6><h3 style="font-size:22px">Prompt templates</h3></div>
    <span class="note text-muted">As a slash command in chat: <code>/name [extra]</code> — the agent expands it server-side (web, app and Signal)</span>
  </div>
  <div class="panel blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=promptlist><span class=text-muted style="font-size:13px">…</span></div>
    <div class=grid2 style="margin-top:16px">
      <div class=field><label>Name (becomes /name)</label><input class=input id=prname placeholder="daily"></div>
      <div class="field span2"><label>Prompt-Text</label><textarea class=input id=prtext style="min-height:70px" placeholder="Write my daily briefing: …"></textarea></div>
    </div>
    <div class=panel-foot><span id=prmsg class=msg></span><button class="btn btn-primary" onclick=savePrompt()>Save template</button></div>
  </div>

  <div class=sec-head style="margin-top:36px">
    <div><h6>Learned rules</h6><h3 style="font-size:22px">Playbooks</h3></div>
    <span class="note text-muted">Fixed rules per agent — apply EVERY turn. The agent learns them itself from corrections (playbook_add); view, add, remove them here.</span>
  </div>
  <div class="panel blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=field style="max-width:280px;margin-bottom:12px"><label>Agent</label>
      <select class=input id=pbinst onchange=loadPlaybooks()></select></div>
    <div id=pblist><span class=text-muted style="font-size:13px">…</span></div>
    <div class="field span2" style="margin-top:14px"><label>New rule</label>
      <textarea class=input id=pbtext style="min-height:56px" placeholder="Always fetch stock prices via http_fetch from query1.finance.yahoo.com …"></textarea></div>
    <div class=panel-foot><span id=pbmsg class=msg></span><button class="btn btn-primary" onclick=addPlaybook()>Add rule</button></div>
  </div>
</section>

<section class="screen" id=s-skills>
  <div class=sec-head>
    <div><h6>Expert knowledge</h6><h3>Skills</h3></div>
    <span class="note text-muted">Loaded into context on demand via <code>load_skill("name")</code> · <code>list_skills</code> shows them · central, no rebuild</span>
  </div>
  <div class=grid3 id=skills></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New skill</h4>
    <div class=grid2>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=skname placeholder="e.g. postgres-expert"></div>
      <div class=field><label>Short description</label><input class=input id=skdesc placeholder="When to load this skill?"></div>
      <div class="field span2"><label>Content (Markdown)</label><textarea class=input id=skcontent style="min-height:130px"></textarea></div>
    </div>
    <div class=panel-foot><span id=skmsg class=msg></span><button class="btn btn-primary" onclick=saveSkill()>Save skill</button></div>
  </div>
</section>

<section class="screen" id=s-plugins>
  <div class=sec-head>
    <div><h6>Custom tools</h6><h3>Plugins</h3></div>
    <span class="note text-muted">Drag a <code>.py</code> file or a <code>.zip</code> (multi-file &#8594; its own folder) here. Convention: <code>DESC / PARAMS / REQUIRED / run()</code>. Runs in the agent VM (sandbox, stdlib) &#183; restart the instance to activate.</span>
  </div>
  <div id=plugdrop class=plugdrop>
    <b>.py</b> or <b>.zip</b> — drag here &#8212; or <label class=pluglnk>browse<input type=file id=plugfile accept=".py,.zip" hidden></label>
  </div>
  <div class=grid2 style="margin-top:16px">
    <div class=field><label>New tool from boilerplate (name: a-z 0-9 _ -)</label><input class=input id=plugnew placeholder="e.g. weather_lookup"></div>
    <div class=field style="align-self:end"><button class="btn btn-secondary" onclick=plugCreate()>Create boilerplate</button></div>
  </div>
  <div id=plugmsg class=msg style="margin-top:8px"></div>
  <div id=pluglist class=grid3 style="margin-top:20px"></div>
</section>

<section class="screen" id=s-mcp>
  <div class=sec-head>
    <div><h6>Catalog</h6><h3>MCP servers</h3></div>
    <span class="note text-muted">Attachable per instance · use <code>${SECRET_NAME}</code> in args to inject from the secret store at create time</span>
  </div>
  <div class=grid2 id=mcps></div>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px">New MCP server</h4>
    <div class=grid2>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=mcpname placeholder="e.g. homeassistant"></div>
      <div class=field><label>Description</label><input class=input id=mcpdesc placeholder="What is this MCP?"></div>
      <div class=field><label>Command</label><input class=input id=mcpcmd placeholder="e.g. mcp-remote  or  npx"></div>
      <div class=field><label>Args (one per line)</label><textarea class=input id=mcpargs style="min-height:80px;font-family:var(--font-mono);font-size:12.5px" placeholder="http://10.0.0.10:8123/mcp_server/sse&#10;--header&#10;Authorization: Bearer ${HA_TOKEN}"></textarea></div>
      <div class="field span2"><label>Env (KEY=VALUE per line; use ${SECRET} for secrets)</label><textarea class=input id=mcpenv style="min-height:64px;font-family:var(--font-mono);font-size:12.5px" placeholder="PORTAINER_URL=http://10.0.0.20:9000&#10;PORTAINER_API_KEY=${PORTAINER_API_KEY}"></textarea></div>
    </div>
    <div class=panel-foot><span id=mcpmsg class=msg></span><button class="btn btn-primary" onclick=saveMcp()>Save MCP</button></div>
  </div>
</section>

<section class="screen" id=s-resources>
  <div class=sec-head>
    <div><h6>Sizing &amp; live usage</h6><h3>Resources</h3></div>
    <span class="note text-muted">Configured size (vCPU/RAM) and live usage per instance &#183; CPU% is relative to ONE core (a 2-vCPU guest goes up to ~200%) &#183; Disk = written overlay layer.</span>
  </div>
  <table class=table id=restable>
    <thead><tr><th style="width:22%">Instance</th><th>Status</th><th>vCPU</th><th>RAM (config.)</th><th>RAM (used)</th><th>CPU</th><th>Disk (Overlay)</th></tr></thead>
    <tbody id=resrows><tr><td colspan=7 class=text-muted style="padding:14px">…</td></tr></tbody>
  </table>
</section>

<section class="screen" id=s-models>
  <div class=sec-head>
    <div><h6>OpenRouter</h6><h3>Models</h3></div>
    <span class="note text-muted">The full catalog, fetched live · ticked models are the shortlist offered when creating an instance</span>
  </div>
  <div class="banner blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke="var(--color-accent-700)" stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=10></circle><path d="M12 8v4"></path><path d="M12 16h.01"></path></svg>
    <span>The openrouter template only offers models that can do tool calling — a model without it stays hidden even when ticked.</span>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
    <input class=input id=mdlq placeholder="filter by id or name…" style="flex:1;min-width:220px;width:auto" oninput=renderModels()>
    <label class=radio><input type=checkbox id=mdltools checked onchange=renderModels()><span class=dot></span>tool calling only</label>
    <label class=radio><input type=checkbox id=mdlsel onchange=renderModels()><span class=dot></span>selected only</label>
    <span class=text-muted id=mdlcount style="font-size:12px"></span>
    <button class="btn btn-secondary btn-sm" onclick="loadModels2(1)">Refresh catalog</button>
  </div>
  <div style="max-height:60vh;overflow:auto;border:1px solid var(--color-divider)">
    <table class=table id=mdltable><tbody id=mdlrows></tbody></table>
  </div>
  <div class=panel-foot><span id=mdlmsg class=msg></span><button class="btn btn-primary" onclick=saveModels()>Save shortlist</button></div>
</section>

<section class="screen" id=s-policy>
  <div class=sec-head>
    <div><h6>What each instance may do & does</h6><h3>Policy</h3></div>
    <span class="note text-muted">Network, tools, secrets and MCP in one place · per instance the tools and URLs called most recently</span>
  </div>
  <div id=policycards style="display:flex;flex-direction:column;gap:18px"></div>
</section>

<section class="screen" id=s-tasks>
  <div class=sec-head>
    <div><h6>Scheduled work</h6><h3>Tasks</h3></div>
    <span class="note text-muted">One message runs on one instance — once or recurring · a stopped instance is started for it</span>
  </div>
  <table class=table>
    <thead><tr><th style="width:20%">Instance</th><th>Job</th><th>Schedule</th><th>Status</th><th>Last result</th><th></th></tr></thead>
    <tbody id=taskrows></tbody>
  </table>
  <div class="panel blueprint" style="margin-top:32px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <h4 style="margin:0 0 16px" id=tk-head>New task</h4>
    <div class=grid2>
      <div class=field><label>Instance</label><select class=input id=tk-inst></select></div>
      <div class=field><label>Schedule (empty = once, right away)</label>
        <input class=input id=tk-sched placeholder="every 30m · every 2h · daily 08:00 · hourly">
        <span class=text-muted style="font-size:12px">Formats: <code>every Nm|Nh|Nd</code>, <code>daily HH:MM</code>, <code>hourly</code></span>
      </div>
      <div class="field span2"><label>Job (the message sent to the agent)</label>
        <textarea class=input id=tk-msg style="min-height:90px" placeholder="e.g. Summarise the new Home Assistant events and report anything unusual."></textarea></div>
    </div>
    <div class=panel-foot><span id=tkmsg class=msg></span><button class="btn btn-secondary" id=tk-cancel style="display:none" onclick=cancelEdit()>Cancel</button><button class="btn btn-primary" id=tk-save onclick=saveTask()>Create task</button></div>
  </div>
</section>

<section class="screen" id=s-missions>
  <div class=sec-head>
    <div><h6>Multi-step work</h6><h3>Missions</h3></div>
    <span class="note text-muted">Multi-step jobs from the orchestrator — plan + progress survive restart and context reset · a finished task immediately triggers the next step</span>
  </div>
  <div id=missions class="panel blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <span class=text-muted style="font-size:13px">…</span>
  </div>
  <p class=text-muted style="font-size:12.5px;margin-top:14px">The orchestrator creates missions itself when a job needs several steps — e.g. via chat: "… — as a mission".</p>
</section>

<section class="screen" id=s-sharing>
  <div class=sec-head>
    <div><h6>Browser → Agent</h6><h3>katfs sharing</h3></div>
    <span class="note text-muted">A folder from the browser you are sitting at, handed to the agents over P2P (iroh) · nothing is mounted into the microVM</span>
  </div>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=katfs-status><span class=text-muted style="font-size:13px">checking…</span></div>
    <div class=field style="margin-top:18px">
      <label>Sharing key — node-id of the katfs node the browser connects to</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <input class="input mono" id=katfskey spellcheck=false autocomplete=off
               placeholder="64 hex characters" style="flex:1;min-width:260px;width:auto"
               oninput=keyHint()>
        <button class="btn btn-secondary" onclick=copyKey()>Copy</button>
        <button class="btn btn-secondary" onclick=resetKey() title="Back to this host's node-id">Reset</button>
      </div>
      <span class=text-muted id=keyhint style="font-size:12px"></span>
    </div>
    <div class=panel-foot>
      <span class=text-muted style="font-size:12px;margin-right:auto">The share lives in the browser tab — close it and the agents lose access.</span>
      <button class="btn btn-secondary" onclick=loadKatfs()>Refresh</button>
      <button class="btn btn-primary" onclick=openShare()>Share a folder…</button>
    </div>
  </div>
  <div class=grid2 style="margin-top:32px">
    <div class="card blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <span class=card-title style="font-size:16px">How the agent reaches it</span>
      <p class=card-body>The share is not a filesystem — it is reachable only through the agent tools
      <code>remote_ls</code>, <code>remote_read(path)</code> and <code>remote_write(path, content)</code>,
      which talk to the katfs node on the host gateway. Paths are relative to the shared folder.
      Without an active share those tools answer <code>503 no browser connected</code>.</p>
    </div>
    <div class="card blueprint">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <span class=card-title style="font-size:16px">Host folders instead</span>
      <p class=card-body>A folder that already lives on this host belongs in
      <b>Mount host folders</b> when creating an instance, or behind 📁 in the instance table —
      that one is a real live mount (NFS) inside the guest and survives without a browser tab.</p>
    </div>
  </div>

  <div class=sec-head style="margin-top:32px">
    <div><h6>Shared folder</h6><h3>File browser</h3></div>
    <span class="note text-muted">Browse the folder currently shared from a browser tab · read-only view</span>
  </div>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=fbbar style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">
      <button class="btn btn-secondary" id=fbup onclick=fbUp()>↑ up</button>
      <span id=fbpath class=mono style="font-size:12.5px;color:var(--color-neutral-600)">/</span>
      <span id=fbshare class=text-muted style="font-size:12px"></span>
      <button class="btn btn-ghost" id=fbdl style="margin-left:auto;font-size:12px" onclick=fbZip() disabled>&#8595; Download all</button>
      <button class="btn btn-ghost" style="font-size:12px" onclick="fbGo(FB.path)">Refresh</button>
    </div>
    <div id=fblist><span class=text-muted style="font-size:13px">…</span></div>
  </div>
</section>

<section class="screen" id=s-secrets>
  <div class=sec-head>
    <div><h6>Runtime access</h6><h3>Secrets access</h3></div>
    <span class="note text-muted">Which keys each template's agents may fetch via <code>get_secret(name)</code> · default is deny · values never written to disk</span>
  </div>
  <div class="banner blueprint" style="background:var(--color-neutral-100);margin:16px 0 20px;padding:9px 14px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <svg width=15 height=15 viewBox="0 0 24 24" fill=none stroke="var(--color-neutral-700)" stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>
    <span style="color:var(--color-neutral-800);font-size:12.5px">Keep dangerous keys (Docker / network admin) unchecked.</span>
  </div>
  <div id=secrets></div>
  <div class=panel-foot style="border:none;padding-top:0"><span id=secmsg class=msg></span><button class="btn btn-primary" onclick=saveSecrets()>Save access</button></div>
</section>

<section class="screen" id=s-changelog>
  <div class=sec-head>
    <div><h6>Open items</h6><h3>Security issues</h3></div>
    <span class="note text-muted">Findings from working on the system · text and rating live in <code>security.json</code>, only the status can be toggled here</span>
  </div>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div id=issues><span class=text-muted style="font-size:13px">…</span></div>
  </div>
  <div class=panel-foot><span id=secissuemsg class=msg></span>
    <label class=radio style="margin-right:auto"><input type=checkbox id=showdone onchange=renderIssues()><span class=dot></span>show fixed ones</label>
    <button class="btn btn-primary" onclick=saveIssues()>Save status</button></div>

  <div class=sec-head style="margin-top:44px">
    <div><h6>History</h6><h3>Changelog</h3></div>
    <span class="note text-muted">from <code>CHANGELOG.md</code></span>
  </div>
  <div class="panel blueprint md" id=changelog>
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
  </div>
</section>


<section class="screen" id=s-architecture>
  <div class=sec-head>
    <div><h6>System</h6><h3>Architecture</h3></div>
    <span class="note text-muted">every box below runs on this host, except the phone, the user PC and the external services</span>
  </div>

  <div class="panel blueprint" style="padding:18px">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <svg id="archsvg" viewBox="0 0 960 672" style="width:100%;height:auto;display:block;font-family:inherit">
      <defs><marker id="arw" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0 0 L8 4 L0 8 z" fill="var(--color-neutral-500)"/></marker></defs>

      <rect class="bx" x="40" y="16" width="230" height="56"/>
      <text class="tt" x="155" y="38" text-anchor="middle">KatAgent app (Android)</text>
      <text class="ss" x="155" y="56" text-anchor="middle">chat sync &#183; voice &#183; assistant key</text>
      <rect class="bx" x="330" y="16" width="230" height="56"/>
      <text class="tt" x="445" y="38" text-anchor="middle">Browser</text>
      <text class="ss" x="445" y="56" text-anchor="middle">admin UI / &#183; chat UI /chat</text>
      <rect class="bx" x="660" y="16" width="260" height="56"/>
      <text class="tt" x="790" y="38" text-anchor="middle">Signal (phone)</text>
      <text class="ss" x="790" y="56" text-anchor="middle">chat with katbot</text>

      <rect class="bx" x="185" y="124" width="230" height="52"/>
      <text class="tt" x="300" y="145" text-anchor="middle">Traefik &#183; TLS + basicAuth</text>
      <text class="ss" x="300" y="162" text-anchor="middle">__PUBLIC_HOST__</text>
      <rect class="bx" x="660" y="124" width="260" height="52"/>
      <text class="tt" x="790" y="145" text-anchor="middle">signal-cli REST</text>
      <text class="ss" x="790" y="162" text-anchor="middle">__SIGNAL_HOST__</text>

      <path class="ln" d="M155 72 L282 124"/>
      <path class="ln" d="M445 72 L318 124"/>
      <path class="ln" d="M790 72 L790 124"/>
      <path class="ln" d="M300 176 L300 224"/>

      <rect class="bx2" x="40" y="224" width="560" height="186"/>
      <text class="tt" x="60" y="248">manager.py &#183; :8700 (root, systemd)</text>
      <text class="ss" x="60" y="274">&#183; REST APIs + admin UI + chat UI</text>
      <text class="ss" x="60" y="294">&#183; chat sync: long-poll, shared store</text>
      <text class="ss" x="60" y="314">&#183; secret broker (guest by source IP)</text>
      <text class="ss" x="60" y="334">&#183; security gateway (unicode + image meta)</text>
      <text class="ss" x="60" y="354">&#183; task scheduler + orchestrator ping</text>
      <text class="ss" x="330" y="274">&#183; voice :8770 &#183; mcp-hub :8771 &#183; embed :8772</text>
      <text class="ss" x="330" y="294">&#183; signal send (allowlist + rate limit)</text>
      <text class="ss" x="330" y="314">&#183; usage &#183; audit &#183; memory</text>
      <text class="ss" x="330" y="334">&#183; katfs proxy &#8594; :8790</text>
      <text class="ss" x="330" y="354">&#183; guest POST allowlist (403 default)</text>
      <text class="ss" x="330" y="374">&#183; per-instance model / tools / mounts</text>

      <rect class="bx" x="660" y="224" width="260" height="56"/>
      <text class="tt" x="790" y="246" text-anchor="middle">voice service (Docker)</text>
      <text class="ss" x="790" y="264" text-anchor="middle">127.0.0.1:8770 &#183; Parakeet STT &#183; Piper TTS</text>
      <rect class="bx" x="660" y="312" width="260" height="56"/>
      <text class="tt" x="790" y="334" text-anchor="middle">katfs node &#183; :8790</text>
      <text class="ss" x="790" y="352" text-anchor="middle">P2P share &#8596; user PC</text>

      <path class="ln" d="M600 252 L660 252"/>
      <path class="ln" d="M600 340 L660 340"/>
      <path class="ln" d="M620 232 L680 180"/><text class="lb" x="665" y="205">/v2/send</text>

      <rect class="bx" x="40" y="444" width="560" height="44"/>
      <text class="ss" x="320" y="470" text-anchor="middle">chats.json &#183; memory.json &#183; history.db &#183; gateway.json &#183; settings.json &#183; instances/*.json &#183; audit/*.jsonl</text>
      <path class="ln" d="M320 410 L320 444"/>

      <rect class="bx" x="40" y="532" width="560" height="124"/>
      <text class="tt" x="60" y="556">Firecracker microVMs &#8212; one per agent</text>
      <text class="ss" x="60" y="580">&#183; tap fcN &#183; 172.30.N.2/30 &#183; NAT egress via host uplink</text>
      <text class="ss" x="60" y="600">&#183; private rootfs copy per start (sparse, removed on stop)</text>
      <text class="ss" x="60" y="620">&#183; guest: agent.py tool loop &#183; web_bridge :8080 &#183; webterm :7682</text>
      <text class="ss" x="60" y="640">&#183; MCP via manager &#8594; hub (no LAN, no tokens in guest)</text>
      <path class="ln" d="M320 488 L320 532"/>
      <path class="ln" d="M340 532 L340 488"/>
      <text class="lb" x="352" y="514">/i/&#8249;name&#8250; proxy &#183; broker &#183; tool calls</text>

      <rect class="bx" x="660" y="532" width="260" height="124"/>
      <text class="tt" x="790" y="556" text-anchor="middle">external</text>
      <text class="ss" x="790" y="580" text-anchor="middle">OpenRouter / Anthropic APIs</text>
      <text class="ss" x="790" y="600" text-anchor="middle">Home Assistant (MCP &#183; SSE)</text>
      <text class="ss" x="790" y="620" text-anchor="middle">Portainer (MCP, read-only)</text>
      <text class="ss" x="790" y="640" text-anchor="middle">user PC (katfs P2P)</text>
      <rect class="bx" x="660" y="400" width="260" height="56"/>
      <text class="tt" x="790" y="422" text-anchor="middle">mcp-hub (Docker)</text>
      <text class="ss" x="790" y="440" text-anchor="middle">127.0.0.1:8771 &#183; mcp-remote &#183; mcp-portainer</text>
      <path class="ln" d="M600 380 L660 424"/>
      <path class="ln" d="M790 456 L790 532"/>
      <path class="ln" d="M600 588 L660 588"/><text class="lb" x="606" y="580">NAT</text>
    </svg>
  </div>

  <div class=sec-head style="margin-top:44px">
    <div><h6>Reference</h6><h3>Components</h3></div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:14px">

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>manager.py &#8212; the core</span>
  <p class=card-body>Split into an <code>mgr/</code> package (ui, store, signal, missions, notify, rules, mcp, katfs, gateway); manager.py stays the systemd entry, facade and composition root (VM lifecycle, networking, secrets, the HTTP handler). mgr modules never import back (no cycles); cross-refs are injected. Single-file Python service (stdlib only), runs as root under systemd
  (<code>firecracker-manager</code>), listens on :8700 behind Traefik basicAuth. Serves the admin UI,
  the chat UI (<code>chatui.py</code>), and every API. Creates/starts/stops microVMs (openrouter rootfs boots as a shared read-only base +
  per-instance overlay upper &#8212; optionally persistent, so installs survive restarts), sets up
  tap devices and NAT, builds per-instance config disks, proxies requests into the guests
  (<code>/i/&#8249;name&#8250;/&#8230;</code>), and is the only component that guests can talk to.
  Guest requests are identified by source IP; writes from guests are limited to an explicit
  allowlist &#8212; everything else returns 403.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Firecracker microVMs</span>
  <p class=card-body>One VM per agent instance. Each gets a tap device <code>fc&#8249;N&#8250;</code> with a
  /30 subnet (host 172.30.N.1, guest 172.30.N.2) and NAT egress over the host uplink; internet
  can be switched off per instance. On every start the VM receives a fresh private copy of its
  template rootfs (sparse, ~550&#8201;MB real), deleted again on stop &#8212; VMs are stateless by design,
  durable state lives centrally. A small read-only config disk carries the non-secret instance
  settings into the guest.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Agent runtime (agent.py)</span>
  <p class=card-body>Tool-calling loop against an OpenAI-compatible backend inside each VM
  (templates: openrouter, <b>orcarouter</b>, pi, prime; the claude template runs Claude Code headless
  instead). The same agent code drives OpenRouter, <b>OrcaRouter</b> (gateway,
  <code>api.orcarouter.ai</code> or self-hosted OrcaRouter-Lite) and a local llama.cpp &#8212; the
  backend is picked by which env is set (<code>ORCAROUTER_MODEL</code> / <code>LLAMA_ENDPOINT</code>,
  else OpenRouter); the key comes from the Settings tab — with <code>LLM_KEY_PROXY</code> on (default here), LLM keys never enter a VM at all: agents call <code>/api/llm/&#8249;backend&#8250;</code> on the manager, which injects the Authorization header on egress (OneCLI pattern); the broker remains for other secrets. Built-in tools: bash, files,
  http_fetch, web_search, read_pdf, spawn_subagent, create_task, read_inbox, list_agents,
  recall_tasks, skills, memory, katfs remote files, secrets, send_signal. The system prompt
  (persona &#8594; <code>AGENT_SYSTEM</code>) always gets a standing memory instruction appended; on the
  first turn after a boot the agent injects its stored facts from <code>memory.json</code> into the
  prompt. Conversation context lives in VM RAM and dies with a restart &#8212; that is deliberate.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Harness patterns (context, goals, guardrails)</span>
  <p class=card-body>Ported from strands-agents/harness-sdk (Apache-2.0) into the stdlib agent, no new deps.
  <b>Summarizing context:</b> on overflow the oldest turns are folded into a pinned
  <code>[Summary]</code> block instead of being dropped &#8212; last ~10 turns stay verbatim.
  <b>Context offloader:</b> tool output over <code>OFFLOAD_MIN</code> is written to <code>.offload/</code>
  whole; the model sees a preview + reference and pages the rest via <code>offload_read</code>.
  <b>Goal loop:</b> <code>/goal &lt;criterion&gt;</code> makes a judge check each answer and refine it up to
  3 times. <b>Guardrails:</b> a hard bash denylist (rm&#8209;rf&#160;/, fork&#8209;bomb, mkfs) is always on; the <b>oracle</b> tool gives a second opinion before destructive actions (challenges assumptions, never acts — playbook-enforced for the orchestrator); risky
  tools can require Signal approval (<code>HITL=1</code> &#8594; manager asks &#8220;ok&#160;&lt;id&gt;&#8221;, routes
  <code>/api/hitl</code>). <b>Guardrails:</b> per-instance daily token budget + LLM rate-limit enforced at the key proxy, a task-frequency cap (>6/h -> paused), optional per-instance egress allowlist (<code>EGRESS_ALLOW</code>), and a secret leak-filter on outgoing notify/Signal. <b>Retry:</b> model calls back off on 429/5xx. <b>Local-model robustness:</b> llama.cpp/Qwen3 reasoning (<code>reasoning_content</code>) is streamed as a collapsible think block instead of being dropped; a tool-call-JSON 500 retries the turn without tools; and a heartbeat keeps the stream alive during long tool execution so a proxy idle-timeout can&#8217;t cut it mid-sentence. <b>Runtime control:</b> <code>/model</code> switches model/backend mid-session; <code>/steps &#8249;n&#8250;|unlimited</code> sets the per-turn tool-round cap; <b>steering</b> injects a user message between tool steps of a running turn (<code>POST /api/steer</code>); <b>prompt templates</b> (Personas tab) expand as <code>/name</code> in any channel; <b>tool plugins</b> (a single .py OR a multi-file folder in <code>plugins/</code>, added by drag-and-drop in the Plugins tab; each is SHA-256 content-pinned so a later out-of-band edit shows as \u201cmodified\u201d until re-approved) ride the config disk into the VM and register at agent start. <b>Tree-chat:</b> <code>/branch</code>/<code>/back</code> fork the context for a side question and fold it back into a one-line note.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Tests (E2E)</span>
  <p class=card-body>Stdlib <code>unittest</code>, no dependency: <code>tests/e2e.py</code> /
  <code>./run-tests.sh</code>. Three tiers that cleanly skip a missing environment &#8212;
  <b>OFFLINE</b> imports agent and manager directly and checks the core logic (backend choice,
  summarizing, offloader, hook denylist, goal, provider switch, HITL store, katfs ZIP walk);
  <b>HTTP</b> runs against the live manager (<code>/api/agents</code> backend+model,
  <code>/api/hitl</code>, katfs status); <b>LIVE</b> does a free <code>/goal</code> round-trip
  to the orchestrator VM. Runs on every change, together with the changelog and this tab.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Templates &amp; rootfs images</span>
  <p class=card-body><b>Install anywhere:</b> <code>install.sh</code> in the repo deploys the whole stack on a fresh KVM machine (preflight, layout, Firecracker download, builds, systemd) — verified end-to-end in a nested-KVM QEMU rig. Four templates (claude, openrouter, pi, prime), each with a Docker-built
  ext4 image under <code>instances/*.ext4</code>. Rebuilding an image and restarting an instance is
  the update path &#8212; the per-start copy guarantees every boot runs the current image. The
  openrouter image carries node (npx MCP servers), python, mcp-remote, mcp-portainer and
  poppler; the agent code itself is ~68&#8201;KB.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Secret broker &amp; policy</span>
  <p class=card-body><b>LLM keys go one step further:</b> with <code>LLM_KEY_PROXY</code> they never enter a VM — the manager injects them on egress (<code>/api/llm/&#8249;backend&#8250;</code>). API keys and tokens never land in instance configs or on the config disk.
  Guests fetch secrets at runtime from <code>/api/secret/&#8249;name&#8250;</code>; the manager identifies the
  instance by source IP and checks a per-instance/template allowlist
  (<code>secret-policy.json</code>). Sources: the 0600 secret store and the manager settings. MCP
  configs are assembled server-side the same way (<code>/api/mcp-config</code>).</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Security gateway</span>
  <p class=card-body>Per-chat toggle (shield icon in app and web). Strips invisible Unicode
  &#8212; tag characters U+E0020&#8211;E007F, zero-width, bidi overrides, homoglyph spaces, plus Unicode noncharacters (U+FDD0&#8211;FDEF, U+xFFFE/xFFFF) and reserved default-ignorables &#8212; from chat
  text in <em>both</em> directions, and EXIF/XMP/C2PA metadata from uploaded JPEG/PNG/WEBP,
  byte-surgically, before anything reaches the guest. Streams are cut at word boundaries so
  emoji ZWJ chains survive. State and counters live in <code>gateway.json</code>, filtering happens in
  the manager &#8212; a guest cannot switch it off. Removed characters are counted visibly.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Chat sync</span>
  <p class=card-body>One shared store (<code>chats.json</code>) for app, web and Signal turns.
  Clients long-poll <code>/api/chats?since=&#8249;rev&#8250;&amp;wait=&#8249;s&#8250;</code>; every write bumps a
  monotonic revision and wakes all waiters, so a message typed on the phone appears in the
  browser in sub-second time without polling. Deletions propagate too, via
  <b>tombstones</b> (<code>{id: deletedAt}</code>): the server never resurrects a deleted chat
  (even on an app re-push) unless it is genuinely re-edited afterwards. The store is display
  history &#8212; it is not fed back into the model.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Tasks &amp; orchestrator</span>
  <p class=card-body>Task queue with schedules (<code>every Nh</code>, <code>daily HH:MM</code>, &#8230;), editable in
  the Tasks tab. Tasks run on a capable instance or an ephemeral VM; results land in the shared
  chat history and in <code>history.db</code> (<code>task_runs</code>), queryable by agents via
  <code>recall_tasks</code>. New user messages ping the orchestrator instance, which routes work via
  <code>create_task</code> instead of doing it itself. Only the orchestrator (env <code>TASK_ADMIN</code>)
  gets the <code>list_tasks</code>/<code>delete_task</code>/<code>edit_task</code> tools, so it can prune or
  reschedule the queue itself; the matching <code>/api/task-delete</code> and <code>/api/task-edit</code> routes
  are gated to that instance. <code>llm_usage</code> in the same DB feeds the per-instance spend counter and the Activity panel&#8217;s per-window usage (<code>/api/usage/&#8249;name&#8250;?since=</code>): tokens are summed per time window, not attributed to single audit lines (a turn triggers 0..N tool calls).</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Voice</span>
  <p class=card-body>Docker container bound to 127.0.0.1:8770, reachable only through the
  manager (<code>/api/stt</code>, <code>/api/tts</code>). STT: Parakeet TDT v3 int8 (RTF &#8776;0.08 on this CPU),
  TTS: Piper (RTF &#8776;0.07) with three voices baked in (de-thorsten, de-eva_k, en-amy) &#8212;
  voice and speed are picked in the Settings tab and injected by the manager into every
  <code>/api/tts</code> call, so clients keep sending only the text. The app records AAC, the service
  converts via ffmpeg. Speech-to-send, tap-bubble-to-stop and barge-in live in the app; the
  long-press assistant key starts listening immediately.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Signal</span>
  <p class=card-body>Two directions, both through the signal-cli REST API
  (<code>__SIGNAL_HOST__</code>, run in <code>json-rpc</code> mode). Inbound: the manager holds a
  stdlib WebSocket to <code>/v1/receive</code>; a message from an allow-listed sender is handed straight to
  the orchestrator (prefixed <code>/fresh</code> so each trigger starts on a clean context) and the turn lands
  in the shared chat history. json-rpc mode fixed the native-mode lock where a long receive blocked sending.
  Outbound: agents call <code>send_signal</code>, the manager checks the recipient against
  <code>ALLOWED_SENDERS</code> (only people who may command the bot can be written to), rate-limits
  10 per 5 minutes, audits every call. Bot number and API stay on the host.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Notifications</span>
  <p class=card-body>A push channel alongside Signal: the agent tool <code>notify(title, message)</code> writes
  via <code>/api/notify</code> into a small store (<code>notifications.json</code>, rev + long-poll
  like the chat store, capped, rate-limited). It is fetched via <code>/api/notifications?since=&amp;wait=</code>:
  the <b>web manager</b> shows a bell with an unread badge + dropdown and can raise a browser notification;
  the <b>app</b> polls the same endpoint and raises an Android system notification. Every notification carries a <code>link</code> target
  (missions / tasks / chat:&#8249;instance&#8250;) — a click (web bell) or tap (Android) leads straight
  to the action. Unlike <code>send_signal</code> this rings on app/web, not in Signal.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>KatAgent app</span>
  <p class=card-body>Android/Compose client. Talks only to the manager: chat via
  <code>/i/&#8249;name&#8250;/api/chat[/stream]</code>, sync via long-poll, voice via <code>/api/stt|tts</code>,
  gateway toggle via <code>/api/gateway</code>. Registers as the digital assistant (long-press power)
  and starts recording on invocation; silence auto-sends (adaptive threshold, 1.8&#8201;s hang).
  Local Gemma mode works offline on-device.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>katfs</span>
  <p class=card-body>P2P file share between this host and the user PC (node on :8790, loopback).
  Agents reach it through manager-proxied tools (<code>remote_ls/read/write/delete</code>); the share
  page under Sharing manages it. Several browser tabs can serve at once &#8212; each is one share
  (id, name, device); the built-in file browser lists them, a click scopes the tree to one share,
  and <b>Download all</b> streams the current folder recursively as a ZIP (<code>/api/katfs/zip</code>).
  Gives agents a controlled window into user files without mounting anything into a VM.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Memory &#8212; short &amp; long term</span>
  <p class=card-body><b>Short term</b> is the conversation itself &#8212; the agent&#8217;s <code>_history</code> in VM RAM; <code>/reset</code> clears it, a restart too. <b>Long term is semantic:</b> <code>memory_store</code> embeds each note (multilingual-e5 on the CPU, <code>embed</code> container behind the manager) and stores text+vector in <code>history.db</code>. Every turn the agent embeds the user&#8217;s message and the manager returns the meaning-nearest notes (cosine), injected as a fresh <code>[Memory]</code> block &#8212; only what fits the question, not the whole store. No LLM and no graph DB needed, so it runs on this host today; degrades to no recall (never an error) if the embedder is down. A richer knowledge-graph memory (Graphiti/Cognee) stays a possible upgrade.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Playbooks &#8212; rules the agent learns</span>
  <p class=card-body>Standing rules that ALWAYS apply, distinct from the meaning-based semantic memory. When the user says how to do something, states a lasting preference, or corrects the approach, the agent records it with <code>playbook_add</code>; every turn all playbooks are injected as a <code>[Playbooks]</code> block, so the orchestrator&#8217;s know-how grows with the user&#8217;s wishes. Per-instance store (<code>playbooks.json</code>, cap 40), tools <code>playbooks</code>/<code>playbook_forget</code>. Editable in the Personas tab (Playbooks panel). Proven: teach &#8220;stock prices via http_fetch from Yahoo&#8221; once &#8594; after a context reset the vague question &#8220;how&#8217;s Apple?&#8221; is answered correctly without naming the source again.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Missions &#8212; multi-step autonomy</span>
  <p class=card-body>Plan + progress store for multi-step assignments, persisted on the host
  (<code>missions.json</code>) so the working state survives resets and restarts. The orchestrator
  plans (<code>mission_start</code>: goal + steps), delegates each step via <code>create_task</code>
  and records the task-id; when that task finishes, the worker <b>immediately</b> re-triggers the
  orchestrator to advance (event-driven, heartbeat only as fallback). Active missions are injected
  every turn as a <code>[Missions]</code> block. Guardrails: max 5 active / 20 steps, 7-day TTL
  auto-pause, finish writes a summary into semantic memory and pushes a notification. UI: Missions
  tab (web) / screen (app) with progress, current step and pause/abort.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Reasoning &amp; thinking</span>
  <p class=card-body>Per-agent runtime toggle via the slash command <code>/reasoning [low|medium|high|off]</code> (sets OpenRouter&#8217;s reasoning parameter; off by default, <code>OPENROUTER_REASONING</code> for a persistent default). The model&#8217;s thinking is streamed separately (marker-wrapped in the token stream, kept OUT of the conversation context so it never bloats follow-ups) and rendered in web and app as a collapsible &#8220;Denken&#8221; block; copy and speak take only the answer. Costs extra tokens, so it is a toggle, not always-on.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Storage (all under firecracker/)</span>
  <p class=card-body><code>instances/*.json</code> instance configs &#183; <code>chats.json</code> shared chat store &#183; <code>notifications.json</code> push-Benachrichtigungen &#183;
  <code>memory.json</code> per-agent key-value memory &#183; <code>playbooks.json</code> per-agent standing rules &#183;
  <code>history.db</code> task runs + LLM usage + semantic memory (vectors) &#183; <code>gateway.json</code> security-gateway state &#183;
  <code>settings.json</code> shared settings, 0600 &#183; <code>secret-policy.json</code> secret allowlists &#183;
  <code>personas.json</code>, <code>skills/</code>, <code>mcp-catalog.json</code> catalogs &#183;
  <code>audit/*.jsonl</code> per-instance tool audit trail &#183; <code>run/</code> pidfiles, sockets, logs,
  config disks and throwaway overlay uppers &#183; <code>instances/&#8249;n&#8250;-upper.ext4</code> persistent
  write layers (per-instance opt-in).</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>MCP servers &#8212; hub on the host</span>
  <p class=card-body>Catalog in <code>mcp-catalog.json</code> (homeassistant via <code>mcp-remote</code>/SSE,
  portainer via <code>mcp-portainer</code>, read-only). The server processes run in the
  <code>mcp-hub</code> container on the host (127.0.0.1:8771), one per (instance, server). Guests speak
  plain JSON-RPC to the manager (<code>/api/mcp</code>); the manager authorizes by source IP against the
  instance&#8217;s <code>MCP_SERVERS</code>, injects the secrets host-side and forwards to the hub &#8212;
  tokens and LAN never reach a VM. Every <code>tools/call</code> lands in the audit trail. A server is
  active only when listed in <code>MCP_SERVERS</code>; the hub respawns dead processes and replays
  their initialization.</p></div>

  <div class="card blueprint"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span class=card-title>Web UIs</span>
  <p class=card-body>Two pages, both served by the manager, both in the Industry design system:
  this admin UI (embedded in <code>manager.py</code>, hash-routed tabs) and the chat UI
  (<code>chatui.py</code> under <code>/chat</code>: streaming, images, voice, gateway toggle, per-browser
  history in localStorage). No CDN, no build step &#8212; one file each.</p></div>

  </div>
</section>

<section class="screen" id=s-settings style="max-width:640px">
  <div style="margin-bottom:18px"><h6 style="color:var(--color-accent);margin:0 0 2px">Shared, persisted</h6><h3 style="margin:0">Settings</h3></div>
  <p class=text-muted style="font-size:13px;margin-bottom:22px">Values apply to all new instances; empty template fields are pre-filled from here.</p>
  <div class="panel blueprint">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class="banner blueprint" style="margin:0 0 16px;padding:9px 14px"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><span id=voicestat class=text-muted style="font-size:12.5px">Voice: …</span></div>
    <div class=stack id=settings></div>
    <div class=panel-foot><span id=setmsg class=msg></span><button class="btn btn-primary" onclick=saveSettings()>Save</button></div>
  </div>
</section>

</main>

<footer class=appfoot>
  <span class=af-brand>kAIm56</span>
  <span class=af-stat>NAT via __HOSTIF__</span>
  <span class=af-stat>Pool __POOL__</span>
  <span class=af-stat id=spend></span>
  <nav class=af-nav>
    <a href="#changelog">Changelog</a>
    <a href="#architecture">Architecture</a>
  </nav>
</footer>
</div>

<div id=picker class=dialog-backdrop style="display:none;z-index:60">
  <div class="dialog blueprint" style="width:min(560px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Choose a host folder</div>
    <div style="display:flex;gap:6px">
      <input class=input id=pkpath spellcheck=false onkeydown="if(event.key==='Enter')pkGo(this.value)">
      <button class="btn btn-secondary" onclick="pkGo(document.getElementById('pkpath').value)">Go</button>
    </div>
    <div class=pkquick id=pkquick></div>
    <div class=pklist id=pklist></div>
    <div id=pkerr class=text-muted style="font-size:12px;min-height:1em"></div>
    <div class=dialog-actions>
      <span class=text-muted style="font-size:12px;margin-right:auto">Not on this host? <a href="#sharing" onclick=pkClose()>share it from your browser via katfs</a></span>
      <button class="btn btn-secondary" onclick=pkClose()>Cancel</button>
      <button class="btn btn-primary" onclick=pkChoose()>Use this folder</button>
    </div>
  </div>
</div>

<div id=mdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Host folders — <span id=mdlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>Host path → guest path, "ro" = read-only. Saved changes are picked up by the reconciler in the running guest.</div>
    <div id=mdlgrows></div>
    <div><button type=button class="btn btn-secondary btn-sm" onclick="addMount(null,'mdlgrows')">+ Folder</button></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=mdlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveMounts()>Save</button>
    </div>
  </div>
</div>

<div id=modeldlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(560px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Model — <span id=modeldlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>The new model takes effect after the next stop/start of the instance.</div>
    <div id=modeldlgbox></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=modelDlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveModel()>Save</button>
    </div>
  </div>
</div>

<div id=actdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class=dialog-title>Activity — <span id=actname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>Tools and targets called most recently (URLs/paths/queries). No secret values, no file contents.</div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:2px 0 10px">
      <div class=actwin id=actwin>
        <button data-w=3600 onclick="actWindow(3600)">1h</button>
        <button data-w=86400 onclick="actWindow(86400)">24h</button>
        <button data-w=604800 onclick="actWindow(604800)">7d</button>
        <button data-w=0 class=on onclick="actWindow(0)">Alle</button>
      </div>
      <span id=actsum class=text-muted style="font-size:12px;margin-left:auto"></span>
    </div>
    <div style="max-height:56vh;overflow:auto;border:1px solid var(--color-divider)">
      <table class=table><tbody id=actrows></tbody></table>
    </div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=actClose()>Close</button>
      <button class="btn btn-secondary" onclick="openActivity(ACT_CUR)">Refresh</button>
    </div>
  </div>
</div>

<script>
const TEMPLATES=__TPLJSON__;
const SETTINGS=__SETTINGS__;
const SETTINGS_SCHEMA=__SETTINGS_SCHEMA__;
const PERSONAS=__PERSONAS__;
const SKILLS=__SKILLS__;
const CORNERS='<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>';
const I_EDIT='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>';
const I_DEL='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
function esc(s){return (s||'').replace(/"/g,'&quot;')}
function escT(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

/* — tool selection in the create form — */
let TOOLS=[];
async function loadTools(){
  try{TOOLS=(await (await fetch('/api/agent-tools')).json()).tools||[];}catch(e){return;}
  document.getElementById('toolpick').innerHTML=TOOLS.map(t=>
    `<label class=radio style="font-size:13px"><input type=checkbox class=toolcb value="${esc(t.name)}" checked>`+
    `<span class=dot></span><span title="${esc(t.desc)}"><span class=mono style="font-size:12px">${escT(t.name)}</span></span></label>`).join('');
}
function toolAll(on){document.querySelectorAll('.toolcb').forEach(c=>c.checked=!!on)}

/* — Policy: what each instance may do (net/tools/secrets/MCP) + what it does (audit) — */
function _b64(buf){let str='',a=new Uint8Array(buf);for(let i=0;i<a.length;i+=0x8000)str+=String.fromCharCode.apply(null,a.subarray(i,i+0x8000));return btoa(str);}
async function loadPlugins(){
  let ps=[]; try{ps=(await (await fetch('/api/plugins')).json()).plugins||[];}catch(e){return;}
  const el=document.getElementById('pluglist'); if(!el)return;
  el.innerHTML = ps.length ? ps.map(p=>
    `<div class=card><div style="display:flex;justify-content:space-between;align-items:center;gap:8px">`+
    `<b>${escT(p.name)}</b><button class="btn btn-ghost" style="font-size:12px" onclick="plugDel('${esc(p.name)}')">L&#246;schen</button></div>`+
    `<div class=text-muted style="font-size:12px">${escT(p.kind)} &#183; ${p.files.length} file(s)</div>`+
    `<div class=mono style="font-size:11px;color:var(--color-neutral-600);word-break:break-all">${p.files.map(escT).join(', ')}</div>`+
    `<div style="display:flex;align-items:center;gap:8px;margin-top:8px">`+
      (p.modified
        ? `<span class="tag" style="background:#c0392b;color:#fff;font-size:11px">\u26a0 ge\u00e4ndert seit Approve</span>`
        : p.pinned
          ? `<span class="tag tag-accent" style="font-size:11px">\u2713 pinned ${escT(p.sha||'')}</span>`
          : `<span class="tag tag-neutral" style="font-size:11px">not pinned</span>`)+
      ((p.modified||!p.pinned)?`<button class="btn btn-secondary" style="font-size:12px;margin-left:auto" onclick="plugApprove('${esc(p.name)}')">Approve</button>`:``)+
    `</div></div>`
  ).join('') : '<div class=text-muted>No plugins yet.</div>';
}
async function plugUpload(file){
  if(!file)return;
  const isZip=/\.zip$/i.test(file.name), name=file.name.replace(/\.(py|zip)$/i,'');
  if(!/\.(py|zip)$/i.test(file.name)){document.getElementById('plugmsg').textContent='\u26a0\ufe0f only .py or .zip';return;}
  const b64=_b64(await file.arrayBuffer());
  const body=isZip?{name,kind:'zip',data_b64:b64}:{name,kind:'py',data_b64:b64};
  const d=await (await fetch('/api/plugins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('plugmsg').textContent=d.error?('\u26a0\ufe0f '+d.error):('\u2713 '+name+' saved \u2014 restart the instance to activate');
  loadPlugins();
}
async function plugCreate(){
  const inp=document.getElementById('plugnew'),name=inp.value.trim(); if(!name)return;
  const d=await (await fetch('/api/plugins/new',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
  document.getElementById('plugmsg').textContent=d.error?('\u26a0\ufe0f '+d.error):('\u2713 '+name+' created (tool.py) \u2014 edit in the folder, restart the instance');
  inp.value='';loadPlugins();
}
async function plugApprove(name){
  const d=await (await fetch('/api/plugins/'+encodeURIComponent(name)+'/pin',{method:'POST'})).json();
  document.getElementById('plugmsg').textContent=(d.msg==='approved'?'\u2713 '+name+' pinned ('+(d.sha||'')+')':(d.msg||'?'));
  loadPlugins();
}
async function plugDel(name){ if(!confirm('Plugin '+name+' l\u00f6schen?'))return;
  await fetch('/api/plugins/'+encodeURIComponent(name)+'/delete',{method:'POST'}); loadPlugins(); }
(function(){
  const dz=document.getElementById('plugdrop'); if(!dz)return;
  const fi=document.getElementById('plugfile');
  if(fi)fi.addEventListener('change',()=>{if(fi.files[0])plugUpload(fi.files[0]);fi.value='';});
  ['dragover','dragenter'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('drag');}));
  ['dragleave','dragend'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('drag');}));
  dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('drag');const f=e.dataTransfer.files[0];if(f)plugUpload(f);});
})();
let POL_TOOLS=[];
let POL_DIRTY=new Set();   // instances with not-yet-saved tool changes
document.addEventListener('DOMContentLoaded',()=>{
  const pc=document.getElementById('policycards');
  if(pc)pc.addEventListener('change',e=>{const pt=e.target&&e.target.dataset&&e.target.dataset.pt;if(pt)POL_DIRTY.add(pt);});
});
async function loadPolicy(auto){
  if(auto&&POL_DIRTY.size)return;   // do not overwrite unsaved checkboxes
  let pol={instances:[]};
  try{
    pol=await (await fetch('/api/policy')).json();
    if(!POL_TOOLS.length)POL_TOOLS=(await (await fetch('/api/agent-tools')).json()).tools||[];
  }catch(e){document.getElementById('policycards').innerHTML='<span class=text-muted>not reachable</span>';return;}
  const tag=(on,y,n)=>`<span class="tag ${on?'tag-accent':'tag-neutral'}">${on?y:n}</span>`;
  document.getElementById('policycards').innerHTML=(pol.instances||[]).map(p=>{
    const toolset=new Set(p.tools||[]);
    const tools=POL_TOOLS.map(t=>
      `<label class=radio style="font-size:12.5px"><input type=checkbox data-pt="${esc(p.name)}" value="${esc(t.name)}" ${p.tools_all||toolset.has(t.name)?'checked':''}>`+
      `<span class=dot></span><span class=mono style="font-size:11.5px" title="${esc(t.desc)}">${escT(t.name)}</span></label>`).join('');
    const secrets=(p.secrets||[]).map(s=>`<span class="tag tag-neutral" style="font-size:11px">${escT(s)}</span>`).join(' ')||'<span class=text-muted style="font-size:12px">none</span>';
    const mcps=(p.mcps||[]).map(s=>`<span class="tag tag-accent" style="font-size:11px">${escT(s)}</span>`).join(' ')||'<span class=text-muted style="font-size:12px">none</span>';
    return `<div class="card blueprint" style="padding:16px">${CORNERS}`+
      `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">`+
        `<span class=card-title style="font-size:17px">${escT(p.name)}</span>`+
        `<span class=text-muted style="font-size:12px">${escT(p.template)}</span>`+
        tag(p.running,'● running','○ off')+
        `<span style="margin-left:auto;display:flex;gap:8px;align-items:center">`+
          `<button class="btn ${p.internet?'btn-secondary':'btn-primary'} btn-sm" onclick="toggleNet('${esc(p.name)}',${(!p.internet)})">${p.internet?'🌐 internet on':'🚫 offline'}</button>`+
          `<button class="btn btn-secondary btn-sm" onclick="openActivity('${esc(p.name)}')">Activity</button>`+
        `</span></div>`+
      (p.model?`<div class=kv><b>Model</b><span class=mono style="font-size:12px">${escT(p.model)}</span></div>`:'')+
      `<div class=kv><b>Secrets</b><span>${secrets}</span></div>`+
      `<div class=kv><b>MCP</b><span>${mcps}</span></div>`+
      (p.katfs_share?`<div class=kv><b>katfs</b><span class=mono style="font-size:12px">${escT(p.katfs_share)}</span></div>`:'')+
      `<div style="margin-top:8px"><div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">`+
        `<b style="font-family:var(--font-heading);font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:color-mix(in srgb,var(--color-text) 60%,transparent)">Tools</b>`+
        `<button class="btn btn-ghost" style="font-size:12px" onclick="polToolAll('${esc(p.name)}',1)">all</button>`+
        `<button class="btn btn-ghost" style="font-size:12px" onclick="polToolAll('${esc(p.name)}',0)">none</button>`+
        `<button class="btn btn-primary btn-sm" style="margin-left:auto" onclick="savePolTools('${esc(p.name)}')">Save tools</button>`+
        `<span class=msg data-tmsg="${esc(p.name)}"></span>`+
      `</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:2px 14px">${tools}</div></div>`+
    `</div>`;
  }).join('')||'<span class=text-muted style="font-size:13px">no instances</span>';
  if(!auto)POL_DIRTY.clear();
}
function polToolAll(name,on){document.querySelectorAll(`input[data-pt="${CSS.escape(name)}"]`).forEach(c=>c.checked=!!on)}
function savePolTools(name){
  const tools=[...document.querySelectorAll(`input[data-pt="${CSS.escape(name)}"]:checked`)].map(c=>c.value);
  fetch(`/api/instances/${name}/tools`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tools})}).then(async r=>{
      const ok=r.ok; let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-tmsg="${CSS.escape(name)}"]`);
      if(ok){POL_DIRTY.delete(name); if(el)el.textContent=(d.msg||'saved')+' ✓';}
      else if(el)el.textContent='⚠️ '+(d.msg||('HTTP '+r.status));
    }).catch(e=>{const el=document.querySelector(`[data-tmsg="${CSS.escape(name)}"]`);if(el)el.textContent='⚠️ '+e;});
}
function _bar(pct,max,color){
  const w=Math.max(0,Math.min(100,max?100*pct/max:0));
  return `<div style="background:var(--color-neutral-200,#e5e7eb);border-radius:4px;height:6px;overflow:hidden;min-width:60px"><div style="width:${w}%;height:100%;background:${color}"></div></div>`;
}
async function loadResources(){
  let rs=[]; try{rs=(await (await fetch('/api/resources')).json()).resources||[];}catch(e){return;}
  const el=document.getElementById('resrows'); if(!el)return;
  el.innerHTML = rs.length ? rs.map(r=>{
    const run=r.running;
    const status=run?`<span class="tag tag-accent">\u25cf running</span>`:`<span class="tag tag-neutral">\u25cb off</span>`;
    const ramUsed = run&&r.rss_mb!=null ? `${r.rss_mb} MB ${_bar(r.rss_mb,r.mem_mib,'var(--color-accent)')}` : '\u2014';
    const cpu = run&&r.cpu_pct!=null ? `${r.cpu_pct}% ${_bar(r.cpu_pct,100*r.vcpus,'#2e9e6b')}` : '\u2014';
    const disk = r.upper_used_mb!=null ? `${r.upper_used_mb} MB${r.persist?' <span class="tag tag-accent" style="font-size:10px">persist</span>':''}` : '\u2014';
    return `<tr><td data-label=Instance><b style="font-family:var(--font-heading)">${escT(r.name)}</b></td>`+
      `<td data-label=Status>${status}</td>`+
      `<td data-label=vCPU style="font-variant-numeric:tabular-nums">${r.vcpus}</td>`+
      `<td data-label="RAM (config.)" style="font-variant-numeric:tabular-nums">${r.mem_mib} MiB</td>`+
      `<td data-label="RAM (used)"><div style="display:flex;align-items:center;gap:8px;font-size:12px">${ramUsed}</div></td>`+
      `<td data-label=CPU><div style="display:flex;align-items:center;gap:8px;font-size:12px">${cpu}</div></td>`+
      `<td data-label="Disk (Overlay)" style="font-size:12px">${disk}</td></tr>`;
  }).join('') : '<tr><td colspan=7 class=text-muted style="padding:14px">no instances</td></tr>';
}
let ACT_CUR='', ACT_EVENTS=[], ACT_WIN=0;
async function openActivity(name){
  ACT_CUR=name;
  document.getElementById('actname').textContent=name;
  document.getElementById('actdlg').style.display='grid';
  document.getElementById('actrows').innerHTML='<tr><td class=text-muted style="padding:12px">…</td></tr>';
  try{ACT_EVENTS=(await (await fetch('/api/audit/'+encodeURIComponent(name))).json()).events||[];}catch(e){ACT_EVENTS=[];}
  actRender();
}
function actWindow(sec){
  ACT_WIN=sec;
  document.querySelectorAll('#actwin button').forEach(b=>b.classList.toggle('on',(+b.dataset.w)===sec));
  actRender();
}
function actRender(){
  const cut = ACT_WIN ? (Date.now()/1000 - ACT_WIN) : 0;
  const ev = ACT_EVENTS.filter(e=>(e.ts||0) >= cut);
  document.getElementById('actrows').innerHTML=ev.map(e=>{
    const d=new Date((e.ts||0)*1000).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
    return `<tr><td class=text-muted style="white-space:nowrap;font-size:12px">${d}</td>`+
      `<td class=mono style="font-size:12.5px">${escT(e.tool)}${e.ok===false?' <span class="tag tag-neutral" style="font-size:10px">denied</span>':''}</td>`+
      `<td class=mono style="font-size:12px;word-break:break-all;color:var(--color-accent-700)">${escT(e.target||'')}</td></tr>`;
  }).join('')||'<tr><td class=text-muted style="padding:12px">nothing in the selected range</td></tr>';
  actUsage(cut, ev.length);
}
async function actUsage(cut, nEv){
  const el=document.getElementById('actsum'); if(!el)return;
  el.textContent='…';
  try{
    const u=await (await fetch('/api/usage/'+encodeURIComponent(ACT_CUR)+'?since='+Math.floor(cut))).json();
    const k=n=>n>=1000?(n/1000).toFixed(n>=100000?0:1)+'k':(''+n);
    el.textContent=`${nEv} Aktionen · ${u.calls} LLM-Aufrufe · ${k(u.in)}→${k(u.out)} Tokens · $${(u.cost||0).toFixed(4)}`;
  }catch(e){el.textContent=`${nEv} Aktionen`;}
}
function actClose(){document.getElementById('actdlg').style.display='none'}

/* — Tasks: scheduled work per instance (backend: /api/tasks, worker in the manager) — */
function fmtTs(t){if(!t)return '—';const d=new Date(t*1000);
  return d.toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
async function loadMissions(){
  const el=document.getElementById('missions'); if(!el)return;
  let d={}; try{d=await (await fetch('/api/missions')).json()}catch(e){}
  const all=d.by_instance?Object.entries(d.by_instance).flatMap(([i,l])=>l.map(m=>({...m,_inst:i})))
            :(d.missions||[]).map(m=>({...m,_inst:'orchestrator'}));
  const open=all.filter(m=>m.status==='active'||m.status==='paused');
  const closed=all.filter(m=>m.status==='done'||m.status==='failed').slice(-3);
  const cor='<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>';
  const bar=m=>{const t=(m.steps||[]).length||1,dn=(m.steps||[]).filter(s=>s.status==='done').length;
    return `<div style="display:flex;align-items:center;gap:8px;min-width:130px">
      <div style="flex:1;height:5px;background:var(--color-neutral-200)"><div style="width:${Math.round(dn/t*100)}%;height:100%;background:var(--color-accent)"></div></div>
      <span class=text-muted style="font-size:11.5px;white-space:nowrap">${dn}/${t}</span></div>`};
  const row=m=>{
    const cur=(m.steps||[]).find(s=>s.status==='doing')||(m.steps||[]).find(s=>s.status==='open');
    const st={active:'tag-accent',paused:'tag-neutral',done:'tag-accent-2',failed:'tag-neutral'}[m.status]||'tag-neutral';
    const log=(m.log||[]).slice(-1)[0]||'';
    return `<div style="padding:12px 4px;border-bottom:1px solid var(--color-divider)">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span class=mono style="font-size:11px;color:var(--color-neutral-500)">${escT(m.id)}</span>
        <b style="font-size:14.5px">${escT(m.goal)}</b>
        <span class="tag ${st}">${m.status}</span>
        ${bar(m)}
        <span style="margin-left:auto;display:flex;gap:6px">
          ${m.status==='active'?`<button class="btn btn-secondary btn-sm" onclick="missionAct('${esc(m.id)}','pause')">Pause</button>`:''}
          ${m.status==='paused'?`<button class="btn btn-secondary btn-sm" onclick="missionAct('${esc(m.id)}','resume')">Weiter</button>`:''}
          ${(m.status==='active'||m.status==='paused')?`<button class="btn btn-ghost btn-sm" onclick="missionAct('${esc(m.id)}','abort')">Cancel</button>`:''}
        </span>
      </div>
      ${cur?`<div class=text-muted style="font-size:12.5px;margin-top:4px">current step ${cur.n}: ${escT(cur.text)} [${cur.status}]${cur.task_id?` · task <span class=mono>${escT(cur.task_id)}</span>`:''}</div>`:''}
      ${m.summary?`<div class=text-muted style="font-size:12.5px;margin-top:4px">Summary: ${escT(m.summary)}</div>`:''}
      ${log?`<div class=text-muted style="font-size:11.5px;margin-top:3px;opacity:.75">${escT(log)}</div>`:''}
    </div>`};
  document.getElementById('missions').innerHTML=cor+
    (open.length||closed.length
      ? open.map(row).join('')
        + (closed.length?`<div class=text-muted style="margin:14px 0 4px;font-size:10px;letter-spacing:.1em;text-transform:uppercase">Zuletzt abgeschlossen</div>${closed.map(row).join('')}`:'')
      : '<span class=text-muted style="font-size:13px">No missions. The orchestrator creates them itself for multi-step jobs (mission_start) — e.g. via chat: "… — as a mission".</span>');
}
async function missionAct(id,action){
  if(action==='abort'&&!confirm('Mission '+id+' abbrechen?'))return;
  await fetch('/api/mission-admin',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,action})}).catch(()=>{});
  loadMissions();
}
async function loadTasks(){
  let tasks=[],insts=[];
  try{
    tasks=await (await fetch('/api/tasks')).json();
    insts=await (await fetch('/api/instances')).json();
  }catch(e){document.getElementById('taskrows').innerHTML='<tr><td colspan=6 class=text-muted>not reachable</td></tr>';return;}
  const sel=document.getElementById('tk-inst');
  if(sel)sel.innerHTML=insts.map(i=>`<option value="${esc(i.name)}">${escT(i.name)}</option>`).join('')||'<option value="">— no instance —</option>';
  const tag={scheduled:'tag-accent',pending:'tag-accent',running:'tag-accent',done:'tag-neutral',error:'tag-neutral'};
  TASKS=tasks||[];
  document.getElementById('taskrows').innerHTML=(tasks||[]).map(t=>{
    const nr=t.schedule?` · next ${fmtTs(t.next_run)}`:'';
    const res=(t.result||'').slice(0,120);
    return `<tr><td data-label=Instance class=mono>${escT(t.instance)}</td>`+
      `<td data-label=Job>${escT((t.message||'').slice(0,90))}${(t.message||'').length>90?'…':''}</td>`+
      `<td data-label=Schedule class=mono style="font-size:12px">${escT(t.schedule||'once')}${nr}</td>`+
      `<td data-label=Status><span class="tag ${tag[t.status]||'tag-neutral'}">${escT(t.status)}</span></td>`+
      `<td data-label=Result class=text-muted style="font-size:12px">${escT(res)}</td>`+
      `<td style="white-space:nowrap">`+
      `<button class="btn btn-icon btn-secondary" style="width:30px;height:30px;margin-right:4px" `+
        `title="Edit (schedule / job)" onclick="editTask('${esc(t.id)}')">${I_EDIT}</button>`+
      `<button class="btn btn-icon btn-secondary" style="width:30px;height:30px" `+
        `title=Delete onclick="delTask('${esc(t.id)}')">${I_DEL}</button></td></tr>`;
  }).join('')||'<tr><td colspan=6 class=text-muted style="padding:14px">no tasks yet</td></tr>';
}
let TASKS=[], TK_EDIT='';
/* Editing goes through the same form — a second dialog would be maintained
   twice. The instance stays locked: switching it would be a different task
   (different tools/secrets), that is what Create is for. */
function editTask(id){
  const t=TASKS.find(x=>x.id===id); if(!t)return;
  TK_EDIT=id;
  const inst=document.getElementById('tk-inst');
  inst.value=t.instance; inst.disabled=true;
  document.getElementById('tk-sched').value=t.schedule||'';
  document.getElementById('tk-msg').value=t.message||'';
  document.getElementById('tk-head').textContent='Edit task · '+t.instance;
  document.getElementById('tk-save').textContent='Save changes';
  document.getElementById('tk-cancel').style.display='';
  document.getElementById('tkmsg').textContent='';
  document.getElementById('tk-head').scrollIntoView({behavior:'smooth',block:'center'});
}
function cancelEdit(){
  TK_EDIT='';
  const inst=document.getElementById('tk-inst'); inst.disabled=false;
  document.getElementById('tk-sched').value='';
  document.getElementById('tk-msg').value='';
  document.getElementById('tk-head').textContent='New task';
  document.getElementById('tk-save').textContent='Create task';
  document.getElementById('tk-cancel').style.display='none';
}
function saveTask(){
  const instance=document.getElementById('tk-inst').value,
        message=document.getElementById('tk-msg').value.trim(),
        schedule=document.getElementById('tk-sched').value.trim();
  if(!message)return alert('Job?');
  if(TK_EDIT){
    fetch('/api/tasks/'+encodeURIComponent(TK_EDIT)+'/update',
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({message,schedule})})
      .then(r=>r.json()).then(d=>{
        document.getElementById('tkmsg').textContent=(d.msg||'saved')+' ✓';
        cancelEdit();loadTasks();});
    return;
  }
  if(!instance)return alert('Instance?');
  fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({instance,message,schedule})})
    .then(r=>r.json()).then(d=>{document.getElementById('tkmsg').textContent=(d.msg||'created')+' ✓';
      document.getElementById('tk-msg').value='';loadTasks();});
}
async function delTask(id){if(confirm('Delete task?')){await fetch('/api/tasks/'+encodeURIComponent(id)+'/delete',{method:'POST'});loadTasks();}}

/* — Usage: the same numbers as server-side, just fetched afterwards — */
function fmtTok(n){n=n||0;
  return n>=1e6?(n/1e6).toFixed(1).replace(/\\.0$/,'')+'M'
       : n>=1000?(n/1000).toFixed(1).replace(/\\.0$/,'')+'k' : String(n)}
function fmtCost(c){c=c||0; return '$'+(c>=0.01?c.toFixed(2):c.toFixed(4))}
async function refreshUsage(){
  let u; try{u=await (await fetch('/api/usage')).json()}catch(e){return}
  let day=0,all=0;
  document.querySelectorAll('[data-usage]').forEach(el=>{
    const v=u[el.dataset.usage]; if(!v)return;
    el.innerHTML='Tokens heute '+fmtTok(v.today.in)+'&nbsp;/&nbsp;'+fmtTok(v.today.out)+
      ' · '+fmtCost(v.today.cost)+' &nbsp;·&nbsp; gesamt '+fmtTok(v.total.in)+
      '&nbsp;/&nbsp;'+fmtTok(v.total.out)+' · '+fmtCost(v.total.cost);
  });
  Object.values(u).forEach(v=>{day+=(v.today||{}).cost||0; all+=(v.total||{}).cost||0});
  const f=document.getElementById('spend');
  if(f)f.textContent='LLM heute '+fmtCost(day)+' · gesamt '+fmtCost(all);
}

/* — tabs (hash-routed, so a reload after an action keeps the screen) — */
const TABS=['instances','personas','skills','plugins','mcp','tasks','missions','policy','models','resources','sharing','secrets','settings','changelog','architecture'];
function showTab(t){
  if(TABS.indexOf(t)<0)t='instances';
  TABS.forEach(x=>document.getElementById('s-'+x).classList.toggle('on',x===t));
  document.querySelectorAll('#tabs a,.af-nav a').forEach(a=>{
    if(a.getAttribute('href')==='#'+t)a.setAttribute('aria-current','page');
    else a.removeAttribute('aria-current');});
}
window.addEventListener('hashchange',()=>{const t=location.hash.slice(1);showTab(t);if(t==='missions')loadMissions();});

async function loadPlaybooks(){
  const sel=document.getElementById('pbinst');
  if(!sel.options.length){
    try{const insts=await (await fetch('/api/instances')).json();
      sel.innerHTML=insts.map(i=>`<option${i.name==='orchestrator'?' selected':''}>${escT(i.name)}</option>`).join('');
    }catch(e){}
  }
  const inst=sel.value||'orchestrator';
  let pbs=[]; try{pbs=(await (await fetch('/api/playbooks?instance='+encodeURIComponent(inst))).json()).playbooks||[]}catch(e){}
  document.getElementById('pblist').innerHTML=pbs.length?pbs.map(p=>
    `<div style="display:flex;gap:10px;align-items:baseline;padding:7px 4px;border-bottom:1px solid var(--color-divider)">`+
    `<span class=mono style="flex:none;font-size:11px;color:var(--color-neutral-500)">${escT(p.id||'')}</span>`+
    `<span style="flex:1;font-size:13px">${escT(p.text||'')}</span>`+
    `<button class="btn btn-ghost btn-sm" onclick="delPlaybook('${esc(inst)}','${esc(p.id)}')">✕</button></div>`).join('')
    :'<span class=text-muted style="font-size:13px">No rules for this agent.</span>';
}
async function addPlaybook(){
  const inst=document.getElementById('pbinst').value,text=document.getElementById('pbtext').value.trim();
  if(!text)return;
  const d=await (await fetch('/api/playbook-add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({instance:inst,text})})).json();
  document.getElementById('pbmsg').textContent=d.added?'added ✓':(d.note||'?');
  if(d.added)document.getElementById('pbtext').value='';
  loadPlaybooks();
}
async function delPlaybook(inst,id){
  await fetch('/api/playbook-remove',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({instance:inst,id})});
  loadPlaybooks();
}
let PROMPTS=[];
async function loadPrompts(){
  try{PROMPTS=(await (await fetch('/api/prompts')).json()).prompts||[]}catch(e){PROMPTS=[]}
  const el=document.getElementById('promptlist'); if(!el)return;
  el.innerHTML=PROMPTS.length?PROMPTS.map(p=>
    `<div style="display:flex;gap:10px;align-items:baseline;padding:7px 4px;border-bottom:1px solid var(--color-divider)">`+
    `<code style="flex:none">/${escT(p.name)}</code>`+
    `<span class=text-muted style="flex:1;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escT(p.text)}</span>`+
    `<button class="btn btn-ghost btn-sm" onclick="editPrompt('${esc(p.name)}')">Edit</button>`+
    `<button class="btn btn-ghost btn-sm" onclick="delPrompt('${esc(p.name)}')">✕</button></div>`).join('')
    :'<span class=text-muted style="font-size:13px">No templates. Create one below — then usable in chat via /name.</span>';
}
function editPrompt(n){const p=PROMPTS.find(x=>x.name===n);if(!p)return;
  document.getElementById('prname').value=p.name;document.getElementById('prtext').value=p.text;}
async function savePrompt(){
  const name=document.getElementById('prname').value.trim(),text=document.getElementById('prtext').value.trim();
  const d=await (await fetch('/api/prompts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,text})})).json();
  document.getElementById('prmsg').textContent=d.msg||'?';
  if(d.msg==='saved'){document.getElementById('prname').value='';document.getElementById('prtext').value='';}
  loadPrompts();
}
async function delPrompt(n){
  if(!confirm('Delete template /'+n+'?'))return;
  await fetch('/api/prompts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:n,delete:true})});
  loadPrompts();
}
function renderPersonas(){
  document.getElementById('personas').innerHTML=PERSONAS.map(p=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:10px">`+
    `<span class=card-title style="font-size:16px">${escT(p.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:4px">`+
    `<button class="btn btn-ghost" style="font-size:12px" onclick="editPersona('${esc(p.name)}')">Edit</button>`+
    `<button class="btn btn-ghost" style="font-size:12px;color:var(--color-neutral-600)" onclick="delPersona('${esc(p.name)}')">Delete</button>`+
    `</span></div>`+
    `<p class=card-body style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${escT(p.prompt||'')}</p></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
  const sel=document.getElementById('persona');
  if(sel)sel.innerHTML='<option value="">— Default —</option>'+PERSONAS.map(p=>`<option value="${esc(p.name)}">${escT(p.name)}</option>`).join('');
}
function editPersona(n){const p=PERSONAS.find(x=>x.name===n);if(!p)return;
  document.getElementById('pname').value=p.name;document.getElementById('pprompt').value=p.prompt||'';
  document.getElementById('pname').scrollIntoView({behavior:'smooth'});}
function savePersona(){
  const name=document.getElementById('pname').value.trim(),prompt=document.getElementById('pprompt').value;
  if(!name)return alert('Name?');
  fetch('/api/personas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,prompt})})
    .then(()=>location.reload());
}
async function delPersona(n){if(confirm('Delete persona '+n+'?')){await fetch('/api/personas/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}

function renderSkills(){
  document.getElementById('skills').innerHTML=SKILLS.map(s=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:8px">`+
    `<span class=card-title style="font-size:15px;font-family:var(--font-mono);font-weight:500">${escT(s.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:2px">`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px" title=Edit onclick="editSkill('${esc(s.name)}')">${I_EDIT}</button>`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px;color:var(--color-neutral-600)" title=Delete onclick="delSkill('${esc(s.name)}')">${I_DEL}</button>`+
    `</span></div>`+
    `<p class=card-body style="font-size:12.5px">${escT(s.description||'')}</p></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
}
function editSkill(n){const s=SKILLS.find(x=>x.name===n);if(!s)return;
  document.getElementById('skname').value=s.name;document.getElementById('skdesc').value=s.description||'';
  document.getElementById('skcontent').value=s.content||'';document.getElementById('skname').scrollIntoView({behavior:'smooth'});}
function saveSkill(){
  const name=document.getElementById('skname').value.trim(),description=document.getElementById('skdesc').value,
        content=document.getElementById('skcontent').value;
  if(!name)return alert('Name?');
  fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description,content})})
    .then(()=>location.reload());
}
async function delSkill(n){if(confirm('Delete skill '+n+'?')){await fetch('/api/skills/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}

function renderSettings(){
  // Key fields are masked via CSS (-webkit-text-security) instead of
  // type=password: a real password field makes Chrome's password manager
  // offer "Save password?" on navigating away — with the katfs node-id as a
  // supposed username. Chrome ignores autocomplete=off there.
  document.getElementById('settings').innerHTML=SETTINGS_SCHEMA.map(s=>{
    if(s.options)return `<div class=field><label>${escT(s.label)}</label><select class=input data-s="${esc(s.key)}">`+
      s.options.map(o=>`<option value="${esc(o.value)}"${(SETTINGS[s.key]||'')===o.value?' selected':''}>${escT(o.label)}</option>`).join('')+`</select></div>`;
    return `<div class=field><label>${escT(s.label)}</label><input class="input${s.key.indexOf('KEY')>=0?' seckey':''}" data-s="${esc(s.key)}" value="${esc(SETTINGS[s.key])}" `+
    `type=text autocomplete=off spellcheck=false></div>`;}).join('');
  voiceHealth();
}
async function voiceHealth(){
  const el=document.getElementById('voicestat'); if(!el)return;
  try{
    const d=await (await fetch('/api/voice-health')).json();
    el.innerHTML=`<span class="tag ${d.ready?'tag-accent':'tag-neutral'}">${d.ready?'● up':'○ loading'}</span> `+
      `STT <code>${escT(d.asr||'?')}</code> · TTS-Stimmen: ${(d.voices||[]).map(v=>'<code>'+escT(v)+'</code>').join(' ')}`;
  }catch(e){el.innerHTML='<span class="tag tag-neutral">○ voice service not reachable</span>';}
}
function saveSettings(){
  const d={};document.querySelectorAll('#settings input,#settings select').forEach(i=>d[i.dataset.s]=i.value);
  fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
    .then(()=>{Object.assign(SETTINGS,d);document.getElementById('setmsg').textContent='saved ✓';renderParams()});
}
function fieldFor(p){
  const v=p.default||SETTINGS[p.key]||'';
  if(p.type==='select'&&p.options)
    // An entry is either a string or {value,label} — so an option can carry
    // a readable label (e.g. "— account default —").
    return `<select class=input data-k="${p.key}" ${p.key==='TRANSPORT'?'onchange=applyTransport()':''}>`+p.options.map(o=>{
      const val=(o&&typeof o==='object')?(o.value||''):o, lbl=(o&&typeof o==='object')?(o.label||o.value||''):o;
      return `<option value="${esc(val)}"${val===v?' selected':''}>${escT(lbl)}</option>`;
    }).join('')+`</select>`;
  if(p.type==='select'&&p.source==='openrouter')
    return `<div style="display:flex;gap:6px">${orSelect(p.key,v,p.tools,p.relevant)}`
      +`<button type=button class="btn btn-secondary btn-icon" title="Refresh list" onclick="orRefresh(this)">⟳</button></div>`;
  return `<input class=input data-k="${p.key}" value="${esc(v)}">`;
}
/* — model pickers: a dropdown off the OpenRouter catalog, with "other model
     id…" as the way back to free text (direct providers are not in the list) — */
const OR_CUSTOM='__custom__';
function orSelect(key,v,tools,relevant){
  return `<select class=input style="flex:1;min-width:0" data-k="${esc(key)}" data-or=1 `
    +`${tools?'data-tools=1':''} ${relevant?'data-relevant=1':''} onchange="orPick(this)">`
    +`<option value="${esc(v)}">${esc(v)||'— loading… —'}</option></select>`;
}
function loadModels(sel,force){
  const cur=sel.value; sel.disabled=true;
  let q='?'; if(force)q+='refresh=1&'; if(sel.dataset.tools)q+='tools=1&'; if(sel.dataset.relevant)q+='relevant=1';
  fetch('/api/openrouter-models'+q).then(r=>r.json()).then(ms=>{
    // The fetch (upstream openrouter.ai) can take seconds. In that time the
    // user may long since have picked something ELSE — or the select was
    // replaced by a template switch. So: read the value NOW (not the one from
    // the fetch start) and never touch detached selects again.
    // Otherwise the dropdown seemingly jumps back to the initial value for no reason.
    if(!sel.isConnected){return}
    const now=sel.value||cur;
    sel.innerHTML=(ms.length?ms:[{id:now,label:now+' (list n/a)'}]).map(m=>
      `<option value="${m.id}"${m.id===now?' selected':''}>${m.label}</option>`).join('')
      +`<option value="${OR_CUSTOM}">— other model id… —</option>`;
    // A value outside the shortlist must not silently flip to the first entry
    // — it stays as its own option.
    if(now&&!ms.some(m=>m.id===now))
      sel.insertAdjacentHTML('afterbegin',`<option value="${esc(now)}" selected>${escT(now)} (not in the shortlist)</option>`);
    sel.disabled=false;
  }).catch(()=>{sel.disabled=false});
}
function orPick(sel){
  if(sel.value!==OR_CUSTOM)return;
  const box=sel.parentNode;
  box.dataset.tools=sel.dataset.tools||''; box.dataset.relevant=sel.dataset.relevant||'';
  sel.outerHTML=`<input class=input style="flex:1;min-width:0" data-k="${esc(sel.dataset.k)}" placeholder="provider/model-id">`;
  box.querySelector('button').title='Back to the list';
  box.querySelector('input[data-k]').focus();
}
function orRefresh(btn){
  const box=btn.parentNode, sel=box.querySelector('select[data-or]');
  if(sel)return loadModels(sel,1);
  const inp=box.querySelector('input[data-k]');
  if(!inp)return;
  inp.outerHTML=orSelect(inp.dataset.k,inp.value.trim(),box.dataset.tools,box.dataset.relevant);
  btn.title='Refresh list';
  loadModels(box.querySelector('select[data-or]'),0);
}
function renderParams(){
  const t=document.getElementById('tpl').value;
  const tpl=TEMPLATES.find(x=>x.template===t)||{params:[]};
  document.getElementById('params').innerHTML=(tpl.params||[]).map(p=>
    `<div class=field data-pk="${p.key}"><label>${escT(p.label||p.key)}</label>${fieldFor(p)}</div>`).join('');
  document.querySelectorAll('#params select[data-or]').forEach(s=>loadModels(s,0));
  applyTransport();
}
function applyTransport(){
  const sel=document.querySelector('#params [data-k="TRANSPORT"]');
  const web=sel&&sel.value==='web';
  ['SIGNAL_NUMBER','ALLOWED_SENDERS'].forEach(k=>{
    const el=document.querySelector('#params [data-pk="'+k+'"]');
    if(el)el.style.display=web?'none':'';
  });
}
function mountRow(m){m=m||{};return `<div class=mrow>`+
  `<input class="input mh" placeholder="/host/path" value="${esc(m.host)}" style="flex:2;min-width:150px;width:auto">`+
  `<button type=button class="btn btn-secondary btn-icon" style="width:32px;height:32px" title="Browse the host…" onclick="pkRow(this)">📁</button>`+
  `<input class="input mg" placeholder="/mnt/name" value="${esc(m.guest)}" style="flex:2;min-width:120px;width:auto">`+
  `<label class=radio><input type=checkbox class=mr ${m.readonly?'checked':''}><span class=dot></span>ro</label>`+
  `<button type=button class="btn btn-secondary btn-icon" style="width:32px;height:32px" onclick="this.parentNode.remove()">✕</button></div>`}
function addMount(m,target){document.getElementById(target||'mounts').insertAdjacentHTML('beforeend',mountRow(m))}

/* — folder picker: browses the host through /api/browse (directories only) — */
let PK={cb:null};
const PK_QUICK=['__HOME__','/mnt','/srv','/media','/opt','/'];
function pkOpen(start,cb){
  PK.cb=cb;
  document.getElementById('pkquick').innerHTML=PK_QUICK.map(p=>
    `<button type=button class="btn btn-secondary btn-sm" data-p="${esc(p)}">${escT(p)}</button>`).join('');
  document.getElementById('picker').style.display='grid';
  pkGo(start||'/home/ulrich');
}
function pkClose(){document.getElementById('picker').style.display='none';PK.cb=null}
async function pkGo(p){
  let d;
  try{d=await (await fetch('/api/browse?path='+encodeURIComponent(p||'/'))).json()}
  catch(e){d={path:p,parent:'',dirs:[],error:'not reachable'}}
  document.getElementById('pkpath').value=d.path||p;
  document.getElementById('pkerr').textContent=d.error||'';
  const up=d.parent?`<button type=button class="btn pkrow" data-p="${esc(d.parent)}">↑ ..</button>`:'';
  const base=(d.path==='/'?'':d.path);
  const rows=(d.dirs||[]).map(n=>
    `<button type=button class="btn pkrow" data-p="${esc(base+'/'+n)}">📁 ${escT(n)}</button>`).join('');
  document.getElementById('pklist').innerHTML=up+rows||
    '<span class=text-muted style="font-size:13px;padding:6px">no sub-folders</span>';
}
function pkChoose(){
  const p=document.getElementById('pkpath').value.trim(),cb=PK.cb;
  pkClose(); if(cb&&p)cb(p);
}
function pkRow(btn){
  const row=btn.closest('.mrow'),h=row.querySelector('.mh'),g=row.querySelector('.mg');
  pkOpen(h.value||'/home/ulrich',p=>{
    h.value=p;
    if(!g.value){const b=p.split('/').filter(Boolean).pop();if(b)g.value='/mnt/'+b}
  });
}
function collectMounts(scope){return [...scope.querySelectorAll('.mrow')].map(r=>({
  host:r.querySelector('.mh').value.trim(),guest:r.querySelector('.mg').value.trim(),
  readonly:r.querySelector('.mr').checked})).filter(m=>m.host&&m.guest)}
function create(){
  const t=document.getElementById('tpl').value,n=document.getElementById('nm').value;
  if(!n)return alert('Name?');
  const cfg={};document.querySelectorAll('#params [data-k]').forEach(i=>cfg[i.dataset.k]=i.value);
  const pn=document.getElementById('persona').value;
  if(pn){const p=PERSONAS.find(x=>x.name===pn);if(p)cfg.AGENT_SYSTEM=p.prompt;}
  const ks=document.getElementById('katfsshare').value;
  if(ks)cfg.KATFS_SHARE=ks;
  const mcps=[...document.querySelectorAll('#mcp-pick input[type=checkbox]:checked')].map(c=>c.value);
  const mounts=collectMounts(document.getElementById('mounts'));
  const internet=document.getElementById('cap-net').checked;
  const tools=[...document.querySelectorAll('.toolcb:checked')].map(c=>c.value);
  fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:n,template:t,config:cfg,mcps,mounts,internet,tools})}).then(()=>location.reload());
}
let MCPS=[];
async function renderMcps(){
  try{MCPS=await (await fetch('/api/mcps')).json();}catch(e){return;}
  const box=document.getElementById('mcps');
  if(box)box.innerHTML=MCPS.map(m=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:10px">`+
    `<span class=card-title style="font-size:16px">${escT(m.name)}</span>`+
    `<span style="margin-left:auto;display:flex;gap:4px">`+
    `<button class="btn btn-ghost" style="font-size:12px" onclick="editMcp('${esc(m.name)}')">Edit</button>`+
    `<button class="btn btn-ghost" style="font-size:12px;color:var(--color-neutral-600)" onclick="delMcp('${esc(m.name)}')">Delete</button>`+
    `</span></div>`+
    `<p class=card-body>${escT(m.description||'')}</p>`+
    `<div class=cmd>${escT(m.command||'')} ${escT((m.args||[]).join(' '))}</div></div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
  const pick=document.getElementById('mcp-pick');
  if(pick)pick.innerHTML=MCPS.map(m=>
    `<label class=radio><input type=checkbox value="${esc(m.name)}"><span class=dot></span>${escT(m.name)}</label>`)
    .join('')||'<span class=text-muted style="font-size:12px">no MCPs in catalog</span>';
}
function editMcp(n){const m=MCPS.find(x=>x.name===n);if(!m)return;
  document.getElementById('mcpname').value=m.name;document.getElementById('mcpdesc').value=m.description||'';
  document.getElementById('mcpcmd').value=m.command||'';document.getElementById('mcpargs').value=(m.args||[]).join('\\n');
  document.getElementById('mcpenv').value=Object.entries(m.env||{}).map(e=>e[0]+'='+e[1]).join('\\n');
  document.getElementById('mcpname').scrollIntoView({behavior:'smooth'});}
function saveMcp(){
  const name=document.getElementById('mcpname').value.trim(),description=document.getElementById('mcpdesc').value,
        command=document.getElementById('mcpcmd').value.trim(),
        args=document.getElementById('mcpargs').value.split('\\n').map(s=>s.replace(/\\r$/,'')).filter(s=>s.length);
  const env={};
  document.getElementById('mcpenv').value.split('\\n').forEach(l=>{l=l.replace(/\\r$/,'');const i=l.indexOf('=');if(i>0)env[l.slice(0,i).trim()]=l.slice(i+1);});
  if(!name||!command)return alert('Name + Command?');
  fetch('/api/mcps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description,command,args,env})})
    .then(()=>location.reload());
}
async function delMcp(n){if(confirm('Delete MCP '+n+'?')){await fetch('/api/mcps/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}
let MDLG='';
async function editMounts(name){
  const list=await (await fetch('/api/instances')).json();
  const inst=list.find(i=>i.name===name)||{};
  MDLG=name;
  document.getElementById('mdlgname').textContent=name;
  document.getElementById('mdlgrows').innerHTML='';
  (inst.mounts||[]).forEach(m=>addMount(m,'mdlgrows'));
  document.getElementById('mdlg').style.display='grid';
}
function mdlgClose(){document.getElementById('mdlg').style.display='none'}
async function saveMounts(){
  const mounts=collectMounts(document.getElementById('mdlgrows'));
  const r=await fetch(`/api/instances/${MDLG}/mounts`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({mounts})});
  const d=await r.json(); mdlgClose();
  alert(d.msg||'ok');location.reload();
}

/* — Model switch for existing instances: the same picker as on create
     (fieldFor renders the OpenRouter list or the curated options per template),
     prefilled with the instance's current model. */
let MODELDLG='';
async function editModel(name){
  const list=await (await fetch('/api/instances')).json();
  const inst=list.find(i=>i.name===name)||{};
  const cfg=inst.config||{};
  const key=['OPENROUTER_MODEL','ORCAROUTER_MODEL','ANTHROPIC_MODEL','PI_MODEL','PRIME_MODEL','LLAMA_MODEL'].find(k=>k in cfg);
  if(!key)return alert('This instance has no model setting.');
  const tpl=TEMPLATES.find(t=>t.template===inst.template)||{params:[]};
  const p=(tpl.params||[]).find(x=>x.key===key)||{key:key};
  MODELDLG=name;
  document.getElementById('modeldlgname').textContent=name;
  // fieldFor takes p.default as the preset — a copy with the current model.
  document.getElementById('modeldlgbox').innerHTML=
    fieldFor(Object.assign({},p,{default:cfg[key]||''}));
  document.getElementById('modeldlg').style.display='grid';
  const sel=document.querySelector('#modeldlgbox select[data-or]');
  if(sel)loadModels(sel,0);
}
function modelDlgClose(){document.getElementById('modeldlg').style.display='none'}
async function saveModel(){
  const el=document.querySelector('#modeldlgbox [data-k]');
  const model=(el?el.value:'').trim();
  if(!model||model===OR_CUSTOM)return alert('Model id?');
  const r=await fetch(`/api/instances/${MODELDLG}/model`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
  const d=await r.json(); modelDlgClose();
  alert(d.msg||'ok');location.reload();
}

/* — Changelog + security: findings live in security.json, the UI only toggles
     the status. Markdown is rendered deliberately minimally (headings, lists,
     bold, code) — after escaping, so nothing can break out of the text. */
let ISSUES=[];
const SEVORDER={high:0,medium:1,low:2};
async function loadChangelog(){
  try{
    const [c,i]=await Promise.all([
      (await fetch('/api/changelog')).json(),
      (await fetch('/api/security')).json()]);
    document.getElementById('changelog').innerHTML=
      '<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>'+md(c.text||'');
    ISSUES=(i.issues||[]).slice().sort((a,b)=>
      (a.status===b.status?0:a.status==='open'?-1:1)||
      (SEVORDER[a.severity]??9)-(SEVORDER[b.severity]??9));
  }catch(e){document.getElementById('issues').textContent='not reachable';return}
  renderIssues();
}
function renderIssues(){
  const showDone=document.getElementById('showdone').checked;
  const rows=ISSUES.filter(i=>showDone||i.status!=='done');
  const open=ISSUES.filter(i=>i.status!=='done').length;
  document.getElementById('issues').innerHTML=rows.map(i=>
    `<div class="issue ${i.status==='done'?'done':''}">`+
    `<span class="sev sev-${esc(i.severity)}">${escT(i.severity)}</span>`+
    `<div><h5>${escT(i.title)}</h5>`+
    `<div class="meta text-muted">${escT(i.where||'')} · ${escT(i.kind||'')}</div>`+
    `<p>${escT(i.detail||'')}</p>`+
    (i.fix?`<p class=text-muted><b>Fix:</b> ${escT(i.fix)}</p>`:'')+`</div>`+
    `<label class=radio><input type=checkbox data-i="${esc(i.id)}" ${i.status==='done'?'checked':''}>`+
    `<span class=dot></span>done</label></div>`).join('')||
    '<span class=text-muted style="font-size:13px">nothing open</span>';
  document.getElementById('secissuemsg').textContent=open+' open';
}
function saveIssues(){
  document.querySelectorAll('#issues input[data-i]').forEach(c=>{
    const it=ISSUES.find(x=>x.id===c.dataset.i);
    if(it)it.status=c.checked?'done':'open';
  });
  fetch('/api/security',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({issues:ISSUES.map(i=>({id:i.id,status:i.status}))})})
    .then(r=>r.json()).then(d=>{document.getElementById('secissuemsg').textContent=(d.msg||'saved')+' ✓';
      loadChangelog();});
}
function md(src){
  const esc1=escT(src);
  const out=[];
  let inList=false;
  for(const raw of esc1.split('\\n')){
    const line=raw.replace(/`([^`]+)`/g,'<code>$1</code>')
                  .replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
    const li=line.match(/^\\s*[-*] (.*)$/);
    if(li){ if(!inList){out.push('<ul>');inList=true} out.push('<li>'+li[1]+'</li>'); continue }
    if(inList){out.push('</ul>');inList=false}
    if(/^### /.test(line)) out.push('<h3>'+line.slice(4)+'</h3>');
    else if(/^## /.test(line)) out.push('<h2>'+line.slice(3)+'</h2>');
    else if(/^# /.test(line)) out.push('');            // title is already in the tab
    else if(line.trim()) out.push('<p>'+line+'</p>');
  }
  if(inList)out.push('</ul>');
  return out.join('');
}

/* — Models: the full catalog live, and from it the shortlist for the create form.
     The ticks live in CURATED (models.json), no longer in the source. — */
let CATALOG=[], PICKED=new Set();
async function loadModels2(force){
  const msg=document.getElementById('mdlmsg');
  msg.textContent=force?'fetching catalog…':'';
  try{
    const [cat,cur]=await Promise.all([
      (await fetch('/api/openrouter-models'+(force?'?refresh=1':''))).json(),
      (await fetch('/api/models')).json()]);
    CATALOG=cat; PICKED=new Set(cur.curated||[]);
  }catch(e){msg.textContent='catalog not reachable';return}
  msg.textContent='';
  renderModels();
}
function renderModels(){
  const q=document.getElementById('mdlq').value.trim().toLowerCase();
  const only=document.getElementById('mdltools').checked;
  const selOnly=document.getElementById('mdlsel').checked;
  const rows=CATALOG.filter(m=>
    (!only||m.tools)&&(!selOnly||PICKED.has(m.id))&&
    (!q||m.id.toLowerCase().includes(q)||(m.name||'').toLowerCase().includes(q)));
  document.getElementById('mdlcount').textContent=
    PICKED.size+' selected · '+rows.length+' of '+CATALOG.length+' shown';
  document.getElementById('mdlrows').innerHTML=rows.map(m=>
    `<tr><td style="width:34px;text-align:center"><input type=checkbox data-m="${esc(m.id)}" ${PICKED.has(m.id)?'checked':''}></td>`+
    `<td><div class=mono style="font-size:13px">${escT(m.id)}</div>`+
    `<div class=text-muted style="font-size:11.5px">${escT(m.name||'')}</div></td>`+
    `<td style="white-space:nowrap;font-variant-numeric:tabular-nums">${escT(m.price||'')}</td>`+
    `<td style="white-space:nowrap;font-variant-numeric:tabular-nums" class=text-muted>${m.ctx?(m.ctx/1000).toFixed(0)+'k':''}</td>`+
    `<td>${m.tools?'<span class="tag tag-accent">tools</span>':'<span class="tag tag-neutral">no tools</span>'}</td></tr>`)
    .join('')||'<tr><td class=text-muted style="padding:14px">nothing matches</td></tr>';
}
function saveModels(){
  fetch('/api/models',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({curated:[...PICKED]})})
    .then(r=>r.json()).then(d=>{
      document.getElementById('mdlmsg').textContent=(d.msg||'saved')+' ✓';
      renderParams();   // refresh the model dropdown in the create form immediately
    });
}

/* — katfs: status of the host node + the browser share it is holding — */
let KATFS_ID='';
async function loadKatfs(){
  const el=document.getElementById('katfs-status');
  let d;
  try{d=await (await fetch('/api/katfs/status')).json()}
  catch(e){d={up:false,error:'manager unreachable'}}
  // File browser: keep the last selected share as long as it is still
  // connected; otherwise the first. That way the status rows are highlighted
  // correctly right away. Clicking a row switches later (fbPick).
  const ids=(d.shares||[]).map(x=>x.id);
  if(!FB.share || !ids.includes(FB.share)) FB.share = ids[0] || '';
  const tag=(on,yes,no)=>`<span class="tag ${on?'tag-accent':'tag-neutral'}">${on?yes:no}</span>`;
  el.innerHTML=
    `<div class=kv><b>Host node</b>${tag(d.up,'● up on :'+(d.port||8790),'○ down')}`+
    (d.up?'':`<span class=text-muted style="font-size:12px">${escT(d.error||'')}</span>`)+`</div>`+
    `<div class=kv><b>Browser shares</b>${tag(d.connected,'● '+(d.count||1)+' active','○ nobody sharing')}</div>`+
    (d.shares||[]).map(s=>
      `<div class="kv fbsharerow${FB.share===s.id?' sel':''}" onclick="fbPick('${esc(s.id)}')" title="Browse this share"><b>&nbsp;</b><span><span class=mono>${escT(s.id)}</span> · `+
      `<b style="font-family:var(--font-body);font-size:13.5px;text-transform:none;letter-spacing:0;min-width:0">${escT(s.name||'?')}</b>`+
      (s.device?` <span class=text-muted>${escT(s.device)}</span>`:'')+
      (s.readonly?' <span class="tag tag-neutral">read-only</span>':'')+
      (FB.share===s.id?' <span class="tag tag-accent">browsing &#9662;</span>':'')+`</span></div>`).join('');
  KATFS_SHARES=d.shares||[]; window.KATFS_SHARES=KATFS_SHARES;
  renderShareOptions();
  KATFS_ID=d.node_id||'';
  const inp=document.getElementById('katfskey');
  if(!inp.value||inp.dataset.auto==='1'){inp.value=KATFS_ID;inp.dataset.auto='1'}
  keyHint();
  document.getElementById('katfs-new').innerHTML= d.connected
    ? '<span class="tag tag-accent">● '+(d.count||1)+' share'+((d.count||1)>1?'s':'')+' active</span> reachable via <code>remote_ls</code> / <code>remote_read</code> / <code>remote_write</code>.'
    : (d.up ? '<span class="tag tag-neutral">○ nobody sharing</span> node is up; open <a href="#sharing">Sharing</a> to hand it a folder.'
            : '<span class="tag tag-neutral">○ node down</span> the katfs node on this host is not answering.');
  fbGo(d.connected ? FB.path : '.');
  if(!d.connected){ document.getElementById('fblist').innerHTML=
    '<span class=text-muted style="font-size:13px">No folder shared right now — click “Share a folder…” above, pick a folder in the new tab, then Refresh.</span>'; }
}

/* ── katfs file browser ────────────────────────────────────────────────── */
const FB={path:'.',share:''};
function fbSize(n){n=+n||0;return n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':n<1073741824?(n/1048576).toFixed(1)+' MB':(n/1073741824).toFixed(1)+' GB';}
function fbQ(p){const q='path='+encodeURIComponent(p);return FB.share?q+'&share='+encodeURIComponent(FB.share):q;}
function fbUp(){if(FB.path==='.'||FB.path==='')return;const i=FB.path.lastIndexOf('/');fbGo(i<0?'.':FB.path.slice(0,i));}
/* Click a share in the status panel -> open it in the browser. */
function fbPick(id){ FB.share=id; FB.path='.'; renderShareSel(); fbGo('.'); }
/* Highlight the active share in the status panel without reloading everything. */
function renderShareSel(){
  document.querySelectorAll('.fbsharerow').forEach(r=>{
    const on=r.getAttribute('onclick')===("fbPick('"+FB.share+"')");
    r.classList.toggle('sel',on);
  });
}
/* Download the current folder of the selected share as a ZIP. */
function fbZip(){ window.location.href='/api/katfs/zip?'+fbQ(FB.path); }
async function fbGo(p){
  FB.path=p||'.';
  document.getElementById('fbpath').textContent='/'+(FB.path==='.'?'':FB.path);
  document.getElementById('fbup').disabled=(FB.path==='.'||FB.path==='');
  const sh=(window.KATFS_SHARES||[]).find(x=>x.id===FB.share);
  const shl=document.getElementById('fbshare');
  if(shl) shl.textContent = FB.share ? ('· '+((sh&&sh.name)||FB.share)) : '· (single share)';
  const dl=document.getElementById('fbdl'); if(dl) dl.disabled=false;
  const el=document.getElementById('fblist');
  el.innerHTML='<span class=text-muted style="font-size:13px">loading…</span>';
  let d;
  try{d=await (await fetch('/api/katfs/browse?'+fbQ(FB.path))).json();}
  catch(e){el.innerHTML='<span class=text-muted style="font-size:13px">not reachable</span>';return;}
  if(d.error){el.innerHTML='<span class=text-muted style="font-size:13px">'+escT(d.error)+'</span>';return;}
  const ents=(d.entries||[]).slice().sort((a,b)=>((b.dir?1:0)-(a.dir?1:0))||String(a.name).localeCompare(b.name));
  if(!ents.length){el.innerHTML='<span class=text-muted style="font-size:13px">(empty folder)</span>';return;}
  el.innerHTML=ents.map(e=>{
    const child=(FB.path==='.'||FB.path===''?'':FB.path+'/')+e.name;
    if(e.dir) return `<div class="fbrow dir" onclick="fbGo('${esc(child)}')"><span class=fbico>📁</span><span class=fbn>${escT(e.name)}</span><span class=fbsz></span></div>`;
    return `<div class=fbrow><span class=fbico>📄</span><span class=fbn>${escT(e.name)}</span>`+
      `<span class=fbsz>${fbSize(e.size)}</span>`+
      `<a class="btn btn-ghost fbact" target=_blank rel=noopener href="/api/katfs/file?${fbQ(child)}">view</a>`+
      `<a class="btn btn-ghost fbact" href="/api/katfs/file?dl=1&${fbQ(child)}">download</a></div>`;
  }).join('');
}
/* The key is the node's node-id; iroh parses it as an EndpointId — the WASM
   bridge does not (yet) accept a ticket, hence the hard hint. */
function keyHint(){
  const inp=document.getElementById('katfskey'),v=inp.value.trim(),h=document.getElementById('keyhint');
  if(inp.value!==KATFS_ID)inp.dataset.auto='0';
  if(!v){h.textContent='Empty — the share page will use whatever the node injects.';return}
  if(!/^[0-9a-fA-F]{64}$/.test(v)){
    h.textContent='Not a node-id: iroh expects 64 hex characters (a ticket does not parse).';return;
  }
  h.textContent=(v.toLowerCase()===KATFS_ID.toLowerCase())
    ? 'This host — agents on this machine reach the share.'
    : 'Foreign node — the folder is served to that node, not to this host.';
}
/* Share selection in the create form. The value is the share-id reported by
   the sharing browser (stable across reload) — not the node-id. */
let KATFS_SHARES=[];
function renderShareOptions(){
  const sel=document.getElementById('katfsshare'),cur=sel.value;
  sel.innerHTML='<option value="">— none / decide at runtime —</option>'+
    KATFS_SHARES.map(s=>{
      const lbl=(s.name||s.id)+(s.device?' · '+s.device:'')+(s.readonly?' · read-only':'')+' — '+s.id;
      return `<option value="${esc(s.id)}"${s.id===cur?' selected':''}>${escT(lbl)}</option>`;
    }).join('');
  katfsNewHint();
}
function katfsNewHint(){
  const v=document.getElementById('katfsshare').value,h=document.getElementById('katfsurlhint');
  if(v){
    const s=KATFS_SHARES.find(x=>x.id===v)||{};
    h.innerHTML='Pinned to <span class=mono>'+escT(v)+'</span> ('+escT(s.name||'?')+') as <span class=mono>KATFS_SHARE</span>'+
      ' — survives a reload of that browser tab, but not clearing its storage.';
    return;
  }
  h.textContent=KATFS_SHARES.length>1
    ? 'With '+KATFS_SHARES.length+' shares active the node cannot guess — the agent must name one, so pick a share here.'
    : 'Fine while at most one share is active: the agent just takes the only one.';
}
function openShare(){
  const v=document.getElementById('katfskey').value.trim();
  window.open(v&&v!==KATFS_ID?'/katfs/?key='+encodeURIComponent(v):'/katfs/','_blank','noopener');
}
function resetKey(){
  const inp=document.getElementById('katfskey');
  inp.value=KATFS_ID;inp.dataset.auto='1';keyHint();
}
function copyKey(){
  const inp=document.getElementById('katfskey'),h=document.getElementById('keyhint');
  const done=()=>{h.textContent='copied ✓'};
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(inp.value).then(done,()=>{inp.select()});
  else{inp.select();try{document.execCommand('copy');done()}catch(e){h.textContent='select + copy manually'}}
}
async function act(n,a){await fetch(`/api/instances/${n}/${a}`,{method:'POST'});location.reload()}
async function togglePersist(n,on){
  const r=await fetch(`/api/instances/${n}/persist`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({on})});
  const d=await r.json(); if(String(d.msg||'').startsWith('error'))alert(d.msg);
  location.reload();
}
function diskReset(n){
  if(confirm('Delete the persistent write layer of '+n+'? (installed packages gone, base image stays)'))
    fetch(`/api/instances/${n}/diskreset`,{method:'POST'}).then(r=>r.json()).then(d=>alert(d.msg||'ok'));
  return false;
}
async function toggleNet(n,on){
  await fetch(`/api/instances/${n}/internet`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({on})});location.reload();
}
async function del(n){if(confirm('Delete instance '+n+'?')){await fetch(`/api/instances/${n}/delete`,{method:'POST'});location.reload()}}

let SECPOL={by_template:{},by_instance:{}};
async function renderSecrets(){
  const el=document.getElementById('secrets');
  let keys=[];
  try{
    keys=(await (await fetch('/api/secret-keys')).json()).keys||[];
    SECPOL=await (await fetch('/api/secret-policy')).json();
  }catch(e){el.innerHTML='<span class=text-muted style="font-size:13px">not available</span>';return;}
  if(!keys.length){el.innerHTML='<span class=text-muted style="font-size:13px">No secret keys found in the store.</span>';return;}
  const tpls=(TEMPLATES||[]).map(t=>t.template), bt=SECPOL.by_template||{};
  const head=`<tr><th>Secret key</th>`+tpls.map(t=>`<th style="text-align:center">${escT(t)}</th>`).join('')+`</tr>`;
  const body=keys.map(k=>`<tr><td data-label="Secret key" class=mono style="font-size:12.5px">${escT(k)}</td>`+
    tpls.map(t=>`<td data-label="${esc(t)}" style="text-align:center"><input type=checkbox data-tpl="${esc(t)}" value="${esc(k)}" ${(bt[t]||[]).indexOf(k)>=0?'checked':''}></td>`).join('')+`</tr>`).join('');
  el.innerHTML=`<table class=table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}
function saveSecrets(){
  const bt={};
  (TEMPLATES||[]).forEach(t=>{bt[t.template]=[];});
  document.querySelectorAll('#secrets input[type=checkbox]').forEach(c=>{
    if(!bt[c.dataset.tpl])bt[c.dataset.tpl]=[];
    if(c.checked)bt[c.dataset.tpl].push(c.value);
  });
  fetch('/api/secret-policy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({by_template:bt,by_instance:(SECPOL.by_instance||{})})})
    .then(r=>r.json()).then(d=>{document.getElementById('secmsg').textContent=(d.msg||'saved')+' ✓';});
}
window.onload=()=>{
  showTab(location.hash.slice(1));
  renderSettings();renderParams();loadMissions();loadPrompts();loadPlaybooks();renderPersonas();renderSkills();renderSecrets();renderMcps();loadKatfs();loadModels2();loadChangelog();loadTools();loadPlugins();loadTasks();loadPolicy();loadResources();
  refreshUsage();
  // Tasks, policy and the usage numbers used to arrive only on page load —
  // whoever left the tab open saw arbitrarily stale state (and thought a
  // long-fixed problem was current). Refresh every 15s, but only for the
  // visible tab, not a background one.
  setInterval(()=>{
    if(document.hidden)return;
    const t=location.hash.slice(1)||'instances';
    if(t==='tasks'&&!TK_EDIT)loadTasks();
    else if(t==='missions')loadMissions();
    else if(t==='policy')loadPolicy(true);
    else if(t==='resources')loadResources();
    else if(t==='instances')refreshUsage();
  },15000);
  document.getElementById('actdlg').onclick=e=>{if(e.target.id==='actdlg')actClose()};
  // Remember ticks without rebuilding the whole table on every click.
  document.getElementById('mdlrows').onchange=e=>{
    const b=e.target.closest('[data-m]'); if(!b)return;
    b.checked?PICKED.add(b.dataset.m):PICKED.delete(b.dataset.m);
    document.getElementById('mdlcount').textContent=
      PICKED.size+' selected · '+document.querySelectorAll('#mdlrows tr').length+' shown';
  };
  // Folder picker: one handler for list + quick targets, so paths with
  // quotes/apostrophes don't have to go through inline onclick.
  ['pklist','pkquick'].forEach(id=>document.getElementById(id).onclick=e=>{
    const b=e.target.closest('[data-p]'); if(b)pkGo(b.dataset.p);
  });
  // A click on the backdrop or Esc closes the topmost dialog.
  document.getElementById('picker').onclick=e=>{if(e.target.id==='picker')pkClose()};
  document.getElementById('mdlg').onclick=e=>{if(e.target.id==='mdlg')mdlgClose()};
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape')return;
    if(document.getElementById('picker').style.display!=='none')pkClose();
    else mdlgClose();
  });
};

/* ── Benachrichtigungen (Glocke + Long-Poll + Browser-Notification) ─────── */
let NOTIF_REV=0, NOTIF_START=Math.floor(Date.now()/1000), NOTIF_SEEN=new Set(), NOTIF_LIST=[];
function nEsc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function notifBadge(u){const b=document.getElementById('nbadge');if(!b)return;if(u>0){b.textContent=u>99?'99+':u;b.hidden=false;}else b.hidden=true;}
function notifRender(list){
  if(list)NOTIF_LIST=list;
  const el=document.getElementById('nlist');if(!el)return;
  if(!NOTIF_LIST.length){el.innerHTML='<span class=text-muted style="font-size:13px;padding:12px;display:block">No notifications.</span>';return;}
  el.innerHTML=NOTIF_LIST.slice().reverse().map(n=>{
    const t=new Date((n.ts||0)*1000).toLocaleString();
    const lk=n.link?` data-link="${nEsc(n.link)}" style="cursor:pointer" title="${n.link==='missions'?'To missions':n.link==='tasks'?'To tasks':'To chat'}"`:'';
    return `<div class="nitem ${n.read?'':'unread'}"${lk}><div class=nt>${nEsc(n.title)}${n.link?' <span style="opacity:.5">\u2192</span>':''}</div>`+
      (n.body?`<div class=nb>${nEsc(n.body)}</div>`:'')+
      `<div class=nm>${nEsc(n.instance||'')} \u00b7 ${t}</div></div>`;
  }).join('');
  el.querySelectorAll('[data-link]').forEach(x=>x.onclick=()=>notifClick(x.dataset.link));
}
function notifClick(link){
  document.getElementById('npanel').hidden=true;
  if(link==='missions'){location.hash='#missions';loadMissions();}
  else if(link==='tasks'){location.hash='#tasks';}
  else if(link&&link.startsWith('chat:'))
    window.open('/chat?i='+encodeURIComponent(link.slice(5)),'_blank');
}
function notifDesktop(list){
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  for(const n of (list||[])){
    if(NOTIF_SEEN.has(n.id))continue;NOTIF_SEEN.add(n.id);
    if((n.ts||0)>=NOTIF_START && !n.read){try{new Notification(n.title||'kAIm56',{body:n.body||'',tag:n.id});}catch(e){}}
  }
}
async function notifPoll(){
  for(;;){
    try{
      const r=await fetch('/api/notifications?since='+NOTIF_REV+'&wait=25');
      const d=await r.json();
      if(d.rev)NOTIF_REV=d.rev;
      if(d.notifications){notifRender(d.notifications);notifDesktop(d.notifications);}
      if(typeof d.unread==='number')notifBadge(d.unread);
    }catch(e){await new Promise(res=>setTimeout(res,3000));}
  }
}
function notifMarkAll(){
  fetch('/api/notifications/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})})
    .then(()=>{NOTIF_LIST.forEach(n=>n.read=true);notifRender();notifBadge(0);}).catch(()=>{});
}
function notifReadAll(){notifMarkAll();}
function notifClear(){
  fetch('/api/notifications/read',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({clear:true})})
    .then(()=>{NOTIF_LIST=[];notifRender();notifBadge(0);}).catch(()=>{});
}
function notifToggle(){
  const p=document.getElementById('npanel');if(!p)return;
  const show=p.hidden;p.hidden=!show;
  if(show){
    notifRender();
    if('Notification' in window && Notification.permission==='default')Notification.requestPermission();
    notifMarkAll();               // Oeffnen quittiert als gelesen
  }
}
document.addEventListener('click',e=>{
  const p=document.getElementById('npanel'),b=document.getElementById('nbell');
  if(p&&!p.hidden&&!p.contains(e.target)&&b&&!b.contains(e.target))p.hidden=true;
});
notifPoll();
</script></body></html>"""
