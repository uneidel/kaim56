# Changelog

## 2026-08-14 (Fix: Frage aus der App verschwand)
- **Regression aus dem Live-Sync (3.7).** Der Merge ersetzte Conversation-
  Objekte (`byId[id] = r`). `send()` haelt aber eine Referenz auf
  `current.messages` und streamt die Antwort dorthin — wurde das Objekt
  ausgetauscht, landeten Frage und Antwort in einer abgehaengten Liste:
  aus der App verschwunden, nie gespeichert, nie gepusht.
- Der Merge **befuellt jetzt das bestehende Objekt** statt es zu ersetzen, und
  Nachrichten werden nur **angehaengt**: nur wenn die lokale Liste ein Praefix
  der entfernten ist, wird uebernommen. Damit kann ein neuerer Stand der
  Gegenseite (Web/Manager, ggf. mit vorgehender Uhr) eine gerade getippte,
  noch nicht gepushte Frage nicht mehr wegwischen. Die offene Konversation
  bleibt waehrend eines laufenden Turns ganz unangetastet.
- Dieselbe Absicherung in der Web-Oberflaeche (`chatui.py`) — dort haelt
  `send()` das Reply-Objekt genauso fest.
- KatAgent **3.8** (versionCode 38).

## 2026-08-14 (Token-Zaehler je Instanz)
- Neue Tabelle `llm_usage` in `history.db` und Gast-Route **`POST /api/usage`**:
  der Agent meldet nach jedem LLM-Aufruf Tokens und Kosten, der Manager bucht
  sie auf die Instanz. Die Zuordnung kommt aus der **Quell-IP**, nicht aus dem
  Body — eine VM kann den Verbrauch einer anderen nicht faelschen.
- `GET /api/usage` (Admin, fuer Gaeste gesperrt) liefert je Instanz heute und
  gesamt: Aufrufe, Tokens rein/raus, Kosten.
- Die Instanz-Tabelle zeigt die Zeile unter dem Modell-Chip, die Fusszeile die
  Summe ueber alle Instanzen.
- `agent.py`: fordert `usage:{include:true}` an (damit OpenRouter die realen
  Kosten je Aufruf mitschickt), meldet streamend wie nicht-streamend, und
  `OPENROUTER_URL` ist jetzt per Env setzbar. Melden ist fire-and-forget: faellt
  der Manager aus, stoert das den Chat nicht.
- Rootfs `openrouter` neu gebaut; verifiziert: ein Prompt an den Orchestrator
  buchte 1313/29 Tokens und $0.0005.
- Grenze: nur die `openrouter`-Vorlage meldet. `pi` und `prime` rufen ihre
  eigenen CLIs auf, `claude` rechnet ueber das Abo — dort gibt es (noch) keine
  Zahlen.

## 2026-08-14 (Gaeste ohne Internet: HOSTIF zeigte ins Leere)
- **Befund:** seit dem Reboot um 03:07 erreichte keine microVM mehr DNS oder LLM
  (`gaierror -3`), auch der Orchestrator-Heartbeat lief ins Leere. Ursache war
  nicht die VM, sondern die NAT-Regel des Hosts: die Unit setzte
  `Environment=HOSTIF=enp0s31f6`, der Uplink heisst aber `eno2`. MASQUERADE auf
  ein nicht existierendes Interface trifft nichts — die Pakete der Gaeste gingen
  unmaskiert raus und kamen nie zurueck. Kein Log, keine Fehlermeldung.
- **Fix:** `manager.py` verlaesst sich nicht mehr blind auf den Namen. Ein
  gesetztes `HOSTIF` gilt nur, wenn `/sys/class/net/<name>` existiert; sonst
  gewinnt das Interface der Default-Route (mit Hinweis im Log). Die Unit-Vorlage
  hat die Zeile jetzt auskommentiert.
- Verifiziert: nach Manager- und Instanz-Neustart antwortet der Orchestrator in
  1,6 s ("pong") statt nach 20 s DNS-Timeout.
- Hinweis: die installierte Unit unter `/etc/systemd/system/` traegt weiterhin
  `HOSTIF=enp0s31f6` (root-only). Dank der Erkennung ist das folgenlos, sollte
  aber bei Gelegenheit aufgeraeumt werden.

## 2026-08-14 (Chats live zwischen App und Web)
- `/api/chats` kann jetzt **Long-Poll**: `?since=<rev>&wait=<sek>` blockiert, bis
  jemand schreibt, und antwortet dann mit `{rev, chats}` (bei Zeitablauf
  `chats:null`). Ohne die Parameter unveraendert die blanke Liste — alte Clients
  laufen weiter. Jedes Schreiben am Store zaehlt eine zeitbasiert-monotone
  Revision hoch und weckt alle Wartenden (`threading.Condition`).
- Die Web-Oberflaeche haengt dauerhaft an diesem Long-Poll: neue Nachrichten aus
  der App stehen ohne Neuladen da (gemessen ~50 ms nach dem Push). Der gerade
  streamende Chat wird beim Merge ausgelassen, damit kein Teiltext verlorengeht;
  der Push nach lokalen Aenderungen wurde von 1200 auf 400 ms verkuerzt.
- Nebenbei: die Gast-Sperre fuer `/api/chats` & Co. verglich den kompletten Pfad
  und haette einen Query-String nicht mehr erkannt — vergleicht jetzt den Pfad
  ohne Query.
- **Die App zieht nach (KatAgent 3.7, versionCode 37):** neues
  `ManagerSync.pollChats()` (Long-Poll mit passend erhoehtem Lesetimeout) und
  eine Dauerschleife in `MainActivity` statt des einmaligen `sync()` beim Start.
  Waehrend eine Antwort streamt (`busy`) wird nicht gemerged; der Merge schreibt
  nur lokal (`store.save`), pusht also nicht zurueck -> kein Ping-Pong.
  Damit ist der Sync in beide Richtungen live. APK: `katagent-3.7.apk`.

## 2026-08-14 (Rebrand: kAIm56)
- **"Firecracker Manager" is gone from the UI — the product is `kAIm56`.**
  Renamed in the page title, the topbar wordmark, the footer, the chat UI's
  back-link, the basic-auth realm, the outgoing User-Agent and the startup log.
- New **`logo.svg`** (hexagon + triangle with side midpoints, incircle and
  centroid) served at `/logo.svg` and `/favicon.ico`, used as the browser-tab
  icon on both the manager and the chat page. The header mark inherits the text
  colour (`currentColor` for the navy) so it carries in light and dark theme;
  the teal stays the accent.
- The topbar no longer prints the hostname next to the wordmark (that was
  `location.host`, i.e. `agents.kat56.de` — never a hardcoded string). Header is
  now mark + name only.
- Unchanged on purpose: the `firecracker-manager.service` unit, the `~/firecracker`
  directory and the Firecracker binary path — those are the hypervisor, not the brand.

## 2026-08-14 (Model dropdown + UI now fully English)
- **Every template can now pick its LLM.** The `claude` template had no model
  field at all — new param `ANTHROPIC_MODEL` (dropdown: account default,
  `opus`/`sonnet`/`haiku` aliases, or a pinned id). It reaches Claude Code as an
  env var via the config disk (`set -a; . /config/config.env` in guest-init), so
  it applies to the signal, web and mail transports without a rootfs rebuild.
  Select options may now be `{value,label}` pairs, not just strings.
- **Model as a dropdown** for the `pi` and `prime` templates: `PI_MODEL` /
  `PRIME_MODEL` are `type: select, source: openrouter` (tool-capable models from
  the shortlist), just like `OPENROUTER_MODEL` — no more free-text typing.
- Every model dropdown gained **"— other model id… —"**, which swaps the select
  for a text field (direct Anthropic/OpenAI ids are not in the OpenRouter
  catalog); the ⟳ button switches back to the list. A stored value outside the
  shortlist stays selected instead of silently falling back to the first entry.
- **UI and API messages are English throughout** — create form, Policy, Tasks,
  Security/Changelog, the activity dialog, the instance rows, the chat UI
  (`chatui.py`) and every `msg` the API returns. Code comments stay German.

## 2026-08-14 (Sofort-Trigger fuer den Orchestrator)
- Neue Nutzer-Nachricht (Signal ueber /api/chat-log, App/Web ueber /api/chats)
  stoesst den Orchestrator **debounced (8 s)** an — er reagiert in Sekunden
  statt erst beim 2-h-Heartbeat. Coalesct Bursts, ein Lauf zur Zeit, feuert nur
  bei wirklich neuem Posteingang (peek). Reiner Manager-Code, kein Rootfs-Build.
- Verifiziert: Nachricht -> ~15 s spaeter Orchestrator auto-gestartet ->
  read_inbox -> create_task(target=remote) fuer die Anfrage. Der 2-h-Heartbeat
  bleibt als Fallback (zeitbasierte/wiederkehrende Checks).


## 2026-08-14 (Orchestrator-Posteingang: Signal/Chat-Store als Quelle)
- Neues Tool **`read_inbox`** (Gast-Route `/api/inbox`): neue Nutzer-Nachrichten
  aus dem gemeinsamen Chat-Store (Signal/App/Web) seit dem letzten Lauf, mit
  **Wasserzeichen** (jede Nachricht nur einmal; ?peek=1 = Vorschau ohne Verbrauch).
  Task-Konversationen ausgeblendet.
- Heartbeat des Orchestrators liest jetzt zuerst den Posteingang. Verifiziert:
  neue Signal-Nachricht "Kellertuer 22:00 pruefen" -> read_inbox -> recall_tasks
  -> list_agents -> create_task(target=hass, daily 22:00). Korrektes Routing zur
  faehigen Instanz, Posteingang danach verbraucht.


## 2026-08-14 (Orchestrator-Heartbeat "Lloyd")
- Neues Tool **`list_agents`** (Gast-Route `/api/agents`): Agenten-Roster +
  Faehigkeiten (Modell/MCP) fuers Routing — ohne Secrets.
- **`orchestrator`-Persona** angelegt: verwaltet Arbeit statt sie auszufuehren —
  list_agents -> recall_tasks (Dubletten-Check) -> create_task an die FAEHIGE
  Instanz (bzw. ephemeral). Aus vorhandenen Bausteinen, kaum neuer Code.
- **`orchestrator`-Instanz** (gemini-2.5-flash) + wiederkehrender **Heartbeat-
  Task** (every 2h). Der Worker weckt die Instanz zum Lauf und laesst sie danach
  laufen.
- Verifiziert: ein Heartbeat mit konkretem Anlass -> recall_tasks (keine Dubl.)
  -> create_task(daily 08:00) korrekt angelegt.
- Offen: der Heartbeat kann bisher Web/HTTP nutzen und an faehige Agenten
  delegieren (z. B. hass), aber NOCH nicht Host-Logs/E-Mail/den Signal-Chat-Store
  lesen — dafuer braucht es je ein kleines Quell-Tool (naechster Schritt).


## 2026-08-14 (Abfragbare Aufgaben-History / Stammwissen)
- Neue **SQLite-History** (`history.db`, stdlib — keine Abhaengigkeit): jeder
  ausgefuehrte Task (Worker + synchrones create_task) wird mit Ziel, Aufgabe,
  Ergebnis, ok, Zeitplan, Herkunft gespeichert. WAL-Modus, thread-safe.
- Agent-Tool **`recall_tasks(query, limit)`** ueber `GET /api/history` — Agenten
  fragen die Vergangenheit ab ("haben wir das schon gemacht?"), VOR create_task
  gegen Dubletten. Damit hat der (kommende) Orchestrator sein Stammwissen.
- Verifiziert: create_task -> Ergebnis in History; recall_tasks(query) findet den
  Lauf wieder (Ziel/Aufgabe/Ergebnis + Zeit).
- Idee aus der Symphony-Diskussion uebernommen (queryable memory), NICHT das
  coding-spezifische Symphony selbst.


## 2026-08-14 (Aufgaben aus der VM: create_task)
- Neues Agent-Tool **`create_task(task, target, schedule, wait)`** — aus jeder
  VM aufrufbar (gegated ueber AGENT_TOOLS). Manager-Gast-Route `POST /api/task`.
- **Routing statt VM-pro-Task:** `target=<instanz>` fuehrt die Aufgabe dort aus,
  wo ihre Tools/MCP/Secrets leben (z. B. `hass` fuer Home Assistant);
  `target=ephemeral` spinnt eine frische, isolierte VM und reisst sie danach ab.
- **Sync/Async:** `wait=true` blockiert und liefert das Ergebnis direkt;
  sonst laeuft es im Hintergrund-Worker und das Ergebnis landet in der
  gemeinsamen Chat-Historie (`task-<target>`) — sichtbar in App/Web/Signal.
- `schedule` (every Nm|Nh|Nd, daily HH:MM, hourly) fuer wiederkehrende Aufgaben.
- Runaway-Schutz: ephemere Kinder (task-*/sub-*) duerfen selbst keine Tasks
  anlegen. `spawn_subagent`-Poll von 3 s auf 1 s gestrafft.
- Verifiziert: ephemer sync -> `TASKOK`; async -> Worker done + Chat-Eintrag;
  keine ephemeren Leichen. v1 seriell (eine Aufgabe zur Zeit) — Parallelitaet
  mit N-Deckel ist der naechste Schritt.


## 2026-08-13 (Signal in den gemeinsamen Chat-Sync)
- Signal-Nachrichten liefen bisher nur im signal_loop der VM zur Signal-API und
  landeten nie im gemeinsamen Store. Jetzt meldet jeder Bridge-Turn (Frage+
  Antwort) an **`POST /api/chat-log`** (Gast per Source-IP erkannt); der Manager
  haengt ihn als Konversation `sig-<instanz>-<sender>` an `chats.json`. Damit
  erscheinen Signal-Chats in **App und Web** wie normale Chats.
- Umgesetzt in allen drei Bridges (openrouter/pi/prime), Images neu gebaut.
  Verifiziert: Gast-POST -> `Signal · remote`-Konversation im Store.
- Grenze: Der **claude**-Signal-Bridge (claude-signal-firecracker) ist ein
  eigener Codestand und noch NICHT angebunden.


## 2026-08-10 (Audit + Policy-Ansicht)

### Audit-Log pro Instanz
- Der Agent meldet jeden Werkzeug-Aufruf an den Manager (`/api/audit`,
  Gast per Source-IP erkannt) — **Tool + Zielfeld** (URL bei http_fetch, Query
  bei web_search, Pfad bei Datei/katfs-Tools, Kommando-Kopf bei bash) und ein
  ok-Flag. **Nie** Secret-Werte oder Dateiinhalte (get_secret loggt nur den
  Namen). Landet als `audit/<name>.jsonl` **auf dem Host** — ueberlebt
  VM-Neustarts, auf die letzten 2000 Zeilen begrenzt. `GET /api/audit/<name>`
  ist admin-only. Verifiziert: `http_fetch -> https://example.com`,
  `web_search -> Hummel`; ein Gast bekommt beim Lesen `forbidden`.

### Policy-Tab
- Eine Ansicht **pro Instanz**, die die verstreuten Kontrollen zusammenzieht:
  Internet (Live-Toggle), Modell, **Werkzeug-Allowlist zum Bearbeiten**
  (`POST /api/instances/<n>/tools`, wirkt nach Stop/Start), erlaubte Secrets,
  MCP-Server, katfs-Freigabe — plus Knopf **Aktivität** mit den zuletzt
  aufgerufenen Tools/URLs aus dem Audit-Log. `GET /api/policy` (admin-only).

### Instanz-Tabelle
- Jede Zeile zeigt jetzt das **verwendete Modell** (Chip statt Emoji).

### Chat-UI
- `/chat` sendet `Cache-Control: no-store`; Icons als Inline-SVG direkt im HTML
  (die grauen Emoji-Kaestchen waren gecachte alte Seiten + fehlende Emoji-Schrift).


## 2026-08-10 (Nachtrag: pi/prime-Fix)
- Die Credential-Umstellung hatte pi/prime vermint: Keys wurden aus der Config
  gestrippt, aber nur openrouter-agent holte sie ueber den Broker. Jetzt holen
  auch **pi und prime** fehlende Provider-Keys beim Start vom Broker
  (`ensure_provider_keys`), Policy fuer beide Templates ergaenzt. Verifiziert
  (pi -> `piok` vom LLM, prime -> Key vom Broker, nichts auf der Config-Disk).
- Beide Build-Skripte bekamen dieselben Fixes wie openrouter: `mkfs.ext4`-PATH
  und **atomarer** Rootfs-Install (mv statt cp in die laufende Datei).


## 2026-08-10

### Agenten-Capabilities (pro Instanz steuerbar)
- **Internet-Schalter.** Neue Instanz-Option `internet` (Default an). Aus =
  die VM darf ihr eigenes /30 nicht verlassen: kein LAN, kein Web — und damit
  auch **kein LLM**, der Agent kann dann nicht denken (im UI so beschriftet).
  Der Manager-Broker am Gateway (8700, host-lokal) bleibt erreichbar.
  Umschaltbar **live** ueber die Instanz-Tabelle (🌐/🚫), ohne Neustart —
  `apply_internet` setzt/entfernt die Egress-Regeln der Tap.
- **Werkzeug-Allowlist.** Neue Option `tools` beim Anlegen (Checkbox-Liste aus
  `/api/agent-tools`). Landet als `AGENT_TOOLS=<namen>` in der Config; der Agent
  filtert damit **Schema UND Ausfuehrung** (`tool_enabled`) — ein Modell kann ein
  abgeschaltetes Tool weder sehen noch erzwingen. Verifiziert: Teilmenge -> Boot
  meldet `tools=3` statt 17, `list_dir` im Modell nicht vorhanden.
  Hinweis: `bash` ist der Generalschluessel — wer Datei/Web wirklich sperren
  will, muss auch `bash` abwaehlen.

### Chat-UI

### Tasks-Tab
- Die geplante Arbeit (Backend + Worker + `tasks.json` gab es laengst, nur ohne
  Oberflaeche) hat jetzt einen **Tab „Tasks"**: Auftrag = Nachricht an eine
  Instanz, einmalig oder wiederkehrend (`every Nm|Nh|Nd`, `daily HH:MM`,
  `hourly`). Liste mit Status/naechster Ausfuehrung/letztem Ergebnis, anlegen
  und loeschen. Eine gestoppte Instanz wird fuer den Lauf gestartet. `/api/tasks`
  ist admin-only (Gaeste geblockt).

- Farb-Emoji (📎 🖥️ 🔄 🤖) gegen Inline-SVGs getauscht. Ohne installierte
  Emoji-Schrift erschienen sie als graue Kaestchen (Tofu) — jetzt rendern die
  Icons ueberall. (Gemeldet: graues Kaestchen neben dem Sende-Knopf = 📎.)

### Diagnose
- „gemma kann nicht im Internet suchen" war **kein** Netz-Problem (Egress 200):
  das freie gemma-Modell ruft `web_search` schlicht nicht auf, sondern erzaehlt
  vom Suchen und leakt `<|channel>`-Tokens. Ein staerkeres tool-faehiges Modell
  waehlen (Tab Models) oder das Tool im Prompt erzwingen.


## 2026-08-09 (Security-Scan Agents + katfs)

### katfs — Cross-Tenant-Loch geschlossen (kritisch)
- Der Knoten band auf `0.0.0.0:8790` und **pruefte den Aufrufer nie**. Jede
  microVM konnte `/shares` enumerieren und mit `?share=<beliebig>` jede aktive
  Freigabe lesen/schreiben/loeschen — auch die eines anderen Operators. Belegt
  im Test: Instanz `remote` las `id_rsa` aus einer fremden Freigabe und schrieb
  eine Backdoor hinein.
- Fix: Knoten bindet nur noch **127.0.0.1**. Gaeste gehen ueber den
  Manager-Broker (`/api/katfs/ls|read|write|delete`), der die Instanz per
  Source-IP erkennt und die Freigabe aus **deren** Config erzwingt. `&share=`
  vom Gast wird ignoriert. Write auf 64 MiB gedeckelt. Der Agent ruft nicht mehr
  den Knoten, sondern den Broker.
- Verifiziert aus der Angreiferposition (kompromittierter Agent mit Shell):
  Direktzugriff auf den Knoten → connection refused; fremde Freigabe via
  `&share=` → unerreichbar; eigene Freigabe → funktioniert.

### Netz — Gast-Isolation
- microVMs konnten untereinander routen; ein Agent erreichte die ungeschuetzten
  Ports 8080/7682 einer anderen Instanz. Fix: Tap-ACCEPTs auf Nicht-Pool-Ziele
  beschraenkt + `pool->pool`-DROP. Test: `remote` → `gemma:8080` laeuft in
  Timeout (geblockt), Internet-Egress unveraendert (200).

### Manager — weitere Gast-Lecks zu
- `/api/chats` und `/api/tasks` in die Gast-Wache aufgenommen.
- `/api/memory/<instanz>`: der Name kommt fuer Gaeste aus der Source-IP, nicht
  aus dem Pfad — keine fremden Gedaechtnisse mehr.


Aenderungen am Manager und am Umfeld (katfs, openrouter-Agent). Neueste zuerst.

## 2026-08-09

### Credentials
- **API-Keys aus den Instanzen entfernt.** `SECRET_PARAMS` wird beim Anlegen und
  beim Bau der Config-Disk verworfen; der Agent holt `OPENROUTER_API_KEY` zur
  Laufzeit ueber `/api/secret/<name>`. Der Manager erkennt den Gast an der
  Source-IP und prueft die Allowlist aus `secret-policy.json`. Altbestand wird
  beim Start bereinigt.
- **`/api/settings` abgedichtet.** Die Route gab die Keys im Klartext an jede
  microVM heraus. Jetzt Gast-Wache auf `/api/settings` und `/api/instances`,
  und gesetzte Geheimnisse gehen nur noch als `__unchanged__` heraus.
- **MCP-Tokens aus den Instanzen entfernt.** Statt `MCP_CONFIG` mit eingesetzten
  Werten wird `MCP_SERVERS=<namen>` gespeichert; der Agent holt die
  Konfiguration ueber `/api/mcp-config`, der Manager setzt nur die per Policy
  dieser Instanz freigegebenen Secrets ein. Migration hebt die Servernamen aus
  dem alten Blob und traegt die noetigen Secrets pro Instanz ein.
- `settings.json` auf 0600.

### katfs
- **Mehrere Freigaben gleichzeitig.** Der Knoten hielt bisher genau eine; jede
  neue verdraengte die alte, zwei teilende Rechner flappten gegeneinander. Jede
  Freigabe meldet jetzt beim `hello` eine stabile **share-id** plus Ordnername,
  Plattform und `readonly`. `GET /shares` listet sie, `?share=<id>` waehlt aus.
- **Auswahl beim Anlegen.** Im Anlege-Formular steht die Freigabe als Dropdown,
  der Wert landet als `KATFS_SHARE` in der Instanz-Config.
- **`katfs-share`** — der Provider als natives Programm (`iroh-fs/client/`).
  Loest das Firefox/Safari-Problem: dort gibt es keine API, die in einen echten
  Nutzerordner schreibt. Stabile share-id aus Hostname + Pfad, Auto-Reconnect,
  `--ro`.
- **`delete`** als fuenfte Operation, mit drei Sperren: Wurzel der Freigabe,
  `..`, und nicht-leere Verzeichnisse ohne `recursive=1`.
- Freigabe-Seite unter **`/katfs/`** durchgereicht — gleiche Herkunft, gleiche
  Auth und damit HTTPS, das die File System Access API zwingend braucht.
  `?key=<node-id>` setzt einen fremden Knoten ein.
- Fehlermeldungen tragen jetzt ihre Ursache (`Directory not empty` statt nur
  `delete <pfad>`), auch bis in den Agenten.

### Manager-UI
- **Neu aufgesetzt** auf das Design-System *Industry*: Barlow, Blueprint-Rahmen,
  Tabs statt einer langen Seite, hash-geroutet.
- **Ordner-Browser** hinter dem 📁 in jeder Mount-Zeile (`/api/browse`, nur
  Verzeichnisnamen, admin-only). Der `prompt()` fuer bestehende Instanzen ist
  einem richtigen Dialog gewichen.
- **Tab „Models"** — voller OpenRouter-Katalog live, daraus die Shortlist fuers
  Anlege-Formular (`models.json`). Vorher eine Konstante im Quelltext.
- **Tab „Sharing"** — katfs-Status, Freigaben, Sharing key.
- `deepseek/deepseek-v4-flash-0731` in die Auswahl aufgenommen.

### Betrieb
- **Rootfs-Rebuild ist atomar.** Vorher `cp` in die Zieldatei — hielt eine VM
  sie offen, endete der naechste Boot im ext4-Checksum-Panic. Jetzt danebenlegen
  und per `mv` umhaengen, plus Warnung bei laufender Instanz.
- `KillMode=process` in der Unit, damit ein Manager-Neustart nicht alle
  laufenden microVMs mitreisst. **Noch nicht installiert** — siehe offene Punkte.
- `node/build.sh` und `client/build.sh` fuer katfs (Docker, kein lokales Rust).
- `build-openrouter-rootfs.sh` findet `mkfs.ext4` auch ohne root in der PATH.
