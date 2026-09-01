// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
// This program is free software under the GNU AGPL v3+; see LICENSE.
//
// iroh-gw — the app<->manager transport over iroh (P2P, NAT-traversal, E2E).
//
// The Android app dials this node by its NodeId (no VPN, no public HTTPS port)
// and opens one iroh bi stream per HTTP request. This gateway is a TRANSPARENT
// relay: each accepted stream is spliced onto a fresh TCP connection to the
// local manager (127.0.0.1:8700). No HTTP parsing here, so token streaming and
// long-poll "just work"; the app speaks the very same HTTP (incl. basic auth)
// it used to, only through the tunnel. The manager is unchanged.
//
// Auth: every iroh connection carries the caller's cryptographically
// authenticated NodeId. Only NodeIds listed in the allowlist file are relayed;
// everyone else is dropped. The allowlist is re-read per connection, so pairing
// a new phone takes effect without a restart. Empty/missing allowlist => reject
// all (secure default).
//
// Env (all optional; defaults suit the reference install):
//   IROHGW_SECRET   path to the 32-byte node secret key (persisted => stable NodeId)
//   IROHGW_ALLOW    path to the allowlist (one NodeId per line, '#' comments)
//   IROHGW_NODEID   path to write our NodeId to (the manager/web-UI shows it)
//   IROHGW_MANAGER  manager address to relay to (default 127.0.0.1:8700)

use std::fs;
use std::io::Read;
use std::path::PathBuf;

use anyhow::{Context, Result};
use iroh::{Endpoint, SecretKey};
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;

const ALPN: &[u8] = b"kaim56-mgr/0";

fn env_path(key: &str, default: &str) -> PathBuf {
    PathBuf::from(std::env::var(key).unwrap_or_else(|_| default.to_string()))
}

fn secret_path() -> PathBuf {
    env_path("IROHGW_SECRET", "/home/ulrich/firecracker/iroh-gw/secret.key")
}
fn allow_path() -> PathBuf {
    env_path("IROHGW_ALLOW", "/home/ulrich/firecracker/iroh-gw/allow.txt")
}
fn nodeid_path() -> PathBuf {
    env_path("IROHGW_NODEID", "/home/ulrich/firecracker/iroh-gw/nodeid.txt")
}
fn manager_addr() -> String {
    std::env::var("IROHGW_MANAGER").unwrap_or_else(|_| "127.0.0.1:8700".to_string())
}

/// Load a persisted 32-byte secret, or create one from the OS CSPRNG. Persisting
/// it keeps the NodeId stable across restarts (the address the phone dials).
fn load_or_create_secret() -> Result<SecretKey> {
    let path = secret_path();
    if let Ok(bytes) = fs::read(&path) {
        if bytes.len() == 32 {
            let mut a = [0u8; 32];
            a.copy_from_slice(&bytes);
            return Ok(SecretKey::from_bytes(&a));
        }
    }
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    let mut a = [0u8; 32];
    let mut f = fs::File::open("/dev/urandom").context("open /dev/urandom")?;
    f.read_exact(&mut a).context("read /dev/urandom")?;
    fs::write(&path, &a).context("write secret key")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
    }
    Ok(SecretKey::from_bytes(&a))
}

/// Read the allowlist fresh (so a newly-paired phone works without a restart).
/// One node-id per line; blank lines and '#' comments ignored. Returns the
/// accepted node-ids as lowercase strings (compared against remote_id string).
fn load_allowlist() -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(text) = fs::read_to_string(allow_path()) {
        for line in text.lines() {
            // allow an optional "# label" after the id on the same line
            let id = line.split('#').next().unwrap_or("").trim();
            if id.is_empty() {
                continue;
            }
            out.push(id.to_ascii_lowercase());
        }
    }
    out
}

/// Splice one iroh bi stream onto a fresh TCP connection to the manager.
/// Copies bytes both ways until either side closes — a transparent tunnel.
async fn relay_stream(
    mut send: iroh::endpoint::SendStream,
    mut recv: iroh::endpoint::RecvStream,
    manager: String,
) {
    let tcp = match TcpStream::connect(&manager).await {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[relay] connect {manager} failed: {e}");
            let _ = send.finish();
            return;
        }
    };
    let (mut tr, mut tw) = tcp.into_split();
    // phone -> manager
    let up = tokio::spawn(async move {
        let _ = tokio::io::copy(&mut recv, &mut tw).await;
        let _ = tw.shutdown().await;
    });
    // manager -> phone
    let _ = tokio::io::copy(&mut tr, &mut send).await;
    let _ = send.finish();
    let _ = up.await;
}

async fn handle_conn(conn: iroh::endpoint::Connection, manager: String) {
    // Authenticate: only allowlisted node-ids are relayed. remote_id() is the
    // caller's cryptographically authenticated identity.
    let id_str = conn.remote_id().to_string().to_ascii_lowercase();
    if !load_allowlist().iter().any(|id| id == &id_str) {
        eprintln!("[conn] REJECT {id_str} (not in allowlist)");
        conn.close(1u32.into(), b"not allowed");
        return;
    }
    eprintln!("[conn] accept {id_str}");
    // The phone opens one bi stream per HTTP request.
    loop {
        match conn.accept_bi().await {
            Ok((send, recv)) => {
                let m = manager.clone();
                tokio::spawn(relay_stream(send, recv, m));
            }
            Err(_) => break, // connection closed
        }
    }
    eprintln!("[conn] closed {id_str}");
}

#[tokio::main]
async fn main() -> Result<()> {
    if std::env::args().any(|a| a == "--version") {
        println!("iroh-gw {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    let manager = manager_addr();
    let secret = load_or_create_secret()?;
    let endpoint = Endpoint::builder(iroh::endpoint::presets::N0)
        .secret_key(secret)
        .alpns(vec![ALPN.to_vec()])
        .bind()
        .await
        .context("bind endpoint")?;

    let node_id = endpoint.id().to_string();
    let np = nodeid_path();
    if let Some(dir) = np.parent() {
        let _ = fs::create_dir_all(dir);
    }
    let _ = fs::write(&np, &node_id);
    println!("node_id: {node_id}");
    eprintln!("[main] kaim56-mgr/0 gateway up; relaying to {manager}; node_id = {node_id}");
    eprintln!("[main] allowlist: {}", allow_path().display());

    loop {
        let Some(incoming) = endpoint.accept().await else {
            eprintln!("[main] endpoint closed, exiting");
            break;
        };
        let manager = manager.clone();
        tokio::spawn(async move {
            match incoming.await {
                Ok(conn) => handle_conn(conn, manager).await,
                Err(e) => eprintln!("[accept] handshake failed: {e}"),
            }
        });
    }
    Ok(())
}
