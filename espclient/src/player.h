#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

typedef int (*player_read_fn)(void *ctx, uint8_t *buf, size_t len);

// Reads a WAV stream through read() and plays it. Blocks until done.
esp_err_t player_play(player_read_fn read, void *ctx);

// Requests an early stop from another task.
void player_abort(void);

// Call after the DAC channel has been recreated elsewhere.
void player_forget_rate(void);
