//! katfs-share — gibt einen lokalen Ordner an einen katfs-Host-Knoten frei.
//!
//! Dieselbe Rolle wie der freigebende Browser-Tab (PROVIDER im Sinne von
//! PROTOCOL.md), nur nativ: kein Browser, keine File System Access API — und
//! deshalb **lesen und schreiben**, auch dort, wo Firefox und Safari nur lesen
//! koennten. Laeuft auf Linux und macOS.
//!
//! Usage:
//!   katfs-share <node-id> <ordner> [--name <label>] [--id <share-id>] [--ro]
//!
//! Die share-id ist standardmaessig aus Hostname + absolutem Pfad abgeleitet,
//! also ueber Neustarts hinweg stabil — eine Instanz, die auf sie zeigt, findet
//! die Freigabe wieder. Mit --id laesst sie sich fest vorgeben.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::{Component, Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use iroh::endpoint::{RecvStream, SendStream};
use iroh::Endpoint;
use serde_json::{json, Value};

const ALPN: &[u8] = b"katfs/0";

// --- framing: 4-byte big-endian length + payload (PROTOCOL.md) --------------
async fn write_frame(send: &mut SendStream, data: &[u8]) -> Result<()> {
    send.write_all(&(data.len() as u32).to_be_bytes()).await?;
    send.write_all(data).await?;
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

// --- Pfadsicherheit ---------------------------------------------------------
// Wie im Browser-Provider: relativ zur Wurzel, kein ".." und keine absoluten
// Pfade. Zusaetzlich wird das Ergebnis gegen die Wurzel geprueft, damit auch
// ein Symlink nicht herausfuehrt.
fn resolve(root: &Path, rel: &str) -> Result<PathBuf> {
    let mut out = root.to_path_buf();
    for comp in Path::new(rel).components() {
        match comp {
            Component::Normal(seg) => out.push(seg),
            Component::CurDir => {}
            Component::ParentDir => return Err(anyhow!("path escapes share root: \"..\" rejected")),
            Component::RootDir | Component::Prefix(_) => {
                return Err(anyhow!("absolute paths are not allowed"))
            }
        }
    }
    // Existierende Pfade hart pruefen; neue Dateien pruefen wir ueber ihr
    // Elternverzeichnis, das es geben muss.
    let probe = if out.exists() { out.clone() } else { out.parent().unwrap_or(root).to_path_buf() };
    if let (Ok(r), Ok(p)) = (root.canonicalize(), probe.canonicalize()) {
        if !p.starts_with(&r) {
            return Err(anyhow!("path escapes share root"));
        }
    }
    Ok(out)
}

fn op_list(root: &Path, rel: &str) -> Result<Value> {
    let dir = resolve(root, rel)?;
    let mut entries = Vec::new();
    for e in std::fs::read_dir(&dir).with_context(|| format!("read_dir {}", dir.display()))? {
        let e = e?;
        let md = e.metadata()?;
        entries.push(json!({
            "name": e.file_name().to_string_lossy(),
            "dir": md.is_dir(),
            "size": if md.is_dir() { 0 } else { md.len() },
        }));
    }
    Ok(json!(entries))
}

fn op_stat(root: &Path, rel: &str) -> Result<Value> {
    let p = resolve(root, rel)?;
    Ok(match std::fs::metadata(&p) {
        Ok(md) => json!({ "exists": true, "dir": md.is_dir(), "size": if md.is_dir() { 0 } else { md.len() } }),
        Err(_) => json!({ "exists": false, "dir": false, "size": 0 }),
    })
}

fn op_read(root: &Path, rel: &str) -> Result<Vec<u8>> {
    let p = resolve(root, rel)?;
    std::fs::read(&p).with_context(|| format!("read {}", p.display()))
}

fn op_write(root: &Path, rel: &str, data: &[u8]) -> Result<()> {
    let p = resolve(root, rel)?;
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&p, data).with_context(|| format!("write {}", p.display()))
}

fn op_delete(root: &Path, rel: &str, recursive: bool) -> Result<()> {
    if rel.trim().is_empty() || rel == "." {
        return Err(anyhow!("refusing to delete the share root"));
    }
    let p = resolve(root, rel)?;
    let md = std::fs::symlink_metadata(&p).with_context(|| format!("stat {}", p.display()))?;
    if md.is_dir() {
        // Ohne recursive scheitert das an nicht-leeren Verzeichnissen — gewollt,
        // damit ein Agent nicht aus Versehen einen ganzen Baum abraeumt.
        if recursive {
            std::fs::remove_dir_all(&p)
        } else {
            std::fs::remove_dir(&p)
        }
    } else {
        std::fs::remove_file(&p)
    }
    .with_context(|| format!("delete {}", p.display()))
}

fn default_share_id(root: &Path) -> String {
    let host = std::env::var("HOSTNAME").ok().unwrap_or_else(|| {
        std::fs::read_to_string("/etc/hostname").unwrap_or_default().trim().to_string()
    });
    let mut h = DefaultHasher::new();
    host.hash(&mut h);
    root.canonicalize().unwrap_or_else(|_| root.to_path_buf()).hash(&mut h);
    format!("{:012x}", h.finish() & 0xffff_ffff_ffff)
}

struct Cfg {
    root: PathBuf,
    share_id: String,
    name: String,
    device: String,
    readonly: bool,
}

/// Eine Verbindung bedienen, bis der Stream schliesst.
async fn serve_once(ep: &Endpoint, node_id: &str, cfg: &Cfg) -> Result<()> {
    let id: iroh::EndpointId = node_id.parse().context("parse node-id")?;
    let conn = ep.connect(id, ALPN).await.context("connect")?;
    // Der HOST oeffnet den Stream und sendet zuerst (PROTOCOL.md).
    let (mut send, mut recv) = conn.accept_bi().await.context("accept_bi")?;
    eprintln!("[katfs-share] connected — serving {}", cfg.root.display());

    loop {
        let raw = read_frame(&mut recv).await?;
        let req: Value = serde_json::from_slice(&raw)?;
        let rid = req.get("id").cloned().unwrap_or(json!(0));
        let op = req.get("op").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let path = req.get("path").and_then(|v| v.as_str()).unwrap_or("").to_string();

        let mut payload: Option<Vec<u8>> = None;
        if op == "write" {
            payload = Some(read_frame(&mut recv).await?);
        }

        let (resp, extra): (Value, Option<Vec<u8>>) = match op.as_str() {
            "hello" => (
                json!({"id": rid, "ok": true, "share": cfg.share_id, "name": cfg.name,
                       "device": cfg.device, "readonly": cfg.readonly}),
                None,
            ),
            "list" => match op_list(&cfg.root, &path) {
                Ok(entries) => (json!({"id": rid, "ok": true, "entries": entries}), None),
                Err(e) => (json!({"id": rid, "ok": false, "error": format!("{e:#}")}), None),
            },
            "stat" => match op_stat(&cfg.root, &path) {
                Ok(v) => (
                    json!({"id": rid, "ok": true, "exists": v["exists"], "dir": v["dir"], "size": v["size"]}),
                    None,
                ),
                Err(e) => (json!({"id": rid, "ok": false, "error": format!("{e:#}")}), None),
            },
            "read" => match op_read(&cfg.root, &path) {
                Ok(bytes) => (json!({"id": rid, "ok": true, "size": bytes.len()}), Some(bytes)),
                Err(e) => (json!({"id": rid, "ok": false, "error": format!("{e:#}")}), None),
            },
            "write" => {
                if cfg.readonly {
                    (json!({"id": rid, "ok": false, "error": "share is read-only (--ro)"}), None)
                } else {
                    match op_write(&cfg.root, &path, payload.as_deref().unwrap_or(&[])) {
                        Ok(()) => (json!({"id": rid, "ok": true}), None),
                        Err(e) => (json!({"id": rid, "ok": false, "error": format!("{e:#}")}), None),
                    }
                }
            }
            "delete" => {
                if cfg.readonly {
                    (json!({"id": rid, "ok": false, "error": "share is read-only (--ro)"}), None)
                } else {
                    let rec = req.get("recursive").and_then(|b| b.as_bool()).unwrap_or(false);
                    match op_delete(&cfg.root, &path, rec) {
                        Ok(()) => (json!({"id": rid, "ok": true}), None),
                        Err(e) => (json!({"id": rid, "ok": false, "error": format!("{e:#}")}), None),
                    }
                }
            }
            other => (json!({"id": rid, "ok": false, "error": format!("unknown op: {other}")}), None),
        };

        if op != "stat" {
            eprintln!("[katfs-share] {op} {path}");
        }
        write_frame(&mut send, &serde_json::to_vec(&resp)?).await?;
        if let Some(p) = extra {
            write_frame(&mut send, &p).await?;
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let positional: Vec<&String> = args.iter().filter(|a| !a.starts_with("--")).collect();
    if positional.len() < 2 {
        eprintln!("usage: katfs-share <node-id> <folder> [--name <label>] [--id <share-id>] [--ro]");
        std::process::exit(2);
    }
    let flag = |k: &str| -> Option<String> {
        args.iter().position(|a| a == k).and_then(|i| args.get(i + 1)).cloned()
    };

    let node_id = positional[0].clone();
    let root = PathBuf::from(positional[1])
        .canonicalize()
        .with_context(|| format!("no such folder: {}", positional[1]))?;
    if !root.is_dir() {
        return Err(anyhow!("not a folder: {}", root.display()));
    }
    let cfg = Cfg {
        share_id: flag("--id").unwrap_or_else(|| default_share_id(&root)),
        name: flag("--name").unwrap_or_else(|| {
            root.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| "share".into())
        }),
        device: format!("{} (native)", std::env::consts::OS),
        readonly: args.iter().any(|a| a == "--ro"),
        root,
    };

    let ep = Endpoint::builder(iroh::endpoint::presets::N0)
        .alpns(vec![])
        .bind()
        .await
        .context("bind endpoint")?;

    eprintln!(
        "[katfs-share] share {} \"{}\" {}— folder {}",
        cfg.share_id,
        cfg.name,
        if cfg.readonly { "(read-only) " } else { "(read/write) " },
        cfg.root.display()
    );
    eprintln!("[katfs-share] pick it by that id when creating an instance");

    // Auto-Reconnect wie im Browser-Tab: die Freigabe soll einen Neustart des
    // Knotens oder einen Netzwechsel ueberleben, ohne dass jemand hinlaeuft.
    let mut fails = 0u32;
    loop {
        match serve_once(&ep, &node_id, &cfg).await {
            Ok(()) => fails = 0,
            Err(e) => {
                fails += 1;
                eprintln!("[katfs-share] connection lost ({fails}): {e}");
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    }
}
