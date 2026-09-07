#pragma once

#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Start the configuration HTTP server.
 * Serves web UI for configuring WiFi, LiveKit, audio settings.
 *
 * @return ESP_OK on success
 */
esp_err_t config_server_start(void);

/**
 * Stop the configuration HTTP server.
 *
 * @return ESP_OK on success
 */
esp_err_t config_server_stop(void);

/**
 * Check if server is running.
 *
 * @return true if running
 */
bool config_server_is_running(void);

#ifdef __cplusplus
}
#endif
