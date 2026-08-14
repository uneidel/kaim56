# katfs — P2P-Dateizugriff (iroh) für den Server-Agenten

Ein Browser gibt (via **File System Access API**) ein lokales Verzeichnis frei; ein
nativer **iroh**-Host-Knoten auf dem Server verbindet sich P2P (relay-only über den
n0-Relay, NAT-tauglich) und stellt die Datei-Ops dem Agenten als HTTP-API bereit.
Der Server-Agent nutzt die Tools `remote_ls` / `remote_read` / `remote_write` / `remote_delete`.

## Komponenten
- `dist/katfs-node` — nativer Host-Knoten (Rust, iroh 1.0.3). Akzeptiert ALPN `katfs/0`,
  HTTP-API auf `0.0.0.0:8790`, liefert die Browser-Seite + WASM aus, stabile node-id (`node/secret.key`).
  Haelt **mehrere Freigaben gleichzeitig**, adressiert ueber die `share`-id aus `hello`
  (s. `PROTOCOL.md`); `GET /shares` listet sie. Bauen: `node/build.sh` (Docker, kein lokales Rust).
- `web/` — Browser-App: `index.html` + `app.js` + `katfs-provider.js` + WASM-Bundle (`web/wasm/`).
- Agent-Tools in `openrouter-agent/agent.py`: `remote_ls/read/write` → `http://<gateway>:8790/…`.

node-id: `c35e0fa98dee08e7b0ab1f01b9d58127afb2362f708d2b1479f4c7f66305402d`

## Host-Knoten dauerhaft (systemd) — als root ausführen
```
sudo cp /tmp/katfs-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now katfs-node
```
(Läuft aktuell übergangsweise via `nohup`; Log: `iroh-fs/katfs-node.log`.)

## Ordner freigeben ohne Browser — `katfs-share` (empfohlen, read/write)
`client/` ist derselbe PROVIDER wie der Browser-Tab, nur nativ. Damit entfaellt die
ganze Browser-Frage: **Firefox und Safari haben keine API, die in einen echten
Nutzerordner schreibt** (`webkitdirectory`/Drag&Drop liefern nur lesbare `File`s,
OPFS ist eine Sandbox) — `katfs-share` kann lesen *und* schreiben, auf jedem OS.

```
katfs-share <node-id> <ordner> [--name <label>] [--id <share-id>] [--ro]
```
- Linux-Binary bauen: `client/build.sh` (Docker, kein lokales Rust) → `dist/katfs-share`.
- macOS: kein Cross-Compile von Linux aus. Auf dem Mac
  `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`, dann in `client/`
  `cargo build --release` → `target/release/katfs-share`.
- Die **share-id** wird aus Hostname + absolutem Pfad abgeleitet, ist also ueber
  Neustarts stabil (`--id` ueberschreibt). Genau diese id steht im Manager beim
  Anlegen einer Instanz zur Auswahl.
- Reconnect ist eingebaut: Knoten-Neustart oder Netzwechsel faengt der Client
  selbst ab, alle 2 s.
- `--ro` meldet die Freigabe als read-only; der Knoten zeigt das in `/shares`, der
  Manager markiert sie in der Auswahl.

## Ordner freigeben (Browser: nur Chromium/Edge)
Am einfachsten über den **Firecracker-Manager**: Tab **„Sharing"** → *Share a folder…*
(er reicht diese Seite unter `/katfs/` durch, also mit seiner HTTPS-Herkunft und Auth).
Direkt am Knoten braucht die File System Access API einen **sicheren Kontext** (HTTPS **oder** localhost):
- **Desktop:** `ssh -L 8790:127.0.0.1:8790 <server>` → im Browser `http://127.0.0.1:8790` öffnen.
- **Von überall (auch Handy):** Traefik-Route `katfs.kat56.de → 10.0.0.240:8790` (TLS) anlegen,
  dann `https://katfs.kat56.de` öffnen.

Auf der Seite: **„Ordner freigeben"** → Verzeichnis wählen → **„Verbinden"** (node-id ist vorausgefüllt).
Solange der Tab offen/verbunden ist, kann der Agent lesen/schreiben.

## Agent nutzen
Im Server-Chat: `remote_ls`, `remote_read(path)`, `remote_write(path, content)`.
Ohne aktive Freigabe liefert der Knoten `HTTP 503 {"error":"no browser connected"}`.

## Status
- ✅ Host-Knoten: gebaut, läuft, HTTP-API/Assets/Pfad-Sicherheit getestet; Agent erreicht ihn (verifiziert).
- ✅ Browser-App: WASM kompiliert (iroh 1.0.3), Provider-Logik 18/18 Tests grün.
- ⚠️ Der Live-Browser↔Host-iroh-Roundtrip ist **noch nicht real getestet** (braucht einen echten Browser).
