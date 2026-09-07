#include "wifi_manager.h"
#include "nvs_config.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include <string.h>

static const char *TAG = "wifi_mgr";

// Event bits
#define WIFI_CONNECTED_BIT    BIT0
#define WIFI_FAIL_BIT         BIT1
#define WIFI_SCAN_DONE_BIT    BIT2

// Configuration
#define SOFTAP_SSID_PREFIX    "MrVoice-"
#define SOFTAP_CHANNEL        1
#define SOFTAP_MAX_CONN       4
#define WIFI_CONNECT_TIMEOUT_MS  15000
#define WIFI_RETRY_COUNT      3
#define DNS_PORT              53

// State
static wifi_manager_state_t g_state = WIFI_MGR_STATE_IDLE;
static wifi_manager_state_cb_t g_state_cb = NULL;
static void *g_state_cb_ctx = NULL;
static EventGroupHandle_t g_wifi_events = NULL;
static esp_netif_t *g_sta_netif = NULL;
static esp_netif_t *g_ap_netif = NULL;
static int g_retry_count = 0;
static TaskHandle_t g_dns_task = NULL;

// Scan results
#define MAX_SCAN_RESULTS 20
static wifi_scan_result_t g_scan_results[MAX_SCAN_RESULTS];
static size_t g_scan_count = 0;

// Forward declarations
static void dns_server_task(void *pvParameters);
static void start_dns_server(void);
static void stop_dns_server(void);

static void set_state(wifi_manager_state_t new_state)
{
    if (g_state != new_state) {
        ESP_LOGI(TAG, "State: %d -> %d", g_state, new_state);
        g_state = new_state;
        if (g_state_cb) {
            g_state_cb(new_state, g_state_cb_ctx);
        }
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_STA_START:
                ESP_LOGI(TAG, "STA started");
                break;

            case WIFI_EVENT_STA_DISCONNECTED: {
                wifi_event_sta_disconnected_t *event = (wifi_event_sta_disconnected_t *)event_data;
                ESP_LOGW(TAG, "Disconnected, reason: %d", event->reason);

                if (g_state == WIFI_MGR_STATE_CONNECTING && g_retry_count < WIFI_RETRY_COUNT) {
                    g_retry_count++;
                    ESP_LOGI(TAG, "Retrying connection (%d/%d)", g_retry_count, WIFI_RETRY_COUNT);
                    esp_wifi_connect();
                } else {
                    xEventGroupSetBits(g_wifi_events, WIFI_FAIL_BIT);
                }
                break;
            }

            case WIFI_EVENT_AP_START:
                ESP_LOGI(TAG, "SoftAP started");
                break;

            case WIFI_EVENT_AP_STACONNECTED:
                ESP_LOGI(TAG, "Station connected to AP");
                break;

            case WIFI_EVENT_AP_STADISCONNECTED:
                ESP_LOGI(TAG, "Station disconnected from AP");
                break;

            case WIFI_EVENT_SCAN_DONE:
                xEventGroupSetBits(g_wifi_events, WIFI_SCAN_DONE_BIT);
                break;
        }
    } else if (event_base == IP_EVENT) {
        if (event_id == IP_EVENT_STA_GOT_IP) {
            ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
            ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
            xEventGroupSetBits(g_wifi_events, WIFI_CONNECTED_BIT);
        }
    }
}

static esp_err_t start_sntp(void)
{
    ESP_LOGI(TAG, "Starting SNTP sync");
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();

    // Wait for time sync (timeout after 10 seconds)
    int retry = 0;
    while (sntp_get_sync_status() != SNTP_SYNC_STATUS_COMPLETED && retry < 100) {
        vTaskDelay(pdMS_TO_TICKS(100));
        retry++;
    }

    if (retry >= 100) {
        ESP_LOGW(TAG, "SNTP sync timeout");
        return ESP_ERR_TIMEOUT;
    }

    ESP_LOGI(TAG, "Time synchronized");
    return ESP_OK;
}

static esp_err_t start_softap(void)
{
    ESP_LOGI(TAG, "Starting SoftAP");

    // Generate unique SSID using MAC address
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_AP, mac);
    char ssid[32];
    snprintf(ssid, sizeof(ssid), "%s%02X%02X", SOFTAP_SSID_PREFIX, mac[4], mac[5]);

    wifi_config_t wifi_config = {
        .ap = {
            .channel = SOFTAP_CHANNEL,
            .max_connection = SOFTAP_MAX_CONN,
            .authmode = WIFI_AUTH_OPEN,  // Open network for easy captive portal
            .pmf_cfg = {
                .required = false,
            },
        },
    };
    strlcpy((char *)wifi_config.ap.ssid, ssid, sizeof(wifi_config.ap.ssid));
    wifi_config.ap.ssid_len = strlen(ssid);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));  // AP+STA for scanning
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    // Start DNS server for captive portal redirect
    start_dns_server();

    ESP_LOGI(TAG, "SoftAP started: %s", ssid);
    set_state(WIFI_MGR_STATE_PORTAL);
    return ESP_OK;
}

// Note: stop_softap not currently used - wifi_manager_connect handles transition

// DNS server for captive portal - responds to all queries with our IP
static void dns_server_task(void *pvParameters)
{
    ESP_LOGI(TAG, "DNS server started");

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Failed to create DNS socket");
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in server_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(DNS_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };

    if (bind(sock, (struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {
        ESP_LOGE(TAG, "Failed to bind DNS socket");
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    // Get our AP IP address
    esp_netif_ip_info_t ip_info;
    esp_netif_get_ip_info(g_ap_netif, &ip_info);

    uint8_t rx_buffer[512];
    uint8_t tx_buffer[512];

    while (1) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);

        int len = recvfrom(sock, rx_buffer, sizeof(rx_buffer), 0,
                           (struct sockaddr *)&client_addr, &client_len);
        if (len < 0) {
            if (errno == EBADF) break;  // Socket closed
            continue;
        }

        if (len < 12) continue;  // Too short for DNS header

        // Build minimal DNS response
        // Copy header and set response flags
        memcpy(tx_buffer, rx_buffer, len);
        tx_buffer[2] = 0x81;  // QR=1 (response), Opcode=0, AA=0, TC=0, RD=1
        tx_buffer[3] = 0x80;  // RA=1, Z=0, RCODE=0 (no error)
        tx_buffer[6] = 0x00;  // ANCOUNT high byte
        tx_buffer[7] = 0x01;  // ANCOUNT low byte (1 answer)

        // Find end of question section
        int qend = 12;
        while (qend < len && rx_buffer[qend] != 0) {
            qend += rx_buffer[qend] + 1;
        }
        qend += 5;  // Skip null byte + QTYPE + QCLASS

        if (qend > len) {
            continue;  // Malformed query
        }

        // Add answer: pointer to question name + type A + class IN + TTL + IP
        int resp_len = qend;
        tx_buffer[resp_len++] = 0xc0;  // Name pointer
        tx_buffer[resp_len++] = 0x0c;  // Points to offset 12 (question name)
        tx_buffer[resp_len++] = 0x00;  // Type A
        tx_buffer[resp_len++] = 0x01;
        tx_buffer[resp_len++] = 0x00;  // Class IN
        tx_buffer[resp_len++] = 0x01;
        tx_buffer[resp_len++] = 0x00;  // TTL: 60 seconds
        tx_buffer[resp_len++] = 0x00;
        tx_buffer[resp_len++] = 0x00;
        tx_buffer[resp_len++] = 0x3c;
        tx_buffer[resp_len++] = 0x00;  // RDLENGTH: 4 bytes
        tx_buffer[resp_len++] = 0x04;

        // IP address (our AP IP)
        tx_buffer[resp_len++] = ip4_addr1(&ip_info.ip);
        tx_buffer[resp_len++] = ip4_addr2(&ip_info.ip);
        tx_buffer[resp_len++] = ip4_addr3(&ip_info.ip);
        tx_buffer[resp_len++] = ip4_addr4(&ip_info.ip);

        sendto(sock, tx_buffer, resp_len, 0,
               (struct sockaddr *)&client_addr, client_len);
    }

    close(sock);
    ESP_LOGI(TAG, "DNS server stopped");
    vTaskDelete(NULL);
}

static void start_dns_server(void)
{
    if (g_dns_task == NULL) {
        xTaskCreate(dns_server_task, "dns_srv", 4096, NULL, 5, &g_dns_task);
    }
}

static void stop_dns_server(void)
{
    if (g_dns_task != NULL) {
        // The task will exit when socket is closed
        // For now, just delete it
        vTaskDelete(g_dns_task);
        g_dns_task = NULL;
    }
}

// Public API

esp_err_t wifi_manager_init(wifi_manager_state_cb_t state_cb, void *ctx)
{
    if (g_wifi_events != NULL) {
        return ESP_OK;  // Already initialized
    }

    g_state_cb = state_cb;
    g_state_cb_ctx = ctx;
    g_wifi_events = xEventGroupCreate();

    // Initialize TCP/IP stack
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    // Create network interfaces
    g_sta_netif = esp_netif_create_default_wifi_sta();
    g_ap_netif = esp_netif_create_default_wifi_ap();

    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    // Register event handlers
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    ESP_LOGI(TAG, "WiFi manager initialized");
    return ESP_OK;
}

esp_err_t wifi_manager_start(void)
{
    const mrvoice_config_t *config = nvs_config_get();

    if (config->wifi_configured && strlen(config->wifi_ssid) > 0) {
        ESP_LOGI(TAG, "Attempting to connect to saved network: %s", config->wifi_ssid);
        set_state(WIFI_MGR_STATE_CONNECTING);
        g_retry_count = 0;

        wifi_config_t wifi_config = {};
        strlcpy((char *)wifi_config.sta.ssid, config->wifi_ssid, sizeof(wifi_config.sta.ssid));
        strlcpy((char *)wifi_config.sta.password, config->wifi_pass, sizeof(wifi_config.sta.password));

        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
        ESP_ERROR_CHECK(esp_wifi_start());
        ESP_ERROR_CHECK(esp_wifi_connect());

        // Wait for connection with timeout
        EventBits_t bits = xEventGroupWaitBits(g_wifi_events,
                                                WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                                pdTRUE, pdFALSE,
                                                pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));

        if (bits & WIFI_CONNECTED_BIT) {
            set_state(WIFI_MGR_STATE_CONNECTED);
            start_sntp();
            return ESP_OK;
        }

        ESP_LOGW(TAG, "Connection failed, starting captive portal");
    }

    // No saved network or connection failed - start captive portal
    return start_softap();
}

esp_err_t wifi_manager_stop(void)
{
    stop_dns_server();
    esp_wifi_stop();
    set_state(WIFI_MGR_STATE_IDLE);
    return ESP_OK;
}

wifi_manager_state_t wifi_manager_get_state(void)
{
    return g_state;
}

bool wifi_manager_is_connected(void)
{
    return g_state == WIFI_MGR_STATE_CONNECTED;
}

esp_err_t wifi_manager_get_ip(char *ip_str)
{
    if (g_state != WIFI_MGR_STATE_CONNECTED || ip_str == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_netif_ip_info_t ip_info;
    esp_err_t ret = esp_netif_get_ip_info(g_sta_netif, &ip_info);
    if (ret != ESP_OK) return ret;

    sprintf(ip_str, IPSTR, IP2STR(&ip_info.ip));
    return ESP_OK;
}

esp_err_t wifi_manager_scan(void)
{
    ESP_LOGI(TAG, "Starting WiFi scan");

    // Make sure WiFi is started
    wifi_mode_t mode;
    esp_wifi_get_mode(&mode);
    if (mode == WIFI_MODE_NULL) {
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_start();
    }

    wifi_scan_config_t scan_config = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = false,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.min = 100,
        .scan_time.active.max = 300,
    };

    xEventGroupClearBits(g_wifi_events, WIFI_SCAN_DONE_BIT);
    esp_err_t ret = esp_wifi_scan_start(&scan_config, false);
    if (ret != ESP_OK) return ret;

    // Wait for scan to complete
    xEventGroupWaitBits(g_wifi_events, WIFI_SCAN_DONE_BIT, pdTRUE, pdFALSE, pdMS_TO_TICKS(10000));

    // Get results
    uint16_t ap_count = 0;
    esp_wifi_scan_get_ap_num(&ap_count);

    wifi_ap_record_t *ap_records = malloc(ap_count * sizeof(wifi_ap_record_t));
    if (ap_records == NULL) return ESP_ERR_NO_MEM;

    esp_wifi_scan_get_ap_records(&ap_count, ap_records);

    // Copy to our format, removing duplicates
    g_scan_count = 0;
    for (int i = 0; i < ap_count && g_scan_count < MAX_SCAN_RESULTS; i++) {
        // Check for duplicate SSID
        bool duplicate = false;
        for (size_t j = 0; j < g_scan_count; j++) {
            if (strcmp(g_scan_results[j].ssid, (char *)ap_records[i].ssid) == 0) {
                duplicate = true;
                // Keep the one with stronger signal
                if (ap_records[i].rssi > g_scan_results[j].rssi) {
                    g_scan_results[j].rssi = ap_records[i].rssi;
                }
                break;
            }
        }

        if (!duplicate && strlen((char *)ap_records[i].ssid) > 0) {
            strlcpy(g_scan_results[g_scan_count].ssid, (char *)ap_records[i].ssid, 33);
            g_scan_results[g_scan_count].rssi = ap_records[i].rssi;
            g_scan_results[g_scan_count].auth_mode = ap_records[i].authmode;
            g_scan_count++;
        }
    }

    free(ap_records);
    ESP_LOGI(TAG, "Scan complete: %d networks found", g_scan_count);
    return ESP_OK;
}

esp_err_t wifi_manager_get_scan_results(wifi_scan_result_t *results,
                                         size_t max_results,
                                         size_t *num_results)
{
    if (results == NULL || num_results == NULL) return ESP_ERR_INVALID_ARG;

    size_t count = (g_scan_count < max_results) ? g_scan_count : max_results;
    memcpy(results, g_scan_results, count * sizeof(wifi_scan_result_t));
    *num_results = count;
    return ESP_OK;
}

esp_err_t wifi_manager_connect(const char *ssid, const char *password)
{
    if (ssid == NULL) return ESP_ERR_INVALID_ARG;

    ESP_LOGI(TAG, "Connecting to: %s", ssid);
    set_state(WIFI_MGR_STATE_CONNECTING);
    g_retry_count = 0;

    // Stop SoftAP DNS server if running
    stop_dns_server();

    wifi_config_t wifi_config = {};
    strlcpy((char *)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid));
    if (password) {
        strlcpy((char *)wifi_config.sta.password, password, sizeof(wifi_config.sta.password));
    }

    // Switch to STA mode
    esp_wifi_disconnect();
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_connect();

    // Wait for connection
    EventBits_t bits = xEventGroupWaitBits(g_wifi_events,
                                            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                            pdTRUE, pdFALSE,
                                            pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));

    if (bits & WIFI_CONNECTED_BIT) {
        set_state(WIFI_MGR_STATE_CONNECTED);

        // Save credentials to NVS
        nvs_config_save_wifi(ssid, password);

        // Sync time
        start_sntp();

        return ESP_OK;
    }

    // Connection failed, restart portal
    ESP_LOGW(TAG, "Connection failed");
    set_state(WIFI_MGR_STATE_FAILED);
    start_softap();
    return ESP_FAIL;
}

esp_err_t wifi_manager_disconnect(void)
{
    esp_wifi_disconnect();

    // Clear saved credentials
    mrvoice_config_t *config = nvs_config_get_mutable();
    config->wifi_ssid[0] = '\0';
    config->wifi_pass[0] = '\0';
    config->wifi_configured = false;
    nvs_config_save();

    // Start portal
    return start_softap();
}

esp_err_t wifi_manager_start_portal(void)
{
    esp_wifi_disconnect();
    return start_softap();
}
