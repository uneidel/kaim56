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
</style>"""
