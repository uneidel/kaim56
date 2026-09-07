# MrVoice — kAIm56 Voice Client (ESP32-S3)

Push-to-talk voice client for the kAIm56 platform. The device records while a
button is held, uploads the audio for recognition, hands the text to an agent
instance, and speaks the reply sentence by sentence.

**Hardware:** Seeed XIAO ESP32-S3 (8 MB flash, 8 MB PSRAM)
**Framework:** ESP-IDF 5.4 via PlatformIO — **stock IDF only, no external components**
**Design spec:** `docs/superpowers/specs/2026-09-07-kaim56-voice-esp-design.md`
**Plan:** `docs/superpowers/plans/2026-09-07-kaim56-voice-esp.md`

The behavioural source of truth is the porting brief (*AIm56 · Sprachclient ·
Portierungs-Brief*), Parts A and B. Where this file and the brief disagree,
the brief wins.

## Build

```bash
pio run -e esp32s3                    # build firmware
pio run -e esp32s3 --target upload    # flash
pio device monitor                    # serial console; type `probe` for the self-test
pio test -e native                    # host-side unit tests (50 tests)
```

`native` is a **test-only** environment with no `main()`. Plain `pio run` will
fail on it — always pass `-e esp32s3` for firmware.

## Behaviour

- **Capture:** button held = recording. There is no VAD and no wake word; the
  button is the "I am addressing you" signal.
- **Guards:** recordings under `min_ms` (400) are discarded; `max_s` (30) caps
  the buffer.
- **Duplex:** none. The microphone is read only in `RECORDING`, so the device
  cannot hear itself and needs no echo cancellation.
- **Latency:** TTS starts on the first finished sentence while the chat
  response is still streaming.

## State machine

```
IDLE ──hold──► RECORDING ──release──► THINKING ──first sentence──► SPEAKING
  ▲                │                      │                            │
  │                │ < min_ms             │ no text                    │ queue empty
  └──────── DISCARDED (blink) ◄───────────┴────────────────────────────┘
```

A tap during `SPEAKING` aborts the player and drains the queue.

## Button (one button, D6 = GPIO 43)

| Gesture | Action |
|---|---|
| Hold | Push-to-talk: record and send |
| Tap | Stop speaking |
| Double tap | New conversation (`/reset`, new chat ID) |
| Hold 10 s | Factory reset |

A tap is reported 350 ms after release, since a tap and the first half of a
double tap are indistinguishable until the window closes. A tap followed by a
hold resolves as a hold — push-to-talk is never blocked by a stray press.

## LED (single onboard LED, GPIO 21, active low — verify on your board)

| State | Pattern |
|---|---|
| Idle | off |
| Recording | solid |
| Thinking | slow pulse (~1 Hz) |
| Speaking | fast pulse (~4 Hz) |
| Discarded | three rapid flashes |
| No network | double-blink heartbeat |

## Pinout (measured on hardware, all confirmed)

| Part | Signal | XIAO | GPIO |
|---|---|---|---|
| MAX98357A | SD / DIN / BCLK / LRC | D2 / D3 / D4 / D5 | 3 / 4 / 5 / 6 |
| INMP441 | SCK / WS / SD / LR | D8 / D10 / D7 / D9 | 7 / 9 / 44 / 8 |
| Button | to GND | D6 | 43 |
| LED | onboard | — | 21 (unconfirmed) |

The board silkscreen uses D-numbers, the software uses GPIO numbers. That
mismatch cost hours of debugging. GPIO 43/44 are U0TXD/U0RXD, so both need
`gpio_reset_pin()` before use. The INMP441's L/R strap sits on a GPIO and must
be driven LOW, and the MAX98357A's SD must be driven HIGH or the amplifier
stays muted regardless of what I2S sends. See `Readme.md`.

## Modules

| File | Responsibility | Host-tested |
|---|---|---|
| `app.c` | State machine, the two tasks, sentence queue, `probe` command | — |
| `manager.c` | HTTP client for the four kAIm56 endpoints | — |
| `capture.c` | I2S0 → PSRAM buffer while recording | — |
| `player.c` | Pull-based WAV playback, retunes I2S1 per file | — |
| `ui.c` | GPIO + LED, delegates the gesture grammar | — |
| `audio.c` | I2S bring-up only | — |
| `wav.c` | 44-byte header write and chunk-walking parse | ✅ 6 |
| `stream.c` | Sentence streamer over the token stream | ✅ 12 |
| `speakable.c` | Read-aloud filter, no regex | ✅ 13 |
| `form.c` | urlencoded form parsing | ✅ 9 |
| `gesture.c` | Button gesture grammar | ✅ 10 |
| `nvs_config.c` | Persistence | — |
| `wifi_manager.c` | STA + captive portal + SNTP | — |
| `config_server.c` | Web config UI | — |

## Task structure

Two tasks joined by a queue of `char*` sentences (depth 8):

- **conversation** — polls the button, records, `POST /api/stt`,
  `POST /api/chat`, cuts the stream into sentences, pushes them.
- **speech** — pops a sentence, `POST /api/tts`, streams the WAV into I2S.

TTS is synchronous per sentence, as in the Go original, so there is a ~0.45 s
gap between sentences. First-sentence latency is unaffected.

## Server contract

The manager answers as **HTTP/1.0 — one connection per request, no
keep-alive**. Every request uses a fresh `esp_http_client` handle with
`keep_alive_enable = false`.

| Endpoint | Request | Response | Handling |
|---|---|---|---|
| `POST /api/stt` | WAV 16 kHz s16 mono, **Content-Length mandatory** | `{"text","seconds","took"}` | `esp_http_client_open()` with explicit length, chunked writes from PSRAM. Chunked upload is rejected. |
| `POST /api/chat/<inst>` | `{"message","chat"}` | plain-text tokens, **no length, no chunked** | read until `esp_http_client_read()` returns 0; feed the watchdog; a turn may run minutes |
| `POST /api/tts` | `{"text"}` | `audio/wav`, 22 050 Hz s16 mono | read in 4 kB blocks straight to I2S; never fully buffered |
| `GET /api/instances` | — | JSON list | not implemented; the instance is set in the web UI |

Any status other than 200 is an error; the body carries JSON with `error`, and
the first 300 bytes are logged. Basic Auth is sent only when a user is set.

## Configuration (NVS namespace `mrvoice`, version 2)

`base_url`, `user`, `pass`, `instance`, `prompt`, `min_ms`, `max_s`, plus WiFi
credentials and the GPIO map. Set via the captive portal or the web UI at the
device's IP: **WiFi**, **Manager**, **Aufnahme**.

Chat ID is `voice-<unix-time>`, so SNTP must have synced before the first turn.

## Memory

| Item | Size | Where |
|---|---|---|
| Utterance buffer (`max_s` 30) | 960 kB | PSRAM, allocated once at boot |
| Chat window / TTS block | 4 kB each | internal |

Capture is 32 kB/s, playback 44 kB/s.

## Conventions

- **Never use `\x` hex escapes for non-ASCII text.** A C hex escape consumes
  every following hex digit, so `"\xC3\xBCbersprungen"` parses `\xBCb` (out of
  range), not `ü` + `b`. Write literal UTF-8 — the sources are UTF-8.
- Log messages are German.

## Hardware status (verified on device 2026-09-07)

| Item | State |
|---|---|
| MAX98357A | ✅ audible; enabled only during playback |
| INMP441 | ✅ captures audio (RMS ~3500 on room sound) |
| Button | ✅ hold/release detected on GPIO 43 |
| WiFi + SNTP | ✅ associated, clock synced |
| Manager over TLS | ✅ certificate validated, all endpoints answer |
| STT / chat / TTS | ✅ full `probe` chain |
| Push-to-talk | ⚠️ every stage verified; one full turn not yet watched end to end |

Capture gain defaults to **800 %**. The INMP441 on this board peaks at only
~500-2000 of 32767 on loud speech, and STT returns empty text for a signal
that quiet. Adjust with `gain <100-4000>`; the applied peak is logged on every
recording.

Playback volume defaults to **25 %**. Piper delivers audio at full scale
(peak 32767), which clips through the amplifier on this board. Adjust with
`vol <0-100>`.

The amplifier must stay off when idle. Enabling it at boot draws enough
current that the WiFi radio browns the board out — scans return zero networks
and flashing fails partway through.

## Serial commands

`probe` `say <text>` `saybuf <text>` `tone` `tonepins <bclk> <lrc> <din> [sd]`
`mic` `micscan [sck ws sd]` `micperm <p1..p4>` `micraw` `loopback [pins]`
`btn` `btnscan` `pintest <gpio>...` `vol <0-100>` `gain <100-4000>` `wifi <ssid> <pass>`
`mgr <url> <instanz> [user] [pass]` `net` `scan`

Diagnostics suspend the gesture loop while they run, so a button press during
`btnscan` is not seen by the scanner itself.

## Known gaps

1. `stream.go`, `manager.go` (`speakable()`) and `client.go` (the
   `[Voice-Client]` prompt prefix) were never provided. Those ports are
   reconstructed from Part B's prose and their fixtures are marked
   `FIXTURE-UNVERIFIED`. A.2 wants the Go original as oracle; that is unmet.
2. A complete turn — button → STT → chat → spoken answer — has not yet been
   watched end to end in one window, though every stage is individually
   verified.

   The microphone was silent for a long stretch because only ~2.2 V reached
   its VDD: with the supply wire not making contact, the chip was parasitically
   powered through the ESD clamp diodes on its clock inputs, enough to sit at
   2.2 V but not to drive its output. That is worth remembering — it looks like
   a software fault and survives every reconfiguration.
3. `DEFAULT_LED_GPIO` (21) is unconfirmed; nobody has looked at the LED.
4. Capture gain (800 %) was a first guess, chosen after STT returned empty text
   for an unamplified recording. It has not yet been confirmed against a real
   utterance; `gain` logs the resulting peak, which should land in the
   thousands without clipping.
