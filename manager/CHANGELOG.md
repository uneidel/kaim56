# Changelog

## 2026-08-19 (Installer END-TO-END verifiziert — QEMU-Rig mit nested KVM)
- Test-Rig: QEMU-VM (Debian 12 Cloud, seed.iso statt SMBIOS — der war der
    Haenger), nested KVM bestaetigt (/dev/kvm in der VM).
- **install.sh lief auf der frischen Maschine komplett durch**: Preflight,
    Layout, Firecracker-Download, rootfs-Build via Docker IN der VM,
    embed+mcp-hub-Container, systemd-Dienst mit generiertem Passwort.
- **Finaler Beweis: eine Firecracker-microVM bootete INNERHALB der Test-VM**
    (Overlay-Boot inkl. Upper, web-bridge antwortet 200). Einzige erwartete
    Abweichung: NFS-WARN (kein NFS im Rig) — degradiert korrekt weiter.
- Ein Installer-Bug gefunden+gefixt: Smoke-Test scheiterte an 401, weil er
    ohne das frisch generierte Passwort curlte — 401 zaehlt jetzt als „lebt".


## 2026-08-19 (Fix: „Denken" klappte beim Streamen immer wieder auf)
- paint() ersetzt beim Streamen die letzte Nachricht je Token komplett — und
    setzte das details-Element dabei stets wieder auf `open`. Jetzt wird der
    vom Nutzer gewaehlte Auf/Zu-Zustand vor dem Repaint gemerkt und danach
    wiederhergestellt; zugeklappt bleibt zugeklappt.


## 2026-08-19 (/steps + jobresearcher-Fix + Config-Route)
- **/steps [n]** — max. Tool-Schritte je Turn zur Laufzeit aendern (1-60,
    bis Neustart; dauerhaft via AGENT_MAX_STEPS). Grund: Recherche-Laeufe des
    jobresearcher endeten mit "(max. Tool-Schritte erreicht)" bei Standard-12.
- **Neue Admin-Route** POST /api/instances/<n>/config {key,value} — einzelnen
    Config-Wert setzen/loeschen (Secrets ausgeschlossen). Damit jobresearcher
    dauerhaft auf AGENT_MAX_STEPS=30; Duplikat-Task (daily 08:00) geloescht.
- /steps in Web-Slash-Hint und App-Picker (App-Eintrag kommt mit naechstem APK).


## 2026-08-19 (pi.dev-Ideen uebernommen: /model, Steering, Prompts, Plugins)
- **/model** — Modell (und Backend) mitten in der Session wechseln, ohne
    Neustart, Kontext bleibt: `/model orcarouter:anthropic/claude-sonnet-4.6`,
    `/model <id>` (nur Modell), `/model` (anzeigen). Wirkt bis zum Neustart.
    Live verifiziert inkl. Backend-Wechsel OrcaRouter->OpenRouter->zurueck.
- **Steering** — dem laufenden Agenten reinrufen: neue Nachricht wird zwischen
    zwei Tool-Schritten als `[Steuerung]`-User-Nachricht eingespeist statt zu
    warten. Web: Enter mit Text waehrend einer Antwort (Button ■ bricht weiter
    ab); App 5.8: Senden waehrend busy. Guest-Endpunkt `POST /api/steer`
    (queued=false wenn kein Turn laeuft -> normal senden).
- **Prompt-Templates** — wiederkehrende Auftraege als Slash-Kommando: im
    Personas-Tab pflegen, im Chat `/name [zusatz]`; Expansion passiert im
    Agenten -> funktioniert in Web, App UND Signal. Store prompts.json,
    `GET/POST /api/prompts`. Slash-Vorschlaege jetzt auch im Web-Chat.
- **Tool-Plugins** (pi.dev-Extension-Idee, uebersetzt) — eine .py-Datei je Tool
    in `firecracker/plugins/` (DESC/PARAMS/REQUIRED + run()); der Manager legt
    sie auf die Config-Disk, der Agent laedt sie beim Start (/config/plugins).
    Kollisionen mit eingebauten Tools werden abgewehrt. Beispiel `wuerfel.py`;
    live: „tools=36", Aufruf funktioniert. Neues Tool = Datei + Stop/Start.
- 48 Tests gruen (neu: model-switch, steering-queue, prompt-expansion+store,
    plugin-loader inkl. Kollisionsschutz).


## 2026-08-19 (pi/prime stillgelegt)
- Nutzungsanalyse (llm_usage, task_runs, Instanzen): pi und prime wurden **nie**
    verwendet; ihr Zweck (Multi-Provider) ist durch openrouter/orcarouter/llama
    besser abgedeckt, und die Bridges hatten keinen Zugriff auf das kAIm56-
    Oekosystem (Tools/Memory/Missionen). Entfernt: Templates, rootfs-Images
    (~7 GB), Docker-Images, Secret-Policy-Eintraege, OVERLAY_ROOTFS, Installer-
    Verweise, Repo-Verzeichnisse. Quellcode bleibt in der Git-History.


## 2026-08-19 (Installer: curl | sh)
- **install.sh** im kaim56-Repo: installiert die ganze Loesung auf einer
    frischen Maschine (Preflight inkl. KVM-Check, Laufzeit-Layout, Firecracker
    v1.16.1 von GitHub, Gast-Kernel via VMLINUX_URL, Rootfs+embed+mcp-hub-
    Builds, systemd-Unit mit generiertem Passwort, Offline-Tests als Smoke).
    Flags: --check/--files-only/--no-build/--with-voice/--with-agents; idempotent.
- Portabilitaet dafuer: GUEST_DNS, CLAUDE_CRED_SRC, AGENT_DIR (NFS), FC_DIR
    (Build-Skripte), Test-Pfade jetzt per Env; Folder-Picker-HOME dynamisch.
- Repo vervollstaendigt: embed/, mcp-hub/, tests/, run-tests.sh eingecheckt;
    kompletter Live-Stand gesynct.
- Verifiziert: --check gruen; --files-only baute ein frisches Ziel-Layout, aus
    dem manager.py importiert und **30 Offline-Tests gruen** laufen; das echte
    Firecracker-Release wurde von GitHub geladen. Offen fuer curl|sh: Repo auf
    GitHub pushen + vmlinux als Release-Asset (VMLINUX_URL).


## 2026-08-19 (Fix: Tool-Katalog-Drift + Drift-Wache)
- Die vier Missions-Tools und `offload_read` fehlten im Manager-Werkzeugkatalog
    (`AGENT_TOOLS_CATALOG`) — dadurch tauchten sie nicht im Create-Instance-
    Formular auf und eine Tool-Allowlist haette sie stumm blockiert. Ergaenzt.
- **Neuer Drift-Test** (`test_tool_catalog_matches_agent`): Agent-BUILTIN und
    Manager-Katalog muessen deckungsgleich sein — ein kuenftig vergessener
    Katalog-Eintrag (oder Geister-Eintrag ohne Tool) laesst die Suite rot werden.


## 2026-08-19 (Fix: Browser-Terminal brach nach ~10 s Leerlauf ab)
- Der WS-Tunnel des Terminals oeffnete den Guest-Socket mit
    `create_connection(timeout=10)` — das Connect-Timeout blieb als READ-
    Timeout auf dem Socket, nach 10 s Stille riss `recv()` den Tunnel ab
    („connection closed"). Fix: Timeout nach dem Connect auf None, dazu
    TCP-Keepalive auf beiden Seiten (halbtote Verbindungen sterben trotzdem).
    Verifiziert: 16 s idle am offenen WS ueberstanden.


## 2026-08-19 (Overlay fuer pi/prime/claude nachgezogen)
- Der Overlay-Boot-Block (fc_upper -> /mnt sync-Mount -> overlayfs ->
    pivot_root, mit ro-Basis-Fallback) ist jetzt auch in den Gast-inits von
    **pi, prime und claude**; alle drei Images neu gebacken und per
    Wegwerf-Testinstanz verifiziert (Bridge bootet, kein Overlay-WARN).
- `OVERLAY_ROOTFS` umfasst damit alle vier Images — **jede** Instanz bootet
    von der geteilten ro-Basis + eigenem Upper, und der Persist-Schalter
    („💾 persistent") steht ueberall zur Verfuegung. private_rootfs() bleibt
    nur noch als Fallback fuer unbekannte Images.
- Diskgewinn je laufender Instanz: pi ~3 GB, prime ~4 GB, claude ~3 GB
    Kopie entfallen (jetzt ~35 MB Upper).


## 2026-08-19 (Overlay-Rootfs + persistente Disk pro Instanz)
- **Overlay-Boot** fuer das openrouter-Rootfs: die Basis haengt READ-ONLY an
    allen Instanzen (Firecracker blockt Schreibzugriffe -> der Journal-Sharing-
    Bug vom 15.08. ist strukturell weg), dazu je Instanz ein kleines rw-Upper-
    Image; der Gast-init baut overlayfs + pivot_root. KEINE 2-GB-Kopie je Start
    mehr. Faellt der Overlay-Aufbau aus, bootet die VM degradiert auf der
    ro-Basis weiter (Fallback, nie Boot-Verweigerung).
- **Persistente Disk, pro Instanz waehlbar (Default aus):** Tag in der
    Instanz-Tabelle („frisch je Start" / „persistent"). An = die Schreibschicht
    (4 GB sparse, instances/<n>-upper.ext4) ueberlebt Stop/Start — apt/pip/npm-
    Installationen bleiben. Rechtsklick = Factory-Reset der Schicht.
    Routen: /api/instances/<n>/persist + /diskreset.
- **Gelernt & gefixt:** (1) ro-Wurzel braucht existierenden Mountpoint (/mnt
    statt mkdir /ov). (2) stop() ist ein Stromstecker (SIGTERM an Firecracker)
    — ohne sync-Mount verlor der Upper die letzten Schreibungen (0-Byte-Datei
    im debugfs-Beweis); Upper mountet jetzt mit -o sync.
- **E2E bewiesen:** Datei schreiben -> Stop/Start -> Inhalt „UEBERLEBT-
    NEUSTART" noch da. Kernel-Check vorab: alle Container-/Overlay-Features
    im Gast-Kernel aktiv (Docker/podman IN der VM damit moeglich; podman
    waere der naechste Schritt). orchestrator+hass laufen auf Overlay
    (tools=35, tencent/hy3). 42 Tests gruen. Andere Templates (claude/pi/
    prime/llama) unveraendert auf dem alten Kopier-Pfad.


## 2026-08-19 (Settings-Pane fuer TTS/STT)
- **Voice-Sektion im Settings-Tab**: Statuszeile (Dienst up? STT-Modell,
    verfuegbare TTS-Stimmen via neuem `GET /api/voice-health`) + zwei neue
    Einstellungen: **TTS-Stimme** (Auswahl) und **TTS-Tempo** (0.5–2.0).
- **Voice-Dienst erweitert**: `/tts` akzeptiert `voice` + `speed`
    (Piper `--length_scale`, geklemmt), `/health` listet die Stimmen.
    Zwei neue Stimmen eingebacken: `de-eva_k-x_low` (dt., weiblich) und
    `en-amy-medium` (engl.) neben `de-thorsten-medium`.
- **Injektion im Manager-Proxy**: App und Web schicken weiter nur `{"text"}` —
    der Manager mischt Stimme/Tempo aus den Settings in den `/api/tts`-Body
    (explizite Client-Werte gewinnen). Kein Client-Update noetig.
- Settings-UI kann jetzt Auswahl-Felder (options im SETTINGS_SCHEMA).
- STT (Parakeet v3) hat nichts sinnvoll Einstellbares (Sprache automatisch) —
    darum nur Status-Anzeige.


## 2026-08-19 (Missions eigener Tab/Screen + klickbare Notifications)
- **Missions separat**: eigener Tab „Missions" im Web-Manager (statt im
    Tasks-Tab; abgeschlossene direkt sichtbar) und eigener Screen „Missionen"
    in der App 5.7 (Drawer-Eintrag mit Flaggen-Icon).
- **Notifications fuehren zur Aktion**: Notification-Eintraege tragen jetzt
    ein `link`-Feld (missions | tasks | chat:<instanz>). Web: Klick im
    Glocken-Dropdown springt zum Tab bzw. oeffnet den Chat der Instanz
    (Pfeil-Indikator). App: Tipp auf die System-Notification oeffnet die App
    direkt am Ziel (PendingIntent + notifLink-Extra, Muster wie Assist-Intent).
    Quellen: Agent-notify -> chat:<instanz>, Missions-Abschluss/TTL -> missions.


## 2026-08-19 (Missionen — Plan-/Fortschritts-Speicher fuer mehrstufige Auftraege)
- **Missionen**: Der Orchestrator legt fuer mehrstufige Auftraege selbst einen
    Plan an (`mission_start`: Ziel + Schritte), arbeitet ihn per `create_task`
    ab und haelt den Fortschritt im Manager (`missions.json`) — der Arbeitsstand
    ueberlebt /reset, VM-Neustart und den zustandslosen Heartbeat (Injektion als
    `[Missionen]`-Block je Turn).
- **Sofort-Trigger**: Ist ein Task fertig, auf den ein Missionsschritt wartet
    (task_id am Schritt), stoesst der Task-Worker den Orchestrator direkt zum
    naechsten Vorstoss an (`_mission_advance_fire`) — kein Warten auf den
    Heartbeat; der prueft nur noch als Fallback haengende Schritte.
- **Leitplanken**: max. 5 aktive Missionen / 20 Schritte / Log gekappt;
    TTL-Sweep pausiert 7 Tage inaktive Missionen + Notification; Abschluss ->
    Fazit ins semantische Gedaechtnis + Push (`notify`).
- **Tools** (nur Orchestrator, TASK_ADMIN): `mission_start/missions/
    mission_update/mission_finish`. Routen `/api/missions`, `/api/mission-*`
    (Gast source-IP-gated), `/api/mission-admin` (UI: Pause/Weiter/Abbrechen).
- **UI**: Missions-Panel im Tasks-Tab (Web: Fortschrittsbalken, aktueller
    Schritt, Log, Pause/Abbruch) und in der **App 5.6** (Tasks-Screen:
    Missions-Karten, aufklappbar mit allen Schritten + Aktionen).
- **Live verifiziert**: 2-Schritt-Mission lief in ~40 s vollautonom durch —
    Task fertig -> Trigger -> Schritt done -> naechster Task -> Trigger ->
    mission_finish + Abschluss-Notification. 39 Tests gruen (Lifecycle, Caps,
    mission_for_task, HTTP).


## 2026-08-19 (Chat-UI im Industry-Design verschoenert)
- Chat-Oberflaeche (/chat) sauber auf das Design-System **Industry**
    (claude.ai/design, Projekt-styles.css als Quelle) gezogen:
  - **Blueprint-Objekte**: Composer und Welcome-Panel mit Haarlinie +
    Registrierungs-Ecken (Ecken faerben sich beim Fokus akzentblau).
  - **Typografie**: Barlow Condensed fuer Headline, Buttons, Chips,
    Agent-Select; Kicker-Labels (MICROVM AGENT, CHATS) in Uppercase.
  - **Token-Rampen** statt Ad-hoc-Werte (accent-100…800, color-mix-Divider);
    Dark Mode aus denselben Rampen abgeleitet.
  - **Feinschliff**: Agent-Chips als tag-accent, aktiver Chat mit Akzent-
    Inset-Balken, Bubbles mit Haarlinie + Elevation, running-Dot mit Glow,
    gestylte Scrollbars, ::selection, Denken-Block als Uppercase-Summary.
  - Verifiziert per Headless-Chrome-Screenshots (hell/dunkel/Konversation).
    JS-Logik unveraendert; Backup chatui.py.bak-design-*.


## 2026-08-19 (Fix: "Save password?"-Popup beim Chat-Wechsel)
- Chrome bot beim Navigieren zu /chat an, ein "Passwort fuer kat56.de" zu
    speichern — mit der katfs node-id als vermeintlichem Nutzernamen und dem
    Maskierungs-Marker `__unchanged__` als Passwort. Ursache: die API-Key-Felder
    im Settings-Tab waren `type=password`; Chromes Passwort-Manager paart so ein
    Feld mit dem naechsten Textfeld (katfs-Key) zu einem "Login" und ignoriert
    `autocomplete=off`. Fix: Maskierung per CSS (`-webkit-text-security:disc`,
    Klasse `seckey`) statt Passwort-Semantik — kein Popup mehr, Werte bleiben
    verdeckt; echte Keys verlassen den Manager ohnehin nie (Marker statt Wert).


## 2026-08-19 (Fix: Dropdown-Reset im New-Instance-Formular)
- Gemeldet als "Template-Dropdown springt beim Transport-Wechsel zurueck".
    Instrumentierter Headless-Chrome-Test (CDP, Setter-Falle auf #tpl, 35 s mit
    allen Intervallen): das Template-Select selbst wird nie zurueckgesetzt.
    Gefunden wurde stattdessen ein **Async-Race in loadModels**: der Fetch der
    OpenRouter-Modellliste (Upstream, Sekunden bei kaltem Cache) merkte sich den
    Wert vom Fetch-START und baute das Modell-Dropdown beim Eintreffen damit neu
    — eine inzwischen getroffene Auswahl sprang sichtbar auf den Anfangswert
    zurueck (zeitlich zufaellig mit dem Transport-Klick). Fix: Wert beim RESOLVE
    lesen, abgehaengte Selects nicht mehr anfassen.
- **Cache-Control: no-store** fuer die Manager-Seite: veraltete Seiten nach
    Updates erzeugten Geister-Fehler (alte JS-Logik gegen neue API).


## 2026-08-19 (Fix: Modell-Dialog fuer orcarouter/llama)
- Klick auf das Modell einer orcarouter-Instanz meldete "This instance has no
    model setting": die Key-Liste im Modell-Dialog (editModel) kannte nur
    OPENROUTER/ANTHROPIC/PI/PRIME. `ORCAROUTER_MODEL` und `LLAMA_MODEL`
    ergaenzt — der Dialog oeffnet jetzt mit dem aktuellen Modell als Freitext.


## 2026-08-19 (Activity-Panel: Zeitfilter + Verbrauch)
- **Zeitfilter** im Activity-Dialog (1h / 24h / 7d / Alle) — filtert die
    Audit-Aktionen clientseitig nach `ts` (Fetch-Limit 1000).
- **Verbrauchs-Summe fuer den Zeitraum** in derselben Kopfzeile: Aktionen,
    LLM-Aufrufe, Tokens (rein->raus) und Kosten aus `llm_usage` via neuem
    `GET /api/usage/<name>?since=` + Helper `usage_for`. Bewusst als SUMME,
    nicht pro Audit-Zeile: Tokens fallen pro LLM-Turn an, nicht pro Tool-Aufruf
    (ein Turn loest 0..N Tool-Aufrufe aus) — eine Zuordnung waere erfunden.


## 2026-08-19 (App 5.5 — VAD-Fix Sprachaufnahme)
- **Aufnahme brach mitten im Reden nach ~4 s ab.** Ursache: das Grundrauschen
    wurde als MAXIMUM der ersten 400 ms gemessen — redete man sofort los, floss
    Sprache in den "Floor" und die Schwelle `floor*3` wurde unerreichbar, jede
    weitere Messung galt als Stille. Fixes: Floor als MINIMUM ueber ~0,6 s
    (gedeckelt), absolute Marge statt Faktor, und **Hysterese** — sobald geredet
    wird, haelt eine viel niedrigere Schwelle die Aufnahme am Leben, sodass die
    Amplituden-Taeler zwischen Woertern nicht als Stille zaehlen. `VAD_HANG`
    1800->2200 ms. Max. Aufnahmedauer unveraendert `VAD_MAX = 120 s` (Notbremse).


## 2026-08-19 (Notifications)
- **Notification-Kanal App + Web + Tool.** Neuer Push-Kanal neben Signal:
  - **Agent-Tool `notify(title, message)`** -> `POST /api/notify` (Gast, per
    Source-IP; rate-limit 30/5min, Audit). Im Werkzeugkatalog.
  - **Manager-Store** `notifications.json` (rev + Long-Poll wie chats, Cap 200):
    `GET /api/notifications?since=&wait=` (Admin, mit `unread`),
    `POST /api/notifications/read` (`{id}`|`{all:true}`).
  - **Web-Manager:** Glocke in der Topbar mit Unread-Badge + Dropdown, Long-Poll,
    optional Browser-Notification (Permission on Klick); Oeffnen quittiert.
  - **App (5.4):** eigener Poll-Loop -> Android-Systemnotification (Channel
    `kaim56_agent`, nutzt die bestehende POST_NOTIFICATIONS-Permission) fuer neue,
    ungelesene Eintraege ab App-Start. `ManagerSync.pollNotifications/markNotifRead`.
  - E2E verifiziert: Orchestrator ruft `notify` -> landet im Store (`tools=31`).


## 2026-08-18 (Playbooks, Reasoning-Anzeige, Task-Verwaltung, Signal-Empfang, katfs)
- **Harness-Muster uebernommen** (angelehnt an strands-agents/harness-sdk,
  Apache-2.0; portiert, keine neue Dependency — der Agent bleibt stdlib-only):
  - **Summarizing Context:** `_trim_history` wirft alte Nachrichten nicht mehr
    weg, sondern fasst die aeltesten zu einem `[Zusammenfassung]`-Block zusammen
    (System gepinnt, letzte `CTX_PRESERVE_RECENT`=10 woertlich, bestehende
    Zusammenfassung wird eingefaltet). Gegen Token-Runaway UND Kontextverlust.
  - **Context-Offloader:** Tool-Ausgaben > `OFFLOAD_MIN` werden komplett in
    `.offload/` ausgelagert; im Kontext bleibt Vorschau + Referenz, der Rest ist
    per neuem Tool `offload_read(id, offset)` nachladbar. Statt hartem Cut.
  - **Goal-Loop:** `/goal <Kriterium>` setzt ein Ziel; ein Judge prueft die
    Antwort und laesst bis `GOAL_MAX_ATTEMPTS`=3 nachbessern. `/goal off` aus.
  - **Tool-Hook + HITL:** harte Denylist (rm -rf /, Forkbomb, mkfs …) immer aktiv;
    optionale Freigabe riskanter Tools per Signal (opt-in `HITL=1`,
    `HITL_TOOLS`) — Manager fragt „ok <id>/nein <id>", Agent pollt
    `/api/hitl`/`/api/hitl/<id>`. Kein Signal-Empfaenger -> blockiert nicht.
  - **Aktiviert:** rootfs neu gebacken, orchestrator + hass neu gestartet
    (`tools=30`, offload_read live). **App 5.2:** `/goal` im Slash-Picker.
  - **Retry mit Backoff** um jeden Modellaufruf (429/5xx, 0.5→8s), leere
    tools-Liste wird nicht mehr mitgeschickt (verhinderte 400 beim Summarizer/Judge).
- **E2E-Testsuite** (`tests/e2e.py`, `./run-tests.sh`) — stdlib-unittest, keine
  Dependency. Drei Stufen: OFFLINE (Agent-/Manager-Funktionen per Import —
  Backend-Wahl, Summarizing, Offloader, Hook-Denylist, Goal, leere-Tools-Fix,
  Provider-Switch inkl. ":free"-Fallstrick, HITL-Store, katfs-ZIP-Walk),
  HTTP (gegen den laufenden Manager: /api/agents backend+model, /api/hitl,
  katfs-status, Template-Registrierung), LIVE (kostenloser /goal-Roundtrip
  zur Orchestrator-VM). Fehlende Stufen werden sauber uebersprungen. 30 Tests.
  Laeuft ab jetzt bei jeder Aenderung mit (wie Changelog/Architecture).
- **Fix Modell-Selbstauskunft:** `/api/agents` kannte nur OPENROUTER/PI/PRIME
  als Modell-Key und meldete das *Template* als Provider — ein orcarouter-
  Agent erschien so als "openrouter, ohne Modell". Jetzt: alle MODEL_KEYS +
  echtes `backend`-Feld. Zusaetzlich kennt der Agent sein eigenes Backend/
  Modell aus dem SYSTEM-Prompt (nennt es direkt statt via list_agents).
- **Provider-Wechsel per set_model** ("orcarouter:tencent/hy3" stellt Backend UND
  Modell um, entfernt die anderen MODEL_KEYS). **Orchestrator produktiv auf
  OrcaRouter `tencent/hy3` umgestellt** (backend=orcarouter verifiziert, Tool-
  Calling + Key-Broker ok).
- **OrcaRouter als zweites LLM-Backend neben OpenRouter.** OpenAI-kompatibles
  Gateway (`https://api.orcarouter.ai/v1`, Key-Format `sk-orca-…`; oder
  selbstgehostet via OrcaRouter-Lite). Derselbe Agent-Code wie OpenRouter —
  gewaehlt wird per Env: ist `ORCAROUTER_MODEL` (bzw. `ORCAROUTER_URL`)
  gesetzt, spricht der Agent OrcaRouter an, sonst OpenRouter. Neu: Template
  `orcarouter.json` (gleiches rootfs), Settings-Felder `ORCAROUTER_API_KEY`
  (Secret, 0600, ueber den Broker) + `ORCAROUTER_URL`, `ORCAROUTER_MODEL` in
  MODEL_KEYS, Secret-Policy-Eintrag. **Key setzen: Settings-Tab.**
- **Playbooks — feste Regeln, die der Orchestrator selbst lernt.** Anders als
  das semantische Gedaechtnis (bedeutungsbasiert eingeblendet) gelten Playbooks
  IMMER. Sagt der Nutzer WIE etwas zu tun ist / korrigiert den Ansatz, legt der
  Agent das per `playbook_add` ab; alle Regeln werden jeden Turn in den Prompt
  gehoben. Werkzeuge `playbook_add`/`playbooks`/`playbook_forget`, Speicher
  `playbooks.json` (je Instanz, Cap 40), Gast-Routen. Belegt: Regel „Aktienkurse
  per http_fetch von Yahoo" → /reset → vage Frage → korrekt beantwortet, ohne
  die Quelle erneut zu nennen.
- **Reasoning steuerbar + sichtbar.** Slash `/reasoning [low|medium|high|off]`
  schaltet den OpenRouter-reasoning-Parameter live (Default aus, per Env
  `OPENROUTER_REASONING` als Dauer-Default). Das Denken wird getrennt gestreamt
  (Marker im Token-Strom, aus dem Kontext rausgehalten) und in Web und App als
  aufklappbarer „Denken"-Block gezeigt. Kopieren/Vorlesen nehmen nur die Antwort.
- **Task-Verwaltung fuer den Orchestrator.** `list_tasks`/`delete_task`/
  `edit_task` (auflisten mit IDs, loeschen, Nachricht/Zeitplan aendern). Nur der
  Orchestrator bekommt sie — ueber ein Flag `TASK_ADMIN`, das der Manager gezielt
  injiziert; gated im Menue UND an der Route (403 „orchestrator only").
- **Signal: Empfang + json-rpc.** Gateway von `native` auf `json-rpc` umgestellt
  (Empfang und Versand koennen jetzt gleichzeitig — im native-Modus sperrte ein
  Long-Poll das Konto und blockierte den Versand). Empfaenger im Manager auf
  einen stdlib-WebSocket-Client umgebaut (Echtzeit-Push). Neue Signal-Nachricht
  → Orchestrator sofort getriggert, Antwort per send_signal zurueck.
- **katfs Datei-Browser** im Sharing-Tab (Ordner durchklicken, ansehen,
  herunterladen; read-only, Admin-Routen). Jetzt mit **Freigabe-Auswahl**: bei
  mehreren aktiven Browser-Freigaben eine im Status-Panel anklicken → der Baum
  darunter wechselt auf genau diese Freigabe (adressiert per `share`-Id). Neu
  ausserdem **„Download all"** — der aktuelle Ordner wird rekursiv eingesammelt
  und als ZIP geliefert (Route `/api/katfs/zip`, Helper `katfs_zip`, Deckel
  2000 Dateien / 512 MB). Der eigenstaendige **CLI-Share**
  (`dist/katfs-share`) existierte bereits — teilt einen Ordner ohne Browser.
  Knoten-Pfade/Bind per Env konfigurierbar; portables Standalone-Buendel unter
  `katfs-standalone/` vorbereitet.
- **App 4.9–5.1:** Text in Blasen markier-/kopierbar (SelectionContainer),
  Slash-Befehl-Auswahl ueber der Eingabezeile (tippt man „/"), Denken-Anzeige.
- **MSFT-Task repariert:** die taegliche Aufgabe scheiterte, weil das Modell
  `http_fetch` nicht nutzte und faelschlich „kann keine Seiten lesen" sagte.
  Jetzt konkrete Anweisung mit fester Yahoo-URL + Feld — liefert einen echten
  Kurs.

## 2026-08-17 (Orchestrator-Token-Runaway behoben)
- **Ursache:** Der Heartbeat lief `every 1m` (1.440 Laeufe/Tag), und der
  Orchestrator setzte seinen Gespraechskontext nie zurueck — Heartbeat und
  App-Chats teilen sich EIN `_history`, das monoton wuchs. Jeder Call schickte
  das ganze angesammelte Transkript erneut: im Schnitt 73k Input-Token, groesster
  Call 183k, bei ~5 Token Output. Summe: 249 Mio Token, ~14 USD.
- **Fix 1 — Takt:** Heartbeat von `every 1m` auf `every 30m` (Faktor 30).
- **Fix 2 — zustandslos:** Heartbeats laufen jetzt mit `/fresh` in einem
  WEGWERF-Kontext (`_tool_loop` auf einer lokalen Liste), das Gespraechs-
  `_history` bleibt unangetastet. Ein harter Reset schied aus, weil er einen
  laufenden App-Chat weggewischt haette (geteiltes `_history`). Beides
  umgestellt: der geplante Task UND der Sofort-Trigger (`ORCH_HEARTBEAT_MSG`).
- **Fix 3 — Deckel:** allgemeiner gleitender Kontext-Deckel (`CTX_MAX_MSGS=20`,
  nur an sauberen Turn-Grenzen geschnitten, nie mitten im Tool-Zyklus) — bindet
  die Kosten auch fuer lange direkte Chats.
- Belegt: 42+8=50 → /fresh-Heartbeat ("nichts zu tun") → "voriges Ergebnis mal
  zwei" = 100 (Kontext ueberlebt den Heartbeat, der laeuft trotzdem zustandslos).
- Gedaechtnis-Anweisung entschaerft: sie stellte den Orchestrator zu meta
  ("mein Gedaechtnis wird nach jeder Interaktion zurueckgesetzt" — irrefuehrend,
  die Kurzzeit funktioniert). Jetzt: innerhalb eines Gespraechs normal erinnern
  und nicht ungefragt die Gedaechtnis-Mechanik erklaeren; still mit
  memory_store nur das Dauerhafte ablegen. Belegt: "merk dir Testwort Alpha" →
  bestaetigt ohne Vortrag; "wie war mein Testwort?" → "Alpha".

## 2026-08-16 (Semantisches Gedaechtnis: Kurzzeit + Langzeit)
- **Kurzzeit** bleibt das laufende Gespraech (`_history` im VM-RAM, `/reset`
  leert es). **Langzeit** ist jetzt **semantisch** statt flaches key/value:
  `memory_store` bettet jede Notiz ein (Modell multilingual-e5 auf der CPU,
  neuer `embed`-Container hinter dem Manager auf :8772) und legt Text+Vektor in
  `history.db` ab. Bei jeder Frage bettet der Agent die Nutzernachricht ein,
  der Manager liefert die **bedeutungsnaechsten** Notizen (Cosinus), die als
  frischer `[Gedaechtnis]`-Block in den Prompt kommen — nur was zur Frage
  passt, nicht der ganze Speicher.
- **Kein LLM, keine Graph-DB noetig** — Embeddings laufen gut auf dem i5, also
  ohne Warten auf die llama-Box und ohne Neo4j & Co. (Graphiti/Cognee bleiben
  ein moeglicher spaeterer Ausbau.) Faellt der Embedder aus, gibt es diesen
  Turn keinen Langzeit-Kontext statt eines Fehlers; die Notizen bleiben
  gespeichert.
- Manager: Tabelle `semantic_memory`, `sem_store`/`sem_search`, Gast-Route
  `POST /api/memory-search`; `memory_store` schreibt zusaetzlich semantisch.
  Agent: Injektion pro Turn statt Voll-Dump beim ersten Turn; Anweisung, den
  value als vollstaendigen Satz zu merken (sonst taugt der abgerufene Fetzen
  nichts — "… ist der Watzmann", nicht bloss "Watzmann").
- Ende-zu-Ende belegt: Fakt merken → /reset (Kurzzeit weg) → mit anderen Worten
  fragen → korrekt erinnert (Score 0.856, rein aus dem Langzeitspeicher).

## 2026-08-16 (Slash-Befehle durchreichen — App 4.8 + claude-Bridge)
- Die App fing bisher JEDEN /-Befehl ab und wies Unbekanntes als "Unbekannter
  Befehl" zurueck — die eigenen Befehle der Agenten waren so unerreichbar.
  Jetzt behaelt die App nur ihre eigenen (`/task`, `/agents`, `/help`), faengt
  `/login` mit einem Hinweis ab (nicht noetig, der Agent ist ueber den Host
  angemeldet) und **reicht alles andere an den Agenten durch**.
- Damit greift `/reset` (neuer Kontext ohne VM-Neustart) sofort fuer die
  OpenRouter/llama-Agenten (koennen es laengst) — und neu auch fuer das
  claude-Template: `/reset` in die Web-Bridge nachgeruestet (leert die
  Claude-Code-Sitzung). Belegt: Codewort setzen → /reset → Agent kennt es
  nicht mehr; claudy antwortet "Neue Unterhaltung", orchestrator "Kontext
  zurueckgesetzt".
- `/help` der App nennt jetzt, dass andere /-Befehle an den Agenten gehen.

## 2026-08-16 (Verhaltensleitplanken aus Anthropics System-Prompts)
- Sinngemaess das Modell-agnostische aus Anthropics veroeffentlichten
  System-Prompts in den Basis-Prompt der OpenRouter/llama-Agenten uebernommen
  (`agent.py`, gilt fuer jedes Modell und auch fuer Personas):
  - **Nicht halluzinieren:** bei Unsicherheit offen sagen und mit web_search/
    http_fetch pruefen statt raten; keine erfundenen Quellen/Zitate/Links.
  - **Erst Werkzeuge, dann "geht nicht":** bevor der Agent Unvermoegen oder
    fehlenden Zugriff behauptet, prueft er, ob ein Tool dafuer da ist — selbst
    handeln vor Nachfragen.
  - **Unklare Anfragen:** sinnvolle Annahme treffen und loslegen, nur bei
    echtem Blocker zurueckfragen; begonnene Aufgaben zu Ende fuehren.
  - **Ton:** sachlich, keine Schmeichelei/uebertriebenen Entschuldigungen,
    freundlich begruendeter Widerspruch statt Nachgeben, keine leeren
    Fuellwoerter ("ehrlich gesagt", "tatsaechlich").
  - **Form:** knapp und in Fliesstext; Listen/Fettung/Ueberschriften nur bei
    echtem Bedarf oder auf Wunsch; keine Spekulation ueber fremde Absichten.
- Nicht angefasst: die Coding-Agenten claude/pi/prime — die haben eigene, gut
  abgestimmte Prompts. Belegt an einer Halluzinationsprobe (fiktive Praemisse
  wird benannt, nicht bestaetigt).

## 2026-08-16 (Selbst gehostetes LLM via llama.cpp)
- **Settings-Tab:** neue Felder `LLAMA_ENDPOINT` (OpenAI-kompatible Basis-URL,
  z. B. `http://10.0.0.50:8080/v1`) und `LLAMA_API_KEY` (optional, nur wenn
  der Server mit `--api-key` laeuft). Der Key ist ein Secret (maskiert, ueber
  den Broker, nie in die Instanz-Config).
- **Neues Template `llama`** (nutzt das vorhandene openrouter-Rootfs — llama.cpp
  ist OpenAI-kompatibel, gleicher Agent-Code). Der Endpoint wird aus den
  Settings vorbefuellt, der Modellname ist ein Freitextfeld (was der Server
  bedient). Modell-Chip und Modellwechsel funktionieren (`LLAMA_MODEL` in
  MODEL_KEYS).
- **Agent:** ist `LLAMA_ENDPOINT` gesetzt, spricht er den lokalen Server statt
  OpenRouter an — gleiche Tool-Schleife, andere Basis-URL/Modell/Key.
  URL-Normalisierung akzeptiert `host:8080`, `…/v1` und `…/v1/chat/completions`.
  Fehlt der Key, laeuft er ohne Auth (fuer llama.cpp ohne `--api-key` der
  Normalfall, kein Fehler). Fehlermeldungen und Startlog nennen jetzt das
  aktive Backend.
- **LAN-Gating:** liegt der llama-Server auf einer privaten LAN-IP, gibt der
  Manager genau dieses Ziel in der FORWARD-Kette der Instanz frei — wie die
  MCP-Endpunkte. Ende-zu-Ende gegen einen OpenAI-kompatiblen Stub belegt
  (Settings → Vorbefuellung → Agent → Backend → SSE-Streaming, App- und
  Web-Pfad).

## 2026-08-15 (claudy: Abo-Anmeldung + rohes JSON im Chat behoben)
- **"Not logged in":** Das claude-Template laesst intern Claude Code laufen und
  hatte keine Anmeldung. Neue Gast-Route `GET /api/claude-credentials` (nur
  claude-Template, per Source-IP): der Gast holt beim Boot den **lebenden**
  `claudeAiOauth`-Block vom Host — folgt damit automatisch dem naechsten
  `/login` des Nutzers; der kurzlebige accessToken wird pro Sitzung von Claude
  Code selbst erneuert (Refresh-Token 20 Tage stabil). Nur der Abo-Block wird
  ausgeliefert, nicht die mcpOAuth-Tokens des Nutzers; die Host-Datei bleibt
  unangetastet. Fuer Nicht-Gaeste 403.
- **Rohes `{"reply": …}` im App-Chat:** Die claude-Bridge kann kein Streaming
  und antwortet mit JSON — die App zeigte das nackt samt `\u00b7`. Der
  Manager-Proxy packt jetzt bei JSON-Antworten auf `api/chat[/stream]` das
  `reply` aus und reicht es als text/plain weiter (wie es der Web-Chat laengst
  tat). Belegt: App-Pfad und Web-Pfad liefern sauberen Text, claudy antwortet
  angemeldet ("Ja, ich bin angemeldet und einsatzbereit").

## 2026-08-15 (MCP-Hub am Host + LAN-Gating fuer die Gaeste)
- **Fund vorab:** mit `internet=on` durfte jede VM ins ganze LAN — Home
  Assistant und Portainer waren von jedem Agenten erreichbar, ob ihm der MCP
  zugewiesen war oder nicht. Jetzt je Instanz eine eigene FORWARD-Kette:
  MCP-Endpunkte der zugewiesenen Server (aus dem Katalog) → ACCEPT, Gast-DNS
  (10.0.0.245:53) → ACCEPT, private Netze → **REJECT** (sofortiges
  Scheitern statt 30-s-Timeout), Internet → ACCEPT. In vier Richtungen
  belegt (orchestrator↛HA, orchestrator→Internet, hass→HA, hass↛Portainer).
- **MCP-Hub** (`mcp-hub/`, Docker, 127.0.0.1:8771): die MCP-Serverprozesse
  laufen jetzt EINMAL je (Instanz, Server) am Host statt in jeder VM. Gaeste
  sprechen nur noch JSON-RPC ueber `POST /api/mcp`; der Manager autorisiert
  per Quell-IP gegen `MCP_SERVERS`, setzt die Secrets host-seitig ein und
  reicht an den Hub durch. **Tokens und LAN erreichen die VM nicht mehr**;
  `/api/mcp-config` liefert Secrets nur noch als Platzhalter aus. Jeder
  `tools/call` steht im Audit (Gast- und Manager-Seite). Der Hub startet tote
  Prozesse neu und wiederholt deren Initialisierung; beim Instanz-Stop werden
  ihre Prozesse beendet.
- Gast-Image: `mcp-portainer` entfernt (−104 MB); `mcp-remote` bleibt als
  Rueckfall fuer Manager ohne `/api/mcp`. Ende-zu-Ende belegt: hass fragt
  ueber den Hub den Live-Zustand ab ("Ein Licht ist gerade eingeschaltet"),
  orchestrator bekommt fuer nicht zugewiesene Server eine Ablehnung.

## 2026-08-15 (Architektur-Tab + eine SVG-Parserfalle)
- Neuer Tab **Architecture** neben dem Changelog: Diagramm (SVG, folgt dem
  hellen/dunklen Thema) plus 15 Referenzkarten zu allen Komponenten samt
  Sicherheitsgrenzen.
- Zwei Fallen dabei, beide erst im echten Chromium sichtbar (jsdom verzeiht
  sie): ein `<style>`-Element **im** Inline-SVG beendet beim HTML-Parsen den
  SVG-Kontext — alles danach faellt unsichtbar heraus; und bei unzitierten
  Attributen frisst `height=56/>` den Schraegstrich mit in den Wert
  (`height="56/"` → Element malt nichts). Regeln ins Seiten-CSS, Attribute
  zitiert, per Headless-Screenshot verifiziert.

## 2026-08-15 (Gedaechtnis: Anweisung + Injektion; ein ext4-Vorfall mit Folgen)
- **Jeder Systemprompt** (auch Personas) bekommt in agent.py eine stehende
  Gedaechtnis-Anweisung angehaengt: Wichtiges sofort per `memory_store` merken,
  Bestehendes aktualisieren, kein Protokoll fuehren. Beim **ersten Turn nach
  einem Neustart** injiziert der Agent seine gemerkten Fakten selbst in den
  Prompt (nicht beim Boot — da steht das Gast-Netz u. U. noch nicht).
  Ende-zu-Ende belegt: merken → VM-Neustart → korrekt erinnert.
- **Vorfall beim Verifizieren:** `EXT4 error loading journal` beim Boot.
  Ursache: alle Instanzen eines Templates teilten sich **dieselbe**
  beschreibbare Rootfs-Datei — zwei laufende VMs, ein Journal. Behoben: jede VM
  bekommt beim Start eine **eigene frische Kopie** (sparse, ~550 MB, beim Stop
  geloescht); Master-Image mit e2fsck repariert. Nebeneffekt: ein Neustart
  bootet garantiert das aktuelle Template-Image.

## 2026-08-15 (Modellwechsel fuer bestehende Instanzen)
- Der Modell-Chip in der Instanzzeile ist jetzt ein Knopf: Dialog mit demselben
  Picker wie beim Anlegen (OpenRouter-Shortlist, ⟳, Freitext). Neue Route
  `POST /api/instances/<n>/model` + `set_model()`; wirksam nach Stop/Start.
  Fuer Gast-VMs automatisch gesperrt (nicht in der POST-Positivliste).

## 2026-08-15 (Signal-Versand fuer Agenten)
- Neues Werkzeug **send_signal**: Agenten koennen dem Nutzer schreiben
  (fertige Aufgabe, Fund, Rueckfrage). Versand laeuft im **Manager**
  (`/api/signal` → signal-cli REST); Bot-Nummer und API bleiben im Host.
- **Fessel:** Empfaenger muessen in `ALLOWED_SENDERS` stehen — ein Agent kann
  nur an Leute schreiben, die ihm ohnehin Befehle geben duerfen. Dazu Drossel
  (10 je 5 min, Ablehnungen zaehlen nicht) und Audit-Eintrag je Aufruf.
  `SIGNAL_API` neu in den Settings.

## 2026-08-15 (Security Gateway, je Chat zuschaltbar)
- Schild-Symbol in App und Web, Zustand am Manager (`gateway.json`), Filterung
  ebenfalls — eine Gast-VM kann ihn nicht abschalten. Entfernt in **beide**
  Richtungen unsichtbare Unicode-Zeichen (Tag-Zeichen U+E0020–E007F,
  Zero-Width, Bidi-Overrides, Homoglyph-Leerzeichen) und aus Uploads
  EXIF/XMP/C2PA (JPEG/PNG/WEBP, byte-chirurgisch, Bilddaten unangetastet).
- Der Strom-Filter schneidet an Wortgrenzen — Emoji-ZWJ-Ketten ueberleben,
  ein ueber Chunks verteilter Schmuggelbefehl nicht (Test: 67 Zeichen
  entfernt, Modell beantwortet die sichtbare Frage). Entferntes wird sichtbar
  gezaehlt. Grundlage: `text_unicode.py` aus watermarks-remover (MIT),
  **nur** die Unicode-Schicht — die Wasserzeichen-/C2PA-Entfernung des
  Projekts bleibt bewusst draussen (SECURITY-GATEWAY.md).
- Ohne `chat`-Feld im Request bleibt alles beim Alten — aeltere Clients laufen
  unveraendert.

## 2026-08-15 (KatAgent 4.3–4.7: freihaendige Sprache, Assistent, Kleinigkeiten)
- **4.3:** Sprechpausen-Erkennung — Aufnahme endet von selbst (Schwellwert aus
  dem Raumgeraeusch der ersten Zehntel, Stopp erst nachdem gesprochen wurde);
  Blase antippen stoppt die Ausgabe (waehrenddessen ist die ganze Blase die
  Stopptaste), neue Spracheingabe bricht sie ab (Generationszaehler gegen
  nachtraeglich eintreffende Synthese).
- **4.4:** Gateway-Schalter in der Kopfzeile, Zaehler in der Sync-Zeile.
- **4.5:** Versionsnummer neben dem Namen im Schubladenkopf (aus dem
  installierten Paket, nicht BuildConfig).
- **4.6:** App meldet sich als **digitaler Assistent** (ASSIST/VOICE_COMMAND,
  singleTask): langer Druck auf die Ein-Aus-Taste startet sofort die Aufnahme.
  Bewusst nicht ueber dem Sperrbildschirm. Zuweisung unter Einstellungen →
  Standard-Apps → Digitaler Assistent.
- **4.7:** Nachlaufzeit vor dem Senden 1,2 → 1,8 s.

## 2026-08-15 (Sprache Stufe 2: die App hoert und spricht — KatAgent 4.2)
- Mikrofonknopf in der Eingabezeile: aufnehmen (AAC/M4A, 16 kHz mono), zum
  Manager schicken, erkannten Text **freihaendig direkt abschicken**. Neue
  Berechtigung `RECORD_AUDIO` samt Laufzeitabfrage.
- Lautsprecher unter jeder Agentenantwort; **automatisch vorgelesen wird nur,
  was per Sprache gefragt wurde**.
- `ManagerSync.stt()` und `.tts()` sprechen die Manager-Routen von Stufe 1 an
  (Basic-Auth wie der uebrige Verkehr, 120 s Lesetimeout).
- Aufnahmen unter 2 KB werden verworfen ("zu kurz") statt eine leere Erkennung
  zu schicken.
- Zwei Kotlin-Fallen beim Bau: `send()` ist eine lokale Funktion und darf nicht
  vor ihrer Deklaration aufgerufen werden (jetzt ueber einen Merker, den ein
  LaunchedEffect abarbeitet), und `VolumeUp` liegt nicht unter `AutoMirrored`.

## 2026-08-15 (Sprache: Stufe 1 — Dienst, Manager-Routen, Mikrofon im Web)
- Neuer **Sprachdienst** (`voice/`): Parakeet TDT v3 (ONNX int8) fuer die
  Erkennung, Piper mit der Stimme Thorsten fuer die Ausgabe, dazu ffmpeg fuer
  die Formatwandlung. Ein Host-Container, Loopback, ~700 MB Modelle einmal im
  Speicher statt je microVM.
- Manager reicht als `/api/stt` und `/api/tts` durch — die einzige Tuer nach
  aussen. Beide stehen in der Gast-Positivliste, damit spaeter auch Agenten
  (z. B. die Signal-Bridge) transkribieren koennen.
- Weboberflaeche: Mikrofonknopf in der Eingabezeile (Aufnahme im Browser,
  Erkennung im Manager, wird freihaendig direkt abgeschickt) und ein
  "Vorlesen" je Antwort. **Vorgelesen wird nur, was per Sprache gefragt
  wurde** — sonst liest er ungefragt lange Erklaerungen vor.
- Gemessen ueber den Manager: sprechen 0,38 s, erkennen 0,31 s; im Rundlauf
  6,5 s Audio in 0,67 s erzeugt und in 0,61 s wieder erkannt.
- Falle dabei: im Container auf 127.0.0.1 zu binden macht den Dienst
  unerreichbar — Dockers Portweiterleitung kennt das Container-Loopback nicht.
  Die Beschraenkung gehoert auf die Host-Seite der Zuordnung.

## 2026-08-14 (Sicherheitsreview: VM konnte sich das Host-Dateisystem einhaengen)
- **Kritisch, behoben.** Die GET-Routen waren gegen Gaeste gesperrt, die
  schreibenden **nicht**: eine Agent-VM erreicht den Broker am Gateway (dort
  holt sie ihre Secrets) und konnte damit u. a.
  `POST /api/instances/<n>/mounts` aufrufen. Der Manager laeuft als root und
  exportiert den gewuenschten Ordner per NFS in den Gast — eine kompromittierte
  VM haette sich so `/` schreibbar einhaengen koennen. Ebenso offen:
  `/api/create` (neue Instanz mit beliebigen Mounts), `/api/settings`
  (ALLOWED_SENDERS/SIGNAL_NUMBER = Steuerkanal), `/api/tasks`, `/api/personas`,
  `/api/instances/<n>/{delete,internet,tools,start,stop}`. Secret-Allowlist,
  Tool-Gating und Egress-Regeln waren damit umgehbar.
- Fix: **Positivliste** ganz oben in `do_POST` — Gaeste duerfen nur `/api/usage`,
  `/api/audit`, `/api/task`, `/api/chat-log` und `/api/memory/…`, alles andere
  403. Bewusst als Allowlist statt Einzelpruefungen: eine neue Route ist dann
  standardmaessig zu, nicht standardmaessig offen.
- Verifiziert: Admin-Routen unveraendert 200, echte Gast-Meldung kommt weiter an
  (Verbrauchszaehler 45 -> 46 nach einem Prompt).
- **Stored XSS, behoben.** Im serverseitigen Rendering gab es kein einziges
  HTML-Escape. Modell-ID (seit dem freien Textfeld beliebig), Beschreibung,
  Mount-Pfade und Werkzeugliste landeten roh im Markup der Admin-Seite. Neues
  `h()` (html.escape) davor; mit praeparierter Instanz nachgewiesen: 0 rohe,
  4 entschaerfte Vorkommen.
- **Offen (Konfiguration, kein Code):** `MANAGER_PASS` ist leer, `_auth()` laesst
  dann jeden durch. Der Schutz haengt allein an Traefik — wer 10.0.0.240:8700
  direkt erreicht, ist Admin. Siehe Hinweis unten.

## 2026-08-14 (Veraltete Ansichten: Tasks, Policy und Verbrauch ziehen nach)
- **Befund aus der Praxis:** in der Tasks-Tabelle stand als letztes Ergebnis noch
  ein DNS-Fehler von 08:08, waehrend `tasks.json` laengst "Nichts zu tun." und
  einen erfolgreichen Lauf um 17:08 fuehrte. Die Tabelle laedt naemlich nur
  einmal beim Oeffnen der Seite — wer den Tab offenliess, sah beliebig alte
  Staende und hielt ein behobenes Problem fuer aktuell.
- Tasks und Policy ziehen jetzt alle 15 s nach, aber nur fuer den sichtbaren
  Tab und nicht im Hintergrund-Reiter (`document.hidden`).
- Die Verbrauchszeilen werden nicht mehr nur serverseitig gerendert: sie tragen
  ein `data-usage` und werden aus `/api/usage` aktualisiert, ebenso die Summe in
  der Fusszeile. Damit waechst der Zaehler beim Zuschauen mit.
- Nebenbei: die Zeitzone des Hosts stand auf `America/Chicago` (UTC und NTP
  waren korrekt, nur die Zone nicht) — 7 Stunden Versatz in `daily HH:MM`,
  in der Tagesgrenze des Verbrauchszaehlers und in allen Server-Zeitstempeln.
  Nach `timedatectl set-timezone Europe/Berlin` braucht der Manager einen
  Neustart, glibc liest die Zone nur einmal pro Prozess.

## 2026-08-14 (Rest der Oberflaechen gegen Industry geprueft)
- **Web-Chat (`/chat`) war das groesste Loch**: eigene Palette mit orangem
  Akzent (#e8590c), 14 px Rundung, system-ui. Jetzt Industry-Tokens unter den
  gleichen Variablennamen — Slate-Blau, Barlow/Barlow Condensed, eckig,
  Haarlinien. Eigene Nachricht gefuellt wie `.btn-primary`, Antwort als Karte
  mit Haarlinie — dieselbe Aufteilung wie in der App.
- **Fund im Manager:** `.radio .dot` war eckig (2 px). Im System ist der
  Radio-Punkt ausdruecklich **rund** (`border-radius: 50%`) — korrigiert. Der
  Statuspunkt im Chat bleibt aus demselben Grund rund.
- **Browser-Terminal**: Konsole bleibt dunkel (dafuer gibt es keine
  Systemkomponente, und ein heller Terminalgrund waere schlechter), aber Leiste
  und Tasten folgen jetzt dem dunklen Band, Slate-Blau, eckig, Barlow.
- **Offen: katfs-Freigabeseite** (`katfs/web/index.html`) — eigene dunkle
  Palette mit Blau/Gruen, Rundungen 5/10/50 %, system-ui. Nicht angefasst,
  weil dort auch die WASM-Bruecke haengt.

## 2026-08-14 (App folgt dem Design-System "Industry")
- Tokens direkt aus dem Projekt gelesen (`DesignSync get_file` auf
  `theme.json` + `styles.css`) statt nachgebaut — die App hatte bisher nur
  Farben und Schriftfamilie uebernommen.
- **Eckig ausnahmslos**: `styles.css` ueberstimmt am Ende das `radius:4` aus
  `theme.json` mit `border-radius:0` fuer Card/Button/Input/Tag/Dialog. Shapes
  auf 0, Kreis-Avatare und -Modussymbole sowie die 2/3/8-dp-Rundungen raus.
- **Typo-Skala 1:1**: h1 42 / h2 32 / h3 25 / h4 20 / h5 16, Headings mit
  line-height 1.12 und -0.015em, Body 15/1.55; `labelSmall` traegt jetzt die
  h6-Rolle (13 px, versal, 0.08em) statt Materials Default.
- **Raster** aus `density: 0.85` als `IndustrySpacing` (3.4/6.8/10.2/13.6/20.4/27.2).
- Neu `IndustryComponents.kt`: `blueprintFrame()` zeichnet Haarlinie plus die
  vier Registermarken *ausserhalb* der Box (in CSS `.corner tl|tr|bl|br`),
  dazu `BlueprintBox`, `Tag` und `IndustryDivider`.
- **Chat-Bubbles** ohne Material-Tonung: eigene Nachricht gefuellt wie
  `.btn-primary`, Antwort des Agenten transparent mit Haarlinie wie `.card`.
- Dark-Mode bleibt (Handy wird nachts benutzt) — im System ist er als
  `band: light` nicht vorgesehen, hier also eine bewusste Erweiterung.
- KatAgent **3.9** (versionCode 39).

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
