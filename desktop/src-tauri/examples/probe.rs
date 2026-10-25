// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The app's transport, end to end without a window: the in-process iroh tunnel
// and the API client against a real gateway.
//   cargo run --example probe -- <key-file> --id
//   cargo run --example probe -- <key-file> <gateway-node-id> [user pass]
// Without credentials a 401 from the manager proves the path works.
use kaim56_desktop::{api, tunnel};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let a: Vec<String> = std::env::args().collect();
    let key = tunnel::secret(std::path::Path::new(&a[1]))?;
    if a.get(2).map(String::as_str) == Some("--id") {
        println!("{}", key.public());
        return Ok(());
    }
    let t = tunnel::start(key, &a[2]).await?;
    let api = api::Api::new(t.port, a.get(3).map(String::as_str).unwrap_or(""), a.get(4).map(String::as_str).unwrap_or(""));
    match api.get("/api/instances", 40).await {
        Ok(v) => println!("OK: {} instances", v.as_array().map(|x| x.len()).unwrap_or(0)),
        Err(e) => println!("answer: {e}"),
    }
    Ok(())
}
