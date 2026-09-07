#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Maximum string lengths for configuration values
#define NVS_CFG_URL_MAX_LEN       128
#define NVS_CFG_USER_MAX_LEN       32
#define NVS_CFG_PASS_MAX_LEN       64
#define NVS_CFG_INSTANCE_MAX_LEN   48
#define NVS_CFG_PROMPT_MAX_LEN    512
#define NVS_CFG_WIFI_SSID_LEN      32
#define NVS_CFG_WIFI_PASS_LEN      64

// GPIO pin configuration structure
typedef struct {
    uint8_t mic_sck;
    uint8_t mic_ws;
    uint8_t mic_sd;
    uint8_t dac_bclk;
    uint8_t dac_lrc;
    uint8_t dac_din;
    uint8_t dac_sd;
    uint8_t button;
} mrvoice_gpio_config_t;

// Complete device configuration
typedef struct {
    // Config version (for migration)
    uint8_t version;

    // WiFi credentials
    char wifi_ssid[NVS_CFG_WIFI_SSID_LEN];
    char wifi_pass[NVS_CFG_WIFI_PASS_LEN];
    bool wifi_configured;

    // kAIm56 manager settings
    char base_url[NVS_CFG_URL_MAX_LEN];
    char user[NVS_CFG_USER_MAX_LEN];
    char pass[NVS_CFG_PASS_MAX_LEN];
    char instance[NVS_CFG_INSTANCE_MAX_LEN];
    char prompt[NVS_CFG_PROMPT_MAX_LEN];
    bool mgr_configured;

    // Recording guards
    uint16_t min_ms;
    uint16_t max_s;
    uint16_t spk_volume;   // percent, 0-100
    uint16_t mic_gain;     // percent, 100 = unity
    uint16_t radio_off;    // 1 = never bring WiFi up (diagnostic)

    // GPIO configuration
    mrvoice_gpio_config_t gpio;
} mrvoice_config_t;

/**
 * Initialize NVS configuration system.
 * Loads config from NVS or initializes with defaults if no config exists.
 *
 * @return ESP_OK on success
 */
esp_err_t nvs_config_init(void);

/**
 * Get pointer to current configuration.
 * Configuration is loaded on init and cached in RAM.
 *
 * @return Pointer to config struct (never NULL after init)
 */
const mrvoice_config_t *nvs_config_get(void);

/**
 * Get mutable pointer to current configuration for modification.
 * Call nvs_config_save() after making changes.
 *
 * @return Pointer to config struct
 */
mrvoice_config_t *nvs_config_get_mutable(void);

/**
 * Save current configuration to NVS.
 *
 * @return ESP_OK on success
 */
esp_err_t nvs_config_save(void);

/**
 * Save WiFi credentials to NVS.
 * Updates both in-memory config and persistent storage.
 *
 * @param ssid WiFi network SSID
 * @param password WiFi password
 * @return ESP_OK on success
 */
esp_err_t nvs_config_save_wifi(const char *ssid, const char *password);

/**
 * Save kAIm56 manager configuration to NVS.
 *
 * @param base_url Manager base URL, e.g. http://manager.example:8700
 * @param user Basic Auth user (may be empty)
 * @param pass Basic Auth password (may be empty)
 * @param instance Agent instance name
 * @param prompt Prompt prepended to every turn
 * @return ESP_OK on success
 */
esp_err_t nvs_config_save_manager(const char *base_url, const char *user,
                                  const char *pass, const char *instance,
                                  const char *prompt);

/**
 * Save recording guards to NVS.
 *
 * @param min_ms Recordings shorter than this are discarded
 * @param max_s Recording cap in seconds (bounded by the PSRAM budget)
 * @return ESP_OK on success
 */
esp_err_t nvs_config_save_recording(uint16_t min_ms, uint16_t max_s);

// Playback attenuation in percent. Piper output peaks at full scale, which
// clips on the MAX98357A; anything above ~70 distorts on this hardware.
esp_err_t nvs_config_save_volume(uint16_t percent);

// Capture gain in percent, 100 = unity.
esp_err_t nvs_config_save_gain(uint16_t percent);

// Diagnostic: keep the radio down from boot, to see whether WiFi activity is
// what stops the microphone from delivering data.
esp_err_t nvs_config_save_radio_off(uint16_t off);

/**
 * Save GPIO configuration to NVS.
 *
 * @param gpio GPIO pin assignments
 * @return ESP_OK on success
 */
esp_err_t nvs_config_save_gpio(const mrvoice_gpio_config_t *gpio);

/**
 * Factory reset - erase all configuration and restore defaults.
 *
 * @return ESP_OK on success
 */
esp_err_t nvs_config_factory_reset(void);

/**
 * Check if initial setup is needed (no WiFi configured).
 *
 * @return true if captive portal should be started
 */
bool nvs_config_needs_setup(void);

#ifdef __cplusplus
}
#endif
