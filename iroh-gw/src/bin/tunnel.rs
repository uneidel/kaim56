// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// kaim56-tunnel — the DESKTOP side of the iroh transport. Counterpart of
// iroh-gw: listens on 127.0.0.1 and splices every accepted TCP connection
// onto one fresh iroh bi stream to the gateway (ALPN kaim56-mgr/0), exactly
// the way the phone app does per HTTP request. Any plain-HTTP client — the
// voice client, curl, a browser — reaches the manager by pointing at the
// local port; no public HTTPS endpoint, no VPN, E2E-encrypted, and the
// gateway's NodeId allowlist decides who gets in.
//
// Usage:
//   kaim56-tunnel --id                      print our NodeId (add to the allowlist:
//                                           web UI -> iroh tab, or /api/iroh)
//   kaim56-tunnel <gateway-node-id> [addr]  run; default listen addr 127.0.0.1:8701
//
// The node secret persists in ~/.config/kaim56-tunnel.key => stable NodeId,
// so pairing happens once.
use std::io::Read;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result};
use iroh::{Endpoint, SecretKey};
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex;

const ALPN: &[u8] = b"kaim56-mgr/0";

fn key_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".config").join("kaim56-tunnel.key")
}

fn load_or_create_secret() -> Result<SecretKey> {
    let path = key_path();
    if let Ok(b) = std::fs::read(&path) {
        if b.len() == 32 {
            let mut a = [0u8; 32];
            a.copy_from_slice(&b);
            return Ok(SecretKey::from_bytes(&a));
        }
    }
    let mut a = [0u8; 32];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut a)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(&path, a).context("write secret key")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(SecretKey::from_bytes(&a))
}

/// One shared iroh connection, re-dialed on demand — the gateway accepts many
/// bi streams per connection, so all local clients share it.
struct Upstream {
    ep: Endpoint,
    gw: iroh::PublicKey,
    conn: Mutex<Option<iroh::endpoint::Connection>>,
}

impl Upstream {
    async fn open_bi(
        &self,
    ) -> Result<(iroh::endpoint::SendStream, iroh::endpoint::RecvStream)> {
        // First try the cached connection; a dead one shows up as open_bi
        // failing, then we dial fresh exactly once.
        for fresh in [false, true] {
            let mut guard = self.conn.lock().await;
            if fresh || guard.is_none() {
                *guard = Some(
                    self.ep
                        .connect(self.gw, ALPN)
                        .await
                        .context("dial gateway")?,
                );
            }
            let conn = guard.as_ref().unwrap().clone();
            drop(guard);
            match conn.open_bi().await {
                Ok(pair) => return Ok(pair),
                Err(e) if !fresh => {
                    eprintln!("[tunnel] stale connection ({e}), redialing");
                }
                Err(e) => return Err(e).context("open_bi"),
            }
        }
        unreachable!()
    }
}

async fn serve_client(tcp: TcpStream, up: Arc<Upstream>) {
    let (mut send, mut recv) = match up.open_bi().await {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[tunnel] gateway unreachable: {e:#}");
            return;
        }
    };
    let (mut tr, mut tw) = tcp.into_split();
    // local client -> gateway
    let upcopy = tokio::spawn(async move {
        let _ = tokio::io::copy(&mut tr, &mut send).await;
        let _ = send.finish();
    });
    // gateway -> local client
    let _ = tokio::io::copy(&mut recv, &mut tw).await;
    let _ = tw.shutdown().await;
    let _ = upcopy.await;
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let ep = Endpoint::builder(iroh::endpoint::presets::N0)
        .secret_key(load_or_create_secret()?)
        .bind()
        .await
        .context("bind endpoint")?;
    if args.iter().any(|a| a == "--id") {
        println!("{}", ep.id());
        return Ok(());
    }
    let gw: iroh::PublicKey = args
        .get(1)
        .context("usage: kaim56-tunnel <gateway-node-id> [listen-addr] | --id")?
        .parse()
        .context("parse gateway node-id")?;
    let addr = args
        .get(2)
        .map(|s| s.as_str())
        .unwrap_or("127.0.0.1:8701");

    let listener = TcpListener::bind(addr).await.context("bind listen addr")?;
    eprintln!(
        "[tunnel] {} -> iroh {} (our id: {})",
        addr,
        gw,
        ep.id()
    );
    let up = Arc::new(Upstream {
        ep,
        gw,
        conn: Mutex::new(None),
    });
    loop {
        let (tcp, _) = listener.accept().await.context("accept")?;
        let up = up.clone();
        tokio::spawn(serve_client(tcp, up));
    }
}
