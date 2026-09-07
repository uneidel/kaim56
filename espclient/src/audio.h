#pragma once
#include "driver/i2s_std.h"
#include "esp_err.h"
#include <stdbool.h>

// Brings up I2S0 (mic RX) and I2S1 (DAC TX) and enables the amplifier.
void board_init(void);

i2s_chan_handle_t audio_mic_handle(void);
i2s_chan_handle_t audio_dac_handle(void);

// Diagnostics: re-point the microphone at a different pin triple, and
// free/rebuild the DAC so its pins can be probed too.
esp_err_t audio_mic_reinit(int sck, int ws, int sd);

// Diagnostics variant: also choose the slot mask and sample width, so the
// classic INMP441 mistakes (L/R strapped to VDD, wrong justification) can be
// swept in software before blaming the wiring.
esp_err_t audio_mic_reinit_ex(int sck, int ws, int sd,
                              i2s_std_slot_mask_t mask,
                              i2s_data_bit_width_t bits,
                              bool left_align, bool bit_shift,
                              i2s_slot_mode_t slot_mode);
void      audio_mic_teardown(void);

// MAX98357A shutdown pin. Enable only while playing: the amplifier draws
// current continuously when enabled, and together with WiFi TX peaks that is
// enough to brown the board out on USB power.
void      audio_amp_enable(bool on);
void      audio_dac_deinit(void);
void      audio_dac_reinit(void);
void      audio_dac_reinit_pins(int bclk, int lrc, int din);

// Diagnostics: configure the mic channel as an I2S *slave* receiver sharing
// the DAC's clock pins, so what I2S1 transmits can be read back on-chip.
esp_err_t audio_mic_slave_on(int bclk, int ws, int din);
