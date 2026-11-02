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
.brand-mark{width:34px;height:34px;flex:none;display:grid;place-items:center;color:var(--color-accent-400)}
.brand-mark svg{width:31px;height:31px}   /* the same mark as the chat page (logo.svg) */
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
.sk-desc{font-size:12px;color:var(--color-neutral-600);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:640px}
.pbgrid{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px}
@media (max-width:640px){.pbgrid{grid-template-columns:1fr}}
.fleet,.tblwrap{background:var(--app-card);border-radius:var(--radius-lg);overflow-x:auto}
.ftable{width:100%;border-collapse:collapse;font-size:13.5px}
.ftable th{text-align:left;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap;
  color:var(--color-neutral-600);padding:12px 10px;border-bottom:1px solid var(--app-line)}
.ftable th[data-k]{cursor:pointer;user-select:none}
.ftable th[data-k]:hover{color:var(--color-text)}
.ftable th[aria-sort=ascending]::after{content:" ▲"}.ftable th[aria-sort=descending]::after{content:" ▼"}
.ftable td{padding:8px 10px;vertical-align:middle;border-bottom:1px solid var(--app-line)}
.ftable .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.icard[hidden]{display:none}
.icard>tr:first-child:hover{background:var(--app-card-hover)}
.icard.open>tr:first-child td{border-bottom-color:transparent}
.f-tog{width:28px;padding-right:0}
.f-name{min-width:140px;width:30%}
.f-model .ic-model{max-width:240px}
.ftable tr:first-child .ic-chips{flex-wrap:nowrap}
.f-det{contain:inline-size;display:flex;flex-direction:column;gap:var(--space-4);padding:4px 0 10px 28px;max-width:760px}
.ftable .f-mob{display:none}
.f-name .ic-desc{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
.ftable td .ic-model{padding:4px 8px}
.f-det .ic-icons{gap:4px}
.cf-rows{display:flex;flex-direction:column;gap:8px;max-height:56vh;overflow:auto;margin:10px 0}
.cf-row{display:grid;grid-template-columns:minmax(120px,220px) minmax(0,1fr) 32px;gap:8px;align-items:start}
.cf-row .input{font-family:var(--font-mono,monospace);font-size:12.5px}
.cf-row textarea.input{min-height:36px;resize:vertical}
.cf-row.del{opacity:.45}.cf-row.del .input{text-decoration:line-through}
@media (max-width:640px){.cf-row{grid-template-columns:minmax(0,1fr) 32px}.cf-row .cf-v{grid-column:1/2}}
/* Collapsed by default: only the row; the triangle opens the details row. */
.icard:not(.open) .ic-more{display:none}
.ic-tog{flex:none;width:28px;height:28px;border:0;border-radius:var(--radius-md);background:transparent;
  color:var(--color-neutral-500);display:grid;place-items:center;cursor:pointer}
.ic-tog:hover{background:var(--app-chip);color:var(--color-text)}
.ic-tog svg{transition:transform .15s ease}
.icard.open .ic-tog svg{transform:rotate(90deg)}
@media (prefers-reduced-motion:reduce){.ic-tog svg{transition:none}}
.ic-name{font-family:var(--font-heading);font-weight:600;font-size:16px;line-height:1.2;color:#fff;overflow-wrap:anywhere}
.ic-desc{font-size:12px;color:var(--color-neutral-600)}
.ipill{flex:none;display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 10px;border-radius:999px;
  color:var(--color-neutral-600);background:var(--app-chip)}
.ipill::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.icard[data-state=run] .ipill{color:var(--color-accent-600);background:var(--color-accent-100)}
.icard[data-state=old] .ipill{color:var(--color-accent-800);background:var(--color-accent-200)}
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
.chip{white-space:nowrap;font-size:11px;padding:3px 9px;border-radius:999px;border:0;background:var(--app-chip);color:var(--color-neutral-800)}
button.chip{cursor:pointer}
button.chip:hover{background:var(--app-chip-hover);color:#fff}
.ic-mnt{font-size:12px;color:var(--color-neutral-600);overflow-wrap:anywhere}
.ic-acts{display:flex;gap:4px;align-items:center;justify-content:flex-end;white-space:nowrap}
.ic-acts>.btn{height:30px;padding:0 10px}
.ic-icons{display:flex;gap:0}
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
  .kpi{padding:var(--space-4)}
  .kval{font-size:36px}
  .ftable .fx{display:none}
  .ftable .ipill{font-size:0;padding:6px;gap:0}
  .ftable td{padding:8px 6px}
  .f-name .ic-desc{max-width:150px}
  .f-name{width:auto}
  .ftable .f-mob{display:flex}
  .f-det{padding-left:0}
  .ic-acts{flex-wrap:wrap;white-space:normal}
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
</style><style id="kaim56-squads-refresh">/* kAIm56 Agent cloud — Squads refresh */
:root{--app-bg:#131716;--app-side:#171c19;--app-card:#1b211d;--app-card-hover:#232c24;--app-chip:#283327;--app-chip-hover:#33412e;--app-seg-on:#3b4d30;--app-line:#ffffff10;--color-text:#e0e7dc;--color-accent:#c9e8a5;--color-accent-2:#b5d29b;--color-neutral-100:#1b221d;--color-neutral-200:#293229;--color-neutral-300:#394337;--color-neutral-400:#53604d;--color-neutral-500:#7f8c78;--color-neutral-600:#95a08f;--color-neutral-700:#b0baaa;--color-neutral-800:#ccd5c5;--color-neutral-900:#e3eadc;--color-accent-100:#222f20;--color-accent-200:#2d4026;--color-accent-300:#425b34;--color-accent-400:#b7d898;--color-accent-500:#bfdcaa;--color-accent-600:#c9e8a5;--color-accent-700:#d6edbb;--color-accent-800:#e3f3d0;--color-accent-900:#edf9e0;--color-ok:#b6d68e;--color-warn:#d9b275;--app-live:#a8d88d;--app-ask:#c7b4de;--app-bad:#e0978d;--font-body:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;--font-heading:var(--font-body);--font-heading-weight:550;--font-mono:ui-monospace,SFMono-Regular,Menlo,monospace;--radius-sm:5px;--radius-md:7px;--radius-lg:12px;--shadow-sm:none;--shadow-md:0 8px 30px #0002}
body{font-size:14px;line-height:1.7}.shell{grid-template-columns:220px minmax(0,1fr)}.side{padding:27px 14px 18px;border-right:1px solid var(--app-line);gap:28px}.brand{gap:12px;padding:0 10px}.brand-txt b{font-size:25px;letter-spacing:-1px;font-weight:650}.brand-txt span{font-size:10px;letter-spacing:.8px;text-transform:none;margin-top:7px}.brand-mark{color:var(--color-accent)}.nav{gap:24px}.navlbl{font-size:9px;letter-spacing:1.6px;padding-bottom:8px}.nav a{font-size:12px;padding:8px 12px;border-radius:7px}.nav a[aria-current=page]{background:#c9e8a510;color:#dbeacf;font-weight:500;box-shadow:inset 2px 0 0 var(--color-accent)}.user{background:transparent;border-top:1px solid var(--app-line);border-radius:0;padding-top:18px}.avatar{border-radius:9px}.uinfo span:first-child{font-size:12px}.uinfo span+span{font-size:10px}main{padding:33px 38px;max-width:2000px;width:100%;margin:auto}.phead{margin-bottom:27px;min-height:55px}.peyebrow{font-size:9px;letter-spacing:1.7px;color:#92a085}.phead h1{font-size:31px;letter-spacing:-1px;margin-top:7px}.btn{font-size:12px;font-weight:500;padding:9px 13px;transition:background .15s,border-color .15s}.btn-primary{background:var(--color-accent);color:#26331f}.btn-primary:hover,.btn-primary:active{background:#ddf0bf;color:#26331f}.btn-secondary{background:#283225;color:#cddac2}.btn-ghost{color:#b2c49f}.input{font-size:13px;min-height:41px;border-radius:8px;padding:9px 12px;background:#161d18;border-color:#ffffff15}.input:hover{border-color:#657b52}.input:focus-visible{outline:2px solid #c9e8a53b;border-color:#a4bc8a}select.input{cursor:pointer}.field>label{font-size:11px}.tag{font-size:10px;padding:3px 9px}.appfoot{font-size:10px;opacity:.8}.af-brand{font-size:12px}.af-stat,.appfoot a{font-size:10px}
#s-squads>.lede{font-size:12px;line-height:1.9;max-width:760px;color:#94a28b;margin:0 0 28px}.sk{gap:22px}.sk-list{gap:9px;padding:0 0 5px}.sk-item{padding:10px 15px;border-radius:8px;border-color:#ffffff12;font-size:12px;gap:10px;background:#1b211b}.sk-item:hover{background:#252f22}.sk-item[aria-current]{background:#283723;border-color:#a5c38750;color:#d9ecc6}.sk-item[data-new]{border-style:solid}.sk-item[data-new] b{font-weight:500}.sk-item small{font-size:10px;color:#97a68b}.sk-chat{border:1px solid #ffffff0e;background:#191f1a;border-radius:14px;padding:27px}.sk-head{gap:16px;padding-bottom:20px;border-bottom:1px solid var(--app-line);margin-bottom:20px}.sk-head>div:first-child{display:flex;align-items:center;flex-wrap:wrap;gap:12px}#sk-title{font-size:22px;font-weight:550;letter-spacing:-.6px}#sk-status{display:inline-flex;align-items:center;flex-wrap:wrap;gap:8px}#sk-acts{display:flex;flex-wrap:wrap;gap:6px}#sk-acts .btn{font-size:11px;padding:6px 9px}.sk-tc{font-size:11px;color:#aab99e;gap:5px 10px}.sk-kick>.sk-comp{grid-template-columns:repeat(3,minmax(0,1fr));gap:22px 16px;align-items:start;margin:4px 0 0}.sk-field{display:flex;flex-direction:column;gap:8px;min-width:0}.sk-field>span,.sk-input-label{font-size:10px;font-weight:500;letter-spacing:.3px;color:#a5b29a}.sk-field .input{width:100%!important;min-width:0}.sk-field:has(>select[hidden]){display:none}.sk-kick .sk-field-repo{order:-3}.sk-kick .sk-field-arch{order:-2}.sk-kick .sk-field-dec{order:-1}.sk-kick .sk-inwrap{grid-column:1/-1;grid-row:auto;order:0}.sk-inwrap{display:flex!important;flex-direction:column;gap:8px}.sk-input-label{display:block}.sk-chat:not(.sk-kick) .kick-label,.sk-kick .chat-label{display:none}.sk-kick #sk-text{min-height:210px;padding:16px;font-size:13px;line-height:1.9;border-color:#4c623e;background:#161d17;resize:vertical}.sk-kick #sk-text:focus-visible{border-color:#b6d394}.sk-kick #sk-teambox{grid-column:1/3;grid-row:auto;order:1;padding-top:6px}.sk-kick #sk-send{grid-column:3;grid-row:auto;order:2;align-self:start;justify-self:end;min-width:150px;padding:13px 20px;font-size:12px}.sk-kick #sk-teambox>summary{font-size:11px;color:#afc19b;max-width:500px;line-height:1.8}.sk-kick #sk-team{font-family:var(--font-mono);font-size:11px!important;line-height:1.9;padding:14px;min-height:220px}.sk-kick #sk-chatbox{margin-top:18px}.sk-comp{gap:12px;margin-top:15px}.sk-chat:not(.sk-kick) #sk-send{align-self:end;min-height:42px}.sk-chat:not(.sk-kick) #sk-text{min-height:55px;padding:12px 14px}.sk-thread{gap:15px;margin:20px 0;scrollbar-width:thin}.sk-bub{padding:13px 16px;background:#151b16;font-size:13px;line-height:1.8;border:1px solid #ffffff08;border-radius:10px;max-width:90%}.sk-bub.me{background:#2a3526;border-radius:11px 11px 3px 11px;color:#d4e0ca}.sk-bub .text-muted{font-size:10px!important;margin-bottom:7px}#sk-chatbox>summary{font-size:11px;letter-spacing:.3px;padding-top:15px;border-top:1px solid var(--app-line)}.sk-reqdoc{font-size:12px;padding:15px;border-radius:8px}#sk-working{padding-top:10px;font-size:12px}#sk-msg{font-size:11px;color:#aec795}
#s-squads .kb-mem{background:#151c16;border-color:#ffffff0d;border-radius:10px;margin-top:20px}#s-squads .kb-mh{background:#1e271d;font-size:9px;letter-spacing:1.2px}#s-squads .kb-mh>span,#s-squads .kb-mr>div{padding:12px 14px}.kb-nm b{font-size:12px}.kb-nm small,.kb-l2{font-size:10px}.kb-l1{font-size:12px}.kb-av{border-radius:8px;font-family:var(--font-body);background:#34452a}.kb-sum{gap:16px;padding:15px 2px;margin:4px 0 15px;font-size:11px}.kb-pbar{height:4px;max-width:260px}.kb-board{grid-template-columns:repeat(5,minmax(185px,1fr));gap:12px;padding-bottom:12px;scrollbar-color:#45583a transparent;scroll-snap-type:x proximity}.kb-col{scroll-snap-align:start;background:#141b15;border:1px solid #ffffff08;padding:12px;gap:10px;min-height:280px;border-radius:10px;max-height:75vh}.kb-col .kb-ch{top:-12px;margin:-12px -12px 3px;padding:15px 14px 11px;background:#182019;font-size:9px;letter-spacing:1.1px}.kb-ch b{font-size:10px;background:#ffffff08;border-radius:4px;padding:1px 6px}.kb-tk{background:#222b21;border-color:#ffffff0d;padding:13px 12px;gap:11px;border-radius:8px;transition:border-color .15s,background .15s}.kb-tk:hover{background:#293627;border-color:#7c9860}.kb-ti{font-size:12px;line-height:1.6}.kb-key{font-size:10px}.kb-meta{font-size:10px;gap:8px}.kb-dep,.kb-q,.kb-t{font-size:10px}.kb-empty{font-size:10px;line-height:1.9;color:#819077;padding:24px 9px;border-color:#ffffff0b}.kb-legend{gap:10px 18px;font-size:9px;line-height:1.8;margin-top:8px}.kb-prob{font-size:12px;border-color:#d9b27535;padding:12px 14px;line-height:1.7}.kb-pane{background:#1b241b;border-color:#7e9a5a35;box-shadow:-20px 0 80px #0006}.kb-ptop{padding:24px;gap:16px}.kb-ptitle{font-size:23px;line-height:1.4}.kb-pbody{padding:0 24px 20px}.kb-md{font-size:13px;line-height:1.9}.kb-ctl{gap:8px}.kb-sel{font-size:11px;padding-top:6px;padding-bottom:6px}.sq-card{border-color:#ffffff12;padding:20px;border-radius:12px;background:#20291e}.sq-mem{border-color:#ffffff10;border-radius:8px;padding:14px}.sq-foot{padding:14px 20px;margin:18px -20px -20px}.sq-row{gap:8px 14px}.sq-role,.sq-by{font-size:10px}.sq-sub{font-size:9px;letter-spacing:1px}.sq-set{border:1px solid #ffffff10}.sq-more{padding-top:15px;margin-top:18px}.sq-more>summary{font-size:12px}.sq-chks label{font-size:12px}.sq-chks{gap:10px 20px}.sq-tk{padding:12px 0;font-size:12px}.kb-st{font-size:11px}
@media(min-width:1600px){main{padding:38px 48px}.sk-chat{padding:32px}.kb-board{grid-template-columns:repeat(5,minmax(200px,1fr))}}
@media(max-width:1100px){.shell{grid-template-columns:190px minmax(0,1fr)}main{padding:28px 25px}.side{padding:25px 10px}.sk-chat{padding:22px}.sk-kick>.sk-comp{gap:18px 12px}.sk-field>span{font-size:9px}.sk-field select.input{font-size:11px;padding:9px}.kb-mh,.kb-mr{grid-template-columns:minmax(120px,165px) minmax(170px,250px) minmax(100px,1fr)}}
@media(max-width:860px){.shell{grid-template-columns:1fr}main{padding:75px 20px 25px}.side{width:230px}.mbar{background:#171e18;border-bottom:1px solid var(--app-line)}.sk-chat{padding:20px}.kb-mr{grid-template-columns:1fr}.kb-mh{display:none}.sk-head{gap:12px}.kb-board{grid-template-columns:repeat(5,minmax(210px,1fr))}}
@media(max-width:600px){main{padding-left:14px;padding-right:14px}.phead h1{font-size:28px}.sk-kick>.sk-comp{grid-template-columns:1fr;gap:18px}.sk-kick .sk-field,.sk-kick .sk-inwrap,.sk-kick #sk-teambox,.sk-kick #sk-send{grid-column:1;grid-row:auto}.sk-kick #sk-send{justify-self:stretch}.sk-kick #sk-text{min-height:240px;font-size:12px}.sk-field>span{font-size:10px}.sk-field select.input{font-size:12px}.sk-chat{padding:17px}.sk-head{align-items:start}#sk-title{font-size:20px}.sk-comp:not(.sk-kick .sk-comp){flex-wrap:wrap}.sk-chat:not(.sk-kick) .sk-inwrap{flex-basis:100%!important}.sk-bub{max-width:95%}.kb-board{grid-template-columns:repeat(5,minmax(220px,1fr))}.kb-pane{width:100%}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
</style><style id="squad-workspace-layout">/* A focused workspace instead of a stack of administration panels. */
#s-squads .sk-chat:not(.sk-kick){display:block;background:transparent;border:0;border-radius:0;padding:0}
#s-squads .sk-chat:not(.sk-kick) .sk-head{padding:0 0 15px;margin:0;border:0;flex-wrap:nowrap;align-items:center}
#s-squads .sk-chat:not(.sk-kick) #sk-title{font-size:23px;letter-spacing:-.7px}
#s-squads .sk-chat:not(.sk-kick) #sk-status{font-size:10px}
#s-squads .sk-chat:not(.sk-kick) #sk-status>code,#s-squads .sk-chat:not(.sk-kick) #sk-status>details,#s-squads .sk-chat:not(.sk-kick) #sk-status>.text-muted{display:none}
#s-squads .sk-chat:not(.sk-kick) #sk-acts{display:none}
.squad-workspace-tabs{display:flex;gap:23px;align-items:center;border-bottom:1px solid #ffffff13;margin:0 0 22px;overflow-x:auto;scrollbar-width:thin}
.squad-workspace-tabs button{display:flex;align-items:center;gap:7px;white-space:nowrap;background:none;border:0;border-bottom:2px solid transparent;padding:13px 0 14px;color:#8f9e86;font:inherit;font-size:12px;cursor:pointer}
.squad-workspace-tabs button:hover{color:#d7e8c7}
.squad-workspace-tabs button[aria-current=page]{border-bottom-color:#c9e8a5;color:#d9edc7}
.squad-tab-count{display:inline-grid;place-items:center;min-width:18px;height:18px;padding:0 4px;border-radius:5px;background:#ffffff08;color:#aebea0;font-size:9px}
.squad-action-menu{position:relative;flex-shrink:0;font-size:11px;margin-left:auto}
.squad-action-menu>summary{list-style:none;cursor:pointer;border:1px solid #ffffff16;border-radius:7px;padding:6px 12px;color:#adbd9f;background:#1c261b}
.squad-action-menu>summary::after{content:'⌄';padding-left:12px}
.squad-menu-items{position:absolute;z-index:35;right:0;top:calc(100% + 8px);min-width:185px;display:flex;flex-direction:column;border:1px solid #ffffff1a;border-radius:10px;padding:6px;background:#232e21;box-shadow:0 15px 50px #0006}
.squad-menu-items button{font:inherit;font-size:11px;border:0;background:none;text-align:left;padding:10px;border-radius:5px;color:#c6d4ba;cursor:pointer}
.squad-menu-items button:hover{background:#ffffff08}.squad-menu-items button.danger{color:#e0a297;border-top:1px solid #ffffff09;margin-top:5px;padding-top:12px}
.sk-kick .squad-workspace-tabs,.sk-kick .squad-action-menu{display:none}
.sk-chat:not(.sk-kick)>#sk-prob,.sk-chat:not(.sk-kick)>#sk-tc,.sk-chat:not(.sk-kick)>#sk-dp,.sk-chat:not(.sk-kick)>#sk-req,.sk-chat:not(.sk-kick)>#sk-cards,.sk-chat:not(.sk-kick)>#sk-results,.sk-chat:not(.sk-kick)>#sk-board,.sk-chat:not(.sk-kick)>#sk-chatbox,.sk-chat:not(.sk-kick)>.sk-comp,.sk-chat:not(.sk-kick)>#sk-working,.sk-chat:not(.sk-kick)>#sq-panel,.sk-chat:not(.sk-kick)>.squad-no-approvals{display:none!important}
.sk-chat:not(.sk-kick)[data-view=board]>#sk-board:not([hidden]),.sk-chat:not(.sk-kick)[data-view=team]>#sk-board:not([hidden]){display:block!important}
.sk-chat[data-view=board] #sk-board>.kb-mem{display:none}
.sk-chat[data-view=team] #sk-board>:not(.kb-mem){display:none}
.sk-chat[data-view=team] #sk-board>.kb-mem{margin-top:0}
.sk-chat:not(.sk-kick)[data-view=chat]>#sk-chatbox,.sk-chat:not(.sk-kick)[data-view=chat]>.sk-comp,.sk-chat:not(.sk-kick)[data-view=chat]>#sk-working{display:flex!important}
.sk-chat:not(.sk-kick)[data-view=chat]>#sk-chatbox{display:block!important}
.sk-chat[data-view=chat] #sk-chatbox>summary{display:none}
.sk-chat[data-view=chat] .sk-thread{height:45vh;max-height:55vh;min-height:220px;padding:12px 3px;margin:0 0 12px}
.sk-chat[data-view=chat]>.sk-comp{background:#1b251b;border:1px solid #5d754848;border-radius:12px;padding:12px}
.sk-chat:not(.sk-kick)[data-view=approvals]>#sk-cards{display:block!important;max-height:none;overflow:visible}
.sk-kick>#sk-results{display:none}
.sk-chat:not(.sk-kick)[data-view=results]>#sk-results{display:grid!important;grid-template-columns:220px minmax(0,1fr) 280px;gap:12px;align-items:start}
#sk-results .rv-list{display:flex;flex-direction:column;gap:6px}
#sk-results .rv-item{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;text-align:left;padding:8px 10px;border-radius:8px;border:1px solid #ffffff12;background:#1b241b;color:inherit;font:inherit;font-size:12.5px;cursor:pointer}
#sk-results .rv-item.on{border-color:var(--color-accent-400)}
#sk-results .rv-item>.text-muted{flex-basis:100%;font-size:11.5px}
#sk-results .rv-files{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}
#sk-results .rv-sum{white-space:pre-wrap;font-size:12.5px;margin:4px 0 8px}
#sk-rframe{width:100%;height:max(420px,calc(100dvh - 330px));border:1px solid #ffffff1a;border-radius:8px;background:#fff}
#sk-results .rv-side{display:flex;flex-direction:column;gap:8px;position:sticky;top:8px}
#sk-results .rv-note{border:1px solid #ffffff12;border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:6px;font-size:12.5px}
#sk-results .rv-note q{font-style:italic;overflow-wrap:anywhere}
#sk-results .rv-acts{display:flex;gap:8px;justify-content:flex-end}
@media (max-width:1100px){.sk-chat:not(.sk-kick)[data-view=results]>#sk-results{grid-template-columns:1fr}#sk-results .rv-side{position:static}}
.sk-chat:not(.sk-kick)[data-view=approvals]>.squad-no-approvals:not([hidden]){display:block!important}
.squad-no-approvals{text-align:center;border:1px dashed #c9e8a526;border-radius:12px;padding:65px 25px;color:#96aa86;font-size:13px}
.sk-chat:not(.sk-kick)[data-view=settings]>#sk-tc,.sk-chat:not(.sk-kick)[data-view=settings]>#sk-dp,.sk-chat:not(.sk-kick)[data-view=settings]>#sk-req{display:flex!important;flex-wrap:wrap;gap:8px;padding:14px 16px;background:#1b241b;border:1px solid #ffffff0a;border-radius:8px;margin:0 0 12px}
.sk-chat:not(.sk-kick)[data-view=settings]>#sq-panel{display:block!important;padding:22px;background:#1b231b}
.sk-chat[data-view=board] .kb-sum{margin:0 0 16px;padding:0 0 12px}
.sk-chat[data-view=board] .kb-board{min-height:360px;grid-template-columns:repeat(5,minmax(175px,1fr));gap:10px}
.sk-chat[data-view=board] .kb-col{min-height:340px}
body:has(.sk-chat:not(.sk-kick)) #s-squads>.lede{display:none}
body:has(.sk-chat:not(.sk-kick)) main>.phead{margin-bottom:17px;min-height:40px}
body:has(.sk-chat:not(.sk-kick)) main>.phead h1{font-size:24px}
body:has(.sk-chat:not(.sk-kick)) #s-squads>.sk{gap:19px}
@media(max-width:1100px){main{padding:23px 25px}.sk-item{padding:8px 11px;font-size:11px}.squad-workspace-tabs{gap:20px}.squad-workspace-tabs button{font-size:11px}.sk-chat[data-view=board] .kb-board{grid-template-columns:repeat(5,minmax(175px,1fr))}}
@media(max-width:860px){main{padding-top:74px}.squad-workspace-tabs{gap:20px}.sk-chat[data-view=team] .kb-mr{grid-template-columns:1fr}.sk-chat[data-view=board] .kb-board{grid-template-columns:repeat(5,minmax(210px,1fr))}}
@media(max-width:600px){.squad-workspace-tabs{gap:20px}.sk-head>div:first-child{gap:7px!important;min-width:0}#sk-title{overflow-wrap:anywhere}.squad-action-menu>summary{padding:6px 9px}.sk-chat[data-view=chat]>.sk-comp{flex-wrap:wrap}.sk-chat[data-view=settings]>#sq-panel{padding:16px}.demo-tools-body{display:block}.demo-actions{margin-top:12px}}

.sk-kick .squad-no-approvals{display:none!important}
</style><style>.squad-board-tools{display:none;align-items:center;justify-content:space-between;gap:15px;margin:-4px 0 17px}
.sk-chat:not(.sk-kick)[data-view=board]>.squad-board-tools{display:flex}
.squad-ticket-search{display:flex;align-items:center;gap:9px;max-width:340px;width:100%;padding:8px 11px;border:1px solid #ffffff13;border-radius:7px;background:#1a231a;color:#829575}
.squad-ticket-search:focus-within{border-color:#a3c18580}
.squad-ticket-search input{width:100%;min-width:0;border:0;background:none;color:#d4e3c8;font:inherit;font-size:11px;outline:none}
.squad-ticket-search input::placeholder{color:#809175}
.squad-search-result{color:#7e9172;font-size:9px;white-space:nowrap}
.sk-chat[data-view=board] .kb-col{min-height:280px;max-height:max(280px,calc(100dvh - 360px));overflow-y:auto;overscroll-behavior-y:contain;scrollbar-width:thin;scrollbar-color:#526644 transparent;scrollbar-gutter:stable}
.sk-chat[data-view=board] .kb-col::-webkit-scrollbar{width:5px}
.sk-chat[data-view=board] .kb-col::-webkit-scrollbar-thumb{background:#526644;border-radius:4px}
.kb-tk[hidden]{display:none!important}
.kb-col[data-st=blocked] .kb-ch b{background:#d9b27516;color:#d9b275;min-width:24px;text-align:center}
.kb-col[data-st=blocked] .kb-ti{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.kb-col[data-st=blocked] .kb-why{font-size:10px;line-height:1.7;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
@media(max-width:650px){.squad-board-tools{flex-wrap:wrap;gap:8px}.squad-ticket-search{max-width:none}.sk-chat[data-view=board] .kb-col{max-height:60dvh}}
</style>"""
