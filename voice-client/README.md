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
./build.sh          # Release: EIN Binary, kaim56-tunnel eingebettet
                    # (baut den Tunnel bei Bedarf zuerst, Docker)
go build .          # Dev-Build ohne eingebetteten Tunnel — sucht ihn
                    # neben dem Binary bzw. im PATH
```

## Topbar-Icon (StatusNotifier)

KDE kann es nativ. GNOME braucht die AppIndicator-Extension:

```bash
sudo dnf install gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com   # dann ab-/anmelden
```

## Einrichten (iroh — der normale Weg)

Wie die App erreicht der Client den Manager über iroh (P2P, E2E-verschlüsselt,
kein offener HTTPS-Port, kein VPN). Der Tunnel steckt im Binary: beim Start
wird er nach `~/.cache/kaim56-voice/` ausgepackt und als Kindprozess gefahren —
eine Datei kopieren genügt.

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

## Custom-Prompt

`"prompt": "…"` in der Config wird jeder gesprochenen Nachricht vorangestellt
(gekennzeichnet als `[Voice-Client] …`, der Agent sieht Anweisung und Satz
getrennt). Template-Default: kurz und vorlesbar antworten, ohne Listen/Links.
`--prompt "…"` überstimmt die Config für einen Lauf, `--prompt -` schaltet
ihn ab. Leer = Nachricht geht unverändert raus.

## Hotword — zwei Stufen

**Stufe 1, Text-Gate** (Default): `"wake_word": "Kati, Katharina"` — eine
Komma-Liste von Varianten; leer = jede Äußerung geht durch. Nur Transkripte,
die mit einer Variante beginnen, erreichen den Agenten. Pro Variante ab
4 Buchstaben ist ein Tippfehler erlaubt (Levenshtein 1; bei 3 nicht — sonst
weckt „hat“ das Wort „Kat“), Wortgrenze bleibt Pflicht. Wortwahl an STT
messen, nicht am Gefühl: Parakeet verstümmelt kurze Wörter („Kat“ → „Tat“,
„Kaim“ → „Kein“); robust getestet sind **Kati, Katharina, Computer**.
Audio geht dabei zur Transkription an den eigenen Server; Verworfenes wird
nirgendwohin weitergeleitet.

**Stufe 2, lokales Modell** (`"wake_mode": "local"`): Audio verlässt den
Desktop erst NACH dem Wort — für Telefonkonferenzen die richtige Stufe.
Kein vortrainiertes Netz, sondern die eigene Stimme als Referenz
(MFCC-Templates + Subsequenz-DTW, pures Go):

```bash
./kaim56-voice --enroll      # Wake-Word dreimal einsprechen -> Modell
./kaim56-voice --wake-test   # Scores live ansehen, nichts wird gesendet
# dann in der Config: "wake_mode": "local"
```

Bei einem Treffer wird das Wort im Audio abgeschnitten (das DTW kennt das
Alignment-Ende) und nur die Nachricht dahinter geht zu STT — das Wort kann
also beliebig heißen, auch „Kaim“; STT sieht es nie. Sprecherabhängig:
fremde Stimmen in der Telko matchen schlecht, genau richtig. Die Schwelle
kommt aus dem Enrollment; `"wake_threshold"` überstimmt sie (kleiner =
strenger), `--wake-test` zeigt, wo eigene Sätze landen. Ähnlich klingende
Wörter („Kein …“ vs. „Kaim“) bleiben die Grenze des Verfahrens — im
Zweifel ein markanteres Wort einsprechen. Das Wort allein quittiert ein
gesprochenes „Ja?“; Modell liegt in `~/.config/kaim56-voice-wake.json`.

Verworfene Äußerungen sind sichtbar: Topbar-Status als ✕ (mit Score bzw.
Transkript), `--headless` druckt sie als `(ignoriert/lokal verworfen: …)`.

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
