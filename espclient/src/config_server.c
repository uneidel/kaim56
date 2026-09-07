#include "config_server.h"
#include "wifi_manager.h"
#include "nvs_config.h"
#include "form.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>

static const char *TAG = "config_srv";

static httpd_handle_t g_server = NULL;

// Minimal CSS for all pages
static const char *CSS_STYLE =
    "<style>"
    "*{box-sizing:border-box;margin:0;padding:0}"
    "body{font-family:-apple-system,sans-serif;background:#1a1a2e;color:#eee;padding:20px;max-width:500px;margin:0 auto}"
    "h1{color:#0f3460;background:#e94560;padding:15px;border-radius:8px;text-align:center;margin-bottom:20px}"
    "h2{color:#e94560;margin:20px 0 10px}"
    ".card{background:#16213e;padding:20px;border-radius:8px;margin-bottom:15px}"
    "label{display:block;margin:10px 0 5px;color:#aaa}"
    "input,select{width:100%;padding:12px;border:1px solid #0f3460;border-radius:5px;background:#1a1a2e;color:#eee;font-size:16px}"
    "button{width:100%;padding:15px;background:#e94560;color:#fff;border:none;border-radius:5px;font-size:16px;cursor:pointer;margin-top:15px}"
    "button.secondary{background:#0f3460}"
    "button.danger{background:#c23616}"
    ".status{padding:10px;border-radius:5px;margin:10px 0}"
    ".ok{background:#27ae60}.warn{background:#f39c12}.err{background:#c23616}"
    ".wifi-item{display:flex;justify-content:space-between;padding:12px;background:#1a1a2e;border-radius:5px;margin:5px 0;cursor:pointer}"
    ".wifi-item:hover{background:#0f3460}"
    "nav{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}"
    "nav a{flex:1;text-align:center;padding:10px;background:#0f3460;color:#eee;text-decoration:none;border-radius:5px}"
    "nav a.active{background:#e94560}"
    ".slider{width:100%;height:8px}"
    ".val{color:#e94560;font-weight:bold}"
    "</style>";

// Form parsing lives in form.c so it can be unit-tested on the host.
static esp_err_t get_form_param(httpd_req_t *req, const char *content,
                                const char *param, char *value, size_t max_len)
{
    (void)req;
    return form_get_param(content, param, value, max_len) ? ESP_OK : ESP_ERR_NOT_FOUND;
}

// Reads the whole POST body. A single httpd_req_recv() silently truncates,
// which the prompt field makes reachable.
static esp_err_t recv_body(httpd_req_t *req, char *buf, size_t cap)
{
    if (req->content_len >= cap) return ESP_ERR_INVALID_SIZE;
    size_t got = 0;
    while (got < req->content_len) {
        int r = httpd_req_recv(req, buf + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) return ESP_FAIL;
        got += (size_t)r;
    }
    buf[got] = '\0';
    return ESP_OK;
}

// Send HTML header
static void send_html_head(httpd_req_t *req, const char *title)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_sendstr_chunk(req, "<!DOCTYPE html><html><head>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta charset='UTF-8'><title>");
    httpd_resp_sendstr_chunk(req, title);
    httpd_resp_sendstr_chunk(req, "</title>");
    httpd_resp_sendstr_chunk(req, CSS_STYLE);
    httpd_resp_sendstr_chunk(req, "</head><body>");
}

// Send navigation bar
static void send_nav(httpd_req_t *req, const char *active)
{
    httpd_resp_sendstr_chunk(req, "<nav>");
    httpd_resp_sendstr_chunk(req, strcmp(active, "home") == 0 ?
        "<a href='/' class='active'>Home</a>" : "<a href='/'>Home</a>");
    httpd_resp_sendstr_chunk(req, strcmp(active, "wifi") == 0 ?
        "<a href='/wifi' class='active'>WiFi</a>" : "<a href='/wifi'>WiFi</a>");
    httpd_resp_sendstr_chunk(req, strcmp(active, "manager") == 0 ?
        "<a href='/manager' class='active'>Manager</a>" : "<a href='/manager'>Manager</a>");
    httpd_resp_sendstr_chunk(req, strcmp(active, "audio") == 0 ?
        "<a href='/audio' class='active'>Aufnahme</a>" : "<a href='/audio'>Aufnahme</a>");
    httpd_resp_sendstr_chunk(req, "</nav>");
}

// Home page handler
static esp_err_t handler_home(httpd_req_t *req)
{
    const mrvoice_config_t *config = nvs_config_get();
    bool wifi_ok = wifi_manager_is_connected();

    char ip_str[16] = "Not connected";
    if (wifi_ok) {
        wifi_manager_get_ip(ip_str);
    }

    send_html_head(req, "MrVoice");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1>");
    send_nav(req, "home");

    httpd_resp_sendstr_chunk(req, "<div class='card'><h2>Status</h2>");
    httpd_resp_sendstr_chunk(req, wifi_ok ?
        "<div class='status ok'>WiFi: Connected</div>" :
        "<div class='status warn'>WiFi: Not connected</div>");
    httpd_resp_sendstr_chunk(req, config->mgr_configured ?
        "<div class='status ok'>Manager: konfiguriert</div>" :
        "<div class='status warn'>Manager: nicht konfiguriert</div>");
    httpd_resp_sendstr_chunk(req, "</div>");

    httpd_resp_sendstr_chunk(req, "<div class='card'><h2>Device Info</h2>");
    httpd_resp_sendstr_chunk(req, "<label>IP Address</label><p>");
    httpd_resp_sendstr_chunk(req, ip_str);
    httpd_resp_sendstr_chunk(req, "</p><label>Instanz</label><p>");
    httpd_resp_sendstr_chunk(req, config->instance);
    httpd_resp_sendstr_chunk(req, "</p></div>");

    httpd_resp_sendstr_chunk(req, "<div class='card'>"
        "<a href='/factory-reset'><button class='danger'>Factory Reset</button></a>"
        "</div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// WiFi page handler
static esp_err_t handler_wifi(httpd_req_t *req)
{
    wifi_manager_scan();

    wifi_scan_result_t results[15];
    size_t count = 0;
    wifi_manager_get_scan_results(results, 15, &count);

    send_html_head(req, "MrVoice - WiFi");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1>");
    send_nav(req, "wifi");

    httpd_resp_sendstr_chunk(req, "<div class='card'><h2>WiFi Networks</h2>"
        "<p style='color:#aaa;margin-bottom:10px'>Select a network to connect:</p>");

    char buf[64];
    for (size_t i = 0; i < count; i++) {
        httpd_resp_sendstr_chunk(req, "<div class='wifi-item' onclick=\"selectWifi('");
        httpd_resp_sendstr_chunk(req, results[i].ssid);
        httpd_resp_sendstr_chunk(req, "')\"><span>");
        httpd_resp_sendstr_chunk(req, results[i].ssid);
        httpd_resp_sendstr_chunk(req, results[i].auth_mode != 0 ? " *" : "");
        httpd_resp_sendstr_chunk(req, "</span><span style='color:#aaa'>");
        snprintf(buf, sizeof(buf), "%d dBm</span></div>", results[i].rssi);
        httpd_resp_sendstr_chunk(req, buf);
    }

    if (count == 0) {
        httpd_resp_sendstr_chunk(req, "<p style='color:#aaa'>No networks found.</p>");
    }

    httpd_resp_sendstr_chunk(req, "<button class='secondary' onclick='location.reload()'>Refresh</button></div>");

    httpd_resp_sendstr_chunk(req, "<div class='card' id='wifi-form' style='display:none'>"
        "<h2>Connect to: <span id='selected-ssid'></span></h2>"
        "<form method='POST' action='/wifi/connect'>"
        "<input type='hidden' name='ssid' id='ssid-input'>"
        "<label>Password</label>"
        "<input type='password' name='password' placeholder='Enter password'>"
        "<button type='submit'>Connect</button></form></div>");

    httpd_resp_sendstr_chunk(req, "<script>"
        "function selectWifi(ssid){"
        "document.getElementById('selected-ssid').textContent=ssid;"
        "document.getElementById('ssid-input').value=ssid;"
        "document.getElementById('wifi-form').style.display='block';}"
        "</script></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// WiFi connect handler (POST)
static esp_err_t handler_wifi_connect(httpd_req_t *req)
{
    char content[512];
    if (recv_body(req, content, sizeof(content)) != ESP_OK) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    char ssid[33], password[65];
    get_form_param(req, content, "ssid", ssid, sizeof(ssid));
    get_form_param(req, content, "password", password, sizeof(password));

    ESP_LOGI(TAG, "WiFi connect request: %s", ssid);

    send_html_head(req, "MrVoice - Connecting");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1><div class='card'><h2>Connecting...</h2>"
        "<p>Attempting to connect to: <strong>");
    httpd_resp_sendstr_chunk(req, ssid);
    httpd_resp_sendstr_chunk(req, "</strong></p>"
        "<p style='color:#aaa;margin-top:15px'>If successful, reconnect to your network to continue.</p>"
        "</div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);

    wifi_manager_connect(ssid, password);
    return ESP_OK;
}

// Manager page handler
static esp_err_t handler_manager(httpd_req_t *req)
{
    const mrvoice_config_t *config = nvs_config_get();

    send_html_head(req, "MrVoice - Manager");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1>");
    send_nav(req, "manager");

    httpd_resp_sendstr_chunk(req, "<form method='POST' action='/manager/save'>"
        "<div class='card'><h2>kAIm56-Manager</h2>"
        "<label>Basis-URL</label>"
        "<input type='text' name='url' value='");
    httpd_resp_sendstr_chunk(req, config->base_url);
    httpd_resp_sendstr_chunk(req, "' placeholder='http://manager.example:8700'>"
        "<label>Benutzer (Basic Auth, optional)</label><input type='text' name='user' value='");
    httpd_resp_sendstr_chunk(req, config->user);
    httpd_resp_sendstr_chunk(req, "'>"
        "<label>Passwort</label><input type='password' name='pass' value='");
    httpd_resp_sendstr_chunk(req, config->pass);
    httpd_resp_sendstr_chunk(req, "'></div>");

    httpd_resp_sendstr_chunk(req, "<div class='card'><h2>Agent</h2>"
        "<label>Instanz</label><input type='text' name='instance' value='");
    httpd_resp_sendstr_chunk(req, config->instance);
    httpd_resp_sendstr_chunk(req, "' placeholder='myassistant'>"
        "<label>Prompt</label><input type='text' name='prompt' value='");
    httpd_resp_sendstr_chunk(req, config->prompt);
    httpd_resp_sendstr_chunk(req, "'></div>"
        "<button type='submit'>Speichern</button></form></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// Manager save handler (POST)
static esp_err_t handler_manager_save(httpd_req_t *req)
{
    char content[1024];   // the prompt field makes this the largest body
    if (recv_body(req, content, sizeof(content)) != ESP_OK) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    char url[NVS_CFG_URL_MAX_LEN], user[NVS_CFG_USER_MAX_LEN];
    char pass[NVS_CFG_PASS_MAX_LEN], instance[NVS_CFG_INSTANCE_MAX_LEN];
    char prompt[NVS_CFG_PROMPT_MAX_LEN];

    get_form_param(req, content, "url", url, sizeof(url));
    get_form_param(req, content, "user", user, sizeof(user));
    get_form_param(req, content, "pass", pass, sizeof(pass));
    get_form_param(req, content, "instance", instance, sizeof(instance));
    get_form_param(req, content, "prompt", prompt, sizeof(prompt));

    ESP_LOGI(TAG, "Manager-Konfiguration: url=%s, instanz=%s", url, instance);
    nvs_config_save_manager(url, user, pass, instance, prompt);

    send_html_head(req, "MrVoice - Gespeichert");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1><div class='card'>"
        "<div class='status ok'>Manager-Konfiguration gespeichert.</div>"
        "<a href='/manager'><button>Weiter</button></a></div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// Recording page handler
static esp_err_t handler_audio(httpd_req_t *req)
{
    const mrvoice_config_t *config = nvs_config_get();
    char buf[32];

    send_html_head(req, "MrVoice - Aufnahme");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1>");
    send_nav(req, "audio");

    httpd_resp_sendstr_chunk(req, "<form method='POST' action='/audio/save'>"
        "<div class='card'><h2>Aufnahme</h2>"
        "<label>Mindestdauer (ms)</label>"
        "<input type='number' name='min_ms' min='100' max='5000' step='50' value='");
    snprintf(buf, sizeof(buf), "%u", config->min_ms);
    httpd_resp_sendstr_chunk(req, buf);
    httpd_resp_sendstr_chunk(req, "'>"
        "<p style='color:#aaa;font-size:12px'>Kuerzere Aufnahmen werden verworfen.</p>"
        "<label>Maximaldauer (s)</label>"
        "<input type='number' name='max_s' min='1' max='30' step='1' value='");
    snprintf(buf, sizeof(buf), "%u", config->max_s);
    httpd_resp_sendstr_chunk(req, buf);
    httpd_resp_sendstr_chunk(req, "'>"
        "<p style='color:#aaa;font-size:12px'>30 s entspricht 960 kB im PSRAM.</p>"
        "</div><button type='submit'>Speichern</button></form></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// Recording save handler (POST)
static esp_err_t handler_audio_save(httpd_req_t *req)
{
    char content[128];
    if (recv_body(req, content, sizeof(content)) != ESP_OK) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    char min_str[16], max_str[16];
    get_form_param(req, content, "min_ms", min_str, sizeof(min_str));
    get_form_param(req, content, "max_s", max_str, sizeof(max_str));

    uint16_t min_ms = (uint16_t)atoi(min_str);
    uint16_t max_s  = (uint16_t)atoi(max_str);

    ESP_LOGI(TAG, "Aufnahme-Konfiguration: min=%u ms, max=%u s", min_ms, max_s);
    nvs_config_save_recording(min_ms, max_s);

    send_html_head(req, "MrVoice - Gespeichert");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1><div class='card'>"
        "<div class='status ok'>Aufnahme-Einstellungen gespeichert.</div>"
        "<a href='/audio'><button>Weiter</button></a></div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// Factory reset page handler
static esp_err_t handler_factory_reset(httpd_req_t *req)
{
    send_html_head(req, "MrVoice - Factory Reset");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1><div class='card'><h2>Factory Reset</h2>"
        "<p style='color:#f39c12'>This will erase all settings including WiFi and LiveKit configuration.</p>"
        "<p style='margin-top:15px'>The device will restart in setup mode.</p>"
        "<form method='POST' action='/factory-reset/confirm'>"
        "<button type='submit' class='danger'>Confirm Reset</button></form>"
        "<a href='/'><button class='secondary'>Cancel</button></a></div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

// Factory reset confirm handler (POST)
static esp_err_t handler_factory_reset_confirm(httpd_req_t *req)
{
    ESP_LOGW(TAG, "Factory reset confirmed!");

    send_html_head(req, "MrVoice - Resetting");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice</h1><div class='card'>"
        "<div class='status warn'>Factory reset complete. Device will restart...</div>"
        "</div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);

    nvs_config_factory_reset();
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
    return ESP_OK;
}

// Captive portal handler
static esp_err_t handler_captive_portal(httpd_req_t *req)
{
    const char *uri = req->uri;

    // Captive portal detection URLs - redirect to root
    if (strstr(uri, "generate_204") || strstr(uri, "gen_204") ||
        strstr(uri, "hotspot-detect") || strstr(uri, "connectivitycheck") ||
        strstr(uri, "ncsi.txt")) {
        httpd_resp_set_status(req, "302 Found");
        httpd_resp_set_hdr(req, "Location", "/");
        httpd_resp_send(req, NULL, 0);
        return ESP_OK;
    }

    // Show welcome page
    send_html_head(req, "MrVoice Setup");
    httpd_resp_sendstr_chunk(req, "<h1>MrVoice Setup</h1><div class='card'>"
        "<h2>Welcome!</h2><p>Please configure your MrVoice device.</p>"
        "<a href='/wifi'><button>Setup WiFi</button></a></div></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

esp_err_t config_server_start(void)
{
    if (g_server != NULL) {
        return ESP_OK;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 16;
    config.uri_match_fn = httpd_uri_match_wildcard;
    config.stack_size = 8192;
    config.max_req_hdr_len = 1024;  // Browsers send large headers

    ESP_LOGI(TAG, "Starting HTTP server on port %d", config.server_port);
    esp_err_t ret = httpd_start(&g_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start server: %s", esp_err_to_name(ret));
        return ret;
    }

    httpd_uri_t uri_home = { .uri = "/", .method = HTTP_GET, .handler = handler_home };
    httpd_uri_t uri_wifi = { .uri = "/wifi", .method = HTTP_GET, .handler = handler_wifi };
    httpd_uri_t uri_wifi_connect = { .uri = "/wifi/connect", .method = HTTP_POST, .handler = handler_wifi_connect };
    httpd_uri_t uri_manager = { .uri = "/manager", .method = HTTP_GET, .handler = handler_manager };
    httpd_uri_t uri_manager_save = { .uri = "/manager/save", .method = HTTP_POST, .handler = handler_manager_save };
    httpd_uri_t uri_audio = { .uri = "/audio", .method = HTTP_GET, .handler = handler_audio };
    httpd_uri_t uri_audio_save = { .uri = "/audio/save", .method = HTTP_POST, .handler = handler_audio_save };
    httpd_uri_t uri_factory_reset = { .uri = "/factory-reset", .method = HTTP_GET, .handler = handler_factory_reset };
    httpd_uri_t uri_factory_reset_confirm = { .uri = "/factory-reset/confirm", .method = HTTP_POST, .handler = handler_factory_reset_confirm };
    httpd_uri_t uri_captive = { .uri = "/*", .method = HTTP_GET, .handler = handler_captive_portal };

    httpd_register_uri_handler(g_server, &uri_home);
    httpd_register_uri_handler(g_server, &uri_wifi);
    httpd_register_uri_handler(g_server, &uri_wifi_connect);
    httpd_register_uri_handler(g_server, &uri_manager);
    httpd_register_uri_handler(g_server, &uri_manager_save);
    httpd_register_uri_handler(g_server, &uri_audio);
    httpd_register_uri_handler(g_server, &uri_audio_save);
    httpd_register_uri_handler(g_server, &uri_factory_reset);
    httpd_register_uri_handler(g_server, &uri_factory_reset_confirm);
    httpd_register_uri_handler(g_server, &uri_captive);

    ESP_LOGI(TAG, "HTTP server started");
    return ESP_OK;
}

esp_err_t config_server_stop(void)
{
    if (g_server == NULL) {
        return ESP_OK;
    }
    httpd_stop(g_server);
    g_server = NULL;
    ESP_LOGI(TAG, "HTTP server stopped");
    return ESP_OK;
}

bool config_server_is_running(void)
{
    return g_server != NULL;
}
