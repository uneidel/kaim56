#!/usr/bin/env python3
"""Chat-Oberflaeche fuer die Agent-microVMs (wird vom Manager unter /chat
ausgeliefert). Nur eine HTML-Seite, keine Abhaengigkeiten, kein CDN.

Backend ist der Manager:
  GET  /api/instances      Agentenliste + Laufzustand
  POST /api/chat/<name>    Prompt -> Antwort-Tokens als roher Text (Stream)

Der Verlauf liegt im localStorage des Browsers. Die microVM haelt ihre eigene
Session (claude --resume bzw. _history), deshalb geht pro Turn nur die neue
Nachricht raus — ein neuer Chat hier startet keine neue Agent-Session.
"""

PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>kAIm56 chat</title>
<link rel=icon type="image/svg+xml" href="/logo.svg">
<style>
:root{
  --bg:#f3f4f6;--panel:#ffffff;--panel-2:#f7f8fa;--border:#e4e7eb;
  --text:#1b1f24;--muted:#6b7280;--heading:#111418;
  --accent:#e8590c;--accent-contrast:#ffffff;
  --ok:#0a8a3f;--off:#9aa4b2;--bubble:#ececf1;
  --radius:14px;--shadow:0 1px 2px rgba(0,0,0,.05),0 10px 28px rgba(0,0,0,.05);
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0e1116;--panel:#161b22;--panel-2:#1b222b;--border:#2a313b;
  --text:#e6e9ee;--muted:#9aa4b2;--heading:#f2f5f9;
  --accent:#fb923c;--accent-contrast:#1a0f06;
  --ok:#3ddc84;--off:#6b7480;--bubble:#252c36;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;height:100dvh;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.55;-webkit-font-smoothing:antialiased}

/* ---- Sidebar ---- */
#side{width:270px;flex:none;background:var(--panel);border-right:1px solid var(--border);
  display:flex;flex-direction:column;transition:margin-left .2s ease}
#side.hidden{margin-left:-270px}
.side-top{padding:.75rem;border-bottom:1px solid var(--border)}
#new{width:100%;display:flex;align-items:center;justify-content:center;gap:.45rem;
  padding:.6rem;font:inherit;font-weight:600;font-size:.9rem;cursor:pointer;
  border:1px solid var(--border);border-radius:10px;background:var(--panel-2);color:var(--text)}
#new:hover{border-color:var(--accent)}
#convs{flex:1;overflow-y:auto;padding:.5rem}
.conv{display:flex;align-items:center;gap:.4rem;padding:.5rem .6rem;border-radius:9px;cursor:pointer;
  font-size:.88rem;color:var(--text);white-space:nowrap}
.conv:hover{background:var(--panel-2)}
.conv.sel{background:var(--panel-2);box-shadow:inset 0 0 0 1px var(--border)}
.conv .t{flex:1;overflow:hidden;text-overflow:ellipsis}
.conv .x{opacity:0;border:none;background:none;color:var(--muted);cursor:pointer;font-size:.95rem;padding:0 .15rem}
.conv:hover .x{opacity:1}
.conv .x:hover{color:var(--accent)}
.conv .ag{font-size:.68rem;color:var(--muted);border:1px solid var(--border);border-radius:5px;padding:0 .28rem}
.side-foot{padding:.7rem .8rem;border-top:1px solid var(--border);font-size:.82rem}
.side-foot a{color:var(--muted);text-decoration:none}
.side-foot a:hover{color:var(--accent)}

/* ---- Main ---- */
#main{flex:1;display:flex;flex-direction:column;min-width:0}
header{display:flex;align-items:center;gap:.6rem;padding:.6rem .9rem;border-bottom:1px solid var(--border);
  background:var(--panel);flex:none}
.icon{border:1px solid transparent;background:none;color:var(--muted);cursor:pointer;font-size:1.05rem;
  padding:.3rem .45rem;border-radius:8px;line-height:1}
.icon:hover{color:var(--text);border-color:var(--border)}
#agent{font:inherit;font-weight:600;font-size:.95rem;color:var(--text);background:var(--panel-2);
  border:1px solid var(--border);border-radius:9px;padding:.4rem .6rem;max-width:15rem}
#agent:focus{outline:2px solid var(--accent);outline-offset:1px}
#state{font-size:.8rem;color:var(--muted);display:flex;align-items:center;gap:.35rem}
.dot{width:.5rem;height:.5rem;border-radius:50%;background:var(--off);flex:none}
.dot.on{background:var(--ok)}
.grow{flex:1}

#log{flex:1;overflow-y:auto;padding:1.4rem .9rem 1rem}
.wrap{max-width:760px;margin:0 auto}
.row{display:flex;gap:.75rem;margin:0 0 1.5rem}
.row.me{justify-content:flex-end}
.av{width:30px;height:30px;flex:none;border-radius:50%;display:grid;place-items:center;font-size:.9rem;
  background:var(--panel-2);border:1px solid var(--border);color:var(--accent)}
.icon svg,.av svg{display:block}
.body{min-width:0;max-width:100%}
.row.me .body{background:var(--bubble);border-radius:16px;padding:.6rem .9rem;max-width:80%}
.body p{margin:.55rem 0}.body p:first-child{margin-top:0}.body p:last-child{margin-bottom:0}
.body h1,.body h2,.body h3{color:var(--heading);margin:1rem 0 .5rem;font-size:1.05rem}
.body ul,.body ol{margin:.5rem 0;padding-left:1.3rem}
.body li{margin:.2rem 0}
.body a{color:var(--accent)}
.body img{max-width:280px;border-radius:10px;margin:.3rem 0;display:block}
.body code{background:rgba(127,127,127,.16);padding:.08rem .34rem;border-radius:5px;font-size:.88em}
.body pre{position:relative;background:var(--panel);border:1px solid var(--border);border-radius:10px;
  padding:.8rem .9rem;overflow-x:auto;margin:.7rem 0}
.body pre code{background:none;padding:0;font-size:.85rem;line-height:1.5}
.cp{position:absolute;top:.4rem;right:.4rem;font:inherit;font-size:.72rem;color:var(--muted);cursor:pointer;
  background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:.15rem .4rem}
.cp:hover{color:var(--text);border-color:var(--accent)}
.tools{margin-top:.35rem;height:1.1rem}
.tools button{font:inherit;font-size:.75rem;color:var(--muted);background:none;border:none;cursor:pointer;padding:0;opacity:0}
.row:hover .tools button{opacity:1}
.tools button:hover{color:var(--accent)}
.cursor{display:inline-block;width:.5rem;height:1rem;background:var(--accent);vertical-align:-2px;
  animation:blink 1s step-end infinite}
@keyframes blink{50%{opacity:0}}

/* ---- Welcome ---- */
#hello{max-width:760px;margin:0 auto;padding:12vh 0 0;text-align:center}
#hello h2{font-size:1.55rem;color:var(--heading);margin:0 0 .4rem;letter-spacing:-.01em}
#hello p{color:var(--muted);margin:0 0 1.6rem;font-size:.92rem}
.chips{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:center}
.chips button{font:inherit;font-size:.85rem;color:var(--text);background:var(--panel);cursor:pointer;
  border:1px solid var(--border);border-radius:999px;padding:.45rem .9rem}
.chips button:hover{border-color:var(--accent)}

/* ---- Composer ---- */
#comp{flex:none;padding:.5rem .9rem 1rem;background:var(--bg)}
#box{max-width:760px;margin:0 auto;background:var(--panel);border:1px solid var(--border);
  border-radius:22px;box-shadow:var(--shadow);padding:.45rem .5rem .45rem .9rem}
#box:focus-within{border-color:var(--accent)}
#thumbs{display:flex;gap:.4rem;padding:.35rem 0 .1rem}
#thumbs:empty{display:none}
#thumbs .th{position:relative}
#thumbs img{height:54px;border-radius:8px;display:block}
#thumbs .x{position:absolute;top:-6px;right:-6px;background:var(--panel-2);border:1px solid var(--border);
  color:var(--text);border-radius:50%;width:18px;height:18px;font-size:.7rem;line-height:1;cursor:pointer}
.inrow{display:flex;align-items:flex-end;gap:.3rem}
#t{flex:1;border:none;background:none;color:var(--text);font:inherit;font-size:1rem;resize:none;
  max-height:180px;padding:.55rem 0;outline:none}
#send{flex:none;width:36px;height:36px;border-radius:50%;border:none;cursor:pointer;font-size:1rem;
  background:var(--accent);color:var(--accent-contrast)}
#send[disabled]{opacity:.35;cursor:default}
#send.stop{background:var(--text);color:var(--panel)}
.foot{max-width:760px;margin:.45rem auto 0;text-align:center;color:var(--muted);font-size:.72rem}
.home{display:flex;align-items:center;gap:8px;text-decoration:none;color:inherit}
.home svg{flex:none}

@media(max-width:820px){
  #side{position:absolute;z-index:5;height:100%;box-shadow:var(--shadow)}
  #side.hidden{margin-left:-270px}
  .row.me .body{max-width:92%}
}
</style></head><body>

<aside id=side>
  <div class=side-top><button id=new onclick=newChat()>＋ New chat</button></div>
  <div id=convs></div>
  <div class=side-foot><a href="/" class=home>__LOGO__<span>kAIm56</span></a></div>
</aside>

<div id=main>
  <header>
    <button class=icon title="Sidebar" onclick="document.getElementById('side').classList.toggle('hidden')">☰</button>
    <select id=agent onchange=pickAgent()></select>
    <span id=state><span class="dot"></span><span id=stateTxt>…</span></span>
    <span class=grow></span>
    <button class=icon id=termBtn title="Browser terminal" onclick=openTerm()><svg width=18 height=18 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><rect x=2 y=3 width=20 height=14 rx=2/><path d="M8 21h8M12 17v4"/></svg></button>
    <button class=icon title="Restart agent (resets the agent session)" onclick=restartAgent()><svg width=18 height=18 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>
  </header>

  <div id=log><div class=wrap id=msgs></div></div>

  <div id=comp>
    <div id=box>
      <div id=thumbs></div>
      <div class=inrow>
        <button class=icon id=clipBtn title="Attach an image (vision-capable agents only)" onclick="document.getElementById('file').click()"></button>
        <input type=file id=file accept="image/*" hidden onchange=addImage(this)>
        <textarea id=t rows=1 placeholder="Message the agent…" autofocus></textarea>
        <button id=send onclick=send() title="Send">➤</button>
      </div>
    </div>
    <div class=foot id=foot></div>
  </div>
</div>

<script>
// Inline-SVGs statt Farb-Emoji: die haengen sonst von einer Emoji-Schrift ab
// und erscheinen ohne sie als graue Kaestchen (Tofu). SVG rendert ueberall.
const _S='<svg width=18 height=18 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round>';
const IC={
  clip:_S+'<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>',
  term:_S+'<rect x=2 y=3 width=20 height=14 rx=2/><path d="M8 21h8M12 17v4"/></svg>',
  refresh:_S+'<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  bot:_S+'<rect x=4 y=9 width=16 height=11 rx=2/><path d="M12 9V5M9 3h6"/><circle cx=9 cy=14 r=1.2 fill=currentColor stroke=none/><circle cx=15 cy=14 r=1.2 fill=currentColor stroke=none/></svg>',
};
const AGENTS=__AGENTS__, START=__CURRENT__;
const $=id=>document.getElementById(id);
const KEY='fc-chat-convs';
let convs=[], cur=null, agent='', img=null, ctrl=null;

/* ---------- Persistenz ---------- */
function load(){
  try{convs=JSON.parse(localStorage.getItem(KEY))||[]}catch(e){convs=[]}
  convs.forEach(c=>(c.msgs||[]).forEach(m=>{delete m.busy}));  /* abgebrochener Stream */
}
function save(){try{localStorage.setItem(KEY,JSON.stringify(convs.slice(0,200)))}catch(e){}pushShared();}

/* ---------- Sync mit /api/chats (gemeinsamer Store mit der App) ----------
   Die App speichert Chats als {id,instance,title,updatedAt,mode,messages:[{user,text}]},
   diese Web-UI als {id,agent,title,ts,msgs:[{role,content}]}. Beide Formate hier
   ineinander abbilden, per updatedAt/ts mergen — so sehen App und Web denselben
   Verlauf. */
function toShared(list){return list.map(c=>({
  id:String(c.id), title:c.title||'', mode:'server', instance:c.agent||'',
  updatedAt:c.ts||0,
  messages:(c.msgs||[]).filter(m=>!m.busy).map(m=>({user:m.role==='user', text:m.content||'', image:m.image}))
}));}
function fromShared(list){return (list||[]).map(c=>({
  id:String(c.id), agent:c.instance||'', title:c.title||'', ts:c.updatedAt||0,
  msgs:(c.messages||[]).map(m=>({role:m.user?'user':'assistant', content:m.text||'', image:m.image}))
}));}
let _pushT=null;
function pushShared(delay){
  clearTimeout(_pushT);
  _pushT=setTimeout(()=>{fetch('/api/chats',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(toShared(convs))}).catch(()=>{});},
    delay===undefined?400:delay);
}
let CHATS_REV=0;
/* Remote in den lokalen Bestand mergen. true = lokal hat sich etwas geaendert.
   Der gerade streamende Chat bleibt unangetastet, sonst faellt der Teiltext weg. */
function applyRemote(remote){
  const byId={}; convs.forEach(c=>byId[c.id]=c);
  let changed=false;
  remote.forEach(r=>{
    if(ctrl&&cur&&r.id===cur.id)return;
    const l=byId[r.id];
    if(!l){byId[r.id]=r;changed=true;return;}
    if((r.ts||0)<=(l.ts||0))return;
    /* Nachrichten nur ANHAENGEN, nie ersetzen: sonst wischt ein Stand der
       Gegenseite eine gerade getippte, noch nicht gepushte Frage weg. Nur
       wenn die lokale Liste ein Praefix der entfernten ist, ist das sicher. */
    const lm=l.msgs||[], rm=r.msgs||[];
    if(rm.length<lm.length)return;
    for(let i=0;i<lm.length;i++)
      if(lm[i].role!==rm[i].role||lm[i].content!==rm[i].content)return;
    if(rm.length>lm.length){lm.push(...rm.slice(lm.length));l.msgs=lm;changed=true;}
    if(r.title&&l.title!==r.title){l.title=r.title;changed=true;}
    l.ts=r.ts;
  });
  if(!changed)return false;
  convs=Object.values(byId).sort((a,b)=>(b.ts||0)-(a.ts||0));
  try{localStorage.setItem(KEY,JSON.stringify(convs.slice(0,200)))}catch(e){}
  drawConvs();
  if(cur){const f=convs.find(c=>c.id===cur.id); if(f&&f!==cur){cur=f;draw();}}
  return true;
}
/* Einmal beim Laden: holen, danach die eigenen (nur lokal vorhandenen) Chats
   hochschieben — ab dann reicht der Push bei jeder lokalen Aenderung. */
async function syncChats(){
  try{
    const d=await (await fetch('/api/chats?since=0&wait=0')).json();
    CHATS_REV=d.rev||0;
    applyRemote(fromShared(d.chats||[]));
  }catch(e){}
  pushShared(0);
  chatSyncLoop();
}
/* Long-Poll: der Manager antwortet, sobald App ODER Web schreibt — dadurch
   stehen neue Nachrichten der anderen Seite in Sekundenbruchteilen hier. */
async function chatSyncLoop(){
  for(;;){
    try{
      const d=await (await fetch('/api/chats?wait=25&since='+CHATS_REV)).json();
      if(typeof d.rev==='number')CHATS_REV=d.rev;
      if(d.chats)applyRemote(fromShared(d.chats));
    }catch(e){ await new Promise(r=>setTimeout(r,3000)); }
  }
}

/* ---------- Markdown (klein, ohne Fremdcode) ---------- */
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function md(src){
  const lines=esc(src).split('\n'), out=[];
  const H=/^(#{1,6}) +(.*)$/, UL=/^\s*[-*+] +/, OL=/^\s*\d+[.)] +/, FENCE=/^\s*```/;
  const inline=s=>s
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')
    .replace(/(^|[^*\w])\*([^\s*][^*\n]*?)\*(?![\w*])/g,'$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,'<a href="$2" target=_blank rel=noopener>$1</a>');
  let i=0;
  while(i<lines.length){
    const l=lines[i];
    if(FENCE.test(l)){                       /* Codeblock (auch noch offen im Stream) */
      const buf=[];i++;
      while(i<lines.length&&!FENCE.test(lines[i]))buf.push(lines[i++]);
      i++;
      out.push('<pre><button class=cp onclick=copyCode(this)>Copy</button><code>'+buf.join('\n')+'</code></pre>');
      continue;
    }
    const h=l.match(H);
    if(h){const n=Math.min(h[1].length,3);out.push('<h'+n+'>'+inline(h[2])+'</h'+n+'>');i++;continue}
    if(UL.test(l)){const b=[];
      while(i<lines.length&&UL.test(lines[i]))b.push(inline(lines[i++].replace(UL,'')));
      out.push('<ul><li>'+b.join('</li><li>')+'</li></ul>');continue}
    if(OL.test(l)){const b=[];
      while(i<lines.length&&OL.test(lines[i]))b.push(inline(lines[i++].replace(OL,'')));
      out.push('<ol><li>'+b.join('</li><li>')+'</li></ol>');continue}
    if(!l.trim()){i++;continue}
    const b=[];                              /* Absatz bis Leerzeile/Blockanfang */
    while(i<lines.length&&lines[i].trim()&&!FENCE.test(lines[i])&&!H.test(lines[i])
          &&!UL.test(lines[i])&&!OL.test(lines[i]))b.push(inline(lines[i++]));
    out.push('<p>'+b.join('<br>')+'</p>');
  }
  return out.join('');
}
function copyCode(b){navigator.clipboard.writeText(b.parentNode.querySelector('code').textContent);
  const o=b.textContent;b.textContent='✓';setTimeout(()=>b.textContent=o,1200)}
function copyMsg(b,i){navigator.clipboard.writeText(cur.msgs[i].content);
  const o=b.textContent;b.textContent='✓ copied';setTimeout(()=>b.textContent=o,1200)}

/* ---------- Sidebar ---------- */
function drawConvs(){
  $('convs').innerHTML=convs.map(c=>
    `<div class="conv${cur&&c.id===cur.id?' sel':''}" onclick="openChat('${c.id}')">`+
    `<span class=t>${esc(c.title||'New chat')}</span>`+
    `<span class=ag>${esc(c.agent)}</span>`+
    `<button class=x title="Delete" onclick="delChat(event,'${c.id}')">✕</button></div>`).join('')
    ||'<div class=side-foot style="border:none">No chats yet.</div>';
}
function newChat(){cur=null;draw();drawConvs();$('t').focus()}
function openChat(id){cur=convs.find(c=>c.id===id)||null;
  if(innerWidth<820)$('side').classList.add('hidden');
  if(cur){agent=cur.agent;$('agent').value=agent;refreshState()}
  draw();drawConvs()}
function delChat(e,id){e.stopPropagation();
  convs=convs.filter(c=>c.id!==id);if(cur&&cur.id===id)cur=null;save();draw();drawConvs()}

/* ---------- Nachrichten ---------- */
function draw(){
  const m=$('msgs');
  if(!cur||!cur.msgs.length){
    m.innerHTML=`<div id=hello><h2>What can ${esc(agent||'the agent')} help with?</h2>`+
      `<p>Runs in its own microVM. The agent keeps its context across chats.</p>`+
      `<div class=chips>`+
      ['What is in my workspace?','Summarise the latest changes','Which tools do you have?']
        .map(s=>`<button onclick="suggest(this)">${esc(s)}</button>`).join('')+`</div></div>`;
    return;
  }
  m.innerHTML=cur.msgs.map((x,i)=>{
    const pic=x.image?`<img src="data:image/jpeg;base64,${x.image}" alt="">`:'';
    if(x.role==='user')
      return `<div class="row me"><div class=body>${pic}${esc(x.content).replace(/\n/g,'<br>')}</div></div>`;
    const busy=x.busy?'<span class=cursor></span>':'';
    const tools=x.busy?'':`<div class=tools><button onclick="copyMsg(this,${i})">Copy</button></div>`;
    return `<div class=row><div class=av>${IC.bot}</div><div class=body>${md(x.content)}${busy}${tools}</div></div>`;
  }).join('');
  scroll();
}
function scroll(){const l=$('log');l.scrollTop=l.scrollHeight}
function atBottom(){const l=$('log');return l.scrollHeight-l.scrollTop-l.clientHeight<80}
let pend=false;
function paint(){                      /* Streaming: nur den letzten Block updaten */
  if(pend)return; pend=true;
  requestAnimationFrame(()=>{
    pend=false;
    const rows=$('msgs').querySelectorAll('.row');
    const b=rows.length?rows[rows.length-1].querySelector('.body'):null;
    if(!b)return draw();
    const stick=atBottom();
    b.innerHTML=md(cur.msgs[cur.msgs.length-1].content)+'<span class=cursor></span>';
    if(stick)scroll();
  });
}
function suggest(b){$('t').value=b.textContent;$('t').focus();autogrow()}

/* ---------- Agenten ---------- */
function drawAgents(){
  $('agent').innerHTML=AGENTS.map(a=>`<option value="${esc(a.name)}">${esc(a.name)}</option>`).join('')
    ||'<option value="">no web instance</option>';
  agent=(AGENTS.find(a=>a.name===START)||AGENTS[0]||{}).name||'';
  $('agent').value=agent;
}
function pickAgent(){agent=$('agent').value;if(cur)cur.agent=agent;save();refreshState();if(!cur)draw()}
async function refreshState(){
  let a=null;
  try{a=(await (await fetch('/api/instances')).json()).find(i=>i.name===agent)}catch(e){}
  const on=!!(a&&a.running);
  document.querySelector('#state .dot').className='dot'+(on?' on':'');
  $('stateTxt').textContent=on?'running':'off — starts on the first prompt';
  $('foot').textContent=`The agent keeps its own session — a new chat here does not reset it (restart button above).`;
}
function openTerm(){if(agent)window.open('/i/'+encodeURIComponent(agent)+'/term/','_blank')}
async function restartAgent(){
  if(!agent||!confirm(`Restart ${agent}? The running agent session is lost.`))return;
  $('stateTxt').textContent='restarting…';
  await fetch(`/api/instances/${encodeURIComponent(agent)}/stop`,{method:'POST'});
  await fetch(`/api/instances/${encodeURIComponent(agent)}/start`,{method:'POST'});
  setTimeout(refreshState,3000);
}

/* ---------- Bilder ---------- */
function addImage(inp){
  const f=inp.files&&inp.files[0];inp.value='';
  if(!f)return;
  const r=new FileReader();
  r.onload=()=>{img=String(r.result).split(',')[1]||null;drawThumb()};
  r.readAsDataURL(f);
}
function drawThumb(){
  $('thumbs').innerHTML=img?`<div class=th><img src="data:image/jpeg;base64,${img}" alt="">`+
    `<button class=x onclick="img=null;drawThumb()">✕</button></div>`:'';
}

/* ---------- Senden ---------- */
function autogrow(){const t=$('t');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,180)+'px'}
$('t').addEventListener('input',autogrow);
$('t').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});

async function send(){
  if(ctrl){ctrl.abort();return}
  const text=$('t').value.trim();
  if(!text&&!img)return;
  if(!agent)return alert('No instance with TRANSPORT=web available.');
  if(!cur){cur={id:String(Date.now()),agent:agent,title:(text||'Image').slice(0,42),ts:Date.now(),msgs:[]};
    convs.unshift(cur)}
  cur.msgs.push({role:'user',content:text,image:img||undefined});
  const reply={role:'assistant',content:'',busy:true};
  cur.msgs.push(reply);
  const payload={message:text}; if(img)payload.image=img;
  img=null;drawThumb();$('t').value='';autogrow();
  cur.ts=Date.now();save();draw();drawConvs();
  ctrl=new AbortController();
  $('send').textContent='■';$('send').classList.add('stop');
  try{
    const r=await fetch('/api/chat/'+encodeURIComponent(agent),
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:ctrl.signal});
    const rd=r.body.getReader(),dec=new TextDecoder();
    for(;;){
      const {done,value}=await rd.read();
      if(done)break;
      reply.content+=dec.decode(value,{stream:true});
      paint();
    }
  }catch(e){
    reply.content+=(e&&e.name==='AbortError')?'\n\n_(aborted)_':'\n\n⚠️ '+e;
  }finally{
    ctrl=null;reply.busy=false;
    if(!reply.content)reply.content='_(empty reply)_';
    $('send').textContent='➤';$('send').classList.remove('stop');
    save();draw();refreshState();
  }
}

/* ---------- Start ---------- */
// Statische Icon-Buttons einmal befuellen (Composer + Kopfzeile) — SVG statt
// Emoji, damit ohne Emoji-Schrift keine grauen Kaestchen erscheinen.
$('termBtn').innerHTML=IC.term;
$('clipBtn').innerHTML=IC.clip;
[...document.querySelectorAll('.icon')].forEach(b=>{
  if(b.title&&b.title.includes('Restart agent'))b.innerHTML=IC.refresh;
});
load();drawAgents();syncChats();
const last=convs.find(c=>c.agent===agent);
if(last&&!START)openChat(last.id); else {draw();drawConvs()}
refreshState();setInterval(refreshState,15000);
</script></body></html>"""


def render(agents, current="", logo=""):
    """agents: Liste von {name, running, description} (Instanzen mit TRANSPORT=web)."""
    import json
    return (PAGE.replace("__LOGO__", logo)
                .replace("__AGENTS__", json.dumps(agents, ensure_ascii=False))
                .replace("__CURRENT__", json.dumps(current)))
