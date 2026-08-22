# katfs — P2P file access (iroh) for the server agent

A browser shares (via the **File System Access API**) a local directory; a
native **iroh** host node on the server connects P2P (relay-only over the
n0 relay, NAT-capable) and provides the file ops to the agent as an HTTP API.
The server agent uses the tools `remote_ls` / `remote_read` / `remote_write` / `remote_delete`.

## Components
- `dist/katfs-node` — native host node (Rust, iroh 1.0.3). Accepts ALPN `katfs/0`,
  HTTP API on `0.0.0.0:8790`, serves the browser page + WASM, stable node-id (`node/secret.key`).
  Holds **multiple shares at once**, addressed via the `share` id from `hello`
  (see `PROTOCOL.md`); `GET /shares` lists them. Build: `node/build.sh` (Docker, no local Rust).
- `web/` — browser app: `index.html` + `app.js` + `katfs-provider.js` + WASM bundle (`web/wasm/`).
- Agent tools in `openrouter-agent/agent.py`: `remote_ls/read/write` → `http://<gateway>:8790/…`.

node-id: `c35e0fa98dee08e7b0ab1f01b9d58127afb2362f708d2b1479f4c7f66305402d`

## Host node permanently (systemd) — run as root
```
sudo cp /tmp/katfs-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now katfs-node
```
(Currently running transitionally via `nohup`; log: `iroh-fs/katfs-node.log`.)

## Share a folder without a browser — `katfs-share` (recommended, read/write)
`client/` is the same PROVIDER as the browser tab, just native. This drops the
whole browser question: **Firefox and Safari have no API that writes into a real
user folder** (`webkitdirectory`/drag & drop only deliver readable `File`s,
OPFS is a sandbox) — `katfs-share` can read *and* write, on any OS.

```
katfs-share <node-id> <folder> [--name <label>] [--id <share-id>] [--ro]
```
- Build the Linux binary: `client/build.sh` (Docker, no local Rust) → `dist/katfs-share`.
- macOS: no cross-compile from Linux. On the Mac
  `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`, then in `client/`
  `cargo build --release` → `target/release/katfs-share`.
- The **share-id** is derived from hostname + absolute path, so it is stable
  across restarts (`--id` overrides). Exactly this id appears in the manager when
  creating an instance for selection.
- Reconnect is built in: a node restart or network change is caught by the client
  itself, every 2 s.
- `--ro` announces the share as read-only; the node shows this in `/shares`, the
  manager marks it in the selection.

## Share a folder (browser: Chromium/Edge only)
Easiest through the **Firecracker manager**: tab **"Sharing"** → *Share a folder…*
(it passes this page through under `/katfs/`, i.e. with its HTTPS origin and auth).
Directly at the node, the File System Access API needs a **secure context** (HTTPS **or** localhost):
- **Desktop:** `ssh -L 8790:127.0.0.1:8790 <server>` → open `http://127.0.0.1:8790` in the browser.
- **From anywhere (incl. phone):** create a Traefik route `katfs.example.com → 10.0.0.10:8790` (TLS),
  then open `https://katfs.example.com`.

On the page: **"Share folder"** → choose a directory → **"Connect"** (node-id is prefilled).
As long as the tab is open/connected, the agent can read/write.

## Using the agent
In the server chat: `remote_ls`, `remote_read(path)`, `remote_write(path, content)`.
Without an active share, the node returns `HTTP 503 {"error":"no browser connected"}`.

## Status
- ✅ Host node: built, running, HTTP API/assets/path safety tested; the agent reaches it (verified).
- ✅ Browser app: WASM compiled (iroh 1.0.3), provider logic 18/18 tests green.
- ⚠️ The live browser↔host iroh roundtrip is **not yet actually tested** (needs a real browser).
