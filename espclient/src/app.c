#include "app.h"
#include "ui.h"
#include "capture.h"
#include "audio.h"
#include "config.h"
#include "driver/gpio.h"
#include "player.h"
#include "manager.h"
#include "stream.h"
#include "speakable.h"
#include "nvs_config.h"
#include "wifi_manager.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_console.h"
#include "mbedtls/base64.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <math.h>
#include "wav.h"

static const char *TAG = "app";

#define SENTENCE_QUEUE_DEPTH 8
#define PROBE_BUF_BYTES      (512 * 1024)

typedef enum { ST_IDLE, ST_RECORDING, ST_THINKING, ST_SPEAKING } app_state_t;

static volatile app_state_t g_state = ST_IDLE;
static QueueHandle_t        g_sentences;      // of char*, malloc'd
static char                 g_chat_id[32];
static volatile bool        g_turn_active;    // conversation task is working
static int64_t              g_release_us;     // for the A.6 latency figure
static volatile bool        g_diag_active;    // suspends gesture handling

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

static void drain_queue(void)
{
    char *s;
    while (xQueueReceive(g_sentences, &s, 0) == pdTRUE) free(s);
}

static void speech_task(void *arg)
{
    (void)arg;
    for (;;) {
        char *sentence = NULL;
        if (xQueueReceive(g_sentences, &sentence, portMAX_DELAY) != pdTRUE) continue;
        if (sentence == NULL) continue;

        g_state = ST_SPEAKING;
        ui_set_state(UI_SPEAKING);

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

        // Idle only once the queue is empty and the turn has finished.
        if (uxQueueMessagesWaiting(g_sentences) == 0 && !g_turn_active) {
            g_state = ST_IDLE;
            ui_set_state(UI_IDLE);
        }
    }
}

// ---- conversation: STT -> chat -> sentences -------------------------------

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

// ---- probe (A.6) ----------------------------------------------------------

esp_err_t app_probe(void)
{
    const char *phrase = "Dies ist ein Selbsttest des Sprachclients.";
    ESP_LOGI(TAG, "Probe: TTS -> STT -> Chat");

    // Synthesise the phrase so STT has something real to recognise.
    mgr_stream_t *s = NULL;
    if (mgr_tts_open(phrase, &s) != ESP_OK) return ESP_FAIL;

    uint8_t *buf = heap_caps_malloc(PROBE_BUF_BYTES, MALLOC_CAP_SPIRAM);
    if (buf == NULL) { mgr_tts_close(s); return ESP_ERR_NO_MEM; }

    size_t len = 0;
    while (len < PROBE_BUF_BYTES) {
        int n = mgr_tts_read(s, buf + len, PROBE_BUF_BYTES - len);
        if (n <= 0) break;
        len += (size_t)n;
    }
    mgr_tts_close(s);
    ESP_LOGI(TAG, "Probe: %u Byte TTS-WAV", (unsigned)len);

    // The server converts any format it is given, so this goes straight back up.
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

// ---- conversation task ----------------------------------------------------

static void finish_recording(void)
{
    capture_stop();
    g_release_us = esp_timer_get_time();

    uint32_t ms = capture_duration_ms();
    const mrvoice_config_t *cfg = nvs_config_get();
    if (ms < cfg->min_ms) {
        ESP_LOGW(TAG, "✕ verworfen: nur %u ms", (unsigned)ms);
        ui_set_state(UI_DISCARDED);
        g_state = ST_IDLE;
        return;
    }

    size_t wav_len = 0;
    const uint8_t *wav = capture_wav(&wav_len);
    run_turn(wav, wav_len);
}

static void conversation_task(void *arg)
{
    (void)arg;
    static const char *gesture_name[] = {
        "NONE", "HALTEN-START", "HALTEN-ENDE", "TIPPEN", "DOPPELTIPPEN", "WERKSRESET"
    };

    for (;;) {
        // Diagnostics reconfigure GPIO out from under the gesture loop; a
        // button pin left floating reads as held and would trip the 10 s
        // factory reset.
        if (g_diag_active) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        gesture_event_t ev = ui_poll();
        if (ev != GESTURE_NONE) {
            ESP_LOGI(TAG, "Taste: %s", gesture_name[ev]);
        }

        switch (ev) {
            case GESTURE_HOLD_START:
                if (g_state == ST_IDLE) {
                    g_state = ST_RECORDING;
                    ui_set_state(UI_RECORDING);
                    capture_start();
                }
                break;

            case GESTURE_HOLD_END:
                if (g_state == ST_RECORDING) finish_recording();
                break;

            case GESTURE_TAP:
                if (g_state == ST_SPEAKING) {
                    ESP_LOGI(TAG, "Wiedergabe gestoppt");
                    player_abort();
                    drain_queue();
                    g_state = ST_IDLE;
                    ui_set_state(UI_IDLE);
                }
                break;

            case GESTURE_DOUBLE_TAP: {
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

            case GESTURE_FACTORY_RESET:
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
            finish_recording();
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

// ---- serial console -------------------------------------------------------

// --- diagnostics: verify the hardware without needing a manager ------------

// Records for two seconds and reports duration and peak amplitude, so a dead
// or miswired microphone is obvious.
static int cmd_mic(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;
    g_diag_active = true;
    g_diag_active = true;
    g_diag_active = true;

    printf("Nehme 2 s auf...\n");
    capture_start();
    int64_t end = esp_timer_get_time() + 2000000;
    while (esp_timer_get_time() < end) {
        capture_pump();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    capture_stop();

    size_t len = 0;
    const uint8_t *wav = capture_wav(&len);
    const int16_t *pcm = (const int16_t *)(wav + WAV_HEADER_SIZE);
    size_t n = (len - WAV_HEADER_SIZE) / sizeof(int16_t);

    int32_t peak = 0;
    int64_t sq = 0;
    for (size_t i = 0; i < n; i++) {
        int32_t v = pcm[i] < 0 ? -pcm[i] : pcm[i];
        if (v > peak) peak = v;
        sq += (int64_t)pcm[i] * pcm[i];
    }
    int32_t rms = n ? (int32_t)sqrt((double)sq / (double)n) : 0;

    printf("Dauer %u ms, %u Samples, Peak %d, RMS %d\n",
           (unsigned)capture_duration_ms(), (unsigned)n, (int)peak, (int)rms);
    // A peak of a few LSBs is the noise floor, not audio. Real speech on an
    // INMP441 reaches the thousands; anything under ~200 means no signal.
    if (n == 0)          printf("FEHLER: keine I2S-Daten\n");
    else if (peak == 0)  printf("FEHLER: nur Stille - kein einziges Bit\n");
    else if (peak < 200) printf("FEHLER: nur Rauschgrund (Peak %d) - kein Audio\n", (int)peak);
    else                 printf("OK: Mikrofon liefert Audio\n");
    return 0;
}

// A generated 22050 Hz WAV pushed through the real player, so this exercises
// wav_write_header, the I2S retune and the amplifier exactly as TTS would.
typedef struct { const uint8_t *p; size_t len, pos; } membuf_t;

static int membuf_read(void *ctx, uint8_t *buf, size_t len)
{
    membuf_t *m = (membuf_t *)ctx;
    size_t n = m->len - m->pos;
    if (n == 0) return 0;
    if (n > len) n = len;
    memcpy(buf, m->p + m->pos, n);
    m->pos += n;
    return (int)n;
}

static int cmd_tone(int argc, char **argv)
{
    (void)argc; (void)argv;
    const uint32_t rate = 22050;      // same rate Piper delivers
    const uint32_t ms = 1000;
    const size_t samples = rate * ms / 1000;
    const size_t pcm_bytes = samples * sizeof(int16_t);

    uint8_t *buf = heap_caps_malloc(WAV_HEADER_SIZE + pcm_bytes, MALLOC_CAP_SPIRAM);
    if (buf == NULL) { printf("Kein Speicher\n"); return 1; }

    wav_write_header(buf, rate, 1, 16, (uint32_t)pcm_bytes);
    int16_t *pcm = (int16_t *)(buf + WAV_HEADER_SIZE);
    for (size_t i = 0; i < samples; i++) {
        pcm[i] = (int16_t)(12000.0f * sinf(2.0f * 3.14159265f * 440.0f * i / rate));
    }

    printf("Spiele 440 Hz Testton bei %u Hz...\n", (unsigned)rate);
    membuf_t m = { .p = buf, .len = WAV_HEADER_SIZE + pcm_bytes, .pos = 0 };
    esp_err_t ret = player_play(membuf_read, &m);
    free(buf);
    printf("Wiedergabe %s\n", ret == ESP_OK ? "OK" : "FEHLGESCHLAGEN");
    return ret == ESP_OK ? 0 : 1;
}

// Cycles the LED through every state so the GPIO guess can be eyeballed.
static int cmd_led(int argc, char **argv)
{
    (void)argc; (void)argv;
    const struct { ui_state_t s; const char *name; int ms; } steps[] = {
        { UI_RECORDING, "RECORDING (dauerhaft an)", 2000 },
        { UI_THINKING,  "THINKING (langsam)",       3000 },
        { UI_SPEAKING,  "SPEAKING (schnell)",       3000 },
        { UI_NO_NET,    "NO_NET (Doppelblink)",     3000 },
        { UI_DISCARDED, "DISCARDED (3 Blitze)",     1000 },
        { UI_IDLE,      "IDLE (aus)",               1000 },
    };
    for (size_t i = 0; i < sizeof(steps) / sizeof(steps[0]); i++) {
        printf("LED: %s\n", steps[i].name);
        ui_set_state(steps[i].s);
        vTaskDelay(pdMS_TO_TICKS(steps[i].ms));
    }
    return 0;
}

// Records a short burst on the currently configured mic pins and returns the
// peak amplitude, so a pin triple that is actually wired stands out.
// Returns the RMS over the window, not the peak. Reconfiguring pads produces
// switching transients, and a single spike is enough to make a peak-based
// search report a hit that never reproduces - which it did, twice.
static int32_t probe_mic_pins(int sck, int ws, int sd, int ms)
{
    if (audio_mic_reinit(sck, ws, sd) != ESP_OK) return -1;
    if (i2s_channel_enable(audio_mic_handle()) != ESP_OK) return -1;

    static int16_t buf[512];
    int64_t sq = 0;
    size_t n = 0;
    int64_t end = esp_timer_get_time() + (int64_t)ms * 1000;

    // Discard the first reads: they contain the transient from switching pads.
    size_t warmup = 2;

    while (esp_timer_get_time() < end) {
        size_t got = 0;
        i2s_channel_read(audio_mic_handle(), buf, sizeof(buf), &got, pdMS_TO_TICKS(50));
        if (warmup) { warmup--; continue; }
        for (size_t i = 0; i < got / sizeof(int16_t); i++) {
            sq += (int64_t)buf[i] * buf[i];
            n++;
        }
    }
    i2s_channel_disable(audio_mic_handle());
    if (n == 0) return 0;
    return (int32_t)sqrt((double)sq / (double)n);
}

// Walks the plausible pin triples for this board and reports which one yields
// a signal. Make noise near the microphone while this runs.
static int cmd_micscan(int argc, char **argv)
{
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }

    // Explicit triple: `micscan <sck> <ws> <sd>`
    if (argc == 4) {
        int sck = atoi(argv[1]), ws = atoi(argv[2]), sd = atoi(argv[3]);
        printf("Teste %d/%d/%d ...\n", sck, ws, sd);
        audio_dac_deinit();
        int32_t peak = probe_mic_pins(sck, ws, sd, 1500);
        printf("  RMS %d\n", (int)peak);
        audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
        audio_dac_reinit();
        return 0;
    }

    static const int cand[][3] = {
        { 1,  2,  3},   // config.h / CLAUDE.md
        { 4,  5,  6},   // Readme.md (documents a different board)
        { 7,  8,  9},   // XIAO D8/D9/D10
        { 2,  3,  4},
        { 3,  4,  5},
        { 8,  9, 43},
        { 9, 43, 44},
    };

    printf("Scanne Mikrofon-Pins. Bitte JETZT laut sprechen oder klopfen.\n");
    printf("Der DAC wird waehrend des Scans freigegeben.\n");
    audio_dac_deinit();

    int best = -1;
    int32_t best_peak = 0;
    for (size_t i = 0; i < sizeof(cand) / sizeof(cand[0]); i++) {
        int32_t peak = probe_mic_pins(cand[i][0], cand[i][1], cand[i][2], 1200);
        printf("  SCK=%2d WS=%2d SD=%2d -> Peak %6d%s\n",
               cand[i][0], cand[i][1], cand[i][2], (int)peak,
               peak > 200 ? "  <== SIGNAL" : "");
        if (peak > best_peak) { best_peak = peak; best = (int)i; }
    }

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();

    g_diag_active = false;
    if (best_peak <= 200) {
        printf("Kein Pin-Paar liefert Signal. Stromversorgung, GND und L/R pruefen.\n");
    } else {
        printf("Bestes Ergebnis: SCK=%d WS=%d SD=%d (Peak %d)\n",
               cand[best][0], cand[best][1], cand[best][2], (int)best_peak);
    }
    return 0;
}

// Is anything electrically attached to this pin? With an internal pull-up then
// a pull-down, an unconnected pin follows the resistor; a pin something is
// driving or shorting does not. The I2S channels are torn down first, so the
// peripheral is not still holding the pin, and rebuilt afterwards.
static int cmd_pintest(int argc, char **argv)
{
    if (argc < 2) { printf("Aufruf: pintest <gpio> [gpio ...]\n"); return 1; }
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }

    g_diag_active = true;
    printf("Gebe I2S frei...\n");
    audio_mic_teardown();
    audio_dac_deinit();
    vTaskDelay(pdMS_TO_TICKS(50));

    const mrvoice_config_t *cfg = nvs_config_get();
    for (int a = 1; a < argc; a++) {
        int pin = atoi(argv[a]);
        if (pin == cfg->gpio.button) {
            printf("GPIO %2d: uebersprungen (Taster - Reset-Gefahr)\n", pin);
            continue;
        }
        gpio_reset_pin(pin);

        gpio_config_t up = { .pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_INPUT,
                             .pull_up_en = GPIO_PULLUP_ENABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE };
        gpio_config(&up);
        vTaskDelay(pdMS_TO_TICKS(30));
        int with_pullup = gpio_get_level(pin);

        gpio_config_t down = { .pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_INPUT,
                               .pull_up_en = GPIO_PULLUP_DISABLE, .pull_down_en = GPIO_PULLDOWN_ENABLE };
        gpio_config(&down);
        vTaskDelay(pdMS_TO_TICKS(30));
        int with_pulldown = gpio_get_level(pin);

        const char *verdict;
        if (with_pullup == 1 && with_pulldown == 0) verdict = "frei - folgt dem Widerstand";
        else if (with_pullup == 0 && with_pulldown == 0) verdict = "fest LOW - GND oder getrieben";
        else if (with_pullup == 1 && with_pulldown == 1) verdict = "fest HIGH - VDD oder getrieben";
        else verdict = "unklar";

        printf("GPIO %2d: pullup=%d pulldown=%d -> %s\n", pin, with_pullup, with_pulldown, verdict);
        gpio_reset_pin(pin);   // leave nothing attached behind
    }

    printf("Stelle I2S wieder her...\n");
    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();
    gpio_config_t sd = { .pin_bit_mask = 1ULL << DAC_SD, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&sd);
    gpio_set_level(DAC_SD, 1);

    ui_init();                 // restores the button pull-up and the LED
    g_diag_active = false;
    return 0;
}

// Sweeps the I2S slot formats an INMP441 might need and dumps raw words, so
// any bit activity at all is visible even when the framing is wrong.
static int cmd_micraw(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }

    struct variant {
        const char *name;
        i2s_std_slot_mask_t mask;
        i2s_data_bit_width_t bits;
        bool left_align, bit_shift;
        i2s_slot_mode_t slot_mode;
    };
    static const struct variant vs[] = {
        { "STEREO 32bit BOTH  (wie esp_codec_dev)", I2S_STD_SLOT_BOTH,  I2S_DATA_BIT_WIDTH_32BIT, true,  true,  I2S_SLOT_MODE_STEREO },
        { "STEREO 16bit BOTH",                      I2S_STD_SLOT_BOTH,  I2S_DATA_BIT_WIDTH_16BIT, true,  true,  I2S_SLOT_MODE_STEREO },
        { "STEREO 32bit LEFT",                      I2S_STD_SLOT_LEFT,  I2S_DATA_BIT_WIDTH_32BIT, true,  true,  I2S_SLOT_MODE_STEREO },
        { "MONO   32bit LEFT",                      I2S_STD_SLOT_LEFT,  I2S_DATA_BIT_WIDTH_32BIT, true,  true,  I2S_SLOT_MODE_MONO },
        { "MONO   16bit LEFT  (aktuell)",           I2S_STD_SLOT_LEFT,  I2S_DATA_BIT_WIDTH_16BIT, true,  true,  I2S_SLOT_MODE_MONO },
        { "STEREO 32bit RIGHT",                     I2S_STD_SLOT_RIGHT, I2S_DATA_BIT_WIDTH_32BIT, true,  true,  I2S_SLOT_MODE_STEREO },
    };

    printf("Bitte Geraeusch machen. Sweep ueber die Slot-Formate:\n");

    for (size_t i = 0; i < sizeof(vs) / sizeof(vs[0]); i++) {
        if (audio_mic_reinit_ex(MIC_SCK, MIC_WS, MIC_SD, vs[i].mask, vs[i].bits,
                                vs[i].left_align, vs[i].bit_shift,
                                vs[i].slot_mode) != ESP_OK) {
            printf("  %s -> Init fehlgeschlagen\n", vs[i].name);
            continue;
        }
        if (i2s_channel_enable(audio_mic_handle()) != ESP_OK) {
            printf("  %s -> Enable fehlgeschlagen\n", vs[i].name);
            continue;
        }

        static uint32_t raw[256];
        size_t nonzero = 0;
        uint32_t or_all = 0;
        int64_t end = esp_timer_get_time() + 800000;
        size_t words_seen = 0;
        uint32_t sample[6] = {0};

        while (esp_timer_get_time() < end) {
            size_t got = 0;
            i2s_channel_read(audio_mic_handle(), raw, sizeof(raw), &got, pdMS_TO_TICKS(50));
            size_t n = got / sizeof(uint32_t);
            for (size_t k = 0; k < n; k++) {
                or_all |= raw[k];
                if (raw[k] != 0) {
                    if (nonzero < 6) sample[nonzero] = raw[k];
                    nonzero++;
                }
            }
            words_seen += n;
        }
        i2s_channel_disable(audio_mic_handle());

        printf("  %-38s Worte=%5u nichtnull=%5u OR=%08lx\n",
               vs[i].name, (unsigned)words_seen, (unsigned)nonzero,
               (unsigned long)or_all);
        if (nonzero > 0) {
            printf("      Beispiel: %08lx %08lx %08lx\n",
                   (unsigned long)sample[0], (unsigned long)sample[1],
                   (unsigned long)sample[2]);
        }
    }

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    g_diag_active = false;
    printf("Fertig. OR=00000000 ueberall bedeutet: kein einziges Bit vom Mikrofon.\n");
    return 0;
}

// Watches the button for a while, reporting raw level changes and the
// gestures the recogniser derives from them.
static int cmd_btn(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    const mrvoice_config_t *cfg = nvs_config_get();
    printf("Taster an GPIO %d - jetzt druecken (kurz, doppelt, lang). 15 s.\n",
           cfg->gpio.button);

    int before = gpio_get_level(cfg->gpio.button);

    // Mirror btnscan: claim the pad here rather than trusting ui_init's setup.
    gpio_reset_pin(cfg->gpio.button);
    gpio_config_t bc = { .pin_bit_mask = 1ULL << cfg->gpio.button,
                         .mode = GPIO_MODE_INPUT,
                         .pull_up_en = GPIO_PULLUP_ENABLE,
                         .pull_down_en = GPIO_PULLDOWN_DISABLE };
    gpio_config(&bc);
    vTaskDelay(pdMS_TO_TICKS(30));
    printf("Pegel vor Reset: %d, nach Reset+Pullup: %d\n",
           before, gpio_get_level(cfg->gpio.button));

    gesture_t g;
    gesture_init(&g);
    int last = gpio_get_level(cfg->gpio.button);
    int changes = 0;
    printf("Ruhepegel: %d (erwartet 1 = nicht gedrueckt)\n", last);

    int64_t end = esp_timer_get_time() + 15000000;
    while (esp_timer_get_time() < end) {
        int lvl = gpio_get_level(cfg->gpio.button);
        if (lvl != last) {
            printf("  Pegel %d -> %d\n", last, lvl);
            last = lvl;
            changes++;
        }
        switch (gesture_update(&g, lvl == 0, esp_timer_get_time() / 1000)) {
            case GESTURE_HOLD_START:    printf("  >> HALTEN start (Sprechen)\n"); break;
            case GESTURE_HOLD_END:      printf("  >> HALTEN ende\n"); break;
            case GESTURE_TAP:           printf("  >> TIPPEN (Wiedergabe stoppen)\n"); break;
            case GESTURE_DOUBLE_TAP:    printf("  >> DOPPELTIPPEN (neues Gespraech)\n"); break;
            case GESTURE_FACTORY_RESET: printf("  >> WERKSRESET (hier nur gemeldet)\n"); break;
            default: break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    if (changes == 0) printf("Keine Pegelaenderung - Taster nicht verdrahtet oder nicht gedrueckt.\n");
    else              printf("%d Pegelaenderungen - Taster funktioniert.\n", changes);

    ui_init();
    g_diag_active = false;
    return 0;
}

// Watches every plausible GPIO with a pull-up and reports which ones are
// pulled low, so the button is found wherever it is actually wired.
static int cmd_btnscan(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    static const int pins[] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 21, 43, 44 };
    const size_t n = sizeof(pins) / sizeof(pins[0]);

    printf("Gebe I2S frei und suche den Taster...\n");
    audio_mic_teardown();
    audio_dac_deinit();
    vTaskDelay(pdMS_TO_TICKS(50));

    for (size_t i = 0; i < n; i++) {
        gpio_reset_pin(pins[i]);
        gpio_config_t c = { .pin_bit_mask = 1ULL << pins[i], .mode = GPIO_MODE_INPUT,
                            .pull_up_en = GPIO_PULLUP_ENABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE };
        gpio_config(&c);
    }
    vTaskDelay(pdMS_TO_TICKS(50));

    int idle[32], last[32], edges[32], low_samples[32];
    for (size_t i = 0; i < n; i++) {
        idle[i] = last[i] = gpio_get_level(pins[i]);
        edges[i] = 0;
        low_samples[i] = 0;
    }

    printf("Ruhepegel:");
    for (size_t i = 0; i < n; i++) printf(" %d=%d", pins[i], idle[i]);
    printf("\n15 s lang mehrfach druecken...\n");

    int samples = 0;
    int64_t end = esp_timer_get_time() + 15000000;
    while (esp_timer_get_time() < end) {
        for (size_t i = 0; i < n; i++) {
            int lvl = gpio_get_level(pins[i]);
            if (lvl != last[i]) { edges[i]++; last[i] = lvl; }   // count edges, not samples
            if (lvl == 0) low_samples[i]++;
            }
        samples++;
        vTaskDelay(pdMS_TO_TICKS(5));
    }

    printf("%d Abtastungen. Flanken je Pin:\n", samples);
    int found = 0;
    for (size_t i = 0; i < n; i++) {
        if (edges[i] == 0) continue;
        int pct = samples ? (low_samples[i] * 100 / samples) : 0;
        const char *tag = "";
        if (edges[i] >= 2 && edges[i] <= 100) tag = "  <== TASTER?";
        else if (edges[i] > 100)              tag = "  (zappelt - floatend oder Signal)";
        printf("  GPIO %2d: %4d Flanken, %3d%% LOW, Ruhe=%d%s\n",
               pins[i], edges[i], pct, idle[i], tag);
        if (edges[i] >= 2 && edges[i] <= 100) found++;
    }
    if (found == 0) printf("Kein Pin zeigt sauberes Tastverhalten.\n");

    for (size_t i = 0; i < n; i++) gpio_reset_pin(pins[i]);
    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();
    gpio_config_t sd = { .pin_bit_mask = 1ULL << DAC_SD, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&sd);
    gpio_set_level(DAC_SD, 1);
    ui_init();
    g_diag_active = false;
    return 0;
}

// tonepins <bclk> <lrc> <din> [sd] - play the test tone on arbitrary pins,
// driving an optional shutdown pin HIGH first. Lets a wrong DAC pinout or a
// stuck amplifier-enable be found by ear.
static int cmd_tonepins(int argc, char **argv)
{
    if (argc < 4) {
        printf("Aufruf: tonepins <bclk> <lrc> <din> [sd]\n");
        printf("Aktuell: bclk=%d lrc=%d din=%d sd=%d\n",
               DAC_BCLK, DAC_LRC, DAC_DIN, DAC_SD);
        return 1;
    }
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    int bclk = atoi(argv[1]), lrc = atoi(argv[2]), din = atoi(argv[3]);
    int sd = (argc >= 5) ? atoi(argv[4]) : -1;

    if (sd >= 0) {
        gpio_reset_pin(sd);
        gpio_config_t c = { .pin_bit_mask = 1ULL << sd, .mode = GPIO_MODE_OUTPUT };
        gpio_config(&c);
        gpio_set_level(sd, 1);
        printf("SD-Pin %d auf HIGH.\n", sd);
    }

    printf("Ton auf bclk=%d lrc=%d din=%d ...\n", bclk, lrc, din);
    audio_dac_reinit_pins(bclk, lrc, din);

    const uint32_t rate = 22050;
    const size_t samples = rate * 2;            // 2 seconds
    const size_t pcm_bytes = samples * sizeof(int16_t);
    uint8_t *buf = heap_caps_malloc(WAV_HEADER_SIZE + pcm_bytes, MALLOC_CAP_SPIRAM);
    if (buf == NULL) { g_diag_active = false; printf("Kein Speicher\n"); return 1; }

    wav_write_header(buf, rate, 1, 16, (uint32_t)pcm_bytes);
    int16_t *pcm = (int16_t *)(buf + WAV_HEADER_SIZE);
    for (size_t i = 0; i < samples; i++) {
        pcm[i] = (int16_t)(16000.0f * sinf(2.0f * 3.14159265f * 440.0f * i / rate));
    }

    membuf_t m = { .p = buf, .len = WAV_HEADER_SIZE + pcm_bytes, .pos = 0 };
    esp_err_t ret = player_play(membuf_read, &m);
    free(buf);
    printf("Wiedergabe %s\n", ret == ESP_OK ? "OK" : "FEHLGESCHLAGEN");

    audio_dac_reinit();
    if (sd >= 0) gpio_reset_pin(sd);   // never leave a pad as an output
    ui_init();                          // button back to input + pull-up
    g_diag_active = false;
    return 0;
}

// loopback [bclk lrc din] - I2S1 transmits while I2S0 reads the very same
// pads as a slave. If the pattern comes back, the ESP32 really is driving
// those pins and the fault is entirely outside the chip.
static int cmd_loopback(int argc, char **argv)
{
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    int bclk = (argc >= 4) ? atoi(argv[1]) : DAC_BCLK;
    int lrc  = (argc >= 4) ? atoi(argv[2]) : DAC_LRC;
    int din  = (argc >= 4) ? atoi(argv[3]) : DAC_DIN;

    printf("Loopback auf bclk=%d lrc=%d dout/din=%d\n", bclk, lrc, din);

    audio_dac_reinit_pins(bclk, lrc, din);
    if (audio_mic_slave_on(bclk, lrc, din) != ESP_OK) {
        printf("Slave-RX konnte nicht eingerichtet werden\n");
        g_diag_active = false;
        return 1;
    }

    i2s_channel_enable(audio_mic_handle());
    i2s_channel_enable(audio_dac_handle());

    static int16_t tx[512];
    for (size_t i = 0; i < 512; i++) tx[i] = (int16_t)(i * 137);   // known pattern

    static int16_t rx[512];
    size_t nonzero = 0, total = 0;
    int32_t peak = 0;

    for (int round = 0; round < 20; round++) {
        size_t w = 0;
        i2s_channel_write(audio_dac_handle(), tx, sizeof(tx), &w, pdMS_TO_TICKS(100));
        size_t r = 0;
        i2s_channel_read(audio_mic_handle(), rx, sizeof(rx), &r, pdMS_TO_TICKS(100));
        size_t n = r / sizeof(int16_t);
        for (size_t i = 0; i < n; i++) {
            total++;
            if (rx[i] != 0) nonzero++;
            int32_t v = rx[i] < 0 ? -rx[i] : rx[i];
            if (v > peak) peak = v;
        }
    }

    i2s_channel_disable(audio_dac_handle());
    i2s_channel_disable(audio_mic_handle());

    printf("Zurueckgelesen: %u Worte, %u nicht null, Peak %d\n",
           (unsigned)total, (unsigned)nonzero, (int)peak);
    if (nonzero > total / 10)
        printf("ERGEBNIS: ESP32 treibt die Pins. Fehler liegt AUSSERHALB des Chips.\n");
    else
        printf("ERGEBNIS: nichts zurueck - I2S-TX erzeugt auf diesen Pins keine Daten.\n");

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();
    g_diag_active = false;
    return 0;
}

// micperm <p1> <p2> <p3> <p4> - try every ordered (SCK, WS, SD) assignment from
// the given pins, driving whichever pin is left over LOW as the INMP441 L/R
// strap. Finds the wiring when only the pin group is known, not the order.
static int cmd_micperm(int argc, char **argv)
{
    if (argc < 5) { printf("Aufruf: micperm <p1> <p2> <p3> <p4>\n"); return 1; }
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    int p[4];
    for (int i = 0; i < 4; i++) p[i] = atoi(argv[i + 1]);

    printf("Permutationen ueber %d %d %d %d. Bitte durchgehend Geraeusch machen.\n",
           p[0], p[1], p[2], p[3]);
    audio_dac_deinit();

    int best_peak = 0, bs = -1, bw = -1, bd = -1, blr = -1;

    for (int a = 0; a < 4; a++)
    for (int b = 0; b < 4; b++)
    for (int c = 0; c < 4; c++) {
        if (a == b || a == c || b == c) continue;
        int leftover = 0 + 1 + 2 + 3 - a - b - c;
        int sck = p[a], ws = p[b], sd = p[c], lr = p[leftover];

        // The spare pin is probably the L/R strap, so bias it low. Use a weak
        // internal pull-down, never a push-pull output: if this pin turns out
        // to be the microphone's SD *output*, driving it fights that driver.
        // That contention browned the board out and dropped it off USB once.
        gpio_reset_pin(lr);
        gpio_config_t lc = { .pin_bit_mask = 1ULL << lr,
                             .mode = GPIO_MODE_INPUT,
                             .pull_up_en = GPIO_PULLUP_DISABLE,
                             .pull_down_en = GPIO_PULLDOWN_ENABLE };
        gpio_config(&lc);

        if (audio_mic_reinit(sck, ws, sd) != ESP_OK) continue;
        if (i2s_channel_enable(audio_mic_handle()) != ESP_OK) continue;

        static int16_t buf[512];
        int peak = 0;
        int64_t end = esp_timer_get_time() + 700000;
        while (esp_timer_get_time() < end) {
            size_t got = 0;
            i2s_channel_read(audio_mic_handle(), buf, sizeof(buf), &got, pdMS_TO_TICKS(50));
            for (size_t i = 0; i < got / sizeof(int16_t); i++) {
                int v = buf[i] < 0 ? -buf[i] : buf[i];
                if (v > peak) peak = v;
            }
        }
        i2s_channel_disable(audio_mic_handle());
        gpio_reset_pin(lr);

        printf("  SCK=%2d WS=%2d SD=%2d (L/R=%2d) -> RMS %6d%s\n",
               sck, ws, sd, lr, peak, peak > 40 ? "  <== SIGNAL" : "");
        if (peak > best_peak) { best_peak = peak; bs = sck; bw = ws; bd = sd; blr = lr; }
    }

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();
    g_diag_active = false;

    if (best_peak > 40)
        printf("TREFFER: SCK=%d WS=%d SD=%d, L/R=%d (RMS %d)\n", bs, bw, bd, blr, best_peak);
    else
        printf("Kein Treffer. Bester RMS nur %d bei SCK=%d WS=%d SD=%d.\n",
               best_peak, bs, bw, bd);
    return 0;
}

// wifi <ssid> <passwort> - store credentials in NVS and reboot into them.
// Kept as a console command so secrets stay on the device instead of in
// config.h and the repository.
static int cmd_wifi(int argc, char **argv)
{
    if (argc < 2) {
        const mrvoice_config_t *c = nvs_config_get();
        printf("Aktuell: SSID=\"%s\" (%s)\n", c->wifi_ssid,
               c->wifi_configured ? "konfiguriert" : "nicht konfiguriert");
        printf("Aufruf: wifi <ssid> <passwort>\n");
        return 0;
    }
    const char *ssid = argv[1];
    const char *pass = (argc >= 3) ? argv[2] : "";

    if (nvs_config_save_wifi(ssid, pass) != ESP_OK) {
        printf("Speichern fehlgeschlagen\n");
        return 1;
    }
    printf("WLAN gespeichert: %s. Neustart...\n", ssid);
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
    return 0;
}

// mgr <base_url> <instanz> [benutzer] [passwort] - kAIm56 manager settings.
static int cmd_mgr(int argc, char **argv)
{
    const mrvoice_config_t *c = nvs_config_get();
    if (argc < 3) {
        printf("Aktuell: url=\"%s\" instanz=\"%s\" benutzer=\"%s\" (%s)\n",
               c->base_url, c->instance, c->user,
               c->mgr_configured ? "konfiguriert" : "nicht konfiguriert");
        printf("Aufruf: mgr <base_url> <instanz> [benutzer] [passwort]\n");
        return 0;
    }
    const char *url  = argv[1];
    const char *inst = argv[2];
    const char *user = (argc >= 4) ? argv[3] : "";
    const char *pass = (argc >= 5) ? argv[4] : "";

    if (nvs_config_save_manager(url, user, pass, inst, c->prompt) != ESP_OK) {
        printf("Speichern fehlgeschlagen\n");
        return 1;
    }
    printf("Manager gespeichert: %s, Instanz %s\n", url, inst);
    return 0;
}

// scan - list the 2.4 GHz networks the radio can actually see. Talks to
// esp_wifi directly: after a failed association the manager's own scan helper
// returns an opaque failure.
static int cmd_scan(int argc, char **argv)
{
    (void)argc; (void)argv;

    // The SoftAP parks the radio on its own channel and can starve the scan,
    // so drop to STA for the duration and put the AP back afterwards.
    wifi_mode_t prev = WIFI_MODE_NULL;
    esp_wifi_get_mode(&prev);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();
    vTaskDelay(pdMS_TO_TICKS(300));

    esp_wifi_scan_stop();                       // clear any scan left running
    vTaskDelay(pdMS_TO_TICKS(100));

    wifi_scan_config_t cfg = {
        .ssid = NULL, .bssid = NULL, .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.min = 150,
        .scan_time.active.max = 400,
    };

    printf("Scanne (blockierend)...\n");
    esp_err_t err = esp_wifi_scan_start(&cfg, true);   // blocking
    if (err != ESP_OK) {
        printf("Scan fehlgeschlagen: %s\n", esp_err_to_name(err));
        if (prev != WIFI_MODE_STA) esp_wifi_set_mode(prev);
        return 1;
    }

    uint16_t count = 0;
    esp_wifi_scan_get_ap_num(&count);
    if (prev != WIFI_MODE_STA) { esp_wifi_set_mode(prev); vTaskDelay(pdMS_TO_TICKS(100)); }
    if (count == 0) {
        printf("Keine Netze gefunden - auch keine fremden.\n");
        printf("Das XIAO ESP32-S3 hat eine externe Antenne am u.FL-Stecker.\n");
        printf("Fehlt sie, ist der Empfang praktisch null. Bitte pruefen.\n");
        return 0;
    }
    if (count > 30) count = 30;

    wifi_ap_record_t *recs = calloc(count, sizeof(wifi_ap_record_t));
    if (recs == NULL) return 1;
    esp_wifi_scan_get_ap_records(&count, recs);

    printf("%u Netze:\n", (unsigned)count);
    for (int i = 0; i < count; i++) {
        printf("  %-32s Kanal %2d  %4d dBm  auth=%d\n",
               (char *)recs[i].ssid, recs[i].primary, recs[i].rssi, recs[i].authmode);
    }
    free(recs);
    return 0;
}

// net - current network state.
static int cmd_net(int argc, char **argv)
{
    (void)argc; (void)argv;
    char ip[16] = "-";
    bool up = wifi_manager_is_connected();
    if (up) wifi_manager_get_ip(ip);
    printf("WLAN: %s  IP: %s\n", up ? "verbunden" : "nicht verbunden", ip);
    printf("Zeit synchronisiert: %s\n", time(NULL) > 1700000000 ? "ja" : "nein");
    return 0;
}

// radio on|off - keep WiFi down from boot. Diagnostic for the silent mic.
static int cmd_radio(int argc, char **argv)
{
    if (argc < 2) {
        printf("Funk beim Start: %s\n", nvs_config_get()->radio_off ? "AUS" : "AN");
        printf("Aufruf: radio on|off\n");
        return 0;
    }
    bool off = (strcmp(argv[1], "off") == 0);
    nvs_config_save_radio_off(off ? 1 : 0);
    printf("Funk beim Start: %s. Neustart...\n", off ? "AUS" : "AN");
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
    return 0;
}

// vol [prozent] - playback attenuation.
static int cmd_vol(int argc, char **argv)
{
    if (argc < 2) {
        printf("Lautstaerke: %u%%\n", nvs_config_get()->spk_volume);
        printf("Aufruf: vol <0-100>\n");
        return 0;
    }
    uint16_t v = (uint16_t)atoi(argv[1]);
    nvs_config_save_volume(v);
    printf("Lautstaerke: %u%%\n", nvs_config_get()->spk_volume);
    return 0;
}

// micclk - is the ESP32 actually clocking the microphone? Samples the BCLK
// and WS pads while the mic channel runs. A generated clock shows up as a mix
// of highs and lows; a static level means no clock leaves the chip, which
// would explain a microphone that never drives its data line.
static int cmd_micclk(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    i2s_channel_enable(audio_mic_handle());

    struct { int pin; const char *name; } pins[] = {
        { MIC_SCK, "SCK" }, { MIC_WS, "WS" }, { MIC_SD, "SD" }, { MIC_LR, "L/R" },
    };

    // Note: BCLK is driven as a plain output, so its input buffer is off and
    // gpio_get_level reads 0 no matter what the pin does. Forcing the input on
    // with gpio_set_direction tears down the matrix routing and makes things
    // worse, so SCK simply cannot be observed this way - only WS and the data
    // line are meaningful here.

    // Keep the DMA busy while sampling: on some drivers the bit clock only
    // runs while a transfer is in flight, and an idle channel would look like
    // a dead clock.
    static int16_t drain[256];
    size_t got = 0;

    for (size_t k = 0; k < 4; k++) {
        int high = 0;
        const int N = 20000;
        for (int i = 0; i < N; i++) {
            if ((i & 0x3ff) == 0) {
                i2s_channel_read(audio_mic_handle(), drain, sizeof(drain), &got, 0);
            }
            if (gpio_get_level(pins[k].pin)) high++;
        }
        int pct = high * 100 / N;
        const char *verdict;
        if (pct > 5 && pct < 95)      verdict = "wechselt -> Takt vorhanden";
        else if (pct >= 95)           verdict = "dauerhaft HIGH";
        else                          verdict = "dauerhaft LOW";
        printf("  %-4s GPIO %2d: %3d%% HIGH  -> %s\n",
               pins[k].name, pins[k].pin, pct, verdict);
    }

    i2s_channel_disable(audio_mic_handle());
    printf("Erwartet: SCK und WS wechseln, L/R dauerhaft LOW.\n");
    g_diag_active = false;
    return 0;
}

// micpull - decisive test on the data line. Runs the I2S clock normally but
// forces a pull-down on the SD pad. A microphone that is driving the line wins
// against the weak pull; if the samples flip to zero, nothing is driving it
// and the earlier all-ones reading was just the pad's own pull-up.
static int cmd_micpull(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    const mrvoice_config_t *cfg = nvs_config_get();
    int sd = cfg->gpio.mic_sd ? cfg->gpio.mic_sd : MIC_SD;

    static uint32_t raw[256];
    for (int pass = 0; pass < 3; pass++) {
        const char *what = pass == 0 ? "Pull-up" : (pass == 1 ? "Pull-down" : "kein Pull");

        audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
        if (pass == 0) gpio_set_pull_mode(sd, GPIO_PULLUP_ONLY);
        else if (pass == 1) gpio_set_pull_mode(sd, GPIO_PULLDOWN_ONLY);
        else gpio_set_pull_mode(sd, GPIO_FLOATING);

        i2s_channel_enable(audio_mic_handle());
        uint32_t or_all = 0, and_all = 0xffffffff;
        size_t words = 0;
        int64_t end = esp_timer_get_time() + 600000;
        while (esp_timer_get_time() < end) {
            size_t got = 0;
            i2s_channel_read(audio_mic_handle(), raw, sizeof(raw), &got, pdMS_TO_TICKS(50));
            for (size_t i = 0; i < got / sizeof(uint32_t); i++) {
                or_all |= raw[i]; and_all &= raw[i]; words++;
            }
        }
        i2s_channel_disable(audio_mic_handle());
        printf("  %-10s Worte=%5u  OR=%08lx  AND=%08lx\n",
               what, (unsigned)words, (unsigned long)or_all, (unsigned long)and_all);
    }

    gpio_set_pull_mode(sd, GPIO_FLOATING);
    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    printf("Folgt das Ergebnis dem Pull, treibt das Mikrofon die Leitung nicht.\n");
    g_diag_active = false;
    return 0;
}

// micdump - record two seconds and print the WAV as base64, so the actual
// audio can be decoded and listened to off-device.
static int cmd_micdump(int argc, char **argv)
{
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    // "micdump quiet" powers the radio down first. The microphone worked
    // before WiFi ever associated and has been dead since; this separates a
    // supply problem from anything in the audio path.
    bool quiet = (argc >= 2 && strcmp(argv[1], "quiet") == 0);
    if (quiet) {
        printf("WLAN wird abgeschaltet...\n");
        esp_wifi_stop();
        vTaskDelay(pdMS_TO_TICKS(800));
    }

    printf("Nehme 2 s auf...\n");
    capture_start();
    int64_t end = esp_timer_get_time() + 2000000;
    while (esp_timer_get_time() < end) {
        capture_pump();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    capture_stop();

    size_t len = 0;
    const uint8_t *wav = capture_wav(&len);

    printf("---BEGIN WAV %u---\n", (unsigned)len);
    static unsigned char b64[4096];
    const size_t chunk = 2880;          // encodes to 3840 base64 chars
    for (size_t off = 0; off < len; off += chunk) {
        size_t n = (len - off < chunk) ? (len - off) : chunk;
        size_t out_len = 0;
        if (mbedtls_base64_encode(b64, sizeof(b64), &out_len, wav + off, n) != 0) break;
        b64[out_len] = 0;
        printf("%s\n", (char *)b64);
        vTaskDelay(pdMS_TO_TICKS(5));   // let the USB CDC drain
    }
    printf("---END WAV---\n");

    if (quiet) {
        esp_wifi_start();
        vTaskDelay(pdMS_TO_TICKS(300));
        esp_wifi_connect();
        printf("WLAN wieder eingeschaltet.\n");
    }
    g_diag_active = false;
    return 0;
}

// gain [prozent] - capture gain, 100 = unity.
static int cmd_gain(int argc, char **argv)
{
    if (argc < 2) {
        printf("Aufnahmeverstaerkung: %u%%\n", nvs_config_get()->mic_gain);
        printf("Aufruf: gain <100-4000>\n");
        return 0;
    }
    nvs_config_save_gain((uint16_t)atoi(argv[1]));
    printf("Aufnahmeverstaerkung: %u%%\n", nvs_config_get()->mic_gain);
    return 0;
}

// say <text> - fetch one TTS sentence and play it. Isolates the streaming
// playback path from the full probe chain.
static int cmd_say(int argc, char **argv)
{
    if (argc < 2) { printf("Aufruf: say <text>\n"); return 1; }
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    char text[256] = {0};
    for (int i = 1; i < argc; i++) {
        strlcat(text, argv[i], sizeof(text));
        if (i + 1 < argc) strlcat(text, " ", sizeof(text));
    }

    printf("TTS: \"%s\"\n", text);
    mgr_stream_t *st = NULL;
    if (mgr_tts_open(text, &st) != ESP_OK) {
        printf("TTS fehlgeschlagen\n"); g_diag_active = false; return 1;
    }
    esp_err_t ret = player_play(tts_shim, st);
    mgr_tts_close(st);
    printf("Wiedergabe %s\n", ret == ESP_OK ? "OK" : "FEHLGESCHLAGEN");
    g_diag_active = false;
    return ret == ESP_OK ? 0 : 1;
}

// saybuf <text> - same, but download the whole WAV into PSRAM first and only
// then play it. If this is clean while `say` is distorted, the fault is the
// network starving the I2S DMA, not the audio format.
static int cmd_saybuf(int argc, char **argv)
{
    if (argc < 2) { printf("Aufruf: saybuf <text>\n"); return 1; }
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    char text[256] = {0};
    for (int i = 1; i < argc; i++) {
        strlcat(text, argv[i], sizeof(text));
        if (i + 1 < argc) strlcat(text, " ", sizeof(text));
    }

    mgr_stream_t *st = NULL;
    if (mgr_tts_open(text, &st) != ESP_OK) {
        printf("TTS fehlgeschlagen\n"); g_diag_active = false; return 1;
    }
    size_t cap = 512 * 1024, len = 0;
    uint8_t *buf = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
    if (buf == NULL) { mgr_tts_close(st); g_diag_active = false; return 1; }
    while (len < cap) {
        int n = mgr_tts_read(st, buf + len, cap - len);
        if (n <= 0) break;
        len += (size_t)n;
    }
    mgr_tts_close(st);
    printf("%u Byte gepuffert, spiele ab...\n", (unsigned)len);

    membuf_t m = { .p = buf, .len = len, .pos = 0 };
    esp_err_t ret = player_play(membuf_read, &m);
    free(buf);
    printf("Wiedergabe %s\n", ret == ESP_OK ? "OK" : "FEHLGESCHLAGEN");
    g_diag_active = false;
    return ret == ESP_OK ? 0 : 1;
}

// micall - sweep every ordered (SCK, WS, SD) triple over all pins that are not
// claimed by the amplifier or the button, biasing the unused ones low. Slower
// than micperm but does not assume the pin group is known.
static int cmd_micall(int argc, char **argv)
{
    (void)argc; (void)argv;
    if (g_state != ST_IDLE) { printf("Nicht im Leerlauf\n"); return 1; }
    g_diag_active = true;

    static const int pool[] = { 1, 2, 7, 8, 9, 44 };
    const int N = sizeof(pool) / sizeof(pool[0]);

    printf("Sweep ueber %d Pins, %d Kombinationen. Durchgehend Geraeusch machen.\n",
           N, N * (N - 1) * (N - 2));
    audio_dac_deinit();

    int best = 0, bs = -1, bw = -1, bd = -1;
    for (int a = 0; a < N; a++)
    for (int b = 0; b < N; b++)
    for (int c = 0; c < N; c++) {
        if (a == b || a == c || b == c) continue;
        int sck = pool[a], ws = pool[b], sd = pool[c];

        // Bias every unused pin low; the L/R strap is somewhere among them.
        for (int k = 0; k < N; k++) {
            if (k == a || k == b || k == c) continue;
            gpio_reset_pin(pool[k]);
            gpio_config_t lc = { .pin_bit_mask = 1ULL << pool[k],
                                 .mode = GPIO_MODE_INPUT,
                                 .pull_up_en = GPIO_PULLUP_DISABLE,
                                 .pull_down_en = GPIO_PULLDOWN_ENABLE };
            gpio_config(&lc);
        }

        int32_t rms = probe_mic_pins(sck, ws, sd, 500);
        if (rms > 40) printf("  SCK=%2d WS=%2d SD=%2d -> RMS %5d  <== SIGNAL\n", sck, ws, sd, (int)rms);
        if (rms > best) { best = rms; bs = sck; bw = ws; bd = sd; }
    }

    audio_mic_reinit(MIC_SCK, MIC_WS, MIC_SD);
    audio_dac_reinit();
    g_diag_active = false;

    if (best > 40) printf("TREFFER: SCK=%d WS=%d SD=%d (RMS %d)\n", bs, bw, bd, best);
    else printf("Kein Treffer ueber alle %d Kombinationen. Bester RMS %d.\n",
                N * (N - 1) * (N - 2), best);
    return 0;
}

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
    rc.task_stack_size = 8192;   // diagnostics call into the audio path

    esp_console_dev_usb_serial_jtag_config_t dev =
        ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();

    if (esp_console_new_repl_usb_serial_jtag(&dev, &rc, &repl) != ESP_OK) return;

    const esp_console_cmd_t probe = {
        .command = "probe",
        .help = "Selbsttest: TTS -> STT -> Chat",
        .func = cmd_probe,
    };
    esp_console_cmd_register(&probe);

    const esp_console_cmd_t mic  = { .command = "mic",  .help = "2 s aufnehmen, Pegel melden", .func = cmd_mic };
    const esp_console_cmd_t tone = { .command = "tone", .help = "440 Hz Testton ueber I2S",    .func = cmd_tone };
    const esp_console_cmd_t led  = { .command = "led",  .help = "LED-Zustaende durchlaufen",   .func = cmd_led };
    esp_console_cmd_register(&mic);
    esp_console_cmd_register(&tone);
    esp_console_cmd_register(&led);

    const esp_console_cmd_t micscan = {
        .command = "micscan",
        .help = "Mikrofon-Pins durchprobieren (optional: micscan <sck> <ws> <sd>)",
        .func = cmd_micscan,
    };
    esp_console_cmd_register(&micscan);

    const esp_console_cmd_t pintest = {
        .command = "pintest",
        .help = "Prueft, ob an einem Pin etwas haengt: pintest <gpio> ...",
        .func = cmd_pintest,
    };
    esp_console_cmd_register(&pintest);

    const esp_console_cmd_t micraw = {
        .command = "micraw",
        .help = "Slot-Formate durchprobieren und Rohdaten zeigen",
        .func = cmd_micraw,
    };
    esp_console_cmd_register(&micraw);

    const esp_console_cmd_t btn = {
        .command = "btn",
        .help = "Taster pruefen: Pegel und erkannte Gesten anzeigen",
        .func = cmd_btn,
    };
    esp_console_cmd_register(&btn);

    const esp_console_cmd_t btnscan = {
        .command = "btnscan",
        .help = "Taster auf allen Pins suchen",
        .func = cmd_btnscan,
    };
    esp_console_cmd_register(&btnscan);

    const esp_console_cmd_t tonepins = {
        .command = "tonepins",
        .help = "Ton auf freien Pins: tonepins <bclk> <lrc> <din> [sd]",
        .func = cmd_tonepins,
    };
    esp_console_cmd_register(&tonepins);

    const esp_console_cmd_t loopback = {
        .command = "loopback",
        .help = "I2S-TX intern zurueckmessen: loopback [bclk lrc din]",
        .func = cmd_loopback,
    };
    esp_console_cmd_register(&loopback);

    const esp_console_cmd_t micperm = {
        .command = "micperm",
        .help = "Alle Mic-Zuordnungen testen: micperm <p1> <p2> <p3> <p4>",
        .func = cmd_micperm,
    };
    esp_console_cmd_register(&micperm);
    const esp_console_cmd_t micall = { .command = "micall", .help = "Alle freien Pins durchsuchen", .func = cmd_micall };
    esp_console_cmd_register(&micall);

    const esp_console_cmd_t wifi = { .command = "wifi", .help = "WLAN setzen: wifi <ssid> <passwort>", .func = cmd_wifi };
    const esp_console_cmd_t mgr  = { .command = "mgr",  .help = "Manager setzen: mgr <url> <instanz> [user] [pass]", .func = cmd_mgr };
    const esp_console_cmd_t scan = { .command = "scan", .help = "Sichtbare WLANs auflisten", .func = cmd_scan };
    esp_console_cmd_register(&scan);
    const esp_console_cmd_t net  = { .command = "net",  .help = "Netzwerkstatus anzeigen", .func = cmd_net };
    esp_console_cmd_register(&wifi);
    esp_console_cmd_register(&mgr);
    esp_console_cmd_register(&net);
    const esp_console_cmd_t say    = { .command = "say",    .help = "Satz per TTS abspielen (gestreamt)", .func = cmd_say };
    const esp_console_cmd_t saybuf = { .command = "saybuf", .help = "Satz per TTS, erst puffern",        .func = cmd_saybuf };
    const esp_console_cmd_t vol = { .command = "vol", .help = "Lautstaerke setzen: vol <0-100>", .func = cmd_vol };
    const esp_console_cmd_t radio = { .command = "radio", .help = "Funk beim Start: radio on|off", .func = cmd_radio };
    esp_console_cmd_register(&radio);
    esp_console_cmd_register(&vol);
    const esp_console_cmd_t gain = { .command = "gain", .help = "Aufnahmeverstaerkung: gain <100-4000>", .func = cmd_gain };
    esp_console_cmd_register(&gain);
    const esp_console_cmd_t micdump = { .command = "micdump", .help = "2 s aufnehmen und als base64 ausgeben", .func = cmd_micdump };
    esp_console_cmd_register(&micdump);
    const esp_console_cmd_t micpull = { .command = "micpull", .help = "Treibt das Mikrofon die Datenleitung?", .func = cmd_micpull };
    esp_console_cmd_register(&micpull);
    const esp_console_cmd_t micclk = { .command = "micclk", .help = "Erzeugt der ESP32 den Mikrofontakt?", .func = cmd_micclk };
    esp_console_cmd_register(&micclk);
    esp_console_cmd_register(&say);
    esp_console_cmd_register(&saybuf);

    esp_console_register_help_command();
    esp_console_start_repl(repl);
}

void app_start(void)
{
    g_sentences = xQueueCreate(SENTENCE_QUEUE_DEPTH, sizeof(char *));
    new_chat_id();
    console_start();

    xTaskCreate(speech_task, "speech", 8192, NULL, 5, NULL);
    xTaskCreate(conversation_task, "conv", 8192, NULL, 5, NULL);
}
