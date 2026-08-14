# kAIm56

Monorepo für die Agenten-Plattform: ein **Manager** verwaltet Agenten in eigenen
Firecracker-microVMs, eine **Android-App** und eine **Web-Oberfläche** chatten mit
ihnen, **katfs** reicht Ordner vom Browser per P2P an die Agenten durch.

```
Handy (App) ─┐
             ├─► Manager (:8700, Web-UI + API) ─► microVM je Agent ─► LLM / MCP / Tools
Browser ─────┘         │                              ▲
                       └── katfs (iroh, P2P) ──────────┘
```

## Was liegt wo

| Pfad | Inhalt |
|---|---|
| `manager/` | Manager: `manager.py` (Web-UI + API + VM-Lebenszyklus), `chatui.py` (`/chat`), `webterm.py` (Browser-Terminal), `templates/` (Agenten-Vorlagen), systemd-Unit, `logo.svg` |
| `app/` | **KatAgent** (Android, Kotlin/Compose): Chat mit Server-Agenten + lokales Gemma-Modell. Build ohne lokales Android-SDK über `Dockerfile.build` |
| `katfs/` | Ordner-Freigabe vom Browser an die Agenten über iroh (P2P): `node/` + `client/` (Rust), `web/` (WASM-Bridge), `PROTOCOL.md` |
| `agents/` | Bauskripte und Gast-Bridges der microVM-Images: `claude/`, `openrouter/`, `pi/`, `prime/` |
| `examples/` | Vorlagen für die Dateien, die bewusst nicht im Repo liegen |

## Was bewusst NICHT im Repo ist

Nichts davon ist ein Versehen — siehe `.gitignore`:

- **Secrets**: `manager/settings.json` (API-Keys), `app/keystore/` (Signaturschlüssel
  der App), `katfs/node/secret.key` (iroh-Identität), `manager/traefik-agents.yml`
  (basicAuth-Hash → als Beispiel in `examples/`)
- **Laufzeitdaten**: Chat-Verlauf, Tasks, Memory, History-DB, Audit-Log, `run/`
- **Images**: `*.ext4` (rootfs, GB-Bereich) und `vmlinux` — werden gebaut, nicht versioniert
- **Releases**: `*.apk` gehören an die Gitea-Releases, nicht in die Historie

Enthalten sind dagegen `personas.json`, `skills.json`, `mcp-catalog.json` und
`secret-policy.json` — das ist Konfiguration bzw. selbst verfasster Inhalt, und die
MCP-Einträge referenzieren Secrets nur als `${NAME}`-Platzhalter.

> Hinweis: In `manager/templates/*.json` und `agents/*/config*.env` stehen echte
> Telefonnummern als Defaults (Signal-Bot und erlaubte Absender), und überall
> tauchen interne Hostnamen/IPs auf. Kein Geheimnis, aber personenbezogen —
> deshalb ist dieses Repo als **privat** gedacht.

## Manager starten

```bash
cd manager
cp ../examples/settings.example.json settings.json   # API-Keys eintragen (chmod 600)
sudo python3 manager.py                              # oder via firecracker-manager.service
```

Braucht auf dem Host: `bin/firecracker` + `bin/vmlinux`, die rootfs-Images unter
`instances/` und die NFS-Freigabe (`setup-nfs-host.sh`). Details in `manager/README.md`.

## App bauen

```bash
cd app
docker build -f Dockerfile.build -t katagent-build .
docker run --rm -v "$PWD":/project -v katagent-gradle:/root/.gradle katagent-build \
  gradle assembleDebug --no-daemon --console=plain
# -> app/build/outputs/apk/debug/app-debug.apk
```

## katfs

`katfs/node` (Host-Gateway) und `katfs/client` sind Rust-Crates, `katfs/web` die
WASM-Bridge für die Browser-Freigabe. Protokoll: `katfs/PROTOCOL.md`.

## Änderungen

`manager/CHANGELOG.md` führt die Historie der Plattform.
