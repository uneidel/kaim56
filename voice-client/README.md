# kAIm56 Sprachclient (Fedora)

Freihändig mit einer Plattform-Instanz sprechen — standardmäßig `myassistant`,
umschaltbar im Menü. Ein Icon in der Topbar zeigt den Zustand (hört / denkt /
spricht / aus); ein Energie-VAD erkennt Äußerungen von selbst, Push-to-talk
gibt es nicht.

Der Client ist bewusst dumm: Aufnahme → `/api/stt` → `/api/chat/<instanz>` →
`/api/tts` → Wiedergabe. STT, LLM und Piper-TTS laufen alle auf dem Server;
der Verlauf lebt im Agenten selbst, „Neues Gespräch" schickt schlicht `/reset`.

## Installation (Fedora Workstation)

```bash
# Audio-Werkzeuge sind mit PipeWire schon da (parec/paplay).
# Für das Topbar-Icon:
sudo dnf install python3-gobject gtk3 libappindicator-gtk3 \
                 gnome-shell-extension-appindicator
# GNOME: Extension einschalten, danach ab- und wieder anmelden:
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

KDE braucht keine Extension — der Indicator landet direkt im Systray.

## Einrichten

```bash
python3 kaim56_voice.py        # erster Lauf schreibt ~/.config/kaim56-voice.json
$EDITOR ~/.config/kaim56-voice.json   # base_url, user, pass, instance
python3 kaim56_voice.py        # ab jetzt: Icon in der Topbar
```

Autostart: `kaim56-voice.desktop` nach `~/.config/autostart/` kopieren und
darin den Pfad zum Skript anpassen.

## Bedienung

Alles hängt am Topbar-Menü: **Hören an/aus**, **Sprechen stoppen**,
**Neues Gespräch** (`/reset` an den Agenten), **Instanz** (Liste kommt live
vom Manager). Während der Client denkt oder spricht, ist das Mikrofon stumm —
er hört sich sonst selbst zu.

Ohne Topbar (SSH, Test): `--headless` loggt Zustände und Transkripte auf
stdout, `--once` verarbeitet genau eine Äußerung und beendet sich.

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
python3 tests.py    # VAD an synthetischem PCM, WAV-Header, Vorlese-Filter, Config
```
