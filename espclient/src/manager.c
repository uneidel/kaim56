#include "manager.h"
#include "nvs_config.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_task_wdt.h"
#include "mbedtls/base64.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include <string.h>
#include <stdlib.h>

static const char *TAG = "manager";

// The watchdog only accepts a reset from a subscribed task.
static void wdt_feed(void)
{
    if (esp_task_wdt_status(NULL) == ESP_OK) esp_task_wdt_reset();
}

#define CHAT_CHUNK       1024
#define TTS_TIMEOUT_MS   60000
#define STT_TIMEOUT_MS   60000
#define CHAT_TIMEOUT_MS 600000     // A.3: a turn may run for minutes

struct mgr_stream { esp_http_client_handle_t client; };

// Builds "Basic <base64>" once per request. Returns false when no user is set.
static bool build_auth(char *dst, size_t cap)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    if (cfg->user[0] == '\0') return false;

    char pair[NVS_CFG_USER_MAX_LEN + NVS_CFG_PASS_MAX_LEN + 2];
    int n = snprintf(pair, sizeof(pair), "%s:%s", cfg->user, cfg->pass);
    if (n <= 0 || (size_t)n >= sizeof(pair)) return false;

    unsigned char enc[256];
    size_t enc_len = 0;
    if (mbedtls_base64_encode(enc, sizeof(enc), &enc_len,
                              (const unsigned char *)pair, (size_t)n) != 0) return false;

    return (size_t)snprintf(dst, cap, "Basic %.*s", (int)enc_len, (char *)enc) < cap;
}

// Every request gets a fresh handle: the manager speaks HTTP/1.0 and closes.
static esp_http_client_handle_t open_post(const char *path, int timeout_ms,
                                          const char *content_type)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    if (cfg->base_url[0] == '\0') {
        ESP_LOGE(TAG, "Keine Manager-URL konfiguriert");
        return NULL;
    }

    char url[NVS_CFG_URL_MAX_LEN + 128];
    snprintf(url, sizeof(url), "%s%s", cfg->base_url, path);

    esp_http_client_config_t hc = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = timeout_ms,
        .keep_alive_enable = false,
        .disable_auto_redirect = true,
    };

    // The manager sits behind a TLS reverse proxy; plain HTTP returns 404.
    // Attach the IDF root-certificate bundle for https:// URLs.
    if (strncmp(cfg->base_url, "https://", 8) == 0) {
        hc.transport_type = HTTP_TRANSPORT_OVER_SSL;
        hc.crt_bundle_attach = esp_crt_bundle_attach;
    }
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (c == NULL) return NULL;

    esp_http_client_set_header(c, "Content-Type", content_type);

    char auth[384];
    if (build_auth(auth, sizeof(auth))) esp_http_client_set_header(c, "Authorization", auth);

    return c;
}

// A.3: anything but 200 is an error and the body carries JSON with "error".
static esp_err_t check_status(esp_http_client_handle_t c)
{
    int status = esp_http_client_get_status_code(c);
    if (status == 200) return ESP_OK;

    char body[300] = {0};
    esp_http_client_read(c, body, sizeof(body) - 1);
    ESP_LOGE(TAG, "HTTP %d: %s", status, body);
    return ESP_FAIL;
}

esp_err_t mgr_stt(const uint8_t *wav, size_t len, mgr_stt_result_t *out)
{
    esp_http_client_handle_t c = open_post("/api/stt", STT_TIMEOUT_MS, "audio/wav");
    if (c == NULL) return ESP_ERR_INVALID_STATE;

    esp_err_t ret = ESP_FAIL;
    cJSON *root = NULL;

    // Content-Length is mandatory here — chunked upload is rejected.
    if (esp_http_client_open(c, (int)len) != ESP_OK) goto done;

    for (size_t sent = 0; sent < len; ) {
        size_t block = len - sent;
        if (block > 4096) block = 4096;
        int w = esp_http_client_write(c, (const char *)wav + sent, block);
        if (w <= 0) goto done;
        sent += (size_t)w;
        wdt_feed();
    }

    if (esp_http_client_fetch_headers(c) < 0) goto done;
    if (check_status(c) != ESP_OK) goto done;

    char body[768] = {0};
    int n = esp_http_client_read_response(c, body, sizeof(body) - 1);
    if (n <= 0) goto done;
    body[n] = '\0';

    root = cJSON_Parse(body);
    if (root == NULL) goto done;

    const cJSON *text = cJSON_GetObjectItem(root, "text");
    if (cJSON_IsString(text)) strlcpy(out->text, text->valuestring, sizeof(out->text));
    const cJSON *secs = cJSON_GetObjectItem(root, "seconds");
    out->seconds = cJSON_IsNumber(secs) ? (float)secs->valuedouble : 0.0f;
    const cJSON *took = cJSON_GetObjectItem(root, "took");
    out->took = cJSON_IsNumber(took) ? (float)took->valuedouble : 0.0f;

    ret = ESP_OK;

done:
    if (root != NULL) cJSON_Delete(root);
    esp_http_client_cleanup(c);
    return ret;
}

esp_err_t mgr_chat(const char *instance, const char *message,
                   const char *chat_id, mgr_chat_cb on_data, void *user)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "message", message);
    cJSON_AddStringToObject(req, "chat", chat_id);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    if (body == NULL) return ESP_ERR_NO_MEM;

    char path[NVS_CFG_INSTANCE_MAX_LEN + 32];
    snprintf(path, sizeof(path), "/api/chat/%s", instance);

    esp_http_client_handle_t c = open_post(path, CHAT_TIMEOUT_MS, "application/json");
    if (c == NULL) { free(body); return ESP_ERR_INVALID_STATE; }

    esp_err_t ret = ESP_FAIL;
    size_t body_len = strlen(body);

    if (esp_http_client_open(c, (int)body_len) != ESP_OK) goto done;
    if (esp_http_client_write(c, body, body_len) != (int)body_len) goto done;
    if (esp_http_client_fetch_headers(c) < 0) goto done;
    if (check_status(c) != ESP_OK) goto done;

    // A.3: no Content-Length, no chunked — read until the connection closes.
    char chunk[CHAT_CHUNK];
    for (;;) {
        int n = esp_http_client_read(c, chunk, sizeof(chunk));
        if (n < 0) goto done;
        if (n == 0) break;                 // connection closed = end of turn
        if (on_data != NULL) on_data(chunk, (size_t)n, user);
        wdt_feed();
    }
    ret = ESP_OK;

done:
    esp_http_client_cleanup(c);
    free(body);
    return ret;
}

esp_err_t mgr_tts_open(const char *text, mgr_stream_t **out)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "text", text);
    char *body = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    if (body == NULL) return ESP_ERR_NO_MEM;

    esp_http_client_handle_t c = open_post("/api/tts", TTS_TIMEOUT_MS, "application/json");
    if (c == NULL) { free(body); return ESP_ERR_INVALID_STATE; }

    size_t body_len = strlen(body);
    mgr_stream_t *s = NULL;

    if (esp_http_client_open(c, (int)body_len) != ESP_OK) goto fail;
    if (esp_http_client_write(c, body, body_len) != (int)body_len) goto fail;
    if (esp_http_client_fetch_headers(c) < 0) goto fail;
    if (check_status(c) != ESP_OK) goto fail;

    s = calloc(1, sizeof(mgr_stream_t));
    if (s == NULL) goto fail;
    s->client = c;
    *out = s;
    free(body);
    return ESP_OK;

fail:
    esp_http_client_cleanup(c);
    free(body);
    return ESP_FAIL;
}

int mgr_tts_read(mgr_stream_t *s, uint8_t *buf, size_t len)
{
    if (s == NULL) return -1;
    return esp_http_client_read(s->client, (char *)buf, len);
}

void mgr_tts_close(mgr_stream_t *s)
{
    if (s == NULL) return;
    esp_http_client_cleanup(s->client);
    free(s);
}
