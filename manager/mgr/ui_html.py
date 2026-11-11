# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI — page markup."""
import html

from mgr import squads as _squads

HTML_TOP = """</head><body>
<div class=shell>
<div class=mbar>
  <button class=iconbtn onclick=navToggle(true) aria-label=Menu><svg width=20 height=20 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M4 6h16M4 12h16M4 18h16"></path></svg></button><b>kAIm56</b>
</div>
<div class=navshade onclick=navToggle(false)></div>
<aside class=side id=side>
  <div class=brand><div class=brand-mark>__LOGO__</div>
    <div class=brand-txt><b>kAIm56</b><span>Agent cloud</span></div></div>
  <div class=user>
    <div class=avatar id=uavatar>A</div>
    <div class=uinfo><span id=uname>Admin</span><span id=urole>basic</span></div>
    <button class=iconbtn id=nbell onclick=notifToggle() title="Notifications" aria-label=Notifications>
      <svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"></path><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"></path></svg>
      <span class=nbadge id=nbadge hidden>0</span>
    </button>
    <button class=iconbtn id=whoami onclick=whoLogout() title="Log out" aria-label="Log out" hidden><svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><path d="m16 17 5-5-5-5M21 12H9"></path></svg></button>
  </div>
  <nav class=nav id=nav aria-label=Pages>
    <div class=navgrp><div class=navlbl>Agents</div>
      <a href="#instances"><span>Instances</span> <span class=navcnt id=navcnt></span></a>
      <a href="#resources"><span>Resources</span></a>
    </div>
    <div class=navgrp><div class=navlbl>Knowledge</div>
      <a href="#personas"><span>Personas</span></a>
      <a href="#skills"><span>Skills</span></a>
      <a href="#plugins"><span>Plugins</span></a>
      <a href="#mcp"><span>MCP</span></a>
    </div>
    <div class=navgrp><div class=navlbl>Work</div>
      <a href="#tasks"><span>Tasks</span></a>
      <a href="#missions"><span>Missions</span></a>
      <a href="#projects"><span>Repos</span></a>
      <a href="#squads"><span>Squads</span></a>
      <a href="#waiting"><span>Waiting for you</span> <span class=navcnt id=waitcnt></span></a>
    </div>
    <div class=navgrp><div class=navlbl>Apps</div>
      <a href="#apps"><span>Apps</span></a>
      <a href="#sharing"><span>Sharing</span></a>
    </div>
    <div class=navgrp><div class=navlbl>System</div>
      <a href="#models"><span>Models</span></a>
      <a href="#secrets"><span>Secrets</span></a>
      <a href="#settings"><span>Settings</span></a>
    </div>
  </nav>
  <div class=cgauges id=cgauges aria-label="Claude usage" hidden></div>
</aside>
<div class=npanel id=npanel hidden>
  <div class=nhead><b>Notifications</b><button class="btn btn-ghost" style="font-size:12px" onclick=notifClear()>Clear</button></div>
  <div id=nlist><span class=text-muted style="font-size:13px;padding:12px;display:block">…</span></div>
</div>
<main>
<header class=phead>
  <div><div class=peyebrow id=pgroup>Agents</div><h1 id=ptitle>Instances</h1></div>
  <button class="btn btn-primary" id=newinst onclick=newInstance()><svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M12 5v14M5 12h14"></path></svg>New instance</button>
  <button class="btn btn-primary" id=newpers onclick=personaOpen() hidden><svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M12 5v14M5 12h14"></path></svg>New persona</button>
  <button class="btn btn-primary" id=newskill onclick=skillOpen() hidden><svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M12 5v14M5 12h14"></path></svg>New skill</button>
</header>

<section class="screen" id=s-instances>
  <p class=lede>Chat opens <a href="/chat">/chat</a> for every <code>TRANSPORT=web</code> instance · a stopped instance starts on the first prompt</p>
  <div class=kpis>
    <div class="kpi ring">
      <svg width=84 height=84 viewBox="0 0 84 84"><circle cx=42 cy=42 r=34 stroke="var(--app-line)"></circle>
        <circle cx=42 cy=42 r=34 stroke="var(--color-accent-600)" stroke-linecap=round id=kring stroke-dasharray="0 214"></circle></svg>
      <div><div class=klbl>Running</div>
        <div class=kval style="margin-top:4px"><span id=krun>0</span><small> / <span id=ktot>0</span></small></div>
        <div class=ksub style="margin-top:6px" id=krsub></div></div>
    </div>
    <div class=kpi><div class=klbl>Tokens today</div><div class=kval id=ktok>0</div>
      <div class=ksub><span><b id=ktin>0</b> in</span><span><b id=ktout>0</b> out</span></div></div>
    <div class=kpi><div class=khead><span class=klbl>Spend · 14 days</span><span id=kspendt></span></div>
      <div class=kval id=kspend>$0.00</div><div class=kbars id=kbars></div></div>
    <div class=kpi><div class=klbl>Allocated</div>
      <div class=kval><span id=kcpu>0</span><small id=kram> vCPU</small></div>
      <div class=track><i id=kmembar></i></div><div class=ksub id=kmemsub></div></div>
  </div>
  <section class=chart>
    <div class=chart-head>
      <div><div class=klbl>Token throughput</div><div class=chart-tot><b id=ctot>0</b><span id=cscope></span></div></div>
      <div class=seg id=crange><button data-r=24h class=on>24h</button><button data-r=7d>7d</button><button data-r=30d>30d</button></div>
    </div>
    <div class=cchips id=cchips></div>
    <div class=cplot><div class=cyt id=cyt></div>
      <div><svg viewBox="0 0 1000 200" preserveAspectRatio=none>
        <defs><linearGradient id=cfill x1=0 y1=0 x2=0 y2=1><stop offset=0 stop-color="var(--color-accent-600)" stop-opacity=".55"></stop>
          <stop offset=1 stop-color="var(--color-accent-600)" stop-opacity=".04"></stop></linearGradient></defs>
        <g stroke="var(--app-line)"><line x1=0 x2=1000 y1=50 y2=50></line><line x1=0 x2=1000 y1=100 y2=100></line><line x1=0 x2=1000 y1=150 y2=150></line></g>
        <g id=cplot></g></svg>
        <div class=cxl id=cxl></div></div>
    </div>
  </section>
  <div class=fhead><h3>Fleet</h3>
    <input class="input fsearch" id=fsearch type=search placeholder="Search name, model, template…" aria-label="Search the fleet" oninput=fleetFilter()>
    <div class=seg id=ffilt><button data-f=all class=on>All <span></span></button><button data-f=run>Running <span></span></button>
      <button data-f=off>Stopped <span></span></button><button data-f=old>Outdated <span></span></button></div></div>
  <div class=obanner id=obanner hidden><svg width=18 height=18 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4M12 17h.01"></path></svg>
    <span><b id=ocount></b> run on an outdated image.</span>
    <button class="btn btn-primary" onclick=restartAll()>Restart all</button></div>
  <div class=fleet id=fleet>__ROWS__</div>
  <div id=svcvms hidden><div class=fhead style="margin-top:24px"><h3>Service VMs</h3><span class=text-muted style="font-size:12.5px">a project's deployed base branch · managed in Squads → Settings → Deploy</span></div>
    <div class="panel blueprint" id=svclist></div></div>

  <div id=newdlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)newClose()">
  <div class="dialog blueprint" id=newform role=dialog aria-modal=true aria-labelledby=newtitle style="width:min(960px,100%)">
    <div style="display:flex;align-items:center;gap:10px"><div class=dialog-title id=newtitle>New instance from template</div>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick=newClose() aria-label=Close>✕</button></div>
    <div>
      
      <div class=grid2>
        <div class=field><label>Template</label><select class=input id=tpl onchange=renderParams()>__TPLS__</select></div>
        <div class=field><label>Instance name</label><input class=input id=nm placeholder="e.g. fabric-gpt4o"></div>
        <div class=field><label>Persona / system prompt (optional)</label>
          <select class=input id=persona><option value="">— Default —</option></select></div>
        <div class="field span2"><label>Purpose (optional) — Jev suggests persona, model and skills; change anything before you create</label>
          <div style="display:flex;gap:6px"><input class=input id=purpose maxlength=1000 placeholder="e.g. review the backend's pull requests for security issues" style="flex:1;min-width:0">
          <button type=button class="btn btn-secondary" id=suggestbtn onclick=jevSuggest()>Suggest</button></div>
          <span class=text-muted id=suggestmsg style="font-size:12px"></span></div>
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
            <span class=text-muted style="font-size:12px">Host path → guest path · pick with 📁 · "ro" = read-only · the agent writes as host user <code>kaim56-guest</code>, so a "rw" folder must let that user in · no host folder? <a href="#sharing">share one from your browser via katfs</a></span>
          </div>
        </div>
        <div class="field span2"><label>Skills (optional, in addition to the standard skills)</label>
          <div id=skillpick style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:4px 16px;max-height:200px;overflow:auto"></div></div>
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
      <div class=panel-foot><button class="btn btn-secondary" onclick=newClose()>Cancel</button><button class="btn btn-primary" onclick=create()>Create instance</button></div>
    </div>
  </div>
  </div>
</section>

<section class="screen" id=s-personas>
  <p class=lede>Selectable as "Persona" when creating an instance → sets <code>AGENT_SYSTEM</code> (applied at start, no rebuild)</p>
  <div class=fhead><h3>Personas</h3>
    <input class="input fsearch" id=psearch type=search placeholder="Search name, prompt…" aria-label="Search the personas" oninput=renderPersonas()></div>
  <div class=grid2 id=personas></div>
  <div id=persdlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)personaClose()">
  <div class="dialog blueprint" role=dialog aria-modal=true aria-labelledby=perstitle style="width:min(760px,100%)">
    <div style="display:flex;align-items:center;gap:10px"><div class=dialog-title id=perstitle>New persona</div>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick=personaClose() aria-label=Close>✕</button></div>
    <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=pname placeholder="e.g. researcher"></div>
    <div class=field><label>Prompt</label><textarea class=input id=pprompt style="min-height:220px"></textarea></div>
    <div class=panel-foot style="border:none;padding:0"><span id=pmsg class=msg></span>
      <button class="btn btn-secondary" onclick=personaClose()>Cancel</button>
      <button class="btn btn-primary" onclick=savePersona()>Save persona</button></div>
  </div></div>

  <div class=sec-head style="margin-top:36px">
    <div><h6>Recurring jobs</h6><h3 style="font-size:22px">Prompt templates</h3></div>
    <span class="note text-muted">As a slash command in chat: <code>/name [extra]</code> — the agent expands it server-side (web, app and Signal)</span>
  </div>
  <div class="panel blueprint">
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
  <div class=fhead>
    <input class="input fsearch" id=pbq type=search placeholder="Search rules…" aria-label="Search the playbooks" oninput=renderPlaybooks()>
    <select class=input id=pbf style="width:auto" aria-label="Filter by agent" onchange=renderPlaybooks()><option value="">all agents</option></select></div>
  <div class=tblwrap id=pblist><span class=text-muted style="font-size:13px;padding:14px;display:block">…</span></div>
  <div class="panel blueprint" style="margin-top:16px">
    <div class=pbgrid>
      <div class=field><label>Agent</label><select class=input id=pbinst></select></div>
      <div class=field><label>New rule</label>
        <textarea class=input id=pbtext style="min-height:56px" placeholder="Always fetch stock prices via http_fetch from query1.finance.yahoo.com …"></textarea></div></div>
    <div class=panel-foot><span id=pbmsg class=msg></span><button class="btn btn-primary" onclick=addPlaybook()>Add rule</button></div>
  </div>
</section>

<section class="screen" id=s-skills>
  <p class=lede>Loaded into context on demand via <code>load_skill("name")</code> · <code>list_skills</code> shows them · central, no rebuild</p>
  <div id=skprops></div>
  <div class=fhead>
    <input class="input fsearch" id=skq type=search placeholder="Search name, description…" aria-label="Filter skills by keyword" oninput=renderSkills(true)>
    <select class=input id=skcat style="width:auto" aria-label="Category" onchange=renderSkills(true)><option value="">all categories</option>__SKCATS__</select>
    <select class=input id=skmode style="width:auto" aria-label="Mode" onchange=renderSkills(true)><option value="">standard + on demand</option><option value=std>standard</option><option value=dem>on demand</option></select></div>
  <div class=tblwrap id=skills></div>
  <p class=text-muted style="font-size:12px;margin-top:8px">security skills reach an agent only with SECURITY_SKILLS=1 or its squad&#8217;s &#8220;Security skills&#8221;</p>
  <div id=skilldlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)skillClose()">
  <div class="dialog blueprint" role=dialog aria-modal=true aria-labelledby=sktitle style="width:min(900px,100%)">
    <div style="display:flex;align-items:center;gap:10px"><div class=dialog-title id=sktitle>New skill</div>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick=skillClose() aria-label=Close>✕</button></div>
    <div class=grid2>
      <div class=field><label>Name (a-z 0-9 _ -)</label><input class=input id=skname placeholder="e.g. postgres-expert"></div>
      <div class=field><label>Short description</label><input class=input id=skdesc placeholder="When to load this skill?"></div>
      <div class="field span2"><label>Content (Markdown)</label><textarea class=input id=skcontent style="min-height:300px;font-family:var(--font-mono);font-size:12.5px"></textarea></div>
    </div>
    <div class=panel-foot style="border:none;padding:0"><span id=skmsg class=msg></span>
      <button class="btn btn-secondary" onclick=skillClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveSkill()>Save skill</button></div>
  </div></div>
</section>

<section class="screen" id=s-plugins>
  <p class=lede>Drag a <code>.py</code> file or a <code>.zip</code> (multi-file &#8594; its own folder) here. Convention: <code>DESC / PARAMS / REQUIRED / run()</code>. Runs in the agent VM (sandbox, stdlib) &#183; restart the instance to activate.</p>
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
  <p class=lede>Attachable per instance · use <code>${SECRET_NAME}</code> in args to inject from the secret store at create time</p>
  <div class=grid2 id=mcps></div>
  <div class="panel blueprint" style="margin-top:32px">
    
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
  <p class=lede>Configured size (vCPU/RAM) and live usage per instance &#183; CPU% is relative to ONE core (a 2-vCPU guest goes up to ~200%) &#183; Disk = written overlay layer.</p>
  <table class=table id=restable>
    <thead><tr><th style="width:22%">Instance</th><th>Status</th><th>vCPU</th><th>RAM (config.)</th><th>RAM (used)</th><th>CPU</th><th>Disk (Overlay)</th></tr></thead>
    <tbody id=resrows><tr><td colspan=7 class=text-muted style="padding:14px">…</td></tr></tbody>
  </table>
  <div class=sec-head style="margin-top:28px">
    <div><h6>LLM usage</h6><h3>Tokens &amp; cost per instance</h3></div>
    <div class=actwin id=uwin>
      <button data-w=86400 onclick="usageWindow(86400)">24h</button>
      <button data-w=604800 onclick="usageWindow(604800)">7d</button>
      <button data-w=2592000 onclick="usageWindow(2592000)">30d</button>
      <button data-w=0 class=on onclick="usageWindow(0)">All</button>
    </div>
  </div>
  <div class=text-muted style="font-size:12px;margin:-6px 0 12px">Tokens in and out, each stacked by model on its own scale, cost as billed by the provider &#183; one-shot task VMs are summed as &#8220;tasks&#8221;.</div>
  <div id=usagechart class=text-muted style="font-size:13px">…</div>
  <div class=sec-head style="margin-top:28px"><div><h6>Model router</h6><h3>Routing: saved vs. always-strong</h3></div></div>
  <div class=text-muted style="font-size:12px;margin:-6px 0 12px">The turns the router decided on: their cost as billed (catalog price where none was reported, local = 0) against the same tokens on the strong model.</div>
  <div id=rtsave class=text-muted style="font-size:13px">…</div>
</section>

<section class="screen" id=s-models>
  <p class=lede>The full catalog, fetched live · ticked models are the shortlist offered when creating an instance</p>
  <div class="banner blueprint">
    
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

  <div class="panel blueprint" style="margin-top:32px">
    <h4 style="margin:0 0 6px">Model router</h4>
    <p class=text-muted style="font-size:12.5px;margin:0 0 12px;max-width:80ch">Jev (OpenJev, a small NLI classifier) reads the first message of a turn and the key proxy
      runs that turn on another model — only for instances where you switch a router policy on (Policy: the shield on its Fleet card), only among the models your
      <b>running</b> instances use, and only those you give a tier here. Jev slow or down: the instance's own model, as always.</p>
    <div id=rtjev class=text-muted style="font-size:12.5px;margin-bottom:10px">…</div>
    <p class=text-muted style="font-size:12.5px;margin:0 0 12px;max-width:80ch">Policy <b>cheapest</b>: each turn gets a level (1 small talk · 2 simple question/edit · 3 code change · 4 multi-file/architecture · 5 hard reasoning)
      and runs on the cheapest model whose level is at least that (price: blended 3:1 input:output per 1M tokens from the catalog; local 0). A failed turn runs once more one level up.
      Level per model: set here, else from its tier (local/cheap 2, code 4, strong 5); a catalog model with a level (column &#8220;Ladder&#8221; above) is a target only for a policy with <code>"catalog": true</code>.</p>
    <table class=table><thead><tr><th>Model (running)</th><th>Used by</th><th style="width:160px">Tier</th><th style="width:110px">Level</th><th>Price</th></tr></thead><tbody id=rtcands></tbody></table>
    <div class=panel-foot><span id=rtmsg class=msg></span><button class="btn btn-primary" onclick=saveTiers()>Save tiers</button></div>
    <h4 style="margin:22px 0 8px;font-size:14px">Latest decisions</h4>
    <div id=rtrecent class=text-muted style="font-size:12.5px">…</div>
  </div>
</section>


<section class="screen" id=s-tasks>
  <p class=lede>One message runs on one instance — once or recurring · a stopped instance is started for it</p>
  <div style="display:flex;justify-content:flex-end;margin-bottom:8px"><button class="btn btn-secondary btn-sm" onclick=delDoneTasks()>Delete done and errors</button></div>
  <table class=table>
    <thead><tr><th style="width:20%">Instance</th><th>Job</th><th>Schedule</th><th>Status</th><th>Last result</th><th></th></tr></thead>
    <tbody id=taskrows></tbody>
  </table>
  <div class="panel blueprint" style="margin-top:32px">
    
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
  <p class=lede>Multi-step jobs from the orchestrator — plan + progress survive restart and context reset · a finished task immediately triggers the next step · every mission can be edited or deleted here, whatever its status</p>
  <div id=missions class="panel blueprint">
    <span class=text-muted style="font-size:13px">…</span>
  </div>
  <p class=text-muted style="font-size:12.5px;margin-top:14px">Any agent creates a mission itself when a job needs several steps — e.g. via chat: "… — as a mission". The owner plans, the steps run on whichever instance has the needed tools.</p>
</section>

<section class="screen" id=s-squads>
  <p class=lede>Give your squad a repository and a goal. The architect proposes a team, tickets and a budget. You review the plan before execution.</p>
  <div class=sk>
    <nav class=sk-list id=sk-list aria-label=Squads><span class=text-muted style="font-size:13px">…</span></nav>
    <div class="panel blueprint sk-chat">
      <div class=sk-head><div><b id=sk-title>New squad</b> <span id=sk-status></span></div><div id=sk-acts></div></div>
      <div id=sk-prob class=kb-prob role=status hidden></div>
      <div id=sk-tc class=sk-tc aria-live=polite></div>
      <div id=sk-dp class=sk-tc aria-live=polite></div>
      <div id=sk-req aria-live=polite></div>
      <div id=sk-cards></div>
      <div id=sk-results aria-label="Squad results"></div>
      <div id=sk-board hidden aria-label="Squad board"></div>
      <div id=sk-graph aria-label="Squad git graph"></div>
      <div id=sk-working aria-live=polite></div>
      <div class=sk-comp><label class="sk-field sk-field-arch"><span>Architect runtime</span><select class=input id=sk-arch aria-label=Architect title="The architect: OpenRouter (API model, token budget) or Claude (Claude Code on your subscription)" style="width:auto">
        <option value=openrouter>Architect: OpenRouter</option><option value=claude>Architect: Claude</option></select></label>
        <label class="sk-field sk-field-dec"><span>Member suggestions</span><select class=input id=sk-dec aria-label=Suggestions title="Who suggests persona, skills and model tier for the members: OpenJev (JEV_URL) or SubQ (SUBQ_API_KEY in Settings)" style="width:auto">
          <option value="">Suggestions: as in Settings</option><option value=jev>Suggestions: OpenJev</option><option value=cloud-llm>Suggestions: cloud LLM</option><option value=subq>Suggestions: SubQ</option></select></label>
        <label class="sk-field sk-field-repo"><span>Repository</span><select class=input id=sk-repo aria-label=Repository title="A new clone from a URL, a new empty repo, or an existing repo from Repos" style="width:auto" onchange=skRepoPick()>
          <option value="">Repo: from a URL</option></select></label>
        <label class="sk-field sk-field-name" id=sk-namef hidden><span>Project name</span><input class=input id=sk-name maxlength=20 placeholder="e.g. tankstellen-app" aria-label="Project name" autocomplete=off></label>
        <span class=sk-inwrap style="flex:1;min-width:0;position:relative;display:flex"><label class="sk-input-label" for="sk-text"><span class="kick-label">Goal & context</span><span class="chat-label">Message the squad</span></label><textarea class=input id=sk-text rows=1 style="flex:1;min-width:0;resize:vertical" aria-label=Message autocomplete=off
          role=combobox aria-autocomplete=list aria-controls=sk-at aria-expanded=false oninput="skAt();skGrow(this)" onkeydown="skAtKey(event)" onblur="setTimeout(skAtClose,150)"></textarea>
          <div id=sk-at class=sk-at role=listbox hidden></div></span>
        <button class="btn btn-primary" id=sk-send onclick=skSend()>Send</button>
      <details id=sk-teambox ><summary>Customize the team · leave empty to let the architect decide</summary>
        <textarea class=input id=sk-team rows=9 aria-label=Team style="width:100%;margin-top:6px;font-size:12.5px">__TEAM_DEFAULT__</textarea></details>
      </div>
      <span id=sk-msg class=msg style="margin-top:6px"></span>
      <details id=sk-chatbox ontoggle="if(this.open){const t=document.getElementById('sk-thread');t.scrollTop=t.scrollHeight}">
        <summary id=sk-chatsum onclick="skChatPref(!this.parentNode.open)">Chat</summary>
        <div class=sk-thread id=sk-thread aria-live=polite></div></details>
    </div>
  </div>
  <div class="panel blueprint sq-set" id=sq-panel style="display:none;margin-top:16px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><h4 style="margin:0" id=sq-head>Squad</h4>
      <span><button class="btn btn-ghost btn-sm" onclick=sqLoad() title=Refresh>&#8635;</button><button class="btn btn-ghost btn-sm" onclick=sqClose() aria-label=Close>✕</button></span></div>
    <h5>Status</h5>
    <div class=sq-chks>
      <label title="Off: no member is woken, the squad is stopped"><input type=checkbox id=sq-enabled> Enabled</label>
      <label title="Messages are stored, nobody is woken"><input type=checkbox id=sq-paused> Paused</label>
      <label title="Before approval only the lead may be woken; approving a squad proposal sets it"><input type=checkbox id=sq-approved> Plan approved</label>
    </div>
    <h5>Work</h5>
    <div class=grid2>
      <div class=field><label>Level</label><select class=input id=sq-level onchange="sqLevel(this.value)"
        title="Sets Autonomy and the approval tools (Advanced); the plan and new members always need you">
        <option value=supervised>Supervised — you approve push/PR/issue, risky bash, merges and review every done ticket</option>
        <option value=merges>Approve merges — you approve push/PR/issue and every merge</option>
        <option value=auto>Auto — only risky bash commands ask you</option>
        <option value=full>Full access — nothing asks you</option>
        <option value=custom disabled>Custom (Advanced)</option></select></div>
      <div class=field><label>Daily token budget</label><input class=input type=number min=0 id=sq-daily_token_budget>
        <span class=text-muted style="font-size:12px" id=sq-usage></span></div>
      <div class="field span2 sq-chks"><label title="All tickets done: the result goes out as branch squad/&#8249;id&#8250; to the repo&#8217;s remotes"><input type=checkbox id=sq-push_when_done> Push to <code>squad/&#8249;id&#8250;</code> when all tickets are done</label></div>
    </div>
    <h5>Members</h5>
    <div id=sq-members></div>
    <details class=sq-more><summary>Code index</summary><div id=sq-index></div></details>
    <details class=sq-more><summary>Git remote (GitHub / Gitea)</summary><div id=sq-git></div></details>
    <details class=sq-more><summary>Advanced</summary>
      <div class=sq-chks>
        <label title="Members may see and load skills of category security; off: hidden, refused"><input type=checkbox id=sq-security_skills> Security skills</label>
        <label title="Wake turns run on the cheapest model that fits the ticket&#8217;s level; a failed turn runs once more one level up; Claude members stay on Claude"><input type=checkbox id=sq-cost_routing> Cost routing</label>
      </div>
      <div class=grid2>
        <div class=field><label>Autonomy</label><select class=input id=sq-autonomy onchange="sqLevelShow()">
          <option value=trusted title="Merges run by themselves; only the approval tools need you">trusted</option>
          <option value=merges title="Every merge waits for you">merges — you approve every merge</option>
          <option value=supervised title="Also every ticket a member sets done is a review for you">supervised — you review every done ticket</option></select></div>
        <div class=field><label>Tools that need your approval</label><input class="input mono" id=sq-approval_tools placeholder="git_push_branch, pr_create, issue_create, bash" spellcheck=false
          title="Comma-separated; bash = network, install, delete and push commands only. Shown in Waiting for you." oninput="sqLevelShow()"></div>
        <div class=field><label>Claude account (new members)</label><input class="input mono" id=sq-claude_account placeholder="empty = host login" spellcheck=false></div>
        <div class=field><label>Max wakes / hour · reply depth</label><span style="display:flex;gap:6px">
          <input class=input type=number min=0 id=sq-max_wakes_per_hour aria-label="Max wakes per hour"><input class=input type=number min=1 id=sq-max_depth aria-label="Max reply depth"></span></div>
      </div>
    </details>
    <div class=panel-foot><span id=sqmsg class=msg></span><button class="btn btn-primary" onclick=sqSave()>Save</button></div>
  </div>
  <aside id=sk-tkd class=kb-pane role=dialog aria-labelledby=sk-tkd-t hidden><div id=sk-tkd-box></div>
    <div class=kb-pfoot><textarea class=input id=sk-tk-c rows=2 placeholder="Comment on this ticket …" aria-label=Comment
      onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();skTkSave()}"></textarea>
      <div class=kb-fr><span>Ctrl+Enter sends <span id=sk-tk-msg class=msg></span></span><span class=mrow style="margin:0">
        <button class="btn btn-ghost btn-sm" onclick=skTkSave(1) title="Post it in the squad chat to the lead">Ask lead</button>
        <button class="btn btn-primary btn-sm" onclick=skTkSave()>Comment</button></span></div></div>
  </aside>
</section>

<section class="screen" id=s-waiting>
  <p class=lede>Approvals, questions and reviews from the squads · a gated tool call, an <code>ask_human</code> question, a ticket a supervised squad set done · your decision wakes the member</p>
  <div id=wait-list class=wait-list><span class=text-muted style="font-size:13px">…</span></div>
  <span id=wait-msg class=msg></span>
</section>

<section class="screen" id=s-projects>
  <p class=lede>The code sources: a folder or clone, its base branch and remotes · instances join with a role (lead and reader read-only, writers read-write) at <code>/project/&#8249;name&#8250;</code> · a squad's repo is run from <a href="#squads">Squads</a></p>
  <table class=table>
    <thead><tr><th style="width:18%">Project</th><th>Source</th><th>Members</th><th style="width:1%"></th></tr></thead>
    <tbody id=projrows></tbody>
  </table>
  <div class="panel blueprint" style="margin-top:32px">
    <h4 style="margin:0 0 16px" id=pj-head>New repo</h4>
    <div class=grid2>
      <div class=field><label>Name</label><input class="input mono" id=pj-name placeholder="codeflow-dev" spellcheck=false autocomplete=off></div>
      <div class=field><label>Strategy (for several writers)</label>
        <select class=input id=pj-strategy onchange=pjType()>
          <option value=shared>shared — everyone works in the one folder</option>
          <option value=worktree>worktree — a git branch + folder per writer</option>
          <option value=overlay disabled>overlay — a private layer per writer (coming)</option>
        </select></div>
      <div class=field><label>Source</label>
        <select class=input id=pj-type onchange=pjType()>
          <option value=host>📁 Host folder</option>
          <option value=katfs>katfs share</option>
        </select></div>
      <div class=field id=pj-hostbox><label>Host folder · subfolder (optional)</label>
        <div style="display:flex;gap:6px">
          <input class="input mono" id=pj-path placeholder="/home/…/repo" style="flex:2;min-width:0">
          <button type=button class="btn btn-secondary" onclick="pkOpen(document.getElementById('pj-path').value||'/home',p=>document.getElementById('pj-path').value=p)">📁</button>
          <input class="input mono" id=pj-subdir placeholder="apps/codeflow" style="flex:1;min-width:0">
        </div></div>
      <div class=field id=pj-katfsbox style="display:none"><label>katfs share</label><select class=input id=pj-share></select></div>
      <div class=field id=pj-basebox style="display:none"><label>Base branch (empty = the checked-out one)</label>
        <input class="input mono" id=pj-base placeholder="main" spellcheck=false autocomplete=off>
        <span class=text-muted style="font-size:12px">The host folder must be the top of the repo (use the subfolder field to narrow). Each writer works on <code>proj/&#8249;project&#8250;/&#8249;writer&#8250;</code>.</span></div>
      <div class="field span2"><label>Members</label>
        <div id=pj-members style="display:grid;gap:6px"></div>
        <div id=pj-memnote class=text-muted style="font-size:12.5px;display:none">The squad manages these members — change them in <a href="#squads">Squads</a>.</div>
        <div id=pj-memadd><button type=button class="btn btn-secondary btn-sm" onclick=pjAddMember()>+ Member</button>
          <span class=text-muted style="font-size:12px;margin-left:8px">lead = the planner (sees everything, read-only) · writer = changes files · reader = looks only</span></div></div>
      <div class="field span2"><label style="display:flex;gap:8px;align-items:center;text-transform:none">
        <input type=checkbox id=pj-merge> The lead may merge writers' work on its own (applies from the worktree strategy on)</label></div>
    </div>
    <div class=panel-foot><span id=pjmsg class=msg></span><button class="btn btn-secondary" id=pj-cancel style="display:none" onclick=pjReset()>Cancel</button><button class="btn btn-primary" id=pj-save onclick=saveProject()>Create project</button></div>
  </div>
</section>

<section class="screen" id=s-apps>
  <p class=lede>Front-ends under <code>/apps/&#8249;name&#8250;/</code> on top of the platform · hosted by celld, the manager as the fallback · an app&#8217;s own state in its Durable Object</p>
  <div class="panel blueprint" >
    <div style="display:flex;align-items:baseline;gap:10px"><h4 style="margin:0">Hosting</h4>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick=loadAppsPanel()>Refresh</button></div>
    <p class=text-muted style="font-size:12.5px;margin:4px 0 12px">Browser apps under <code>/apps/&#8249;name&#8250;/</code> — hosted by celld when
      <code>CELLD_URL</code> is set (the manager forwards behind its login and serves its own copy when celld is down); an app&#8217;s own state
      lives in its Durable Object there.</p>
    <div id=appsHost class=text-muted style="font-size:12.5px;margin-bottom:10px">…</div>
    <div id=appsCf class=text-muted style="font-size:12.5px;margin-bottom:10px"></div>
    <div id=appsCfList class=text-muted style="font-size:12.5px;margin:-4px 0 10px"></div>
    <table class=table><thead><tr><th>App</th><th>Served by</th><th>Server side</th><th style="width:220px">Cloudflare</th></tr></thead><tbody id=appsRows></tbody></table>
  </div>
</section>

<section class="screen" id=s-sharing>
  <p class=lede>A folder from the browser you are sitting at, handed to the agents over P2P (iroh) · nothing is mounted into the microVM</p>
  <div class="panel blueprint">
    
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
  <div class=sec-head style="margin-top:36px">
    <div><h6>App &#8596; Manager</h6><h3 style="font-size:22px">App transport (iroh)</h3></div>
    <span class="note text-muted">The Android app reaches the manager over iroh (P2P) &#183; no VPN, no exposed HTTPS port &#183; only allow-listed node-ids get through &#183; desktops use the same path via <code>kaim56-tunnel</code> (local port &#8594; iroh), so any plain-HTTP client rides the tunnel</span>
  </div>
  <div class="panel blueprint">
    
    <div class=field>
      <label>Manager node-id &#8212; paste this into the app (Settings &#8250; Server connection)</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <input class="input mono" id=irohnid readonly spellcheck=false
               placeholder="gateway not running (start the iroh-gw service)" style="flex:1;min-width:260px;width:auto">
        <button class="btn btn-secondary" onclick=irohCopy()>Copy</button>
      </div>
      <span class=text-muted id=irohhint style="font-size:12px"></span>
    </div>
    <div class=field style="margin-top:18px">
      <label>Paired phones (allow-listed node-ids)</label>
      <div id=irohallow><span class=text-muted style="font-size:13px">&#8230;</span></div>
    </div>
    <div class=grid2 style="margin-top:14px">
      <div class=field><label>Add a phone &#8212; its node-id (64 hex, shown in the app)</label>
        <input class="input mono" id=irohaddid spellcheck=false placeholder="64 hex characters"></div>
      <div class=field><label>Label (optional)</label>
        <div style="display:flex;gap:6px">
          <input class=input id=irohaddlabel placeholder="e.g. Ulrich Phone" style="flex:1">
          <button class="btn btn-primary" onclick=irohAdd()>Add</button>
        </div></div>
    </div>
    <div id=irohmsg class=msg style="margin-top:8px"></div>
  </div>

  <div class=grid2 style="margin-top:32px">
    <div class="card blueprint">
      
      <span class=card-title style="font-size:16px">How the agent reaches it</span>
      <p class=card-body>The share is not a filesystem — it is reachable only through the agent tools
      <code>remote_ls</code>, <code>remote_read(path)</code> and <code>remote_write(path, content)</code>,
      which talk to the katfs node on the host gateway. Paths are relative to the shared folder.
      Without an active share those tools answer <code>503 no browser connected</code>.</p>
    </div>
    <div class="card blueprint">
      
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
  <p class=lede>Per template/instance: which keys the host may substitute into that agent's MCP servers · <b>raw to guest</b>: which of those an agent may fetch via <code>get_secret(name)</code> — default none, since the hub and the LLM key proxy exist · values never written to disk</p>
  <div class="banner blueprint" style="background:var(--color-neutral-100);margin:16px 0 20px;padding:9px 14px">
    
    <svg width=15 height=15 viewBox="0 0 24 24" fill=none stroke="var(--color-neutral-700)" stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>
    <span style="color:var(--color-neutral-800);font-size:12.5px">Keep dangerous keys (Docker / network admin) unchecked.</span>
  </div>
  <div id=secrets></div>
  <div class=panel-foot style="border:none;padding-top:0"><span id=secmsg class=msg></span><button class="btn btn-primary" onclick=saveSecrets()>Save access</button></div>
</section>
"""

HTML_BOTTOM = """

<section class="screen" id=s-settings>
  <p class=lede>Shared and persisted · values apply to all new instances; empty template fields are pre-filled from here.</p>
  <div class="panel blueprint">
    
    <div class="banner blueprint" style="margin:0 0 16px;padding:9px 14px"><span id=voicestat class=text-muted style="font-size:12.5px">Voice: …</span></div>
    <div class="banner blueprint" style="margin:0 0 16px;padding:9px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <span id=updtxt class=text-muted style="font-size:12.5px">Version: …</span>
      <button class="btn btn-secondary btn-sm" id=updbtn hidden style="margin-left:auto" onclick=runUpdate()>Update</button>
    </div>
    <pre id=updlog hidden class=mono style="font-size:11.5px;max-height:180px;overflow:auto;margin:-8px 0 16px;padding:8px 12px;border:1px solid var(--color-divider);white-space:pre-wrap"></pre>
    <div class=setgrid id=settings></div>
    <div class=panel-foot><span id=setmsg class=msg></span><button class="btn btn-secondary" title="One sample setup suggestion with the saved decision backend" onclick=decisionTest()>Test decision backend</button><button class="btn btn-primary" onclick=saveSettings()>Save</button></div>
  </div>
  <div class=setgrid style="margin-top:18px;align-items:start">
  <div class="panel blueprint" id=capanel>
    <h6 style="margin:0 0 10px">Claude accounts</h6>
    <p class=text-muted style="font-size:12.5px">Create a token per account with <code>claude setup-token</code>. For the usage gauges, optionally paste the account&#8217;s <code>~/.claude/.credentials.json</code> from a login made just for kAIm56 (e.g. <code>CLAUDE_CONFIG_DIR=/tmp/k claude login</code>) &#8212; the manager then owns and refreshes it. An instance picks its account with <code>CLAUDE_ACCOUNT</code>; <code>default</code> is the host login (read only).</p>
    <div class=stack id=calist></div>
    <div class=grid2 style="margin-top:12px">
      <div class=field><label>Name</label><input class="input mono" id=ca-name placeholder="work" maxlength=32 spellcheck=false autocomplete=off></div>
      <div class=field><label>Label</label><input class=input id=ca-label maxlength=64 autocomplete=off></div>
      <div class="field span2"><label>Token (claude setup-token)</label><input class="input mono" type=password id=ca-token autocomplete=new-password spellcheck=false placeholder="empty on update = keep the stored one"></div>
      <div class="field span2"><label>Credentials JSON for usage (optional)</label><textarea class="input mono" id=ca-creds rows=3 spellcheck=false autocomplete=off></textarea></div>
    </div>
    <div class=panel-foot><span id=camsg class=msg></span><button class="btn btn-primary" onclick=caSave()>Save account</button></div>
  </div>
  <div class="panel blueprint" id=sesspanel hidden>
    <h6 style="margin:0 0 10px">Sessions (OIDC logins)</h6>
    <div class=stack id=sesslist></div>
  </div>
  </div>
</section>

<footer class=appfoot>
  <span class=af-brand>kAIm56</span>
  <span class=af-stat>NAT via __HOSTIF__</span>
  <span class=af-stat>Pool __POOL__</span>
  <span class=af-stat id=spend></span>
  <span class=af-stat id=version></span>
  __CODE_LINK__
</footer>
</main>
</div>

<!-- squad feedback (mgr/squadnotes.py): context menu, floating button (touch), comment popover -->
<button id=sqa-fab class="btn btn-secondary btn-sm" hidden aria-label="Comment on the selected text" onmousedown="event.preventDefault()" onclick="sqPop()">&#128172; Comment</button>
<div id=sqa-menu class=sqa-menu role=menu hidden><button class="btn btn-ghost btn-sm" role=menuitem onmousedown="event.preventDefault()" onclick="sqPop()">Comment</button></div>
<div id=sqa-pop class=sqa-pop role=dialog aria-label="Comment on a squad message" hidden>
  <div id=sqa-q class=sqa-q></div>
  <div id=sqa-view></div>
  <div id=sqa-edit class=sqa-edit>
    <textarea id=sqa-text class=input rows=4 maxlength=4000 aria-label="Your comment" placeholder="What should change here?"></textarea>
    <div class=sqa-acts><button class="btn btn-secondary btn-sm" onclick="sqNoteSave(false)">Save draft</button>
      <button class="btn btn-primary btn-sm" onclick="sqNoteSave(true)">Send now</button>
      <button class="btn btn-ghost btn-sm" onclick="sqPopClose()">Cancel</button></div>
  </div>
  <div id=sqa-msg class=text-muted role=status style="font-size:12px"></div>
</div>

<div id=picker class=dialog-backdrop style="display:none;z-index:60">
  <div class="dialog blueprint" style="width:min(560px,100%)">
    
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

<div id=midlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)midlgClose()">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    
    <div class=dialog-title>Edit mission &#8212; <span id=midlgid class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>Any status. Steps are matched by position: existing ones keep their status and result, new lines start open, removed lines are dropped. Setting the status back to <b>active</b> reopens the mission for its owner.</div>
    <div class=field><label>Goal</label><input class=input id=migoal maxlength=300></div>
    <div class=field><label>Steps (one per line)</label><textarea class=input id=misteps rows=8 style="font-family:var(--font-mono);font-size:12.5px"></textarea></div>
    <div class=field><label>Status</label><select class=input id=mistatus><option value=active>active</option><option value=paused>paused</option><option value=done>done</option><option value=failed>failed</option></select></div>
    <div id=mihint class=text-muted style="font-size:12px"></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=midlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=missionSave()>Save</button>
    </div>
  </div>
</div>
<div id=cfdlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)cfdlgClose()">
  <div class="dialog blueprint" style="width:min(820px,100%)">
    <div class=dialog-title>Config &#8212; <span id=cfdlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>The instance's environment. Most keys apply after a restart, a few (budget, rate, delegation, katfs, router, apps) at once. API keys and passwords are not here &#8212; they live in the Secrets tab.</div>
    <div id=cfdlgrows class=cf-rows></div>
    <div><button type=button class="btn btn-secondary btn-sm" onclick="cfAddRow('','',true)">+ Key</button></div>
    <div class=dialog-body id=cfdlgmsg style="margin-top:10px"></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=cfdlgClose()>Close</button>
      <button class="btn btn-secondary" id=cfdlgrestart style="display:none">Restart now</button>
      <button class="btn btn-primary" onclick=saveConfig()>Save</button>
    </div>
  </div>
</div>

<div id=mdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    
    <div class=dialog-title>Folders — <span id=mdlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body><b>Host folders</b>: host path → guest path, "ro" = read-only. Saved changes are picked up by the reconciler in the running guest.</div>
    <div id=mdlgrows></div>
    <div><button type=button class="btn btn-secondary btn-sm" onclick="addMount(null,'mdlgrows')">+ Folder</button></div>
    <div class=dialog-body style="margin-top:14px"><b>katfs share</b>: a folder shared from a browser or <code>katfs-share</code> on any machine;
      the agent reads and writes it with <code>remote_ls</code> / <code>remote_read</code> / <code>remote_write</code>. Applies at once, also while the instance runs.</div>
    <select class=input id=mdlgkatfs></select>
    <div class=dialog-body id=mdlgproj style="margin-top:14px;display:none"></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=mdlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveMounts()>Save</button>
    </div>
  </div>
</div>

<div id=pjdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(980px,100%)">
    <div class=dialog-title>Changes — <span id=pjdlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body id=pjdlgsum>…</div>
    <pre id=pjdlgdiff class=mono style="max-height:52vh;overflow:auto;font-size:12px;line-height:1.45;margin:0;padding:10px;background:var(--color-neutral-100);white-space:pre"></pre>
    <div class=dialog-actions>
      <span id=pjdlgmsg class=msg style="margin-right:auto"></span>
      <button class="btn btn-secondary" onclick=pjDlgClose()>Close</button>
      <button class="btn btn-secondary" onclick=pjDiscard()>Discard</button>
      <button class="btn btn-primary" onclick=pjMerge()>Merge into base</button>
    </div>
  </div>
</div>

<div id=tcdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(860px,100%)">
    <div class=dialog-title>Toolchain — <span id=tcdlgname class=mono style="font-size:16px"></span></div>
    <div id=tcdlg-edit class=dialog-body>
      <p class=text-muted style="font-size:12.5px;margin:0 0 8px">One read-only layer for every member, built in a throwaway VM (no secrets, no mounts) — saving starts the build; members use it after Restart members.</p>
      <label for=tc-apt>apt packages <span class=text-muted>(space separated)</span></label><input class="input mono" id=tc-apt placeholder="golang-go gcc libgl1-mesa-dev">
      <label for=tc-script style="margin-top:8px;display:block">Script <span class=text-muted>(root, after apt; at most 8 KB)</span></label><textarea class="input mono" id=tc-script rows=10 placeholder="curl -fsSL https://go.dev/dl/go1.23.4.linux-amd64.tar.gz | tar -C /usr/local -xz"></textarea>
      <label for=tc-env style="margin-top:8px;display:block">Env <span class=text-muted>(NAME=value per line; $NAME expands)</span></label><textarea class="input mono" id=tc-env rows=3 placeholder="PATH=/usr/local/go/bin:$PATH"></textarea>
    </div>
    <pre id=tcdlglog class=mono hidden style="max-height:60vh;overflow:auto;font-size:12px;line-height:1.45;margin:0;padding:10px;background:var(--color-neutral-100);white-space:pre-wrap"></pre>
    <div class=dialog-actions>
      <span id=tcdlgmsg class=msg style="margin-right:auto"></span>
      <button class="btn btn-secondary" onclick=tcClose()>Close</button>
      <button class="btn btn-primary" id=tc-save onclick=tcSave()>Save &amp; build</button>
    </div>
  </div>
</div>

<div id=dpdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(860px,100%)">
    <div class=dialog-title>Deploy — <span id=dpdlgname class=mono style="font-size:16px"></span></div>
    <div id=dpdlg-edit class=dialog-body>
      <p class=text-muted style="font-size:12.5px;margin:0 0 8px">The base branch runs in its own VM (the squad's base image + toolchain, only the secrets set here, no host access), at <span class=mono>/d/&lt;project&gt;/</span> on the apps' host. Env: <span class=mono>PORT</span>, <span class=mono>DATA_DIR=/data</span> (kept across deploys), <span class=mono>BASE_PATH</span>.</p>
      <p class=text-muted style="font-size:12.5px;margin:0 0 8px">Build, Start and Port come from <span class=mono>deploy.json</span> in the base branch (the squad writes it); fill them here only for a project without one.</p>
      <label for=dp-build>Build <span class=text-muted>(optional, in the release folder, as uid 1000)</span></label><input class="input mono" id=dp-build placeholder="npm ci && npm run build">
      <label for=dp-start style="margin-top:8px;display:block">Start <span class=text-muted>(listens on $PORT)</span></label><input class="input mono" id=dp-start placeholder="node server.js">
      <div style="display:flex;gap:8px;margin-top:8px">
        <label>Port <input class="input mono" id=dp-port type=number min=1024 max=65535 style="width:100px"></label>
        <label>RAM MiB <input class="input mono" id=dp-mem type=number min=256 max=8192 style="width:100px"></label>
        <label>vCPUs <input class="input mono" id=dp-vcpus type=number min=1 max=4 style="width:70px"></label>
      </div>
      <label for=dp-sec style="margin-top:8px;display:block">Secrets <span class=text-muted>(env of build and start, one <span class=mono>NAME=value</span> per line; <span class=mono>NAME=</span> removes; a value is never shown again)</span></label><div style="font-size:13px;margin:2px 0 6px">Stored: <span id=dp-secnames class=mono>none</span></div><textarea class="input mono" id=dp-sec rows=3 autocomplete=off spellcheck=false placeholder="NEW_NAME=value"></textarea>
    </div>
    <pre id=dpdlglog class=mono hidden style="max-height:60vh;overflow:auto;font-size:12px;line-height:1.45;margin:0;padding:10px;background:var(--color-neutral-100);white-space:pre-wrap"></pre>
    <div class=dialog-actions>
      <span id=dpdlgmsg class=msg style="margin-right:auto"></span>
      <button class="btn btn-secondary" onclick=dpClose()>Close</button>
      <button class="btn btn-primary" id=dp-save onclick=dpSave()>Save</button>
    </div>
  </div>
</div>

<div id=modeldlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(560px,100%)">
    
    <div class=dialog-title>Model — <span id=modeldlgname class=mono style="font-size:16px"></span></div>
    <div class=dialog-body>The new model takes effect after the next stop/start of the instance.</div>
    <div id=modeldlgbox></div>
    <div class=dialog-actions>
      <button class="btn btn-secondary" onclick=modelDlgClose()>Cancel</button>
      <button class="btn btn-primary" onclick=saveModel()>Save</button>
    </div>
  </div>
</div>

<div id=poldlg class=dialog-backdrop style="display:none" onclick="if(event.target===this)polClose()">
  <div class="dialog blueprint" role=dialog aria-modal=true aria-label="Policy" style="width:min(960px,100%)">
    <div style="display:flex;align-items:center;gap:10px"><div class=dialog-title>Policy</div>
      <span class=text-muted style="font-size:12px">network, tools, secrets, MCP, limits</span>
      <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick=polClose() aria-label=Close>✕</button></div>
    <div id=policycards></div>
  </div>
</div>

<div id=actdlg class=dialog-backdrop style="display:none">
  <div class="dialog blueprint" style="width:min(720px,100%)">
    
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

"""

HTML_TOP = HTML_TOP.replace("__TEAM_DEFAULT__", html.escape(_squads.TEAM_DEFAULT))   # New squad's Team box
