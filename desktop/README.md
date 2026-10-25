# kAIm56 for Mac

The phone app's core as a macOS desktop app (Tauri 2, Rust): chat with the agents (the shared chat
store of phone, web and Mac), the "Waiting for you" queue, the manager's notifications as macOS
notifications, an update check against the `mac-v*` releases.

**Transport: iroh only.** The Rust side runs an iroh endpoint in-process (`src-tauri/src/tunnel.rs`,
the same splice as `iroh-gw/src/bin/tunnel.rs`) and speaks the manager's normal HTTP API with Basic
auth through it — no public HTTPS URL, end-to-end encrypted, and the gateway's NodeId allowlist
decides who gets in.

## Pairing

1. Settings → copy **This device's NodeId**.
2. Manager web UI → iroh → add it to the allowlist.
3. Copy the manager's **gateway NodeId** from there into Settings, plus user and password → *Save and connect*.

The node key, settings (incl. the password, 0600) and the local chat copy live in
`~/Library/Application Support/de.kat56.kaim56.desktop/`.

## Build

- Release: bump `version` in `src-tauri/tauri.conf.json` **and** `src-tauri/Cargo.toml`, write the
  `## <version>` section in `RELEASE_NOTES.md`, push to main — `.github/workflows/mac.yml` builds a
  universal `.dmg` on a macOS runner and publishes `mac-v<version>`.
- Local on a Mac: `cd desktop && npx @tauri-apps/cli@2 icon app-icon.png -o src-tauri/icons && npx @tauri-apps/cli@2 build`.
- Tests (any OS with the Tauri deps): `cd src-tauri && cargo test`.
- Transport check without a window: `cargo run --example probe -- <key-file> <gateway-node-id> [user pass]`.

## Layout

| File | What |
|---|---|
| `src-tauri/src/tunnel.rs` | iroh endpoint + local listener, one bi stream per HTTP connection |
| `src-tauri/src/api.rs` | the manager API through the tunnel (JSON, reply streaming) |
| `src-tauri/src/chats.rs` | the chat JSON and the merge rules of the phone app (`ChatMerge.kt`) |
| `src-tauri/src/lib.rs` | state, background loops (chats, notifications, waiting), the window's commands |
| `ui/` | the window: plain HTML/JS/CSS in the Industry style |
