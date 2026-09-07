#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "nvs_config.h"
#include "audio.h"
#include "wifi_manager.h"
#include "config_server.h"
#include "capture.h"
#include "ui.h"
#include "app.h"

static const char *TAG = "main";

static void on_wifi_state_changed(wifi_manager_state_t state, void *ctx)
{
    switch (state) {
        case WIFI_MGR_STATE_CONNECTED:
            ESP_LOGI(TAG, "WLAN verbunden");
            config_server_start();
            break;
        case WIFI_MGR_STATE_PORTAL:
            ESP_LOGI(TAG, "Captive Portal aktiv");
            config_server_start();
            break;
        case WIFI_MGR_STATE_FAILED:
            ESP_LOGW(TAG, "WLAN-Verbindung fehlgeschlagen");
            break;
        default:
            break;
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "MrVoice startet");

    if (nvs_config_init() != ESP_OK) {
        ESP_LOGE(TAG, "NVS-Konfiguration fehlgeschlagen");
        return;
    }

    board_init();

    // Diagnostic mode: leave the radio down entirely. The microphone delivered
    // audio only while WiFi had never associated, and esp_wifi_stop() does not
    // fully quiet the RF section, so this is the clean comparison.
    if (nvs_config_get()->radio_off) {
        ESP_LOGW(TAG, "FUNK AUS (radio_off=1) - kein WLAN. Zum Aufheben: radio on");
    } else {
        wifi_manager_init(on_wifi_state_changed, NULL);
        wifi_manager_start();
    }

    if (capture_init() != ESP_OK) {
        ESP_LOGE(TAG, "Aufnahmepuffer konnte nicht angelegt werden");
        return;
    }
    ui_init();
    app_start();

    vTaskDelete(NULL);   // the conversation and speech tasks own the device now
}
