#include "capture.h"
#include "audio.h"
#include "wav.h"
#include "nvs_config.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include <string.h>

static const char *TAG = "capture";

static uint8_t *g_buf;        // [0..43] header, [44..] PCM
static size_t   g_cap_bytes;  // PCM capacity, not counting the header
static size_t   g_len;        // PCM bytes captured so far

esp_err_t capture_init(void)
{
    const mrvoice_config_t *cfg = nvs_config_get();
    g_cap_bytes = (size_t)cfg->max_s * CAPTURE_BYTES_PER_S;

    g_buf = heap_caps_malloc(WAV_HEADER_SIZE + g_cap_bytes, MALLOC_CAP_SPIRAM);
    if (g_buf == NULL) {
        ESP_LOGE(TAG, "Kein PSRAM für %u Byte Aufnahmepuffer",
                 (unsigned)(WAV_HEADER_SIZE + g_cap_bytes));
        return ESP_ERR_NO_MEM;
    }
    g_len = 0;
    ESP_LOGI(TAG, "Aufnahmepuffer: %u kB (max %u s)",
             (unsigned)((WAV_HEADER_SIZE + g_cap_bytes) / 1024), cfg->max_s);
    return ESP_OK;
}

esp_err_t capture_start(void)
{
    g_len = 0;
    esp_err_t ret = i2s_channel_enable(audio_mic_handle());
    return (ret == ESP_ERR_INVALID_STATE) ? ESP_OK : ret;
}

bool capture_pump(void)
{
    if (g_buf == NULL || g_len >= g_cap_bytes) return false;

    // The caller polls the button in the same loop, so the read must not block
    // long. Ask only for what can plausibly arrive in that window: at 32 kB/s a
    // 20 ms slice is ~640 bytes, so a 4 kB request would time out every single
    // time. i2s_channel_read reports a partial count on timeout, and that data
    // is valid, so accumulate it whatever the return code says.
    size_t room = g_cap_bytes - g_len;
    size_t want = room > 1024 ? 1024 : room;
    size_t got = 0;

    i2s_channel_read(audio_mic_handle(), g_buf + WAV_HEADER_SIZE + g_len,
                     want, &got, pdMS_TO_TICKS(20));
    g_len += got;

    return g_len < g_cap_bytes;
}

void capture_stop(void)
{
    i2s_channel_disable(audio_mic_handle());
}

const uint8_t *capture_wav(size_t *total_len)
{
    // Remove the DC offset, then amplify. The INMP441 sits at a noticeable
    // offset (about -1100 after gain), which wastes headroom and skews the
    // recogniser. One pass over a couple of seconds is cheap.
    uint16_t gain = nvs_config_get()->mic_gain;
    if (g_len >= 2) {
        int16_t *pcm = (int16_t *)(g_buf + WAV_HEADER_SIZE);
        size_t n = g_len / sizeof(int16_t);
        int64_t sum = 0;
        for (size_t i = 0; i < n; i++) sum += pcm[i];
        int32_t dc = (int32_t)(sum / (int64_t)n);

        int32_t g = (int32_t)gain;
        int32_t peak = 0;
        for (size_t i = 0; i < n; i++) {
            int32_t v = (((int32_t)pcm[i] - dc) * g) / 100;
            if (v >  32767) v =  32767;
            if (v < -32768) v = -32768;
            pcm[i] = (int16_t)v;
            int32_t a = v < 0 ? -v : v;
            if (a > peak) peak = a;
        }
        ESP_LOGI(TAG, "Gleichanteil %d entfernt, Verstaerkung %u%%, Peak danach %d",
                 (int)dc, (unsigned)gain, (int)peak);
    }

    wav_write_header(g_buf, CAPTURE_SAMPLE_RATE, 1, 16, (uint32_t)g_len);
    *total_len = WAV_HEADER_SIZE + g_len;
    return g_buf;
}

uint32_t capture_duration_ms(void)
{
    return (uint32_t)((g_len * 1000ULL) / CAPTURE_BYTES_PER_S);
}
