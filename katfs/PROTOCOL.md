# katfs/0 — P2P file access (iroh)

## Roles
- HOST node (native, Rust, on the server): iroh endpoint, **accepts** ALPN "katfs/0".
  It is the file CONSUMER: sends requests, receives responses.
- BROWSER (WASM): **connects** to the HOST (node-id/ticket). File PROVIDER:
  receives requests, answers from the directory chosen via the File System Access API.

## Transport
One iroh bidirectional connection. Each message: 4-byte big-endian length + JSON.
On read/write a second frame follows: 4-byte length + raw bytes (file content).

## Requests (HOST -> BROWSER)
- {"id":N,"op":"hello","path":"."}            -> {"id":N,"ok":true,"share":"<id>","name":"<folder>","device":"<platform>","readonly":bool}
- {"id":N,"op":"list","path":"rel/path"}      -> {"id":N,"ok":true,"entries":[{"name","dir","size"}]}
- {"id":N,"op":"stat","path":"..."}           -> {"id":N,"ok":true,"exists","dir","size"}
- {"id":N,"op":"read","path":"..."}           -> {"id":N,"ok":true,"size":M} + [M bytes]
- {"id":N,"op":"write","path":"...","size":M} + [M bytes] -> {"id":N,"ok":true}
- {"id":N,"op":"delete","path":"...","recursive":bool} -> {"id":N,"ok":true}
Error: {"id":N,"ok":false,"error":"..."}

## Multiple shares at once
The HOST holds **any number** of browser connections. Each announces itself on
connect via `hello` with a **share-id** that the browser keeps in `localStorage`
— a reload thus reconnects the *same* share (the old entry
is replaced, not duplicated). If a browser answers `ok:false` to `hello`
(old page), the host falls back to the `list "."` warmup and assigns the
id itself (`s<epoch>`).

The id comes from the browser and is **not access protection**: whoever knows it and
can reach the node can replace a share with the same id. This was already the case
before (every connection displaced the single active one) — only now on purpose.

## Host local HTTP API (for the agent tools; 127.0.0.1 / gateway)
- GET  /status                 -> {"connected":bool,"count":N,"share":"<name|N shares>"}
- GET  /shares                 -> {"shares":[{"id","name","device","readonly","since"}]}
- GET  /ls?path=...[&share=ID]    -> {"entries":[...]}
- GET  /read?path=...[&share=ID]  -> raw bytes (404 if missing)
- POST /write?path=...[&share=ID] (body=bytes) -> {"ok":true}
- POST /delete?path=...[&recursive=1][&share=ID] -> {"ok":true}
- GET  /                       -> browser page (web/), with embedded node-id
- GET  /nodeid                 -> {"node_id":"..."}

Without `share` the node serves the request only as long as **exactly one** share
is active; with several it answers with the list of ids instead of guessing.

`delete` is irreversible (no trash). Three safeguards: the root of the
share cannot be deleted (empty path -> 400), `..` is rejected as everywhere,
and a **non-empty directory** fails without `recursive=1`.
A share announced as read-only rejects `delete` just as it rejects `write`.

## Paths
Always relative to the shared root directory. ".." is rejected (no escape).

## Stream roles (added after implementation)
The HOST opens the bidirectional stream (`open_bi`) and sends the first request;
the BROWSER accepts it (`accept_bi`). Reason: in QUIC a stream only becomes visible
to the peer through the first bytes — the HOST (consumer) sends first.
Connecting via a bare node-id requires n0 DNS discovery (the N0 preset publishes the node-id).
