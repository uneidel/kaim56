#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#define WAV_HEADER_SIZE 44

typedef struct {
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t bits_per_sample;
    uint32_t data_offset;   // byte offset of PCM data from the start of the stream
    uint32_t data_bytes;    // declared length of the PCM payload
} wav_info_t;

// Writes a canonical 44-byte PCM header into dst. Returns WAV_HEADER_SIZE.
size_t wav_write_header(uint8_t *dst, uint32_t sample_rate, uint16_t channels,
                        uint16_t bits, uint32_t pcm_bytes);

// Walks RIFF chunks looking for "fmt " and "data". Returns false if either is
// missing or the buffer ends early.
bool wav_parse_header(const uint8_t *src, size_t len, wav_info_t *out);
