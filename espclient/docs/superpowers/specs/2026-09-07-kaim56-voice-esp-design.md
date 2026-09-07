# MrVoice → kAIm56 Voice Client (ESP32-S3) — Design

**Date:** 2026-09-07
**Status:** Approved, ready for implementation
**Supersedes:** the LiveKit architecture described in `CLAUDE.md`

## 1. Purpose

Port the kAIm56 hands-free voice client — today a Go binary for the Linux
desktop — onto the existing MrVoice ESP32-S3 hardware.

Toward the kAIm56 manager the device must behave like the desktop client:
capture an utterance, upload it for recognition, hand the text to an agent
instance, and speak the reply sentence by sentence. The manager and its voice
service are unchanged. Everything described here is built on the device.

**Source of truth for behaviour and numbers** is the porting brief
(*AIm56 · Sprachclient · Portierungs-Brief*), Part A (assignment) and Part B
(reference implementation in Go). Where this document and the brief disagree,
the brief wins — except for the deviations recorded in §3, which were decided
explicitly.

## 2. Non-goals

- No speech recognition, language model, or synthesis on the device.
- No iroh transport.
- No topbar menu.
- No OTA (not requested; the partition table leaves room for it later).

## 3. Decisions that deviate from the brief

The brief assumes a continuously-listening desktop client. This device has a
physical button, which removes the need for several subsystems.

| # | Decision | Consequence |
|---|---|---|
| D1 | **No VAD.** Capture is gated by the button: audio is recorded only while it is held. | `vad.go` (B.4) is not ported. The energy VAD, adaptive noise floor, 8-frame pre-roll ring, and the six `vad` config keys are all dropped, as are the VAD fixtures and A.6 acceptance criterion #2. |
| D2 | **No wake word, neither stage.** The button press *is* the "I am addressing you" signal. | `wake.go` and `wakelocal.go` are not ported. No MFCC, no FFT, no DTW, no enrollment, no wake templates, no `esp-dsp` dependency. `--enroll` and `--wake-test` do not exist. Removes the NVS sizing problem, since nothing large is stored. |
| D3 | **Button map:** hold = talk, tap = stop speaking, double tap = new conversation (`/reset`). 10 s hold remains factory reset. | Replaces A.2's short/long/double mapping, whose "listening on/off" is meaningless without continuous listening. |
| D4 | **Transport: LAN, plain HTTP on port 8700.** | No mbedTLS, no CA bundle. Basic Auth still applies when `MANAGER_PASS` is set. The HTTP client sits behind a thin interface so TLS can be added later as a config switch. |
| D5 | **Status display: the XIAO's single onboard LED**, driven by LEDC. | The four states become rhythms rather than colours (§7). |

Two remnants of the VAD config survive as guards on the recording buffer:
`min_ms` (default 400) discards a stray tap, and `max_s` (default 30) caps the
buffer.

## 4. What is deleted

The entire LiveKit stack goes: the `livekit` component, `esp_peer`,
`esp_capture`, `av_render`, `media_lib_sal`, `esp_libsrtp`,
`esp_websocket_client`, `nanopb`, `khash`, `esp_audio_codec` (Opus), and the
camera/video components those pulled in transitively.

In `src/`: `lk_client.c/h` and `jwt.c/h` are removed outright. Roughly 400 of
`audio.c`'s 560 lines go with them — the mute wrapper, the `esp_capture`
plumbing, and the `direct_i2s_render` vtable.

After the purge the project depends on **stock ESP-IDF only**. No external
component is required: audio is raw I2S in both directions and every network
call is `esp_http_client`.

This also retires the "Known Issue: SCTP over TURN" blocker permanently.

## 5. What is kept

`wifi_manager.c` (STA + captive portal + SNTP), `config_server.c` (web config
UI), `nvs_config.c` (persistence), and `button.c` (GPIO polling) survive —
about 1,400 lines of working code. `nvs_config` is re-keyed (§8) and
`config_server` loses its LiveKit page in favour of a manager page.

SNTP stays: the chat ID is `voice-<unix-time>`, so the device needs the wall
clock.

## 6. Architecture

### 6.1 Modules

| Module | Responsibility | Origin |
|---|---|---|
| `capture.c` | I2S0 RX at 16 kHz s16 mono into a PSRAM buffer while the button is held; enforces `min_ms` and `max_s` | new |
| `wav.c` | 44-byte header: write (16 kHz, for STT upload) and parse (22.05 kHz, from TTS) | port, B.4 |
| `manager.c` | HTTP client for the four endpoints; Basic Auth; error extraction | port, A.3 / B.3 `manager.go` |
| `stream.c` | Sentence streamer over the chat token stream | port 1:1, B.6 / `stream.go` |
| `speakable.c` | Read-aloud filter as a state scanner, no regex | port, B.6 / `manager.go` |
| `player.c` | Parse WAV header, retune I2S1, stream 4 kB blocks to the DAC | new |
| `ui.c` | Button gesture recognition and LED patterns | new |
| `app.c` | State machine, the two tasks, the sentence queue | new |

Each module is a flat C file with a header exposing a small function set, so
the pure ones (`wav`, `stream`, `speakable`) can be compiled and tested on the
host.

### 6.2 Task structure

A.5 requires TTS playback to begin on the first finished sentence *while the
chat response is still streaming*. Reading and speaking therefore cannot share
a task.

```
  [Conversation task]                      [Speech task]
  POST /api/stt  (blocking upload)
  POST /api/chat/<inst>
    read until connection close
    feed bytes -> stream.c
      sentence complete ──► queue ──►  pop sentence
                                        POST /api/tts
                                        parse WAV header
                                        retune I2S1
                                        stream 4 kB blocks -> I2S TX
```

The queue holds heap-allocated sentence strings; depth 8. TTS is synchronous
*per sentence*, exactly as in the Go original, so there is a ~0.45 s gap
between sentences. First-sentence latency — the only figure A.6 measures — is
unaffected.

### 6.3 State machine

```
IDLE ──button down──► RECORDING ──button up──► THINKING ──first sentence──► SPEAKING
  ▲                       │                        │                          │
  │                       │ < min_ms               │ error / empty            │ queue drained
  │                       ▼                        ▼                          │
  └──────────────── DISCARDED (blink) ◄────────────┴──────────────────────────┘
```

A tap during `SPEAKING` aborts the player, drains the queue, and returns to
`IDLE`. The microphone is only ever read in `RECORDING`, so the device cannot
hear itself and no echo cancellation is needed.

## 7. LED patterns

Single LED on the XIAO (believed GPIO 21, active low — **to be confirmed
against the board**), driven by LEDC so pulses are smooth.

| State | Pattern |
|---|---|
| IDLE | off |
| RECORDING | solid on |
| THINKING | slow pulse, ~1 Hz |
| SPEAKING | fast pulse, ~4 Hz |
| DISCARDED | three rapid flashes, then IDLE |
| No WiFi / portal | double-blink heartbeat |

## 8. Configuration

NVS namespace `mrvoice`, config version bumped to 2 (version 1 keys are
abandoned; a mismatch already falls back to defaults).

| Key | Default | Notes |
|---|---|---|
| `base_url` | *(empty)* | e.g. `http://manager.example:8700` |
| `user` | *(empty)* | Basic Auth, optional |
| `pass` | *(empty)* | Basic Auth, optional |
| `instance` | *(empty)* | agent instance name |
| `prompt` | brief's default | prepended per turn |
| `min_ms` | 400 | discard shorter recordings |
| `max_s` | 30 | recording cap |

Dropped from B.7: `iroh`, `iroh_listen`, `wake_word`, `wake_mode`,
`wake_threshold`, and the whole `vad` object.

Retained from the existing firmware: WiFi credentials and the GPIO map.

## 9. Server contract

Per A.3, taken as given. The manager answers as **HTTP/1.0 — one connection
per request, no keep-alive**. Every request therefore uses a fresh
`esp_http_client` handle with `keep_alive_enable = false`.

| Endpoint | Request | Response | Device handling |
|---|---|---|---|
| `POST /api/stt` | WAV 16 kHz s16 mono, `Content-Type: audio/wav`, **Content-Length mandatory** | `{"text","seconds","took"}` | `esp_http_client_open()` with an explicit length, then chunked `esp_http_client_write()` from PSRAM. No chunked upload. |
| `POST /api/chat/<inst>` | `{"message","chat"}` | `text/plain` tokens, **no Content-Length, no chunked**, until connection close | loop `esp_http_client_read()` until it returns 0; feed the task watchdog throughout; a turn may run for minutes |
| `POST /api/tts` | `{"text"}` | `audio/wav` with Content-Length, 22 050 Hz s16 mono | read in 4 kB blocks straight to I2S; never buffer the whole file |
| `GET /api/instances` | — | JSON list | only needed if the instance is selectable on-device; deferred |

Any status other than 200 is an error; the body carries JSON with `error`. Log
the first 300 bytes, as the original does.

New conversation: `{"message": "/reset"}` to the same instance, then a fresh
chat ID.

## 10. Memory budget

| Item | Size | Placement |
|---|---|---|
| Utterance buffer (`max_s` 30) | 960 kB | PSRAM, allocated once at boot |
| Chat response window | 4 kB | internal |
| TTS block buffer | 4 kB | internal |
| Sentence queue | 8 × pointer + strings | internal + heap |

Capture is 32 kB/s, playback 44 kB/s. Comfortable on 8 MB PSRAM.

## 11. Audio path

- **In:** I2S0, 16 kHz, 16-bit data in 32-bit slots, left channel, Philips —
  already configured correctly in `audio.c` for the INMP441 and carried over
  unchanged.
- **Out:** I2S1 to the MAX98357A. Currently hard-wired to 16 kHz stereo; the
  player reads the rate from each WAV header and calls
  `i2s_channel_reconfig_std_clock()` when it differs. Preferred over
  resampling: cheaper and lossless.

## 12. Testing

Host-side unit tests (`pio test -e native`) for the three pure modules:

- `stream.c` — sentence boundaries on `.!?…:` followed by whitespace, the
  last character never counted, think blocks and code fences holding output
  back, `Close()` flushing the remainder.
- `speakable.c` — think blocks including unterminated ones, 🔧 lines, code
  blocks becoming "Codeblock übersprungen", link text kept and URLs dropped,
  decoration characters removed, whitespace collapsed, empty results not
  spoken.
- `wav.c` — header round-trip, and parsing of a real Piper response header.

**Outstanding dependency:** A.2 requires these ported 1:1 and verified against
the Go original as oracle, using its fixtures. `stream.go` and `manager.go`'s
`speakable()` have not been provided. Until they are, fixtures are derived
from B.6's prose and the oracle requirement is unmet — recorded here as a
known gap.

On-device acceptance per A.6: the `--probe` chain (text → TTS → STT → chat →
TTS → playback) as a serial command, and a latency measurement from button
release to first sound, broken down into STT / first sentence / TTS. Desktop
reference: STT 0.44 s, TTS 0.45 s.

**Outstanding dependency:** a reachable manager (`base_url`, instance,
credentials) is required for both.

## 13. Build changes

- `src/idf_component.yml`: reduced to `idf: ">=5.4"`; every other dependency
  removed.
- `managed_components/`, `components/livekit/`, `components/sandbox_token/`,
  `dependencies.lock`, `sdkconfig.esp32s3`: deleted and regenerated.
- `sdkconfig.defaults`: DTLS-SRTP, Opus, and the WebRTC LWIP tuning removed;
  PSRAM and 240 MHz kept; log level set to INFO.
- `platformio.ini`: `board_build.flash_size` corrected from `4MB` to `8MB`
  (it currently contradicts a 7 MB factory partition), and the stale
  `-DWIFI_SSID` / `-DWIFI_PASS` build flags removed — they collide with the
  `config.h` macros of the same name.
- `CLAUDE.md`: rewritten; it currently documents the LiveKit design.
