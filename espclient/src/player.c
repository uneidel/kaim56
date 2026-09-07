#include "player.h"
#include "audio.h"
#include "wav.h"
#include "nvs_config.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdlib.h>
#include "esp_heap_caps.h"

static const char *TAG = "player";

#define BLOCK 4096

// Fill this much before the first sample reaches I2S. At 44 kB/s that is
// roughly a second of slack against network stalls, and it costs only PSRAM.
#define PREBUFFER 48000

static volatile bool g_abort;
static uint32_t g_current_rate;
static bool g_tx_enabled;

// Forget the cached rate whenever the channel is rebuilt underneath us,
// otherwise retune() is skipped and mono samples go into a stereo slot.
void player_forget_rate(void) { g_current_rate = 0; }

// The watchdog only accepts a reset from a task that is subscribed to it;
// calling it unconditionally floods the log with "task not found".
static void wdt_feed(void)
{
    if (esp_task_wdt_status(NULL) == ESP_OK) esp_task_wdt_reset();
}

// Pulls exactly n bytes unless the source ends first.
static size_t read_exact(player_read_fn read, void *ctx, uint8_t *dst, size_t n)
{
    size_t got = 0;
    while (got < n) {
        int r = read(ctx, dst + got, n - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    return got;
}

static esp_err_t retune(uint32_t rate)
{
    if (rate == g_current_rate) return ESP_OK;

    i2s_chan_handle_t tx = audio_dac_handle();
    if (g_tx_enabled) {
        i2s_channel_disable(tx);
        g_tx_enabled = false;
    }

    i2s_std_clk_config_t clk = I2S_STD_CLK_DEFAULT_CONFIG(rate);
    esp_err_t ret = i2s_channel_reconfig_std_clock(tx, &clk);
    if (ret != ESP_OK) return ret;

    i2s_std_slot_config_t slot =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
    ret = i2s_channel_reconfig_std_slot(tx, &slot);
    if (ret != ESP_OK) return ret;

    g_current_rate = rate;
    ESP_LOGI(TAG, "I2S auf %u Hz mono umgestellt", (unsigned)rate);
    return ESP_OK;
}

void player_abort(void) { g_abort = true; }

esp_err_t player_play(player_read_fn read, void *ctx)
{
    g_abort = false;

    uint8_t head[WAV_HEADER_SIZE];
    if (read_exact(read, ctx, head, WAV_HEADER_SIZE) != WAV_HEADER_SIZE) {
        ESP_LOGE(TAG, "WAV-Header unvollständig");
        return ESP_FAIL;
    }

    wav_info_t info;
    if (!wav_parse_header(head, WAV_HEADER_SIZE, &info)) {
        ESP_LOGE(TAG, "WAV-Header ungültig");
        return ESP_FAIL;
    }

    // A non-canonical header means PCM starts later than byte 44; skip the gap.
    if (info.data_offset > WAV_HEADER_SIZE) {
        uint8_t skip[64];
        size_t remaining = info.data_offset - WAV_HEADER_SIZE;
        while (remaining > 0) {
            size_t n = remaining > sizeof(skip) ? sizeof(skip) : remaining;
            if (read_exact(read, ctx, skip, n) != n) return ESP_FAIL;
            remaining -= n;
        }
    }

    if (retune(info.sample_rate) != ESP_OK) return ESP_FAIL;

    esp_err_t ret = i2s_channel_enable(audio_dac_handle());
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) return ret;
    g_tx_enabled = true;
    audio_amp_enable(true);

    // Heap, not stack: 4 kB of locals overflows both the console REPL task and
    // a modest speech task, which shows up as an interrupt watchdog panic.
    uint16_t pct = nvs_config_get()->spk_volume;
    if (pct > 100) pct = 100;
    const int32_t vol_fixed = (int32_t)pct * 256 / 100;

    uint8_t *block = malloc(BLOCK);
    if (block == NULL) {
        audio_amp_enable(false);
        i2s_channel_disable(audio_dac_handle());
        return ESP_ERR_NO_MEM;
    }

    // Pre-roll: pull a chunk from the source before starting playback, so a
    // stall early in the stream does not empty the DMA.
    uint8_t *pre = heap_caps_malloc(PREBUFFER, MALLOC_CAP_SPIRAM);
    size_t pre_len = 0;
    if (pre != NULL) {
        while (pre_len < PREBUFFER && !g_abort) {
            int n = read(ctx, pre + pre_len, PREBUFFER - pre_len);
            if (n <= 0) break;
            pre_len += (size_t)n;
        }
        for (size_t off = 0; off < pre_len; ) {
            size_t n = pre_len - off;
            if (n > BLOCK) n = BLOCK;
            if (vol_fixed < 256) {
                int16_t *sm = (int16_t *)(pre + off);
                for (size_t i = 0; i < n / sizeof(int16_t); i++) {
                    sm[i] = (int16_t)(((int32_t)sm[i] * vol_fixed) >> 8);
                }
            }
            size_t w = 0;
            i2s_channel_write(audio_dac_handle(), pre + off, n, &w, portMAX_DELAY);
            off += n;
            wdt_feed();
        }
        free(pre);
    }

    for (;;) {
        if (g_abort) { ret = ESP_OK; break; }

        int n = read(ctx, block, sizeof(block));
        if (n < 0) { ret = ESP_FAIL; break; }
        if (n == 0) { ret = ESP_OK; break; }

        // Piper peaks at full scale and clips through the amplifier, so scale
        // down before it reaches I2S. Fixed point: percent/100 in 8.8.
        if (vol_fixed < 256) {
            int16_t *sm = (int16_t *)block;
            size_t count = (size_t)n / sizeof(int16_t);
            for (size_t i = 0; i < count; i++) {
                sm[i] = (int16_t)(((int32_t)sm[i] * vol_fixed) >> 8);
            }
        }

        size_t written = 0;
        i2s_channel_write(audio_dac_handle(), block, (size_t)n, &written, portMAX_DELAY);
        wdt_feed();
    }

    free(block);

    // Let the DMA drain before cutting the clock, or the tail is clipped.
    vTaskDelay(pdMS_TO_TICKS(40));
    audio_amp_enable(false);
    i2s_channel_disable(audio_dac_handle());
    g_tx_enabled = false;
    return ret;
}
