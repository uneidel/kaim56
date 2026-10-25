// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// kAIm56 desktop (macOS): the phone app's core — chat with the agents (one chat
// store with the phone and the web UI), the "Waiting for you" queue, the
// manager's notifications as system notifications, an update check — over the
// iroh transport (tunnel.rs), never the manager's public URL.
//
// The window (ui/) only draws: it calls the commands below and listens to
// three events: "chats" (the store changed), "waiting" (open items), "notif"
// (new notifications); "status" says whether the manager is reachable.
pub mod api;
pub mod chats;
pub mod tunnel;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_notification::NotificationExt;
use tokio::sync::Mutex;

use chats::{Conversation, Msg};

const GITHUB_REPO: &str = "uneidel/kaim56";
const RELEASE_TAG: &str = "mac-v";
const POLL_WAIT: u64 = 25;                       // s a long-poll may wait on the manager

#[derive(Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default)]
    gateway: String,                              // the manager's iroh NodeId (web UI: iroh tab)
    #[serde(default)]
    user: String,
    #[serde(default)]
    pass: String,
}

#[derive(Default)]
struct Inner {
    settings: Settings,
    api: Option<api::Api>,
    tunnel: Option<tunnel::Tunnel>,
    gen: u64,                                     // bumped per (re)connect: old loops end
    convs: Vec<Conversation>,
    tombs: HashMap<String, i64>,
    busy: Option<String>,                         // the chat a reply streams into
    stream: Option<tokio::task::AbortHandle>,
    notif_ts: i64,                                // newest notification already shown
    notifs: Vec<Value>,
}

#[derive(Clone)]
struct App(Arc<Mutex<Inner>>, PathBuf);

fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

impl App {
    fn file(&self, name: &str) -> PathBuf {
        self.1.join(name)
    }

    fn read<T: for<'a> Deserialize<'a> + Default>(&self, name: &str) -> T {
        std::fs::read(self.file(name)).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
    }

    /// Our files: the folder and every file 0600 — the password is in settings.json.
    fn write(&self, name: &str, v: &impl Serialize) {
        let _ = std::fs::create_dir_all(&self.1);
        let p = self.file(name);
        let tmp = p.with_extension("tmp");
        if std::fs::write(&tmp, serde_json::to_vec(v).unwrap_or_default()).is_ok() {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
            }
            let _ = std::fs::rename(&tmp, &p);
        }
    }

    async fn save_chats(&self) {
        let g = self.0.lock().await;
        self.write("chats.json", &g.convs);
        self.write("tombstones.json", &g.tombs);
    }

    /// Our chats to the manager (it merges; nothing is overwritten).
    async fn push(&self) {
        let (api, body) = {
            let g = self.0.lock().await;
            let srv: Vec<&Conversation> = g.convs.iter().filter(|c| c.mode == "server").collect();
            (g.api.clone(), json!({"chats": srv, "tombstones": g.tombs}))
        };
        if let Some(api) = api {
            let _ = api.post("/api/chats", &body).await;
        }
    }
}

fn emit_chats(app: &AppHandle) {
    let _ = app.emit("chats", ());
}

/// (Re)connect: a fresh tunnel and API with the current settings, then the
/// background loops (chats, notifications, waiting) of this generation.
async fn connect(app: AppHandle, st: App) -> Result<(), String> {
    let (key, s, gen) = {
        let mut g = st.0.lock().await;
        g.gen += 1;
        g.api = None;
        g.tunnel = None;
        (tunnel::secret(&st.file("node.key")).map_err(|e| e.to_string())?, g.settings.clone(), g.gen)
    };
    if s.gateway.trim().is_empty() || s.user.is_empty() {
        let _ = app.emit("status", "not set up");
        return Err("enter the gateway NodeId, user and password".into());
    }
    let t = tunnel::start(key, &s.gateway).await.map_err(|e| format!("{e:#}"))?;
    let api = api::Api::new(t.port, &s.user, &s.pass);
    {
        let mut g = st.0.lock().await;
        g.tunnel = Some(t);
        g.api = Some(api.clone());
    }
    tokio::spawn(chat_loop(app.clone(), st.clone(), api.clone(), gen));
    tokio::spawn(notif_loop(app.clone(), st.clone(), api.clone(), gen));
    tokio::spawn(waiting_loop(app, st, api, gen));
    Ok(())
}

async fn alive(st: &App, gen: u64) -> bool {
    st.0.lock().await.gen == gen
}

/// The shared chat store, live: a long-poll that answers when the phone or the
/// web UI writes. First round: everything, then push ours.
async fn chat_loop(app: AppHandle, st: App, api: api::Api, gen: u64) {
    let mut rev = 0i64;
    let mut first = true;
    while alive(&st, gen).await {
        match api.get(&format!("/api/chats?since={rev}&wait={}", if first { 0 } else { POLL_WAIT }), POLL_WAIT + 20).await {
            Ok(v) => {
                let _ = app.emit("status", "online");
                rev = v.get("rev").and_then(Value::as_i64).unwrap_or(rev);
                let remote: Vec<Conversation> = v.get("chats").cloned().and_then(|c| serde_json::from_value(c).ok()).unwrap_or_default();
                let tombs: HashMap<String, i64> = v.get("tombstones").cloned().and_then(|t| serde_json::from_value(t).ok()).unwrap_or_default();
                let changed = {
                    let mut g = st.0.lock().await;
                    for (k, t) in tombs {
                        let e = g.tombs.entry(k).or_insert(t);
                        *e = (*e).max(t);
                    }
                    let busy = g.busy.clone();
                    let Inner { convs, tombs, .. } = &mut *g;
                    let a = chats::apply_tombs(convs, tombs);
                    chats::merge(convs, remote, tombs, busy.as_deref()) || a
                };
                if changed {
                    st.save_chats().await;
                    emit_chats(&app);
                }
                if first {
                    st.push().await;
                    first = false;
                }
            }
            Err(e) => {
                let _ = app.emit("status", format!("offline: {e}"));
                tokio::time::sleep(Duration::from_secs(10)).await;
            }
        }
    }
}

/// New, unread notifications -> system notifications; the watermark survives
/// restarts (the first run shows only the last day).
async fn notif_loop(app: AppHandle, st: App, api: api::Api, gen: u64) {
    let mut rev = 0i64;
    let mut first = true;
    while alive(&st, gen).await {
        let path = format!("/api/notifications?since={rev}&wait={}", if first { 0 } else { POLL_WAIT });
        let Ok(v) = api.get(&path, POLL_WAIT + 20).await else {
            tokio::time::sleep(Duration::from_secs(15)).await;
            continue;
        };
        first = false;
        rev = v.get("rev").and_then(Value::as_i64).unwrap_or(rev);
        let Some(items) = v.get("notifications").and_then(Value::as_array).cloned() else { continue };
        let fresh: Vec<Value> = {
            let mut g = st.0.lock().await;
            let floor = if g.notif_ts > 0 { g.notif_ts } else { now_ms() / 1000 - 24 * 3600 };
            let ts = |n: &Value| n.get("ts").and_then(Value::as_f64).unwrap_or(0.0) as i64;
            let fresh = items.iter().filter(|n| !n.get("read").and_then(Value::as_bool).unwrap_or(false) && ts(n) > floor).cloned().collect();
            g.notif_ts = items.iter().map(ts).max().unwrap_or(0).max(g.notif_ts).max(if g.notif_ts == 0 { now_ms() / 1000 } else { 0 });
            g.notifs = items;
            st.write("notif.json", &g.notif_ts);
            fresh
        };
        for n in &fresh {
            let s = |k: &str| n.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            let _ = app.notification().builder().title(s("title")).body(s("body")).show();
        }
        let _ = app.emit("notif", fresh.len());
    }
}

/// The "Waiting for you" count, once a minute (the badge in the sidebar).
async fn waiting_loop(app: AppHandle, st: App, api: api::Api, gen: u64) {
    while alive(&st, gen).await {
        if let Ok(v) = api.get("/api/waiting?status=open", 30).await {
            let n = v.get("items").and_then(Value::as_array).map(|a| a.len()).unwrap_or(0);
            let _ = app.emit("waiting", n);
        }
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

async fn api_of(st: &App) -> Result<api::Api, String> {
    st.0.lock().await.api.clone().ok_or_else(|| "not connected — check the settings".to_string())
}

// ---- commands (the window calls these) ----------------------------------------
#[tauri::command]
async fn settings_get(st: State<'_, App>) -> Result<Value, String> {
    let g = st.0.lock().await;
    let id = tunnel::secret(&st.file("node.key")).map(|k| k.public().to_string()).unwrap_or_default();
    Ok(json!({"gateway": g.settings.gateway, "user": g.settings.user, "has_pass": !g.settings.pass.is_empty(),
              "node_id": id, "version": env!("CARGO_PKG_VERSION")}))
}

#[tauri::command]
async fn settings_set(app: AppHandle, st: State<'_, App>, gateway: String, user: String, pass: String) -> Result<(), String> {
    {
        let mut g = st.0.lock().await;
        g.settings.gateway = gateway.trim().into();
        g.settings.user = user.trim().into();
        if !pass.is_empty() {
            g.settings.pass = pass;
        }
        st.write("settings.json", &g.settings);
    }
    connect(app, st.inner().clone()).await
}

#[tauri::command]
async fn agents(st: State<'_, App>) -> Result<Value, String> {
    let v = api_of(&st).await?.get("/api/instances", 30).await.map_err(|e| e.to_string())?;
    Ok(Value::Array(v.as_array().cloned().unwrap_or_default().into_iter()
        .map(|i| json!({"name": i["name"], "running": i["running"], "template": i["template"]})).collect()))
}

#[tauri::command]
async fn chats_list(st: State<'_, App>) -> Result<Value, String> {
    let g = st.0.lock().await;
    Ok(json!({"chats": g.convs.iter().filter(|c| c.mode == "server").collect::<Vec<_>>(), "busy": g.busy}))
}

#[tauri::command]
async fn chat_new(st: State<'_, App>, instance: String) -> Result<String, String> {
    let c = Conversation::new(&instance, now_ms());
    let id = c.id.clone();
    st.0.lock().await.convs.insert(0, c);
    st.save_chats().await;
    Ok(id)
}

#[tauri::command]
async fn chat_delete(app: AppHandle, st: State<'_, App>, id: String) -> Result<(), String> {
    {
        let mut g = st.0.lock().await;
        g.convs.retain(|c| c.id != id);
        g.tombs.insert(id, now_ms());
    }
    st.save_chats().await;
    st.push().await;
    emit_chats(&app);
    Ok(())
}

/// Ask the chat's agent: the question and an empty reply go in at once (with
/// our turn id), the reply streams in; "chats" fires as it grows.
#[tauri::command]
async fn chat_send(app: AppHandle, st: State<'_, App>, id: String, text: String) -> Result<(), String> {
    let api = api_of(&st).await?;
    let turn = uuid::Uuid::new_v4().simple().to_string()[..12].to_string();
    let instance = {
        let mut g = st.0.lock().await;
        if g.busy.is_some() {
            return Err("a reply is still coming in".into());
        }
        let c = g.convs.iter_mut().find(|c| c.id == id).ok_or("chat gone")?;
        c.messages.push(Msg { user: true, text: text.clone(), image: None, turn: None });
        c.messages.push(Msg { user: false, text: String::new(), image: None, turn: Some(turn.clone()) });
        c.touch(now_ms());
        let inst = c.instance.clone();
        g.busy = Some(id.clone());
        inst
    };
    st.save_chats().await;
    st.push().await;
    emit_chats(&app);
    let st2 = st.inner().clone();
    let body = json!({"message": text, "chat": id, "turn": turn});
    let task = tokio::spawn(async move {
        let add = |piece: String| {
            let st2 = st2.clone();
            let id = id.clone();
            async move {
                let mut g = st2.0.lock().await;
                if let Some(m) = g.convs.iter_mut().find(|c| c.id == id).and_then(|c| c.messages.last_mut()) {
                    m.text.push_str(&piece);
                }
            }
        };
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        let path = format!("/i/{instance}/api/chat/stream");
        let run = api.stream(&path, &body, move |s| { let _ = tx.send(s.to_string()); });
        let pump = async {
            while let Some(p) = rx.recv().await {
                add(p).await;
                emit_chats(&app);
            }
        };
        let (res, _) = tokio::join!(run, pump);
        if let Err(e) = res {
            add(format!("\n⚠️ {e}")).await;
        }
        {
            let mut g = st2.0.lock().await;
            g.busy = None;
            g.stream = None;
            if let Some(c) = g.convs.iter_mut().find(|c| c.id == id) {
                c.touch(now_ms());
            }
        }
        st2.save_chats().await;
        st2.push().await;
        emit_chats(&app);
    });
    st.0.lock().await.stream = Some(task.abort_handle());
    Ok(())
}

/// Stop the reply: the connection closes, what came so far stays.
#[tauri::command]
async fn chat_cancel(app: AppHandle, st: State<'_, App>) -> Result<(), String> {
    {
        let mut g = st.0.lock().await;
        if let Some(h) = g.stream.take() {
            h.abort();
        }
        if let Some(id) = g.busy.take() {
            if let Some(c) = g.convs.iter_mut().find(|c| c.id == id) {
                if let Some(m) = c.messages.last_mut() {
                    m.text.push_str("\n_(stopped)_");
                }
                c.touch(now_ms());
            }
        }
    }
    st.save_chats().await;
    st.push().await;
    emit_chats(&app);
    Ok(())
}

#[tauri::command]
async fn waiting_list(st: State<'_, App>) -> Result<Value, String> {
    api_of(&st).await?.get("/api/waiting?status=open", 30).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn waiting_act(st: State<'_, App>, id: String, action: String, text: String, choice: String) -> Result<Value, String> {
    let mut b = json!({"action": action});
    if !text.trim().is_empty() {
        b["text"] = json!(text.trim());
    }
    if !choice.is_empty() {
        b["choice"] = json!(choice);
    }
    let path = format!("/api/waiting/{}", id.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_').collect::<String>());
    api_of(&st).await?.post(&path, &b).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn notifications(st: State<'_, App>) -> Result<Value, String> {
    Ok(Value::Array(st.0.lock().await.notifs.clone()))
}

#[tauri::command]
async fn notifications_read(st: State<'_, App>) -> Result<(), String> {
    api_of(&st).await?.post("/api/notifications/read", &json!({"all": true})).await.map(|_| ()).map_err(|e| e.to_string())
}

/// Markdown -> HTML for a bubble; raw HTML in the text is shown as text.
#[tauri::command]
fn md(text: String) -> String {
    use pulldown_cmark::{Event, Options, Parser};
    let p = Parser::new_ext(&text, Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TASKLISTS)
        .map(|e| match e {
            Event::Html(h) | Event::InlineHtml(h) => Event::Text(h),
            e => e,
        });
    let mut out = String::new();
    pulldown_cmark::html::push_html(&mut out, p);
    out
}

/// Version strings compared as numbers ("0.10.0" > "0.9.1").
fn newer(a: &str, b: &str) -> bool {
    let n = |s: &str| s.split('.').map(|x| x.parse::<u64>().unwrap_or(0)).collect::<Vec<_>>();
    n(a) > n(b)
}

/// The newest mac-v* release on GitHub, if it is newer than this build.
#[tauri::command]
async fn update_check() -> Result<Value, String> {
    let rel: Value = reqwest::Client::new()
        .get(format!("https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=30"))
        .header("User-Agent", "kaim56-desktop")
        .timeout(Duration::from_secs(20))
        .send().await.map_err(|e| e.to_string())?
        .json().await.map_err(|e| e.to_string())?;
    let best = rel.as_array().into_iter().flatten()
        .filter_map(|r| Some((r["tag_name"].as_str()?.strip_prefix(RELEASE_TAG)?.to_string(), r)))
        .filter(|(v, _)| newer(v, env!("CARGO_PKG_VERSION")))
        .max_by(|a, b| if newer(&a.0, &b.0) { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Less });
    Ok(match best {
        Some((v, r)) => json!({"version": v, "url": r["html_url"], "notes": r["body"].as_str().unwrap_or("").chars().take(2000).collect::<String>()}),
        None => Value::Null,
    })
}

/// A release page in the browser (https only).
#[tauri::command]
fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    if !url.starts_with("https://") {
        return Err("not an https link".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let dir = app.path().app_config_dir()?;
            let st = App(Arc::new(Mutex::new(Inner::default())), dir);
            {
                let mut g = st.0.try_lock().expect("fresh state");
                g.settings = st.read("settings.json");
                g.convs = st.read("chats.json");
                g.tombs = st.read("tombstones.json");
                g.notif_ts = st.read("notif.json");
            }
            app.manage(st.clone());
            let h = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = connect(h.clone(), st).await {
                    let _ = h.emit("status", format!("offline: {e}"));
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            settings_get, settings_set, agents, chats_list, chat_new, chat_delete, chat_send, chat_cancel,
            waiting_list, waiting_act, notifications, notifications_read, md, update_check, open_url
        ])
        .run(tauri::generate_context!())
        .expect("kAIm56 desktop");
}

#[cfg(test)]
mod tests {
    #[test]
    fn versions_compare_as_numbers() {
        assert!(super::newer("0.10.0", "0.9.1"));
        assert!(!super::newer("0.1.0", "0.1.0"));
    }

    #[test]
    fn markdown_renders_but_html_stays_text() {
        let h = super::md("**hi** <script>x</script>".into());
        assert!(h.contains("<strong>hi</strong>"));
        assert!(!h.contains("<script>"));
    }
}
