# kAIm56 Sprachclient (Linux-Desktop, Go)

Freihändig mit einer Plattform-Instanz sprechen — standardmäßig `myassistant`,
umschaltbar im Menü. Ein Icon in der Topbar zeigt den Zustand (grün hört /
bernstein denkt / blau spricht / grau aus); ein Energie-VAD erkennt Äußerungen
von selbst, Push-to-talk gibt es nicht.

Der Client ist bewusst dumm: Aufnahme → `/api/stt` → `/api/chat/<instanz>` →
`/api/tts` → Wiedergabe. STT, LLM und Piper-TTS laufen alle auf dem Server;
der Verlauf lebt im Agenten selbst, „Neues Gespräch" schickt schlicht `/reset`.

Ein einziges statisches Binary (pures Go, kein cgo, keine Python-Umgebung).
Audio läuft über PipeWire-Werkzeuge als Subprozess (parec/pw-record/arecord
bzw. paplay/pw-play/aplay — das erste, das da ist; mit PipeWire schon an Bord).

## Bauen

```bash
go build -o kaim56-voice .          # oder: CGO_ENABLED=0 go build -trimpath -ldflags="-s -w"
```

## Topbar-Icon (StatusNotifier)

KDE kann es nativ. GNOME braucht die AppIndicator-Extension:

```bash
sudo dnf install gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com   # dann ab-/anmelden
```

## Einrichten (iroh — der normale Weg)

Wie die App erreicht der Client den Manager über iroh (P2P, E2E-verschlüsselt,
kein offener HTTPS-Port, kein VPN). `kaim56-tunnel` (aus `iroh-gw/`, Binary in
`dist/`) liegt neben `kaim56-voice` — der Client startet ihn selbst.

```bash
./kaim56-voice                      # erster Lauf schreibt ~/.config/kaim56-voice.json
$EDITOR ~/.config/kaim56-voice.json # "iroh": "<Manager-NodeId>" eintragen
                                    # (steht im Web-UI im iroh-Tab)
./kaim56-voice --probe "Wie spät ist es?"
```

Der erste Start zeigt die eigene iroh-Identität an — die einmalig im Web-UI
(iroh-Tab) zur Allowlist hinzufügen, wie beim Telefon. Der Schlüssel liegt
stabil in `~/.config/kaim56-tunnel.key`. Danach:

```bash
./kaim56-voice                      # Icon in der Topbar; Tunnel läuft als
                                    # Kindprozess und stirbt mit dem Client
```

Mit `"iroh"` gesetzt sind `base_url`, `user` und `pass` überflüssig
(`iroh_listen` ändert bei Bedarf den lokalen Port, Default 127.0.0.1:8701).

## Alternative: direktes HTTP(S)

Ohne `"iroh"` gilt `base_url` + `user`/`pass` — z. B. `http://<manager>:8700`
im LAN oder die öffentliche HTTPS-Adresse mit den Web-UI-Zugangsdaten. Der
Template-Platzhalter `manager.example` wird beim Start abgewiesen, nicht erst
beim ersten Satz.

Autostart: `kaim56-voice.desktop` nach `~/.config/autostart/` kopieren und
darin den Pfad zum Binary anpassen.

## Bedienung

Alles hängt am Topbar-Menü: **Hören an/aus**, **Sprechen stoppen**,
**Neues Gespräch** (`/reset` an den Agenten), **Instanz** (Liste kommt live
vom Manager). Während der Client denkt oder spricht, ist das Mikrofon stumm —
er hört sich sonst selbst zu.

Ohne Topbar (SSH, Test): `--headless` loggt Zustände und Transkripte auf
stdout (auch verworfene Äußerungen, als `(ignoriert: …)`), `--once`
verarbeitet genau eine Äußerung und beendet sich, `--instance <name>`
überstimmt die Config.

## Hotword

`"wake_word": "Kat"` in der Config (Template-Default; leer = jede Äußerung
geht durch). Gedacht für Telefonkonferenzen: das Mikro hört dauernd Sprache,
aber nur Äußerungen, die mit dem Wort beginnen, erreichen den Agenten —
„Kat, fass mir das Dokument zusammen“. Das Wort allein („Kat?“) antwortet
mit einem kurzen „Ja?“.

Es läuft **kein** Modell auf dem Desktop: STT läuft ohnehin für jede
Äußerung auf dem eigenen Server (Parakeet, lokal), das Gate ist ein
Textvergleich auf dem Transkript danach — case-insensitiv, STT-Interpunktion
egal, aber mit Wortgrenze („Katalog“ weckt „Kat“ nicht). Verworfenes wird
nirgendwohin weitergeleitet und nicht gespeichert. Wähle ein Wort, das STT
zuverlässig trifft — kurz, betont, keine Homophone des Alltagsvokabulars.

## Tunnel von Hand (curl, Browser, andere Clients)

`kaim56-tunnel` ist nicht an den Sprachclient gebunden — von Hand gestartet
legt er den Manager fuer JEDEN lokalen HTTP-Client auf einen Port:

```bash
./kaim56-tunnel --id                 # NodeId anzeigen -> Allowlist
./kaim56-tunnel <manager-node-id> &  # lauscht auf 127.0.0.1:8701
curl http://127.0.0.1:8701/api/instances
```

## VAD einstellen

In der Config unter `vad`:

| Schlüssel          | Default | Bedeutung                                    |
|--------------------|---------|----------------------------------------------|
| `start_frames`     | 5       | stimmhafte 30-ms-Frames (von 8) bis Start    |
| `end_ms`           | 800     | Stille, die eine Äußerung beendet            |
| `min_ms`           | 400     | kürzere Segmente werden verworfen            |
| `max_s`            | 30      | Zwangsschnitt bei Dauersprechen              |
| `threshold_factor` | 3.0     | Schwelle = Rauschteppich × Faktor            |
| `threshold_min`    | 350     | Untergrenze der Schwelle (RMS, s16)          |

Zu empfindlich (reagiert auf Tastatur): `threshold_factor` oder
`threshold_min` erhöhen. Schneidet Satzenden ab: `end_ms` erhöhen.

## Tests

```bash
go vet ./... && go test ./...   # VAD an synthetischem PCM, WAV-Header,
                                # Vorlese-Filter, Config — ohne Mikrofon/Manager
```
