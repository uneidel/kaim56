// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The iroh transport, in-process: the same splice as iroh-gw/src/bin/tunnel.rs.
// A listener on 127.0.0.1:<random> hands every TCP connection to one fresh bi
// stream (ALPN kaim56-mgr/0) to the manager's gateway; the app's HTTP client
// talks plain HTTP (with its Basic auth) to that port. No public HTTPS endpoint,
// E2E-encrypted; the gateway's NodeId allowlist decides who gets in.
use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use iroh::{Endpoint, SecretKey};
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex;

const ALPN: &[u8] = b"kaim56-mgr/0";

/// The node secret (stable NodeId => pairing once), created on first use.
pub fn secret(path: &Path) -> Result<SecretKey> {
    if let Ok(b) = std::fs::read(path) {
        if let Ok(a) = <[u8; 32]>::try_from(b.as_slice()) {
            return Ok(SecretKey::from_bytes(&a));
        }
    }
    let a: [u8; 32] = rand::random();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, a).context("write node key")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(SecretKey::from_bytes(&a))
}

struct Upstream {
    ep: Endpoint,
    gw: iroh::PublicKey,
    conn: Mutex<Option<iroh::endpoint::Connection>>,
}

impl Upstream {
    /// A bi stream on the shared connection; a dead connection is redialed once.
    async fn open_bi(&self) -> Result<(iroh::endpoint::SendStream, iroh::endpoint::RecvStream)> {
        for fresh in [false, true] {
            let mut guard = self.conn.lock().await;
            if fresh || guard.is_none() {
                *guard = Some(self.ep.connect(self.gw, ALPN).await.context("dial gateway")?);
            }
            let conn = guard.as_ref().unwrap().clone();
            drop(guard);
            match conn.open_bi().await {
                Ok(pair) => return Ok(pair),
                Err(_) if !fresh => continue,
                Err(e) => return Err(e).context("open_bi"),
            }
        }
        unreachable!()
    }
}

async fn splice(tcp: TcpStream, up: Arc<Upstream>) {
    let Ok((mut send, mut recv)) = up.open_bi().await else { return };
    let (mut tr, mut tw) = tcp.into_split();
    let upcopy = tokio::spawn(async move {
        let _ = tokio::io::copy(&mut tr, &mut send).await;
        let _ = send.finish();
    });
    let _ = tokio::io::copy(&mut recv, &mut tw).await;
    let _ = tw.shutdown().await;
    let _ = upcopy.await;
}

/// A running tunnel; dropping it stops the listener.
pub struct Tunnel {
    pub port: u16,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Tunnel {
    fn drop(&mut self) {
        self.task.abort();
    }
}

pub async fn start(key: SecretKey, gateway: &str) -> Result<Tunnel> {
    let gw: iroh::PublicKey = gateway.trim().parse().context("gateway NodeId")?;
    let ep = Endpoint::builder(iroh::endpoint::presets::N0)
        .secret_key(key)
        .bind()
        .await
        .context("bind iroh endpoint")?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let port = listener.local_addr()?.port();
    let up = Arc::new(Upstream { ep, gw, conn: Mutex::new(None) });
    let task = tokio::spawn(async move {
        while let Ok((tcp, _)) = listener.accept().await {
            tokio::spawn(splice(tcp, up.clone()));
        }
    });
    Ok(Tunnel { port, task })
}
