//! A minimal katfs/0 PROVIDER as a test client — what the browser normally is,
//! just without a browser. It lets you check the node's multi-share path
//! without Chromium: start several instances in parallel, then
//! test /shares, ?share=<id> and the error case "several active, none chosen"
//! against the HTTP API.
//!
//! Usage: cargo run --example fakeshare -- <node-id> <share-id> <name>
//!
//! Answers hello/list/stat/read; write is rejected. The content is hard-wired
//! (a single file `hello.txt`), it is only about the protocol.

use anyhow::{anyhow, Context, Result};
use iroh::endpoint::{RecvStream, SendStream};
use iroh::Endpoint;
use serde_json::{json, Value};

const ALPN: &[u8] = b"katfs/0";

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

#[tokio::main]
async fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let node_id = args.next().context("usage: fakeshare <node-id> <share-id> <name>")?;
    let share_id = args.next().unwrap_or_else(|| "test1".into());
    let name = args.next().unwrap_or_else(|| "Testordner".into());

    let ep = Endpoint::builder(iroh::endpoint::presets::N0)
        .alpns(vec![])
        .bind()
        .await
        .context("bind endpoint")?;
    eprintln!("[fakeshare] own id: {}", ep.id());

    let id: iroh::EndpointId = node_id.parse().context("parse node-id")?;
    let conn = ep.connect(id, ALPN).await.context("connect")?;
    eprintln!("[fakeshare] connected, waiting for the host to open the stream");

    // The HOST opens the stream and sends first (see PROTOCOL.md).
    let (mut send, mut recv) = conn.accept_bi().await.context("accept_bi")?;

    let payload = b"hello from the test share\n";
    loop {
        let raw = match read_frame(&mut recv).await {
            Ok(b) => b,
            Err(e) => {
                eprintln!("[fakeshare] stream closed: {e}");
                return Ok(());
            }
        };
        let req: Value = serde_json::from_slice(&raw)?;
        let rid = req.get("id").cloned().unwrap_or(json!(0));
        let op = req.get("op").and_then(|v| v.as_str()).unwrap_or("");
        if op == "write" {
            let _ = read_frame(&mut recv).await?; // Datenframe verwerfen
        }
        let (resp, extra): (Value, Option<&[u8]>) = match op {
            "hello" => (
                json!({"id": rid, "ok": true, "share": share_id, "name": name, "device": "fakeshare"}),
                None,
            ),
            "list" => (
                json!({"id": rid, "ok": true,
                       "entries": [{"name": "hello.txt", "dir": false, "size": payload.len()}]}),
                None,
            ),
            "stat" => (json!({"id": rid, "ok": true, "exists": true, "dir": true, "size": 0}), None),
            "read" => (json!({"id": rid, "ok": true, "size": payload.len()}), Some(payload)),
            _ => (json!({"id": rid, "ok": false, "error": format!("unknown op: {op}")}), None),
        };
        write_frame(&mut send, &serde_json::to_vec(&resp)?).await?;
        if let Some(p) = extra {
            write_frame(&mut send, p).await?;
        }
    }
}
