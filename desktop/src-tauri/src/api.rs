// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The manager's HTTP API, reached through the local iroh tunnel port: the same
// calls (and Basic auth) the phone app makes.
use std::time::Duration;

use anyhow::{anyhow, Result};
use futures_util::StreamExt;
use serde_json::Value;

#[derive(Clone)]
pub struct Api {
    base: String,
    user: String,
    pass: String,
    http: reqwest::Client,
}

/// An error page as one readable line (the manager or a proxy may answer HTML).
pub fn plain(body: &str) -> String {
    if let Ok(v) = serde_json::from_str::<Value>(body) {
        if let Some(e) = v.get("error").and_then(|e| e.as_str()) {
            return e.to_string();
        }
    }
    let mut out = String::new();
    let mut tag = false;
    for ch in body.chars() {
        match ch {
            '<' => tag = true,
            '>' => { tag = false; out.push(' '); }
            c if !tag => out.push(c),
            _ => {}
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(300).collect()
}

impl Api {
    pub fn new(port: u16, user: &str, pass: &str) -> Self {
        Api {
            base: format!("http://127.0.0.1:{port}"),
            user: user.into(),
            pass: pass.into(),
            http: reqwest::Client::builder().connect_timeout(Duration::from_secs(20)).build().expect("http client"),
        }
    }

    fn req(&self, method: reqwest::Method, path: &str, timeout: u64) -> reqwest::RequestBuilder {
        self.http
            .request(method, format!("{}{}", self.base, path))
            .basic_auth(&self.user, Some(&self.pass))
            .timeout(Duration::from_secs(timeout))
    }

    async fn json(&self, r: reqwest::RequestBuilder) -> Result<Value> {
        let resp = r.send().await.map_err(|e| anyhow!("manager unreachable: {e}"))?;
        let code = resp.status();
        let body = resp.text().await.unwrap_or_default();
        if !code.is_success() {
            let why = plain(&body);
            return Err(anyhow!(if why.is_empty() { format!("HTTP {}", code.as_u16()) } else { format!("HTTP {}: {why}", code.as_u16()) }));
        }
        Ok(serde_json::from_str(&body).unwrap_or(Value::Null))
    }

    pub async fn get(&self, path: &str, timeout: u64) -> Result<Value> {
        self.json(self.req(reqwest::Method::GET, path, timeout)).await
    }

    pub async fn post(&self, path: &str, body: &Value) -> Result<Value> {
        self.json(self.req(reqwest::Method::POST, path, 60).json(body)).await
    }

    /// POST and hand the answer on chunk by chunk (a reply stream). Ok(()) at
    /// its end; the caller cancels by dropping the future.
    pub async fn stream(&self, path: &str, body: &Value, mut chunk: impl FnMut(&str)) -> Result<()> {
        let resp = self.req(reqwest::Method::POST, path, 1800).json(body).send().await
            .map_err(|e| anyhow!("manager unreachable: {e}"))?;
        let code = resp.status();
        if !code.is_success() {
            let why = plain(&resp.text().await.unwrap_or_default());
            return Err(anyhow!("HTTP {}{}", code.as_u16(), if why.is_empty() { String::new() } else { format!(": {why}") }));
        }
        let mut s = resp.bytes_stream();
        let mut rest = Vec::new();                       // a UTF-8 character split across chunks
        while let Some(b) = s.next().await {
            rest.extend_from_slice(&b?);
            let ok = match std::str::from_utf8(&rest) {
                Ok(_) => rest.len(),
                Err(e) => e.valid_up_to(),
            };
            if ok > 0 {
                chunk(std::str::from_utf8(&rest[..ok]).unwrap());
                rest.drain(..ok);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn plain_reads_json_errors_and_strips_html() {
        assert_eq!(super::plain(r#"{"error": "unknown instance"}"#), "unknown instance");
        assert_eq!(super::plain("<html><p>Instance 'x' is not running.</p></html>"), "Instance 'x' is not running.");
    }
}
