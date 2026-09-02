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

## Einrichten

```bash
./kaim56-voice                      # erster Lauf schreibt ~/.config/kaim56-voice.json
$EDITOR ~/.config/kaim56-voice.json # base_url, user, pass, instance
./kaim56-voice --probe "Wie spät ist es?"   # Selbsttest ohne Mikrofon:
                                    # TTS -> STT -> Chat -> Antwort wird gesprochen
./kaim56-voice                      # ab jetzt: Icon in der Topbar
```

Autostart: `kaim56-voice.desktop` nach `~/.config/autostart/` kopieren und
darin den Pfad zum Binary anpassen.

## Bedienung

Alles hängt am Topbar-Menü: **Hören an/aus**, **Sprechen stoppen**,
**Neues Gespräch** (`/reset` an den Agenten), **Instanz** (Liste kommt live
vom Manager). Während der Client denkt oder spricht, ist das Mikrofon stumm —
er hört sich sonst selbst zu.

Ohne Topbar (SSH, Test): `--headless` loggt Zustände und Transkripte auf
stdout, `--once` verarbeitet genau eine Äußerung und beendet sich,
`--instance <name>` überstimmt die Config.

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
