# firecracker — microVM-Plattform + Web-Manager

Verwaltet **1..x Firecracker-microVMs** über eine kleine Web-UI. Erste Instanz =
die Signal↔Claude-Code-Bridge (`claude`).

```
firecracker/
├── bin/           firecracker (v1.16.1 ✅) + vmlinux (Kernel, s. u.)
├── instances/     <name>.json + <name>-rootfs.ext4  (je Instanz)
├── run/           Laufzeit (sock/pid/log/config) – automatisch
├── manager.py     Web-UI + API (Port 8700), laeuft als root
├── chatui.py      Chat-Oberflaeche (vom Manager unter /chat ausgeliefert)
├── firecracker-manager.service   systemd-Autostart
└── traefik-firecracker.yml       Exposé via firecracker.kat56.de
```

## Chat-Oberfläche (`/chat`)
Chatten mit den Agenten läuft über die Seite **`/chat`** des Managers — gleiche
Herkunft, gleiche Traefik-Auth, kein Extra-Dienst und kein Extra-Port. Der Button
**💬 Chat** in der Instanz-Tabelle öffnet `/chat?i=<instanz>`.

Kann: Verlauf-Seitenleiste, Streaming-Antworten, Markdown mit Code-Blöcken +
Kopieren, Bild-Anhang (Vision), Abbrechen, Hell/Dunkel, mobil.

| Endpoint | Bedeutung |
|---|---|
| `GET /chat[?i=<instanz>]` | die Oberfläche (`chatui.py`) |
| `POST /api/chat/<instanz>` | `{message, image?}` → Antwort-Tokens als roher Text (Stream) |

Details:
- Chatbar ist jede Instanz mit `TRANSPORT=web`; eine **gestoppte Instanz startet
  beim ersten Prompt** (wartet auf Guest-Port 8080, max. 120 s).
- Gestreamt wird über `/api/chat/stream` der Bridge, sofern sie das kann
  (openrouter); sonst kommt die Antwort als ein Stück.
- Der **Verlauf liegt im localStorage** des Browsers — die microVM führt ihre
  eigene Session (`claude --resume` bzw. `_history`), deshalb geht pro Turn nur
  die neue Nachricht raus. Ein neuer Chat startet *keine* neue Agent-Session;
  dafür gibt es 🔄 im Kopf der Seite (stoppt + startet die Instanz).
- Die alten Chat-Seiten der Bridges bleiben unter `/i/<name>/` erreichbar
  (Fallback; die Agent-zu-Agent-API `/i/<name>/api/chat` nutzt denselben Pfad).

## Netz-Modell (pro Instanz aus `index`)
`host 172.30.<index>.1/30` · `guest 172.30.<index>.2/30` · `tap fc<index>` · MASQUERADE via `enp0s31f6`, Gast-DNS Pi-hole.

## Instanz-JSON (`instances/<name>.json`)
```json
{ "name":"claude", "index":1, "vcpus":2, "mem_mib":1536,
  "rootfs":"instances/claude-rootfs.ext4",
  "extra_drives":[ {"path":"/pfad/data.ext4","readonly":false} ] }
```
Neue Instanz = neue JSON mit anderem `index` + eigenes `rootfs.ext4`.

## Host-Ordner in die VM bekommen
Firecracker kann **keine** Host-Ordner bind-mounten (nur Block-Devices + Netz):
- **Extra-Disk:** ein ext4-Image als `extra_drives` anhängen (Snapshot bzw. Disk, die nur der Gast beschreibt).
- **Live-Share:** im Gast einen **NFS/SMB-Share** mounten (echter Live-Ordner) — der saubere Weg, wenn Claude auf einen Host-Ordner soll. Dazu auf dem Host NFS exportieren und in `guest-init.sh` der Instanz `mount -t nfs …` ergänzen.

Host-Pfade tippt man nicht mehr: **📁** in der Mount-Zeile (beim Anlegen wie in der
Instanz-Tabelle) öffnet einen **Ordner-Browser** über `GET /api/browse?path=…`
(nur Verzeichnisnamen, admin-only, versteckte Ordner ausgeblendet — direkt tippen
geht weiter). Der Gast-Pfad wird als `/mnt/<ordnername>` vorgeschlagen.

## Policy & Audit (Tab „Policy")
Eine Ansicht pro Instanz: was sie **darf** (Internet-Toggle, Werkzeug-Allowlist
zum Bearbeiten, erlaubte Secrets, MCP, Modell) und was sie **tut** — Knopf
*Aktivität* zeigt die zuletzt aufgerufenen Tools und Ziele (URL/Pfad/Query) aus
dem Audit-Log. Der Agent meldet jeden Tool-Aufruf an `/api/audit`; gespeichert
wird `audit/<name>.jsonl` auf dem Host (ueberlebt Neustarts, ohne Secret-Werte).
`GET /api/policy`, `GET /api/audit/<name>` — admin-only.

## Tasks (Tab „Tasks")
Geplante Arbeit pro Instanz: ein **Auftrag** (die Nachricht an den Agenten) laeuft
einmalig oder wiederkehrend. Zeitplan-Formate: `every Nm|Nh|Nd`, `daily HH:MM`,
`hourly`; leer = einmal sofort. Ein Hintergrund-Worker im Manager fuehrt faellige
Tasks aus (startet die Instanz bei Bedarf) und plant wiederkehrende neu.
`GET/POST /api/tasks`, `POST /api/tasks/<id>/delete` — admin-only.

## Capabilities pro Instanz
Beim Anlegen: **Internet-Zugang** (an/aus) und eine **Werkzeug-Allowlist**
(Checkboxen). Internet aus = die VM erreicht nur den Manager-Broker, nicht LAN/Web
und damit nicht den LLM. Live umschaltbar in der Tabelle (🌐/🚫). Die Tool-Auswahl
landet als `AGENT_TOOLS` in der Config; der Agent filtert Schema und Ausfuehrung.
`bash` ist der Generalschluessel — zum echten Sperren auch `bash` abwaehlen.

## Modell-Auswahl (Tab „Models")
Der Tab zieht den **vollen OpenRouter-Katalog live** (~400 Modelle, 10 min
gecacht, *Refresh catalog* umgeht den Cache) und laesst daraus die **Shortlist**
ankreuzen, die beim Anlegen einer Instanz im Modell-Dropdown steht. Gespeichert
wird sie in `models.json`; fehlt die Datei, ist die Konstante `CURATED` in
`manager.py` die Erstbefuellung. Speichern wirkt sofort — kein Neustart.

Filter: Textsuche ueber id und Name, *tool calling only* (Vorgabe) und
*selected only*. **Achtung:** Das openrouter-Template zieht die Liste mit
`tools=1`; ein Modell ohne Tool-Calling bleibt also unsichtbar, auch wenn es
angekreuzt ist — die Spalte zeigt es deshalb an.

| Endpoint | Bedeutung |
|---|---|
| `GET /api/openrouter-models[?refresh=1&tools=1&relevant=1]` | Katalog (`relevant=1` = nur Shortlist) |
| `GET /api/models` | die gespeicherte Shortlist |
| `POST /api/models` | `{curated:[id,…]}` speichern |

## katfs — Ordner aus dem Browser (Tab „Sharing")
Kein Host-Ordner, sondern das Verzeichnis **des Rechners, an dem du gerade sitzt**:
`iroh-fs/` gibt es per P2P an die Agenten (`remote_ls` / `remote_read` / `remote_write`).
Der Manager reicht die Freigabe-Seite unter **`/katfs/`** durch — gleiche Herkunft,
gleiche Auth und damit **HTTPS**, das die File System Access API zwingend braucht;
der SSH-Tunnel bzw. die eigene `katfs.kat56.de`-Route aus `iroh-fs/README.md` ist
dafür nicht mehr nötig. Der Tab zeigt Knoten-Status und ob gerade ein Browser teilt
(`GET /api/katfs/status`). **Kein Mount:** ist der Tab zu, ist der Ordner weg —
für dauerhafte Ordner die NFS-Mounts oben nehmen.

**Sharing key** = die **node-id** des Knotens, zu dem der Browser sich verbindet
(iroh parst sie als `EndpointId`: 64 Hex-Zeichen — ein *Ticket* akzeptiert die
WASM-Bruecke trotz des Platzhaltertexts auf der Seite nicht). Im Tab steht sie
zum Kopieren und ist editierbar: *Share a folder…* öffnet dann
`/katfs/?key=<node-id>`, und der Proxy setzt genau diesen Wert in das Feld
`#nodeid` der Freigabe-Seite. Damit kann derselbe Browser einen Ordner auch an
einen **fremden** katfs-Knoten liefern; *Reset* holt die eigene node-id zurück.

**Freigeben geht auf zwei Wegen.** Im **Browser** nur mit Chromium/Edge über
HTTPS — Firefox und Safari haben keine API, die in einen echten Nutzerordner
schreibt. Wer read/write braucht (oder Firefox benutzt), nimmt stattdessen
`iroh-fs/dist/katfs-share <node-id> <ordner>`: derselbe Provider als natives
Programm, ohne Browser, mit Schreibzugriff und stabiler share-id.

**Mehrere Freigaben, Auswahl beim Anlegen.** Der Knoten haelt beliebig viele
Freigaben gleichzeitig; jede meldet beim Verbinden eine **share-id** (im Browser
aus dem `localStorage`, bei `katfs-share` aus Hostname + Pfad abgeleitet) plus
Ordnername, Plattform und ob sie read-only ist.
Der Sharing-Tab listet sie, und im Anlege-Formular steht **katfs share** als
Auswahl → der gewaehlte Wert landet als `KATFS_SHARE` in der Instanz-Config, die
Agent-Tools haengen ihn als `&share=…` an. Ohne Auswahl bedient der Knoten die
Anfrage nur, solange **genau eine** Freigabe aktiv ist — bei mehreren nennt er
die ids, statt zu raten.

Die **node-id** ist etwas anderes und pro Instanz **kein** Wert: der Agent leitet
seinen Knoten aus der eigenen IP ab (`_katfs_base()` in `openrouter-agent/agent.py`
→ `http://<gateway>:8790`), die node-id sagt nur dem freigebenden *Browser*, wohin
er sich verbindet. Soll eine Instanz einen *anderen* Knoten benutzen, braucht es
eine Adresse: `KATFS_URL` in der Instanz-Config (z. B. `http://10.0.0.240:8790`).

⚠️ `KATFS_SHARE`/`KATFS_URL` wertet erst ein **neu gebautes openrouter-Rootfs**
aus (`agent.py` steckt im Image). Der Knoten selbst wird mit
`iroh-fs/node/build.sh` gebaut (Docker, kein lokales Rust noetig).

## Agent-Ordner (NFS-Live-Share)
`/home/ulrich/agent` wird per **NFSv4** live in die `claude`-VM als `/root/workspace`
gemountet — Claude arbeitet dort, die Dateien liegen auf dem Host. Einrichten (root):
```
sudo /home/ulrich/firecracker/setup-nfs-host.sh
```
Der Gast mountet beim Boot automatisch `<gateway>:/ -> /root/workspace`
(gesteuert über `AGENT_NFS`/`AGENT_EXPORT` in `claude-signal-firecracker/config.env`).
Gast-Writes erscheinen auf dem Host als `ulrich` (all_squash/anonuid=1000).

## Setup
**0) NFS-Agent-Share** (siehe oben): `sudo ./setup-nfs-host.sh`

**1) Kernel holen** — bereits erledigt (`bin/vmlinux` = 6.1.128, 40 MB). Falls neu nötig:
```
! curl -fsSL "https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.12/x86_64/vmlinux-6.1.128" -o /home/ulrich/firecracker/bin/vmlinux
```
**2) Claude-Rootfs bauen** (docker build → du) und ablegen:
```
! cd /home/ulrich/claude-signal-firecracker && ./build.sh && cp rootfs.ext4 /home/ulrich/firecracker/instances/claude-rootfs.ext4
```
**3) Manager starten** (root):
```
sudo cp /home/ulrich/firecracker/firecracker-manager.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now firecracker-manager
```
→ UI erreichbar unter `http://10.0.0.240:8700` (im LAN) und via Traefik unter
`https://firecracker.kat56.de` (nach Einbau von `traefik-firecracker.yml`).

## Exposé (firecracker.kat56.de)
`traefik-firecracker.yml` in den **File-Provider-Ordner** des Traefik-Stacks legen
(im traefik-Container `/etc/traefik/...`). Basic-Auth-Hash mit
`htpasswd -nbB admin 'PW'` erzeugen. **Wichtig:** Der Manager steuert VMs als root —
**nie ohne Auth exponieren** (Basic-Auth oder euer pocket-id/SSO davor).

## Changelog & Security (Tab „Changelog")
Der Tab zeigt oben die **offenen Befunde** aus `security.json` — nach Schweregrad
sortiert, mit Ort, Beschreibung und Fix-Vorschlag — und darunter den gerenderten
`CHANGELOG.md`. Behobenes ist ausgeblendet und laesst sich einblenden.

Ueber die UI laesst sich **nur der Status** umschalten (offen/erledigt); Text und
Bewertung stehen in der Datei, damit ein Befund nicht per Klick verschwindet.
Beide Routen sind admin-only — die Liste beschreibt Loecher, Gaeste lesen sie
nicht mit.

| Endpoint | Bedeutung |
|---|---|
| `GET /api/changelog` | `CHANGELOG.md` als Text |
| `GET /api/security` | `{issues:[…]}` |
| `POST /api/security` | `{issues:[{id,status}]}` — nur der Status |

## Credentials (Secret-Broker)
Zugangsdaten stehen **nicht** in der Instanz-Config und damit nicht auf der
Config-Disk der microVM. `SECRET_PARAMS` in `manager.py`
(`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) wird beim Anlegen
verworfen und beim Bau der Config-Disk ein zweites Mal — aeltere Instanz-JSONs
bereinigt der Manager beim Start selbst (`migrate_secrets_out_of_instances`).

Der Agent holt den Key stattdessen zur Laufzeit: `ensure_or_key()` fragt
`GET /api/secret/OPENROUTER_API_KEY` beim Manager an. Der erkennt den Gast an
der **Source-IP** (`instance_by_ip`), prueft die Allowlist aus
`secret-policy.json` (Default deny, `by_template` ∪ `by_instance`) und liefert
nur dann den Wert. Aufrufe, die zu keiner Instanz gehoeren — etwa vom Host —
bekommen `403 nicht erlaubt`.

Quelle der Werte ist `secret_store()`: der Secret-Store
(`~/.config/kat56/secrets.env`, 0600) und ergaenzend die Manager-Settings, wo die
LLM-Keys im Tab **Settings** gepflegt werden (`settings.json`, jetzt 0600).

**MCP-Server ebenso.** Frueher stand in `MCP_CONFIG` die fertige Konfiguration
*mit eingesetzten Tokens* — in `hass.json` das HA-Bearer-Token, das damit auch auf
der Config-Disk lag. Gespeichert wird jetzt nur `MCP_SERVERS=<name>,<name>`; der
Agent holt beim Start `GET /api/mcp-config` und bekommt die Konfiguration mit den
Werten, die die Policy **dieser Instanz** freigibt. Nicht Freigegebenes bleibt als
`${PLATZHALTER}` stehen und wird als `unresolved` gemeldet, statt still zu
scheitern. Der Endpunkt beantwortet nur Anfragen aus einer Instanz — vom Host aus
`403 nur fuer Gaeste`.

Der Manager hebt Altbestand beim Start selbst: `migrate_mcp_config_out_of_instances`
liest die Servernamen aus dem alten Blob, schreibt `MCP_SERVERS` und traegt die
noetigen Secrets **pro Instanz** in `by_instance` ein (also `hass -> HA_TOKEN`,
nicht fuer alle openrouter-Agenten).

Folge fuer den Betrieb: Ohne Eintrag in `secret-policy.json` startet ein
openrouter-Agent zwar, meldet aber `FATAL: OPENROUTER_API_KEY fehlt … vom
Secret-Broker` und kann nicht antworten. Ein MCP-Server ohne freigegebenes Token
kommt hoch, aber ohne Zugang. Der Tab **Secrets** ist die Stelle, an der man das
freigibt.

## Sicherheit
- Manager = root-Dienst, der microVMs startet/stoppt und tap/iptables setzt.
- Öffentlich nur **mit Auth** (Traefik-BasicAuth/SSO). Im LAN Port 8700 ggf. per Firewall begrenzen.
- Jede microVM ist HW-isoliert; Host-Zugriff nur über explizit angehängte Disks/NFS.

## Grenzen
`bin/vmlinux` + `instances/*-rootfs.ext4` müssen vorhanden sein, sonst schlägt
Start fehl (Log in `run/<name>.log`). Manager selbst braucht kein Rootfs.
