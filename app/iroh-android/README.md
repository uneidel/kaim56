# kaim_iroh — the app's iroh transport (native module)

A small Rust cdylib (UniFFI) that lets the Android app reach the manager over
**iroh** (P2P) instead of HTTPS: no VPN, no exposed HTTPS port, NAT-traversed and
end-to-end encrypted. It mirrors the verified `iroh-gw/src/bin/testclient.rs`.

## What it exposes (to Kotlin, via UniFFI)

- `IrohClient(keyPath)` — one endpoint per app, stable device node-id (key persisted at `keyPath`).
- `client.nodeId()` — this device's node-id; paste it into the manager allowlist to pair.
- `client.open(managerNodeId)` — dial the manager gateway, open one bi stream.
- `stream.write(bytes)` / `stream.finishSend()` / `stream.read(max)` / `stream.close()` — a duplex
  pipe; `read` returns an empty array at end-of-stream (so token streaming works).

Kotlin never calls this directly: `IrohNet` registers an `iroh://` URL scheme and
`IrohUrlConnection` speaks HTTP/1.1 over one stream, so every existing
`HttpURLConnection` call site keeps working — the base URL is just
`iroh://<manager-node-id>`.

## Build (produces the .so files + Kotlin bindings the APK needs)

```bash
cd app/iroh-android
./build-android.sh        # Docker: Rust + Android NDK + cargo-ndk + uniffi-bindgen
```

This cross-compiles `libkaim_iroh.so` for `arm64-v8a`, `armeabi-v7a`, `x86_64`
into `app/src/main/jniLibs/<abi>/`, generates the Kotlin bindings into
`app/src/main/kotlin/uniffi/kaim_iroh/`, and mirrors both into the `app/app/`
tree. Both are gitignored (regenerate with the script). The Gradle build then
packages them; the UniFFI runtime dependency (`net.java.dev.jna:jna`) is already
in `build.gradle.kts`.

Then build the APK as usual (`app/README.md`).

> Host-only sanity check (no NDK): `./build-check.sh` compiles the crate for the
> build host — useful when editing `src/lib.rs`.

## Pairing

1. Manager web UI → **Sharing → App transport (iroh)**: copy the manager node-id.
2. App → Settings → Server connection → paste it into **Manager node-id**.
3. Copy the app's **This device** node-id (shown there) back into the web UI's
   allowlist and **Add**. Done — the app now talks to the manager over iroh.
