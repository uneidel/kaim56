# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI — the client script."""

JS = """<script>
const TEMPLATES=__TPLJSON__;
const SETTINGS=__SETTINGS__;
const SETTINGS_SCHEMA=__SETTINGS_SCHEMA__;
const PERSONAS=__PERSONAS__;
const SKILLS=__SKILLS__;
const SK_CATS=__SKCATLIST__;
const INDEX_HOST=__INDEX_HOST__;   /* the code dashboard's own origin (site INDEX_HOST); '' = no Dashboard link */
const APPS_HOST=__APPS_HOST__;     /* the apps' own origin (site APPS_HOST); '' = apps under this host */
const CORNERS='';   // registration corners retired (Ulrich, 2026-09-12)
const I_EDIT='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path></svg>';
const I_RUN='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><polygon points="6 3 20 12 6 21 6 3"></polygon></svg>';
const I_DEL='<svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.5 stroke-linecap=round stroke-linejoin=round><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;')}
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

/* — skills of the new instance (config SKILLS) and Jev's suggestion (/api/setup/suggest) — */
let SK_SCORE={};
function renderSkillPick(){
  const box=document.getElementById('skillpick'); if(!box)return;
  const on=new Set([...box.querySelectorAll('.skillcb:checked')].map(c=>c.value).concat(Object.keys(SK_SCORE)));
  const list=SKILLS.filter(s=>!s.default).sort((a,b)=>(SK_SCORE[b.name]||0)-(SK_SCORE[a.name]||0));
  box.innerHTML=list.map(s=>`<label class=radio style="font-size:13px"><input type=checkbox class=skillcb value="${esc(s.name)}"${on.has(s.name)?' checked':''}>`+
    `<span class=dot></span><span title="${esc(s.description)}"><span class=mono style="font-size:12px">${escT(s.name)}</span>`+
    (SK_SCORE[s.name]?` <span class="tag tag-accent">${SK_SCORE[s.name].toFixed(2)}</span>`:'')+`</span></label>`).join('')
    ||'<span class=text-muted style="font-size:12px">no skills in the catalog besides the standard ones</span>';
}
function setParam(k,v){
  const el=document.querySelector('#params [data-k="'+k+'"]'); if(!el)return false;
  if(el.tagName==='SELECT'&&![...el.options].some(o=>o.value===v))
    el.insertAdjacentHTML('afterbegin',`<option value="${esc(v)}">${escT(v)}</option>`);
  el.value=v; return true;
}
async function jevSuggest(){
  const purpose=document.getElementById('purpose').value.trim(), msg=document.getElementById('suggestmsg'), btn=document.getElementById('suggestbtn');
  if(!purpose){msg.textContent='Describe the purpose first.';return}
  btn.disabled=true; msg.textContent='asking Jev… (can take half a minute)';
  let d={}; try{d=await (await fetch('/api/setup/suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose})})).json()}catch(e){d={note:'manager not reachable'}}
  btn.disabled=false;
  const f=v=>(+v).toFixed(2), said=[], sel=document.getElementById('persona');
  if(d.persona&&[...sel.options].some(o=>o.value===d.persona.name)){sel.value=d.persona.name;said.push('persona '+d.persona.name+' '+f(d.persona.score))}
  if(d.tier)said.push('tier '+d.tier.name+' '+f(d.tier.score)+': '+(!d.model?'no model has this tier (Router)':
    setParam(d.model_key,d.model)?d.model:d.model+' — not set, this template has no '+d.model_key));
  SK_SCORE={}; (d.skills||[]).forEach(s=>SK_SCORE[s.name]=s.score);
  renderSkillPick();
  if((d.skills||[]).length)said.push(d.skills.length+' skill(s), ticked below');
  msg.textContent=(said.length?'Jev suggests: '+said.join(' · '):'No suggestion. '+(d.note||''))+(d.ms?' ('+(d.ms/1000).toFixed(1)+' s)':'');
}

/* — Policy: what each instance may do (net/tools/secrets/MCP) + what it does (audit) — */
function _b64(buf){let str='',a=new Uint8Array(buf);for(let i=0;i<a.length;i+=0x8000)str+=String.fromCharCode.apply(null,a.subarray(i,i+0x8000));return btoa(str);}
async function loadPlugins(){
  let ps=[]; try{ps=(await (await fetch('/api/plugins')).json()).plugins||[];}catch(e){return;}
  const el=document.getElementById('pluglist'); if(!el)return;
  el.innerHTML = ps.length ? ps.map(p=>
    `<div class=card><div style="display:flex;justify-content:space-between;align-items:center;gap:8px">`+
    `<b>${escT(p.name)}</b><button class="btn btn-ghost" style="font-size:12px" onclick="plugDel('${esc(p.name)}')">L&#246;schen</button></div>`+
    `<div class=text-muted style="font-size:12px">${escT(p.kind)} &#183; ${p.files.length} file(s)</div>`+
    `<div class=mono style="font-size:11px;color:var(--color-neutral-600);word-break:break-all">${p.files.map(f=>(p.links||{})[f]?`<a href="${esc(p.links[f])}" target="_blank" rel="noopener noreferrer" title="open in VS Code">${escT(f)}</a>`:escT(f)).join(', ')}</div>`+
    `<div style="display:flex;align-items:center;gap:8px;margin-top:8px">`+
      (p.modified
        ? `<span class="tag" style="background:#c0392b;color:#fff;font-size:11px">\u26a0 changed since Approve \u2014 not delivered</span>`
        : p.pinned
          ? `<span class="tag tag-accent" style="font-size:11px">\u2713 approved ${escT(p.sha||'')} \u2014 delivered</span>`
          : `<span class="tag tag-neutral" style="font-size:11px">not approved \u2014 not delivered</span>`)+
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
  if(pc)pc.addEventListener('change',e=>{const ds=e.target&&e.target.dataset;const pt=ds&&(ds.pt||ds.pm);if(pt)POL_DIRTY.add(pt);});
});
async function loadPolicy(auto){
  if(auto&&POL_DIRTY.size)return;   // do not overwrite unsaved checkboxes
  let pol={instances:[]};
  try{
    pol=await (await fetch('/api/policy')).json();
    if(!POL_TOOLS.length)POL_TOOLS=(await (await fetch('/api/agent-tools')).json()).tools||[];
    if(!MCPS.length){try{MCPS=await (await fetch('/api/mcps')).json();}catch(e){}}
  }catch(e){document.getElementById('policycards').innerHTML='<span class=text-muted>not reachable</span>';return;}
  const tag=(on,y,n)=>`<span class="tag ${on?'tag-accent':'tag-neutral'}">${on?y:n}</span>`;
  document.getElementById('policycards').innerHTML=(pol.instances||[]).map(p=>{
    const toolset=new Set(p.tools||[]);
    const tools=POL_TOOLS.map(t=>
      `<label class=radio style="font-size:12.5px"><input type=checkbox data-pt="${esc(p.name)}" value="${esc(t.name)}" ${p.tools_all||toolset.has(t.name)?'checked':''}>`+
      `<span class=dot></span><span class=mono style="font-size:11.5px" title="${esc(t.desc)}">${escT(t.name)}</span>`+
      (t.plugin?`<span class=text-muted style="font-size:10.5px;margin-left:4px">plugin</span>`:'')+`</label>`).join('');
    const secrets=(p.secrets||[]).map(s=>`<span class="tag tag-neutral" style="font-size:11px">${escT(s)}</span>`).join(' ')||'<span class=text-muted style="font-size:12px">none</span>';
    /* MCP: assign from the catalog right here. A server that needs ${SECRET}
       placeholders shows them — released ones as a key, missing ones as a
       warning (release them in the Secrets tab, or the config stays a
       placeholder and the agent reports it as unresolved). */
    const assigned=new Set(p.mcps||[]), rel=new Set(p.secrets||[]);
    const mcps=(MCPS||[]).map(m=>{
      const need=[...new Set(JSON.stringify(m).split('${').slice(1).map(s=>s.split('}')[0]))];
      const hint=need.length?` <span class=text-muted style="font-size:11px" title="secrets this server needs — released in the Secrets tab?">${need.map(k=>(rel.has(k)?'🔑 ':'⚠️ ')+escT(k)).join(' ')}</span>`:'';
      return `<label class=radio style="font-size:12.5px"><input type=checkbox data-pm="${esc(p.name)}" value="${esc(m.name)}" ${assigned.has(m.name)?'checked':''}>`+
        `<span class=dot></span><span class=mono style="font-size:11.5px" title="${esc(m.description||'')}">${escT(m.name)}</span>${hint}</label>`;
    }).join('')||'<span class=text-muted style="font-size:12px">catalog empty — add servers in the MCP tab</span>';
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
      `<div class=kv><b>MCP</b><span style="display:flex;flex-wrap:wrap;gap:2px 14px;align-items:center">${mcps}`+
        `<button class="btn btn-primary btn-sm" onclick="savePolMcps('${esc(p.name)}')">Save MCP</button><span class=msg data-mmsg="${esc(p.name)}"></span></span></div>`+
      `<div class=kv><b>Auto-reset</b><span style="display:flex;gap:8px;align-items:center;font-size:12.5px"><input class="input mono" type=number min=0 step=5 data-ar="${esc(p.name)}" value="${esc(p.auto_reset||'0')}" style="width:80px;font-size:12.5px">`+
        `<span class=text-muted>min idle, then a fresh context (0 = never) · applies after a restart</span>`+
        `<button class="btn btn-secondary btn-sm" onclick="savePolAutoReset('${esc(p.name)}')">Save</button><span class=msg data-armsg="${esc(p.name)}"></span></span></div>`+
      polBudgetRow(p)+polCtxRow(p)+polRouterRow(p)+polAppsRow(p)+
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
function savePolAutoReset(name){
  const v=String(Math.max(0,parseInt(document.querySelector(`input[data-ar="${CSS.escape(name)}"]`).value||'0',10)));
  fetch(`/api/instances/${name}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'AUTO_RESET_MIN',value:v})}).then(async r=>{
      let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-armsg="${CSS.escape(name)}"]`);
      if(el)el.textContent=r.ok?(d.msg||'saved')+' ✓':'⚠️ '+(d.msg||('HTTP '+r.status));
    });
}
function savePolMcps(name){
  const list=[...document.querySelectorAll(`input[data-pm="${CSS.escape(name)}"]:checked`)].map(c=>c.value);
  fetch(`/api/instances/${name}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'MCP_SERVERS',value:list.join(',')})}).then(async r=>{
      let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-mmsg="${CSS.escape(name)}"]`); if(!el)return;
      const m=d.msg||('HTTP '+r.status), bad=!r.ok||/error/.test(m);
      if(!bad)POL_DIRTY.delete(name);
      el.innerHTML=(bad?'⚠️ ':'✓ ')+escT(m)+(/applies after/.test(m)?` <button class="btn btn-secondary btn-sm" onclick="act('${esc(name)}','restart')">Restart now</button>`:'');
    }).catch(e=>{const el=document.querySelector(`[data-mmsg="${CSS.escape(name)}"]`);if(el)el.textContent='⚠️ '+e;});
}
/* Daily token budget per instance: BUDGET_TOKENS, enforced at the key proxy and
   read per request — a change applies at once, no restart. Shown in millions. */
const _mtok=n=>(n/1e6).toLocaleString(undefined,{maximumFractionDigits:2})+'M';
/* the cost rule: what rides along on every LLM call, and what that costs per day */
const CTX_LBL={system:'system prompt',tools:'tool schemas',playbooks:'playbooks',memory_index:'MEMORY.md',
  missions:'missions',now:'clock',recall:'memory recall',summary:'summary',other:'other'};
function polCtxRow(p){
  const c=p.context||{}; if(!c.fixed)return '';
  const parts=Object.entries(c.parts||{}).filter(([k,v])=>v>0).sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<span class="tag tag-neutral">${escT(CTX_LBL[k]||k)} ~${fmtTok(v)}</span>`).join(' ');
  return `<div class=kv><b>Fixed context</b><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12.5px">`+
    `<span class=mono>~${fmtTok(c.fixed)} per call</span>${parts}`+
    `<span class=text-muted>× ${c.calls_24h} calls in 24 h ≈ ${fmtTok(c.per_day)} tokens/day before caching</span></span></div>`;
}
function polBudgetRow(p){
  const b=+p.budget||0, u=+p.used_today||0, pct=b?Math.min(100,100*u/b):0;
  const col=!b?'var(--color-neutral-400,#9ca3af)':pct>=100?'var(--color-danger,#c0392b)':pct>=80?'var(--color-warning,#d97706)':'var(--color-success,#16a34a)';
  return `<div class=kv><b>Token budget</b><span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px">`+
    `<input class="input mono" type=number min=0 step=0.5 data-bud="${esc(p.name)}" value="${b?+(b/1e6).toFixed(2):0}" style="width:90px;font-size:12.5px">`+
    `<span class=text-muted>M tokens/day (0 = no limit)</span>`+
    `<span style="display:inline-flex;align-items:center;gap:6px;min-width:190px">${_bar(u,b||Math.max(u,1),col)}`+
      `<span class=mono style="font-size:12px;white-space:nowrap">${_mtok(u)} used today${b?` · ${Math.round(pct)}%`:''}</span></span>`+
    `<span class=text-muted>${p.budget_default?'default':'custom'} · applies at once</span>`+
    `<button class="btn btn-secondary btn-sm" onclick="savePolBudget('${esc(p.name)}')">Save</button>`+
    (p.budget_default?'':`<button class="btn btn-ghost btn-sm" title="back to the default of ${_mtok(+p.budget_default_value||0)}" onclick="savePolBudget('${esc(p.name)}',true)">Default</button>`)+
    `<span class=msg data-budmsg="${esc(p.name)}"></span></span></div>`;
}
/* Settings → Apps: who serves each app (celld or the manager's fallback), and its server side */
async function loadAppsPanel(){
  let d; try{d=await (await fetch('/api/apps')).json()}catch(e){document.getElementById('appsHost').textContent='manager not reachable';return}
  const c=d.celld||{};
  const dot=(ok,txt)=>`<span class="tag ${ok?'tag-accent':'tag-neutral'}">${escT(txt)}</span>`;
  document.getElementById('appsHost').innerHTML=!c.configured
    ?`${dot(false,'manager')} celld is not set up — the manager serves the apps itself (set <code>CELLD_URL</code> above, e.g. http://127.0.0.1:9876).`
    :c.reachable?`${dot(true,'celld reachable')} <code>${escT(c.url)}</code> · ${c.ms} ms`
    :`${dot(false,'celld down')} <code>${escT(c.url)}</code> — the manager serves its own copy; apps with a server side cannot save state (${escT(c.error||'')})`;
  const cf=d.cloudflare||{};
  document.getElementById('appsCf').innerHTML=cf.configured
    ?'Cloudflare is set up — <b>Upload</b> sends an app (files + its state) there, <b>Restore</b> brings it back to celld; the worker stays deployed.'
    :'Cloudflare: fill in <code>CF_ACCOUNT_ID</code>, <code>CF_API_TOKEN</code>, <code>CF_APP_SECRET</code> in <a href="#settings">Settings</a>';
  const apps=d.apps||[];
  let busy=false;
  document.getElementById('appsRows').innerHTML=apps.length?apps.map(a=>{
    const ss=a.server_side, cl=a.cloud||{}, j=a.job||{}, run=j.done===false;
    busy=busy||run;
    const served=a.served_by==='cloudflare'?dot(true,'Active · cloud'):a.served_by==='celld'?dot(true,'celld'):dot(false,c.configured?'manager (fallback)':'manager');
    const side=cl.where==='cloud'?'<span class=text-muted>on Cloudflare</span>'
      :ss?(ss.error?'⚠️ '+escT(ss.error):escT(ss.text||''))
      :a.server?'⚠️ unreachable — this app keeps its state on celld':'<span class=text-muted>none — files only</span>';
    const btn=run?`<span class=text-muted>${escT(j.op)}: ${escT(j.step)}…</span>`
      :cl.where==='cloud'?`<button class="btn btn-secondary btn-sm" onclick="appCloud('${esc(a.name)}','restore')">Restore to celld</button>`
      :`<button class="btn btn-primary btn-sm" ${cf.configured?'':'disabled'} onclick="appCloud('${esc(a.name)}','upload')">Upload to Cloudflare</button>`;
    const last=!run&&j.step==='failed'?`<div style="font-size:11.5px;color:var(--color-danger,#c0392b)">${escT(j.op)} failed: ${escT(j.error)}</div>`
      :!run&&j.step==='done'?`<div class=text-muted style="font-size:11.5px">${escT(j.op)} done</div>`:'';
    const link=cl.where==='cloud'&&cl.url?`<a href="${esc(cl.url)}/" target=_blank rel=noopener>${escT(a.icon||'')} ${escT(a.title||a.name)}</a>`
      :`<a href="${APPS_HOST?'https://'+APPS_HOST:''}/apps/${encodeURIComponent(a.name)}/" target=_blank rel=noopener>${escT(a.icon||'')} ${escT(a.title||a.name)}</a>`;
    const cfLink=cl.url?` · <a href="${esc(cl.url)}/" target=_blank rel=noopener title="open on Cloudflare (new tab)">${escT(cl.url.replace(/^https:\/\//,''))} ↗</a>`:'';
    const noRen=cl.where==='cloud'?'runs on Cloudflare — restore it first':a.server?'has a server side on celld, which knows it by name':'';
    const edit=run?'':`<div style="display:flex;gap:6px;margin-top:6px">`+
      `<button class="btn btn-secondary btn-sm" ${noRen?`disabled title="${esc(noRen)}"`:''} onclick="appRename('${esc(a.name)}')">Rename</button>`+
      `<button class="btn btn-secondary btn-sm" style="color:var(--color-danger,#c0392b)" onclick="appDelete('${esc(a.name)}',${cl.where==='cloud'},${!!a.server})">Delete</button></div>`;
    return `<tr><td>${link}<div class=text-muted style="font-size:11.5px">${escT(a.name)}${cfLink}</div></td>`+
      `<td>${served}</td><td style="font-size:12.5px">${side}</td><td>${btn}${last}${edit}</td></tr>`}).join('')
    :'<tr><td colspan=4 class=text-muted>No apps (a folder with app.json + index.html under apps/).</td></tr>';
  clearTimeout(window.APPS_POLL);
  if(busy)window.APPS_POLL=setTimeout(loadAppsPanel,3000);            // follow a running upload/restore
  if(cf.configured)loadCfWorkers(apps.length);
}
/* every Worker on the Cloudflare account — also those created outside the manager */
async function loadCfWorkers(nLocal,refresh){
  let d; try{d=await (await fetch('/api/apps/cloudflare'+(refresh?'?refresh=1':''))).json()}catch(e){return}
  const ws=d.workers||[], ext=ws.filter(w=>!w.app), info=document.getElementById('appsCfList');
  if(info)info.innerHTML=d.error?`⚠️ Cloudflare list: ${escT(d.error)}`
    :`On Cloudflare: ${ws.length} worker${ws.length===1?'':'s'} — ${ws.length-ext.length} from here, ${ext.length} created elsewhere · <a href="#" onclick="loadCfWorkers(0,1);return false">refresh</a>`;
  const tb=document.getElementById('appsRows');
  tb.querySelectorAll('tr[data-cfext]').forEach(r=>r.remove());
  if(!nLocal&&ext.length)tb.innerHTML='';
  const day=t=>t?new Date(t).toLocaleString():'';
  const dash=n=>d.account?` · <a href="https://dash.cloudflare.com/${encodeURIComponent(d.account)}/workers/services/view/${encodeURIComponent(n)}/production" target=_blank rel=noopener>dashboard ↗</a>`:'';
  tb.insertAdjacentHTML('beforeend',ext.map(w=>
    `<tr data-cfext=1><td>${w.url?`<a href="${esc(w.url)}/" target=_blank rel=noopener>${escT(w.name)}</a>`:escT(w.name)}`+
    `<div class=text-muted style="font-size:11.5px">${w.url?`<a href="${esc(w.url)}/" target=_blank rel=noopener>${escT(w.url.replace(/^https:\/\//,''))} ↗</a>`:'no workers.dev URL'}</div></td>`+
    `<td><span class="tag tag-accent">Cloudflare only</span></td>`+
    `<td style="font-size:12.5px"><span class=text-muted>created outside the manager${w.assets?' · static files':''} · updated ${escT(day(w.modified))}</span></td>`+
    `<td style="font-size:12.5px"><span class=text-muted>not managed here</span>${dash(w.name)}`+
    `<div style="display:flex;gap:6px;margin-top:6px"><button class="btn btn-secondary btn-sm" title="Download its Durable Objects as JSON (cfdo workers)" onclick="cfWorkerExport('${esc(w.name)}')">Download data</button>`+
    `<button class="btn btn-secondary btn-sm" disabled title="Cloudflare cannot rename a worker, and its static files cannot be downloaded to redeploy it under a new name">Rename</button>`+
    `<button class="btn btn-secondary btn-sm" style="color:var(--color-danger,#c0392b)" onclick="cfWorkerDelete('${esc(w.name)}')">Delete</button></div></td></tr>`).join(''));
}
async function appPost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  let d={};try{d=await r.json()}catch(e){}
  if(!r.ok||!d.ok){alert(d.error||('HTTP '+r.status));return null}
  return d;
}
async function appRename(name){
  const to=(prompt(`New name for ${name} (a-z, 0-9, - and _). Its address becomes /apps/<new name>/.`,name)||'').trim();
  if(!to||to===name)return;
  const d=await appPost('/api/apps/'+encodeURIComponent(name)+'/rename',{to});
  if(d&&d.old_worker&&d.old_worker!=='none'&&d.old_worker!=='not set up')alert(`Renamed. The old Cloudflare worker: ${d.old_worker}`);
  loadAppsPanel();
}
async function appDelete(name,inCloud,server){
  const what=`Delete the app ${name}?\\n\\n- its folder moves to apps/.trash/ (can be moved back)\\n- its Cloudflare worker is deleted`+
    (inCloud?' — the app runs THERE, its state is lost':'')+(server&&!inCloud?'\\n- its state on celld stays':'')+`\\n\\nType the name to confirm:`;
  if((prompt(what)||'').trim()!==name)return;
  const d=await appPost('/api/apps/'+encodeURIComponent(name)+'/delete');
  if(d)alert(`Deleted. Folder: ${d.trashed}\\nCloudflare worker: ${d.worker}`);
  loadAppsPanel();
}
async function cfWorkerExport(name){
  // the worker's own CFDO_SECRET (Cloudflare never returns it); used once, not stored
  const secret=prompt(`CFDO_SECRET of ${name} (empty = the default secret from Settings):`,'');
  if(secret===null)return;
  const d=await appPost('/api/cfworkers/'+encodeURIComponent(name)+'/export',{secret});
  if(!d)return;
  delete d.ok;
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,1)],{type:'application/json'}));
  a.download=`${name}-durable-objects-${new Date().toISOString().slice(0,10)}.json`;
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),5000);
  alert(`${d.objects.length} object${d.objects.length===1?'':'s'} of ${name} downloaded.`);
}
async function cfWorkerDelete(name){
  if((prompt(`Delete the Cloudflare worker ${name}? Its Durable Object data goes with it — this cannot be undone.\\n\\nType the name to confirm:`)||'').trim()!==name)return;
  if(await appPost('/api/cfworkers/'+encodeURIComponent(name)+'/delete'))loadCfWorkers(0,1);
}
async function appCloud(name,op){
  if(op==='upload'&&!confirm(`Send ${name} to Cloudflare? Its files and its state move there; here it redirects to the cloud until you restore it.`))return;
  if(op==='restore'&&!confirm(`Bring ${name} back to celld? Its state is copied from Cloudflare; the worker stays deployed there.`))return;
  const r=await fetch('/api/apps/'+encodeURIComponent(name)+'/'+op,{method:'POST'});
  let d={};try{d=await r.json()}catch(e){}
  if(!r.ok)alert(d.error||('HTTP '+r.status));
  loadAppsPanel();
}

/* model router: a policy per instance, off by default (mgr/router.py) */
/* apps + cloudflare tools: what this instance may do with the apps (mgr/guestgrants.py) */
const APPS_LEVELS=[['','off','the tools apps/cloudflare are refused'],['read','read','list apps and workers, read app state, download Durable Objects'],
  ['write','write','+ upload/restore to Cloudflare, rename, write app state'],['full','full','+ delete apps and Cloudflare workers']];
function polAppsRow(p){
  const cur=p.manage_apps||'';
  return `<div class=kv><b>Apps &amp; Cloudflare</b><span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px">`+
    `<select class=input data-ma="${esc(p.name)}" style="width:auto;font-size:12.5px" onchange="savePolApps('${esc(p.name)}')">`+
    APPS_LEVELS.map(([v,l])=>`<option value="${v}"${v===cur?' selected':''}>${l}</option>`).join('')+`</select>`+
    `<span class=text-muted>${escT((APPS_LEVELS.find(x=>x[0]===cur)||APPS_LEVELS[0])[2])} · applies at once</span>`+
    `<span class=msg data-mamsg="${esc(p.name)}"></span></span></div>`;
}
function savePolApps(name){
  const v=document.querySelector(`select[data-ma="${CSS.escape(name)}"]`).value;
  if(v==='full'&&!confirm(`Let ${name} delete apps and Cloudflare workers?`)){loadPolicy();return;}
  fetch(`/api/instances/${name}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'MANAGE_APPS',value:v})}).then(async r=>{
      let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-mamsg="${CSS.escape(name)}"]`), m=d.msg||('HTTP '+r.status);
      if(el)el.textContent=(!r.ok||/error/.test(m)?'⚠️ ':'✓ ')+m;
    });
}
function polRouterRow(p){
  const pols=window.ROUTER_POLICIES||['default'], cur=p.model_router||'';
  const opts=['<option value="">off</option>'].concat(pols.map(n=>`<option${n===cur?' selected':''}>${escT(n)}</option>`));
  if(cur&&pols.indexOf(cur)<0)opts.push(`<option selected>${escT(cur)}</option>`);
  return `<div class=kv><b>Model router</b><span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px">`+
    `<select class=input data-rt="${esc(p.name)}" style="width:auto;font-size:12.5px" onchange="savePolRouter('${esc(p.name)}')">${opts.join('')}</select>`+
    `<span class=text-muted>${cur?'each turn may run on another running model (Models tab → Model router)':'the instance always uses its own model'} · applies at once</span>`+
    `<span class=msg data-rtmsg="${esc(p.name)}"></span></span></div>`;
}
function savePolRouter(name){
  const v=document.querySelector(`select[data-rt="${CSS.escape(name)}"]`).value;
  fetch(`/api/instances/${name}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'MODEL_ROUTER',value:v})}).then(async r=>{
      let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-rtmsg="${CSS.escape(name)}"]`), m=d.msg||('HTTP '+r.status);
      if(el)el.textContent=(!r.ok||/error/.test(m)?'⚠️ ':'✓ ')+m;
    });
}
async function loadRouter(){
  let d; try{d=await (await fetch('/api/router')).json()}catch(e){return}
  window.ROUTER_POLICIES=d.policies||['default'];
  const j=d.jev||{};
  document.getElementById('rtjev').innerHTML=j.ok&&j.ready
    ?`Jev ready · <code>${escT(j.model||'')}</code> · ${j.calls||0} classifications · <code>${escT(d.url)}</code>`
    :`Jev not available — ${escT(j.error||'loading')} ${d.url?'':'(set JEV_URL in Settings)'}`;
  const tiers=d.tiers||{}, names=d.tier_names||[];
  RT_LVL={...(d.levels||{})}; RT_TL=d.tier_level||{};
  const lad=Object.fromEntries((d.ladder||[]).map(e=>[e.key,e]));
  document.getElementById('rtcands').innerHTML=(d.candidates||[]).map(c=>
    `<tr><td class=mono style="font-size:12.5px">${escT(c.key)}</td><td style="font-size:12.5px">${escT(c.instances.join(', '))}</td>`+
    `<td><select class=input data-tier="${esc(c.key)}" style="font-size:12.5px"><option value="">— not a target —</option>`+
    names.map(t=>`<option${tiers[c.key]===t?' selected':''}>${t}</option>`).join('')+`</select></td>`+
    `<td>${lvlSel(c.key,RT_TL[tiers[c.key]]||'')}</td><td style="font-size:12px;white-space:nowrap">${lad[c.key]?rtPrice(lad[c.key]):'—'}</td></tr>`).join('')
    ||'<tr><td colspan=5 class=text-muted>No running instance with an OpenAI-style model.</td></tr>';
  if(CATALOG.length)renderModels();
  const rec=d.recent||[];
  document.getElementById('rtrecent').innerHTML=rec.length?rec.slice(0,15).map(r=>{
    const cl=Object.entries(r.classes||{}).filter(([q])=>q!=='level').map(([q,v])=>`${escT(q)}=${escT(v[0])} ${v[1]}`).join(' · ');
    const moved=r.chosen&&r.chosen!==r.own;
    return `<div style="padding:5px 0;border-bottom:1px solid var(--color-divider)"><span class=mono>${new Date(r.ts*1000).toLocaleString()}</span> · <b>${escT(r.instance)}</b> · `+
      (r.level?`<span class="tag tag-neutral">L${r.level}</span> `:'')+(r.escalated?'<span class="tag tag-accent">escalated</span> ':'')+
      (moved?`<span class="tag tag-accent">${escT(r.own)} → ${escT(r.chosen)}</span>`:`<span class="tag tag-neutral">own model</span>`)+
      (r.price!=null?` $${(+r.price).toFixed(2)}/1M`:'')+(r.result?` · <b>${escT(r.result)}</b>`:'')+
      ` <span>${cl}</span> · ${r.ms} ms · ${escT(r.why||'')}`+
      (r.excerpt?`<div class=text-muted style="font-size:11.5px">${escT(r.excerpt)}</div>`:'')+`</div>`}).join('')
    :'No routed turns yet.';
}
/* — cost router: a level per model (the ladder); catalog rows and running models share RT_LVL — */
let RT_LVL={}, RT_TL={};
function rtPrice(e){return e.price==null?'price ?':'$'+(+e.price).toFixed(2)+'/1M'+(e.note?' · '+escT(e.note):'')+(e.vision?' · vision':'')}
function lvlSel(key,dflt){
  return `<select class=input data-lvl="${esc(key)}" style="font-size:12px;padding:2px 4px" onchange="setLvl(this)"><option value="">${dflt?'tier: '+dflt:'—'}</option>`+
    [1,2,3,4,5].map(n=>`<option value=${n}${RT_LVL[key]===n?' selected':''}>L${n}</option>`).join('')+'</select>';
}
async function setLvl(el){
  const k=el.dataset.lvl; if(el.value)RT_LVL[k]=+el.value; else delete RT_LVL[k];
  let d={}; try{d=await (await fetch('/api/router/levels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({levels:RT_LVL})})).json()}catch(e){d={error:'manager not reachable'}}
  const m=d.ok?'ladder saved ✓':'⚠️ '+(d.error||'failed');
  ['rtmsg','mdlmsg'].forEach(i=>{const x=document.getElementById(i);if(x)x.textContent=m});
}
async function loadRouterSavings(){
  const el=document.getElementById('rtsave'); if(!el)return;
  let d={}; try{d=await (await fetch('/api/router/savings')).json()}catch(e){el.textContent='not reachable';return}
  if(d.error){el.textContent=d.error;return}
  el.innerHTML=`<div style="font-size:12px;margin-bottom:8px">Always-strong reference: <code>${escT(d.strong)}</code></div>`+
    '<table class=table><thead><tr><th>Window</th><th>Routed turns</th><th>Tokens in/out</th><th>Cost</th><th>Always strong</th><th>Saved</th></tr></thead><tbody>'+
    ['24h','7d','30d'].map(w=>{const x=d[w]||{};return `<tr><td>${w}</td><td>${x.turns||0}</td><td style="font-variant-numeric:tabular-nums">${(x.in||0).toLocaleString()} / ${(x.out||0).toLocaleString()}</td>`+
      `<td>$${(x.actual||0).toFixed(4)}</td><td>$${(x.strong||0).toFixed(4)}</td><td><b>$${(x.saved||0).toFixed(4)}</b></td></tr>`}).join('')+'</tbody></table>';
}
async function saveTiers(){
  const tiers={};
  document.querySelectorAll('select[data-tier]').forEach(s=>{if(s.value)tiers[s.dataset.tier]=s.value});
  const d=await (await fetch('/api/router/tiers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tiers})})).json();
  document.getElementById('rtmsg').textContent=d.ok?'saved ✓':'⚠️ '+(d.error||'failed');
  loadRouter();
}
function savePolBudget(name,reset){
  const v=reset?'':String(Math.max(0,Math.round(parseFloat(document.querySelector(`input[data-bud="${CSS.escape(name)}"]`).value||'0')*1e6)));
  fetch(`/api/instances/${name}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'BUDGET_TOKENS',value:v})}).then(async r=>{
      let d={}; try{d=await r.json()}catch(e){}
      const el=document.querySelector(`[data-budmsg="${CSS.escape(name)}"]`);
      const m=d.msg||('HTTP '+r.status), bad=!r.ok||/error/.test(m);
      if(el)el.textContent=(bad?'⚠️ ':'✓ ')+m;
      if(!bad)setTimeout(loadPolicy,600);
    });
}
function _bar(pct,max,color){
  const w=Math.max(0,Math.min(100,max?100*pct/max:0));
  return `<div style="background:var(--color-neutral-200,#e5e7eb);border-radius:4px;height:6px;overflow:hidden;min-width:60px"><div style="width:${w}%;height:100%;background:${color}"></div></div>`;
}
async function loadResources(){
  loadUsageChart();                       // the chart below the table, same tab, both entry points
  loadRouterSavings();
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
/* — LLM usage chart (Resources tab): one row per instance, tokens stacked
   by model and a cost bar, each scaled to the largest instance. — */
let USAGE_WIN=0;
const MODEL_HUES=[212,28,150,268,92,340,48,190];
function usageWindow(sec){
  USAGE_WIN=sec;
  document.querySelectorAll('#uwin button').forEach(b=>b.classList.toggle('on',(+b.dataset.w)===sec));
  loadUsageChart();
}
async function loadUsageChart(){
  const el=document.getElementById('usagechart'); if(!el)return;
  const since=USAGE_WIN?Math.floor(Date.now()/1000)-USAGE_WIN:0;
  let r,rows=[];
  try{r=await fetch('/api/usage-by-model?since='+since)}catch(e){el.textContent='usage unavailable (network: '+e.message+')';return;}
  if(!r.ok){el.textContent='usage unavailable (HTTP '+r.status+')';return;}
  try{rows=(await r.json()).rows||[];}catch(e){el.textContent='usage unavailable (answer is not JSON)';return;}
  if(!rows.length){el.textContent='no LLM calls in this window';return;}
  const models=[]; rows.forEach(r=>{if(models.indexOf(r.model)<0)models.push(r.model)});
  const col=m=>`hsl(${MODEL_HUES[models.indexOf(m)%MODEL_HUES.length]} 55% ${models.indexOf(m)>=MODEL_HUES.length?35:50}%)`;
  const inst={};
  rows.forEach(r=>{const i=inst[r.instance]||(inst[r.instance]={name:r.instance,tin:0,tout:0,cost:0,calls:0,parts:[]});
    i.tin+=r.in; i.tout+=r.out; i.cost+=r.cost; i.calls+=r.calls; i.parts.push(r);});
  const list=Object.values(inst).sort((a,b)=>(b.tin+b.tout)-(a.tin+a.tout));
  const maxI=Math.max(...list.map(i=>i.tin),1), maxO=Math.max(...list.map(i=>i.tout),1), maxC=Math.max(...list.map(i=>i.cost),1e-9);
  const totI=list.reduce((s,i)=>s+i.tin,0), totO=list.reduce((s,i)=>s+i.tout,0), totC=list.reduce((s,i)=>s+i.cost,0);
  const cell=(l,v)=>`<td data-label="${l}">${v}</td>`;
  /* in and out each on their own scale (out is ~1% of in): a stacked bar by model, the figure beside it */
  const stack=(i,key,max)=>`<div style="display:flex;align-items:center;gap:8px;font-size:12px"><span style="font-variant-numeric:tabular-nums;min-width:48px;text-align:right">${fmtTok(i[key==='in'?'tin':'tout'])}</span>`+
    `<div style="display:flex;height:12px;flex:0 0 ${Math.max(1,100*i[key==='in'?'tin':'tout']/max).toFixed(1)}%;max-width:calc(100% - 56px);min-width:2px;border-radius:3px;overflow:hidden">`+
    i.parts.filter(p=>p[key]>0).sort((a,b)=>b[key]-a[key]).map(p=>`<div title="${esc(p.model)}: ${fmtTok(p.in)} in / ${fmtTok(p.out)} out · ${fmtCost(p.cost)}" style="width:${(100*p[key]/Math.max(i[key==='in'?'tin':'tout'],1)).toFixed(2)}%;background:${col(p.model)}"></div>`).join('')+`</div></div>`;
  el.innerHTML=`<table class=table><thead><tr><th style="width:16%">Instance</th><th style="width:28%">Tokens in (by model)</th><th style="width:28%">Tokens out (by model)</th><th style="width:20%">Cost</th><th style="width:8%;text-align:right">Calls</th></tr></thead><tbody>`+
    list.map(i=>`<tr>${cell('Instance',`<b style="font-family:var(--font-heading)">${escT(i.name)}</b>`)}`+
      cell('Tokens in',stack(i,'in',maxI))+cell('Tokens out',stack(i,'out',maxO))+
      cell('Cost',`<div style="display:flex;align-items:center;gap:8px;font-size:12px"><span style="font-variant-numeric:tabular-nums;min-width:52px">${fmtCost(i.cost)}</span>${_bar(i.cost,maxC,'var(--color-accent)')}</div>`)+
      cell('Calls',`<span style="font-variant-numeric:tabular-nums;font-size:12px;float:right">${i.calls}</span>`)+`</tr>`).join('')+
    `</tbody><tfoot><tr><td class=text-muted style="font-size:12px">${list.length} instances · ${models.length} models</td><td style="font-size:12px;font-variant-numeric:tabular-nums;padding-left:8px">${fmtTok(totI)} in</td><td style="font-size:12px;font-variant-numeric:tabular-nums;padding-left:8px">${fmtTok(totO)} out</td><td style="font-size:12px;font-variant-numeric:tabular-nums">${fmtCost(totC)}</td><td></td></tr></tfoot></table>`+
    `<div style="display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:10px;font-size:12px">`+
    models.map(m=>{const rs=rows.filter(r=>r.model===m), ti=rs.reduce((s,r)=>s+r.in,0), to=rs.reduce((s,r)=>s+r.out,0), c=rs.reduce((s,r)=>s+r.cost,0);
      return `<span style="display:inline-flex;align-items:center;gap:6px"><span style="width:10px;height:10px;border-radius:2px;background:${col(m)}"></span><span class=mono>${escT(m)}</span><span class=text-muted style="font-variant-numeric:tabular-nums">${fmtTok(ti)} in · ${fmtTok(to)} out · ${fmtCost(c)}</span></span>`}).join('')+`</div>`;
}
let ACT_CUR='', ACT_EVENTS=[], ACT_WIN=0;
async function openActivity(name){
  ACT_CUR=name;
  document.getElementById('actname').textContent=name;
  document.getElementById('actdlg').style.display='grid';
  document.getElementById('actrows').innerHTML='<tr><td class=text-muted style="padding:12px">…</td></tr>';
  try{ACT_EVENTS=(await (await fetch('/api/audit/'+encodeURIComponent(name))).json()).events||[];}catch(e){ACT_EVENTS=[];}
  ACT_TURNS={};
  try{for(const t of ((await (await fetch('/api/trace/'+encodeURIComponent(name)+'?limit=200')).json()).turns||[]))ACT_TURNS[t.turn]=t;}catch(e){}
  actRender();
}
let ACT_TURNS={}, ACT_OPEN={};
function actToggle(id){ACT_OPEN[id]=!ACT_OPEN[id];actRender();}
function actMs(ms){return ms==null?'':(ms>=1000?(ms/1000).toFixed(1)+' s':ms+' ms');}
function actWindow(sec){
  ACT_WIN=sec;
  document.querySelectorAll('#actwin button').forEach(b=>b.classList.toggle('on',(+b.dataset.w)===sec));
  actRender();
}
function actRow(e){
  const d=new Date((e.ts||0)*1000).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
  // Failed calls show WHY (the audit carries the error text now); healthy
  // ones show a peek at the result. The word changed too: "denied" implied
  // policy, but ok:false mostly means the call itself failed.
  const extra = e.ok===false
    ? (e.err?`<div style="font-size:11px;color:var(--color-neutral-600)">${escT(e.err)}</div>`:'')
    : (e.result?`<div class=text-muted style="font-size:11px">${escT(e.result)}</div>`:'');
  const ms = e.ms!=null?`<span class=text-muted style="font-size:11px;white-space:nowrap"> · ${actMs(e.ms)}</span>`:'';
  return `<tr><td class=text-muted style="white-space:nowrap;font-size:12px">${d}</td>`+
    `<td class=mono style="font-size:12.5px">${escT(e.tool)}${e.ok===false?' <span class="tag tag-neutral" style="font-size:10px">failed</span>':''}</td>`+
    `<td class=mono style="font-size:12px;word-break:break-all;color:var(--color-accent-700)">${escT(e.target||'')}${ms}${extra}</td></tr>`;
}
function actRender(){
  const cut = ACT_WIN ? (Date.now()/1000 - ACT_WIN) : 0;
  const ev = ACT_EVENTS.filter(e=>(e.ts||0) >= cut);
  // Traces: audit lines grouped by turn (newest turn first, as the audit is);
  // a group head carries what the turn row knows — duration, steps, LLM
  // calls, tokens, outcome. Lines without a turn (old audits, notify,
  // send_signal) stay as "unbound" at the end.
  const groups=[], byTurn={}, loose=[];
  for(const e of ev){
    if(!e.turn){loose.push(e);continue;}
    if(!byTurn[e.turn]){byTurn[e.turn]={id:e.turn,rows:[]};groups.push(byTurn[e.turn]);}
    byTurn[e.turn].rows.push(e);
  }
  let html='';
  for(const g of groups){
    const t=ACT_TURNS[g.id]||{}, open=!!ACT_OPEN[g.id], first=g.rows[g.rows.length-1];
    const d=new Date(((t.ts_start||first.ts)||0)*1000).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const failed=g.rows.filter(e=>e.ok===false).length;
    const oc=t.outcome||'', ocTag=oc&&oc!=='ok'?` <span class="tag tag-neutral" style="font-size:10px">${escT(oc)}</span>`:'';
    const facts=[t.ms!=null?actMs(t.ms):null, t.steps!=null?t.steps+' steps':null,
      t.llm_calls?t.llm_calls+' LLM'+(t.llm_failed?' ('+t.llm_failed+' failed)':''):null,
      (t.in||t.out)?(t.in||0)+'/'+(t.out||0)+' tok':null,
      g.rows.length+' tool'+(g.rows.length===1?'':'s')+(failed?' ('+failed+' failed)':'')].filter(Boolean).join(' · ');
    html+=`<tr style="cursor:pointer;background:var(--color-neutral-50)" onclick="actToggle('${esc(g.id)}')">`+
      `<td class=text-muted style="white-space:nowrap;font-size:12px">${open?'▾':'▸'} ${d}</td>`+
      `<td class=mono style="font-size:12px">${escT(t.kind||'turn')} <span class=text-muted>${escT(g.id)}</span>${ocTag}</td>`+
      `<td class=text-muted style="font-size:11.5px">${escT(facts)}</td></tr>`;
    if(open) html+=g.rows.map(actRow).join('');
  }
  if(loose.length){
    if(groups.length) html+=`<tr><td colspan=3 class=text-muted style="font-size:11px;padding:6px 8px">unbound (no turn)</td></tr>`;
    html+=loose.map(actRow).join('');
  }
  document.getElementById('actrows').innerHTML=html||'<tr><td class=text-muted style="padding:12px">nothing in the selected range</td></tr>';
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
  const closed=all.filter(m=>m.status==='done'||m.status==='failed');   // all of them: they can be edited/deleted
  const cor='';
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
        <span class="tag tag-neutral" title="owner of the mission">${escT(m._inst||'?')}</span>
        ${bar(m)}
        <span style="margin-left:auto;display:flex;gap:6px">
          ${m.status==='active'?`<button class="btn btn-secondary btn-sm" onclick="missionAct('${esc(m.id)}','pause','${esc(m._inst||'')}')">Pause</button>`:''}
          ${m.status==='paused'?`<button class="btn btn-secondary btn-sm" onclick="missionAct('${esc(m.id)}','resume','${esc(m._inst||'')}')">Resume</button>`:''}
          ${(m.status==='active'||m.status==='paused')?`<button class="btn btn-ghost btn-sm" onclick="missionAct('${esc(m.id)}','abort','${esc(m._inst||'')}')">Cancel</button>`:''}
          <button class="btn btn-secondary btn-sm" onclick="missionEdit('${esc(m.id)}')">Edit</button>
          <button class="btn btn-ghost btn-sm" onclick="missionAct('${esc(m.id)}','delete','${esc(m._inst||'')}')">Delete</button>
        </span>
      </div>
      ${cur?`<div class=text-muted style="font-size:12.5px;margin-top:4px">current step ${cur.n}: ${escT(cur.text)} [${cur.status}]${cur.target?` · on <b>${escT(cur.target)}</b>`:''}${cur.task_id?` · task <span class=mono>${escT(cur.task_id)}</span>`:''}</div>`:''}
      ${m.summary?`<div class=text-muted style="font-size:12.5px;margin-top:4px">Summary: ${escT(m.summary)}</div>`:''}
      ${log?`<div class=text-muted style="font-size:11.5px;margin-top:3px;opacity:.75">${escT(log)}</div>`:''}
    </div>`};
  document.getElementById('missions').innerHTML=cor+
    (open.length||closed.length
      ? open.map(row).join('')
        + (closed.length?`<div class=text-muted style="margin:14px 0 4px;font-size:10px;letter-spacing:.1em;text-transform:uppercase">Recently completed</div>${closed.map(row).join('')}`:'')
      : '<span class=text-muted style="font-size:13px">No missions. Any agent creates them itself for multi-step jobs (mission_start) — e.g. via chat: "… — as a mission".</span>');
}
async function missionAct(id,action,instance){
  if(action==='abort'&&!confirm('Abort mission '+id+'?'))return;
  if(action==='delete'&&!confirm('Delete mission '+id+' for good? (any status)'))return;
  await fetch('/api/mission-admin',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,action,instance:instance||''})}).catch(()=>{});
  loadMissions();
}
let _miEdit=null;   // the mission currently open in the edit dialog
async function missionEdit(id){
  let d={}; try{d=await (await fetch('/api/missions')).json()}catch(e){return;}
  const all=Object.entries(d.by_instance||{}).flatMap(([i,l])=>l.map(m=>({...m,_inst:i})));
  const m=all.find(x=>x.id===id); if(!m)return;
  _miEdit=m;
  document.getElementById('midlgid').textContent=m.id+' · '+(m._inst||'');
  document.getElementById('migoal').value=m.goal||'';
  document.getElementById('misteps').value=(m.steps||[]).map(s=>s.text).join('\\n');
  document.getElementById('mistatus').value=m.status||'active';
  document.getElementById('mihint').textContent=(m.steps||[]).map(s=>`${s.n}: ${s.status}`).join(' · ');
  document.getElementById('midlg').style.display='grid';
}
function midlgClose(){document.getElementById('midlg').style.display='none';_miEdit=null;}
async function missionSave(){
  if(!_miEdit)return;
  const body={id:_miEdit.id,action:'edit',instance:_miEdit._inst||'',
    goal:document.getElementById('migoal').value,
    steps:document.getElementById('misteps').value.split('\\n').map(s=>s.trim()).filter(Boolean),
    status:document.getElementById('mistatus').value};
  const r=await (await fetch('/api/mission-admin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json().catch(()=>({msg:'?'}));
  if(r.msg!=='ok'&&r.msg!=='unchanged'){document.getElementById('mihint').textContent='\u26a0\ufe0f '+r.msg;return;}
  midlgClose();loadMissions();
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
        `title="Run now" onclick="runTask('${esc(t.id)}')" ${t.status==='running'?'disabled':''}>${I_RUN}</button>`+
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
  inst.value=t.instance; inst.disabled=false;
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
       body:JSON.stringify({message,schedule,instance})})
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
async function runTask(id){const r=await (await fetch('/api/tasks/'+encodeURIComponent(id)+'/run',{method:'POST'})).json();if(!/queued/.test(r.msg||''))alert(r.msg||'?');loadTasks();}
async function delTask(id){if(confirm('Delete task?')){await fetch('/api/tasks/'+encodeURIComponent(id)+'/delete',{method:'POST'});loadTasks();}}

/* — Usage: the same numbers as server-side, just fetched afterwards — */
function fmtTok(n){n=n||0;
  return n>=1e6?(n/1e6).toFixed(1).replace(/\\.0$/,'')+'M'
       : n>=1000?(n/1000).toFixed(1).replace(/\\.0$/,'')+'k' : String(n)}
function fmtCost(c){c=c||0; return '$'+(c>=0.01||!c?c.toFixed(2):c.toFixed(4))}
async function refreshUsage(){
  let u; try{u=await (await fetch('/api/usage')).json()}catch(e){return}
  let day=0,all=0,tin=0,tout=0;
  Object.values(u).forEach(v=>{const d=v.today||{},t=v.total||{};
    day+=d.cost||0; all+=t.cost||0; tin+=d.in||0; tout+=d.out||0});
  const cards=[...document.querySelectorAll('.icard')];
  const tot=n=>{const t=(u[n]||{}).total||{};return (t.in||0)+(t.out||0)};
  const mx=Math.max(1,...cards.map(c=>tot(c.dataset.name)));
  cards.forEach(c=>{
    const v=u[c.dataset.name]||{},d=v.today||{},t=v.total||{};
    c.querySelector('.ic-today').textContent='Today '+fmtTok(d.in)+' / '+fmtTok(d.out);
    c.querySelector('.ic-total').textContent='Total '+fmtTok(t.in)+' / '+fmtTok(t.out);
    c.querySelector('.ic-cost').textContent=fmtCost(t.cost);
    c.querySelector('.ic-bar').style.width=Math.round(tot(c.dataset.name)/mx*100)+'%';
    c.querySelector('.ic-use').title=(t.calls||0)+' LLM calls · today '+fmtCost(d.cost);
  });
  document.getElementById('ktok').textContent=fmtTok(tin+tout);
  document.getElementById('ktin').textContent=fmtTok(tin);
  document.getElementById('ktout').textContent=fmtTok(tout);
  document.getElementById('spend').textContent='LLM today '+fmtCost(day)+' · total '+fmtCost(all);
}

/* — Instances dashboard: fleet counts from the server-rendered cards — */
const HOST_MEM_MIB=__HOSTMEM__;
let FLEET_F='all';
/* Instance cards start collapsed; which ones are open is remembered per browser. */
function icOpenSet(){try{return new Set(JSON.parse(localStorage.getItem('icOpen')||'[]'))}catch(e){return new Set()}}
function icToggle(btn){
  const c=btn.closest('.icard'), o=c.classList.toggle('open'), s=icOpenSet();
  btn.setAttribute('aria-expanded',o);
  o?s.add(c.dataset.name):s.delete(c.dataset.name);
  try{localStorage.setItem('icOpen',JSON.stringify([...s]))}catch(e){}
}
function icRestore(){const s=icOpenSet();
  document.querySelectorAll('.icard').forEach(c=>{if(s.has(c.dataset.name)){c.classList.add('open');
    const b=c.querySelector('.ic-tog');if(b)b.setAttribute('aria-expanded','true');}});}
/* How a squad message reached its recipients: queued (sent), the wake turn ran
   (received), or it failed (VM down, no web bridge, timeout) — with the reason. */
function sqDelivery(m){
  if(m.queued)return `<div class=text-muted style="font-size:12px">⏸ waiting (${escT(m.blocked||'limit')}) — delivered automatically</div>`;
  if(m.blocked)return `<div style="font-size:12px;color:var(--color-warn)">not delivered: ${escT(m.blocked)}</div>`;
  const got=m.received||[], bad=m.failed||{}, sent=(m.delivered||[]).filter(x=>!got.includes(x)&&!(x in bad));
  return (got.length?`<div class=text-muted style="font-size:12px">received by ${escT(got.join(', '))}</div>`:'')+
    (sent.length?`<div class=text-muted style="font-size:12px">sent to ${escT(sent.join(', '))} · waiting</div>`:'')+
    Object.keys(bad).map(x=>`<div style="font-size:12px;color:var(--color-warn)">not received by ${escT(x)}: ${escT(bad[x])}</div>`).join('');
}
/* A squad's wake turns now (mgr/squads.py working): running = working… (elapsed), or queued. */
function sqWorking(list){
  const mmss=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
  return (list||[]).map(w=>`<div class="text-muted sq-work">`+(w.state==='working'
    ?`⏳ ${escT(w.member)} is working… (${mmss(+w.elapsed||0)})`
    :`⏸ ${escT(w.member)} queued${w.msg?' (msg #'+(+w.msg)+')':''}`)+'</div>').join('');
}
/* Wakes held back by the squad's hourly limit / budget / pause: they go out by themselves. */
function sqQueued(q){
  if(!q||!q.count)return '';
  const mins=q.next_at?Math.max(1,Math.round((q.next_at*1000-Date.now())/60000)):0;
  return `<div class="text-muted sq-work">⏸ wake limit reached — ${+q.count} message${q.count===1?'':'s'} waiting`+
    (mins?`, next delivery in ~${mins} min`:'')+' (raise "Max wakes per hour" in the squad settings to go faster)</div>';
}
/* a message's text; a member's long turn answer (kind "turn") folds. Turn answers and long member
   messages are annotatable (class sq-ann): select text, comment, send the notes to the lead (mgr/squadnotes.py) */
function sqAnnot(m){return m.kind==='turn'||(!String(m.from).startsWith('user:')&&String(m.text||'').length>300)}
function sqText(m){
  const t=String(m.text||''), pre='white-space:pre-wrap;word-break:break-word;margin:4px 0', an=sqAnnot(m);
  const body=`<div ${an?`class=sq-ann data-mid=${+m.id} `:''}style="${pre}">${escT(t)}</div>`;
  return (m.kind==='turn'&&t.length>600?`<details class=sq-turn><summary>${escT(t.slice(0,280))}…</summary>${body}</details>`:body)+
    (an?`<div class=sq-nb data-mid=${+m.id}></div>`:'');
}
let SQN={p:'',list:[]}, SQA={sel:null,open:false,note:null,p:'',ret:null,t:0};
const sqEl=id=>document.getElementById(id);
async function sqJ(url,body){
  try{return await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}
  catch(e){return {error:'manager not reachable'}}
}
const sqNotesUrl=p=>'/api/projects/'+encodeURIComponent(p)+'/squad/notes';
async function sqNotesLoad(){
  const p=sqName(); if(!p)return;
  let d={}; try{d=await (await fetch(sqNotesUrl(p))).json()}catch(e){return}
  if(d.error||p!==sqName())return;
  SQN={p,list:d.notes||[]}; sqAnnApply();
}
/* where a note's quote is in the text now: the hit whose prefix/suffix match, else the first, else -1 */
function sqAnchor(t,n){
  let first=-1;
  for(let i=t.indexOf(n.quote);i>=0;i=t.indexOf(n.quote,i+1)){
    if(t.slice(0,i).endsWith(n.prefix||'')&&t.slice(i+n.quote.length).startsWith(n.suffix||''))return i;
    if(first<0)first=i;
  }
  return first;
}
function sqAnnApply(){
  if(SQN.p!==sqName())return;
  document.querySelectorAll('.sq-ann').forEach(el=>{
    const mid=+el.dataset.mid, t=el.textContent, ns=SQN.list.filter(n=>n.msg_id===mid);
    const hits=ns.map(n=>({n,i:sqAnchor(t,n)})).filter(x=>x.i>=0).sort((a,b)=>a.i-b.i), shown=new Set();
    let h='',at=0;
    for(const x of hits){
      if(x.i<at)continue;          /* ponytail: an overlapping note is listed under the message, not highlighted */
      shown.add(x.n.id);
      h+=escT(t.slice(at,x.i))+`<mark class="sq-mk${x.n.status==='sent'?' sent':''}" tabindex=0 data-nid=${+x.n.id} `+
        `title="${esc(x.n.comment)}" aria-label="${esc('comment: '+x.n.comment)}">${escT(t.substr(x.i,x.n.quote.length))}</mark>`;
      at=x.i+x.n.quote.length;
    }
    h+=escT(t.slice(at));
    if(el.dataset.h!==h&&(hits.length||el.dataset.h)){el.dataset.h=h;el.innerHTML=h}
    const nb=(el.closest('details')||el).nextElementSibling; if(!nb||!nb.classList.contains('sq-nb'))return;
    const dr=ns.filter(n=>n.status==='draft'), sent=[...new Set(ns.filter(n=>n.status==='sent').map(n=>n.sent_msg_id))];
    const bh=(dr.length?`<span>${dr.length} comment${dr.length>1?'s':''}</span> · <button class="btn btn-ghost btn-sm" onclick="sqNoteSend({msg_id:${mid}})">Send all</button>`:'')+
      sent.map(id=>`<span class=text-muted>sent → #${+id}</span>`).join('')+
      ns.filter(n=>!shown.has(n.id)).map(n=>`<div class=text-muted style="flex-basis:100%">unanchored: «${escT(n.quote.slice(0,80))}» — ${escT(n.comment)}`+
        (n.status==='draft'?` <button class="btn btn-ghost btn-sm" onclick=sqNoteDel(${+n.id})>Delete</button>`:` (sent → #${+n.sent_msg_id})`)+'</div>').join('');
    if(nb.dataset.h!==bh){nb.dataset.h=bh;nb.innerHTML=bh}
  });
}
/* the selection when it lies inside one annotatable message -> {mid, quote, prefix, suffix, rect} | null */
function sqSel(){
  const s=getSelection(); if(!s||s.isCollapsed||!s.rangeCount)return null;
  const r=s.getRangeAt(0), c=r.commonAncestorContainer, el=(c.nodeType===1?c:c.parentElement).closest('.sq-ann');
  if(!el)return null;
  const pre=document.createRange(); pre.setStart(el,0); pre.setEnd(r.startContainer,r.startOffset);
  const t=el.textContent, i=pre.toString().length, q=r.toString();
  if(!q.trim()||q.length>1000)return null;
  return {mid:+el.dataset.mid,quote:q,prefix:t.slice(Math.max(0,i-40),i),suffix:t.slice(i+q.length,i+q.length+40),rect:r.getBoundingClientRect()};
}
/* while a selection or the popover is in a thread, polling does not redraw it */
function sqaBusy(th){
  const s=getSelection();
  return SQA.open||!!(th&&s&&!s.isCollapsed&&s.anchorNode&&th.contains(s.anchorNode));
}
function sqPlace(el,x,y){
  el.hidden=false;
  const w=el.offsetWidth, h=el.offsetHeight;
  el.style.left=Math.max(8,Math.min(innerWidth-w-8,x))+'px';
  el.style.top=Math.max(8,y+h>innerHeight-8?y-h-40:y)+'px';
}
function sqPopOpen(rect,quote){
  sqEl('sqa-menu').hidden=true; sqEl('sqa-fab').hidden=true;
  SQA.open=true; SQA.p=sqName(); SQA.ret=document.activeElement;
  sqEl('sqa-q').textContent='«'+(quote.length>300?quote.slice(0,300)+'…':quote)+'»';
  sqEl('sqa-msg').textContent='';
  sqPlace(sqEl('sqa-pop'),rect.left,rect.bottom+8);
}
function sqPop(){
  const s=SQA.sel; if(!s)return;
  SQA.note=null; sqPopOpen(s.rect,s.quote);
  sqEl('sqa-view').innerHTML=''; sqEl('sqa-edit').hidden=false; sqEl('sqa-text').value=''; sqEl('sqa-text').focus();
}
function sqView(mk){
  const n=SQN.list.find(x=>x.id===+mk.dataset.nid); if(!n)return;
  SQA.sel=null; SQA.note=n; sqPopOpen(mk.getBoundingClientRect(),n.quote);
  sqEl('sqa-edit').hidden=true;
  sqEl('sqa-view').innerHTML=`<div style="white-space:pre-wrap;overflow-wrap:anywhere">${escT(n.comment)}</div>`+
    `<div class=text-muted style="font-size:12px;margin:4px 0">${escT(n.by)} · ${new Date(n.created*1000).toLocaleString()} · `+
    (n.status==='sent'?`sent → #${+n.sent_msg_id}`:'draft')+'</div><div class=sqa-acts>'+
    (n.status==='draft'?`<button class="btn btn-primary btn-sm" onclick="sqNoteSend({ids:[${+n.id}]})">Send now</button>`+
      `<button class="btn btn-ghost btn-sm" onclick=sqNoteDel(${+n.id})>Delete</button>`:'')+
    `<button class="btn btn-ghost btn-sm" onclick=sqPopClose()>Close</button></div>`;
  sqEl('sqa-view').querySelector('button').focus();
}
function sqPopClose(){
  if(!SQA.open)return;
  SQA.open=false; sqEl('sqa-pop').hidden=true;
  if(SQA.ret&&SQA.ret.focus&&document.contains(SQA.ret))SQA.ret.focus();
  if(location.hash==='#squads'&&SK)skLoad();         /* a redraw held back while it was open */
}
function sqFlash(t){if(location.hash==='#squads')skMsg(t);else sqEl('sqmsg').textContent=t}
function sqAfterSend(){if(location.hash==='#squads'&&SK)skLoad();else if(SQ)sqLoad()}
async function sqNoteSave(send){
  const s=SQA.sel, c=sqEl('sqa-text').value.trim(); if(!s)return;
  if(!c){sqEl('sqa-msg').textContent='write a comment first';return}
  const d=await sqJ(sqNotesUrl(SQA.p),{msg_id:s.mid,quote:s.quote,prefix:s.prefix,suffix:s.suffix,comment:c});
  if(d.error){sqEl('sqa-msg').textContent=d.error;return}
  if(send){
    const r=await sqJ(sqNotesUrl(SQA.p)+'/send',{ids:[d.id]});
    if(r.error){sqEl('sqa-msg').textContent='saved as a draft; sending failed: '+r.error;sqNotesLoad();return}
  }
  getSelection().removeAllRanges(); SQA.sel=null; sqPopClose(); await sqNotesLoad(); if(send)sqAfterSend();
}
async function sqNoteSend(body){
  const d=await sqJ(sqNotesUrl(SQA.open?SQA.p:sqName())+'/send',body);
  if(d.error){if(SQA.open)sqEl('sqa-msg').textContent=d.error;else sqFlash(d.error);return}
  sqPopClose(); sqFlash('feedback sent to '+(d.to||[]).join(', ')+' as #'+d.id); await sqNotesLoad(); sqAfterSend();
}
async function sqNoteDel(id){
  const d=await sqJ(sqNotesUrl(SQA.open?SQA.p:sqName())+'/'+(+id)+'/delete',{});
  if(d.error){if(SQA.open)sqEl('sqa-msg').textContent=d.error;else sqFlash(d.error);return}
  sqPopClose(); await sqNotesLoad();
}
document.addEventListener('selectionchange',()=>{
  clearTimeout(SQA.t);
  SQA.t=setTimeout(()=>{
    if(SQA.open)return;
    SQA.sel=sqSel(); const b=sqEl('sqa-fab'); if(!b)return;
    if(!SQA.sel){b.hidden=true;return}
    sqPlace(b,SQA.sel.rect.left,SQA.sel.rect.bottom+6);
  },120);
});
document.addEventListener('contextmenu',e=>{
  const s=sqSel(); if(!s)return;                    /* else the browser's own menu */
  e.preventDefault(); SQA.sel=s; sqEl('sqa-fab').hidden=true;
  const mn=sqEl('sqa-menu'); sqPlace(mn,e.clientX,e.clientY); mn.querySelector('button').focus();
});
document.addEventListener('mousedown',e=>{const mn=sqEl('sqa-menu');if(mn&&!mn.hidden&&!mn.contains(e.target))mn.hidden=true});
document.addEventListener('scroll',()=>{const b=sqEl('sqa-fab');if(b)b.hidden=true;const mn=sqEl('sqa-menu');if(mn)mn.hidden=true},true);
document.addEventListener('click',e=>{const mk=e.target.closest&&e.target.closest('mark.sq-mk');if(mk&&getSelection().isCollapsed)sqView(mk)});
document.addEventListener('keydown',e=>{
  const mk=e.target.closest&&e.target.closest('mark.sq-mk');
  if(mk&&(e.key==='Enter'||e.key===' ')){e.preventDefault();sqView(mk);return}
  if(e.key!=='Escape')return;
  const mn=sqEl('sqa-menu');
  if(SQA.open){e.stopPropagation();sqPopClose()}
  else if(mn&&!mn.hidden){e.stopPropagation();mn.hidden=true}
},true);
function fleetInit(){
  icRestore();
  const cards=[...document.querySelectorAll('.icard')], n=s=>cards.filter(c=>c.dataset.state===s).length;
  const run=n('run')+n('old'), off=n('off'), old=n('old'), on=cards.filter(c=>c.dataset.state!=='off');
  const circ=2*Math.PI*34;
  document.getElementById('kring').setAttribute('stroke-dasharray',
    (cards.length?run/cards.length*circ:0).toFixed(1)+' '+circ.toFixed(1));
  document.getElementById('krun').textContent=run;
  document.getElementById('ktot').textContent=cards.length;
  document.getElementById('navcnt').textContent=cards.length;
  document.getElementById('krsub').textContent=off+' stopped · '+old+' outdated';
  const cpu=on.reduce((a,c)=>a+(+c.dataset.cpu||0),0), mem=on.reduce((a,c)=>a+(+c.dataset.mem||0),0);
  const pct=HOST_MEM_MIB?Math.min(100,Math.round(mem/HOST_MEM_MIB*100)):0;
  document.getElementById('kcpu').textContent=cpu;
  document.getElementById('kram').textContent=' vCPU · '+(mem/1024).toFixed(1)+' GiB';
  document.getElementById('kmembar').style.width=pct+'%';
  document.getElementById('kmemsub').textContent=HOST_MEM_MIB
    ?pct+'% of '+(HOST_MEM_MIB/1024).toFixed(0)+' GiB host RAM':'host RAM unknown';
  const cnt={all:cards.length,run,off,old};
  document.querySelectorAll('#ffilt button').forEach(b=>b.lastElementChild.textContent=cnt[b.dataset.f]);
  document.getElementById('ocount').textContent=old+(old===1?' instance':' instances');
  document.getElementById('obanner').hidden=!old;
}
function fleetFilter(f){
  FLEET_F=f;
  document.querySelectorAll('#ffilt button').forEach(b=>b.classList.toggle('on',b.dataset.f===f));
  document.querySelectorAll('.icard').forEach(c=>{const st=c.dataset.state;
    c.hidden=!(f==='all'||st===f||(f==='run'&&st==='old'));});
}
async function restartAll(){
  for(const c of document.querySelectorAll('.icard[data-state=old]'))
    await fetch('/api/instances/'+encodeURIComponent(c.dataset.name)+'/restart',{method:'POST'});
  location.reload();
}
function newInstance(){
  document.getElementById('newform').scrollIntoView({behavior:'smooth',block:'start'});
  document.getElementById('nm').focus({preventScroll:true});
}

/* — token throughput: /api/usage-series, one area for the selection — */
let SERIES=null, SERIES_RANGE='24h', CHART_SEL=[];
const CHART_PAL=['var(--color-accent-800)','var(--color-accent-500)','var(--color-neutral-800)','var(--color-accent-400)'];
async function loadSeries(r){
  if(r)SERIES_RANGE=r;
  try{SERIES=await (await fetch('/api/usage-series?range='+SERIES_RANGE)).json()}catch(e){return}
  document.querySelectorAll('#crange button').forEach(b=>b.classList.toggle('on',b.dataset.r===SERIES_RANGE));
  const c=SERIES.cost||[], mx=Math.max(0,...c)||1;
  document.getElementById('kspend').textContent=fmtCost(c.reduce((a,b)=>a+b,0));
  document.getElementById('kspendt').textContent='+'+fmtCost(c[c.length-1])+' today';
  document.getElementById('kbars').innerHTML=c.map(v=>`<i style="height:${Math.round(v/mx*100)}%" title="${fmtCost(v)}"></i>`).join('');
  renderChart();
}
function renderChart(){
  if(!SERIES)return;
  const W=1000,H=200,ser=SERIES.series||{},bk=SERIES.buckets||[],len=bk.length;
  const sum=a=>a.reduce((x,y)=>x+y,0);
  const names=Object.keys(ser).sort((a,b)=>sum(ser[b])-sum(ser[a]));
  CHART_SEL=CHART_SEL.filter(n=>names.includes(n));
  const sel=CHART_SEL.length?CHART_SEL:names;
  const tot=bk.map((_,k)=>sel.reduce((a,n)=>a+ser[n][k],0));
  const max=Math.max(1,...tot)*1.15;
  const path=arr=>'M'+arr.map((v,k)=>(k*W/Math.max(1,len-1)).toFixed(1)+','+(H-v/max*H).toFixed(1)).join(' L');
  const col=n=>CHART_PAL[names.indexOf(n)%CHART_PAL.length];
  const line=path(tot);
  document.getElementById('cplot').innerHTML=
    `<path d="${line} L${W},${H} L0,${H} Z" fill="url(#cfill)"></path>`+
    `<path d="${line}" fill=none stroke="var(--color-accent-700)" stroke-width=2 vector-effect=non-scaling-stroke></path>`+
    (CHART_SEL.length&&sel.length>1?sel.map(n=>`<path d="${path(ser[n])}" fill=none stroke="${col(n)}" stroke-width=1.25 stroke-dasharray="4 3" vector-effect=non-scaling-stroke></path>`).join(''):'');
  const chip=(on,n,label,color)=>`<button data-n="${esc(n)}"${on?' class=on':''}><i style="background:${color}"></i>${escT(label)}</button>`;
  document.getElementById('cchips').innerHTML=chip(!CHART_SEL.length,'','All instances','var(--color-accent-700)')+
    names.map(n=>chip(CHART_SEL.includes(n),n,n,col(n))).join('');
  document.getElementById('ctot').textContent=fmtTok(sum(tot));
  document.getElementById('cscope').textContent=CHART_SEL.length?'tokens · '+CHART_SEL.join(', '):'tokens across all instances';
  document.getElementById('cyt').innerHTML=[3,2,1,0].map(q=>`<span>${fmtTok(Math.round(max*q/3))}</span>`).join('');
  /* x labels at real bucket positions: every 6h / every day / every week, plus the newest */
  const day=SERIES_RANGE!=='24h', step={'24h':6,'7d':1,'30d':7}[SERIES_RANGE]||1, idx=[];
  for(let k=0;k<=len-1-step/2;k+=step)idx.push(k);
  if(len)idx.push(len-1);
  const lbl=k=>{if(k===len-1)return day?'today':'now';const d=new Date(bk[k]*1000);
    return !day?String(d.getHours()).padStart(2,'0')+':00'
      :SERIES_RANGE==='7d'?d.toLocaleDateString(undefined,{weekday:'short'}):d.toLocaleDateString(undefined,{day:'numeric',month:'short'})};
  document.getElementById('cxl').innerHTML=idx.map(k=>`<span style="left:${(k/Math.max(1,len-1)*100).toFixed(2)}%">${escT(lbl(k))}</span>`).join('');
}
function chartPick(n){
  CHART_SEL=!n?[]:CHART_SEL.includes(n)?CHART_SEL.filter(x=>x!==n):CHART_SEL.concat([n]);
  renderChart();
}

/* — navigation: hash-routed pages in the sidebar (a reload after an action keeps the page) — */
const NAV=[...document.querySelectorAll('#nav a')];
const TABS=NAV.map(a=>a.hash.slice(1));
function showTab(t){
  if(TABS.indexOf(t)<0)t='instances';
  TABS.forEach(x=>document.getElementById('s-'+x).classList.toggle('on',x===t));
  NAV.forEach(a=>{
    if(a.hash!=='#'+t){a.removeAttribute('aria-current');return;}
    a.setAttribute('aria-current','page');
    document.getElementById('ptitle').textContent=a.firstElementChild.textContent;
    document.getElementById('pgroup').textContent=a.closest('.navgrp').firstElementChild.textContent;
  });
  document.getElementById('newinst').hidden=t!=='instances';
  navToggle(false);
}
/* below 860px the sidebar is a drawer */
function navToggle(on){document.body.classList.toggle('navopen',!!on)}
/* several pages hold an element whose id equals the hash: start a page at its top, not there */
window.addEventListener('hashchange',()=>{const t=location.hash.slice(1);showTab(t);scrollTo(0,0);if(t==='missions')loadMissions();if(t==='projects')loadProjects();if(t==='squads'){sqClose();loadSquads()}if(t==='models'||t==='policy')loadRouter();if(t==='apps')loadAppsPanel();if(t==='sharing'){loadKatfs();loadIroh();}});
document.getElementById('crange').onclick=e=>{const b=e.target.closest('[data-r]');if(b)loadSeries(b.dataset.r)};
document.getElementById('cchips').onclick=e=>{const b=e.target.closest('[data-n]');if(b)chartPick(b.dataset.n)};
document.getElementById('ffilt').onclick=e=>{const b=e.target.closest('[data-f]');if(b)fleetFilter(b.dataset.f)};

/* — Projects: one folder set, joined by instances with a role (mgr/projects.py) — */
let PROJECTS=[], PJ_EDIT='', PJ_INSTS=[];
async function loadProjects(){
  try{PROJECTS=(await (await fetch('/api/projects')).json()).projects||[]}catch(e){return}
  try{PJ_INSTS=(await (await fetch('/api/instances')).json()).map(i=>i.name)}catch(e){}
  const src=p=>p.source.type==='katfs'?'katfs · '+p.source.share:
    p.source.path+(p.source.subdir?'/'+p.source.subdir:'');
  document.getElementById('projrows').innerHTML=PROJECTS.length?PROJECTS.map(p=>
    `<tr><td><b class=mono>${escT(p.name)}</b><div class=text-muted style="font-size:12px">${escT(p.strategy)}${p.base?' · base '+escT(p.base):''}${p.lead_may_merge?' · lead merges':''}</div></td>`+
    `<td class=mono style="font-size:12.5px;word-break:break-all">${escT(src(p))}</td>`+
    `<td>${Object.entries(p.members||{}).map(([n,m])=>`<span class="tag ${m.role==='writer'?'tag-accent':'tag-neutral'}">${escT(n)} · ${escT(m.role)}</span>`+
      (p.strategy==='worktree'&&m.role==='writer'?` <button class="btn btn-ghost btn-sm" onclick="pjReview('${esc(p.name)}','${esc(n)}')">Changes</button>`:'')).join(' ')||'<span class=text-muted>none yet</span>'}</td>`+
    `<td style="white-space:nowrap"><button class="btn btn-ghost btn-sm" onclick="sqOpen('${esc(p.name)}')">Squad${(p.squad||{}).enabled?((p.squad||{}).paused?' ⏸':' ●'):''}</button>`+
    `<button class="btn btn-ghost btn-sm" onclick="editProject('${esc(p.name)}')">Edit</button>`+
    `<button class="btn btn-ghost btn-sm" onclick="delProject('${esc(p.name)}')">✕</button></td></tr>`).join('')
    :'<tr><td colspan=4 class=text-muted>No projects yet — name a folder below and add the instances that work on it.</td></tr>';
  if(!document.getElementById('pj-members').children.length&&!PJ_EDIT)pjAddMember();
  pjShares('');
}
function pjType(){const k=document.getElementById('pj-type').value==='katfs';
  document.getElementById('pj-hostbox').style.display=k?'none':'';
  document.getElementById('pj-katfsbox').style.display=k?'':'none';
  document.getElementById('pj-basebox').style.display=document.getElementById('pj-strategy').value==='worktree'?'':'none';}
function pjShares(cur){
  const shares=[...(window.KATFS_SHARES||[])], sel=document.getElementById('pj-share');
  cur=cur||sel.value;
  if(cur&&!shares.some(x=>x.id===cur))shares.push({id:cur,name:'(not shared right now)'});
  sel.innerHTML=shares.length?shares.map(x=>`<option value="${esc(x.id)}"${x.id===cur?' selected':''}>${escT((x.name||x.id)+' — '+x.id)}</option>`).join('')
    :'<option value="">no katfs share online</option>';
}
function pjAddMember(name,role){
  const row=document.createElement('div'); row.className='pjrow'; row.style.cssText='display:flex;gap:6px';
  row.innerHTML=`<select class="input pjn" style="flex:2">${PJ_INSTS.map(n=>`<option${n===name?' selected':''}>${escT(n)}</option>`).join('')}</select>`+
    `<select class="input pjr" style="flex:1">${['writer','lead','reader'].map(r=>`<option${r===role?' selected':''}>${r}</option>`).join('')}</select>`+
    `<button type=button class="btn btn-ghost btn-sm" onclick="this.parentNode.remove()">✕</button>`;
  document.getElementById('pj-members').appendChild(row);
}
function pjReset(){
  PJ_EDIT=''; ['pj-name','pj-path','pj-subdir','pj-base'].forEach(i=>document.getElementById(i).value='');
  document.getElementById('pj-strategy').value='shared';
  document.getElementById('pj-name').disabled=false; document.getElementById('pj-merge').checked=false;
  document.getElementById('pj-type').value='host'; pjType();
  document.getElementById('pj-members').innerHTML=''; pjAddMember();
  document.getElementById('pj-head').textContent='New project';
  document.getElementById('pj-save').textContent='Create project';
  document.getElementById('pj-cancel').style.display='none';
}
function editProject(name){
  const p=PROJECTS.find(x=>x.name===name); if(!p)return;
  PJ_EDIT=name;
  document.getElementById('pj-name').value=p.name; document.getElementById('pj-name').disabled=true;
  document.getElementById('pj-strategy').value=p.strategy;
  document.getElementById('pj-base').value=p.base||'';
  document.getElementById('pj-type').value=p.source.type; pjType();
  document.getElementById('pj-path').value=p.source.path||''; document.getElementById('pj-subdir').value=p.source.subdir||'';
  pjShares(p.source.share||'');
  document.getElementById('pj-merge').checked=!!p.lead_may_merge;
  document.getElementById('pj-members').innerHTML='';
  Object.entries(p.members||{}).forEach(([n,m])=>pjAddMember(n,m.role));
  document.getElementById('pj-head').textContent='Edit project '+name;
  document.getElementById('pj-save').textContent='Save';
  document.getElementById('pj-cancel').style.display='';
  document.getElementById('pj-head').scrollIntoView({block:'nearest'});
}
async function saveProject(){
  const type=document.getElementById('pj-type').value, members={};
  document.querySelectorAll('#pj-members .pjrow').forEach(r=>{members[r.querySelector('.pjn').value]={role:r.querySelector('.pjr').value}});
  const source=type==='katfs'?{type,share:document.getElementById('pj-share').value}
    :{type,path:document.getElementById('pj-path').value.trim(),subdir:document.getElementById('pj-subdir').value.trim()};
  const body={name:document.getElementById('pj-name').value.trim(),strategy:document.getElementById('pj-strategy').value,
    source,members,lead_may_merge:document.getElementById('pj-merge').checked,base:document.getElementById('pj-base').value.trim()};
  let d={}; try{d=await (await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}catch(e){d={error:'manager not reachable'}}
  const msg=document.getElementById('pjmsg');
  if(d.error){msg.textContent=d.error;return}
  msg.textContent='saved ✓'+(d.warn?' — '+d.warn:'');
  pjReset(); loadProjects();
}
/* review one writer of a worktree project: files, diff, merge / discard */
let PJ_REV=null;
async function pjReview(p,w){
  PJ_REV={p,w};
  document.getElementById('pjdlgname').textContent=p+' · '+w;
  document.getElementById('pjdlgsum').textContent='taking a snapshot of the folder…';
  document.getElementById('pjdlgdiff').textContent=''; document.getElementById('pjdlgmsg').textContent='';
  document.getElementById('pjdlg').style.display='grid';
  const base='/api/projects/'+encodeURIComponent(p);
  let st={}; try{st=((await (await fetch(base+'/status')).json()).members||{})[w]||{}}catch(e){st={error:'manager not reachable'}}
  const sum=document.getElementById('pjdlgsum');
  if(st.error){sum.textContent=st.error;return}
  sum.innerHTML=`<code>${escT(st.branch)}</code> · <b>${st.ahead}</b> ahead, <b>${st.behind}</b> behind the base · `+
    (st.files.length?st.files.map(f=>`<span class="tag tag-neutral">${escT(f[0])} ${escT(f[1])}</span>`).join(' '):'no changes');
  try{document.getElementById('pjdlgdiff').textContent=await (await fetch(base+'/diff/'+encodeURIComponent(w))).text()}catch(e){}
}
function pjDlgClose(){document.getElementById('pjdlg').style.display='none';PJ_REV=null}
async function pjOp(op){
  if(!PJ_REV)return {};
  const r=await fetch('/api/projects/'+encodeURIComponent(PJ_REV.p)+'/'+op+'/'+encodeURIComponent(PJ_REV.w),{method:'POST'});
  return r.json();
}
async function pjMerge(){
  const d=await pjOp('merge'), msg=document.getElementById('pjdlgmsg');
  if(d.error){msg.textContent=d.error+(d.conflicts&&d.conflicts.length?': '+d.conflicts.join(', '):'');return}
  const done='merged ✓ '+(d.commit||'').slice(0,12)+(d.note?' — '+d.note:'');
  pjReview(PJ_REV.p,PJ_REV.w).then(()=>{document.getElementById('pjdlgmsg').textContent=done});
}
async function pjDiscard(){
  if(!confirm('Throw away '+PJ_REV.w+"'s work? Its folder and branch go back to the base."))return;
  const d=await pjOp('discard');
  if(d.error){document.getElementById('pjdlgmsg').textContent=d.error;return}
  pjReview(PJ_REV.p,PJ_REV.w).then(()=>{document.getElementById('pjdlgmsg').textContent='discarded ✓'});
}
/* squad: the project's members talk in one channel (mgr/squads.py) */
let SQ='';
/* the project the squad views act on: the Projects panel's, else the Squads tab's (SQ was empty
   there after a reload, and an empty name made '/api/projects//squad/decide' -> 'unknown operation') */
function sqName(){return SQ||(typeof SK!=='undefined'?SK:'')}
async function sqOpen(name){SQ=name;document.getElementById('sq-panel').style.display='';document.getElementById('sqmsg').textContent='';
  await sqLoad();document.getElementById('sq-panel').scrollIntoView({block:'nearest'})}
function sqClose(){SQ='';document.getElementById('sq-panel').style.display='none';document.getElementById('sq-gate').innerHTML=''}
async function sqLoad(){
  if(!SQ)return;
  const base='/api/projects/'+encodeURIComponent(sqName())+'/squad';
  let s={},d={};
  try{s=await (await fetch(base)).json();d=await (await fetch(base+'/messages')).json()}catch(e){document.getElementById('sqmsg').textContent='manager not reachable';return}
  if(s.error){document.getElementById('sqmsg').textContent=s.error;return}
  const st=s.settings||{};
  document.getElementById('sq-head').textContent='Squad · '+SQ;
  document.getElementById('sq-autonomy').value=st.autonomy||'trusted'; document.getElementById('sq-approval_tools').value=(st.approval_tools||[]).join(', ');
  document.getElementById('sq-enabled').checked=!!st.enabled; document.getElementById('sq-paused').checked=!!st.paused; document.getElementById('sq-approved').checked=!!st.approved; document.getElementById('sq-cost_routing').checked=!!st.cost_routing; document.getElementById('sq-security_skills').checked=!!st.security_skills;
  ['max_wakes_per_hour','daily_token_budget','max_depth'].forEach(k=>document.getElementById('sq-'+k).value=st[k]);
  document.getElementById('sq-usage').textContent='Tokens today: '+(s.tokens_today||0).toLocaleString()+' of '+(st.daily_token_budget||0).toLocaleString()+' (measured as the LLM usage of the member instances since midnight)';
  document.getElementById('sq-members').innerHTML=(s.members||[]).map(m=>`<span class="tag ${m.role==='writer'?'tag-accent':'tag-neutral'}">${escT(m.name)} · ${escT(m.role)}</span>`).join(' ')||'<span class=text-muted>no members</span>';
  const msgs=d.messages||[], th=document.getElementById('sq-thread');
  if(!sqaBusy(th)){th.innerHTML=msgs.length?msgs.map(m=>`<div class="${m.kind==='turn'?'sq-tbub':''}" style="padding:8px 10px;border-radius:8px;background:var(--app-bg);${m.reply_to?'margin-left:24px;':''}">`+
    `<div class=text-muted style="font-size:12px">#${m.id} · ${new Date(m.ts*1000).toLocaleString()} · <b>${escT(m.from)}</b> → ${escT((m.to||[]).join(', '))}`+
    `${m.reply_to?' · re #'+m.reply_to:''}${m.kind!=='post'?' · '+escT(m.kind):''} · depth ${m.depth}</div>`+
    sqText(m)+
    sqDelivery(m)+
    `</div>`).join(''):'<span class=text-muted>No messages yet. A message goes to the lead, or to @name / @all.</span>';
  th.scrollTop=th.scrollHeight; sqNotesLoad()}
  document.getElementById('sq-working').innerHTML=sqWorking(d.working)+sqQueued(d.queued);
  sqBoard();sqIndex();sqGit();
}
/* code index (mgr/indexer.py): the indexer VM, this project's last run, Reindex, Dashboard */
async function sqIndex(){
  const el=document.getElementById('sq-index');
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/index')).json()}catch(e){return}
  if(d.error||!el)return;
  if(!d.instance){el.innerHTML='<span class=text-muted style="font-size:12.5px">No indexer: create an instance from template <code>indexer</code>.</span>';return}
  const l=d.last||{}, sm='<span class=text-muted style="font-size:12.5px">';
  el.innerHTML=`<span class="tag ${d.running?'tag-accent':'tag-neutral'}">${escT(d.instance)} · ${d.running?'running':'stopped'}</span> `+
    (d.pending?sm+'reindex queued … </span>':'')+
    (l.ts?sm+`last index ${new Date(l.ts*1000).toLocaleString()} · ${l.ok?'ok':'failed'}${l.detail?' · '+escT(String(l.detail).slice(0,120)):''}${l.why?' · '+escT(l.why):''} · ${+l.seconds||0} s</span> `:sm+'not indexed yet </span>')+
    `<button class="btn btn-ghost btn-sm" onclick=sqReindex()>Reindex</button>`+
    (l.ok&&INDEX_HOST?` <a class="btn btn-ghost btn-sm" href="https://${INDEX_HOST}/indexer/open/${encodeURIComponent(sqName())}" target=_blank rel="noopener noreferrer" title="repowise dashboard on its own origin (starts the indexer)">Dashboard</a>`:'');
}
async function sqReindex(){
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/reindex',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json()}catch(e){d={error:'manager not reachable'}}
  document.getElementById('sqmsg').textContent=d.error||d.msg||'';
  sqIndex();
}
/* squad git remote (mgr/squadgit.py): settings, Fetch now, what the squad opened */
async function sqGit(){
  const el=document.getElementById('sq-git'); if(!el)return;
  let d={}; try{d=await (await fetch('/api/squad-git/'+encodeURIComponent(sqName()))).json()}catch(e){return}
  if(d.error){el.textContent=d.error;return}
  const r=d.remote||d.defaults||{}, on=!!d.remote, S=v=>String(v==null?'':v);
  const opt=(v,cur)=>`<option value="${esc(S(v))}"${v===cur?' selected':''}>${escT(S(v))}</option>`;
  const url=u=>/^https?:/i.test(S(u))?esc(S(u)):'#';
  el.innerHTML='<div class=grid2>'+
    `<div class=field><label>Provider</label><select class=input id=sg-provider>${(d.providers||[]).map(p=>opt(p,r.provider)).join('')}</select></div>`+
    `<div class=field><label>Repo URL (https)</label><input class="input mono" id=sg-url placeholder="https://github.com/owner/repo" value="${esc(S(r.url))}"></div>`+
    `<div class=field><label>Token secret (host store, never sent to a VM)</label><select class=input id=sg-token><option value="">— choose —</option>${(d.secrets||[]).map(x=>opt(x,r.token_secret)).join('')}</select></div>`+
    `<div class=field><label>Base branch</label><input class="input mono" id=sg-base value="${esc(S(r.base||'main'))}"></div>`+
    `<div class=field><label>Label (empty: none)</label><input class=input id=sg-label value="${esc(S(r.label))}"></div>`+
    `<div class=field><label>PRs + issues per member and day</label><input class=input type=number min=0 max=200 id=sg-limit value="${+r.daily_limit||0}"></div></div>`+
    '<div style="display:flex;gap:6px;align-items:center;margin:6px 0"><button class="btn btn-secondary btn-sm" onclick=sqGitSave()>Save remote</button>'+
    (on?'<button class="btn btn-ghost btn-sm" onclick=sqGitFetch()>Fetch now</button><button class="btn btn-ghost btn-sm" onclick=sqGitSave(true)>Remove</button>':'')+
    `<span class=text-muted style="font-size:12px" id=sg-msg>${on?'':'no remote set'}${d.strategy!=='worktree'?' · push and PRs need a worktree project':''}</span></div>`+
    ((d.created||[]).length?'<div style="display:grid;gap:2px;font-size:12.5px">'+d.created.map(c=>
      `<div class=text-muted>${new Date((+c.ts||0)*1000).toLocaleString()} · ${escT(S(c.by))} · ${c.kind==='pr'?'PR':'issue'} <a href="${url(c.url)}" target=_blank rel=noopener>#${+c.number||0}</a> ${escT(S(c.title))}</div>`).join('')+'</div>'
      :'<span class=text-muted style="font-size:12px">No PRs or issues opened by the squad yet.</span>');
}
async function sqGitSave(remove){
  if(remove&&!confirm('Remove the git remote of '+SQ+'? The token secret stays in the store.'))return;
  const v=id=>document.getElementById(id).value;
  const body=remove?{remove:true}:{provider:v('sg-provider'),url:v('sg-url').trim(),token_secret:v('sg-token'),base:v('sg-base').trim(),label:v('sg-label').trim(),daily_limit:+v('sg-limit')};
  let d={}; try{d=await (await fetch('/api/squad-git/'+encodeURIComponent(sqName()),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}catch(e){d={error:'manager not reachable'}}
  if(d.error){document.getElementById('sg-msg').textContent=d.error;return}
  await sqGit(); document.getElementById('sg-msg').textContent=remove?'removed ✓':'saved ✓';
}
async function sqGitFetch(){
  document.getElementById('sg-msg').textContent='fetching …';
  let d={}; try{d=await (await fetch('/api/squad-git/'+encodeURIComponent(sqName())+'/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json()}catch(e){d={error:'manager not reachable'}}
  document.getElementById('sg-msg').textContent=d.error||`fetched ${d.refs} branches into ${d.namespace} · base ${d.base||'?'}`;
}
async function sqSave(){
  const body={enabled:document.getElementById('sq-enabled').checked,paused:document.getElementById('sq-paused').checked,approved:document.getElementById('sq-approved').checked,cost_routing:document.getElementById('sq-cost_routing').checked,security_skills:document.getElementById('sq-security_skills').checked};
  ['max_wakes_per_hour','daily_token_budget','max_depth'].forEach(k=>body[k]=+document.getElementById('sq-'+k).value);
  body.autonomy=document.getElementById('sq-autonomy').value; body.approval_tools=document.getElementById('sq-approval_tools').value;
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}catch(e){d={error:'manager not reachable'}}
  document.getElementById('sqmsg').textContent=d.error||'saved ✓';
  if(!d.error){sqLoad();loadProjects()}
}
async function sqSend(){
  const el=document.getElementById('sq-text'), text=el.value.trim(); if(!text)return;
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/post',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})})).json()}catch(e){d={error:'manager not reachable'}}
  document.getElementById('sqmsg').textContent=d.error||'';
  if(!d.error){el.value='';sqLoad()}
}
/* squad gate: proposals the user approves, tickets (mgr/squadgate.py) */
let SQB={};
const SQ_KIND={squad:'Squad plan',member:'New member',merge:'Merge request'};
function sqSel(k,opts,cur,lab){return `<select class=input data-k=${k}${lab?` aria-label="${esc(lab)}"`:''}><option value="">—</option>`+
  opts.map(o=>`<option value="${esc(o)}"${o===cur?' selected':''}>${escT(o)}</option>`).join('')+'</select>'}
const SQ_X=`<button class="btn btn-ghost btn-sm" title="remove" aria-label="remove" onclick="this.closest('[data-t]').remove()">✕</button>`;
/* the repo profile the proposal was made with (mgr/repoprofile.py) */
function sqProfile(r){
  if(!r)return '';
  const row=(k,v)=>v?`<div><b>${k}</b> ${escT(v)}</div>`:'';
  return `<details style="margin:10px 0 0;font-size:12px"><summary class=text-muted>Repo profile — what the choice is based on</summary>`+
    row('keywords',(r.keywords||[]).join(', '))+row('languages',(r.languages||[]).map(x=>x[0]+' ('+x[1]+')').join(', '))+
    row('frameworks',(r.frameworks||[]).join(', '))+row('entry points',(r.entry_points||[]).join('; '))+
    row('main dirs',(r.main_dirs||[]).map(x=>x[0]+' ('+x[1]+')').join(', '))+row('README',r.readme)+
    (r.commit?`<div class=text-muted>commit ${escT(String(r.commit).slice(0,10))}</div>`:'')+'</details>';
}
/* who chose each field: architect, jev (score), you (an edit here), default — mgr/squadgate.py _chose */
const SQ_F={persona:'persona',new_persona:'new persona',skills:'skills',model_tier:'tier',model:'model',template:'template'};
const SQ_ROLE={lead:'Lead',writer:'Writer',reader:'Reader'};
const sqCap=s=>s?s[0].toUpperCase()+s.slice(1):'';
function sqBadge(v,empty){
  return v?`<span class=sq-by>${escT(v.startsWith('jev (')?'Jev '+v.slice(5,-1):sqCap(v))}</span>`
    :`<span class="sq-by${empty?' sq-empty':''}">${empty?'empty — choose':''}</span>`;
}
function sqRow(lab,fid,inner,badge){
  return `<div class=sq-row>${fid?`<label for="${fid}">${lab}</label>`:`<span class=sq-lab>${lab}</span>`}<div class=sq-fld>${inner}</div>${badge}</div>`;
}
function sqMark(row){const s=row&&row.querySelector('.sq-by');if(s){s.textContent='You';s.classList.remove('sq-empty')}}
function sqYou(e){
  const t=e.target; if(!t.dataset.k||t.dataset.k==='role')return;
  if(t.dataset.k==='persona'){const np=t.closest('.sq-row').querySelector('.sq-np');if(np)np.hidden=t.value!==np.dataset.n}
  if(t.dataset.k==='template'){const s=t.closest('[data-m]').querySelector('[data-k=model]');s.innerHTML=sqModelOpts(t.value,s.value,s.value.split("|")[1])}
  sqMark(t.closest('.sq-row'));
}
/* model: "<Tier> — <concrete model>"; value tier|model. The tier's first model is sent as tier only
   (the gate takes the first model of the tier), unless the proposal named it explicitly */
function sqModelOpts(tpl,cur,keep){
  const tm=SQB.tier_models||{}, ct=SQB.claude_tier||{}, out=[['','— choose a model']];
  (SQB.tiers||[]).forEach(t=>{const ms=tm[t]||[];
    if(tpl==='claude'){const a=ms.find(x=>x.startsWith('anthropic/'));out.push([t+'|',sqCap(t)+' — '+(a?a.slice(10):ct[t]||'account default')])}
    else if(!ms.length)out.push([t+'|',sqCap(t)+' — no model in the Router (template default)']);
    else ms.forEach((x,i)=>out.push([t+'|'+(i||x===keep?x:''),sqCap(t)+' — '+x]));
  });
  if(cur&&!out.some(o=>o[0]===cur)){const [t,x]=cur.split('|');out.push([cur,(sqCap(t)||'Custom')+' — '+(x||'from the tier')])}
  return out.map(([v,l])=>`<option value="${esc(v)}"${v===cur?' selected':''}>${escT(l)}</option>`).join('');
}
function sqChip(n){return `<span class=sq-chip data-s="${esc(n)}">${escT(n)}<button type=button aria-label="remove skill ${esc(n)}" onclick="sqSkill(this)">×</button></span>`}
function sqSkill(el,add){
  const row=el.closest('.sq-row'), box=row.querySelector('.sq-chips'), inp=row.querySelector('.sq-add');
  if(add===undefined){el.closest('.sq-chip').remove();inp.focus()}
  else{
    add=add.trim(); if(!SKILLS.some(s=>s.name===add)){if(add)inp.select();return}
    if(![...box.querySelectorAll('.sq-chip')].some(c=>c.dataset.s===add))inp.insertAdjacentHTML('beforebegin',sqChip(add));
    if(el===inp)inp.value=''; else el.remove();
  }
  sqMark(row);
}
function sqPick(el){   /* a Jev hint (persona or tier) the user applies */
  const s=el.closest('.sq-row').querySelector('select'), v=el.dataset.v;
  if(![...s.options].some(o=>o.value===v))s.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${escT(v)}</option>`);
  s.value=v; s.dispatchEvent(new Event('change',{bubbles:true})); el.remove();
}
function sqSkl(){   /* one datalist for every "+ add" field: the catalog without the standard skills */
  if(document.getElementById('sq-skl'))return;
  document.body.insertAdjacentHTML('beforeend','<datalist id=sq-skl>'+SKILLS.filter(s=>!s.default&&s.category!=='security').map(s=>`<option value="${esc(s.name)}">${escT(s.description)}</option>`).join('')+'</datalist>');
}
function sqNewPersona(n,shown){
  if(!n)return '';
  return `<div class=sq-np data-n="${esc(n.name)}"${shown?'':' hidden'}><span class=text-muted style="font-size:12px">New persona — created only on Approve, never over an existing one</span>`+
    `<input class=input data-k=np_name value="${esc(n.name)}" placeholder="name (a-z 0-9 -)" aria-label="new persona name">`+
    `<input class=input data-k=np_desc value="${esc(n.description)}" placeholder="one-line description" aria-label="new persona description">`+
    `<textarea class=input data-k=np_prompt rows=4 placeholder="system prompt" aria-label="new persona system prompt">${escT(n.prompt)}</textarea>`+
    `<input class=input data-k=np_tools value="${esc((n.tools||[]).join(', '))}" placeholder="tools (empty = all)" aria-label="new persona tools"></div>`;
}
function sqMem(p,m){
  const nm=esc(m.name), b=m.by||{}, h=(m.suggested||{}).hint||{}, f=v=>(+v).toFixed(2), fid=esc('sq-'+p.id+'-'+m.name+'-');
  const rm=`<button class="btn btn-ghost btn-sm" aria-label="remove member ${nm}" title="remove" onclick="this.closest('[data-m]').remove()">✕</button>`;
  if(m.existing)return `<div class="sq-mem sq-old" data-m="${nm}"><b>${escT(m.name)}</b><span class="tag tag-neutral">EXISTING</span>`+
    `<span class=text-muted>${escT(SQ_ROLE[m.role]||m.role)}${m.persona?' · '+escT(m.persona):''}</span><span class=sq-sp></span>${rm}</div>`;
  const pers=[...new Set(PERSONAS.map(x=>x.name).concat(p.members.map(x=>x.persona).filter(Boolean)))];
  const ghost=(v,txt,fn)=>`<button type=button class=sq-ghost data-v="${esc(v)}" onclick="${fn}">+ ${escT(txt)}</button>`;
  const hk=Object.entries(h.skills||{}).filter(([n])=>!(m.skills||[]).includes(n));
  const cur=m.model_tier||m.model?(m.model_tier||'')+'|'+(m.model||''):'', tpl=m.template||'openrouter';
  const pnew=!!(m.new_persona&&m.persona===m.new_persona.name);
  return `<div class=sq-mem data-m="${nm}" onchange="sqYou(event)"><div class=sq-mh><b>${escT(m.name)}</b><span class="tag tag-accent">NEW</span>`+
    `<select class=sq-role data-k=role aria-label="role of ${nm}">`+Object.entries(SQ_ROLE).map(([v,l])=>`<option value=${v}${v===m.role?' selected':''}>${l}</option>`).join('')+'</select>'+
    `<span class=sq-sp></span><button class="btn btn-ghost btn-sm" onclick="sqSuggest('${esc(p.id)}','${nm}',this)">Suggest again</button>${rm}</div>`+
    (m.purpose?`<div class=sq-purp>${escT(m.purpose)}</div>`:'')+
    sqRow('Persona',fid+'p',`<select class=input id="${fid}p" data-k=persona><option value="">— choose a persona</option>`+
      pers.map(o=>`<option value="${esc(o)}"${o===m.persona?' selected':''}>${escT(o)}${m.new_persona&&o===m.new_persona.name?' (new)':''}</option>`).join('')+'</select>'+
      (h.persona?`<div class=sq-hint><span class=text-muted>Hint:</span>${ghost(h.persona.name,h.persona.name+' '+f(h.persona.score),'sqPick(this)')}</div>`:'')+
      sqNewPersona(m.new_persona,pnew),
      sqBadge(b.persona||b.new_persona,!m.persona&&!m.new_persona))+
    sqRow('Skills',fid+'s',`<div class=sq-chips>${(m.skills||[]).map(sqChip).join('')}`+
      `<input class="input sq-add" id="${fid}s" list=sq-skl placeholder="+ add skill" autocomplete=off onchange="event.stopPropagation();sqSkill(this,this.value)" `+
      `onkeydown="if(event.key==='Enter'){event.preventDefault();sqSkill(this,this.value)}"></div>`+
      (hk.length?`<div class=sq-hint><span class=text-muted>Hints:</span>${hk.map(([n,v])=>ghost(n,n+' '+f(v),'sqSkill(this,this.dataset.v)')).join('')}</div>`:''),
      sqBadge(b.skills,!(m.skills||[]).length))+
    sqRow('Model',fid+'m',`<select class=input id="${fid}m" data-k=model>${sqModelOpts(tpl,cur,m.model)}</select>`+
      (h.tier?`<div class=sq-hint><span class=text-muted>Hint:</span>${ghost(h.tier.name+'|',sqCap(h.tier.name)+' '+f(h.tier.score),'sqPick(this)')}</div>`:''),
      sqBadge(b.model||b.model_tier,!m.model_tier&&!m.model))+
    sqRow('Runs on','',`<div class=sq-seg role=radiogroup aria-label="runs on" title="claude: Claude Code on your subscription, the tier picks haiku/sonnet/opus; openrouter: API model, counts against the token budget">`+
      [['openrouter','OpenRouter'],['claude','Claude']].map(([v,l])=>`<label><input type=radio name="${fid}t" data-k=template value=${v}${v===tpl?' checked':''}>${l}</label>`).join('')+'</div>',
      sqBadge(b.template))+'</div>';
}
async function sqSuggest(id,name,btn){
  btn.disabled=true; btn.textContent='asking Jev…';
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,member:name})})).json()}catch(e){d={error:'manager not reachable'}}
  const p=(SQB.proposals||[]).find(x=>x.id===id), m=(d.members||[]).find(x=>x.name===name);
  if(d.error||!p||!m){btn.disabled=false;btn.textContent='Suggest again';document.getElementById('sqmsg').textContent=d.error||'';return}
  p.members=p.members.map(x=>x.name===name?m:x);
  btn.closest('[data-m]').outerHTML=sqMem(p,m);         // only this member: the others keep the user's edits
}
const sqNames=p=>[...new Set(p.members.map(m=>m.name).concat((SQB.members||[]).map(m=>m.name)))];
function sqTk(t,names){
  const k=esc(t.key);
  return `<div class=sq-tk data-t="${k}"><span class="mono sq-key">${escT(t.key)}</span>`+
    `<input class=input data-k=title value="${esc(t.title)}" placeholder="title" aria-label="title of ${k}">${sqSel('assignee',names,t.assignee,'assignee of '+t.key)}`+
    `<input class="input sq-dep" data-k=deps value="${esc((t.depends_on||[]).join(', '))}" placeholder="after keys" title="depends on (ticket keys, comma-separated)" aria-label="depends on, for ${k}">`+SQ_X+
    `<details class=sq-tkd${t.open?' open':''}><summary class=text-muted>Description${t.description?'':' (empty)'}</summary>`+
    `<textarea class=input data-k=desc rows=3 aria-label="description of ${k}">${escT(t.description||'')}</textarea></details></div>`;
}
function sqTicketRows(p){
  return `<div class=sq-sub>Tickets</div><div class=sq-tks>`+p.tickets.map(t=>sqTk(t,sqNames(p))).join('')+'</div>'+
    `<button type=button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="sqAddTk('${esc(p.id)}',this)">+ Ticket</button>`;
}
function sqAddTk(id,btn){   /* key auto: the first free T<n> */
  const p=(SQB.proposals||[]).find(x=>x.id===id), box=btn.previousElementSibling;
  const have=new Set([...box.querySelectorAll('[data-t]')].map(r=>r.dataset.t).concat(p.tickets.map(t=>t.key),(SQB.tickets||[]).map(t=>t.key)));
  let n=1; while(have.has('T'+n))n++;
  box.insertAdjacentHTML('beforeend',sqTk({key:'T'+n,title:'',description:'',assignee:'',depends_on:[],open:true},sqNames(p)));
  box.lastElementChild.querySelector('[data-k=title]').focus();
}
/* a revised proposal: what differs from the previous version, and the operator's comments */
function sqHistory(p){
  const v=p.version||1, ch=p.changes||[], cs=p.comments||[];
  return (v>1?`<details class=sq-chg><summary>Changes from v${v-1} (${ch.length})</summary>`+
      (ch.length?'<ul>'+ch.map(x=>`<li>${escT(x)}</li>`).join('')+'</ul>':'<div class=text-muted>no differences</div>')+'</details>':'')+
    (cs.length?`<details class=sq-chg${p.status==='changes_requested'?' open':''}><summary>Comments (${cs.length})</summary>`+
      cs.map(c=>`<div class=sq-cmt><div class=text-muted>${escT(c.by)} · v${+c.version||1} · ${new Date(c.ts*1000).toLocaleString()}</div>`+
        `<div style="white-space:pre-wrap">${escT(c.text)}</div>`+
        ((c.edits||[]).length?'<ul class=text-muted>'+c.edits.map(x=>`<li>${escT(x)}</li>`).join('')+'</ul>':'')+'</div>').join('')+'</details>':'');
}
function sqCard(p){
  sqSkl();
  const id=esc(p.id);
  let body=(p.warnings||[]).map(w=>`<div style="font-size:12px;color:var(--color-warn)">⚠ ${escT(w)}</div>`).join('');
  if(p.kind==='merge')body+=`<p style="white-space:pre-wrap">${escT(p.summary)}</p><button class="btn btn-ghost btn-sm" onclick="pjReview('${esc(SQ)}','${esc(p.writer)}')">Diff of ${escT(p.writer)}</button>`;
  else body+=sqHistory(p)+(p.kind==='squad'?`<div class=sq-sub>Goal</div><textarea class="input sq-goal" data-k=goal rows=3 aria-label="squad goal">${escT(p.goal||'')}</textarea>`
    :p.goal?`<p style="white-space:pre-wrap;margin:8px 0">${escT(p.goal)}</p>`:'')+p.members.map(m=>sqMem(p,m)).join('')+(p.kind==='squad'?sqTicketRows(p)+
    `<div class=sq-row style="margin-top:10px"><label for="sqb-${id}">Budget</label><div class=sq-fld style="display:flex;gap:8px;align-items:center">`+
    `<input class=input id="sqb-${id}" type=number min=0 data-k=budget value="${+p.budget_tokens_per_day||0}" style="max-width:180px"><span class=text-muted style="font-size:12px">tokens a day, all members</span></div></div>`:'')+
    sqProfile(p.profile);
  return `<div id="sqp-${id}" class=sq-card><div class=sq-head><b>${SQ_KIND[p.kind]||escT(p.kind)}${(p.version||1)>1?` <span class="tag tag-accent">v${+p.version}</span>`:''}${p.status==='changes_requested'?' <span class="tag tag-neutral">changes requested</span>':''}</b><span class=text-muted style="font-size:12px">${id} · by ${escT(p.by)} · ${new Date(p.created*1000).toLocaleString()}</span></div>`+
    body+`<div class=sq-foot><input class=input data-k=note placeholder="Note to the lead (required for Request changes)" aria-label="note to the lead">`+
    `<button class="btn btn-secondary" onclick="sqDecide('${id}',false)">Reject</button>`+
    (p.kind!=='merge'?`<button class="btn btn-secondary" onclick="sqDecide('${id}',false,true)" title="the note and your edits go to the lead; it sends a new version">Request changes</button>`:'')+
    `<button class="btn btn-primary" onclick="sqDecide('${id}',true)">Approve</button></div>`+
    `<div class=sq-result role=status aria-live=polite style="font-size:12.5px;margin-top:6px"></div></div>`;
}
function sqDone(p){
  const bad=(p.steps||[]).some(s=>!s.ok);
  return `<div style="font-size:12.5px;margin:6px 0"><b>${SQ_KIND[p.kind]||escT(p.kind)}</b> ${escT(p.id)} · ${escT(p.status)} by ${escT(p.decided_by)}${p.note?' · '+escT(p.note):''}`+
    (p.members||[]).filter(m=>!m.existing).map(m=>`<div class=text-muted>${escT(m.name)} (${escT(m.template)}) — `+
      Object.keys(SQ_F).filter(k=>(m.by||{})[k]).map(k=>SQ_F[k]+': '+escT(m.by[k])).join(' · ')+'</div>').join('')+
    (p.steps||[]).map(s=>`<div ${s.ok?'class=text-muted':'style="color:var(--color-warn)"'}>${s.ok?'✓':'✗'} ${escT(s.step)}: ${escT(s.detail)}</div>`).join('')+
    (bad?`<button class="btn btn-ghost btn-sm" onclick="sqDecide('${esc(p.id)}',true)">Retry the failed steps</button>`:'')+'</div>';
}
function sqCollect(p,c){
  const v=(r,k)=>{const e=r.querySelector('[data-k='+k+']');return e?e.value:''}, out={};
  const list=x=>x.split(',').map(s=>s.trim()).filter(Boolean);
  out.members=[...c.querySelectorAll('[data-m]')].map(r=>{
    const b=p.members.find(m=>m.name===r.dataset.m);
    if(!b)return null;
    if(b.existing)return {...b};
    const [tier,model]=(v(r,'model')||'|').split('|'), t=r.querySelector('[data-k=template]:checked');
    const o={...b,role:v(r,'role'),persona:v(r,'persona'),skills:[...r.querySelectorAll('.sq-chip')].map(x=>x.dataset.s),
      model_tier:tier,model:model||'',template:t?t.value:b.template};
    delete o.new_persona;               /* kept only while the persona select still names it */
    if(b.new_persona&&r.querySelector('[data-k=np_name]')&&o.persona===b.new_persona.name){
      o.new_persona={name:v(r,'np_name').trim(),description:v(r,'np_desc'),prompt:v(r,'np_prompt'),tools:list(v(r,'np_tools'))};
      o.persona=o.new_persona.name;
    }
    return o;
  }).filter(Boolean);
  if(p.kind==='squad'){
    out.goal=v(c,'goal');
    out.tickets=[...c.querySelectorAll('[data-t]')].map(r=>({key:r.dataset.t,title:v(r,'title'),description:v(r,'desc'),assignee:v(r,'assignee'),depends_on:list(v(r,'deps'))}));
    out.budget_tokens_per_day=+(v(c,'budget')||0)||p.budget_tokens_per_day;
  }
  return out;
}
const sqOpenP=p=>p.status==='pending'||p.status==='changes_requested';
async function sqBoard(){
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/board')).json()}catch(e){return}
  if(d.error)return;
  SQB=d;
  const ps=d.proposals||[], pend=ps.filter(sqOpenP), done=ps.filter(p=>!sqOpenP(p)).slice(0,10);
  document.getElementById('sq-gate').innerHTML=(d.approved?'':'<p class=text-muted style="font-size:12.5px;margin:0">No approved plan yet: only the lead can be woken. It proposes with squad_propose.</p>')+
    pend.map(sqCard).join('')+(done.length?`<details><summary class=text-muted style="font-size:12.5px">Decided (${done.length})</summary>${done.map(sqDone).join('')}</details>`:'');
  const cols=d.statuses||[], ts=d.tickets||[], mem=(d.members||[]).map(m=>m.name);
  document.getElementById('sq-board').innerHTML=ts.length?'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px">'+
    cols.map(c=>`<div><div class=text-muted style="font-size:12px;margin-bottom:4px">${c} · ${ts.filter(t=>t.status===c).length}</div>`+
      ts.filter(t=>t.status===c).map(t=>`<div style="padding:8px;border-radius:8px;background:var(--app-bg);margin-bottom:6px;font-size:12.5px">`+
        `<b>${escT(t.key)}</b> ${escT(t.title)}`+(t.depends_on.length?`<div class=text-muted>after ${escT(t.depends_on.join(', '))}</div>`:'')+
        (t.comments.length?`<div class=text-muted title="${esc(t.comments[t.comments.length-1].text)}">${t.comments.length} comment(s), last by ${escT(t.comments[t.comments.length-1].by)}</div>`:'')+
        `<div style="display:flex;gap:4px;margin-top:6px"><select class=input style="font-size:12px;padding:2px 4px" onchange="sqTicket(${+t.id},{status:this.value})">`+
        cols.map(s=>`<option${s===t.status?' selected':''}>${s}</option>`).join('')+'</select>'+
        `<select class=input style="font-size:12px;padding:2px 4px" onchange="sqTicket(${+t.id},{assignee:this.value})"><option value="">—</option>`+
        mem.map(n=>`<option value="${esc(n)}"${n===t.assignee?' selected':''}>${escT(n)}</option>`).join('')+'</select>'+
        `<select class=input style="font-size:12px;padding:2px 4px" title="level for cost routing (${esc(t.level_by||'')})" onchange="sqTicket(${+t.id},{level:+this.value})">`+
        [1,2,3,4,5].map(n=>`<option value=${n}${n===(t.level||3)?' selected':''}>L${n}</option>`).join('')+'</select></div></div>').join('')+'</div>').join('')+'</div>'
    :'<span class=text-muted style="font-size:12.5px">No tickets yet.</span>';
}
async function sqDecide(id,ok,changes){
  /* Approve / Reject / Request changes. The buttons are locked while the manager works (an approval
     creates the members' VMs — that can take a minute) and the outcome or any error is shown IN the
     card: before, an error inside the browser stopped it silently ("nothing happens"). */
  const c=document.getElementById('sqp-'+id), sk=location.hash==='#squads';
  const out=c&&c.querySelector('.sq-result'), btns=c?[...c.querySelectorAll('button')]:[];
  const say=(t,bad)=>{if(out){out.textContent=t;out.style.color=bad?'var(--color-warn)':''}
    const g=document.getElementById(sk?'sk-msg':'sqmsg'); if(g)g.textContent=t;};
  try{
    const p=(SQB.proposals||[]).find(x=>x.id===id);
    const note=c?c.querySelector('[data-k=note]'):null;
    if(changes&&note&&!note.value.trim()){say('Request changes: write in the note what should change.',true);note.focus();return}
    if(!ok&&!changes&&!confirm('Reject this proposal? The note goes to the lead.'))return;
    const body={id,approve:ok,changes:!!changes,note:note?note.value:''};
    if((ok||changes)&&c&&p&&p.kind!=='merge')Object.assign(body,sqCollect(p,c));
    const proj=(p&&p.project)||sqName();
    if(!proj){say('⚠️ no project selected — reload the page and pick the squad again.',true);return}
    btns.forEach(b=>b.disabled=true);
    say(ok?'Approving… (creating the members can take a minute)':changes?'Sending to the lead…':'Rejecting…');
    let d={};
    try{const r=await fetch('/api/projects/'+encodeURIComponent(proj)+'/squad/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      try{d=await r.json()}catch(e){d={error:'HTTP '+r.status}}
      if(!r.ok&&!d.error)d.error='HTTP '+r.status;}
    catch(e){d={error:'manager not reachable'}}
    if(d.error){btns.forEach(b=>b.disabled=false);say('⚠️ '+d.error,true);return}
    const bad=(d.steps||[]).filter(s=>!s.ok);
    say(d.status+(bad.length?' — failed: '+bad.map(s=>s.step+': '+s.detail).join('; '):' ✓'),bad.length>0);
    if(sk){SK_PEND='';skLoad();loadSquads()}else{sqLoad();loadProjects()}
  }catch(e){btns.forEach(b=>b.disabled=false);say('⚠️ '+(e&&e.message||e)+' — nothing was sent',true)}
}
async function sqTicket(id,change){
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,...change})})).json()}catch(e){d={error:'manager not reachable'}}
  document.getElementById('sqmsg').textContent=d.error||'';
  sqLoad();
}
/* — Squads: one chat per squad; a kickoff from one line (mgr/squadkick.py) — */
let SK='', SK_JOB='', SK_LIST=[], SK_LAST=-1, SK_PEND='', SK_POLL=false, SK_MEMBERS=[], SK_AT=0;
/* @-mentions in the composer: typing @ lists the squad's members (+ all); arrows/Enter/Tab pick, Esc closes. */
function skAtWord(){const i=document.getElementById('sk-text'), pre=i.value.slice(0,i.selectionStart), m=pre.match(/(^|\s)@([\w.-]*)$/);return m?m[2]:null}
function skAtClose(){const a=document.getElementById('sk-at');if(a){a.hidden=true;document.getElementById('sk-text').setAttribute('aria-expanded','false')}}
function skAt(){
  const a=document.getElementById('sk-at'), w=skAtWord();
  if(w===null||!SK){skAtClose();return}
  const opts=[...SK_MEMBERS,{name:'all',role:'everyone'}].filter(m=>m.name.toLowerCase().startsWith(w.toLowerCase()));
  if(!opts.length){skAtClose();return}
  SK_AT=Math.min(SK_AT,opts.length-1);
  a.innerHTML=opts.map((m,i)=>`<div role=option data-n="${esc(m.name)}" aria-selected=${i===SK_AT} onmousedown="event.preventDefault();skAtPick('${esc(m.name)}')">@${escT(m.name)}<small>${escT(m.role||'')}</small></div>`).join('');
  a.hidden=false; document.getElementById('sk-text').setAttribute('aria-expanded','true');
}
function skAtPick(n){
  const i=document.getElementById('sk-text'), pos=i.selectionStart, pre=i.value.slice(0,pos).replace(/@([\w.-]*)$/,'@'+n+' ');
  i.value=pre+i.value.slice(pos); i.setSelectionRange(pre.length,pre.length); i.focus(); SK_AT=0; skAtClose();
}
function skAtKey(e){
  const a=document.getElementById('sk-at'), open=a&&!a.hidden, items=open?[...a.querySelectorAll('[role=option]')]:[];
  if(open&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();SK_AT=(SK_AT+(e.key==='ArrowDown'?1:items.length-1))%items.length;skAt();return}
  if(open&&(e.key==='Enter'||e.key==='Tab')&&items[SK_AT]){e.preventDefault();skAtPick(items[SK_AT].dataset.n);return}
  if(open&&e.key==='Escape'){e.preventDefault();skAtClose();return}
  if(e.key==='Enter')skSend();
}
const SK_SEEN=(()=>{try{return JSON.parse(localStorage.getItem('sk-seen')||'{}')}catch(e){return {}}})();
function skSeen(n,id){SK_SEEN[n]=id;try{localStorage.setItem('sk-seen',JSON.stringify(SK_SEEN))}catch(e){}}
const SK_TAG={running:'tag-accent',paused:'tag-neutral','waiting for approval':'tag-neutral',failed:'tag-neutral'};
function skMsg(t){document.getElementById('sk-msg').textContent=t||''}
async function loadSquads(){
  let d={}; try{d=await (await fetch('/api/squads')).json()}catch(e){return}
  SK_LIST=d.squads||[];
  let jobs=[]; try{jobs=((await (await fetch('/api/squads/kickoff')).json()).jobs||[]).filter(j=>j.status!=='done')}catch(e){}
  const item=(attr,cur,title,sub)=>`<button class=sk-item ${attr}${cur?' aria-current=true':''}>${title}<small>${sub}</small></button>`;
  document.getElementById('sk-list').innerHTML=
    item('data-new=1',!SK&&!SK_JOB,'<b>+ New squad</b>','')+
    jobs.map(j=>item(`data-job="${esc(j.id)}"`,SK_JOB===j.id,escT(j.name),'kickoff · '+escT(j.status))).join('')+
    SK_LIST.map(s=>item(`data-n="${esc(s.name)}"`,SK===s.name,
      (s.waiting_for_you?'<span class=sk-qdot aria-hidden=true></span>':s.working?'<span class=sk-live aria-hidden=true></span>':'')+
      escT(s.name)+(s.last_id>(SK_SEEN[s.name]||0)&&SK!==s.name?' <span class="tag tag-accent">new</span>':''),
      s.waiting_for_you?'❓ wartet auf dich':s.working?`${+s.working} arbeiten · ${+s.tickets_done}/${+s.tickets_total}`   /* live state (mgr/squadkick.overview) */
        :escT(s.status)+(s.pending?' · '+(+s.pending)+' to approve':''))).join('');
  if(!SK&&!SK_JOB)skNew();
}
document.getElementById('sk-list').onclick=e=>{
  const b=e.target.closest('.sk-item'); if(!b)return;
  if(b.dataset.n)skPick(b.dataset.n); else if(b.dataset.job)skJob(b.dataset.job); else {SK='';SK_JOB='';skNew()}
  loadSquads();
};
function skNew(){
  document.getElementById('sk-title').textContent='New squad';
  document.getElementById('sk-status').innerHTML=''; document.getElementById('sk-acts').innerHTML='';
  document.getElementById('sk-board').hidden=true; document.getElementById('sk-cards').innerHTML='';
  document.getElementById('sk-thread').innerHTML='<div class=sk-bub>Paste the repository URL and say what the squad should do, e.g. <code>https://github.com/owner/repo add a dark mode with tests</code>. '+
    'The repo is cloned on the host, a project and an architect are set up, and the architect proposes the squad here for your approval. Private repos need the host secret <code>SQUAD_GIT_TOKEN_GITHUB</code> (or <code>SQUAD_GIT_TOKEN_&lt;HOST&gt;</code> for Gitea).</div>';
  document.getElementById('sk-text').placeholder='https://github.com/owner/repo — the goal';
  document.getElementById('sk-arch').hidden=false;
  skMsg('');
}
async function skJob(id){
  SK='';SK_JOB=id; document.getElementById('sk-board').hidden=true;
  let j={}; try{j=await (await fetch('/api/squads/kickoff/'+encodeURIComponent(id))).json()}catch(e){return}
  if(j.error){skMsg(j.error);return}
  document.getElementById('sk-title').textContent='Kickoff · '+j.name;
  document.getElementById('sk-arch').hidden=false;
  document.getElementById('sk-status').innerHTML=`<span class="tag ${SK_TAG[j.status]||'tag-neutral'}">${escT(j.status)}</span>`;
  document.getElementById('sk-acts').innerHTML=j.status==='failed'?'<button class="btn btn-secondary btn-sm" onclick=skRetry()>Retry</button>'
    :j.status==='done'?`<button class="btn btn-primary btn-sm" data-open="${esc(j.name)}" onclick="skPick(this.dataset.open);loadSquads()">Open squad</button>`:'';
  document.getElementById('sk-cards').innerHTML='';
  const th=document.getElementById('sk-thread');
  th.innerHTML=`<div class="sk-bub me"><div class=text-muted style="font-size:12px">${escT(j.who)}</div>${escT(j.url)} ${escT(j.goal)}</div>`+
    (j.log||[]).map(l=>`<div class="sk-bub${l.ok?'':' bad'}"><div class=text-muted style="font-size:12px">kickoff · ${new Date(l.ts*1000).toLocaleTimeString()}</div>${escT(l.text)}</div>`).join('');
  th.scrollTop=th.scrollHeight;
  if(j.status==='running'&&!SK_POLL){SK_POLL=true;setTimeout(()=>{SK_POLL=false;if(SK_JOB===id&&location.hash==='#squads')skJob(id)},1500)}
  if(j.status==='done'&&SK_JOB===id){SK_JOB='';skPick(j.name);loadSquads()}
}
async function skRetry(){
  let d={}; try{d=await (await fetch('/api/squads/kickoff/'+encodeURIComponent(SK_JOB)+'/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json()}catch(e){d={error:'manager not reachable'}}
  skMsg(d.error||''); if(!d.error)skJob(SK_JOB);
}
function skPick(name){
  SK=name;SK_JOB='';SQ=name;SK_LAST=-1;SK_PEND=''; if(SKT)skTkClose();
  document.getElementById('sk-status').dataset.h='';
  document.getElementById('sk-text').placeholder='message the lead, or @member / @all …';
  document.getElementById('sk-arch').hidden=true;
  document.getElementById('sk-board').hidden=true; SKB=''; skMsg(''); skLoad();
}
async function skLoad(){
  if(!SK)return;
  const name=SK, base='/api/projects/'+encodeURIComponent(name)+'/squad';
  let d={},b={}; try{d=await (await fetch(base+'/messages')).json();b=await (await fetch(base+'/board')).json()}catch(e){skMsg('manager not reachable');return}
  if(SK!==name)return;
  if(d.error){skMsg(d.error);return}
  SK_MEMBERS=(b.members||[]).filter(m=>m.name!=='indexer').map(m=>({name:m.name,role:m.role||''}));
  const st=d.settings||{}, s=SK_LIST.find(x=>x.name===name)||{};
  const status=st.paused?'paused':st.approved?'running':'waiting for approval';
  document.getElementById('sk-title').textContent=name;
  const cp='onclick="navigator.clipboard&&navigator.clipboard.writeText(this.textContent)" style="cursor:pointer;font-size:11px"';
  const stH=`<span class="tag ${SK_TAG[status]}">${status}</span>`+(s.lead?` <span class=text-muted style="font-size:12px">lead ${escT(s.lead)}</span>`:'')+
    (s.id?` <code class=text-muted title="Squad ID — click to copy" ${cp}>${escT(s.id)}</code>`+
      ` <details style="display:inline-block;position:relative;font-size:12px"><summary class=text-muted style="cursor:pointer">members · folder</summary>`+
      `<div class=sk-bub style="position:absolute;z-index:5;max-width:none;white-space:nowrap;margin-top:4px">`+
      (s.members_ids||[]).map(m=>`${escT(m.name)} <span class=text-muted>${escT(m.role)}</span> <code title="Member ID — click to copy" ${cp}>${escT(m.member_id)}</code>`).join('<br>')+
      `<div class=text-muted style="margin-top:6px" title="Archive: squad.json, conversations, proposals, tickets.jsonl">Folder <code ${cp}>${escT(s.folder)}</code></div></div></details>`:'');
  const stEl=document.getElementById('sk-status');
  if(stEl.dataset.h!==stH){stEl.dataset.h=stH;stEl.innerHTML=stH}   /* an open members list stays open */
  document.getElementById('sk-acts').innerHTML=`<button class="btn btn-ghost btn-sm" onclick=skChat() title="To the chat and the composer">Chat</button>`+
    `<button class="btn btn-ghost btn-sm" onclick=skPause(${st.paused?'false':'true'})>${st.paused?'Resume':'Pause'}</button>`+
    `<button class="btn btn-ghost btn-sm" onclick=skRestart() title="Stop + start every member (one busy with a turn: right after it) — picks up new images, tools and config">Restart members</button>`+
    (INDEX_HOST?`<a class="btn btn-ghost btn-sm" href="https://${INDEX_HOST}/indexer/open/${encodeURIComponent(name)}" target=_blank rel="noopener noreferrer">Dashboard</a>`:'')+
    `<button class="btn btn-ghost btn-sm" onclick=skProject()>Project</button>`;
  const msgs=d.messages||[], last=msgs.length?msgs[msgs.length-1].id:0;
  if(last!==SK_LAST&&!sqaBusy(document.getElementById('sk-thread'))){
    SK_LAST=last; skSeen(name,last);
    const th=document.getElementById('sk-thread');
    th.innerHTML=msgs.length?msgs.map(m=>`<div class="sk-bub${String(m.from).startsWith('user:')?' me':''}${m.kind==='turn'?' sq-tbub':''}${m.blocked||Object.keys(m.failed||{}).length?' bad':''}">`+
      `<div class=text-muted style="font-size:12px"><b>${escT(m.from)}</b> → ${escT((m.to||[]).join(', '))} · ${new Date(m.ts*1000).toLocaleString()}${m.kind!=='post'?' · '+escT(m.kind):''}</div>`+
      sqText(m)+
      sqDelivery(m)+'</div>').join('')
      :'<span class=text-muted>No messages yet.</span>';
    th.scrollTop=th.scrollHeight; sqNotesLoad();
  }
  document.getElementById('sk-working').innerHTML=sqWorking(d.working)+sqQueued(d.queued);
  if(b.error)return;
  SQB=b;
  await loadWaiting(); if(SK!==name)return;
  const hq=WAIT.filter(x=>x.project===name);                      /* approvals, questions, reviews (mgr/hitlq.py) */
  const pend=(b.proposals||[]).filter(sqOpenP), key=pend.map(p=>p.id+':'+(p.version||1)+':'+p.status+':'+JSON.stringify(p.members.map(m=>m.suggested||0))).join()+'|'+hq.map(x=>x.id).join();
  if(key!==SK_PEND){SK_PEND=key;document.getElementById('sk-cards').innerHTML=pend.map(sqCard).join('')+hq.map(hqCard).join('')}   /* never under the user's edits */
  await skMetrics(name); if(SK!==name)return;
  SKB_D={d,b}; skBoardRender(d,b,hq); if(SKT)skTkRender();
}
/* — Squads board: team row, summary, Kanban Open · Doing · Review · Blocked · Done; click = ticket, drag = status — */
let SKB='', SKB_DRAG=false, SKB_D={d:{},b:{}}, SKM={name:'',at:0,ev:[],sum:{}}, SKT=0, SKT_EDIT=false;
const KB_COLS=[['open','Open','keine offenen Tickets'],['doing','Doing','niemand arbeitet gerade'],['review','Review','nichts zur Prüfung'],
  ['blocked','Blocked','keine Rückfrage offen'],['done','Done','noch nichts erledigt']];
const kbMmss=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
const kbAv=(n,on)=>`<span class="kb-av${on?'':' idle'}" aria-hidden=true>${escT(String(n||'?').charAt(0).toUpperCase())}</span>`;
const kbTok=n=>n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n);
function kbWhen(ts){
  if(!ts)return '';
  const d=new Date(ts*1000), t=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',hourCycle:'h23'}),
    days=Math.round((new Date().setHours(0,0,0,0)-new Date(ts*1000).setHours(0,0,0,0))/864e5);
  return days===0?t:days===1?'yesterday '+t:d.toLocaleDateString();
}
async function skMetrics(name){       /* turns of the last 12 h + today's cost/tokens (mgr/squadmetrics.py); at most once a minute */
  if(SKM.name===name&&Date.now()-SKM.at<60000)return;
  const base='/api/squads/'+encodeURIComponent(name)+'/metrics/', now=Math.floor(Date.now()/1000), mid=Math.floor(new Date().setHours(0,0,0,0)/1000);
  let ev=[],sum={};
  try{ev=(await (await fetch(base+'events?kind=turn_end&since='+(now-12*3600))).json()).events||[];
    sum=await (await fetch(base+'summary?since='+mid)).json()}catch(e){}
  SKM={name,at:Date.now(),ev,sum};
}
function skBoardRender(d,b,hq){
  const el=document.getElementById('sk-board'), ts=b.tickets||[], ws=b.working||[], msgs=d.messages||[], now=Date.now()/1000;
  const done=new Set(ts.filter(t=>t.status==='done').map(t=>t.key));
  const deps=t=>t.status==='done'?[]:(t.depends_on||[]).filter(k=>!done.has(k));
  const on={};      /* ticket key -> the working turn on it: the waking message names the ticket, else the member's doing ticket */
  ws.filter(w=>w.state==='working').forEach(w=>{
    const m=msgs.find(x=>x.id===w.msg), k=m&&(String(m.text||'').match(/Ticket (\\S+) \\(id \\d+\\) is yours/)||[])[1];
    const t=(k&&ts.find(x=>x.key===k))||ts.find(x=>x.assignee===w.member&&x.status==='doing');
    if(t&&!on[t.key])on[t.key]=w;
  });
  const last={}; SKM.ev.forEach(e=>{last[e.member]=e});
  const state=n=>{
    const w=ws.find(x=>x.member===n&&x.state==='working'), l=last[n];
    if(w)return ['work',kbMmss(+w.elapsed||0),'arbeitet'];
    if(hq.some(x=>x.instance===n))return ['ask','fragt dich','fragt dich'];
    if(l&&l.outcome!=='ok')return ['err','Fehler','letzter Turn fehlgeschlagen'];
    if(ws.some(x=>x.member===n)||ts.some(t=>t.assignee===n&&t.status==='open'&&deps(t).length))return ['wait','wartet','wartet'];
    const mins=l?Math.max(1,Math.round((now-l.ts)/60)):0;
    return ['idle',l?'ruht '+(mins>90?Math.round(mins/60)+' h':mins+' min'):'—','ruht'];
  };
  const team=(b.members||[]).filter(m=>m.name!=='indexer').map(m=>{const [s,lab,aria]=state(m.name);
    return `<span class=kb-who title="${esc(m.role||'')}"><span class="kb-av${s==='work'?'':' idle'}">${escT(m.name.charAt(0).toUpperCase())}<i class="kb-s ${s}" role=img aria-label="${aria}"></i></span>${escT(m.name)} <small>${escT(lab)}</small></span>`}).join('');
  const n=ts.length, nd=ts.filter(t=>t.status==='done').length, ng=ts.filter(t=>t.status==='doing').length, pct=x=>n?(100*x/n).toFixed(1):0;
  const bins=Array(12).fill(0); SKM.ev.forEach(e=>{const i=11-Math.floor((now-e.ts)/3600); if(i>=0&&i<12)bins[i]++});
  const mx=Math.max(1,...bins), sum=SKM.sum||{};
  const head=`<div class=kb-team aria-label=Team>${team}</div><div class=kb-sum>`+
    (n?`<span><b>${nd}/${n}</b> erledigt</span><span class=kb-pbar role=img aria-label="${nd} erledigt, ${ng} in Arbeit, ${n-nd-ng} offen"><i style="width:${pct(nd)}%;background:var(--app-live)"></i><i style="width:${pct(ng)}%;background:var(--color-accent-400)"></i></span>`
      :'<span>noch keine Tickets — sie kommen mit dem genehmigten Plan</span>')+
    `<span class=kb-spark title="Turns pro Stunde, letzte 12 h" aria-hidden=true>${bins.map((v,i)=>`<i${i===11?' class=now':''} style="height:${Math.max(1,Math.round(20*v/mx))}px"></i>`).join('')}</span>`+
    (sum.cost!==undefined?`<span>heute $${(+sum.cost).toFixed(2)} · ${kbTok((+sum.tokens_in||0)+(+sum.tokens_out||0))} Tokens</span>`:'')+'</div>';
  const card=t=>{
    const w=on[t.key], dep=deps(t), q=hq.find(x=>x.ticket===t.key&&x.kind==='question'), dn=t.status==='done';
    const cls=(w?' working':q?' asking':dep.length&&t.status==='open'?' waiting':dn?' donec':'')+(t.id===SKT?' cur':'');
    return `<article class="kb-tk${cls}" tabindex=0 role=button draggable=true data-id="${+t.id}" aria-label="${esc(t.key+' '+t.title+' — '+t.status)}">`+
      `<span class=kb-key>${escT(t.key)}${t.level&&!dn?` <span class=kb-lvl>L${+t.level}</span>`:''}</span><span class=kb-ti>${escT(t.title)}</span>`+
      (q?`<span class=kb-q>❓ ${escT(q.title)}</span>`:'')+
      (dep.length&&!w?`<span class=kb-dep>⏸ wartet auf ${escT(dep.join(', '))}</span>`:'')+
      `<span class=kb-meta><span class=kb-as>${t.assignee?kbAv(t.assignee,!!w)+escT(t.assignee):'<span class=text-muted>unassigned</span>'}</span>`+
      (w?`<span class=kb-t>🟢 ${kbMmss(+w.elapsed||0)}</span>`:dn?`<span>${escT(kbWhen(t.updated))}</span>`:'')+'</span>'+
      (w?'<span class=kb-bar><i class=kb-run></i></span>':'')+'</article>';
  };
  const loose=hq.filter(x=>x.kind==='question'&&!ts.some(t=>t.key===x.ticket));   /* a question on no ticket: still Blocked */
  const cols=KB_COLS.map(([s,label,empty])=>{
    let mine=ts.filter(t=>t.status===s); if(s==='done')mine=mine.slice().sort((a,c)=>(c.updated||0)-(a.updated||0));
    const extra=s==='blocked'?loose.map(x=>`<article class="kb-tk asking" tabindex=0 role=button data-hq="${esc(x.id)}"><span class=kb-q>❓ ${escT(x.title)}</span>`+
      `<span class=kb-meta><span class=kb-as>${kbAv(x.instance,0)}${escT(x.instance)}</span></span></article>`).join(''):'';
    const cnt=mine.length+(s==='blocked'?loose.length:0);
    return `<section class=kb-col data-st=${s} aria-label="${label}"><div class=kb-ch>${label} <b>${cnt}</b></div>`+
      (cnt?mine.map(card).join('')+extra:`<div class=kb-empty>${empty}</div>`)+'</section>';
  }).join('');
  const html=head+`<div class=kb-board>${cols}</div><div class=kb-legend><span><i style="background:var(--app-live)"></i>arbeitet gerade (Laufzeit)</span>`+
    '<span><i style="background:var(--color-warn)"></i>wartet (Abhängigkeit, Limit)</span><span><i style="background:var(--app-ask)"></i>fragt dich (Blocked)</span>'+
    '<span><i style="background:var(--color-neutral-500)"></i>ruht</span><span><i style="background:var(--app-bad)"></i>letzter Turn fehlgeschlagen</span>'+
    '<span>Klick auf eine Karte = Ticket, Kommentare, letzte Antwort · Ziehen = Status ändern</span></div>';
  el.hidden=false;
  if(html===SKB||SKB_DRAG)return;                       /* redraw only on change, never under a drag */
  const f=document.activeElement, fid=f&&el.contains(f)?f.dataset.id:null, sx=(el.querySelector('.kb-board')||{}).scrollLeft||0;
  SKB=html; el.innerHTML=html;
  el.querySelector('.kb-board').scrollLeft=sx;
  if(fid){const c=el.querySelector(`.kb-tk[data-id="${fid}"]`); if(c)c.focus({preventScroll:true})}
}
async function skTk(id,change){       /* the operator's ticket edit (existing /squad/ticket route) */
  let d={};
  try{const r=await fetch('/api/projects/'+encodeURIComponent(SK)+'/squad/ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,...change})});
    try{d=await r.json()}catch(e){d={error:'HTTP '+r.status}}}
  catch(e){d={error:'manager not reachable'}}
  return d;
}
const KB_DOT={done:'var(--app-live)',doing:'var(--color-accent-600)',review:'var(--app-ask)',blocked:'var(--color-warn)',open:'var(--color-neutral-500)'};
function kbMd(s){        /* minimal safe markdown: escape first, then `code`, **bold**, "- " lists */
  const il=x=>x.split(/(`[^`\\n]+`)/).map((p,i)=>i%2?'<code>'+p.slice(1,-1)+'</code>':p.replace(/\\*\\*([^*\\n]+)\\*\\*/g,'<b>$1</b>')).join(''), b=[];
  escT(String(s||'')).split('\\n').forEach(l=>{const m=l.match(/^\\s*[-*] +(.*)/), p=b[b.length-1]||'';
    if(!m)b.push(il(l)); else if(p.startsWith('<ul>'))b[b.length-1]=p.slice(0,-5)+'<li>'+il(m[1])+'</li></ul>'; else b.push('<ul><li>'+il(m[1])+'</li></ul>')});
  return b.map((x,i)=>i&&!x.startsWith('<ul>')&&!b[i-1].startsWith('<ul>')?'<br>'+x:x).join('');
}
function skTkHtml(t){   /* the side panel's header + body; the footer (comment box) is static in ui_html */
  const ts=SKB_D.b.tickets||[], msgs=(SKB_D.d.messages||[]).slice().reverse(), names=SK_MEMBERS.map(m=>m.name), who=n=>String(n||'').replace(/^user:/,'');
  if(t.assignee&&!names.includes(t.assignee))names.push(t.assignee);
  const ans=msgs.find(m=>m.from===t.assignee&&m.kind==='turn')||msgs.find(m=>t.assignee&&m.from===t.assignee);
  const opt=(v,l,cur)=>`<option value="${esc(v)}"${v===cur?' selected':''}>${escT(l)}</option>`, isRs=c=>/re-scope/i.test(c.text||'');
  const vs=t.versions||[], lv=vs[vs.length-1], rc=(t.comments||[]).filter(isRs).pop();      /* a re-scope: a new version (ticket_update title/description) or a RE-SCOPE comment */
  const rs=lv&&(!rc||lv.at>=rc.ts)?{by:lv.by,ts:lv.at}:rc;
  const deps=(t.depends_on||[]).map(k=>ts.find(x=>x.key===k)||{key:k,title:'',status:'open'}), wait=t.status==='done'?0:deps.filter(x=>x.status!=='done').length;
  const ev=[...(t.created?[{ts:t.created,sys:1}]:[]),...vs.map((v,i)=>({ts:v.at,by:v.by,ver:i+2})),...(t.comments||[]).map(c=>({ts:c.ts,by:c.by,text:c.text,rs:isRs(c)})),
    ...(ans?[{ts:ans.ts,by:ans.from,text:ans.text,ans:1}]:[])].sort((a,c)=>(a.ts||0)-(c.ts||0));
  const item=e=>e.ver?`<li class=kb-ev><span class="kb-av idle" aria-hidden=true>↻</span><div class=kb-eh><b>${escT(who(e.by))}</b><span class="kb-tag rs">re-scope</span>`+
      `title/description → v${e.ver}<time>${escT(kbWhen(e.ts))}</time></div></li>`
    :e.sys?`<li class=kb-ev><span class="kb-av idle" aria-hidden=true>+</span><div class=kb-eh>Created${t.by?' by <b>'+escT(who(t.by))+'</b>':''}`+
      `${t.assignee?' · assigned to '+escT(t.assignee):''}<time>${escT(kbWhen(e.ts))}</time></div></li>`
    :`<li class=kb-ev>${kbAv(who(e.by),1)}<div><div class=kb-eh><b>${escT(who(e.by))}</b>${e.rs?'<span class="kb-tag rs">re-scope</span>':''}`+
      `${e.ans?'<span class="kb-tag ans">turn result</span>':''}<time>${escT(kbWhen(e.ts))}</time></div>`+
      (e.ans?`<div class="kb-md kb-box kb-clip">${kbMd(e.text)}</div><button class=kb-more data-l="Show full answer" aria-expanded=false onclick=kbMore(this)>Show full answer</button>`
        :`<div class=kb-md>${kbMd(e.text)}</div>`)+'</div></li>';
  const ed=SKT_EDIT;
  return `<div class=kb-ptop><div class=kb-prow><div style="flex:1;min-width:0"><span class=kb-key>${escT(t.key)}</span>`+
      (ed?`<input class=input id=sk-tk-ti value="${esc(t.title)}" maxlength=200 aria-label=Title style="margin-top:4px">`
        :` <button class=kb-more onclick=skTkEdit(1) title="Edit title and description (re-scope; the old version is kept)">Edit</button><h2 class=kb-ptitle id=sk-tkd-t>${kbMd(t.title)}</h2>`)+'</div>'+
      `<button class=kb-x onclick=skTkClose() aria-label=Close title="Close (Esc)">×</button></div>`+
    `<div class=kb-ctl><select class="kb-sel st" id=sk-tk-st aria-label=Status onchange="skTkSet({status:this.value})">${KB_COLS.map(c=>opt(c[0],c[1],t.status)).join('')}</select>`+
      `<select class=kb-sel id=sk-tk-as aria-label=Assignee onchange="skTkSet({assignee:this.value})">${opt('','unassigned',t.assignee)}${names.map(n=>opt(n,n,t.assignee)).join('')}</select>`+
      (t.level?`<span class=kb-badge title="Model level ${+t.level}">L${+t.level}</span>`:'')+(t.updated?`<span class=kb-when>updated ${escT(kbWhen(t.updated))}</span>`:'')+'</div>'+
    (rs?`<div class=kb-rescope role=note><span aria-hidden=true>↻</span><div style="flex:1;min-width:0"><b>Re-scoped</b> by ${escT(who(rs.by))} at ${escT(kbWhen(rs.ts))}`+
      (lv?`<div class=kb-prev hidden><b>v${vs.length}:</b> ${kbMd(lv.title)}${lv.description?`<div class=kb-md>${kbMd(lv.description)}</div>`:''}</div>`:'')+'</div>'+
      (lv?`<button class=kb-more data-v="v${vs.length}" aria-expanded=false onclick=kbPrev(this)>Show v${vs.length}</button>`:'')+'</div>':'')+'</div>'+
    '<div class=kb-pbody>'+
    (ed?`<section><div class=sq-sub>Description</div><textarea class=input id=sk-tk-de rows=8 maxlength=4000 aria-label=Description>${escT(t.description)}</textarea>`+
      '<div class=mrow><button class="btn btn-primary btn-sm" onclick=skTkEditSave()>Save</button><button class="btn btn-ghost btn-sm" onclick=skTkEdit(0)>Cancel</button></div></section>'
    :t.description?`<section><div class=sq-sub>Description</div><div class="kb-md kb-box kb-clip">${kbMd(t.description)}</div>`+
      '<button class=kb-more data-l="Show all" aria-expanded=false onclick=kbMore(this)>Show all</button></section>':'')+
    (deps.length?`<section><div class=sq-sub>Depends on</div><div class=kb-deps>${deps.map(x=>`<button class=kb-chip ${x.id?`onclick=skTkOpen(${+x.id})`:'disabled'}>`+
      `<i style="background:${KB_DOT[x.status]||KB_DOT.open}"></i><span class=kb-key>${escT(x.key)}</span><span class=kb-ct>${escT(x.title)}</span><small>${escT(x.status)}</small></button>`).join('')}</div>`+
      (wait?`<div class=kb-dep>Waiting on ${wait} ticket${wait>1?'s':''}</div>`:'')+'</section>':'')+
    `<section><div class=sq-sub>Activity</div><ol class=kb-tl>${ev.map(item).join('')||'<li class=text-muted>No activity yet</li>'}</ol></section></div>`;
}
function kbPrev(b){const p=b.parentElement.querySelector('.kb-prev'); p.hidden=!p.hidden; b.textContent=(p.hidden?'Show ':'Hide ')+b.dataset.v; b.setAttribute('aria-expanded',!p.hidden)}
function skTkEdit(on){SKT_EDIT=!!on; skTkRender(true); if(on)document.getElementById('sk-tk-ti').focus()}
async function skTkEditSave(){         /* the operator re-scopes: title + description via /squad/ticket (the old version is kept) */
  const ti=document.getElementById('sk-tk-ti').value.trim(), de=document.getElementById('sk-tk-de').value.trim(), m=document.getElementById('sk-tk-msg');
  if(!ti){m.textContent='⚠️ title missing';return}
  const d=await skTk(SKT,{title:ti,description:de});
  m.textContent=d.error?'⚠️ '+d.error:d.note?d.key+': '+d.note:'';
  if(d.error)return;
  SKT_EDIT=false; SKB=''; await skLoad(); skTkRender(true);
}
function kbMore(b){const o=b.previousElementSibling.classList.toggle('open'); b.textContent=o?'Show less':b.dataset.l; b.setAttribute('aria-expanded',o)}
function skTkRender(force){            /* redraw only on change, never under an open select (unless forced) */
  const t=(SKB_D.b.tickets||[]).find(x=>x.id===SKT), box=document.getElementById('sk-tkd-box'); if(!t)return;
  const h=skTkHtml(t), a=document.activeElement, fid=a&&box.contains(a)?a.id:'';
  if(h===box.dataset.h||(!force&&(SKT_EDIT||a&&a.tagName==='SELECT'&&fid)))return;
  const st=(box.querySelector('.kb-pbody')||{}).scrollTop||0;
  box.dataset.h=h; box.innerHTML=h; box.querySelector('.kb-pbody').scrollTop=st;
  box.querySelectorAll('.kb-clip').forEach(c=>{if(c.scrollHeight<=c.clientHeight+2){c.classList.remove('kb-clip');c.nextElementSibling.remove()}});
  if(fid&&document.getElementById(fid))document.getElementById(fid).focus({preventScroll:true});
}
function skTkOpen(id){
  const t=(SKB_D.b.tickets||[]).find(x=>+x.id===+id); if(!t)return;
  if(SKT!==t.id){SKT_EDIT=false;document.getElementById('sk-tk-c').value='';document.getElementById('sk-tk-msg').textContent=''}
  document.querySelectorAll('#sk-board .kb-tk.cur').forEach(c=>c.classList.remove('cur'));
  document.getElementById('sk-tkd').hidden=false; document.getElementById('s-squads').classList.add('kb-open');
  SKT=t.id; document.getElementById('sk-tkd-box').dataset.h=''; skTkRender(true);      /* visible first: the clip check measures */
  const c=document.querySelector(`#sk-board .kb-tk[data-id="${t.id}"]`); if(c){c.classList.add('cur');c.scrollIntoView({block:'nearest',inline:'nearest'})}
  document.getElementById('sk-tk-st').focus({preventScroll:true});
}
function skTkClose(){
  document.getElementById('sk-tkd').hidden=true; document.getElementById('s-squads').classList.remove('kb-open'); SKT_EDIT=false;
  const c=document.querySelector(`#sk-board .kb-tk[data-id="${SKT}"]`); SKT=0; if(c){c.classList.remove('cur');c.focus()}
}
async function skTkSet(ch){             /* a header select: status / assignee, applied at once */
  const t=(SKB_D.b.tickets||[]).find(x=>x.id===SKT); if(!t)return;
  const d=await skTk(t.id,ch);
  document.getElementById('sk-tk-msg').textContent=d.error?'⚠️ '+d.error:d.note?t.key+': '+d.note:'';
  SKB=''; await skLoad(); skTkRender(true);
}
async function skTkSave(lead){          /* the comment (ticket comment), or "Ask lead" (a squad post: no addressee = the lead) */
  const t=(SKB_D.b.tickets||[]).find(x=>x.id===SKT), el=document.getElementById('sk-tk-c'), c=el.value.trim(); if(!t||!c)return;
  const btns=document.querySelectorAll('.kb-pfoot button'); btns.forEach(b=>b.disabled=true);
  let d={};
  if(lead){try{d=await (await fetch('/api/projects/'+encodeURIComponent(SK)+'/squad/post',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:`About ticket ${t.key} (id ${t.id}): ${c}`})})).json()}catch(e){d={error:'manager not reachable'}}}
  else d=await skTk(t.id,{comment:c});
  btns.forEach(b=>b.disabled=false);
  document.getElementById('sk-tk-msg').textContent=d.error?'⚠️ '+d.error:lead?'sent to the lead':'';
  if(d.error)return;
  el.value=''; SKB=''; await skLoad(); skTkRender(true);
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.getElementById('sk-tkd').hidden)skTkClose()});
function skChat(){const th=document.getElementById('sk-thread');th.scrollTop=th.scrollHeight;document.getElementById('sk-text').focus()}
(()=>{
  const bd=document.getElementById('sk-board'), clr=()=>bd.querySelectorAll('.kb-col.drop').forEach(x=>x.classList.remove('drop'));
  bd.addEventListener('click',e=>{const c=e.target.closest('.kb-tk'); if(!c)return;
    if(c.dataset.hq)document.getElementById('sk-cards').scrollIntoView({block:'nearest'}); else skTkOpen(c.dataset.id)});
  bd.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.classList.contains('kb-tk')){e.preventDefault();e.target.click()}});
  bd.addEventListener('dragstart',e=>{const c=e.target.closest('.kb-tk[data-id]'); if(!c)return;
    SKB_DRAG=true; e.dataTransfer.setData('text/plain',c.dataset.id); e.dataTransfer.effectAllowed='move'});
  bd.addEventListener('dragend',()=>{SKB_DRAG=false;clr()});
  bd.addEventListener('dragover',e=>{const col=e.target.closest('.kb-col'); if(!col||!SKB_DRAG)return;
    e.preventDefault(); if(!col.classList.contains('drop')){clr();col.classList.add('drop')}});
  bd.addEventListener('drop',async e=>{
    const col=e.target.closest('.kb-col'); if(!col)return;
    e.preventDefault(); SKB_DRAG=false; clr();
    const id=e.dataTransfer.getData('text/plain'), t=(SKB_D.b.tickets||[]).find(x=>String(x.id)===id);
    if(!t||t.status===col.dataset.st)return;
    const d=await skTk(t.id,{status:col.dataset.st});
    skMsg(d.error?'⚠️ '+d.error:t.key+' → '+col.dataset.st+(d.note?' — '+d.note:'')); SKB=''; skLoad();
  });
})();
async function skRestart(){
  if(!SK||!confirm('Restart every member of '+SK+'? A member busy with a turn restarts right after it.'))return;
  try{
    const d=await (await fetch('/api/projects/'+encodeURIComponent(SK)+'/squad/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
    skMsg(d.error?'⚠️ '+d.error:'Restart: '+Object.entries(d.members||{}).map(([n,v])=>n+' — '+v).join(' · ')||'no members');
  }catch(e){skMsg('⚠️ '+e)}
}
async function skPause(on){
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(SK)+'/squad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paused:on})})).json()}catch(e){d={error:'manager not reachable'}}
  skMsg(d.error||(on?'paused':'resumed')); skLoad(); loadSquads();
}
function skProject(){const n=SK;location.hash='#projects';setTimeout(()=>sqOpen(n),300)}
async function skSend(){
  const el=document.getElementById('sk-text'), text=el.value.trim(); if(!text)return;
  const btn=document.getElementById('sk-send'); btn.disabled=true;
  let d={};
  try{
    d=SK?await (await fetch('/api/projects/'+encodeURIComponent(SK)+'/squad/post',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})})).json()
       :await (await fetch('/api/squads/kickoff',{method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({text,architect_template:document.getElementById('sk-arch').value})})).json();
  }catch(e){d={error:'manager not reachable'}}
  btn.disabled=false; skMsg(d.error||'');
  if(d.error)return;
  el.value='';
  if(SK)skLoad(); else {skJob(d.id);loadSquads()}
}
setInterval(()=>{if(!document.hidden&&location.hash==='#squads'&&SK)skLoad();else if(!document.hidden&&location.hash==='#projects'&&SQ)sqWork()},5000);
/* Projects → Squad: only the working line refreshes (the settings form stays as edited) */
async function sqWork(){
  let d={}; try{d=await (await fetch('/api/projects/'+encodeURIComponent(sqName())+'/squad/messages?since=999999999')).json()}catch(e){return}
  if(!d.error)document.getElementById('sq-working').innerHTML=sqWorking(d.working)+sqQueued(d.queued);
}
setInterval(()=>{if(!document.hidden&&location.hash==='#squads')loadSquads()},20000);
/* — human in the loop: approvals, questions, reviews that wait for you (mgr/hitlq.py) — */
let WAIT=[];
const HQ_KIND={approval:'Approval',question:'Question',review:'Review'};
async function loadWaiting(){
  let d={}; try{d=await (await fetch('/api/waiting?status=open')).json()}catch(e){return WAIT}
  if(d.error)return WAIT;
  WAIT=d.items||[];
  const c=document.getElementById('waitcnt'); if(c){c.textContent=WAIT.length||'';c.classList.toggle('hot',WAIT.length>0)}
  if(location.hash==='#waiting')hqRender();
  return WAIT;
}
function hqCard(it){
  const id=esc(it.id), b=(cls,act,label)=>`<button class="btn ${cls}" onclick="hqAct('${id}','${act}',this)">${label}</button>`;
  const inp=ph=>`<input class=input data-k=text placeholder="${esc(ph)}" aria-label="${esc(ph)}">`;
  const foot=it.kind==='question'?inp('Your answer')+b('btn-primary','answer','Answer')
    :it.kind==='review'?inp('Comment (required for Request changes)')+b('btn-secondary','request_changes','Request changes')+b('btn-primary','approve','Approve')
    :inp('Comment (optional)')+b('btn-secondary','deny','Deny')+b('btn-primary','approve','Approve');
  return `<div class=sq-card data-hq="${id}"><div class=sq-head><b>${HQ_KIND[it.kind]||escT(it.kind)} · ${escT(it.title)}</b>`+
    `<span class=text-muted style="font-size:12px">${escT(it.instance)}${it.project?' · '+escT(it.project):''}${it.ticket?' · ticket '+escT(it.ticket):''} · ${id} · ${new Date(it.created*1000).toLocaleString()}</span></div>`+
    (it.tool?`<div class=text-muted style="font-size:12px;margin-top:6px">tool <code>${escT(it.tool)}</code></div>`:'')+
    (it.detail&&it.detail!==it.title?`<div class="hq-det${it.tool?' mono':''}">${escT(it.detail)}</div>`:'')+
    ((it.options||[]).length?'<div class=hq-opts>'+it.options.map(o=>`<button class="btn btn-secondary btn-sm" data-choice="${esc(o)}" onclick="hqAct('${id}','answer',this)">${escT(o)}</button>`).join('')+'</div>':'')+
    `<div class=sq-foot>${foot}</div></div>`;
}
function hqRender(){
  document.getElementById('wait-list').innerHTML=WAIT.length?WAIT.map(hqCard).join(''):'<span class=text-muted style="font-size:13px">Nothing waits for you.</span>';
}
async function hqAct(id,action,btn){
  const c=btn.closest('[data-hq]'), t=c.querySelector('[data-k=text]'), text=t?t.value.trim():'', choice=btn.dataset.choice||'';
  const sk=location.hash==='#squads', say=x=>{document.getElementById(sk?'sk-msg':'wait-msg').textContent=x};
  if(action==='request_changes'&&!text){say('Request changes: write what should change.');t.focus();return}
  if(action==='answer'&&!text&&!choice){say('Answer: pick an option or write an answer.');t.focus();return}
  btn.disabled=true;
  let d={}; try{d=await (await fetch('/api/waiting/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,text,choice})})).json()}catch(e){d={error:'manager not reachable'}}
  btn.disabled=false;
  say(d.error||(HQ_KIND[d.item.kind]+' '+d.item.id+': '+d.item.status.replace('_',' ')+' ✓'));
  await loadWaiting();
  if(sk){SK_PEND='';skLoad()}else hqRender();
}
window.addEventListener('hashchange',()=>{if(location.hash==='#waiting')loadWaiting()});
setInterval(()=>{if(!document.hidden)loadWaiting()},15000);
loadWaiting();
async function delProject(name){
  if(!confirm('Delete project '+name+'? The folder stays; the members lose /project/'+name+'.'))return;
  await fetch('/api/projects/'+encodeURIComponent(name)+'/delete',{method:'POST'});
  if(PJ_EDIT===name)pjReset();
  loadProjects();
}

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

async function loadProposals(){
  const el=document.getElementById('skprops'); if(!el)return;
  let ps=[]; try{ps=(await (await fetch('/api/skill-proposals')).json()).proposals||[];}catch(e){ps=[];}
  if(!ps.length){el.innerHTML='';return;}
  // Skills the agents distilled from their own runs: nothing enters the
  // catalog without a click here.
  el.innerHTML=`<div class=text-muted style="font-size:12px;margin:0 0 6px">Proposed by agents — approve to add to the catalog</div>`+
    `<div class=grid3 style="margin-bottom:14px">`+ps.map(p=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:8px">`+
    `<span class=card-title style="font-size:15px;font-family:var(--font-mono);font-weight:500">${escT(p.name)}</span>`+
    (p.update?`<span class="tag tag-neutral" style="font-size:10px">update</span>`:'')+
    `<span class=text-muted style="font-size:11px;margin-left:auto">${escT(p.instance||'')}</span></div>`+
    `<p class=card-body style="font-size:12.5px">${escT(p.description||'')}</p>`+
    `<details style="font-size:12px;margin:4px 0 8px"><summary class=text-muted style="cursor:pointer">content</summary>`+
    `<pre style="white-space:pre-wrap;font-size:11.5px;max-height:260px;overflow:auto;margin:6px 0 0">${escT(p.content||'')}</pre></details>`+
    `<div style="display:flex;gap:6px"><button class="btn btn-primary btn-sm" onclick="decideProposal('${esc(p.id)}','approve')">Approve</button>`+
    `<button class="btn btn-secondary btn-sm" onclick="decideProposal('${esc(p.id)}','discard')">Discard</button></div></div>`).join('')+`</div>`;
}
async function decideProposal(id,what){
  // a discard may carry a reason: the agents see it and do not propose the idea again
  let reason='';
  if(what==='discard'){reason=prompt('Why discard it? (optional — the agents are told, so they do not propose it again)','');
    if(reason===null)return;}
  const r=await (await fetch('/api/skill-proposals/'+encodeURIComponent(id)+'/'+what,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})})).json();
  if(what==='approve')location.reload(); else loadProposals();
  if(r.msg&&/error|unknown/.test(r.msg))alert(r.msg);
}
let SKSTATS={};
async function loadSkillStats(){
  try{SKSTATS=(await (await fetch('/api/skill-stats')).json()).skills||{}}catch(e){SKSTATS={}}
  renderSkills(true);
}
const SK_VERDICT={working:'tag-accent',failing:'tag-neutral',unused:'tag-neutral',few:'tag-neutral','new':'tag-neutral'};
function skStat(n){const st=SKSTATS[n]; if(!st)return '';
  const col=st.verdict==='failing'?' style="background:var(--color-danger,#c0392b);color:#fff"':'';
  return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:11.5px;margin-top:6px" title="${esc(st.why)}">`+
    `<span class="tag ${SK_VERDICT[st.verdict]||'tag-neutral'}"${col}>${escT(st.verdict)}</span>`+
    `<span class=text-muted>${st.uses} load${st.uses===1?'':'s'} · ${st.ok} ok · ${st.failed} failed`+
    (st.last?` · last ${new Date(st.last*1000).toLocaleDateString()}`:'')+
    ` · ~${st.load_tokens} tokens per load</span></div>`;}
function renderSkills(statsOnly){
  if(!statsOnly){loadProposals();loadSkillStats();}
  const cat=(document.getElementById('skcat')||{}).value||'';
  const qs=((document.getElementById('skq')||{}).value||'').toLowerCase().split(/\\s+/).filter(Boolean);   /* every word must match name or description */
  document.getElementById('skills').innerHTML=SKILLS.filter(s=>(!cat||s.category===cat)&&
    qs.every(w=>(s.name+' '+(s.description||'')).toLowerCase().includes(w))).map(s=>
    `<div class="card blueprint">${CORNERS}`+
    `<div style="display:flex;align-items:center;gap:8px">`+
    `<span class=card-title style="font-size:15px;font-family:var(--font-mono);font-weight:500">${escT(s.name)}</span>`+
    `<button class="tag ${s.default?'tag-accent':'tag-neutral'}" style="border:0;cursor:pointer;font-size:11px" `+
    `title="${s.default?'Standard: every agent is told about it each turn and loads it when it applies — click for on demand':'On demand: agents find it via list_skills — click to make it standard'}" `+
    `onclick="skDefault('${esc(s.name)}',${!s.default})">${s.default?'standard':'on demand'}</button>`+
    `<select class=input style="width:auto;font-size:11px;padding:1px 4px;min-height:0" aria-label="Category of ${esc(s.name)}" onchange="skCategory('${esc(s.name)}',this.value)">`+
    SK_CATS.map(c=>`<option${c===s.category?' selected':''}>${c}</option>`).join('')+`</select>`+
    `<span style="margin-left:auto;display:flex;gap:2px">`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px" title=Edit onclick="editSkill('${esc(s.name)}')">${I_EDIT}</button>`+
    `<button class="btn btn-icon btn-ghost" style="width:26px;height:26px;color:var(--color-neutral-600)" title=Delete onclick="delSkill('${esc(s.name)}')">${I_DEL}</button>`+
    `</span></div>`+
    `<p class=card-body style="font-size:12.5px">${escT(s.description||'')}</p>${skStat(s.name)}</div>`)
    .join('')||'<span class=text-muted style="font-size:13px">none</span>';
}
async function editSkill(n){const s=SKILLS.find(x=>x.name===n);if(!s)return;
  document.getElementById('skname').value=s.name;document.getElementById('skdesc').value=s.description||'';
  const c=document.getElementById('skcontent');c.value='… lade';
  // The page carries only name+description (the catalog is ~1 MB); the body
  // comes on demand.
  try{c.value=await (await fetch('/api/skills/'+encodeURIComponent(n))).text()}catch(e){c.value=''}
  document.getElementById('skname').scrollIntoView({behavior:'smooth'});}
function saveSkill(){
  const name=document.getElementById('skname').value.trim(),description=document.getElementById('skdesc').value,
        content=document.getElementById('skcontent').value;
  if(!name)return alert('Name?');
  fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description,content})})
    .then(()=>location.reload());
}
async function skCategory(n,cat){
  const d=await (await fetch('/api/skills/'+encodeURIComponent(n)+'/category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:cat})})).json();
  if(/unknown|must be/.test(d.msg||''))return alert(d.msg);
  const s=SKILLS.find(x=>x.name===n); if(s)s.category=cat; renderSkills(true);
}
async function skDefault(n,on){
  const d=await (await fetch('/api/skills/'+encodeURIComponent(n)+'/default',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})})).json();
  if(/unknown|error/.test(d.msg||''))return alert(d.msg);
  const s=SKILLS.find(x=>x.name===n); if(s)s.default=on; renderSkills(true);
}
async function delSkill(n){if(confirm('Delete skill '+n+'?')){await fetch('/api/skills/'+encodeURIComponent(n)+'/delete',{method:'POST'});location.reload()}}

/* — version + updates: footer badge and the card in Settings; the update
   itself is the installer, run by kaim56-update.service (root, oneshot) — */
let UPD=null,_updT=null;
async function loadVersion(force){
  let v; try{v=await (await fetch('/api/version'+(force?'?force=1':''))).json()}catch(e){return}
  UPD=v;
  const f=document.getElementById('version');
  if(f)f.innerHTML=esc(v.installed)+(v.available?` · <a href="${esc(v.url)}" target=_blank title="new release">${esc(v.latest)} available</a>`:'');
  const t=document.getElementById('updtxt'),b=document.getElementById('updbtn'),l=document.getElementById('updlog');
  if(!t)return;
  if(v.updating){t.textContent='Updating to '+(v.latest||'the latest release')+' … the manager restarts when the installer is done';b.hidden=true;l.hidden=false;l.textContent=v.log||'(starting)';l.scrollTop=l.scrollHeight;
    clearTimeout(_updT);_updT=setTimeout(()=>loadVersion(),5000);return}
  if(l&&!l.hidden&&v.log){l.textContent=v.log;}   /* the log of a finished run stays visible */
  t.innerHTML=`Installed <b>${esc(v.installed)}</b>`+(v.latest?` · latest <a href="${esc(v.url)}" target=_blank>${esc(v.latest)}</a>`:(v.error?` · <span title="${esc(v.error)}">release check failed</span>`:''))+
    (v.available?(v.unit?' · <b>update available</b>':' · update available — run <code>install.sh --release</code> on the host (the update service is not installed yet)'):(v.latest?' · up to date':''));
  b.hidden=!(v.available&&v.unit); b.textContent='Update to '+v.latest;
}
async function runUpdate(){
  if(!UPD||!confirm('Update to '+UPD.latest+'?\\n\\nThe installer checks out the release tag, refreshes the files, rebuilds images and restarts the manager. Running instances keep running; the web UI is away for a moment.'))return;
  const r=await (await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  if(!/started/.test(r.msg||''))alert(r.msg||'error');
  setTimeout(()=>loadVersion(),1500);
}
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
    .then(r=>r.json()).then(r=>{const m=document.getElementById('setmsg');
      if(/^error/.test(r.msg||'')){m.textContent=r.msg;return}
      Object.assign(SETTINGS,d);m.textContent='saved ✓';renderParams();loadAppsPanel()});
}
async function decisionTest(){
  // POST /api/decision/test (mgr/decision.py): one sample suggestion with the SAVED backend
  const m=document.getElementById('setmsg'); m.textContent='testing the decision backend…';
  let d; try{d=await (await fetch('/api/decision/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json()}
  catch(e){m.textContent='manager not reachable';return}
  const s=d.result||{}, fell=d.backend!==d.used;
  m.textContent=`${d.backend}${fell?' → fell back to '+d.used:''} · ${d.ms} ms · `+
    (s.persona?`persona ${s.persona.name} (${s.persona.score})`:'no persona')+
    ` · skills ${(s.skills||[]).map(x=>x.name+' ('+x.score+')').join(', ')||'–'}`+
    ` · tier ${s.tier?s.tier.name+' ('+s.tier.score+')':'–'} · ${s.note||''}`;
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
  pkGo(start||'/home');
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
  pkOpen(h.value||'/home',p=>{
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
  if(pn){const p=PERSONAS.find(x=>x.name===pn);if(p){cfg.AGENT_SYSTEM=p.prompt;
    if(p.tools&&p.tools.length)cfg.AGENT_TOOLS=p.tools.join(',');   // persona's recommended tool subset
    if(p.model)cfg.OPENROUTER_MODEL=p.model;}}
  const ks=document.getElementById('katfsshare').value;
  if(ks)cfg.KATFS_SHARE=ks;
  const mcps=[...document.querySelectorAll('#mcp-pick input[type=checkbox]:checked')].map(c=>c.value);
  const mounts=collectMounts(document.getElementById('mounts'));
  const internet=document.getElementById('cap-net').checked;
  const tools=[...document.querySelectorAll('.toolcb:checked')].map(c=>c.value);
  const skills=[...document.querySelectorAll('.skillcb:checked')].map(c=>c.value);
  fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:n,template:t,config:cfg,mcps,mounts,internet,tools,skills})}).then(r=>r.json())
    .then(d=>{if(/created/.test(d.msg||''))location.reload();else alert(d.msg||'not created')}).catch(()=>alert('manager not reachable'));
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
  // katfs: the shares online now, plus the instance's current one even if its provider is offline
  const cur=(inst.config||{}).KATFS_SHARE||'', sel=document.getElementById('mdlgkatfs');
  const shares=[...(window.KATFS_SHARES||[])];
  if(cur&&!shares.some(x=>x.id===cur))shares.push({id:cur,name:'(not shared right now)'});
  sel.innerHTML='<option value="">— none —</option>'+shares.map(x=>{
    const lbl=(x.name||x.id)+(x.device?' · '+x.device:'')+(x.readonly?' · read-only':'')+' — '+x.id;
    return `<option value="${esc(x.id)}"${x.id===cur?' selected':''}>${escT(lbl)}</option>`}).join('');
  sel.dataset.orig=cur;
  const pb=document.getElementById('mdlgproj'), mine=(PROJECTS||[]).filter(p=>(p.members||{})[name]);
  pb.style.display=mine.length?'':'none';
  pb.innerHTML='<b>Projects</b>: '+mine.map(p=>`<a href="#projects" onclick=mdlgClose()>${escT(p.name)}</a> (${escT(p.members[name].role)}) at <code>/project/${escT(p.name)}</code>`).join(' · ');
  document.getElementById('mdlg').style.display='grid';
}
function mdlgClose(){document.getElementById('mdlg').style.display='none'}
async function saveMounts(){
  const mounts=collectMounts(document.getElementById('mdlgrows'));
  const r=await fetch(`/api/instances/${MDLG}/mounts`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({mounts})});
  const d=await r.json(); const msgs=[d.msg||'ok'];
  const sel=document.getElementById('mdlgkatfs');
  if(sel.value!==(sel.dataset.orig||'')){
    const k=await (await fetch(`/api/instances/${MDLG}/config`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'KATFS_SHARE',value:sel.value})})).json();
    msgs.push(k.msg||'katfs: ok');
  }
  mdlgClose();
  alert(msgs.join('\\n'));location.reload();
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
      ''+md(c.text||'');
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
    `<td>${m.tools?'<span class="tag tag-accent">tools</span>':'<span class="tag tag-neutral">no tools</span>'}</td>`+
    `<td title="Ladder level for the cost router (policy cheapest with catalog: true)">${lvlSel('openrouter/'+m.id,'')}</td></tr>`)
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

/* — iroh app transport: the manager's gateway node-id + the phone allowlist — */
async function loadIroh(){
  let d={node_id:'',allow:[]};
  try{d=await (await fetch('/api/iroh')).json()}catch(e){}
  const nid=document.getElementById('irohnid'); if(nid){
    nid.value=d.node_id||'';
    document.getElementById('irohhint').textContent=d.node_id
      ? 'Live — the app dials this node-id over iroh (relayed, NAT-traversed, end-to-end encrypted).'
      : 'Gateway not running. Start it: sudo systemctl enable --now iroh-gw';
  }
  const box=document.getElementById('irohallow'); if(box){
    box.innerHTML=(d.allow&&d.allow.length)? d.allow.map(a=>
      `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--color-border,#0002)">`+
      `<span style="flex:1;min-width:0"><b>${escT(a.label||'(no label)')}</b> `+
      `<span class="mono text-muted" style="font-size:12px">${escT(a.id.slice(0,16))}…</span></span>`+
      `<button class="btn btn-ghost btn-sm" onclick="irohRemove('${escT(a.id)}')">Remove</button></div>`).join('')
      : '<span class=text-muted style="font-size:13px">No phones paired yet.</span>';
  }
}
function irohCopy(){const v=document.getElementById('irohnid').value;if(v)navigator.clipboard.writeText(v).then(()=>{document.getElementById('irohhint').textContent='Copied.';});}
async function irohAdd(){
  const id=document.getElementById('irohaddid').value.trim();
  const label=document.getElementById('irohaddlabel').value.trim();
  const d=await (await fetch('/api/iroh',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'add',id,label})})).json();
  document.getElementById('irohmsg').textContent=d.ok?('✓ '+d.msg):('⚠️ '+d.msg);
  if(d.ok){document.getElementById('irohaddid').value='';document.getElementById('irohaddlabel').value='';}
  loadIroh();
}
async function irohRemove(id){
  await fetch('/api/iroh',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'remove',id})}).catch(()=>{});
  loadIroh();
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
/* Config editor: every key of the instance's config, edited in place; Save
   posts only the changes (one /config call each, the server validates). */
let CFDLG='', CFORIG={};
async function editConfig(name){
  const inst=(await (await fetch('/api/instances')).json()).find(i=>i.name===name)||{};
  CFDLG=name; CFORIG={...(inst.config||{})};
  document.getElementById('cfdlgname').textContent=name;
  document.getElementById('cfdlgmsg').textContent='';
  document.getElementById('cfdlgrestart').style.display='none';
  const box=document.getElementById('cfdlgrows'); box.innerHTML='';
  Object.keys(CFORIG).sort().forEach(k=>cfAddRow(k,CFORIG[k],false));
  document.getElementById('cfdlg').style.display='grid';
}
function cfAddRow(k,v,isNew){
  const row=document.createElement('div'); row.className='cf-row'; row.dataset.orig=isNew?'':k;
  const key=document.createElement('input'); key.className='input'; key.value=k; key.placeholder='KEY';
  key.readOnly=!isNew; key.spellcheck=false;
  const val=document.createElement('textarea'); val.className='input cf-v'; val.value=v==null?'':String(v);
  val.rows=Math.min(8,Math.max(1,val.value.split('\\n').length,Math.ceil(val.value.length/70)));
  const del=document.createElement('button'); del.type='button'; del.className='btn btn-icon'; del.title='Remove key';
  del.setAttribute('aria-label','Remove key'); del.textContent='✕';
  del.onclick=()=>{if(isNew)row.remove();else row.classList.toggle('del')};
  row.append(key,val,del); document.getElementById('cfdlgrows').append(row);
  if(isNew)key.focus();
}
async function saveConfig(){
  const out=[], rows=[...document.querySelectorAll('#cfdlgrows .cf-row')];
  for(const r of rows){
    const [ki,vi]=r.querySelectorAll('.input'), k=ki.value.trim(), orig=r.dataset.orig;
    let value=null;
    if(orig&&r.classList.contains('del'))value='';
    else if(orig&&vi.value!==String(CFORIG[orig]??''))value=vi.value;
    else if(!orig&&k&&vi.value!=='')value=vi.value;
    if(value===null)continue;
    try{
      const d=await (await fetch(`/api/instances/${CFDLG}/config`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({key:orig||k,value})})).json();
      const m=String(d.msg||'');
      out.push((/^error/.test(m)?'⚠️ ':'✓ ')+(orig||k)+(/^error/.test(m)?': '+m.replace(/^error:\\s*/,''):value===''?' removed':' saved')
        +(/after stop\\/start/.test(m)?' (after restart)':/at once/.test(m)?' (live)':''));
    }catch(e){out.push('⚠️ '+(orig||k)+': '+e)}
  }
  const msg=document.getElementById('cfdlgmsg');
  msg.textContent=out.length?out.join(' · '):'No changes.';
  if(out.some(x=>/after restart/.test(x))){
    const b=document.getElementById('cfdlgrestart'); b.style.display='';
    b.onclick=()=>act(CFDLG,'restart');
  }
  if(out.length){const inst=(await (await fetch('/api/instances')).json()).find(i=>i.name===CFDLG)||{};CFORIG={...(inst.config||{})};
    [...document.querySelectorAll('#cfdlgrows .cf-row')].forEach(r=>r.remove());
    Object.keys(CFORIG).sort().forEach(k=>cfAddRow(k,CFORIG[k],false));}
}
function cfdlgClose(){document.getElementById('cfdlg').style.display='none'}
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
    const sk=await (await fetch('/api/secret-keys')).json(); keys=sk.keys||[]; SECSRC=sk.sources||{};
    SECPOL=await (await fetch('/api/secret-policy')).json();
  }catch(e){el.innerHTML='<span class=text-muted style="font-size:13px">not available</span>';return;}
  if(!keys.length){el.innerHTML='<span class=text-muted style="font-size:13px">No secret keys yet.</span>'+
    `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px"><input class="input mono" id=sk-name placeholder="NEW_KEY" style="width:220px;font-size:12.5px" autocomplete=off spellcheck=false>`+
    `<input class="input seckey" id=sk-val placeholder="value" style="width:300px" type=text autocomplete=off spellcheck=false><button class="btn btn-secondary btn-sm" onclick="secAdd()">Add</button></div>`;return;}
  const tpls=(TEMPLATES||[]).map(t=>t.template), bt=SECPOL.by_template||{}, gr=SECPOL.guest_readable||[];
  const head=`<tr><th>Secret key</th>`+tpls.map(t=>`<th style="text-align:center">${escT(t)}</th>`).join('')+`<th style="text-align:center" title="Only a key ticked here ever leaves the host as a raw value (get_secret). A release per template/instance alone lets the MCP hub substitute it on the host.">raw to guest</th></tr>`;
  const src=SECSRC||{};
  // Value column: the key is set (values never come to the browser); store
  // keys can be replaced or deleted here, settings keys point to their tab.
  const val=k=>src[k]==='settings'
    ?`<span class=text-muted style="font-size:11.5px">set · <a href="#settings">Settings</a></span>`
    :`<span class=text-muted style="font-size:11.5px">set</span> <button class="btn btn-ghost" style="font-size:11.5px;padding:2px 6px" title="Replace the value" onclick="secReplace('${esc(k)}')">replace</button>`+
     `<button class="btn btn-ghost" style="font-size:11.5px;padding:2px 6px;color:var(--color-neutral-600)" title="Delete from the store" onclick="secDelete('${esc(k)}')">delete</button>`;
  const body=keys.map(k=>`<tr><td data-label="Secret key" class=mono style="font-size:12.5px">${escT(k)}</td>`+
    `<td data-label="Value" style="white-space:nowrap">${val(k)}</td>`+
    tpls.map(t=>`<td data-label="${esc(t)}" style="text-align:center"><input type=checkbox data-tpl="${esc(t)}" value="${esc(k)}" ${(bt[t]||[]).indexOf(k)>=0?'checked':''}></td>`).join('')+
    `<td data-label="raw to guest" style="text-align:center"><input type=checkbox data-gr="1" value="${esc(k)}" ${gr.indexOf(k)>=0?'checked':''}></td></tr>`).join('');
  el.innerHTML=`<table class=table><thead>${head.replace('<th>Secret key</th>','<th>Secret key</th><th>Value</th>')}</thead><tbody>${body}</tbody></table>`+
    `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px">`+
    `<input class="input mono" id=sk-name placeholder="NEW_KEY" style="width:220px;font-size:12.5px" autocomplete=off spellcheck=false>`+
    `<input class="input seckey" id=sk-val placeholder="value" style="width:300px" type=text autocomplete=off spellcheck=false>`+
    `<button class="btn btn-secondary btn-sm" onclick="secAdd()">Add / replace</button>`+
    `<span class=text-muted style="font-size:11.5px">Written to the secret store on the host (0600), never shown again. LLM keys live in Settings.</span></div>`;
}
let SECSRC={};
async function secAdd(){
  const n=document.getElementById('sk-name').value.trim().toUpperCase(), v=document.getElementById('sk-val').value;
  if(!n||!v)return alert('Name and value?');
  const r=await (await fetch('/api/secret-store',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,value:v})})).json();
  document.getElementById('secmsg').textContent=r.msg||'';
  if(/added|replaced/.test(r.msg||'')){document.getElementById('sk-val').value='';renderSecrets();}
}
function secReplace(k){document.getElementById('sk-name').value=k;const v=document.getElementById('sk-val');v.value='';v.focus();}
async function secDelete(k){
  if(!confirm('Delete '+k+' from the secret store? Releases that use it stop working.'))return;
  const r=await (await fetch('/api/secret-store/'+encodeURIComponent(k)+'/delete',{method:'POST'})).json();
  document.getElementById('secmsg').textContent=r.msg||'';renderSecrets();
}
function saveSecrets(){
  const bt={};
  (TEMPLATES||[]).forEach(t=>{bt[t.template]=[];});
  const gr=[];
  document.querySelectorAll('#secrets input[type=checkbox]').forEach(c=>{
    if(c.dataset.gr){ if(c.checked)gr.push(c.value); return; }
    if(!bt[c.dataset.tpl])bt[c.dataset.tpl]=[];
    if(c.checked)bt[c.dataset.tpl].push(c.value);
  });
  fetch('/api/secret-policy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({by_template:bt,by_instance:(SECPOL.by_instance||{}),guest_readable:gr})})
    .then(r=>r.json()).then(d=>{document.getElementById('secmsg').textContent=(d.msg||'saved')+' ✓';});
}
window.onload=()=>{
  showTab(location.hash.slice(1));
  if(location.hash==='#squads')loadSquads();
  renderSettings();renderParams();renderSkillPick();loadMissions();loadProjects();loadRouter();loadAppsPanel();loadPrompts();loadPlaybooks();renderPersonas();renderSkills();renderSecrets();renderMcps();loadKatfs();loadIroh();loadModels2();loadChangelog();loadTools();loadPlugins();loadTasks();loadPolicy();loadResources();loadVersion();
  fleetInit();refreshUsage();loadSeries();
  // Tasks, policy and the usage numbers used to arrive only on page load —
  // whoever left the tab open saw arbitrarily stale state (and thought a
  // long-fixed problem was current). Refresh every 15s, but only for the
  // visible tab, not a background one.
  setInterval(()=>{
    if(document.hidden)return;
    const t=location.hash.slice(1)||'instances';
    if(t==='tasks'&&!TK_EDIT)loadTasks();
    else if(t==='missions')loadMissions();
    else if(t==='projects'&&!PJ_EDIT)loadProjects();
    else if(t==='policy')loadPolicy(true);
    else if(t==='resources')loadResources();
    else if(t==='instances'){refreshUsage();loadSeries();}
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
    const lk=n.link?` data-link="${nEsc(n.link)}" style="cursor:pointer" title="${n.link==='missions'?'To missions':n.link==='tasks'?'To tasks':n.link==='skills'?'To skills':'To chat'}"`:'';
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
  else if(link==='skills'){location.hash='#skills';loadProposals();}
  else if(link&&link.startsWith('chat:'))
    location.href='/chat?i='+encodeURIComponent(link.slice(5));   // same tab: no popup blocker
  else if(TABS.includes(link)){location.hash='#'+link;}           // any other tab by name (policy, …)
}
function notifDesktop(list){
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  for(const n of (list||[])){
    if(NOTIF_SEEN.has(n.id))continue;NOTIF_SEEN.add(n.id);
    if((n.ts||0)>=NOTIF_START && !n.read){try{const nt=new Notification(n.title||'kAIm56',{body:n.body||'',tag:n.id});if(n.link)nt.onclick=()=>{window.focus();notifClick(n.link);};}catch(e){}}
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
/* — who is logged in (OIDC only) and, for the admin, the session list — */
function whoLogout(){const w=document.getElementById('whoami');if(confirm(`${w.dataset.who}\n\nLog out?`))location.href='/auth/logout'}
/* The sidebar shows "Admin · basic" unless an OIDC login says who it is. */
async function loadMe(){
  let d={};try{d=await (await fetch('/api/auth/me')).json()}catch(e){return}
  if(!d.oidc||!d.name)return;
  document.getElementById('uname').textContent=d.name;
  document.getElementById('urole').textContent=d.role||'';
  document.getElementById('uavatar').textContent=(d.name.trim()[0]||'?').toUpperCase();
  const w=document.getElementById('whoami');w.hidden=false;w.dataset.who=`${d.name} · ${d.role}`;
  if(d.role==='admin')loadSessions();
}
async function loadSessions(){
  let d={};try{d=await (await fetch('/api/auth/sessions')).json()}catch(e){return}
  document.getElementById('sesspanel').hidden=false;
  document.getElementById('sesslist').innerHTML=(d.sessions||[]).map(s=>
    `<div style="display:flex;gap:10px;align-items:center;font-size:13px"><b>${escT(s.name)}</b>`+
    `<span class="tag tag-neutral">${escT(s.kind)}</span><span>${escT(s.device_name||'')}</span>`+
    `<span class=text-muted style="margin-left:auto">${new Date(s.last_use*1000).toLocaleString()}</span>`+
    `<button class="btn btn-ghost btn-sm" onclick="revokeSession(${+s.id})">Revoke</button></div>`).join('')
    ||'<span class=text-muted>none</span>';
}
async function revokeSession(id){
  await fetch('/api/auth/sessions/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  loadSessions();
}
loadMe();
</script></body></html>"""
