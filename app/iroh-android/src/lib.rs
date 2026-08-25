// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// kaim_iroh — the Android app's iroh transport to the manager. A tiny UniFFI
// surface over iroh: the app opens one bi stream per HTTP request, writes the
// raw HTTP/1.1 request, and reads the response incrementally (so token
// streaming from /api/chat/stream is preserved). Dialing is by the manager
// gateway's node-id; no VPN and no HTTPS endpoint needed. Mirrors the verified
// iroh-gw/src/bin/testclient.rs.
use std::sync::Arc;

use iroh::{Endpoint, SecretKey};
use tokio::runtime::{Handle, Runtime};
use tokio::sync::Mutex;

uniffi::setup_scaffolding!();

const ALPN: &[u8] = b"kaim56-mgr/0";

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum IrohError {
    #[error("bind failed")]
    Bind,
    #[error("bad node-id")]
    Parse,
    #[error("connect failed")]
    Connect,
    #[error("io error")]
    Io,
}

fn load_or_create_secret(path: &str) -> SecretKey {
    use std::io::Read;
    if let Ok(b) = std::fs::read(path) {
        if b.len() == 32 {
            let mut a = [0u8; 32];
            a.copy_from_slice(&b);
            return SecretKey::from_bytes(&a);
        }
    }
    let mut a = [0u8; 32];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        let _ = f.read_exact(&mut a);
    }
    let _ = std::fs::write(path, &a);
    SecretKey::from_bytes(&a)
}

#[derive(uniffi::Object)]
pub struct IrohClient {
    rt: Runtime,
    ep: Endpoint,
}

#[uniffi::export]
impl IrohClient {
    /// key_path persists the 32-byte node secret (a stable device node-id).
    #[uniffi::constructor]
    pub fn new(key_path: String) -> Result<Arc<Self>, IrohError> {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|_| IrohError::Bind)?;
        let secret = load_or_create_secret(&key_path);
        let ep = rt
            .block_on(async {
                Endpoint::builder(iroh::endpoint::presets::N0)
                    .secret_key(secret)
                    .bind()
                    .await
            })
            .map_err(|_| IrohError::Bind)?;
        Ok(Arc::new(IrohClient { rt, ep }))
    }

    /// This device's node-id (paste into the manager allowlist to pair).
    pub fn node_id(&self) -> String {
        self.ep.id().to_string()
    }

    /// Dial the manager gateway and open one bi stream for a single request.
    pub fn open(&self, manager_node_id: String) -> Result<Arc<IrohStream>, IrohError> {
        let ep = self.ep.clone();
        let (conn, send, recv) = self.rt.block_on(async move {
            let nid: iroh::PublicKey =
                manager_node_id.trim().parse().map_err(|_| IrohError::Parse)?;
            let conn = ep.connect(nid, ALPN).await.map_err(|_| IrohError::Connect)?;
            let (send, recv) = conn.open_bi().await.map_err(|_| IrohError::Connect)?;
            Ok::<_, IrohError>((conn, send, recv))
        })?;
        Ok(Arc::new(IrohStream {
            handle: self.rt.handle().clone(),
            _conn: conn,
            send: Mutex::new(send),
            recv: Mutex::new(recv),
        }))
    }
}

#[derive(uniffi::Object)]
pub struct IrohStream {
    handle: Handle,
    _conn: iroh::endpoint::Connection,
    send: Mutex<iroh::endpoint::SendStream>,
    recv: Mutex<iroh::endpoint::RecvStream>,
}

#[uniffi::export]
impl IrohStream {
    /// Write request bytes (call once with the full HTTP request, or in parts).
    pub fn write(&self, data: Vec<u8>) -> Result<(), IrohError> {
        self.handle.block_on(async {
            let mut s = self.send.lock().await;
            s.write_all(&data).await.map_err(|_| IrohError::Io)
        })
    }

    /// Half-close the send side (signals end-of-request to the manager).
    pub fn finish_send(&self) -> Result<(), IrohError> {
        self.handle.block_on(async {
            let mut s = self.send.lock().await;
            s.finish().map_err(|_| IrohError::Io)
        })
    }

    /// Read up to `max` response bytes. Returns an empty vec at end-of-stream.
    /// Blocking — the caller loops until it gets an empty vec.
    pub fn read(&self, max: u32) -> Result<Vec<u8>, IrohError> {
        self.handle.block_on(async {
            let mut r = self.recv.lock().await;
            let mut buf = vec![0u8; max.max(1) as usize];
            match r.read(&mut buf).await {
                Ok(Some(n)) => {
                    buf.truncate(n);
                    Ok(buf)
                }
                Ok(None) => Ok(Vec::new()),
                Err(_) => Err(IrohError::Io),
            }
        })
    }
    // No explicit close(): UniFFI objects are AutoCloseable — the generated
    // close()/destroy() drops this struct (and with it the streams/connection),
    // which is exactly what the caller's disconnect()/cancel needs.
}
