# MrVoice — Verdrahtung (Seeed XIAO ESP32-S3)

Verbindliche Pinbelegung. Deckungsgleich mit `src/config.h`.

## Pinbelegung

| Bauteil | Signal | XIAO-Pin | GPIO | Status |
|---------|--------|----------|------|--------|
| **MAX98357A** | SD (Shutdown) | D2 | **3** | ✅ bestätigt |
| | DIN | D3 | **4** | ✅ bestätigt |
| | BCLK | D4 | **5** | ✅ bestätigt |
| | LRC | D5 | **6** | ✅ bestätigt |
| | VIN | 5V | — | |
| | GND | GND | — | |
| **Taster** | gegen GND | D6 | **43** | ✅ bestätigt |
| **INMP441** | SCK | D8 | **7** | verdrahtet, liefert nichts |
| | WS | D9 | **8** | verdrahtet, liefert nichts |
| | SD | D7 | **44** | verdrahtet, liefert nichts |
| | L/R | GND | — | gegen GND |
| | VDD | 3V3 | — | |
| | GND | GND | — | |

Frei geblieben: GPIO 1, 2 (D0, D1).

### L/R liegt auf einem GPIO

Beim INMP441 wählt `L/R` den Kanal. Hier hängt der Pin nicht fest auf GND,
sondern auf D9 (GPIO 8). `board_init()` treibt ihn deshalb aktiv auf LOW —
sonst sendet das Mikrofon im rechten Slot und die Aufnahme bleibt still.

### SD liegt auf U0RXD

`SD` hängt auf D7 (GPIO 44), was zugleich U0RXD ist. `gpio_config()` allein
löst die UART nicht vom Pad; `board_init()` ruft davor `gpio_reset_pin()`.
Dasselbe gilt für den Taster auf GPIO 43 (U0TXD).

## XIAO ESP32-S3: Pin-Bezeichnung → GPIO

| Pin | GPIO | | Pin | GPIO |
|-----|------|---|-----|------|
| D0 | 1 | | D6 | 43 |
| D1 | 2 | | D7 | 44 |
| D2 | 3 | | D8 | 7 |
| D3 | 4 | | D9 | 8 |
| D4 | 5 | | D10 | 9 |
| D5 | 6 | | LED | 21 |

Der Aufdruck auf der Platine nennt D-Nummern, die Software GPIO-Nummern. Diese
Verwechslung hat beim Aufbau mehrere Stunden gekostet.

## Wichtig: SD am MAX98357A

`SD` ist **nicht** optional. Der Pin hat intern einen Pulldown; liegt er nicht
auf HIGH, bleibt der Verstärker stumm, egal was I2S sendet. `board_init()`
treibt GPIO 3 deshalb beim Start aktiv auf HIGH.

Genau daran lag es: `SD` hing an GPIO 3, in der alten Konfiguration war dort
aber die Mikrofon-Datenleitung eingetragen, und der Verstärker wurde nie
eingeschaltet.

## Prüfen ohne Messgerät

Über die serielle Konsole (`pio device monitor`):

| Befehl | Zweck |
|--------|-------|
| `tone` | 440-Hz-Testton über den Verstärker |
| `tonepins <bclk> <lrc> <din> [sd]` | Ton auf frei gewählten Pins |
| `mic` | 2 s aufnehmen, Pegel melden |
| `micscan [sck ws sd]` | Mikrofon-Pins durchprobieren |
| `micraw` | I2S-Slot-Formate durchprobieren, Rohdaten zeigen |
| `btn` | Taster beobachten, erkannte Gesten anzeigen |
| `btnscan` | Taster auf allen Pins suchen |
| `pintest <gpio> ...` | Prüfen, ob an einem Pin etwas hängt |
| `loopback [bclk lrc din]` | I2S-TX chipintern zurückmessen |
| `probe` | Selbsttest TTS → STT → Chat |

`loopback` ist der Schiedsrichter: Kommt das Muster zurück, arbeitet der ESP32
korrekt und der Fehler liegt außerhalb des Chips.

## Historie

`Readme-supermini-alt.md` beschreibt einen **ESP32-S3 SuperMini** mit einer
völlig anderen Belegung. Diese Datei gehört nicht zu diesem Aufbau — ihre
GPIO 15/16/17 sind auf dem XIAO nicht herausgeführt. Nur als Referenz behalten.
