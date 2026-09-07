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
