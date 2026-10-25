// kAIm56 desktop — the window. It only draws; the Rust side (src-tauri) talks
// to the manager over iroh and fires "chats", "waiting", "notif", "status".
// SPDX-License-Identifier: AGPL-3.0-or-later
const {invoke} = window.__TAURI__.core;
const {listen} = window.__TAURI__.event;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let CHATS = [], BUSY = null, CUR = localStorage.getItem("chat") || "", VIEW = "chat";

/* ---- views ---- */
function show(v) {
  VIEW = v;
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("act", b.dataset.view === v));
  document.querySelectorAll(".view").forEach(s => s.hidden = s.id !== "v-" + v);
  if (v === "waiting") loadWaiting();
  if (v === "notifs") loadNotifs();
  if (v === "settings") loadSettings();
}
document.querySelector("nav").onclick = e => { const b = e.target.closest("button"); if (b) show(b.dataset.view); };

/* ---- chats ---- */
async function loadChats() {
  const d = await invoke("chats_list");
  CHATS = d.chats; BUSY = d.busy;
  if (!CHATS.some(c => c.id === CUR)) CUR = CHATS[0]?.id || "";
  drawList(); drawChat();
}
function drawList() {
  $("chats").innerHTML = CHATS.map(c =>
    `<div class="citem${c.id === CUR ? " act" : ""}" data-id="${esc(c.id)}">${esc(c.title || "Chat")}<small>${esc(c.instance)} · ${new Date(c.updatedAt).toLocaleString()}</small></div>`).join("")
    || '<div class="muted">No chats yet.</div>';
}
$("chats").onclick = e => {
  const it = e.target.closest(".citem"); if (!it) return;
  CUR = it.dataset.id; localStorage.setItem("chat", CUR); drawList(); drawChat(); show("chat");
};
const mdCache = new Map();
async function md(t) {
  if (!mdCache.has(t)) { if (mdCache.size > 500) mdCache.clear(); mdCache.set(t, await invoke("md", {text: t})); }
  return mdCache.get(t);
}
let drawing = false, again = false;
async function drawChat() {
  if (drawing) { again = true; return; }
  drawing = true;
  try {
    const c = CHATS.find(x => x.id === CUR);
    $("ctitle").textContent = c ? (c.title || "Chat") : "No chat";
    $("cagent").textContent = c ? "· " + c.instance : "";
    $("cdel").hidden = !c;
    const box = $("msgs"), atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const html = [];
    for (const [i, m] of (c?.messages || []).entries()) {
      const last = i === c.messages.length - 1 && BUSY === c.id;
      html.push(m.user ? `<div class="msg me">${esc(m.text)}</div>`
                       : `<div class="msg${last ? " typing" : ""}">${m.text ? await md(m.text) : ""}</div>`);
    }
    box.innerHTML = html.join("") || '<div class="muted">Ask something.</div>';
    if (atEnd) box.scrollTop = box.scrollHeight;
    $("stop").hidden = BUSY !== CUR || !c; $("send").disabled = !!BUSY;
  } finally {
    drawing = false;
    if (again) { again = false; drawChat(); }
  }
}
$("composer").onsubmit = async e => {
  e.preventDefault();
  const t = $("input").value.trim(); if (!t || BUSY) return;
  if (!CUR) { await newChat(); if (!CUR) return; }
  $("input").value = "";
  try { await invoke("chat_send", {id: CUR, text: t}); } catch (err) { $("input").value = t; alert(err); }
};
$("input").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); } };
$("stop").onclick = () => invoke("chat_cancel");
async function newChat() {
  const a = $("agent").value; if (!a) { alert("No agent — check the settings."); return; }
  CUR = await invoke("chat_new", {instance: a}); localStorage.setItem("chat", CUR);
  await loadChats(); show("chat"); $("input").focus();
}
$("newchat").onclick = newChat;
$("cdel").onclick = async () => { if (CUR && confirm("Delete this chat on all devices?")) { await invoke("chat_delete", {id: CUR}); CUR = ""; } };
async function loadAgents() {
  try {
    const list = await invoke("agents"), keep = $("agent").value || localStorage.getItem("agent");
    $("agent").innerHTML = list.map(a => `<option value="${esc(a.name)}">${esc(a.name)}${a.running ? "" : " (off)"}</option>`).join("");
    if (keep && list.some(a => a.name === keep)) $("agent").value = keep;
  } catch (e) { /* offline: the status dot says it */ }
}
$("agent").onchange = () => localStorage.setItem("agent", $("agent").value);

/* ---- waiting for you ---- */
const ACTIONS = {approval: ["approve", "deny"], question: ["answer"], review: ["approve", "request_changes"]};
async function loadWaiting() {
  let d;
  try { d = await invoke("waiting_list"); } catch (e) { $("wlist").innerHTML = `<div class="card err">${esc(e)}</div>`; return; }
  const items = d.items || [];
  badge("wcount", items.length);
  $("wlist").innerHTML = items.map(it => `
    <div class="card" data-id="${esc(it.id)}" data-kind="${esc(it.kind)}">
      <div class="meta">${esc(it.kind)} · ${esc([it.project, it.instance, it.ticket].filter(Boolean).join(" · "))}</div>
      <h3>${esc(it.title)}</h3>
      ${it.detail && it.detail !== it.title ? `<div class="detail">${esc(it.detail)}</div>` : ""}
      ${it.tool ? `<div class="meta">${esc(it.tool)} ${esc(it.target)}</div>` : ""}
      ${(it.options || []).length ? `<div class="opts">${it.options.map(o => `<button type="button" class="opt">${esc(o)}</button>`).join("")}</div>` : ""}
      ${it.kind !== "approval" ? `<textarea rows="2" placeholder="${it.kind === "question" ? "Your answer" : "Comment"}"></textarea>` : ""}
      <div class="row"><span class="err"></span>${(ACTIONS[it.kind] || []).map(a => `<button type="button" class="act${a === "approve" || a === "answer" ? " pri" : ""}" data-a="${a}">${a.replace("_", " ")}</button>`).join("")}</div>
    </div>`).join("") || '<div class="muted">Nothing is waiting for you.</div>';
}
$("wlist").onclick = async e => {
  const card = e.target.closest(".card"); if (!card) return;
  const opt = e.target.closest(".opt");
  if (opt) { card.querySelectorAll(".opt").forEach(b => b.classList.toggle("sel", b === opt && !b.classList.contains("sel"))); return; }
  const btn = e.target.closest("button.act"); if (!btn) return;
  const kind = card.dataset.kind, a = btn.dataset.a;
  const text = card.querySelector("textarea")?.value || "", choice = card.querySelector(".opt.sel")?.textContent || "";
  const err = card.querySelector(".err");
  if (kind === "question" && !text.trim() && !choice) { err.textContent = "Type or choose an answer"; return; }
  if (a === "request_changes" && !text.trim()) { err.textContent = "Say what should change"; return; }
  try { await invoke("waiting_act", {id: card.dataset.id, action: a, text, choice}); loadWaiting(); }
  catch (x) { err.textContent = String(x); if (String(x).includes("409")) setTimeout(loadWaiting, 1200); }
};
$("wreload").onclick = loadWaiting;

/* ---- notifications ---- */
async function loadNotifs() {
  const list = (await invoke("notifications")).slice().sort((a, b) => b.ts - a.ts);
  badge("ncount", list.filter(n => !n.read).length);
  $("nlist").innerHTML = list.map(n => `
    <div class="card${n.read ? "" : " unread"}">
      <div class="meta">${new Date(n.ts * 1000).toLocaleString()}${n.instance ? " · " + esc(n.instance) : ""}</div>
      <h3>${esc(n.title)}</h3><div class="detail">${esc(n.body)}</div>
      ${/^(waiting|squads|projects)/.test(n.link || "") || (n.title || "").startsWith("Waiting for you") ? '<button class="ghost go-w">Open Waiting</button>' : ""}
    </div>`).join("") || '<div class="muted">No notifications.</div>';
}
$("nlist").onclick = e => { if (e.target.closest(".go-w")) show("waiting"); };
$("nread").onclick = async () => { try { await invoke("notifications_read"); } catch (e) { alert(e); } };
function badge(id, n) { $(id).textContent = n; $(id).hidden = !n; }

/* ---- settings ---- */
async function loadSettings() {
  const s = await invoke("settings_get");
  $("nodeid").value = s.node_id; $("gateway").value = s.gateway; $("user").value = s.user;
  $("pass").placeholder = s.has_pass ? "unchanged" : "required"; $("ver").textContent = s.version;
  return s;
}
$("sform").onsubmit = async e => {
  e.preventDefault(); $("smsg").textContent = "connecting…";
  try {
    await invoke("settings_set", {gateway: $("gateway").value, user: $("user").value, pass: $("pass").value});
    $("pass").value = ""; $("smsg").textContent = "connected"; loadAgents();
  } catch (err) { $("smsg").textContent = String(err); }
};
$("nodeid").onclick = () => { $("nodeid").select(); navigator.clipboard?.writeText($("nodeid").value); };
async function checkUpdate(manual) {
  try {
    const u = await invoke("update_check");
    if (u) {
      $("update").innerHTML = `Version ${esc(u.version)} is available — <a href="#" id="getup">download</a>`;
      $("update").hidden = false;
      $("getup").onclick = ev => { ev.preventDefault(); invoke("open_url", {url: u.url}); };
    } else if (manual) alert("This is the newest version.");
  } catch (e) { if (manual) alert(e); }
}
$("upcheck").onclick = e => { e.preventDefault(); checkUpdate(true); };

/* ---- events from the Rust side ---- */
listen("chats", () => loadChats());
listen("waiting", ev => { badge("wcount", ev.payload); if (VIEW === "waiting") loadWaiting(); });
listen("notif", () => loadNotifs());
listen("status", ev => {
  const s = String(ev.payload), on = s === "online";
  $("status").className = "dot " + (on ? "on" : "off"); $("status").title = s;
  if (on && !$("agent").options.length) loadAgents();
});

(async () => {
  const s = await loadSettings();
  await loadChats();
  if (!s.gateway || !s.user) show("settings"); else loadAgents();
  checkUpdate(false);
})();
