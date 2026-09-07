#include "nvs_config.h"
#include "config.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"
#include <string.h>

static const char *TAG = "nvs_config";

// NVS namespace and keys
#define NVS_NAMESPACE "mrvoice"
#define NVS_KEY_VERSION    "version"
#define NVS_KEY_WIFI_SSID  "wifi_ssid"
#define NVS_KEY_WIFI_PASS  "wifi_pass"
#define NVS_KEY_BASE_URL   "base_url"
#define NVS_KEY_MGR_USER   "mgr_user"
#define NVS_KEY_MGR_PASS   "mgr_pass"
#define NVS_KEY_INSTANCE   "instance"
#define NVS_KEY_PROMPT     "prompt"
#define NVS_KEY_MIN_MS     "min_ms"
#define NVS_KEY_MAX_S      "max_s"
#define NVS_KEY_SPK_VOL    "spk_vol"
#define NVS_KEY_MIC_GAIN   "mic_gain"
#define NVS_KEY_RADIO_OFF  "radio_off"
#define NVS_KEY_GPIO       "gpio_pins"

// Current config version - increment when format changes
#define CONFIG_VERSION 2

// In-memory configuration cache
static mrvoice_config_t g_config;
static bool g_initialized = false;

// Load default configuration from config.h
static void load_defaults(void)
{
    memset(&g_config, 0, sizeof(g_config));
    g_config.version = CONFIG_VERSION;

    // WiFi defaults
    strlcpy(g_config.wifi_ssid, DEFAULT_WIFI_SSID, NVS_CFG_WIFI_SSID_LEN);
    strlcpy(g_config.wifi_pass, DEFAULT_WIFI_PASS, NVS_CFG_WIFI_PASS_LEN);
    g_config.wifi_configured = false;

    // Manager defaults
    strlcpy(g_config.base_url, DEFAULT_BASE_URL, NVS_CFG_URL_MAX_LEN);
    strlcpy(g_config.user,     DEFAULT_MGR_USER, NVS_CFG_USER_MAX_LEN);
    strlcpy(g_config.pass,     DEFAULT_MGR_PASS, NVS_CFG_PASS_MAX_LEN);
    strlcpy(g_config.instance, DEFAULT_INSTANCE, NVS_CFG_INSTANCE_MAX_LEN);
    strlcpy(g_config.prompt,   DEFAULT_PROMPT,   NVS_CFG_PROMPT_MAX_LEN);
    g_config.mgr_configured = false;

    // Recording guards
    g_config.min_ms = DEFAULT_MIN_MS;
    g_config.max_s  = DEFAULT_MAX_S;
    g_config.spk_volume = DEFAULT_SPK_VOLUME;
    g_config.mic_gain = DEFAULT_MIC_GAIN;
    g_config.radio_off = 0;

    // GPIO defaults
    g_config.gpio.mic_sck = DEFAULT_MIC_SCK;
    g_config.gpio.mic_ws = DEFAULT_MIC_WS;
    g_config.gpio.mic_sd = DEFAULT_MIC_SD;
    g_config.gpio.dac_bclk = DEFAULT_DAC_BCLK;
    g_config.gpio.dac_lrc = DEFAULT_DAC_LRC;
    g_config.gpio.dac_din = DEFAULT_DAC_DIN;
    g_config.gpio.dac_sd = DEFAULT_DAC_SD;
    g_config.gpio.button = DEFAULT_BUTTON_GPIO;
}

// Helper: read string from NVS with fallback
static void nvs_read_str(nvs_handle_t handle, const char *key, char *buf, size_t max_len, const char *fallback)
{
    size_t len = max_len;
    if (nvs_get_str(handle, key, buf, &len) != ESP_OK) {
        strncpy(buf, fallback, max_len - 1);
        buf[max_len - 1] = '\0';
    }
}

// Load configuration from NVS
static esp_err_t load_from_nvs(void)
{
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "No saved config, using defaults");
        return ret;
    }

    // Check version
    uint8_t version = 0;
    nvs_get_u8(handle, NVS_KEY_VERSION, &version);
    if (version != CONFIG_VERSION) {
        ESP_LOGW(TAG, "Config version mismatch (%d vs %d), using defaults", version, CONFIG_VERSION);
        nvs_close(handle);
        return ESP_ERR_INVALID_VERSION;
    }

    // WiFi
    nvs_read_str(handle, NVS_KEY_WIFI_SSID, g_config.wifi_ssid, NVS_CFG_WIFI_SSID_LEN, DEFAULT_WIFI_SSID);
    nvs_read_str(handle, NVS_KEY_WIFI_PASS, g_config.wifi_pass, NVS_CFG_WIFI_PASS_LEN, DEFAULT_WIFI_PASS);
    // Mark as configured if SSID was actually saved (not just default)
    size_t len = NVS_CFG_WIFI_SSID_LEN;
    g_config.wifi_configured = (nvs_get_str(handle, NVS_KEY_WIFI_SSID, NULL, &len) == ESP_OK);

    // Manager
    nvs_read_str(handle, NVS_KEY_BASE_URL, g_config.base_url, NVS_CFG_URL_MAX_LEN, DEFAULT_BASE_URL);
    nvs_read_str(handle, NVS_KEY_MGR_USER, g_config.user, NVS_CFG_USER_MAX_LEN, DEFAULT_MGR_USER);
    nvs_read_str(handle, NVS_KEY_MGR_PASS, g_config.pass, NVS_CFG_PASS_MAX_LEN, DEFAULT_MGR_PASS);
    nvs_read_str(handle, NVS_KEY_INSTANCE, g_config.instance, NVS_CFG_INSTANCE_MAX_LEN, DEFAULT_INSTANCE);
    nvs_read_str(handle, NVS_KEY_PROMPT, g_config.prompt, NVS_CFG_PROMPT_MAX_LEN, DEFAULT_PROMPT);
    g_config.mgr_configured = (g_config.base_url[0] != '\0' && g_config.instance[0] != '\0');

    // Recording guards
    if (nvs_get_u16(handle, NVS_KEY_MIN_MS, &g_config.min_ms) != ESP_OK) {
        g_config.min_ms = DEFAULT_MIN_MS;
    }
    if (nvs_get_u16(handle, NVS_KEY_MAX_S, &g_config.max_s) != ESP_OK) {
        g_config.max_s = DEFAULT_MAX_S;
    }
    if (nvs_get_u16(handle, NVS_KEY_SPK_VOL, &g_config.spk_volume) != ESP_OK) {
        g_config.spk_volume = DEFAULT_SPK_VOLUME;
    g_config.mic_gain = DEFAULT_MIC_GAIN;
    g_config.radio_off = 0;
    }

    // GPIO
    size_t blob_len = sizeof(mrvoice_gpio_config_t);
    if (nvs_get_blob(handle, NVS_KEY_GPIO, &g_config.gpio, &blob_len) != ESP_OK) {
        // Use defaults
        g_config.gpio.mic_sck = DEFAULT_MIC_SCK;
        g_config.gpio.mic_ws = DEFAULT_MIC_WS;
        g_config.gpio.mic_sd = DEFAULT_MIC_SD;
        g_config.gpio.dac_bclk = DEFAULT_DAC_BCLK;
        g_config.gpio.dac_lrc = DEFAULT_DAC_LRC;
        g_config.gpio.dac_din = DEFAULT_DAC_DIN;
        g_config.gpio.dac_sd = DEFAULT_DAC_SD;
        g_config.gpio.button = DEFAULT_BUTTON_GPIO;
    }

    nvs_close(handle);
    ESP_LOGI(TAG, "Configuration loaded from NVS");
    return ESP_OK;
}

esp_err_t nvs_config_init(void)
{
    if (g_initialized) {
        return ESP_OK;
    }

    // Initialize NVS flash
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition needs erase");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Load defaults first
    load_defaults();

    // Try to load from NVS (will override defaults if successful)
    load_from_nvs();

    g_initialized = true;
    ESP_LOGI(TAG, "NVS-Konfiguration geladen (WLAN: %s, Manager: %s)",
             g_config.wifi_configured ? "konfiguriert" : "nicht konfiguriert",
             g_config.mgr_configured ? "konfiguriert" : "nicht konfiguriert");

    return ESP_OK;
}

const mrvoice_config_t *nvs_config_get(void)
{
    return &g_config;
}

mrvoice_config_t *nvs_config_get_mutable(void)
{
    return &g_config;
}

esp_err_t nvs_config_save(void)
{
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open NVS for writing: %s", esp_err_to_name(ret));
        return ret;
    }

    // Save version
    nvs_set_u8(handle, NVS_KEY_VERSION, g_config.version);

    // WiFi
    if (g_config.wifi_configured) {
        nvs_set_str(handle, NVS_KEY_WIFI_SSID, g_config.wifi_ssid);
        nvs_set_str(handle, NVS_KEY_WIFI_PASS, g_config.wifi_pass);
    }

    // Manager
    if (g_config.mgr_configured) {
        nvs_set_str(handle, NVS_KEY_BASE_URL, g_config.base_url);
        nvs_set_str(handle, NVS_KEY_MGR_USER, g_config.user);
        nvs_set_str(handle, NVS_KEY_MGR_PASS, g_config.pass);
        nvs_set_str(handle, NVS_KEY_INSTANCE, g_config.instance);
        nvs_set_str(handle, NVS_KEY_PROMPT, g_config.prompt);
    }

    // Recording guards
    nvs_set_u16(handle, NVS_KEY_MIN_MS, g_config.min_ms);
    nvs_set_u16(handle, NVS_KEY_MAX_S, g_config.max_s);
    nvs_set_u16(handle, NVS_KEY_SPK_VOL, g_config.spk_volume);
    nvs_set_u16(handle, NVS_KEY_MIC_GAIN, g_config.mic_gain);
    nvs_set_u16(handle, NVS_KEY_RADIO_OFF, g_config.radio_off);

    // GPIO
    nvs_set_blob(handle, NVS_KEY_GPIO, &g_config.gpio, sizeof(mrvoice_gpio_config_t));

    ret = nvs_commit(handle);
    nvs_close(handle);

    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Configuration saved to NVS");
    } else {
        ESP_LOGE(TAG, "Failed to commit NVS: %s", esp_err_to_name(ret));
    }

    return ret;
}

esp_err_t nvs_config_save_wifi(const char *ssid, const char *password)
{
    if (ssid == NULL) return ESP_ERR_INVALID_ARG;

    strlcpy(g_config.wifi_ssid, ssid, NVS_CFG_WIFI_SSID_LEN);

    if (password != NULL) {
        strlcpy(g_config.wifi_pass, password, NVS_CFG_WIFI_PASS_LEN);
    } else {
        g_config.wifi_pass[0] = '\0';
    }

    g_config.wifi_configured = true;
    return nvs_config_save();
}

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

esp_err_t nvs_config_save_volume(uint16_t percent)
{
    if (percent > 100) percent = 100;
    g_config.spk_volume = percent;
    return nvs_config_save();
}

esp_err_t nvs_config_save_gain(uint16_t percent)
{
    if (percent < 100)  percent = 100;
    if (percent > 4000) percent = 4000;
    g_config.mic_gain = percent;
    return nvs_config_save();
}

esp_err_t nvs_config_save_radio_off(uint16_t off)
{
    g_config.radio_off = off ? 1 : 0;
    return nvs_config_save();
}

esp_err_t nvs_config_save_gpio(const mrvoice_gpio_config_t *gpio)
{
    if (gpio == NULL) return ESP_ERR_INVALID_ARG;
    memcpy(&g_config.gpio, gpio, sizeof(mrvoice_gpio_config_t));
    return nvs_config_save();
}

esp_err_t nvs_config_factory_reset(void)
{
    ESP_LOGW(TAG, "Factory reset initiated");

    // Erase NVS namespace
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret == ESP_OK) {
        nvs_erase_all(handle);
        nvs_commit(handle);
        nvs_close(handle);
    }

    // Reload defaults
    load_defaults();

    ESP_LOGI(TAG, "Factory reset complete");
    return ESP_OK;
}

bool nvs_config_needs_setup(void)
{
    return !g_config.wifi_configured;
}
