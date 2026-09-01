use std::fs;
use std::io::Read;
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use iroh::endpoint::{Connection, RecvStream, SendStream};
use iroh::{Endpoint, SecretKey};
use serde_json::{json, Value};
use tokio::sync::Mutex;

const ALPN: &[u8] = b"katfs/0";
const SECRET_PATH: &str = "/home/ulrich/iroh-fs/node/secret.key";
const WEB_INDEX: &str = "/home/ulrich/iroh-fs/web/index.html";
const HTTP_ADDR: &str = "127.0.0.1:8790";  // only the manager (loopback) may connect; guests go through the broker

/// One active browser share. Several may be connected at once (one per browser
/// tab, on any machine); each is addressed by the `share_id` the browser
/// reports in the `hello` round-trip right after connecting.
struct ConnState {
    _conn: Connection,
    send: SendStream,
    recv: RecvStream,
    next_id: u64,
    /// Stable per-browser id (kept in the browser's localStorage), so a reload
    /// or a reconnect lands on the same share instead of creating a new one.
    share_id: String,
    /// Name of the shared folder, for humans picking a share.
    name: String,
    /// Where it is being shared from ("Linux", "Android", …).
    device: String,
    /// Provider reports that it rejects writes (browser fallback without a
    /// writing API, or katfs-share --ro).
    readonly: bool,
    since: u64,
}

/// Insertion-ordered; a handful of shares at most, so a linear scan is fine
/// and we keep a stable order for /shares.
type Shared = Arc<Mutex<Vec<ConnState>>>;

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Browser-supplied strings land in ids, logs and JSON — keep them boring.
fn sanitize_id(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '_' || *c == '-')
        .take(64)
        .collect()
}

fn sanitize_label(s: &str) -> String {
    s.chars()
        .filter(|c| !c.is_control())
        .take(80)
        .collect()
}

// ---------------------------------------------------------------------------
// Secret key persistence (stable node-id)
// ---------------------------------------------------------------------------
fn load_or_create_secret() -> Result<SecretKey> {
    if let Ok(bytes) = fs::read(SECRET_PATH) {
        if bytes.len() == 32 {
            let mut a = [0u8; 32];
            a.copy_from_slice(&bytes);
            return Ok(SecretKey::from_bytes(&a));
        }
    }
    // Generate 32 bytes from the OS CSPRNG (no extra crate / version coupling).
    let mut a = [0u8; 32];
    let mut f = fs::File::open("/dev/urandom").context("open /dev/urandom")?;
    f.read_exact(&mut a).context("read /dev/urandom")?;
    fs::write(SECRET_PATH, &a).context("write secret.key")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(SECRET_PATH, fs::Permissions::from_mode(0o600));
    }
    Ok(SecretKey::from_bytes(&a))
}

// ---------------------------------------------------------------------------
// Framing helpers: 4-byte big-endian length + payload
// ---------------------------------------------------------------------------
async fn write_frame(send: &mut SendStream, data: &[u8]) -> Result<()> {
    let len = (data.len() as u32).to_be_bytes();
    send.write_all(&len).await.map_err(|e| anyhow!("write len: {e}"))?;
    send.write_all(data).await.map_err(|e| anyhow!("write body: {e}"))?;
    Ok(())
}

async fn read_frame(recv: &mut RecvStream) -> Result<Vec<u8>> {
    let mut lenb = [0u8; 4];
    recv.read_exact(&mut lenb).await.map_err(|e| anyhow!("read len: {e}"))?;
    let len = u32::from_be_bytes(lenb) as usize;
    let mut buf = vec![0u8; len];
    recv.read_exact(&mut buf).await.map_err(|e| anyhow!("read body: {e}"))?;
    Ok(buf)
}

// ---------------------------------------------------------------------------
// One katfs/0 request round-trip over the active connection.
// Returns (json response value, optional trailing raw bytes for `read`).
// ---------------------------------------------------------------------------
async fn katfs_request(
    conn: &mut ConnState,
    op: &str,
    path: &str,
    body: Option<&[u8]>,
    opts: Option<&Value>,
) -> Result<(Value, Option<Vec<u8>>)> {
    let id = conn.next_id;
    conn.next_id += 1;

    let mut req = json!({ "id": id, "op": op, "path": path });
    if let Some(Value::Object(extra)) = opts {
        for (k, v) in extra {
            req[k.as_str()] = v.clone();
        }
    }
    if op == "write" {
        let n = body.map(|b| b.len()).unwrap_or(0);
        req["size"] = json!(n);
    }
    let req_bytes = serde_json::to_vec(&req)?;
    write_frame(&mut conn.send, &req_bytes).await?;

    if op == "write" {
        write_frame(&mut conn.send, body.unwrap_or(&[])).await?;
    }

    let resp_bytes = read_frame(&mut conn.recv).await?;
    let resp: Value = serde_json::from_slice(&resp_bytes)
        .context("parse response json")?;

    let mut extra = None;
    if op == "read" && resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
        let data = read_frame(&mut conn.recv).await?;
        extra = Some(data);
    }
    Ok((resp, extra))
}

// ---------------------------------------------------------------------------
// Path safety: reject any ".." segment (no escaping the shared root).
// ---------------------------------------------------------------------------
fn path_is_safe(path: &str) -> bool {
    !path.split(|c| c == '/' || c == '\\').any(|seg| seg == "..")
}

fn normalize_path(raw: &str) -> String {
    raw.trim_start_matches('/').to_string()
}

// ---------------------------------------------------------------------------
// Accept loop: keep accepting katfs/0 connections; the host opens the bi
// stream (host sends the first bytes, so the browser becomes aware of it).
// ---------------------------------------------------------------------------
async fn accept_loop(endpoint: Endpoint, state: Shared) {
    loop {
        let Some(incoming) = endpoint.accept().await else {
            eprintln!("[accept] endpoint closed, stopping accept loop");
            break;
        };
        let conn = match incoming.await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[accept] handshake failed: {e}");
                continue;
            }
        };
        match conn.open_bi().await {
            Ok((send, recv)) => {
                let mut cs = ConnState {
                    _conn: conn,
                    send,
                    recv,
                    next_id: 1,
                    share_id: String::new(),
                    name: String::new(),
                    device: String::new(),
                    readonly: false,
                    since: now_secs(),
                };
                // Warmup + identification in one: "hello" sends the first bytes
                // immediately (so accept_bi fires in the browser — a QUIC stream
                // only becomes visible to the peer through bytes) and returns the
                // share id. Older pages don't know "hello" and answer ok:false —
                // then it falls back to the old list-"."-warmup and the node
                // assigns the id itself.
                match katfs_request(&mut cs, "hello", ".", None, None).await {
                    Ok((v, _)) if v.get("ok").and_then(|b| b.as_bool()) == Some(true) => {
                        cs.share_id =
                            sanitize_id(v.get("share").and_then(|s| s.as_str()).unwrap_or(""));
                        cs.name =
                            sanitize_label(v.get("name").and_then(|s| s.as_str()).unwrap_or(""));
                        cs.device =
                            sanitize_label(v.get("device").and_then(|s| s.as_str()).unwrap_or(""));
                        cs.readonly =
                            v.get("readonly").and_then(|b| b.as_bool()).unwrap_or(false);
                    }
                    Ok(_) => {
                        if let Err(e) = katfs_request(&mut cs, "list", ".", None, None).await {
                            eprintln!("[accept] legacy warmup failed, dropping conn: {e}");
                            continue;
                        }
                        eprintln!("[accept] browser without hello — legacy share");
                    }
                    Err(e) => {
                        eprintln!("[accept] hello failed, dropping conn: {e}");
                        continue;
                    }
                }
                if cs.share_id.is_empty() {
                    cs.share_id = format!("s{}", now_secs());
                }
                if cs.name.is_empty() {
                    cs.name = cs.share_id.clone();
                }
                let mut g = state.lock().await;
                // Reconnect of the same id replaces the old entry instead of
                // duplicating it — exactly the case after a reload.
                let replaced = g.iter().any(|c| c.share_id == cs.share_id);
                g.retain(|c| c.share_id != cs.share_id);
                eprintln!(
                    "[accept] share {} ({}, {}) {} — {} active",
                    cs.share_id,
                    cs.name,
                    if cs.device.is_empty() { "?" } else { &cs.device },
                    if replaced { "reconnected" } else { "connected" },
                    g.len() + 1
                );
                g.push(cs);
            }
            Err(e) => {
                eprintln!("[accept] open_bi failed: {e}");
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Minimal percent-decoding for query values.
// ---------------------------------------------------------------------------
fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let h = (hex_val(bytes[i + 1]), hex_val(bytes[i + 2]));
                if let (Some(a), Some(b)) = h {
                    out.push(a * 16 + b);
                    i += 3;
                    continue;
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}

/// Extract the value of a query parameter (e.g. `path`) from a raw URL.
fn content_type_for(p: &str) -> &'static str {
    if p.ends_with(".js") || p.ends_with(".mjs") {
        "application/javascript"
    } else if p.ends_with(".wasm") {
        "application/wasm"
    } else if p.ends_with(".html") {
        "text/html; charset=utf-8"
    } else if p.ends_with(".css") {
        "text/css"
    } else if p.ends_with(".json") {
        "application/json"
    } else {
        "application/octet-stream"
    }
}

fn query_param(url: &str, key: &str) -> Option<String> {
    let q = url.splitn(2, '?').nth(1)?;
    for pair in q.split('&') {
        let mut it = pair.splitn(2, '=');
        let k = it.next().unwrap_or("");
        if k == key {
            return Some(percent_decode(it.next().unwrap_or("")));
        }
    }
    None
}

// ---------------------------------------------------------------------------
// HTTP server (blocking, on its own OS thread). Bridges to the async runtime
// via a Handle for each katfs round-trip.
// ---------------------------------------------------------------------------
fn run_http(
    handle: tokio::runtime::Handle,
    state: Shared,
    node_id: String,
) -> Result<()> {
    let server = tiny_http::Server::http(HTTP_ADDR)
        .map_err(|e| anyhow!("bind http {HTTP_ADDR}: {e}"))?;
    eprintln!("[http] listening on http://{HTTP_ADDR}");

    for mut request in server.incoming_requests() {
        let method = request.method().clone();
        let url = request.url().to_string();
        let path_only = url.splitn(2, '?').next().unwrap_or("").to_string();

        // --- routes that don't need the browser connection ---
        if method == tiny_http::Method::Get && path_only == "/nodeid" {
            respond_json(request, 200, &json!({ "node_id": node_id }));
            continue;
        }
        if method == tiny_http::Method::Get && path_only == "/status" {
            let (n, first) = handle.block_on(async {
                let g = state.lock().await;
                (g.len(), g.first().map(|c| c.name.clone()).unwrap_or_default())
            });
            respond_json(
                request,
                200,
                &json!({
                    "connected": n > 0,
                    // "share" stays a string for compatibility; with exactly
                    // one share it is now its name instead of the literal "active".
                    "share": if n == 1 { first } else if n > 1 { format!("{n} shares") } else { String::new() },
                    "count": n,
                }),
            );
            continue;
        }
        if method == tiny_http::Method::Get && path_only == "/shares" {
            let list = handle.block_on(async {
                let g = state.lock().await;
                g.iter()
                    .map(|c| {
                        json!({
                            "id": c.share_id,
                            "name": c.name,
                            "device": c.device,
                            "readonly": c.readonly,
                            "since": c.since,
                        })
                    })
                    .collect::<Vec<_>>()
            });
            respond_json(request, 200, &json!({ "shares": list }));
            continue;
        }
        if method == tiny_http::Method::Get && path_only == "/" {
            serve_index(request, &node_id);
            continue;
        }

        // --- static assets from web/ (app.js, katfs-provider.js, wasm/…) ---
        if method == tiny_http::Method::Get {
            let rel = path_only.trim_start_matches('/');
            if !rel.is_empty() && !rel.contains("..") {
                let full = format!("/home/ulrich/iroh-fs/web/{}", rel);
                if let Ok(bytes) = fs::read(&full) {
                    let ct = content_type_for(&full);
                    let header =
                        tiny_http::Header::from_bytes(&b"Content-Type"[..], ct.as_bytes()).unwrap();
                    let _ = request.respond(tiny_http::Response::from_data(bytes).with_header(header));
                    continue;
                }
            }
        }

        // --- routes that require the active browser share ---
        if method == tiny_http::Method::Get && path_only == "/ls" {
            let path = normalize_path(&query_param(&url, "path").unwrap_or_default());
            let share = query_param(&url, "share");
            if !path_is_safe(&path) {
                respond_json(request, 400, &json!({ "error": "invalid path" }));
                continue;
            }
            match handle.block_on(with_share(&state, share, |c| {
                Box::pin(async move { katfs_request(c, "list", &path, None, None).await })
            })) {
                Ok((resp, _)) => {
                    let entries = resp.get("entries").cloned().unwrap_or(json!([]));
                    if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                        respond_json(request, 200, &json!({ "entries": entries }));
                    } else {
                        respond_json(request, 500, &resp);
                    }
                }
                Err(e) => respond_json(request, 503, &json!({ "error": e.to_string() })),
            }
            continue;
        }

        if method == tiny_http::Method::Get && path_only == "/read" {
            let path = normalize_path(&query_param(&url, "path").unwrap_or_default());
            let share = query_param(&url, "share");
            if !path_is_safe(&path) {
                respond_json(request, 400, &json!({ "error": "invalid path" }));
                continue;
            }
            match handle.block_on(with_share(&state, share, |c| {
                Box::pin(async move { katfs_request(c, "read", &path, None, None).await })
            })) {
                Ok((resp, extra)) => {
                    if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                        let data = extra.unwrap_or_default();
                        let r = tiny_http::Response::from_data(data).with_header(
                            header("Content-Type", "application/octet-stream"),
                        );
                        let _ = request.respond(r);
                    } else {
                        respond_json(request, 404, &resp);
                    }
                }
                Err(e) => respond_json(request, 503, &json!({ "error": e.to_string() })),
            }
            continue;
        }

        if method == tiny_http::Method::Post && path_only == "/write" {
            let path = normalize_path(&query_param(&url, "path").unwrap_or_default());
            let share = query_param(&url, "share");
            if !path_is_safe(&path) {
                respond_json(request, 400, &json!({ "error": "invalid path" }));
                continue;
            }
            let mut body = Vec::new();
            if request.as_reader().read_to_end(&mut body).is_err() {
                respond_json(request, 400, &json!({ "error": "read body failed" }));
                continue;
            }
            let body_arc = body;
            match handle.block_on(with_share(&state, share, move |c| {
                let b = body_arc;
                Box::pin(async move { katfs_request(c, "write", &path, Some(&b), None).await })
            })) {
                Ok((resp, _)) => {
                    if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                        respond_json(request, 200, &json!({ "ok": true }));
                    } else {
                        respond_json(request, 500, &resp);
                    }
                }
                Err(e) => respond_json(request, 503, &json!({ "error": e.to_string() })),
            }
            continue;
        }

        if method == tiny_http::Method::Post && path_only == "/delete" {
            let path = normalize_path(&query_param(&url, "path").unwrap_or_default());
            let share = query_param(&url, "share");
            let recursive = query_param(&url, "recursive").as_deref() == Some("1");
            // An empty path would be the root of the share — that is never deleted.
            if !path_is_safe(&path) || path.is_empty() {
                respond_json(request, 400, &json!({ "error": "invalid path" }));
                continue;
            }
            match handle.block_on(with_share(&state, share, move |c| {
                Box::pin(async move {
                    let opts = json!({ "recursive": recursive });
                    katfs_request(c, "delete", &path, None, Some(&opts)).await
                })
            })) {
                Ok((resp, _)) => {
                    if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                        respond_json(request, 200, &json!({ "ok": true }));
                    } else {
                        respond_json(request, 500, &resp);
                    }
                }
                Err(e) => respond_json(request, 503, &json!({ "error": e.to_string() })),
            }
            continue;
        }

        respond_json(request, 404, &json!({ "error": "not found" }));
    }
    Ok(())
}

/// Run `f` against one share. `want` picks it by id; without an id this only
/// works while exactly one share is connected, so a second sharer can never
/// silently redirect an agent that never asked for a specific share.
async fn with_share<F>(state: &Shared, want: Option<String>, f: F) -> Result<(Value, Option<Vec<u8>>)>
where
    F: for<'a> FnOnce(
        &'a mut ConnState,
    )
        -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(Value, Option<Vec<u8>>)>> + 'a>>,
{
    let mut guard = state.lock().await;
    if guard.is_empty() {
        return Err(anyhow!("no browser connected"));
    }
    let idx = match want.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(id) => guard
            .iter()
            .position(|c| c.share_id == id)
            .ok_or_else(|| anyhow!("no such share: {id}"))?,
        None if guard.len() == 1 => 0,
        None => {
            let ids: Vec<&str> = guard.iter().map(|c| c.share_id.as_str()).collect();
            return Err(anyhow!(
                "{} shares active — pass ?share=<id>, one of: {}",
                guard.len(),
                ids.join(", ")
            ));
        }
    };
    let res = f(&mut guard[idx]).await;
    if res.is_err() {
        // connection is likely broken; drop it so /shares reflects reality
        guard.remove(idx);
    }
    res
}

fn header(k: &str, v: &str) -> tiny_http::Header {
    tiny_http::Header::from_bytes(k.as_bytes(), v.as_bytes()).unwrap()
}

fn respond_json(request: tiny_http::Request, status: u16, body: &Value) {
    let data = serde_json::to_vec(body).unwrap_or_default();
    let r = tiny_http::Response::from_data(data)
        .with_status_code(status)
        .with_header(header("Content-Type", "application/json"));
    let _ = request.respond(r);
}

fn serve_index(request: tiny_http::Request, node_id: &str) {
    match fs::read_to_string(WEB_INDEX) {
        Ok(html) => {
            let html = html.replace("%%NODE_ID%%", node_id);
            let r = tiny_http::Response::from_string(html)
                .with_header(header("Content-Type", "text/html; charset=utf-8"));
            let _ = request.respond(r);
        }
        Err(_) => {
            let html = format!(
                "<!doctype html><meta charset=utf-8><title>katfs host</title>\
                 <h1>katfs host node</h1><p>node-id: <code>{node_id}</code></p>\
                 <p>web/index.html not found.</p>"
            );
            let r = tiny_http::Response::from_string(html)
                .with_status_code(200)
                .with_header(header("Content-Type", "text/html; charset=utf-8"));
            let _ = request.respond(r);
        }
    }
}

// ---------------------------------------------------------------------------
#[tokio::main]
async fn main() -> Result<()> {
    let secret = load_or_create_secret()?;

    let endpoint = Endpoint::builder(iroh::endpoint::presets::N0)
        .secret_key(secret)
        .alpns(vec![ALPN.to_vec()])
        .bind()
        .await
        .context("bind endpoint")?;

    let node_id = endpoint.id().to_string();
    println!("node_id: {node_id}");
    eprintln!("[main] katfs/0 host node started, node_id = {node_id}");

    let state: Shared = Arc::new(Mutex::new(Vec::new()));

    // HTTP server on its own OS thread (blocking tiny_http), bridged via Handle.
    let handle = tokio::runtime::Handle::current();
    {
        let state = state.clone();
        let node_id = node_id.clone();
        std::thread::spawn(move || {
            if let Err(e) = run_http(handle, state, node_id) {
                eprintln!("[http] fatal: {e}");
            }
        });
    }

    // Application-level keepalive: every 10s a light stat "." over the active
    // connection, so the idle relay connection does not idle-timeout.
    {
        let state = state.clone();
        tokio::spawn(async move {
            let mut iv = tokio::time::interval(std::time::Duration::from_secs(2));
            loop {
                iv.tick().await;
                let mut g = state.lock().await;
                let mut dead: Vec<usize> = Vec::new();
                for (i, cs) in g.iter_mut().enumerate() {
                    if let Err(e) = katfs_request(cs, "stat", ".", None, None).await {
                        eprintln!("[keepalive] share {} lost: {e}", cs.share_id);
                        dead.push(i);
                    }
                }
                for i in dead.into_iter().rev() {
                    g.remove(i);
                }
            }
        });
    }

    // Accept browser connections on the runtime.
    accept_loop(endpoint, state).await;
    Ok(())
}
