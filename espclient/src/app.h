#pragma once
#include "esp_err.h"

// Creates the conversation and speech tasks and the serial console.
void app_start(void);

// A.6 probe: text -> TTS -> STT -> chat, without the microphone.
esp_err_t app_probe(void);
