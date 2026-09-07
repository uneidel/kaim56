#pragma once

#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// WiFi manager states
typedef enum {
    WIFI_MGR_STATE_IDLE,           // Not started
    WIFI_MGR_STATE_CONNECTING,     // Trying to connect to saved network
    WIFI_MGR_STATE_CONNECTED,      // Connected to WiFi as station
    WIFI_MGR_STATE_PORTAL,         // Running captive portal (SoftAP mode)
    WIFI_MGR_STATE_FAILED,         // Connection failed, portal available
} wifi_manager_state_t;

// Callback for state changes
typedef void (*wifi_manager_state_cb_t)(wifi_manager_state_t state, void *ctx);

// WiFi scan result
typedef struct {
    char ssid[33];
    int8_t rssi;
    uint8_t auth_mode;  // wifi_auth_mode_t
} wifi_scan_result_t;

/**
 * Initialize WiFi manager.
 * Must be called after nvs_config_init().
 *
 * @param state_cb Optional callback for state changes
 * @param ctx Context passed to callback
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_init(wifi_manager_state_cb_t state_cb, void *ctx);

/**
 * Start WiFi manager.
 * If WiFi is configured in NVS, attempts to connect.
 * If not configured or connection fails, starts captive portal.
 *
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_start(void);

/**
 * Stop WiFi manager.
 * Disconnects from WiFi and stops any running servers.
 *
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_stop(void);

/**
 * Get current WiFi manager state.
 *
 * @return Current state
 */
wifi_manager_state_t wifi_manager_get_state(void);

/**
 * Check if connected to WiFi station mode.
 *
 * @return true if connected
 */
bool wifi_manager_is_connected(void);

/**
 * Get IP address (only valid when connected).
 *
 * @param ip_str Buffer for IP string (min 16 bytes)
 * @return ESP_OK on success, ESP_ERR_INVALID_STATE if not connected
 */
esp_err_t wifi_manager_get_ip(char *ip_str);

/**
 * Scan for available WiFi networks.
 * Results are stored internally and can be retrieved with wifi_manager_get_scan_results().
 *
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_scan(void);

/**
 * Get scan results after wifi_manager_scan().
 *
 * @param results Array to fill with results
 * @param max_results Size of results array
 * @param num_results Output: actual number of results
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_get_scan_results(wifi_scan_result_t *results,
                                         size_t max_results,
                                         size_t *num_results);

/**
 * Connect to a WiFi network.
 * Saves credentials to NVS on successful connection.
 *
 * @param ssid Network SSID
 * @param password Network password (can be NULL for open networks)
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_connect(const char *ssid, const char *password);

/**
 * Disconnect and forget saved network.
 * Returns to captive portal mode.
 *
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_disconnect(void);

/**
 * Force start captive portal mode.
 * Useful for manual reconfiguration.
 *
 * @return ESP_OK on success
 */
esp_err_t wifi_manager_start_portal(void);

#ifdef __cplusplus
}
#endif
