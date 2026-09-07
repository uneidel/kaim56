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
