#include "audio.h"
#include "config.h"
#include "esp_log.h"
#include "player.h"
#include <stdbool.h>
#include "driver/gpio.h"

static const char *TAG = "audio";

static i2s_chan_handle_t mic_rx_handle = NULL;
static i2s_chan_handle_t dac_tx_handle = NULL;

// --- microphone (INMP441) on I2S0 ------------------------------------------

// The INMP441's L/R strap sits on a GPIO, not on GND, and must be held LOW or
// the microphone transmits in the right slot and every read is silent.
// Diagnostics call gpio_reset_pin on that pad, so re-assert it on every mic
// setup rather than only once in board_init.
static void mic_strap_left_unless_used(int sck, int ws, int sd)
{
    if (MIC_LR < 0) return;                                     // strapped to GND
    if (MIC_LR == sck || MIC_LR == ws || MIC_LR == sd) return;  // pin is in use

    gpio_config_t lr = {
        .pin_bit_mask = 1ULL << MIC_LR,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&lr);
    gpio_set_level(MIC_LR, 0);
}

static esp_err_t mic_setup_ex(int sck, int ws, int sd,
                              i2s_std_slot_mask_t mask, i2s_data_bit_width_t bits,
                              bool left_align, bool bit_shift,
                              i2s_slot_mode_t slot_mode)
{
    mic_strap_left_unless_used(sck, ws, sd);

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 320;    // 20 ms at 16 kHz
    chan_cfg.auto_clear = true;

    esp_err_t ret = i2s_new_channel(&chan_cfg, NULL, &mic_rx_handle);
    if (ret != ESP_OK) return ret;

    i2s_std_config_t std_cfg = {
        .clk_cfg = {
            .sample_rate_hz = 16000,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = bits,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_32BIT,  // INMP441 uses 32-bit slots
            .slot_mode = slot_mode,
            .slot_mask = mask,
            .ws_width = 32,
            .ws_pol = false,
            .bit_shift = bit_shift,                      // Philips: 1 BCLK delay
            .left_align = left_align,
            .big_endian = false,
            .bit_order_lsb = false,
        },
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = sck,
            .ws = ws,
            .dout = I2S_GPIO_UNUSED,
            .din = sd,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    return i2s_channel_init_std_mode(mic_rx_handle, &std_cfg);
}

static esp_err_t mic_setup(int sck, int ws, int sd)
{
    return mic_setup_ex(sck, ws, sd, I2S_STD_SLOT_LEFT,
                        I2S_DATA_BIT_WIDTH_16BIT, true, true, I2S_SLOT_MODE_MONO);
}

void audio_mic_teardown(void)
{
    if (mic_rx_handle != NULL) {
        esp_log_level_set("i2s_common", ESP_LOG_NONE);
        i2s_channel_disable(mic_rx_handle);
        esp_log_level_set("i2s_common", ESP_LOG_ERROR);
        i2s_del_channel(mic_rx_handle);
        mic_rx_handle = NULL;
    }
}

esp_err_t audio_mic_reinit_ex(int sck, int ws, int sd,
                              i2s_std_slot_mask_t mask,
                              i2s_data_bit_width_t bits,
                              bool left_align, bool bit_shift,
                              i2s_slot_mode_t slot_mode)
{
    audio_mic_teardown();
    return mic_setup_ex(sck, ws, sd, mask, bits, left_align, bit_shift, slot_mode);
}

esp_err_t audio_mic_reinit(int sck, int ws, int sd)
{
    audio_mic_teardown();
    return mic_setup(sck, ws, sd);
}

// --- DAC (MAX98357A) on I2S1 -----------------------------------------------

static void dac_setup_pins(int bclk, int lrc, int din)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    // Deep DMA buffer: playback pulls from a TLS stream, and a network hiccup
    // that outlasts the buffer is audible as a stutter. 16 x 512 frames is
    // about 370 ms at 22050 Hz.
    chan_cfg.dma_desc_num = 16;
    chan_cfg.dma_frame_num = 512;
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &dac_tx_handle, NULL));

    // The rate is retuned per WAV file by player.c; 16 kHz is a placeholder.
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = bclk,
            .ws = lrc,
            .dout = din,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(dac_tx_handle, &std_cfg));
}

static void dac_setup(void)
{
    dac_setup_pins(DAC_BCLK, DAC_LRC, DAC_DIN);
}

esp_err_t audio_mic_slave_on(int bclk, int ws, int din)
{
    audio_mic_teardown();

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_SLAVE);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 240;
    chan_cfg.auto_clear = true;
    esp_err_t ret = i2s_new_channel(&chan_cfg, NULL, &mic_rx_handle);
    if (ret != ESP_OK) return ret;

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(22050),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = bclk,
            .ws = ws,
            .dout = I2S_GPIO_UNUSED,
            .din = din,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    return i2s_channel_init_std_mode(mic_rx_handle, &std_cfg);
}

void audio_dac_reinit_pins(int bclk, int lrc, int din)
{
    audio_dac_deinit();
    dac_setup_pins(bclk, lrc, din);
    player_forget_rate();
}

void audio_dac_deinit(void)
{
    if (dac_tx_handle != NULL) {
        i2s_channel_disable(dac_tx_handle);
        i2s_del_channel(dac_tx_handle);
        dac_tx_handle = NULL;
    }
}

void audio_dac_reinit(void)
{
    audio_dac_deinit();
    dac_setup();
    player_forget_rate();
}

void board_init(void)
{
    ESP_LOGI(TAG, "Initialisiere Board-Hardware");

    // DAC shutdown pin. Start with the amplifier OFF; player.c switches it on
    // for the duration of a playback. Leaving it enabled at boot adds a
    // constant current draw that, combined with WiFi TX peaks, browns the
    // board out on USB power.
    gpio_config_t sd_cfg = {
        .pin_bit_mask = 1ULL << DAC_SD,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&sd_cfg);
    gpio_set_level(DAC_SD, 0);

    // GPIO 44 is U0RXD; gpio_config alone does not detach the UART from the
    // pad, so release it before I2S claims it as the data input.
    gpio_reset_pin(MIC_SD);

    ESP_ERROR_CHECK(mic_setup(MIC_SCK, MIC_WS, MIC_SD));
    dac_setup();

    ESP_LOGI(TAG, "Board initialisiert (Mic SCK=%d WS=%d SD=%d LR=%d[-1=GND], DAC %d/%d/%d SD=%d, Taster %d)",
             MIC_SCK, MIC_WS, MIC_SD, MIC_LR,
             DAC_BCLK, DAC_LRC, DAC_DIN, DAC_SD, BUTTON_GPIO);
}

void audio_amp_enable(bool on)
{
    gpio_set_level(DAC_SD, on ? 1 : 0);
}

i2s_chan_handle_t audio_mic_handle(void) { return mic_rx_handle; }
i2s_chan_handle_t audio_dac_handle(void) { return dac_tx_handle; }
