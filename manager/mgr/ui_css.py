# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI — head and stylesheet."""

CSS = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>kAIm56</title>
<link rel=icon type="image/svg+xml" href="/logo.svg">
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap');
/* ── Industry — design-system tokens (claude.ai/design) ─────────────────── */
/* Dark only (the design is): one ground for every page. The ramps count
   from the ground — step 100 sits nearest the background, 900 furthest — so
   the design export's light-ramp step N is our step 1000-N (its accent-400
   is our accent-600, its accent-800 our accent-200). */
:root{
  color-scheme:dark;
  --app-bg:#16181b; --app-side:#1c1f23; --app-card:#1f2226; --app-card-hover:#23272b;
  --app-chip:#2a2f35; --app-chip-hover:#333940; --app-seg-on:#2f353c; --app-line:#2c3137;
  --color-bg:var(--app-bg); --color-surface:var(--app-card); --color-text:#f5f5f8;
  --color-accent:#94bce3; --color-accent-2:#9ebbd8;
  --color-divider:var(--app-line);
  --color-neutral-100:#212326;--color-neutral-200:#2b2d31;--color-neutral-300:#3a3d41;
  --color-neutral-400:#4e5155;--color-neutral-500:#6b6e73;--color-neutral-600:#8b8e93;
  --color-neutral-700:#a9acb1;--color-neutral-800:#c8cacd;--color-neutral-900:#e8e9ea;
  --color-accent-100:#1d2d3d;--color-accent-200:#2c455d;--color-accent-300:#416180;
  --color-accent-400:#597ea3;--color-accent-500:#749dc4;--color-accent-600:#94bce3;
  --color-accent-700:#b5d9fd;--color-accent-800:#d6ebff;--color-accent-900:#eef6ff;
  --font-heading:"Barlow Condensed",system-ui,sans-serif; --font-heading-weight:600;
  --font-body:"Barlow",system-ui,sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --space-1:3.4px;--space-2:6.8px;--space-3:10.2px;--space-4:13.6px;--space-6:20.4px;--space-8:27.2px;
  --radius-sm:2px;--radius-md:4px;--radius-lg:7px;
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
  --shadow-md:0 3px 10px rgba(0,0,0,.45);
  --shadow-lg:0 12px 32px rgba(0,0,0,.55);
  --color-ok:#b5d9fd; --color-warn:#f0a35a;
  --app-live:#5fb88a; --app-ask:#c792ea; --app-bad:#e06c6c;   /* squad board: working / asks you / failed */
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font-family:var(--font-body);font-size:15px;line-height:1.55;font-weight:400;
  background:var(--color-bg);color:var(--color-text);-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,h5,h6{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  line-height:1.12;letter-spacing:-.015em;margin:0 0 var(--space-2)}
h1{font-size:42px}h2{font-size:32px}h3{font-size:25px}h4{font-size:20px}h5{font-size:16px}
h6{font-size:13px;letter-spacing:.08em;text-transform:uppercase}
p{margin:0 0 var(--space-3)}
h1,h2,h3,h4{color:#fff}
a{color:var(--color-accent);text-underline-offset:3px}
a:hover{color:var(--color-accent-700)}
.text-muted{color:color-mix(in srgb,var(--color-text) 55%,transparent)}
:focus{outline:none}
:focus-visible{outline:2px solid var(--color-accent);outline-offset:2px}
::selection{background:color-mix(in srgb,var(--color-accent) 30%,transparent)}
code{font-family:var(--font-mono);font-size:.86em;background:var(--app-bg);
  border:1px solid var(--app-line);border-radius:var(--radius-sm);padding:.02rem .3rem}
button{font:inherit}
/* — framed surfaces (panels, banners, dialogs): a card on the ground — */
.blueprint{position:relative;background:var(--app-card);border-radius:var(--radius-lg)}
/* — buttons — */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;cursor:pointer;
  text-decoration:none;font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:14px;line-height:1.2;color:var(--color-text);background:transparent;
  border:1px solid transparent;border-radius:var(--radius-md);
  padding:var(--space-2) calc(var(--space-3)*1.2)}
.btn svg{display:block;flex:none}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-primary{background:var(--color-accent-400);color:#fff}
.btn-primary:hover{background:var(--color-accent-300);color:#fff}
.btn-primary:active{background:var(--color-accent-200)}
.btn-secondary{background:var(--app-chip);color:var(--color-text)}
.btn-secondary:hover{background:var(--app-chip-hover);color:#fff}
.btn-ghost{color:var(--color-accent);padding-inline:var(--space-1)}
.btn-ghost:hover{background:color-mix(in srgb,var(--color-accent) 10%,transparent)}
.btn-icon{width:32px;height:32px;padding:0;color:var(--color-neutral-700)}
.btn-icon:hover{background:var(--app-chip);color:#fff}
.btn-sm{font-size:13px;padding:5px 10px}
/* — forms — */
.field>label{display:block;font-size:12px;margin-bottom:5px;
  color:color-mix(in srgb,var(--color-text) 70%,transparent)}
.input{width:100%;min-height:36px;padding:6px 10px;font:inherit;font-size:14px;
  color:var(--color-text);caret-color:var(--color-accent);background:var(--app-bg);
  border:1px solid var(--app-line);border-radius:var(--radius-md)}
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
  border-radius:var(--radius-lg);background:var(--app-card)}
.card:hover{background:var(--app-card-hover)}
.card-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);
  font-size:17px;line-height:1.2}
.card-body{margin:0;font-size:13px;opacity:.8;flex:1}
/* — tags — */
.tag{display:inline-flex;align-items:center;font-size:11px;letter-spacing:.02em;
  padding:3px 10px;border-radius:999px;white-space:nowrap}
.tag-accent{background:var(--color-accent-100);color:var(--color-accent-800)}
.tag-neutral{background:var(--app-chip);color:var(--color-neutral-800)}
/* — tables — */
.table{width:100%;border-collapse:collapse;font-size:14px}
.table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:color-mix(in srgb,var(--color-text) 60%,transparent);
  padding:var(--space-2);border-bottom:1px solid var(--color-divider)}
.table td{padding:var(--space-2);border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent);
  vertical-align:top}
.table tbody tr:hover{background:color-mix(in srgb,var(--color-text) 4%,transparent)}
/* — app shell: sticky sidebar + content; a drawer below 860px — */
.shell{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}
.side{min-width:0;background:var(--app-side);padding:var(--space-6) var(--space-3);display:flex;
  flex-direction:column;gap:var(--space-6);position:sticky;top:0;height:100vh;overflow:hidden auto}
.brand{display:flex;align-items:center;gap:10px;padding:0 10px}
.brand-mark{width:34px;height:34px;flex:none;border-radius:var(--radius-lg);background:var(--color-accent-400);
  display:grid;place-items:center;color:#fff}
.brand-txt{display:flex;flex-direction:column;line-height:1.1}
.brand-txt b{font-family:var(--font-heading);font-weight:600;font-size:19px;letter-spacing:.04em}
.brand-txt span{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-neutral-600)}
.nav{display:flex;flex-direction:column;gap:var(--space-6)}
.navgrp{display:flex;flex-direction:column;gap:2px}
.navlbl{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-neutral-500);padding:0 12px 6px}
.nav a{display:flex;justify-content:space-between;align-items:center;padding:7px 12px;border-radius:var(--radius-md);
  text-decoration:none;font-size:14px;color:var(--color-neutral-700)}
.nav a:hover{background:rgba(255,255,255,.05);color:#fff}
.nav a[aria-current=page]{color:#fff;background:var(--color-accent-200);font-weight:500}
.navcnt{font-size:11px;color:var(--color-neutral-600)}
.user{margin-top:auto;display:flex;align-items:center;gap:10px;padding:10px;border-radius:var(--radius-lg);
  background:rgba(255,255,255,.04)}
/* — Claude usage gauges (sidebar, above the user; mgr/claudeaccts.py) — */
.cgauges{margin-top:auto;display:flex;flex-direction:column;gap:8px;padding:0 10px}
.cgauges[hidden]{display:none}
.cgauges:not([hidden])+.user{margin-top:0}
.cg-row{display:flex;flex-direction:column;gap:2px;font-size:11px;min-width:0}
.cg-name{color:var(--color-neutral-700);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cg-g{display:grid;grid-template-columns:auto minmax(0,1fr) 3em;gap:1px 6px;align-items:center;font-size:10.5px;color:var(--color-neutral-600)}
.cg-bar{height:4px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}
.cg-bar i{display:block;height:100%;background:var(--app-live)}
.cg-bar i.warn{background:var(--color-warn)}.cg-bar i.bad{background:var(--app-bad)}
.cg-p{text-align:right;font-family:var(--font-mono)}.cg-p.warn{color:var(--color-warn)}.cg-p.bad{color:var(--app-bad)}
.ca-row{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;font-size:13px}
.ca-u{flex:1 1 150px;min-width:140px;font-size:12px}
.avatar{width:32px;height:32px;flex:none;border-radius:50%;background:var(--color-accent-200);color:var(--color-accent-800);
  display:grid;place-items:center;font-family:var(--font-heading);font-weight:600}
.uinfo{display:flex;flex-direction:column;line-height:1.2;flex:1;min-width:0}
.uinfo span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.uinfo span:first-child{font-size:13px;font-weight:500}
.uinfo span+span{font-size:11px;color:var(--color-neutral-600)}
.iconbtn{position:relative;flex:none;width:32px;height:32px;border:0;border-radius:var(--radius-md);background:transparent;
  color:var(--color-neutral-700);display:grid;place-items:center;cursor:pointer}
.iconbtn:hover{background:var(--app-chip);color:#fff}
.iconbtn[hidden]{display:none}
.nbadge{position:absolute;top:-4px;right:-4px;min-width:17px;height:17px;padding:0 4px;border-radius:9px;
  background:var(--color-accent-600);color:var(--app-bg);font-size:11px;font-weight:700;line-height:17px;text-align:center}
.mbar,.navshade{display:none}
main{min-width:0;padding:var(--space-6) var(--space-8) var(--space-8);display:flex;flex-direction:column}
.phead{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap;margin-bottom:var(--space-6)}
.peyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-accent-600)}
.phead h1{margin:2px 0 0;font-size:36px}
.phead .btn{height:40px;padding:0 16px;border-radius:var(--radius-lg);font-size:15px;letter-spacing:.02em}
.phead .btn[hidden]{display:none}
.lede{font-size:12.5px;color:var(--color-neutral-600);margin:calc(-1*var(--space-3)) 0 var(--space-6);max-width:900px}
.appfoot{display:flex;align-items:center;flex-wrap:wrap;gap:6px 18px;padding-top:14px;margin-top:auto;
  border-top:1px solid var(--app-line);color:var(--color-neutral-600)}
main>.screen{margin-bottom:var(--space-8)}
.af-brand{font-family:var(--font-heading);font-weight:600;font-size:13px;letter-spacing:.02em}
.af-stat{font-size:12px;color:var(--color-neutral-600);white-space:nowrap}
.appfoot a{margin-left:auto;font-size:13px}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--color-text) 18%,transparent);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:color-mix(in srgb,var(--color-text) 32%,transparent)}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-corner{background:transparent}
.npanel{position:fixed;left:244px;bottom:16px;width:340px;max-width:calc(100vw - 32px);max-height:60vh;overflow:auto;
  background:var(--app-card);border:1px solid var(--app-line);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);z-index:60}
.nhead{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--app-line);
  position:sticky;top:0;background:var(--app-card)}
.nitem{padding:10px 14px;border-bottom:1px solid var(--app-line)}
.nitem.unread{background:var(--app-card-hover)}
.nitem[data-link]:hover{background:var(--app-chip)}
.nitem .nt{font-weight:600;font-size:13.5px;display:flex;gap:8px;align-items:baseline}
.nitem .nb{font-size:13px;color:var(--color-neutral-700);margin-top:2px;white-space:pre-wrap;word-break:break-word}
.nitem .nm{font-size:11px;color:var(--color-neutral-500);margin-top:4px}
/* segmented control: the design's range / filter switch (and the activity window) */
.seg,.actwin{display:inline-flex;flex-wrap:wrap;gap:2px;padding:3px;border-radius:var(--radius-lg);background:var(--app-bg)}
.seg button,.actwin button{height:30px;padding:0 12px;border:0;border-radius:var(--radius-md);display:flex;align-items:center;gap:6px;
  font-size:13px;background:transparent;color:var(--color-neutral-700);cursor:pointer}
.seg button:hover,.actwin button:hover{color:#fff}
.seg button.on,.actwin button.on{background:var(--app-seg-on);color:#fff}
.seg button span{opacity:.55}
.seckey{-webkit-text-security:disc}
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
.cmd{font-family:var(--font-mono);font-size:12px;background:var(--app-bg);
  border:1px solid var(--app-line);border-radius:var(--radius-md);padding:10px 12px;white-space:pre-wrap;line-height:1.6;
  overflow-x:auto}
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
  background:rgba(0,0,0,.6);z-index:50}
.dialog{width:min(440px,100%);max-height:88vh;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;
  gap:var(--space-3);padding:var(--space-6);border-radius:var(--radius-lg);background:var(--app-card);
  border:1px solid var(--app-line);box-shadow:var(--shadow-lg)}
.dialog-title{font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:20px}
.dialog-body{font-size:13px;opacity:.85}
.dialog-actions{display:flex;justify-content:flex-end;align-items:center;gap:var(--space-2);
  margin-top:var(--space-2);padding-top:var(--space-3);border-top:1px solid var(--color-divider)}
/* — folder picker — */
.pklist{max-height:46vh;overflow:auto;border:1px solid var(--app-line);border-radius:var(--radius-md);
  background:var(--app-bg);padding:4px;display:flex;flex-direction:column}
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
/* — Instances dashboard: KPI tiles, token chart, fleet cards — */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:var(--space-4);margin-bottom:var(--space-6)}
.kpi,.chart{background:var(--app-card);border-radius:var(--radius-lg);padding:var(--space-6);min-width:0}
.kpi{display:flex;flex-direction:column;gap:var(--space-3)}
.kpi.ring{flex-direction:row;align-items:center;gap:var(--space-6)}
.ring svg{flex:none;transform:rotate(-90deg)}
.ring circle{fill:none;stroke-width:7}
.klbl{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-neutral-600)}
.kval{font-family:var(--font-heading);font-size:44px;line-height:1;color:#fff}
.kval small{font-size:22px;color:var(--color-neutral-600)}
.ksub{font-size:12px;color:var(--color-neutral-700);display:flex;gap:var(--space-6)}
.ksub b{color:var(--color-accent-700);font-weight:500}
.khead{display:flex;justify-content:space-between;gap:8px}
.khead span+span{font-size:12px;color:var(--color-accent-700)}
.kbars{display:flex;align-items:flex-end;gap:4px;height:26px}
.kbars i{flex:1;border-radius:2px;background:var(--color-accent-200);min-height:2px}
.kbars i:last-child{background:var(--color-accent-700)}
.track{height:6px;border-radius:3px;background:var(--app-line);overflow:hidden}
.track>i{display:block;height:100%;width:0;border-radius:3px;background:var(--color-accent-600)}
.chart{margin-bottom:var(--space-6)}
.chart-head{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--space-4);flex-wrap:wrap}
.chart-tot{display:flex;align-items:baseline;gap:10px;margin-top:6px;flex-wrap:wrap}
.chart-tot b{font-family:var(--font-heading);font-weight:400;font-size:40px;line-height:1;color:#fff}
.chart-tot span{font-size:13px;color:var(--color-neutral-700)}
.cchips{display:flex;gap:6px;flex-wrap:wrap;margin-top:var(--space-4)}
.cchips button{height:30px;padding:0 12px;border-radius:999px;display:flex;align-items:center;gap:7px;font-size:12.5px;
  border:1px solid var(--app-line);background:transparent;color:var(--color-neutral-700);cursor:pointer}
.cchips button:hover{filter:brightness(1.2)}
.cchips button.on{border-color:var(--color-accent-500);background:var(--color-accent-100);color:#fff}
.cchips i{width:8px;height:8px;border-radius:50%}
.cplot{display:grid;grid-template-columns:36px minmax(0,1fr);gap:8px;margin-top:var(--space-6)}
.cyt{display:flex;flex-direction:column;justify-content:space-between;font-size:11px;color:var(--color-neutral-500);height:200px;text-align:right}
.cplot svg{width:100%;height:200px;display:block}
.cxl{position:relative;height:16px;font-size:11px;color:var(--color-neutral-500);margin-top:8px}
.cxl span{position:absolute;top:0;transform:translateX(-50%);white-space:nowrap}
.cxl span:first-child{transform:none}.cxl span:last-child{transform:translateX(-100%)}
.fhead{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);flex-wrap:wrap;margin-bottom:var(--space-4)}
.fhead h3{margin:0}
.fsearch{flex:1;min-width:160px;max-width:300px;margin-right:auto;font-size:13px;padding:6px 10px}
.fhead .seg{background:var(--app-card)}
.obanner{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-4);margin-bottom:var(--space-4);
  border-radius:var(--radius-lg);background:var(--color-accent-100);color:var(--color-accent-800);flex-wrap:wrap}
.obanner[hidden]{display:none}
.obanner span{font-size:14px;flex:1;min-width:200px}
.obanner b{color:#fff}
.fleet{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:var(--space-4)}
.icard{background:var(--app-card);border-radius:var(--radius-lg);display:flex;flex-direction:column;overflow:hidden;min-width:0}
.icard:hover{background:var(--app-card-hover)}
.icard[hidden]{display:none}
.cf-rows{display:flex;flex-direction:column;gap:8px;max-height:56vh;overflow:auto;margin:10px 0}
.cf-row{display:grid;grid-template-columns:minmax(120px,220px) minmax(0,1fr) 32px;gap:8px;align-items:start}
.cf-row .input{font-family:var(--font-mono,monospace);font-size:12.5px}
.cf-row textarea.input{min-height:36px;resize:vertical}
.cf-row.del{opacity:.45}.cf-row.del .input{text-decoration:line-through}
@media (max-width:640px){.cf-row{grid-template-columns:minmax(0,1fr) 32px}.cf-row .cf-v{grid-column:1/2}}
/* Collapsed by default: only the head line; the triangle opens the full card. */
.icard:not(.open) .ic-more{display:none}
.icard:not(.open) .ic-body{padding-bottom:var(--space-6)}
.ic-more{display:flex;flex-direction:column;gap:var(--space-4)}
.ic-acts.ic-more{flex-direction:row}
.ic-tog{flex:none;width:28px;height:28px;margin:6px -4px 0 -8px;border:0;border-radius:var(--radius-md);background:transparent;
  color:var(--color-neutral-500);display:grid;place-items:center;cursor:pointer}
.ic-tog:hover{background:var(--app-chip);color:var(--color-text)}
.ic-tog svg{transition:transform .15s ease}
.icard.open .ic-tog svg{transform:rotate(90deg)}
@media (prefers-reduced-motion:reduce){.ic-tog svg{transition:none}}
.ic-head .ic-id{flex:1}
.ic-body{padding:var(--space-6) var(--space-6) var(--space-4);display:flex;flex-direction:column;gap:var(--space-4)}
.ic-head{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--space-2)}
.ic-id{display:flex;gap:12px;align-items:center;min-width:0}
.ic-av{width:40px;height:40px;flex:none;border-radius:var(--radius-lg);display:grid;place-items:center;
  font-family:var(--font-heading);font-weight:600;font-size:18px;background:var(--app-chip);color:var(--color-neutral-600)}
.ic-name{font-family:var(--font-heading);font-weight:600;font-size:21px;line-height:1.1;color:#fff;overflow-wrap:anywhere}
.ic-desc{font-size:12px;color:var(--color-neutral-600)}
.ipill{flex:none;display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 10px;border-radius:999px;
  color:var(--color-neutral-600);background:var(--app-chip)}
.ipill::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.icard[data-state=run] .ipill{color:var(--color-accent-600);background:var(--color-accent-100)}
.icard[data-state=old] .ipill{color:var(--color-accent-800);background:var(--color-accent-200)}
.icard:not([data-state=off]) .ic-av{background:var(--color-accent-200);color:var(--color-accent-800)}
.ic-model{display:block;width:100%;text-align:left;border:0;font-family:var(--font-mono);font-size:12px;color:var(--color-accent-700);
  background:var(--app-bg);border-radius:var(--radius-md);padding:7px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
button.ic-model{cursor:pointer}
button.ic-model:hover{color:#fff}
.ic-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-3)}
.ic-k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--color-neutral-500)}
.ic-v{font-size:14px;margin-top:2px;overflow-wrap:anywhere}
.ic-v.mono{font-size:12.5px;margin-top:3px}
.ic-use{display:flex;flex-direction:column;gap:6px}
.ic-usel{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--color-neutral-600);font-variant-numeric:tabular-nums}
.ic-use .track{height:5px}
.ic-chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:11px;padding:3px 9px;border-radius:999px;border:0;background:var(--app-chip);color:var(--color-neutral-800)}
button.chip{cursor:pointer}
button.chip:hover{background:var(--app-chip-hover);color:#fff}
.ic-mnt{font-size:12px;color:var(--color-neutral-600);overflow-wrap:anywhere}
.ic-acts{display:flex;gap:6px;padding:var(--space-3) var(--space-6) var(--space-4);margin-top:auto;flex-wrap:wrap;align-items:center}
.ic-acts>.btn{height:34px;padding:0 12px}
.ic-icons{margin-left:auto;display:flex;gap:2px}
/* Squads: a slim bar of squads on top, the chat takes the full width. */
.sk{display:flex;flex-direction:column;gap:10px}
.sk-list{display:flex;gap:6px;align-items:center;overflow-x:auto;scrollbar-width:thin;padding:6px}
.sk-item{flex:none;display:inline-flex;align-items:baseline;gap:6px;white-space:nowrap;background:none;border:1px solid var(--app-line);border-radius:999px;padding:5px 12px;color:inherit;cursor:pointer;font:inherit;font-size:13px}
.sk-item:hover,.sk-item[aria-current]{background:var(--app-chip);border-color:var(--color-accent)}
.sk-item[data-new]{border-style:dashed}
.sk-item small{font-size:11px;color:var(--color-neutral-600)}
.sk-chat{display:flex;flex-direction:column;min-width:0}
.sk-chat>#sk-chatbox{order:1}.sk-chat>#sk-working,.sk-chat>.sk-comp,.sk-chat>#sk-msg{order:2}   /* the chat, then what you type */
.sk-chat:not(.sk-kick) #sk-text{min-height:2.6em;max-height:12em;overflow-y:auto;resize:none}
.sk-chat:not(.sk-kick) .sk-comp{align-items:flex-end}
.sk-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center}
.sk-tc{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12.5px;margin:4px 0}
.sk-reqdoc{white-space:pre-wrap;font-size:12.5px;max-height:320px;overflow:auto;margin:8px 0;padding:8px;border-radius:6px;background:var(--color-bg-subtle,rgba(127,127,127,.08))}
.sk-tc:empty{display:none}
.sk-thread{overflow:auto;max-height:60vh;min-height:240px;display:grid;gap:8px;align-content:start;margin:12px 0}
.sk-bub{padding:8px 10px;border-radius:8px;background:var(--app-bg);max-width:88%;overflow-wrap:anywhere}
.sk-bub.me{justify-self:end;background:var(--app-chip)}
.sk-bub.bad{border-left:3px solid var(--color-warn)}
.sq-tbub{opacity:.82}
mark.sq-mk{color:inherit;background:color-mix(in srgb,var(--color-accent) 24%,transparent);border-radius:2px;
  box-shadow:0 1px 0 var(--color-accent-400);cursor:pointer}
mark.sq-mk.sent{background:color-mix(in srgb,var(--color-text) 9%,transparent);box-shadow:none}
.sq-nb{font-size:12px;display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center}
.sq-nb:empty{display:none}
#sqa-fab,.sqa-menu,.sqa-pop{position:fixed;z-index:70}
.sqa-menu,.sqa-pop{background:var(--app-card);border:1px solid var(--app-line);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.45);padding:4px}
.sqa-pop{width:min(380px,calc(100vw - 32px));padding:10px;display:grid;gap:8px;animation:sqa-in .12s ease-out}
#sqa-fab[hidden],.sqa-menu[hidden],.sqa-pop[hidden],.sqa-edit[hidden]{display:none}
.sqa-edit{display:grid;gap:8px}
.sqa-q{font-size:12.5px;max-height:6.5em;overflow:auto;white-space:pre-wrap;color:color-mix(in srgb,var(--color-text) 65%,transparent)}
.sqa-acts{display:flex;flex-wrap:wrap;gap:6px}
@keyframes sqa-in{from{opacity:0;transform:translateY(-4px)}}
@media (prefers-reduced-motion:reduce){.sqa-pop{animation:none}}
.sq-turn summary{cursor:pointer;white-space:pre-wrap;word-break:break-word;margin:4px 0}
.sq-work{font-size:12.5px;margin:2px 0}
.sk-comp{display:flex;gap:6px}
.sq-set h5{margin:16px 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--color-neutral-600)}
.sq-chks{display:flex;flex-wrap:wrap;gap:6px 18px;margin-bottom:4px}.sq-chks label{display:flex;gap:6px;align-items:center;text-transform:none;font-size:13.5px}
.sq-more{margin-top:12px;border-top:1px solid var(--app-line);padding-top:10px}.sq-more>summary{cursor:pointer;font-size:13px;color:var(--color-neutral-600)}
.sq-more[open]>summary{margin-bottom:10px}
#sk-chatbox>summary{cursor:pointer;font-size:13px;margin-top:12px;color:var(--color-neutral-600)}
.sk-kick>#sk-chatbox{order:1}.sk-kick>#sk-chatbox>summary{display:none}   /* new squad / kickoff: the chat is the main view */
#sk-cards{max-height:55vh;overflow:auto}   /* waiting cards above the board, never pushing it off screen */
.sk-kick>#sk-working,.sk-kick>.sk-comp,.sk-kick>#sk-msg{order:2}
.sk-kick .sk-thread{min-height:0}                                   /* new squad: a short hint, the input is the big part */
/* new squad as a form: repo · architect · suggestions / the description / team · start */
.sk-kick>.sk-comp{display:grid;grid-template-columns:auto auto auto 1fr;gap:8px;align-items:center;margin-top:4px}
.sk-kick>.sk-comp>select{width:auto;max-width:100%}.sk-kick #sk-repo{order:-1}
.sk-kick .sk-inwrap{grid-column:1/-1;grid-row:2}.sk-kick #sk-text{min-height:14em;line-height:1.5}
.sk-kick #sk-send{grid-column:4;grid-row:3;justify-self:end;align-self:start;padding-inline:20px}
.sk-kick #sk-teambox{grid-column:1/4;grid-row:3;align-self:start;padding-top:6px}.sk-kick #sk-teambox>summary{cursor:pointer;font-size:13px;color:var(--color-neutral-600)}
.sk-kick #sk-chatbox:not(:has(.sk-bub)){display:none}
@media (max-width:640px){.sk-kick>.sk-comp{grid-template-columns:1fr}
  .sk-kick .sk-inwrap,.sk-kick #sk-send,.sk-kick #sk-teambox{grid-column:1;grid-row:auto}
  .sk-kick .sk-inwrap{order:1}.sk-kick #sk-teambox{order:2}.sk-kick #sk-send{order:3;justify-self:stretch}}
.sk-at{position:absolute;left:0;bottom:calc(100% + 4px);min-width:220px;max-height:240px;overflow:auto;z-index:30;padding:4px;
  background:var(--app-card);border:1px solid var(--app-line);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg)}
.sk-at div{padding:6px 10px;border-radius:var(--radius-md);cursor:pointer;font-size:13px;display:flex;justify-content:space-between;gap:12px}
.sk-at div[aria-selected=true],.sk-at div:hover{background:var(--app-chip)}
.sk-at small{color:var(--color-neutral-600)}
/* — Squads board (ui_js skBoardRender): team row, summary, Kanban columns — */
.sk-live,.sk-qdot{width:8px;height:8px;border-radius:50%;background:var(--app-live);align-self:center;flex:none}
.sk-live{animation:skpulse 1.6s infinite}.sk-qdot{background:var(--app-ask)}
@keyframes skpulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--app-live) 55%,transparent)}70%{box-shadow:0 0 0 7px transparent}100%{box-shadow:0 0 0 0 transparent}}
.kb-t{color:var(--color-neutral-600);font-family:var(--font-mono);font-size:11.5px}
/* who does what: member | now | 2 h strip (mgr/squadkick.activity) */
.kb-prob{display:flex;gap:8px 10px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:13px;padding:6px 10px;border-radius:8px;
  background:color-mix(in srgb,var(--color-warn) 8%,transparent);border:1px solid color-mix(in srgb,var(--color-warn) 35%,transparent)}
.kb-prob[hidden]{display:none}.kb-ptx{flex:1;min-width:200px}.kb-ptx b{color:var(--color-warn);font-weight:600}
.kb-mem{border:1px solid var(--app-line);border-radius:10px;overflow:hidden;margin:14px 0 4px}
.kb-mh,.kb-mr{display:grid;grid-template-columns:minmax(140px,190px) minmax(200px,340px) minmax(0,1fr)}   /* the timeline gets the room */
.kb-mh>span,.kb-mr>div{padding:8px 12px;min-width:0;display:flex;align-items:center;gap:9px}
.kb-mr+.kb-mr{border-top:1px solid var(--app-line)}
.kb-mh{background:var(--app-side);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--color-neutral-500)}
.kb-mh .kb-axis{justify-content:space-between;font-family:var(--font-mono);letter-spacing:0;text-transform:none}
.kb-mr .kb-av{position:relative;flex:none}
.kb-nm{display:flex;flex-direction:column;line-height:1.2;min-width:0}.kb-nm b{font-weight:500;font-size:13.5px;overflow:hidden;text-overflow:ellipsis}
.kb-nm small{color:var(--color-neutral-500);font-size:11.5px}
.kb-mr .kb-now{flex-direction:column;align-items:flex-start;gap:2px}
.kb-l1{font-size:13.5px;display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;min-width:0;max-width:100%}
.kb-l2{font-size:12px;color:var(--color-neutral-600);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kb-st{font-size:12px;font-weight:500}.kb-st.work{color:var(--app-live)}.kb-st.err{color:var(--app-bad)}.kb-st.wait{color:var(--color-warn)}
.kb-st.ask{color:var(--app-ask)}.kb-st.idle{color:var(--color-neutral-600)}
.kb-why1{overflow-wrap:anywhere}
.kb-cmd{display:block;font-family:var(--font-mono);font-size:12px;background:var(--app-chip);padding:1px 6px;border-radius:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kb-retry{background:none;border:0;padding:0;font:inherit;font-size:12px;color:var(--color-accent-600);cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.kb-live{display:inline-flex;gap:3px;align-self:center}.kb-live i{width:4px;height:4px;border-radius:50%;background:var(--app-live);animation:kbdot 1.2s infinite}
.kb-live i:nth-child(2){animation-delay:.2s}.kb-live i:nth-child(3){animation-delay:.4s}
@keyframes kbdot{0%,100%{opacity:.25}50%{opacity:1}}
.kb-strip{position:relative;height:18px;width:100%;overflow:hidden;border-radius:3px;
  background:repeating-linear-gradient(90deg,transparent 0,transparent calc(16.6667% - 1px),var(--app-line) calc(16.6667% - 1px),var(--app-line) 16.6667%)}   /* grid = 1 h */
.kb-b{position:absolute;top:3px;height:12px;border-radius:2px;background:var(--app-live);opacity:.85}
.kb-b.f{background:var(--app-bad)}.kb-b.now{box-shadow:0 0 0 1px color-mix(in srgb,var(--app-live) 60%,transparent);animation:skpulse 1.6s infinite}
.kb-m{position:absolute;top:6px;width:6px;height:6px;border-radius:50%;background:var(--color-accent-400)}
.kb-nowl{position:absolute;right:0;top:0;bottom:0;width:1px;background:var(--color-accent-400)}
.kb-legend i.kb-lb{width:12px;height:8px;border-radius:2px;background:var(--app-live)}.kb-legend i.kb-lb.f{background:var(--app-bad)}
.kb-why{font-size:12px;color:var(--color-warn);background:color-mix(in srgb,var(--color-warn) 8%,transparent);border-radius:6px;padding:5px 7px;line-height:1.35}
.kb-why b{font-weight:600}
@media(max-width:860px){.kb-mh{display:none}.kb-mr{grid-template-columns:minmax(0,1fr)}.kb-mr>div{padding:6px 12px}.kb-mr>div:first-child{padding-top:10px}.kb-mr>div:last-child{padding-bottom:10px}}
.kb-av{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-family:var(--font-heading);font-weight:600;font-size:13px;
  background:var(--color-accent-200);color:var(--color-accent-800);position:relative;flex:none}
.kb-av.idle{background:var(--app-chip);color:var(--color-neutral-600)}
.kb-as .kb-av{width:20px;height:20px;font-size:11px}
.kb-s{position:absolute;right:-2px;bottom:-2px;width:10px;height:10px;border-radius:50%;border:2px solid var(--app-card-hover)}
.kb-s.work{background:var(--app-live);animation:skpulse 1.6s infinite}.kb-s.wait{background:var(--color-warn)}
.kb-s.idle{background:var(--color-neutral-500)}.kb-s.ask{background:var(--app-ask)}.kb-s.err{background:var(--app-bad)}
.kb-sum{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:10px 0 12px;font-size:12.5px;color:var(--color-neutral-600)}
.kb-sum b{color:var(--color-text)}
.kb-pbar{flex:1;min-width:140px;max-width:360px;height:6px;border-radius:3px;background:var(--app-line);display:flex;overflow:hidden}
.kb-pbar i{display:block;height:100%}
.kb-spark{display:inline-flex;align-items:flex-end;gap:2px;height:20px}
.kb-spark i{display:block;width:5px;background:var(--color-accent-200);border-radius:1px}.kb-spark i.now{background:var(--color-accent-600)}
.kb-board{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;overflow-x:auto;scrollbar-width:thin}   /* all 5 columns fit; a phone scrolls */
.kb-col{min-width:0}.kb-tk{min-width:0;overflow-wrap:break-word}
.kb-col{background:var(--app-side);border-radius:10px;padding:10px;min-height:200px;max-height:1000px;overflow-y:auto;scrollbar-width:thin;display:flex;flex-direction:column;gap:8px}   /* board max 1000px, a long column (Done) scrolls */
.kb-col>*{flex:none}.kb-col .kb-ch{position:sticky;top:-10px;z-index:1;background:inherit;margin:-10px -10px 0;padding:10px 12px 4px}
.kb-col.drop{outline:2px dashed var(--color-accent-400);outline-offset:-2px}
.kb-ch{display:flex;justify-content:space-between;align-items:center;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--color-neutral-500);padding:0 2px 4px}
.kb-ch b{font-family:var(--font-mono);letter-spacing:0;color:var(--color-neutral-600);font-weight:400}
.kb-col[data-st=doing] .kb-ch{color:var(--color-accent-600)}.kb-col[data-st=done] .kb-ch{color:var(--app-live)}.kb-col[data-st=blocked] .kb-ch{color:var(--color-warn)}
.kb-tk{background:var(--app-card-hover);border-radius:8px;padding:10px;border:1px solid var(--app-line);display:flex;flex-direction:column;gap:7px;
  cursor:pointer;font:inherit;color:inherit;text-align:left;width:100%}
.kb-tk:hover{border-color:var(--color-neutral-400)}
.kb-tk.working{border-color:color-mix(in srgb,var(--app-live) 55%,transparent);box-shadow:inset 3px 0 0 var(--app-live)}
.kb-tk.waiting{box-shadow:inset 3px 0 0 var(--color-warn)}
.kb-tk.asking{box-shadow:inset 3px 0 0 var(--app-ask)}
.kb-tk.donec{opacity:.7}
.kb-key{font-family:var(--font-mono);font-size:11.5px;color:var(--color-accent-700)}
.kb-ti{font-size:13.5px;line-height:1.3}
.kb-meta{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:4px 6px;font-size:12px;color:var(--color-neutral-600)}
.kb-as{display:inline-flex;align-items:center;gap:6px;min-width:0}
.kb-t{color:var(--color-accent-700)}
.kb-dep{font-size:11.5px;color:var(--color-warn)}.kb-q{font-size:11.5px;color:var(--app-ask)}
.kb-bar{height:3px;background:var(--app-line);border-radius:2px;overflow:hidden}
.kb-run{display:block;height:100%;width:35%;background:var(--app-live);animation:skslide 1.4s linear infinite}
@keyframes skslide{from{transform:translateX(-100%)}to{transform:translateX(290%)}}
.kb-lvl{font-family:var(--font-mono);font-size:10.5px;padding:1px 6px;border-radius:4px;background:var(--app-chip);color:var(--color-neutral-600)}
.kb-empty{font-size:12px;color:var(--color-neutral-500);text-align:center;padding:14px 4px;border:1px dashed var(--app-line);border-radius:8px}
.kb-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--color-neutral-600);margin-top:10px}
.kb-legend span{display:inline-flex;align-items:center;gap:6px}.kb-legend i{width:8px;height:8px;border-radius:50%;display:inline-block}
.kb-tk.cur{border-color:var(--color-accent-600);box-shadow:inset 3px 0 0 var(--color-accent-600)}
/* — ticket side panel (ui_js skTkOpen): right of the board on desktop, full screen on phones — */
.kb-pane{position:fixed;top:0;right:0;bottom:0;width:min(520px,45vw);z-index:40;display:flex;flex-direction:column;background:var(--app-card);
  border-left:1px solid var(--app-line);box-shadow:-12px 0 30px rgba(0,0,0,.35);animation:kbin .18s ease-out}
.kb-pane[hidden]{display:none}
@keyframes kbin{from{transform:translateX(24px);opacity:0}}
@media(min-width:1700px){#s-squads.kb-open{padding-right:min(520px,35vw)}}   /* room for board + panel; narrower: the panel overlays */
#sk-tkd-box{flex:1;min-height:0;display:flex;flex-direction:column}
.kb-ptop{flex:none;padding:16px 18px 12px;border-bottom:1px solid var(--app-line);display:flex;flex-direction:column;gap:10px}
.kb-prow{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.kb-ptitle{font-size:24px;line-height:1.15;margin:2px 0 0;text-wrap:balance;overflow-wrap:anywhere}
.kb-x{background:none;border:0;color:var(--color-neutral-600);font-size:22px;line-height:1;cursor:pointer;padding:2px 8px;border-radius:6px}
.kb-x:hover{background:var(--app-chip)}
.kb-ctl{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.kb-sel{appearance:none;font:inherit;font-size:12.5px;color:var(--color-text);background-color:var(--app-chip);border:1px solid var(--app-line);border-radius:999px;
  padding:3px 26px 3px 10px;cursor:pointer;background-repeat:no-repeat;background-size:4px 4px;background-position:calc(100% - 13px) 55%,calc(100% - 9px) 55%;
  background-image:linear-gradient(45deg,transparent 50%,var(--color-neutral-600) 50%),linear-gradient(135deg,var(--color-neutral-600) 50%,transparent 50%)}
.kb-sel.st{border-color:var(--color-accent-400);color:var(--color-accent-700)}
.kb-badge{font-family:var(--font-mono);font-size:11.5px;padding:3px 8px;border-radius:999px;background:var(--color-accent-100);color:var(--color-accent-700)}
.kb-when{font-size:12px;color:var(--color-neutral-500);margin-left:auto}
.kb-rescope{display:flex;gap:10px;align-items:flex-start;border-radius:8px;padding:9px 11px;font-size:12.5px;
  background:color-mix(in srgb,var(--color-warn) 9%,transparent);border:1px solid color-mix(in srgb,var(--color-warn) 35%,transparent)}
.kb-rescope b{color:var(--color-warn);font-weight:600}
.kb-rescope>.kb-more{margin:0 0 0 auto;padding:0;white-space:nowrap}
.kb-prev{margin-top:6px;padding-top:6px;border-top:1px solid color-mix(in srgb,var(--color-warn) 25%,transparent);max-height:30vh;overflow:auto;color:var(--color-neutral-800)}
.kb-prev b{color:var(--color-neutral-700);font-weight:500}
.kb-pbody{flex:1;min-height:0;overflow-y:auto;padding:0 18px 14px}
.kb-pbody .sq-sub{margin-bottom:6px}
.kb-md{font-size:13.5px;line-height:1.55;color:var(--color-neutral-900);overflow-wrap:anywhere}
.kb-md ul{margin:4px 0;padding-left:18px;list-style:disc}
.kb-md code{font-family:var(--font-mono);font-size:.88em;background:var(--app-chip);padding:1px 5px;border-radius:4px}
.kb-box{background:var(--app-card-hover);border-radius:8px;padding:10px 12px}
.kb-clip{max-height:calc(9.3em + 20px);overflow:hidden;position:relative}
.kb-clip::after{content:"";position:absolute;left:0;right:0;bottom:0;height:2.4em;background:linear-gradient(transparent,var(--app-card-hover))}
.kb-clip.open{max-height:none}.kb-clip.open::after{display:none}
.kb-more{background:none;border:0;padding:2px 0;margin-top:2px;font:inherit;font-size:12.5px;color:var(--color-accent-600);cursor:pointer}
.kb-deps{display:flex;gap:6px;flex-wrap:wrap}
.kb-chip{display:inline-flex;align-items:center;gap:7px;max-width:100%;border:1px solid var(--app-line);background:none;color:inherit;font:inherit;font-size:12.5px;
  border-radius:999px;padding:3px 10px;cursor:pointer}
.kb-chip:hover{border-color:var(--color-accent-400)}
.kb-chip i{width:7px;height:7px;border-radius:50%;flex:none}
.kb-ct{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kb-chip small{color:var(--color-neutral-600)}
.kb-pbody .kb-dep{display:block;margin-top:6px;font-size:12px}
.kb-tl{list-style:none;margin:0;padding:0}
.kb-ev{display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;padding-bottom:14px;position:relative}
.kb-ev::before{content:"";position:absolute;left:13px;top:30px;bottom:2px;width:1px;background:var(--app-line)}
.kb-ev:last-child::before{display:none}
.kb-ev>.kb-av{width:28px;height:28px}
.kb-eh{font-size:12.5px;color:var(--color-neutral-600);display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;padding-top:4px}
.kb-eh b{color:var(--color-text);font-weight:500}
.kb-eh time{font-family:var(--font-mono);font-size:11.5px;color:var(--color-neutral-500)}
.kb-tag{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;padding:1px 7px;border-radius:999px}
.kb-tag.rs{background:color-mix(in srgb,var(--color-warn) 15%,transparent);color:var(--color-warn)}
.kb-tag.ans{background:color-mix(in srgb,var(--app-live) 14%,transparent);color:var(--app-live)}
.kb-ev .kb-md{margin-top:4px}
.kb-pfoot{flex:none;border-top:1px solid var(--app-line);padding:10px 18px 14px;display:flex;flex-direction:column;gap:8px}
.kb-pfoot textarea{resize:vertical;min-height:58px;font-size:13.5px}
.kb-fr{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--color-neutral-500)}
@media(max-width:900px){.kb-pane{width:100%;border-left:0}#s-squads.kb-open{padding-right:0}.kb-board{grid-template-columns:repeat(5,minmax(200px,1fr));scroll-snap-type:x proximity}.kb-col{scroll-snap-align:start}}
@media (prefers-reduced-motion:reduce){.sk-live,.kb-s.work,.kb-run,.kb-pane,.kb-live i,.kb-b.now{animation:none!important}}
@media(max-width:860px){
  .grid2,.grid3{grid-template-columns:1fr}
  .shell{display:block}
  main{padding:var(--space-4) 16px var(--space-8)}
  .mbar{display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:30;padding:8px 16px;
    background:var(--app-side);border-bottom:1px solid var(--app-line)}
  .mbar b{font-family:var(--font-heading);font-weight:600;font-size:19px;letter-spacing:.04em}
  .side{position:fixed;left:0;top:0;bottom:0;width:260px;max-width:85vw;z-index:50;transform:translateX(-100%);
    transition:transform .2s ease;box-shadow:var(--shadow-lg)}
  .navopen .side{transform:none}
  .navopen .navshade{display:block;position:fixed;inset:0;z-index:40;background:rgba(0,0,0,.55)}
  .npanel{left:16px;right:16px;width:auto}
  .phead h1{font-size:30px}
  .fleet{grid-template-columns:minmax(0,1fr)}
  .kpi{padding:var(--space-4)}
  .kval{font-size:36px}
  .ic-body{padding:var(--space-4) var(--space-4) var(--space-3)}
  .ic-acts{padding:var(--space-3) var(--space-4) var(--space-4)}
  .sec-head{flex-direction:column;align-items:flex-start}
  .sec-head .note{text-align:left}
  .table thead{display:none}
  .table,.table tbody,.table tr,.table td{display:block;width:100%}
  .table tr{border:1px solid var(--color-divider);margin:12px 0;padding:6px 4px}
  .table td{border:none;padding:6px 10px}
  .table td::before{content:attr(data-label);display:block;font-size:10px;
    letter-spacing:.08em;text-transform:uppercase;
    color:color-mix(in srgb,var(--color-text) 55%,transparent)}
}
/* — squad proposal card (ui_js sqCard): one block per member, label | field | who chose it — */
.wait-list{display:grid;gap:4px;max-width:880px}
.hq-opts{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.hq-det{white-space:pre-wrap;word-break:break-word;margin:8px 0;font-size:13px}
.navcnt.hot{color:var(--color-accent-700);font-weight:600}
.sq-card{padding:14px;border:1px solid var(--app-line);border-radius:var(--radius-lg);margin:8px 0;background:var(--app-card)}
.sq-head{display:flex;justify-content:space-between;gap:4px 8px;flex-wrap:wrap;align-items:baseline}
.sq-mem{display:flex;flex-direction:column;gap:8px;margin:8px 0;padding:10px 12px;border:1px solid var(--app-line);border-radius:var(--radius-md);background:var(--app-bg)}
.sq-old{flex-direction:row;flex-wrap:wrap;align-items:center;padding:4px 4px 4px 12px;font-size:13px}
.sq-mh{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sq-sp{flex:1}
.sq-purp{font-size:13px;color:var(--color-neutral-800)}
.sq-role{font:inherit;font-size:12px;padding:2px 6px;border-radius:999px;border:1px solid var(--app-line);background:var(--app-chip);color:var(--color-text);cursor:pointer}
.sq-row{display:grid;grid-template-columns:72px minmax(0,1fr) auto;gap:4px 10px;align-items:start}
.sq-row>label,.sq-lab{font-size:12px;padding-top:9px;color:color-mix(in srgb,var(--color-text) 70%,transparent)}
.sq-by{font-size:11px;padding:3px 8px;margin-top:7px;border-radius:999px;background:var(--app-chip);color:var(--color-neutral-800);white-space:nowrap}
.sq-by:empty{visibility:hidden}
.sq-empty{color:var(--color-warn);background:transparent;border:1px dashed color-mix(in srgb,var(--color-warn) 55%,transparent)}
.sq-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.sq-chip{display:inline-flex;align-items:center;font-size:12.5px;padding:2px 2px 2px 10px;border-radius:999px;background:var(--color-accent-100);color:var(--color-accent-800)}
.sq-chip button{border:0;background:transparent;color:inherit;cursor:pointer;font-size:15px;line-height:1;padding:2px 7px;border-radius:999px}
.sq-chip button:hover{background:var(--color-accent-200)}
.sq-add{width:auto;flex:1 1 140px;max-width:240px;min-height:30px;padding:3px 10px;font-size:13px}
.sq-hint{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px;font-size:12px}
.sq-ghost{font:inherit;font-size:12px;padding:2px 10px;border-radius:999px;border:1px dashed var(--color-accent-300);background:transparent;color:var(--color-accent);cursor:pointer}
.sq-ghost:hover{background:color-mix(in srgb,var(--color-accent) 10%,transparent)}
.sq-seg{display:inline-flex;gap:2px;padding:3px;border-radius:var(--radius-lg);background:var(--app-chip)}
.sq-seg label{padding:5px 14px;border-radius:var(--radius-md);font-size:13px;cursor:pointer;color:var(--color-neutral-700)}
.sq-seg input{position:absolute;opacity:0;width:0;height:0}
.sq-seg label:has(input:checked){background:var(--color-accent-300);color:#fff}
.sq-seg label:has(input:focus-visible){outline:2px solid var(--color-accent);outline-offset:1px}
.sq-np{display:flex;flex-direction:column;gap:6px;margin-top:6px}
.sq-np[hidden]{display:none}
.sq-sub{margin:14px 0 2px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:color-mix(in srgb,var(--color-text) 60%,transparent)}
.sq-tk{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--app-line);font-size:13px}
.sq-tk>.input{flex:1 1 220px;min-width:0;width:auto}
.sq-tk>select.input{flex:0 1 180px}
.sq-key{min-width:40px}
.sq-tkd{flex-basis:100%;font-size:12px}
.sq-tkd textarea,.sq-goal{width:100%;margin-top:4px;font-size:13px;resize:vertical}
.sq-tk>.sq-dep{flex:0 1 110px}
.sq-chg{margin:8px 0;font-size:12.5px}
.sq-chg summary{cursor:pointer;color:var(--color-accent)}
.sq-chg ul{margin:6px 0;padding-left:18px}
.sq-cmt{margin:6px 0;padding:6px 10px;border-left:2px solid var(--app-line)}
.sq-foot{position:sticky;bottom:0;z-index:2;display:flex;flex-wrap:wrap;gap:6px;margin:12px -14px -14px;padding:10px 14px;
  background:var(--app-card);border-top:1px solid var(--app-line);border-radius:0 0 var(--radius-lg) var(--radius-lg)}
.sq-foot .input{flex:1 1 200px;min-width:0;width:auto}
@media (max-width:640px){
  .sq-row{grid-template-columns:minmax(0,1fr) auto}
  .sq-row>.sq-fld{grid-column:1/-1;grid-row:2}
  .sq-row>label,.sq-lab{padding-top:3px}
  .sq-by{margin-top:0}
  .sq-foot .input{flex-basis:100%}
  .sq-foot .btn{flex:1}
}
</style>"""
