# kAIm56 Voice Client (ESP32-S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the LiveKit/WebRTC firmware on the MrVoice ESP32-S3 with a push-to-talk client that talks plain HTTP to a kAIm56 manager: record while the button is held, upload for STT, stream the agent's reply, and speak it sentence by sentence.

**Architecture:** Two FreeRTOS tasks joined by a sentence queue. The conversation task uploads the recording, streams the chat response, and cuts it into sentences; the speech task turns each sentence into TTS audio and pushes it straight to I2S. Everything else is a flat C module with one job. No external components — stock ESP-IDF only.

**Tech Stack:** ESP-IDF 5.4.1 (C), PlatformIO, `esp_http_client`, `driver/i2s_std`, `driver/ledc`, cJSON and mbedTLS base64 (both ship with IDF), `pio test -e native` for host-side unit tests.

**Spec:** `docs/superpowers/specs/2026-09-07-kaim56-voice-esp-design.md`

## Global Constraints

- **Target:** Seeed XIAO ESP32-S3, 8 MB flash, 8 MB PSRAM (OCT, 80 MHz). PSRAM is required.
- **Dependencies:** stock ESP-IDF only. `src/idf_component.yml` must end as `dependencies: {idf: ">=5.4"}` and nothing else. Adding a managed component is a plan violation.
- **Capture format:** 16 kHz, 16-bit signed, mono. Non-negotiable — `/api/stt` is fed this directly.
- **Playback format:** whatever the TTS WAV header says; Piper emits 22 050 Hz, 16-bit, mono.
- **Manager is HTTP/1.0:** one connection per request, no keep-alive. Every request uses a fresh `esp_http_client` handle with `.keep_alive_enable = false`.
- **`/api/stt`** requires `Content-Length`. Chunked upload is rejected.
- **`/api/chat`** sends neither `Content-Length` nor chunked encoding. Read until the connection closes. A turn may run for minutes — feed the task watchdog.
- **`/api/tts`** response must never be fully buffered. Stream it in 4 kB blocks.
- **The microphone is read only in the RECORDING state.** The device must never hear itself.
- **Language:** log messages and the `Codeblock übersprungen` replacement string are German. Keep them German.
- **Never use `\x` hex escapes for non-ASCII text.** A C hex escape consumes every following hex digit, so `"\xC3\xBCbersprungen"` parses `\xBCb` (out of range), not `ü` + `b`. Write literal UTF-8 in the source instead — the files are UTF-8 and GCC handles them.
- **Behavioural source of truth:** the porting brief, Part B. Where this plan and the brief disagree, the brief wins.

**Version control:** this project is not currently a git repository. Task 1 initialises one; the commit steps in every later task depend on it.

**Known gap carried through the whole plan:** `stream.go`, `manager.go` (`speakable()`), and `client.go` (the prompt prefix) were not provided. Their behaviour is reconstructed from Part B's prose. Every test fixture derived this way is marked `FIXTURE-UNVERIFIED` in a comment so it can be re-checked against the Go original later.

## File Structure

**Deleted:** `src/lk_client.c`, `src/lk_client.h`, `src/jwt.c`, `src/jwt.h`, `src/wifi.c`, `src/wifi.h`, `components/livekit/`, `components/sandbox_token/`, `managed_components/`, `dependencies.lock`, `sdkconfig.esp32s3`.

**Created:**

| File | Responsibility |
|---|---|
| `src/wav.c/h` | 44-byte WAV header: write for upload, parse from TTS |
| `src/form.c/h` | urlencoded form parsing, extracted from config_server so it can be host-tested |
| `src/stream.c/h` | Sentence streamer over the chat token stream |
| `src/speakable.c/h` | Read-aloud filter — strips think blocks, code, links, decorations |
| `src/capture.c/h` | I2S0 RX into a PSRAM buffer while recording |
| `src/player.c/h` | Pull-based WAV playback to I2S1, retuning the clock per file |
| `src/manager.c/h` | HTTP client for the four kAIm56 endpoints |
| `src/ui.c/h` | Button gesture recognition, LED state patterns |
| `src/app.c/h` | State machine, the two tasks, the sentence queue |
| `test/test_wav/` `test/test_stream/` `test/test_speakable/` `test/test_form/` | Host-side unit tests |

**Modified:** `src/audio.c/h` (reduced to I2S bring-up only), `src/main.c`, `src/config.h`, `src/nvs_config.c/h`, `src/config_server.c`, `src/button.c/h` (absorbed by `ui.c`), `src/CMakeLists.txt`, `src/idf_component.yml`, `platformio.ini`, `sdkconfig.defaults`, `CLAUDE.md`.

---

### Task 1: Purge LiveKit and get a minimal build green

Strip the WebRTC stack down to a board that boots, joins WiFi, serves the config portal, and does nothing else. Nothing here is testable in isolation, so the deliverable is a clean build and a clean boot log.

**Files:**
- Delete: `src/lk_client.c`, `src/lk_client.h`, `src/jwt.c`, `src/jwt.h`, `src/wifi.c`, `src/wifi.h`, `components/`, `managed_components/`, `dependencies.lock`, `sdkconfig.esp32s3`
- Modify: `src/audio.c`, `src/audio.h`, `src/main.c`, `src/CMakeLists.txt`, `src/idf_component.yml`, `platformio.ini`, `sdkconfig.defaults`

**Interfaces:**
- Consumes: nothing.
- Produces: `void board_init(void)` — brings up I2S0 RX (16 kHz s16 mono) and I2S1 TX, sets the DAC shutdown pin high. Exposes `i2s_chan_handle_t audio_mic_handle(void)` and `i2s_chan_handle_t audio_dac_handle(void)` for `capture.c` and `player.c`.

- [ ] **Step 1: Initialise version control**

```bash
cd /home/ulrich/Documents/code/MrVoice
git init
printf '.pio/\nbuild/\nmanaged_components/\ndependencies.lock\nsdkconfig.esp32s3\nsdkconfig.esp32s3.old\n' >> .gitignore
git add -A && git commit -m "chore: baseline before kAIm56 port"
git checkout -b kaim56-port
```

- [ ] **Step 2: Delete the LiveKit stack**

```bash
cd /home/ulrich/Documents/code/MrVoice
rm -f src/lk_client.c src/lk_client.h src/jwt.c src/jwt.h src/wifi.c src/wifi.h
rm -rf components managed_components dependencies.lock sdkconfig.esp32s3
```

`src/wifi.c/h` goes because it is a 16-line stub superseded by `wifi_manager.c`.

- [ ] **Step 3: Reduce the component manifest**

Replace `src/idf_component.yml` entirely:

```yaml
dependencies:
  idf: ">=5.4"
```

- [ ] **Step 4: Rewrite the build source list**

Replace `src/CMakeLists.txt`:

```cmake
idf_component_register(
    SRCS
        "main.c"
        "audio.c"
        "nvs_config.c"
        "wifi_manager.c"
        "config_server.c"
        "button.c"
    INCLUDE_DIRS "."
    REQUIRES
        nvs_flash
        esp_wifi
        esp_http_server
        esp_http_client
        esp_netif
        esp_timer
        driver
        json
        mbedtls
        lwip
)
```

- [ ] **Step 5: Fix the PlatformIO configuration**

In `platformio.ini`: change `board_build.flash_size = 4MB` to `8MB` (it currently contradicts the 7 MB factory partition), and delete the entire `build_flags` block — the `-DWIFI_SSID` / `-DWIFI_PASS` defines collide with the macros of the same name in `config.h`.

- [ ] **Step 6: Strip the WebRTC settings out of sdkconfig.defaults**

Replace `sdkconfig.defaults`:

```ini
# PSRAM (required — the utterance buffer lives here)
CONFIG_SPIRAM=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=256
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=8192
CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y

# Cache
CONFIG_ESP32S3_DATA_CACHE_64KB=y
CONFIG_ESP32S3_DATA_CACHE_LINE_64B=y
CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB=y

# Performance
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
CONFIG_COMPILER_OPTIMIZATION_PERF=y
CONFIG_FREERTOS_HZ=1000
CONFIG_ESP_MAIN_TASK_STACK_SIZE=8192

# Console
CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y

# Flash
CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y

# The chat endpoint can hold a connection open for minutes
CONFIG_ESP_TASK_WDT_TIMEOUT_S=30

CONFIG_LOG_DEFAULT_LEVEL_INFO=y
```

- [ ] **Step 7: Reduce audio.c to I2S bring-up**

Replace `src/audio.h`:

```c
#pragma once
#include "driver/i2s_std.h"

// Brings up I2S0 (mic RX) and I2S1 (DAC TX) and enables the amplifier.
void board_init(void);

i2s_chan_handle_t audio_mic_handle(void);
i2s_chan_handle_t audio_dac_handle(void);
```

In `src/audio.c`: delete every include of `esp_capture*`, `av_render*`, `esp_codec_dev*`, `esp_audio_enc/dec*`. Delete `mute_wrapper_t` and its eight callbacks, `direct_i2s_render_t` and its eight callbacks, `create_direct_i2s_render`, `create_mute_wrapper`, `media_init`, `media_get_capturer`, `media_get_renderer`, `media_set_mic_muted`, `media_is_mic_muted`, `media_set_mic_gain`, `media_get_mic_gain`, `media_set_speaker_volume`, `media_get_speaker_volume`, `media_load_audio_config`, and `audio_test_tone`.

Keep `board_init()` exactly as it stands (lines 256–372) — the INMP441 slot configuration is already correct — and append:

```c
i2s_chan_handle_t audio_mic_handle(void) { return mic_rx_handle; }
i2s_chan_handle_t audio_dac_handle(void) { return dac_tx_handle; }
```

Also delete the now-unused `record_handle` / `playback_handle` codec devices and the `esp_codec_dev` blocks that create them (lines 333–369).

- [ ] **Step 8: Reduce main.c to boot + portal**

Replace `src/main.c`:

```c
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "nvs_config.h"
#include "audio.h"
#include "wifi_manager.h"
#include "config_server.h"

static const char *TAG = "main";

static void on_wifi_state_changed(wifi_manager_state_t state, void *ctx)
{
    switch (state) {
        case WIFI_MGR_STATE_CONNECTED:
            ESP_LOGI(TAG, "WLAN verbunden");
            config_server_start();
            break;
        case WIFI_MGR_STATE_PORTAL:
            ESP_LOGI(TAG, "Captive Portal aktiv");
            config_server_start();
            break;
        case WIFI_MGR_STATE_FAILED:
            ESP_LOGW(TAG, "WLAN-Verbindung fehlgeschlagen");
            break;
        default:
            break;
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "MrVoice startet");

    if (nvs_config_init() != ESP_OK) {
        ESP_LOGE(TAG, "NVS-Konfiguration fehlgeschlagen");
        return;
    }

    board_init();

    wifi_manager_init(on_wifi_state_changed, NULL);
    wifi_manager_start();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
```

- [ ] **Step 9: Build**

Run: `pio run -e esp32s3`
Expected: SUCCESS. The binary should be dramatically smaller than the LiveKit build — under 1 MB.

- [ ] **Step 10: Flash and check the boot log**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: `MrVoice startet`, NVS init, `Board initialized`, then either a WiFi connection or `Captive Portal aktiv`. No `esp_peer`, `livekit`, or `av_render` lines anywhere.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor: strip LiveKit stack down to board bring-up"
```

---

### Task 2: Host test harness and the WAV container

First testable code. Sets up `pio test -e native` so the three pure modules can be verified on the host, then implements the WAV header.

**Files:**
- Create: `src/wav.c`, `src/wav.h`, `test/test_wav/test_wav.c`
- Modify: `platformio.ini`, `src/CMakeLists.txt`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `size_t wav_write_header(uint8_t *dst, uint32_t sample_rate, uint16_t channels, uint16_t bits, uint32_t pcm_bytes)` — writes exactly 44 bytes, returns `WAV_HEADER_SIZE`.
  - `bool wav_parse_header(const uint8_t *src, size_t len, wav_info_t *out)` — walks RIFF chunks; returns false if `fmt `/`data` are absent or the buffer is short.
  - `wav_info_t { uint32_t sample_rate; uint16_t channels; uint16_t bits_per_sample; uint32_t data_offset; uint32_t data_bytes; }`
  - `#define WAV_HEADER_SIZE 44`

- [ ] **Step 1: Add the native test environment**

Append to `platformio.ini`:

```ini
[env:native]
platform = native
test_framework = unity
build_flags = -std=c11 -Isrc -DUNIT_TEST
test_build_src = yes
build_src_filter = +<wav.c> +<stream.c> +<speakable.c>
```

`build_src_filter` names files that do not exist yet; they arrive in Tasks 3 and 4. Until then this environment only compiles `wav.c`.

`native` is a **test-only** environment with no `main()` of its own, so plain `pio run` will fail on it. Always build the firmware with `pio run -e esp32s3` from here on.

- [ ] **Step 2: Write the failing test**

Create `test/test_wav/test_wav.c`:

```c
#include <unity.h>
#include <string.h>
#include "wav.h"

void setUp(void) {}
void tearDown(void) {}

static void test_header_is_44_bytes(void)
{
    uint8_t buf[64];
    TEST_ASSERT_EQUAL_UINT32(44, wav_write_header(buf, 16000, 1, 16, 32000));
}

static void test_header_fields(void)
{
    uint8_t buf[64];
    wav_write_header(buf, 16000, 1, 16, 32000);

    TEST_ASSERT_EQUAL_MEMORY("RIFF", buf, 4);
    TEST_ASSERT_EQUAL_MEMORY("WAVE", buf + 8, 4);
    TEST_ASSERT_EQUAL_MEMORY("fmt ", buf + 12, 4);
    TEST_ASSERT_EQUAL_MEMORY("data", buf + 36, 4);

    // RIFF size = 36 + pcm_bytes
    TEST_ASSERT_EQUAL_UINT32(36 + 32000, buf[4] | (buf[5]<<8) | (buf[6]<<16) | ((uint32_t)buf[7]<<24));
    // byte rate = 16000 * 1 * 2
    TEST_ASSERT_EQUAL_UINT32(32000, buf[28] | (buf[29]<<8) | (buf[30]<<16) | ((uint32_t)buf[31]<<24));
    // block align = channels * bits/8
    TEST_ASSERT_EQUAL_UINT16(2, buf[32] | (buf[33]<<8));
}

static void test_roundtrip(void)
{
    uint8_t buf[64];
    wav_info_t info;
    wav_write_header(buf, 16000, 1, 16, 32000);

    TEST_ASSERT_TRUE(wav_parse_header(buf, 44, &info));
    TEST_ASSERT_EQUAL_UINT32(16000, info.sample_rate);
    TEST_ASSERT_EQUAL_UINT16(1, info.channels);
    TEST_ASSERT_EQUAL_UINT16(16, info.bits_per_sample);
    TEST_ASSERT_EQUAL_UINT32(44, info.data_offset);
    TEST_ASSERT_EQUAL_UINT32(32000, info.data_bytes);
}

// Piper emits 22050 Hz mono. FIXTURE-UNVERIFIED: header synthesised, not captured.
static void test_parse_piper_rate(void)
{
    uint8_t buf[64];
    wav_info_t info;
    wav_write_header(buf, 22050, 1, 16, 100);

    TEST_ASSERT_TRUE(wav_parse_header(buf, 44, &info));
    TEST_ASSERT_EQUAL_UINT32(22050, info.sample_rate);
}

// A LIST chunk before "data" must not break parsing.
static void test_parse_skips_unknown_chunks(void)
{
    uint8_t buf[80];
    wav_info_t info;
    memset(buf, 0, sizeof(buf));

    memcpy(buf, "RIFF", 4);
    buf[4] = 72;
    memcpy(buf + 8, "WAVE", 4);
    memcpy(buf + 12, "fmt ", 4);
    buf[16] = 16;
    buf[20] = 1; buf[22] = 1;
    buf[24] = 0x22; buf[25] = 0x56;   // 22050
    buf[34] = 16;
    memcpy(buf + 36, "LIST", 4);
    buf[40] = 4;
    memcpy(buf + 48, "data", 4);
    buf[52] = 10;

    TEST_ASSERT_TRUE(wav_parse_header(buf, 80, &info));
    TEST_ASSERT_EQUAL_UINT32(22050, info.sample_rate);
    TEST_ASSERT_EQUAL_UINT32(56, info.data_offset);
    TEST_ASSERT_EQUAL_UINT32(10, info.data_bytes);
}

static void test_parse_rejects_short_buffer(void)
{
    uint8_t buf[8] = {'R','I','F','F',0,0,0,0};
    wav_info_t info;
    TEST_ASSERT_FALSE(wav_parse_header(buf, 8, &info));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_header_is_44_bytes);
    RUN_TEST(test_header_fields);
    RUN_TEST(test_roundtrip);
    RUN_TEST(test_parse_piper_rate);
    RUN_TEST(test_parse_skips_unknown_chunks);
    RUN_TEST(test_parse_rejects_short_buffer);
    return UNITY_END();
}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pio test -e native -f test_wav`
Expected: FAIL — `wav.h: No such file or directory`.

- [ ] **Step 4: Write the header**

Create `src/wav.h`:

```c
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#define WAV_HEADER_SIZE 44

typedef struct {
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t bits_per_sample;
    uint32_t data_offset;   // byte offset of PCM data from the start of the stream
    uint32_t data_bytes;    // declared length of the PCM payload
} wav_info_t;

// Writes a canonical 44-byte PCM header into dst. Returns WAV_HEADER_SIZE.
size_t wav_write_header(uint8_t *dst, uint32_t sample_rate, uint16_t channels,
                        uint16_t bits, uint32_t pcm_bytes);

// Walks RIFF chunks looking for "fmt " and "data". Returns false if either is
// missing or the buffer ends early.
bool wav_parse_header(const uint8_t *src, size_t len, wav_info_t *out);
```

- [ ] **Step 5: Write the implementation**

Create `src/wav.c`:

```c
#include "wav.h"
#include <string.h>

static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}

static uint32_t get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

size_t wav_write_header(uint8_t *dst, uint32_t sample_rate, uint16_t channels,
                        uint16_t bits, uint32_t pcm_bytes)
{
    uint16_t block_align = (uint16_t)(channels * (bits / 8));
    uint32_t byte_rate = sample_rate * block_align;

    memcpy(dst, "RIFF", 4);
    put_u32(dst + 4, 36 + pcm_bytes);
    memcpy(dst + 8, "WAVE", 4);

    memcpy(dst + 12, "fmt ", 4);
    put_u32(dst + 16, 16);           // PCM fmt chunk size
    put_u16(dst + 20, 1);            // format = PCM
    put_u16(dst + 22, channels);
    put_u32(dst + 24, sample_rate);
    put_u32(dst + 28, byte_rate);
    put_u16(dst + 32, block_align);
    put_u16(dst + 34, bits);

    memcpy(dst + 36, "data", 4);
    put_u32(dst + 40, pcm_bytes);

    return WAV_HEADER_SIZE;
}

bool wav_parse_header(const uint8_t *src, size_t len, wav_info_t *out)
{
    if (src == NULL || out == NULL || len < 12) return false;
    if (memcmp(src, "RIFF", 4) != 0 || memcmp(src + 8, "WAVE", 4) != 0) return false;

    bool have_fmt = false;
    size_t pos = 12;

    while (pos + 8 <= len) {
        const uint8_t *id = src + pos;
        uint32_t size = get_u32(src + pos + 4);
        size_t body = pos + 8;

        if (memcmp(id, "fmt ", 4) == 0) {
            if (body + 16 > len) return false;
            out->channels        = get_u16(src + body + 2);
            out->sample_rate     = get_u32(src + body + 4);
            out->bits_per_sample = get_u16(src + body + 14);
            have_fmt = true;
        } else if (memcmp(id, "data", 4) == 0) {
            if (!have_fmt) return false;
            out->data_offset = (uint32_t)body;
            out->data_bytes  = size;
            return true;
        }

        pos = body + size + (size & 1u);   // chunks are word-aligned
    }

    return false;
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pio test -e native -f test_wav`
Expected: PASS, 6 tests.

- [ ] **Step 7: Add wav.c to the firmware build**

In `src/CMakeLists.txt`, add `"wav.c"` to `SRCS`.

- [ ] **Step 8: Verify the firmware still builds**

Run: `pio run -e esp32s3`
Expected: SUCCESS.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: WAV header writer and chunk-walking parser with host tests"
```

---

### Task 3: Sentence streamer

Ports `stream.go` (B.6). Cuts the chat token stream into speakable sentences so TTS can start on the first one while the model is still writing. This is the module that determines the latency figure A.6 measures.

**Files:**
- Create: `src/stream.c`, `src/stream.h`, `test/test_stream/test_stream.c`
- Modify: `src/CMakeLists.txt`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `typedef void (*stream_sentence_fn)(const char *sentence, void *user);`
  - `bool stream_init(stream_t *s, stream_sentence_fn cb, void *user);`
  - `bool stream_feed(stream_t *s, const char *data, size_t len);` — may fire the callback zero or more times
  - `void stream_close(stream_t *s);` — flushes whatever remains as a final sentence
  - `void stream_free(stream_t *s);`

**Behaviour being ported (B.6), stated exactly:**
1. Emit as soon as one of `.` `!` `?` `…` `:` is followed by a space or newline.
2. The last character of the buffer never counts as a terminator — the sentence may be unfinished.
3. An open think block (`⟦think⟧` with no `⟦/think⟧`) holds back *all* output.
4. An open code fence (an odd number of ```` ``` ````) holds back *all* output.
5. `stream_close()` flushes the remainder.
6. Sentences are whitespace-trimmed; empty ones are never emitted.

- [ ] **Step 1: Write the failing test**

Create `test/test_stream/test_stream.c`:

```c
#include <unity.h>
#include <string.h>
#include <stdlib.h>
#include "stream.h"

#define MAX_OUT 16
static char out[MAX_OUT][512];
static int out_n;

static void collect(const char *s, void *user) {
    (void)user;
    if (out_n < MAX_OUT) { strncpy(out[out_n], s, 511); out[out_n][511] = 0; out_n++; }
}

void setUp(void) { out_n = 0; memset(out, 0, sizeof(out)); }
void tearDown(void) {}

static void feed(stream_t *s, const char *text) { stream_feed(s, text, strlen(text)); }

static void test_emits_on_period_space(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Hallo Welt. Zweiter Satz");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Hallo Welt.", out[0]);
    stream_free(&s);
}

// Rule 2: a terminator as the final byte must not fire.
static void test_terminator_at_end_does_not_emit(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Noch nicht fertig.");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_all_terminators(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Eins! Zwei? Drei: Vier… x");
    TEST_ASSERT_EQUAL_INT(4, out_n);
    TEST_ASSERT_EQUAL_STRING("Eins!", out[0]);
    TEST_ASSERT_EQUAL_STRING("Zwei?", out[1]);
    TEST_ASSERT_EQUAL_STRING("Drei:", out[2]);
    TEST_ASSERT_EQUAL_STRING("Vier…", out[3]);
    stream_free(&s);
}

static void test_newline_also_terminates(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Satz eins.\nRest");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Satz eins.", out[0]);
    stream_free(&s);
}

// Arrives one token at a time, as the chat endpoint delivers it.
static void test_token_by_token(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    const char *toks[] = {"Das", " ist", " ein", " Satz.", " Und", " noch", " einer."};
    for (int i = 0; i < 7; i++) feed(&s, toks[i]);
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Das ist ein Satz.", out[0]);
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(2, out_n);
    TEST_ASSERT_EQUAL_STRING("Und noch einer.", out[1]);
    stream_free(&s);
}

// Rule 3: an unterminated think block gags the streamer entirely.
static void test_open_think_block_holds_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "⟦think⟧ Ich denke nach. Immer noch. ");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_closed_think_block_releases_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "⟦think⟧ nachdenken ⟦/think⟧ Antwort hier. x");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    stream_free(&s);
}

// Rule 4: odd number of fences = open.
static void test_open_code_fence_holds_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Hier Code:\n```c\nint x = 1. y\n");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_closed_code_fence_releases_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Code:\n```c\nint x = 1;\n```\nFertig. x");
    TEST_ASSERT_TRUE(out_n >= 1);
    stream_free(&s);
}

static void test_close_flushes_remainder(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Kein Satzende hier");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Kein Satzende hier", out[0]);
    stream_free(&s);
}

static void test_close_on_empty_emits_nothing(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "   \n  ");
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_sentences_are_trimmed(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "   Erster Satz.    Zweiter Satz. x");
    TEST_ASSERT_EQUAL_INT(2, out_n);
    TEST_ASSERT_EQUAL_STRING("Erster Satz.", out[0]);
    TEST_ASSERT_EQUAL_STRING("Zweiter Satz.", out[1]);
    stream_free(&s);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_emits_on_period_space);
    RUN_TEST(test_terminator_at_end_does_not_emit);
    RUN_TEST(test_all_terminators);
    RUN_TEST(test_newline_also_terminates);
    RUN_TEST(test_token_by_token);
    RUN_TEST(test_open_think_block_holds_output);
    RUN_TEST(test_closed_think_block_releases_output);
    RUN_TEST(test_open_code_fence_holds_output);
    RUN_TEST(test_closed_code_fence_releases_output);
    RUN_TEST(test_close_flushes_remainder);
    RUN_TEST(test_close_on_empty_emits_nothing);
    RUN_TEST(test_sentences_are_trimmed);
    return UNITY_END();
}
```

All fixtures in this file are `FIXTURE-UNVERIFIED` — derived from B.6's prose, not from `voice_test.go`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pio test -e native -f test_stream`
Expected: FAIL — `stream.h: No such file or directory`.

- [ ] **Step 3: Write the header**

Create `src/stream.h`:

```c
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef void (*stream_sentence_fn)(const char *sentence, void *user);

typedef struct {
    char              *buf;
    size_t             len;
    size_t             cap;
    stream_sentence_fn cb;
    void              *user;
} stream_t;

bool stream_init(stream_t *s, stream_sentence_fn cb, void *user);

// Appends data and emits every complete sentence it now contains.
bool stream_feed(stream_t *s, const char *data, size_t len);

// Emits whatever is left as a final sentence.
void stream_close(stream_t *s);

void stream_free(stream_t *s);
```

- [ ] **Step 4: Write the implementation**

Create `src/stream.c`:

```c
#include "stream.h"
#include <stdlib.h>
#include <string.h>

#define THINK_OPEN  "⟦think⟧"
#define THINK_CLOSE "⟦/think⟧"
#define ELLIPSIS_2  0xE2
#define ELLIPSIS_1  0x80
#define ELLIPSIS_0  0xA6                                   // U+2026 as E2 80 A6

static bool is_space(char c)
{
    return c == ' ' || c == '\n' || c == '\r' || c == '\t';
}

// A sentence terminator whose final byte sits at index i.
static bool terminator_ends_at(const char *b, size_t i)
{
    char c = b[i];
    if (c == '.' || c == '!' || c == '?' || c == ':') return true;
    if (i >= 2 && (unsigned char)b[i - 2] == ELLIPSIS_2 &&
                  (unsigned char)b[i - 1] == ELLIPSIS_1 &&
                  (unsigned char)b[i]     == ELLIPSIS_0) return true;
    return false;
}

static size_t count_occurrences(const char *hay, size_t hay_len, const char *needle)
{
    size_t nl = strlen(needle), n = 0;
    if (nl == 0 || hay_len < nl) return 0;
    for (size_t i = 0; i + nl <= hay_len; i++) {
        if (memcmp(hay + i, needle, nl) == 0) { n++; i += nl - 1; }
    }
    return n;
}

// Rules 3 and 4: any unclosed construct gags the whole stream.
static bool output_is_gagged(const stream_t *s)
{
    if (count_occurrences(s->buf, s->len, THINK_OPEN) >
        count_occurrences(s->buf, s->len, THINK_CLOSE)) return true;
    if (count_occurrences(s->buf, s->len, "```") % 2u == 1u) return true;
    return false;
}

static void emit_trimmed(stream_t *s, const char *start, size_t len)
{
    while (len > 0 && is_space(*start)) { start++; len--; }
    while (len > 0 && is_space(start[len - 1])) len--;
    if (len == 0) return;

    char *sentence = malloc(len + 1);
    if (sentence == NULL) return;
    memcpy(sentence, start, len);
    sentence[len] = '\0';
    s->cb(sentence, s->user);
    free(sentence);
}

static void consume(stream_t *s, size_t upto)
{
    memmove(s->buf, s->buf + upto, s->len - upto);
    s->len -= upto;
}

bool stream_init(stream_t *s, stream_sentence_fn cb, void *user)
{
    s->cap = 1024;
    s->buf = malloc(s->cap);
    if (s->buf == NULL) return false;
    s->len = 0;
    s->cb = cb;
    s->user = user;
    return true;
}

bool stream_feed(stream_t *s, const char *data, size_t len)
{
    if (s->len + len + 1 > s->cap) {
        size_t cap = s->cap;
        while (cap < s->len + len + 1) cap *= 2;
        char *grown = realloc(s->buf, cap);
        if (grown == NULL) return false;
        s->buf = grown;
        s->cap = cap;
    }
    memcpy(s->buf + s->len, data, len);
    s->len += len;

    if (output_is_gagged(s)) return true;

    // Rule 2: never consider the final byte as a terminator, so stop at len-2.
    for (size_t i = 0; s->len >= 2 && i <= s->len - 2; i++) {
        if (terminator_ends_at(s->buf, i) && is_space(s->buf[i + 1])) {
            emit_trimmed(s, s->buf, i + 1);
            size_t skip = i + 1;
            while (skip < s->len && is_space(s->buf[skip])) skip++;
            consume(s, skip);
            i = (size_t)-1;   // restart the scan on the shortened buffer
        }
    }
    return true;
}

void stream_close(stream_t *s)
{
    if (s->len > 0) {
        emit_trimmed(s, s->buf, s->len);
        s->len = 0;
    }
}

void stream_free(stream_t *s)
{
    free(s->buf);
    s->buf = NULL;
    s->len = s->cap = 0;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pio test -e native -f test_stream`
Expected: PASS, 12 tests.

- [ ] **Step 6: Add stream.c to the firmware build**

In `src/CMakeLists.txt`, add `"stream.c"` to `SRCS`.

- [ ] **Step 7: Verify the firmware still builds**

Run: `pio run -e esp32s3`
Expected: SUCCESS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: port sentence streamer from stream.go with host tests"
```

---

### Task 4: Read-aloud filter

Ports `speakable()` from `manager.go` (B.6). Everything the streamer emits passes through this before it reaches TTS. Implemented as sequential state scans — the brief explicitly forbids regex.

**Files:**
- Create: `src/speakable.c`, `src/speakable.h`, `test/test_speakable/test_speakable.c`
- Modify: `src/CMakeLists.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: `char *speakable(const char *input);` — returns a newly allocated string the caller must `free()`, or `NULL` when nothing speakable remains.

**Behaviour being ported (B.6), in application order:**
1. Think blocks removed, including an unterminated one (to end of input).
2. Fenced code blocks replaced by the literal `Codeblock übersprungen`.
3. Lines beginning with `🔧` removed entirely.
4. `[text](url)` reduced to `text`.
5. Bare URLs dropped.
6. Decoration characters `*` `_` `` ` `` `#` `>` `|` dropped.
7. Runs of spaces and blank lines collapsed; result trimmed.
8. An empty result returns `NULL` and is never spoken.

- [ ] **Step 1: Write the failing test**

Create `test/test_speakable/test_speakable.c`:

```c
#include <unity.h>
#include <string.h>
#include <stdlib.h>
#include "speakable.h"

void setUp(void) {}
void tearDown(void) {}

static void check(const char *in, const char *expect)
{
    char *got = speakable(in);
    if (expect == NULL) {
        TEST_ASSERT_NULL(got);
    } else {
        TEST_ASSERT_NOT_NULL(got);
        TEST_ASSERT_EQUAL_STRING(expect, got);
    }
    free(got);
}

static void test_plain_text_survives(void)      { check("Hallo Welt.", "Hallo Welt."); }
static void test_think_block_removed(void)      { check("⟦think⟧geheim⟦/think⟧Antwort", "Antwort"); }
static void test_unterminated_think_removed(void){ check("Antwort ⟦think⟧ offen bis Ende", "Antwort"); }
static void test_tool_line_removed(void)        { check("🔧 tool_call(x)\nEchter Satz.", "Echter Satz."); }
static void test_code_block_replaced(void)      { check("Vorher.\n```c\nint x;\n```\nNachher.", "Vorher. Codeblock übersprungen Nachher."); }
static void test_link_keeps_text(void)          { check("Siehe [die Doku](https://example.com/a) dort.", "Siehe die Doku dort."); }
static void test_bare_url_dropped(void)         { check("Quelle https://example.com/x ist gut.", "Quelle ist gut."); }
static void test_www_url_dropped(void)          { check("Siehe www.example.com hier.", "Siehe hier."); }
static void test_decorations_dropped(void)      { check("**fett** _kursiv_ `code` # H1 > Zitat | Tab", "fett kursiv code H1 Zitat Tab"); }
static void test_whitespace_collapsed(void)     { check("Zu    viel\n\n\nLuft.", "Zu viel Luft."); }
static void test_empty_returns_null(void)       { check("   \n  ", NULL); }
static void test_only_decorations_returns_null(void) { check("*** ___ ###", NULL); }
static void test_only_think_returns_null(void)  { check("⟦think⟧nur denken⟦/think⟧", NULL); }

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_plain_text_survives);
    RUN_TEST(test_think_block_removed);
    RUN_TEST(test_unterminated_think_removed);
    RUN_TEST(test_tool_line_removed);
    RUN_TEST(test_code_block_replaced);
    RUN_TEST(test_link_keeps_text);
    RUN_TEST(test_bare_url_dropped);
    RUN_TEST(test_www_url_dropped);
    RUN_TEST(test_decorations_dropped);
    RUN_TEST(test_whitespace_collapsed);
    RUN_TEST(test_empty_returns_null);
    RUN_TEST(test_only_decorations_returns_null);
    RUN_TEST(test_only_think_returns_null);
    return UNITY_END();
}
```

All fixtures are `FIXTURE-UNVERIFIED` — derived from B.6's prose, not from the Go original.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pio test -e native -f test_speakable`
Expected: FAIL — `speakable.h: No such file or directory`.

- [ ] **Step 3: Write the header**

Create `src/speakable.h`:

```c
#pragma once

// Reduces model output to something worth reading aloud.
// Returns a malloc'd string the caller frees, or NULL if nothing remains.
char *speakable(const char *input);
```

- [ ] **Step 4: Write the implementation**

Create `src/speakable.c`:

```c
#include "speakable.h"
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define THINK_OPEN  "⟦think⟧"
#define THINK_CLOSE "⟦/think⟧"
#define TOOL_MARK   "🔧"
#define CODE_NOTE   "Codeblock übersprungen"

typedef struct { char *p; size_t len, cap; } sbuf_t;

static bool sb_init(sbuf_t *b, size_t cap)
{
    b->p = malloc(cap); b->len = 0; b->cap = cap;
    return b->p != NULL;
}

static bool sb_putn(sbuf_t *b, const char *s, size_t n)
{
    if (b->len + n + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 64;
        while (cap < b->len + n + 1) cap *= 2;
        char *g = realloc(b->p, cap);
        if (g == NULL) return false;
        b->p = g; b->cap = cap;
    }
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = '\0';
    return true;
}

static bool sb_put(sbuf_t *b, const char *s) { return sb_putn(b, s, strlen(s)); }
static bool sb_putc(sbuf_t *b, char c)       { return sb_putn(b, &c, 1); }

static bool starts_with(const char *s, const char *pre)
{
    return strncmp(s, pre, strlen(pre)) == 0;
}

// Rules 1 and 2: drop think blocks, turn fenced code into a spoken note.
static char *strip_blocks(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 64)) return NULL;

    const char *p = in;
    while (*p) {
        if (starts_with(p, THINK_OPEN)) {
            const char *end = strstr(p, THINK_CLOSE);
            if (end == NULL) break;                    // unterminated: drop the rest
            p = end + strlen(THINK_CLOSE);
            continue;
        }
        if (starts_with(p, "```")) {
            const char *end = strstr(p + 3, "```");
            sb_put(&out, " " CODE_NOTE " ");
            if (end == NULL) break;                    // unterminated fence: drop the rest
            p = end + 3;
            continue;
        }
        sb_putc(&out, *p++);
    }
    return out.p;
}

// Rule 3: a line whose first non-space glyph is 🔧 disappears.
static char *strip_tool_lines(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    const char *line = in;
    while (*line) {
        const char *nl = strchr(line, '\n');
        size_t n = nl ? (size_t)(nl - line) : strlen(line);

        const char *t = line;
        while (t < line + n && (*t == ' ' || *t == '\t')) t++;

        if (!starts_with(t, TOOL_MARK)) {
            sb_putn(&out, line, n);
            sb_putc(&out, '\n');
        }
        if (nl == NULL) break;
        line = nl + 1;
    }
    return out.p;
}

static bool url_char(char c)
{
    return c != ' ' && c != '\t' && c != '\n' && c != '\r' && c != '\0' && c != ')';
}

// Rules 4 and 5: [text](url) keeps text; a bare URL vanishes.
static char *strip_links(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    const char *p = in;
    while (*p) {
        if (*p == '[') {
            const char *close = strchr(p, ']');
            if (close != NULL && close[1] == '(') {
                const char *paren = strchr(close + 2, ')');
                if (paren != NULL) {
                    sb_putn(&out, p + 1, (size_t)(close - p - 1));
                    p = paren + 1;
                    continue;
                }
            }
        }
        if (starts_with(p, "http://") || starts_with(p, "https://") || starts_with(p, "www.")) {
            while (url_char(*p)) p++;
            continue;
        }
        sb_putc(&out, *p++);
    }
    return out.p;
}

// Rules 6 and 7: decorations go, whitespace collapses, result is trimmed.
static char *strip_decoration_and_collapse(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    bool pending_space = false;
    for (const char *p = in; *p; p++) {
        char c = *p;
        if (c == '*' || c == '_' || c == '`' || c == '#' || c == '>' || c == '|') continue;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { pending_space = true; continue; }
        if (pending_space && out.len > 0) sb_putc(&out, ' ');
        pending_space = false;
        sb_putc(&out, c);
    }
    return out.p;
}

char *speakable(const char *input)
{
    if (input == NULL) return NULL;

    char *a = strip_blocks(input);                    if (a == NULL) return NULL;
    char *b = strip_tool_lines(a);         free(a);   if (b == NULL) return NULL;
    char *c = strip_links(b);              free(b);   if (c == NULL) return NULL;
    char *d = strip_decoration_and_collapse(c); free(c);
    if (d == NULL) return NULL;

    if (d[0] == '\0') { free(d); return NULL; }       // rule 8
    return d;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pio test -e native -f test_speakable`
Expected: PASS, 13 tests.

- [ ] **Step 6: Run the whole host suite**

Run: `pio test -e native`
Expected: PASS — 6 + 12 + 13 = 31 tests across three suites.

- [ ] **Step 7: Add speakable.c to the firmware build**

In `src/CMakeLists.txt`, add `"speakable.c"` to `SRCS`.

- [ ] **Step 8: Verify the firmware still builds**

Run: `pio run -e esp32s3`
Expected: SUCCESS.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: port speakable() read-aloud filter with host tests"
```

---

### Task 5: Configuration model and web UI

Re-key NVS from LiveKit fields to kAIm56 fields and replace the LiveKit page in the config portal with a manager page. Everything after this task reads its settings from here.

**Files:**
- Modify: `src/config.h`, `src/nvs_config.h`, `src/nvs_config.c`, `src/config_server.c`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `const mrvoice_config_t *nvs_config_get(void)` with the fields below.
  - `esp_err_t nvs_config_save_manager(const char *base_url, const char *user, const char *pass, const char *instance, const char *prompt);`
  - `esp_err_t nvs_config_save_recording(uint16_t min_ms, uint16_t max_s);`

- [ ] **Step 1: Replace the defaults in config.h**

In `src/config.h`, delete every `DEFAULT_LIVEKIT_*` macro and the `LIVEKIT_*` / `USE_LIVEKIT_SANDBOX` compatibility aliases, plus `DEFAULT_MIC_GAIN`, `DEFAULT_SPEAKER_VOLUME`, `MIC_GAIN`, `SPEAKER_VOLUME`. Keep the WiFi and GPIO macros as they are. Add:

```c
// kAIm56 manager defaults
#define DEFAULT_BASE_URL   ""
#define DEFAULT_MGR_USER   ""
#define DEFAULT_MGR_PASS   ""
#define DEFAULT_INSTANCE   ""
#define DEFAULT_PROMPT     "Du wirst über einen Sprachclient bedient: antworte kurz und in vorlesbarer Prosa, ohne Listen, Links oder Code."

// Recording guards (the VAD remnants — see spec D1)
#define DEFAULT_MIN_MS     400    // shorter recordings are discarded
#define DEFAULT_MAX_S      30     // recording cap
```

- [ ] **Step 2: Replace the config struct**

In `src/nvs_config.h`, replace the length macros and the LiveKit fields of `mrvoice_config_t`:

```c
#define NVS_CFG_URL_MAX_LEN       128
#define NVS_CFG_USER_MAX_LEN       32
#define NVS_CFG_PASS_MAX_LEN       64
#define NVS_CFG_INSTANCE_MAX_LEN   48
#define NVS_CFG_PROMPT_MAX_LEN    512
```

The struct becomes:

```c
typedef struct {
    uint8_t version;

    char    wifi_ssid[NVS_CFG_WIFI_SSID_LEN];
    char    wifi_pass[NVS_CFG_WIFI_PASS_LEN];
    bool    wifi_configured;

    char    base_url[NVS_CFG_URL_MAX_LEN];
    char    user[NVS_CFG_USER_MAX_LEN];
    char    pass[NVS_CFG_PASS_MAX_LEN];
    char    instance[NVS_CFG_INSTANCE_MAX_LEN];
    char    prompt[NVS_CFG_PROMPT_MAX_LEN];
    bool    mgr_configured;

    uint16_t min_ms;
    uint16_t max_s;

    mrvoice_gpio_config_t gpio;
} mrvoice_config_t;
```

Declare the two new savers and delete `nvs_config_save_livekit` and `nvs_config_save_audio`.

- [ ] **Step 3: Update nvs_config.c**

Bump `CONFIG_VERSION` to `2` — a version mismatch already falls back to defaults, so old LiveKit configs are abandoned cleanly. Replace the NVS key macros:

```c
#define NVS_KEY_BASE_URL   "base_url"
#define NVS_KEY_MGR_USER   "mgr_user"
#define NVS_KEY_MGR_PASS   "mgr_pass"
#define NVS_KEY_INSTANCE   "instance"
#define NVS_KEY_PROMPT     "prompt"
#define NVS_KEY_MIN_MS     "min_ms"
#define NVS_KEY_MAX_S      "max_s"
```

Update `load_defaults()`, `load_from_nvs()`, and `nvs_config_save()` to match, and add:

```c
esp_err_t nvs_config_save_manager(const char *base_url, const char *user,
                                  const char *pass, const char *instance,
                                  const char *prompt)
{
    if (base_url) strlcpy(g_config.base_url, base_url, NVS_CFG_URL_MAX_LEN);
    if (user)     strlcpy(g_config.user,     user,     NVS_CFG_USER_MAX_LEN);
    if (pass)     strlcpy(g_config.pass,     pass,     NVS_CFG_PASS_MAX_LEN);
    if (instance) strlcpy(g_config.instance, instance, NVS_CFG_INSTANCE_MAX_LEN);
    if (prompt)   strlcpy(g_config.prompt,   prompt,   NVS_CFG_PROMPT_MAX_LEN);

    g_config.mgr_configured = (g_config.base_url[0] != '\0' && g_config.instance[0] != '\0');
    return nvs_config_save();
}

esp_err_t nvs_config_save_recording(uint16_t min_ms, uint16_t max_s)
{
    if (min_ms < 100)  min_ms = 100;
    if (min_ms > 5000) min_ms = 5000;
    if (max_s < 1)     max_s = 1;
    if (max_s > 30)    max_s = 30;      // 30 s is the PSRAM budget ceiling
    g_config.min_ms = min_ms;
    g_config.max_s = max_s;
    return nvs_config_save();
}
```

Use `strlcpy` throughout rather than the existing `strncpy` + manual terminator pairs.

- [ ] **Step 4: Replace the LiveKit page with a manager page**

In `src/config_server.c`, rename `handler_livekit` → `handler_manager` and `handler_livekit_save` → `handler_manager_save`, serving `/manager` and `/manager/save`. Fields: `url`, `user`, `pass`, `instance`, `prompt`. Update `send_nav()` to link `/manager` instead of `/livekit`, and change the `/audio` page from gain/volume sliders to `min_ms` and `max_s` number inputs saved via `nvs_config_save_recording`. Update the home page to report `config->mgr_configured` instead of `lk_configured`.

- [ ] **Step 5: Fix the form parser while you are in this file**

`get_form_param` (around line 61) uses `strstr(content, "name=")` across the whole body, so a value containing `key=` returns the wrong field. Replace the body-scan with a parser that walks `&`-separated pairs and compares the name up to `=`:

```c
static esp_err_t get_form_param(httpd_req_t *req, const char *content,
                                const char *param, char *value, size_t max_len)
{
    (void)req;
    size_t plen = strlen(param);
    const char *p = content;

    value[0] = '\0';
    while (*p) {
        const char *amp = strchr(p, '&');
        size_t pair_len = amp ? (size_t)(amp - p) : strlen(p);
        const char *eq = memchr(p, '=', pair_len);

        if (eq != NULL && (size_t)(eq - p) == plen && memcmp(p, param, plen) == 0) {
            size_t vlen = pair_len - plen - 1;
            if (vlen >= max_len) vlen = max_len - 1;
            memcpy(value, eq + 1, vlen);
            value[vlen] = '\0';
            url_decode(value);
            return ESP_OK;
        }
        if (amp == NULL) break;
        p = amp + 1;
    }
    return ESP_ERR_NOT_FOUND;
}
```

- [ ] **Step 6: Read the full POST body**

Every POST handler calls `httpd_req_recv` once into a fixed buffer, silently truncating longer bodies — the prompt field makes this reachable. Add a helper and use it in all three save handlers:

```c
static esp_err_t recv_body(httpd_req_t *req, char *buf, size_t cap)
{
    if (req->content_len >= cap) return ESP_ERR_INVALID_SIZE;
    size_t got = 0;
    while (got < req->content_len) {
        int r = httpd_req_recv(req, buf + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) return ESP_FAIL;
        got += (size_t)r;
    }
    buf[got] = '\0';
    return ESP_OK;
}
```

The manager save handler needs an 1024-byte buffer to hold the prompt.

- [ ] **Step 7: Build and flash**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: SUCCESS, device boots, portal reachable.

- [ ] **Step 8: Exercise the page by hand**

Open the device's IP, go to **Manager**, enter a base URL, instance, and a prompt containing an `&` and an `=`. Save, reload the page.
Expected: every field comes back exactly as entered — this is what proves Steps 5 and 6.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: re-key config for kAIm56 manager; fix form parsing and body reads"
```

---

### Task 6: Manager HTTP client

The four endpoints from A.3. The contract is unusual in three ways and each one has to be honoured exactly: HTTP/1.0 with no keep-alive, `/api/stt` demanding `Content-Length`, and `/api/chat` sending no length at all.

**Files:**
- Create: `src/manager.c`, `src/manager.h`
- Modify: `src/CMakeLists.txt`

**Interfaces:**
- Consumes: `nvs_config_get()` for `base_url`, `user`, `pass`.
- Produces:
  - `esp_err_t mgr_stt(const uint8_t *wav, size_t len, mgr_stt_result_t *out);`
  - `esp_err_t mgr_chat(const char *instance, const char *message, const char *chat_id, mgr_chat_cb on_data, void *user);`
  - `esp_err_t mgr_tts_open(const char *text, mgr_stream_t **out);`
  - `int mgr_tts_read(mgr_stream_t *s, uint8_t *buf, size_t len);` — bytes read, 0 at EOF, negative on error
  - `void mgr_tts_close(mgr_stream_t *s);`
  - `typedef void (*mgr_chat_cb)(const char *data, size_t len, void *user);`
  - `mgr_stt_result_t { char text[512]; float seconds; float took; }`

- [ ] **Step 1: Write the header**

Create `src/manager.h`:

```c
#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

typedef struct {
    char  text[512];
    float seconds;
    float took;
} mgr_stt_result_t;

typedef void (*mgr_chat_cb)(const char *data, size_t len, void *user);

typedef struct mgr_stream mgr_stream_t;

// POST /api/stt — uploads a 16 kHz s16 mono WAV. Content-Length is mandatory,
// so the whole buffer must already exist.
esp_err_t mgr_stt(const uint8_t *wav, size_t len, mgr_stt_result_t *out);

// POST /api/chat/<instance> — streams plain-text tokens until the connection
// closes. on_data may be called many times. Can run for minutes.
esp_err_t mgr_chat(const char *instance, const char *message,
                   const char *chat_id, mgr_chat_cb on_data, void *user);

// POST /api/tts — opens the WAV response for pull-based reading.
esp_err_t mgr_tts_open(const char *text, mgr_stream_t **out);
int       mgr_tts_read(mgr_stream_t *s, uint8_t *buf, size_t len);
void      mgr_tts_close(mgr_stream_t *s);
```

- [ ] **Step 2: Write the implementation**

Create `src/manager.c`:

```c
#include "manager.h"
#include "nvs_config.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_task_wdt.h"
#include "mbedtls/base64.h"
#include "cJSON.h"
#include <string.h>
#include <stdlib.h>

static const char *TAG = "manager";

#define CHAT_CHUNK   1024
#define TTS_TIMEOUT_MS   60000
#define STT_TIMEOUT_MS   60000
#define CHAT_TIMEOUT_MS 600000     // A.3: a turn may run for minutes

struct mgr_stream { esp_http_client_handle_t client; };

// Builds "Basic <base64>" once per request. Returns false when no user is set.
static bool build_auth(char *dst, size_t cap)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    if (cfg->user[0] == '\0') return false;

    char pair[NVS_CFG_USER_MAX_LEN + NVS_CFG_PASS_MAX_LEN + 2];
    int n = snprintf(pair, sizeof(pair), "%s:%s", cfg->user, cfg->pass);
    if (n <= 0 || (size_t)n >= sizeof(pair)) return false;

    unsigned char enc[256];
    size_t enc_len = 0;
    if (mbedtls_base64_encode(enc, sizeof(enc), &enc_len,
                              (const unsigned char *)pair, (size_t)n) != 0) return false;

    return (size_t)snprintf(dst, cap, "Basic %.*s", (int)enc_len, (char *)enc) < cap;
}

// Every request gets a fresh handle: the manager speaks HTTP/1.0 and closes.
static esp_http_client_handle_t open_post(const char *path, int timeout_ms,
                                          const char *content_type)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    if (cfg->base_url[0] == '\0') return NULL;

    char url[NVS_CFG_URL_MAX_LEN + 128];
    snprintf(url, sizeof(url), "%s%s", cfg->base_url, path);

    esp_http_client_config_t hc = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = timeout_ms,
        .keep_alive_enable = false,
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (c == NULL) return NULL;

    esp_http_client_set_header(c, "Content-Type", content_type);

    char auth[384];
    if (build_auth(auth, sizeof(auth))) esp_http_client_set_header(c, "Authorization", auth);

    return c;
}

// A.3: anything but 200 is an error and the body carries JSON with "error".
static esp_err_t check_status(esp_http_client_handle_t c)
{
    int status = esp_http_client_get_status_code(c);
    if (status == 200) return ESP_OK;

    char body[300] = {0};
    esp_http_client_read(c, body, sizeof(body) - 1);
    ESP_LOGE(TAG, "HTTP %d: %s", status, body);
    return ESP_FAIL;
}

esp_err_t mgr_stt(const uint8_t *wav, size_t len, mgr_stt_result_t *out)
{
    esp_http_client_handle_t c = open_post("/api/stt", STT_TIMEOUT_MS, "audio/wav");
    if (c == NULL) return ESP_ERR_INVALID_STATE;

    esp_err_t ret = ESP_FAIL;

    // Content-Length is mandatory here — chunked upload is rejected.
    if (esp_http_client_open(c, (int)len) != ESP_OK) goto done;

    for (size_t sent = 0; sent < len; ) {
        size_t block = len - sent;
        if (block > 4096) block = 4096;
        int w = esp_http_client_write(c, (const char *)wav + sent, block);
        if (w <= 0) goto done;
        sent += (size_t)w;
        esp_task_wdt_reset();
    }

    if (esp_http_client_fetch_headers(c) < 0) goto done;
    if (check_status(c) != ESP_OK) goto done;

    char body[768] = {0};
    int n = esp_http_client_read_response(c, body, sizeof(body) - 1);
    if (n <= 0) goto done;
    body[n] = '\0';

    cJSON *root = cJSON_Parse(body);
    if (root == NULL) goto done;

    const cJSON *text = cJSON_GetObjectItem(root, "text");
    if (cJSON_IsString(text)) strlcpy(out->text, text->valuestring, sizeof(out->text));
    const cJSON *secs = cJSON_GetObjectItem(root, "seconds");
    out->seconds = cJSON_IsNumber(secs) ? (float)secs->valuedouble : 0.0f;
    const cJSON *took = cJSON_GetObjectItem(root, "took");
    out->took = cJSON_IsNumber(took) ? (float)took->valuedouble : 0.0f;

    cJSON_Delete(root);
    ret = ESP_OK;

done:
    esp_http_client_cleanup(c);
    return ret;
}

esp_err_t mgr_chat(const char *instance, const char *message,
                   const char *chat_id, mgr_chat_cb on_data, void *user)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "message", message);
    cJSON_AddStringToObject(req, "chat", chat_id);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    if (body == NULL) return ESP_ERR_NO_MEM;

    char path[NVS_CFG_INSTANCE_MAX_LEN + 32];
    snprintf(path, sizeof(path), "/api/chat/%s", instance);

    esp_http_client_handle_t c = open_post(path, CHAT_TIMEOUT_MS, "application/json");
    if (c == NULL) { free(body); return ESP_ERR_INVALID_STATE; }

    esp_err_t ret = ESP_FAIL;
    size_t body_len = strlen(body);

    if (esp_http_client_open(c, (int)body_len) != ESP_OK) goto done;
    if (esp_http_client_write(c, body, body_len) != (int)body_len) goto done;
    if (esp_http_client_fetch_headers(c) < 0) goto done;
    if (check_status(c) != ESP_OK) goto done;

    // A.3: no Content-Length, no chunked — read until the connection closes.
    char chunk[CHAT_CHUNK];
    for (;;) {
        int n = esp_http_client_read(c, chunk, sizeof(chunk));
        if (n < 0) goto done;
        if (n == 0) break;                 // connection closed = end of turn
        on_data(chunk, (size_t)n, user);
        esp_task_wdt_reset();
    }
    ret = ESP_OK;

done:
    esp_http_client_cleanup(c);
    free(body);
    return ret;
}

esp_err_t mgr_tts_open(const char *text, mgr_stream_t **out)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "text", text);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    if (body == NULL) return ESP_ERR_NO_MEM;

    esp_http_client_handle_t c = open_post("/api/tts", TTS_TIMEOUT_MS, "application/json");
    if (c == NULL) { free(body); return ESP_ERR_INVALID_STATE; }

    esp_err_t ret = ESP_FAIL;
    size_t body_len = strlen(body);

    if (esp_http_client_open(c, (int)body_len) != ESP_OK) goto fail;
    if (esp_http_client_write(c, body, body_len) != (int)body_len) goto fail;
    if (esp_http_client_fetch_headers(c) < 0) goto fail;
    if (check_status(c) != ESP_OK) goto fail;

    mgr_stream_t *s = calloc(1, sizeof(mgr_stream_t));
    if (s == NULL) goto fail;
    s->client = c;
    *out = s;
    free(body);
    return ESP_OK;

fail:
    esp_http_client_cleanup(c);
    free(body);
    return ret;
}

int mgr_tts_read(mgr_stream_t *s, uint8_t *buf, size_t len)
{
    if (s == NULL) return -1;
    return esp_http_client_read(s->client, (char *)buf, len);
}

void mgr_tts_close(mgr_stream_t *s)
{
    if (s == NULL) return;
    esp_http_client_cleanup(s->client);
    free(s);
}
```

- [ ] **Step 3: Add manager.c to the firmware build**

In `src/CMakeLists.txt`, add `"manager.c"` to `SRCS`.

- [ ] **Step 4: Build**

Run: `pio run -e esp32s3`
Expected: SUCCESS.

- [ ] **Step 5: Add a temporary smoke test to main.c**

After WiFi connects, once, call `mgr_tts_open("Testton", &s)` and log how many bytes `mgr_tts_read` returns in total, then `mgr_tts_close`. This needs a configured manager.

- [ ] **Step 6: Flash and verify against a real manager**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: a byte count in the tens of kilobytes and no HTTP error line. If the manager is unreachable, this step is **blocked** — record it and continue to Task 7, which does not need the network.

- [ ] **Step 7: Remove the smoke test and commit**

```bash
git add -A
git commit -m "feat: kAIm56 manager HTTP client for stt, chat and tts"
```

---

### Task 7: Recording into PSRAM

I2S0 capture while the button is held, straight into a PSRAM buffer that already has room for the WAV header, so the upload needs no copy.

**Files:**
- Create: `src/capture.c`, `src/capture.h`
- Modify: `src/CMakeLists.txt`

**Interfaces:**
- Consumes: `audio_mic_handle()` from Task 1, `wav_write_header()` from Task 2, `nvs_config_get()->max_s` and `->min_ms` from Task 5.
- Produces:
  - `esp_err_t capture_init(void);` — allocates `44 + max_s * 32000` bytes in PSRAM once
  - `esp_err_t capture_start(void);`
  - `bool capture_pump(void);` — drains the DMA queue; returns `false` once the cap is hit
  - `void capture_stop(void);`
  - `const uint8_t *capture_wav(size_t *total_len);` — writes the header in place, returns the buffer
  - `uint32_t capture_duration_ms(void);`

- [ ] **Step 1: Write the header**

Create `src/capture.h`:

```c
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

#define CAPTURE_SAMPLE_RATE 16000
#define CAPTURE_BYTES_PER_S (CAPTURE_SAMPLE_RATE * 2)   // s16 mono

esp_err_t capture_init(void);
esp_err_t capture_start(void);

// Drains whatever the DMA has ready. Returns false when max_s is reached.
bool capture_pump(void);

void capture_stop(void);

// Fills in the 44-byte header and hands back the complete WAV.
const uint8_t *capture_wav(size_t *total_len);

uint32_t capture_duration_ms(void);
```

- [ ] **Step 2: Write the implementation**

Create `src/capture.c`:

```c
#include "capture.h"
#include "audio.h"
#include "wav.h"
#include "nvs_config.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include <string.h>

static const char *TAG = "capture";

static uint8_t *g_buf;        // [0..43] header, [44..] PCM
static size_t   g_cap_bytes;  // PCM capacity, not counting the header
static size_t   g_len;        // PCM bytes captured so far

esp_err_t capture_init(void)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    g_cap_bytes = (size_t)cfg->max_s * CAPTURE_BYTES_PER_S;

    g_buf = heap_caps_malloc(WAV_HEADER_SIZE + g_cap_bytes, MALLOC_CAP_SPIRAM);
    if (g_buf == NULL) {
        ESP_LOGE(TAG, "Kein PSRAM für %u Byte Aufnahmepuffer",
                 (unsigned)(WAV_HEADER_SIZE + g_cap_bytes));
        return ESP_ERR_NO_MEM;
    }
    g_len = 0;
    ESP_LOGI(TAG, "Aufnahmepuffer: %u kB (max %u s)",
             (unsigned)((WAV_HEADER_SIZE + g_cap_bytes) / 1024), cfg->max_s);
    return ESP_OK;
}

esp_err_t capture_start(void)
{
    g_len = 0;
    esp_err_t ret = i2s_channel_enable(audio_mic_handle());
    return (ret == ESP_ERR_INVALID_STATE) ? ESP_OK : ret;
}

bool capture_pump(void)
{
    if (g_len >= g_cap_bytes) return false;

    size_t room = g_cap_bytes - g_len;
    size_t want = room > 4096 ? 4096 : room;
    size_t got = 0;

    // Short timeout: the caller is also polling the button.
    if (i2s_channel_read(audio_mic_handle(), g_buf + WAV_HEADER_SIZE + g_len,
                         want, &got, pdMS_TO_TICKS(20)) == ESP_OK) {
        g_len += got;
    }
    return g_len < g_cap_bytes;
}

void capture_stop(void)
{
    i2s_channel_disable(audio_mic_handle());
}

const uint8_t *capture_wav(size_t *total_len)
{
    wav_write_header(g_buf, CAPTURE_SAMPLE_RATE, 1, 16, (uint32_t)g_len);
    *total_len = WAV_HEADER_SIZE + g_len;
    return g_buf;
}

uint32_t capture_duration_ms(void)
{
    return (uint32_t)((g_len * 1000ULL) / CAPTURE_BYTES_PER_S);
}
```

- [ ] **Step 3: Add capture.c to the firmware build**

In `src/CMakeLists.txt`, add `"capture.c"` to `SRCS`.

- [ ] **Step 4: Add a temporary recording check to main.c**

After `board_init()`, call `capture_init()`, then `capture_start()`, loop `capture_pump()` for 3 seconds, `capture_stop()`, and log `capture_duration_ms()` and the total length.

- [ ] **Step 5: Flash and verify**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: `Aufnahmepuffer: 960 kB (max 30 s)` and a duration of roughly 3000 ms. A duration near 0 means I2S0 is not producing data — check the INMP441 wiring before continuing.

- [ ] **Step 6: Remove the temporary check and commit**

```bash
git add -A
git commit -m "feat: PSRAM recording buffer with in-place WAV header"
```

---

### Task 8: WAV playback

Pull-based playback to I2S1. Retunes the peripheral from each WAV header, so the 22 050 Hz Piper output plays at the right pitch without resampling.

**Files:**
- Create: `src/player.c`, `src/player.h`
- Modify: `src/CMakeLists.txt`

**Interfaces:**
- Consumes: `audio_dac_handle()` from Task 1, `wav_parse_header()` from Task 2.
- Produces:
  - `typedef int (*player_read_fn)(void *ctx, uint8_t *buf, size_t len);` — same contract as `mgr_tts_read`: bytes read, 0 at EOF, negative on error
  - `esp_err_t player_play(player_read_fn read, void *ctx);` — blocks until the source is drained or aborted
  - `void player_abort(void);` — asks a running `player_play` to stop; safe from another task

- [ ] **Step 1: Write the header**

Create `src/player.h`:

```c
#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

typedef int (*player_read_fn)(void *ctx, uint8_t *buf, size_t len);

// Reads a WAV stream through read() and plays it. Blocks until done.
esp_err_t player_play(player_read_fn read, void *ctx);

// Requests an early stop from another task.
void player_abort(void);
```

- [ ] **Step 2: Write the implementation**

Create `src/player.c`:

```c
#include "player.h"
#include "audio.h"
#include "wav.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static const char *TAG = "player";

#define BLOCK 4096

static volatile bool g_abort;
static uint32_t g_current_rate;

// Pulls exactly n bytes unless the source ends first.
static size_t read_exact(player_read_fn read, void *ctx, uint8_t *dst, size_t n)
{
    size_t got = 0;
    while (got < n) {
        int r = read(ctx, dst + got, n - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    return got;
}

static esp_err_t retune(uint32_t rate)
{
    if (rate == g_current_rate) return ESP_OK;

    i2s_chan_handle_t tx = audio_dac_handle();
    i2s_channel_disable(tx);

    i2s_std_clk_config_t clk = I2S_STD_CLK_DEFAULT_CONFIG(rate);
    esp_err_t ret = i2s_channel_reconfig_std_clock(tx, &clk);
    if (ret != ESP_OK) return ret;

    i2s_std_slot_config_t slot =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
    ret = i2s_channel_reconfig_std_slot(tx, &slot);
    if (ret != ESP_OK) return ret;

    g_current_rate = rate;
    ESP_LOGI(TAG, "I2S auf %u Hz mono umgestellt", (unsigned)rate);
    return ESP_OK;
}

void player_abort(void) { g_abort = true; }

esp_err_t player_play(player_read_fn read, void *ctx)
{
    g_abort = false;

    uint8_t head[WAV_HEADER_SIZE];
    if (read_exact(read, ctx, head, WAV_HEADER_SIZE) != WAV_HEADER_SIZE) {
        ESP_LOGE(TAG, "WAV-Header unvollständig");
        return ESP_FAIL;
    }

    wav_info_t info;
    if (!wav_parse_header(head, WAV_HEADER_SIZE, &info)) {
        ESP_LOGE(TAG, "WAV-Header ungültig");
        return ESP_FAIL;
    }

    // A non-canonical header means PCM starts later than byte 44; skip the gap.
    if (info.data_offset > WAV_HEADER_SIZE) {
        uint8_t skip[64];
        size_t remaining = info.data_offset - WAV_HEADER_SIZE;
        while (remaining > 0) {
            size_t n = remaining > sizeof(skip) ? sizeof(skip) : remaining;
            if (read_exact(read, ctx, skip, n) != n) return ESP_FAIL;
            remaining -= n;
        }
    }

    if (retune(info.sample_rate) != ESP_OK) return ESP_FAIL;

    esp_err_t ret = i2s_channel_enable(audio_dac_handle());
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) return ret;

    uint8_t block[BLOCK];
    for (;;) {
        if (g_abort) break;

        int n = read(ctx, block, sizeof(block));
        if (n < 0) { ret = ESP_FAIL; break; }
        if (n == 0) { ret = ESP_OK; break; }

        size_t written = 0;
        i2s_channel_write(audio_dac_handle(), block, (size_t)n, &written, portMAX_DELAY);
        esp_task_wdt_reset();
    }

    // Let the DMA drain before cutting the clock, or the tail is clipped.
    vTaskDelay(pdMS_TO_TICKS(40));
    i2s_channel_disable(audio_dac_handle());
    return ret;
}
```

- [ ] **Step 3: Add player.c to the firmware build**

In `src/CMakeLists.txt`, add `"player.c"` to `SRCS`.

- [ ] **Step 4: Add a temporary playback check to main.c**

Wire `mgr_tts_open("Dies ist ein Test der Sprachausgabe.", &s)` to `player_play` with a read shim that calls `mgr_tts_read`, then `mgr_tts_close`.

```c
static int tts_shim(void *ctx, uint8_t *buf, size_t len) {
    return mgr_tts_read((mgr_stream_t *)ctx, buf, len);
}
```

- [ ] **Step 5: Flash and listen**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: `I2S auf 22050 Hz mono umgestellt` and the sentence audible at correct pitch. Too fast or too slow means the retune failed. Requires a reachable manager; if none, **blocked** — record and continue.

- [ ] **Step 6: Remove the temporary check and commit**

```bash
git add -A
git commit -m "feat: pull-based WAV player with per-file I2S retuning"
```

---

### Task 9: Button gestures and LED

Replaces `button.c` with gesture recognition and adds the status LED. Per spec D3: hold = talk, tap = stop speaking, double tap = new conversation, 10 s = factory reset.

**Files:**
- Create: `src/ui.c`, `src/ui.h`
- Delete: `src/button.c`, `src/button.h`
- Modify: `src/CMakeLists.txt`, `src/config.h`

**Interfaces:**
- Consumes: `nvs_config_get()->gpio.button`.
- Produces:
  - `void ui_init(void);`
  - `ui_event_t ui_poll(void);` — call every 10 ms; returns one event per call
  - `void ui_set_state(ui_state_t s);`
  - `ui_event_t { UI_EV_NONE, UI_EV_HOLD_START, UI_EV_HOLD_END, UI_EV_TAP, UI_EV_DOUBLE_TAP, UI_EV_FACTORY_RESET }`
  - `ui_state_t { UI_IDLE, UI_RECORDING, UI_THINKING, UI_SPEAKING, UI_DISCARDED, UI_NO_NET }`

- [ ] **Step 1: Add the LED pin to config.h**

```c
// XIAO ESP32-S3 user LED — VERIFY against your board before trusting this.
#define DEFAULT_LED_GPIO   21
#define LED_ACTIVE_LOW     1
```

- [ ] **Step 2: Write the header**

Create `src/ui.h`:

```c
#pragma once
#include <stdbool.h>

typedef enum {
    UI_EV_NONE = 0,
    UI_EV_HOLD_START,      // push-to-talk begins
    UI_EV_HOLD_END,        // push-to-talk ends
    UI_EV_TAP,             // stop speaking
    UI_EV_DOUBLE_TAP,      // new conversation
    UI_EV_FACTORY_RESET,   // 10 s hold
} ui_event_t;

typedef enum {
    UI_IDLE = 0,
    UI_RECORDING,
    UI_THINKING,
    UI_SPEAKING,
    UI_DISCARDED,
    UI_NO_NET,
} ui_state_t;

void       ui_init(void);
ui_event_t ui_poll(void);        // call every 10 ms
void       ui_set_state(ui_state_t s);
```

- [ ] **Step 3: Write the implementation**

Create `src/ui.c`:

```c
#include "ui.h"
#include "config.h"
#include "nvs_config.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_timer.h"

#define DEBOUNCE_MS        30
#define HOLD_MIN_MS       250     // longer than this is a hold, not a tap
#define DOUBLE_GAP_MS     350     // window for the second tap
#define FACTORY_HOLD_MS 10000

#define LEDC_TIMER      LEDC_TIMER_0
#define LEDC_CHANNEL    LEDC_CHANNEL_0
#define LEDC_RES        LEDC_TIMER_10_BIT
#define LEDC_MAX        1023

static uint8_t  g_btn;
static bool     g_down;              // debounced button state
static bool     g_hold_active;       // HOLD_START has fired, HOLD_END has not
static bool     g_factory_fired;
static int64_t  g_edge_us;           // last debounced transition
static int64_t  g_pending_tap_us;    // >0 while waiting for a second tap

static ui_state_t g_state = UI_IDLE;
static int64_t    g_state_since_us;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static void led_set(uint16_t duty)
{
#if LED_ACTIVE_LOW
    duty = LEDC_MAX - duty;
#endif
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL);
}

// Triangle wave between off and full, one cycle per period_ms.
static uint16_t pulse(int64_t elapsed_ms, int period_ms)
{
    int phase = (int)(elapsed_ms % period_ms);
    int half = period_ms / 2;
    int up = phase < half ? phase : period_ms - phase;
    return (uint16_t)((long)up * LEDC_MAX / half);
}

static void led_update(void)
{
    int64_t elapsed = now_ms() - g_state_since_us;

    switch (g_state) {
        case UI_IDLE:       led_set(0); break;
        case UI_RECORDING:  led_set(LEDC_MAX); break;
        case UI_THINKING:   led_set(pulse(elapsed, 1000)); break;   // ~1 Hz
        case UI_SPEAKING:   led_set(pulse(elapsed, 250)); break;    // ~4 Hz
        case UI_NO_NET:
            // double blink then a long gap
            led_set(((elapsed % 2000) < 100 || ((elapsed % 2000) > 200 && (elapsed % 2000) < 300))
                    ? LEDC_MAX : 0);
            break;
        case UI_DISCARDED:
            // three rapid flashes, then fall back to idle
            if (elapsed > 600) { ui_set_state(UI_IDLE); break; }
            led_set(((elapsed / 100) % 2 == 0) ? LEDC_MAX : 0);
            break;
    }
}

void ui_init(void)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    g_btn = cfg->gpio.button;

    gpio_config_t btn = {
        .pin_bit_mask = 1ULL << g_btn,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&btn);

    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER,
        .duty_resolution = LEDC_RES,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t ch = {
        .gpio_num = DEFAULT_LED_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL,
        .timer_sel = LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ledc_channel_config(&ch);

    g_state_since_us = now_ms();
    led_set(0);
}

void ui_set_state(ui_state_t s)
{
    if (g_state == s) return;
    g_state = s;
    g_state_since_us = now_ms();
}

ui_event_t ui_poll(void)
{
    led_update();

    int64_t t = now_ms();
    bool raw = (gpio_get_level(g_btn) == 0);          // active low
    ui_event_t ev = UI_EV_NONE;

    if (raw != g_down && (t - g_edge_us) >= DEBOUNCE_MS) {
        g_down = raw;
        g_edge_us = t;

        if (g_down) {
            g_factory_fired = false;
        } else {
            // release
            if (g_hold_active) {
                g_hold_active = false;
                ev = UI_EV_HOLD_END;
            } else if (g_pending_tap_us > 0) {
                g_pending_tap_us = 0;
                ev = UI_EV_DOUBLE_TAP;
            } else {
                g_pending_tap_us = t;                  // wait and see
            }
        }
        return ev;
    }

    if (g_down) {
        int64_t held = t - g_edge_us;

        if (held >= FACTORY_HOLD_MS && !g_factory_fired) {
            g_factory_fired = true;
            g_hold_active = false;
            return UI_EV_FACTORY_RESET;
        }
        if (held >= HOLD_MIN_MS && !g_hold_active && g_pending_tap_us == 0) {
            g_hold_active = true;
            return UI_EV_HOLD_START;
        }
    } else if (g_pending_tap_us > 0 && (t - g_pending_tap_us) >= DOUBLE_GAP_MS) {
        g_pending_tap_us = 0;
        return UI_EV_TAP;                              // no second tap arrived
    }

    return UI_EV_NONE;
}
```

Note the deliberate consequence: a single tap is reported `DOUBLE_GAP_MS` after release, because a tap and the first half of a double tap are indistinguishable until the window expires. Push-to-talk is unaffected — a hold fires `HOLD_START` after 250 ms, not on release.

- [ ] **Step 4: Delete the old button module**

```bash
rm -f src/button.c src/button.h
```

In `src/CMakeLists.txt`, replace `"button.c"` with `"ui.c"`.

- [ ] **Step 5: Add a temporary gesture check to main.c**

Call `ui_init()`, then loop `ui_poll()` every 10 ms and log every non-`UI_EV_NONE` event by name. Cycle `ui_set_state` through the five states on a 3-second timer.

- [ ] **Step 6: Flash and verify by hand**

Run: `pio run -e esp32s3 --target upload && pio device monitor`
Expected: a hold logs `HOLD_START` then `HOLD_END`; one quick press logs `TAP` about 350 ms later; two quick presses log `DOUBLE_TAP` and no `TAP`; a 10-second hold logs `FACTORY_RESET`. The LED is off / solid / slow pulse / fast pulse / triple flash as the states rotate. If the LED does nothing, `DEFAULT_LED_GPIO` is wrong — find the right pin before continuing.

- [ ] **Step 7: Remove the temporary check and commit**

```bash
git add -A
git commit -m "feat: button gestures and LED state patterns"
```

---

### Task 10: State machine, conversation pipeline, probe

Wires everything together: the two tasks, the sentence queue, and the `--probe` self-test. This is the task that makes the device work.

**Files:**
- Create: `src/app.c`, `src/app.h`
- Modify: `src/main.c`, `src/CMakeLists.txt`, `CLAUDE.md`

**Interfaces:**
- Consumes: every module built so far.
- Produces: `void app_start(void);` and `esp_err_t app_probe(void);`

- [ ] **Step 1: Write the header**

Create `src/app.h`:

```c
#pragma once
#include "esp_err.h"

// Creates the conversation and speech tasks and runs the UI loop forever.
void app_start(void);

// A.6 probe: text -> TTS -> STT -> chat -> TTS -> playback, no microphone.
esp_err_t app_probe(void);
```

- [ ] **Step 2: Write the implementation**

Create `src/app.c`:

```c
#include "app.h"
#include "ui.h"
#include "capture.h"
#include "player.h"
#include "manager.h"
#include "stream.h"
#include "speakable.h"
#include "nvs_config.h"
#include "wifi_manager.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stdlib.h>
#include <time.h>

static const char *TAG = "app";

#define SENTENCE_QUEUE_DEPTH 8

typedef enum { ST_IDLE, ST_RECORDING, ST_THINKING, ST_SPEAKING } app_state_t;

static volatile app_state_t g_state = ST_IDLE;
static QueueHandle_t        g_sentences;      // of char*, malloc'd
static char                 g_chat_id[32];
static volatile bool        g_turn_active;    // conversation task is working
static int64_t              g_release_us;     // for the A.6 latency figure

static void new_chat_id(void)
{
    snprintf(g_chat_id, sizeof(g_chat_id), "voice-%lld", (long long)time(NULL));
    ESP_LOGI(TAG, "Neue Chat-ID: %s", g_chat_id);
}

// ---- speech task: sentence -> TTS -> I2S ----------------------------------

static int tts_shim(void *ctx, uint8_t *buf, size_t len)
{
    return mgr_tts_read((mgr_stream_t *)ctx, buf, len);
}

static void speech_task(void *arg)
{
    (void)arg;
    for (;;) {
        char *sentence = NULL;
        if (xQueueReceive(g_sentences, &sentence, portMAX_DELAY) != pdTRUE) continue;
        if (sentence == NULL) continue;

        ui_set_state(UI_SPEAKING);
        g_state = ST_SPEAKING;

        mgr_stream_t *s = NULL;
        if (mgr_tts_open(sentence, &s) == ESP_OK) {
            if (g_release_us > 0) {
                ESP_LOGI(TAG, "Latenz bis erster Ton: %lld ms",
                         (long long)((esp_timer_get_time() - g_release_us) / 1000));
                g_release_us = 0;
            }
            player_play(tts_shim, s);
            mgr_tts_close(s);
        } else {
            ESP_LOGE(TAG, "TTS fehlgeschlagen");
        }
        free(sentence);

        // Back to idle only once the queue is empty and the turn has finished.
        if (uxQueueMessagesWaiting(g_sentences) == 0 && !g_turn_active) {
            g_state = ST_IDLE;
            ui_set_state(UI_IDLE);
        }
    }
}

// ---- conversation task: STT -> chat -> sentences --------------------------

static void on_sentence(const char *sentence, void *user)
{
    (void)user;
    char *spoken = speakable(sentence);
    if (spoken == NULL) return;                     // B.6: empty is never spoken

    if (xQueueSend(g_sentences, &spoken, pdMS_TO_TICKS(100)) != pdTRUE) {
        ESP_LOGW(TAG, "Satzwarteschlange voll, Satz verworfen");
        free(spoken);
    }
}

static void on_chat_data(const char *data, size_t len, void *user)
{
    stream_feed((stream_t *)user, data, len);
}

// /reset returns a body we neither speak nor keep.
static void discard_chat_data(const char *data, size_t len, void *user)
{
    (void)data; (void)len; (void)user;
}

static void run_turn(const uint8_t *wav, size_t wav_len)
{
    const mrvoice_config_t *cfg = nvs_config_get();

    g_turn_active = true;
    g_state = ST_THINKING;
    ui_set_state(UI_THINKING);

    mgr_stt_result_t stt = {0};
    if (mgr_stt(wav, wav_len, &stt) != ESP_OK || strlen(stt.text) < 2) {
        ESP_LOGW(TAG, "✕ verworfen: kein Text erkannt");
        ui_set_state(UI_DISCARDED);
        g_state = ST_IDLE;
        g_turn_active = false;
        return;
    }
    ESP_LOGI(TAG, "Erkannt (%.1fs): %s", stt.seconds, stt.text);

    // FIXTURE-UNVERIFIED: exact prefix format not confirmed against client.go.
    size_t msg_cap = strlen(cfg->prompt) + strlen(stt.text) + 32;
    char *message = malloc(msg_cap);
    if (message == NULL) { g_turn_active = false; return; }
    snprintf(message, msg_cap, "[Voice-Client] %s\n\n%s", cfg->prompt, stt.text);

    stream_t sentences;
    stream_init(&sentences, on_sentence, NULL);

    if (mgr_chat(cfg->instance, message, g_chat_id, on_chat_data, &sentences) != ESP_OK) {
        ESP_LOGE(TAG, "Chat fehlgeschlagen");
    }
    stream_close(&sentences);
    stream_free(&sentences);
    free(message);

    g_turn_active = false;

    if (uxQueueMessagesWaiting(g_sentences) == 0 && g_state != ST_SPEAKING) {
        g_state = ST_IDLE;
        ui_set_state(UI_IDLE);
    }
}

static void drain_queue(void)
{
    char *s;
    while (xQueueReceive(g_sentences, &s, 0) == pdTRUE) free(s);
}

// ---- probe (A.6) ----------------------------------------------------------

esp_err_t app_probe(void)
{
    const char *phrase = "Dies ist ein Selbsttest des Sprachclients.";
    ESP_LOGI(TAG, "Probe: TTS -> STT -> Chat -> TTS");

    // TTS the phrase into a buffer so STT has something to recognise.
    mgr_stream_t *s = NULL;
    if (mgr_tts_open(phrase, &s) != ESP_OK) return ESP_FAIL;

    size_t cap = 512 * 1024, len = 0;
    uint8_t *buf = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
    if (buf == NULL) { mgr_tts_close(s); return ESP_ERR_NO_MEM; }

    for (;;) {
        int n = mgr_tts_read(s, buf + len, cap - len);
        if (n <= 0) break;
        len += (size_t)n;
        if (len >= cap) break;
    }
    mgr_tts_close(s);
    ESP_LOGI(TAG, "Probe: %u Byte TTS-WAV", (unsigned)len);

    // The TTS output is 22050 Hz; /api/stt accepts any format the server can
    // convert, so it goes back up unchanged.
    mgr_stt_result_t stt = {0};
    if (mgr_stt(buf, len, &stt) != ESP_OK) { free(buf); return ESP_FAIL; }
    free(buf);
    ESP_LOGI(TAG, "Probe: STT lieferte \"%s\"", stt.text);

    const mrvoice_config_t *cfg = nvs_config_get();
    stream_t sentences;
    stream_init(&sentences, on_sentence, NULL);
    esp_err_t ret = mgr_chat(cfg->instance, stt.text, g_chat_id, on_chat_data, &sentences);
    stream_close(&sentences);
    stream_free(&sentences);

    ESP_LOGI(TAG, "Probe %s", ret == ESP_OK ? "erfolgreich" : "fehlgeschlagen");
    return ret;
}

// ---- main loop ------------------------------------------------------------

static void conversation_task(void *arg)
{
    (void)arg;
    for (;;) {
        ui_event_t ev = ui_poll();

        switch (ev) {
            case UI_EV_HOLD_START:
                if (g_state == ST_IDLE) {
                    g_state = ST_RECORDING;
                    ui_set_state(UI_RECORDING);
                    capture_start();
                }
                break;

            case UI_EV_HOLD_END:
                if (g_state == ST_RECORDING) {
                    capture_stop();
                    g_release_us = esp_timer_get_time();

                    uint32_t ms = capture_duration_ms();
                    const mrvoice_config_t *cfg = nvs_config_get();
                    if (ms < cfg->min_ms) {
                        ESP_LOGW(TAG, "✕ verworfen: nur %u ms", (unsigned)ms);
                        ui_set_state(UI_DISCARDED);
                        g_state = ST_IDLE;
                        break;
                    }
                    size_t wav_len = 0;
                    const uint8_t *wav = capture_wav(&wav_len);
                    run_turn(wav, wav_len);
                }
                break;

            case UI_EV_TAP:
                if (g_state == ST_SPEAKING) {
                    ESP_LOGI(TAG, "Wiedergabe gestoppt");
                    player_abort();
                    drain_queue();
                    g_state = ST_IDLE;
                    ui_set_state(UI_IDLE);
                }
                break;

            case UI_EV_DOUBLE_TAP: {
                ESP_LOGI(TAG, "Neues Gespräch");
                player_abort();
                drain_queue();
                const mrvoice_config_t *cfg = nvs_config_get();
                mgr_chat(cfg->instance, "/reset", g_chat_id, discard_chat_data, NULL);
                new_chat_id();
                g_state = ST_IDLE;
                ui_set_state(UI_IDLE);
                break;
            }

            case UI_EV_FACTORY_RESET:
                ESP_LOGW(TAG, "Werksreset");
                nvs_config_factory_reset();
                vTaskDelay(pdMS_TO_TICKS(500));
                esp_restart();
                break;

            default:
                break;
        }

        if (g_state == ST_RECORDING && !capture_pump()) {
            ESP_LOGW(TAG, "Maximale Aufnahmedauer erreicht");
            // Treat the cap as a release.
            capture_stop();
            g_release_us = esp_timer_get_time();
            size_t wav_len = 0;
            const uint8_t *wav = capture_wav(&wav_len);
            run_turn(wav, wav_len);
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void app_start(void)
{
    g_sentences = xQueueCreate(SENTENCE_QUEUE_DEPTH, sizeof(char *));
    new_chat_id();

    xTaskCreate(speech_task, "speech", 6144, NULL, 5, NULL);
    xTaskCreate(conversation_task, "conv", 8192, NULL, 5, NULL);
}
```

- [ ] **Step 3: Wire app_start into main.c**

In `src/main.c`, after `wifi_manager_start()`, add:

```c
    if (capture_init() != ESP_OK) {
        ESP_LOGE(TAG, "Aufnahmepuffer konnte nicht angelegt werden");
        return;
    }
    ui_init();
    app_start();
```

and replace the idle `while (1)` loop with `vTaskDelete(NULL);` — the two tasks own the device from here. Include `app.h`, `ui.h`, `capture.h`.

- [ ] **Step 4: Add app.c to the firmware build**

In `src/CMakeLists.txt`, add `"app.c"` to `SRCS`.

- [ ] **Step 5: Build**

Run: `pio run -e esp32s3`
Expected: SUCCESS with no warnings about the removed label.

- [ ] **Step 6: Flash and run the full loop**

Run: `pio run -e esp32s3 --target upload && pio device monitor`

Hold the button, say a sentence, release. Expected sequence in the log:
```
Erkannt (2.1s): <your words>
Latenz bis erster Ton: <ms>
```
with the reply audible. Then verify each gesture: a tap during playback stops it; a double tap logs `Neues Gespräch` and a fresh chat ID; a sub-400 ms tap-and-hold logs `✕ verworfen: nur NNN ms` and triple-flashes the LED.

- [ ] **Step 7: Confirm the device does not hear itself (A.6)**

While a long reply is playing, watch the log. Expected: no `Erkannt` line appears. The microphone is only enabled in `ST_RECORDING`, so this should hold by construction.

- [ ] **Step 8: Expose the probe as a serial command**

`app_probe()` exists but nothing calls it. Spec §12 requires it as a serial command. Add a REPL in `app_start()` using the stock console component:

```c
#include "esp_console.h"

static int cmd_probe(int argc, char **argv)
{
    (void)argc; (void)argv;
    return app_probe() == ESP_OK ? 0 : 1;
}

static void console_start(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t rc = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    rc.prompt = "mrvoice>";

    esp_console_dev_usb_serial_jtag_config_t dev =
        ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();

    if (esp_console_new_repl_usb_serial_jtag(&dev, &rc, &repl) != ESP_OK) return;

    const esp_console_cmd_t probe = {
        .command = "probe",
        .help = "Selbsttest: TTS -> STT -> Chat",
        .func = cmd_probe,
    };
    esp_console_cmd_register(&probe);
    esp_console_register_help_command();
    esp_console_start_repl(repl);
}
```

Call `console_start()` at the top of `app_start()`, and add `esp_console` to `REQUIRES` in `src/CMakeLists.txt`.

Run: `pio run -e esp32s3 --target upload && pio device monitor`, then type `probe` at the prompt.
Expected: the three `Probe:` log lines and a final `Probe erfolgreich`. Blocked without a reachable manager.

- [ ] **Step 9: Rewrite CLAUDE.md**

`CLAUDE.md` still documents the LiveKit architecture end to end and is now wrong in almost every section. Replace its contents with: the kAIm56 architecture, the module table from this plan's File Structure, the A.3 endpoint contract, the button gesture map, the LED patterns, the NVS keys, the build commands, and a pointer to the spec at `docs/superpowers/specs/2026-09-07-kaim56-voice-esp-design.md`. Delete the "Known Issue: SCTP over TURN" section — it no longer applies.

- [ ] **Step 10: Run the whole host suite one more time**

Run: `pio test -e native`
Expected: PASS, 31 tests.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: conversation state machine, speech pipeline and probe"
```

---

## Open items carried out of this plan

These are recorded in the spec and remain unresolved:

1. **`stream.go` / `manager.go` / `client.go` were never provided.** Every fixture marked `FIXTURE-UNVERIFIED` in Tasks 3 and 4, plus the `[Voice-Client]` prompt prefix format in Task 10, is reconstructed from Part B's prose. A.2 requires the Go original as oracle; until the source arrives that requirement is unmet.
2. **No manager to test against.** Tasks 6, 8, and 10 have steps that need a reachable `base_url`, instance, and credentials. Without them those steps are blocked, not skipped.
3. **`DEFAULT_LED_GPIO` is a guess.** Task 9 Step 6 is the check that confirms it.
4. **`GET /api/instances` is not implemented.** The instance is configured by hand in the web UI. Add it only if on-device instance selection is wanted.
