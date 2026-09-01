// Smoke-test client for iroh-gw: dial the gateway by node-id, open one bi
// stream, send a raw HTTP request, print the response. Mirrors what the phone
// app does. Usage:
//   testclient --id                      -> print our node-id (add to allowlist)
//   testclient <gateway_node_id> <req>   -> tunnel `req` (an HTTP request) and print reply
use anyhow::{Context, Result};
use iroh::{Endpoint, SecretKey};
use std::io::Read;

const ALPN: &[u8] = b"kaim56-mgr/0";

fn secret() -> SecretKey {
    // stable client identity across runs
    let path = "/tmp/iroh-gw-testclient.key";
    if let Ok(b) = std::fs::read(path) {
        if b.len() == 32 { let mut a=[0u8;32]; a.copy_from_slice(&b); return SecretKey::from_bytes(&a); }
    }
    let mut a=[0u8;32];
    std::fs::File::open("/dev/urandom").unwrap().read_exact(&mut a).unwrap();
    let _ = std::fs::write(path, &a);
    SecretKey::from_bytes(&a)
}

#[tokio::main]
async fn main() -> Result<()> {
    let ep = Endpoint::builder(iroh::endpoint::presets::N0)
        .secret_key(secret()).bind().await.context("bind")?;
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--id") {
        println!("{}", ep.id());
        return Ok(());
    }
    let gw = args.get(1).context("need gateway node-id")?;
    let req = args.get(2).map(|s| s.as_str())
        .unwrap_or("GET /api/iroh HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n");
    let node_id: iroh::PublicKey = gw.parse().context("parse node-id")?;
    eprintln!("[client] dialing {node_id} ...");
    let conn = ep.connect(node_id, ALPN).await.context("connect")?;
    eprintln!("[client] connected; opening bi stream");
    let (mut send, mut recv) = conn.open_bi().await.context("open_bi")?;
    send.write_all(req.as_bytes()).await?;
    send.finish()?;
    let buf = recv.read_to_end(1 << 20).await.unwrap_or_default();
    println!("--- reply ({} bytes) ---", buf.len());
    println!("{}", String::from_utf8_lossy(&buf));
    Ok(())
}
