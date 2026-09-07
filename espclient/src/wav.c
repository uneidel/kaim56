#include "wav.h"
#include <string.h>

static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}

static uint32_t get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

size_t wav_write_header(uint8_t *dst, uint32_t sample_rate, uint16_t channels,
                        uint16_t bits, uint32_t pcm_bytes)
{
    uint16_t block_align = (uint16_t)(channels * (bits / 8));
    uint32_t byte_rate = sample_rate * block_align;

    memcpy(dst, "RIFF", 4);
    put_u32(dst + 4, 36 + pcm_bytes);
    memcpy(dst + 8, "WAVE", 4);

    memcpy(dst + 12, "fmt ", 4);
    put_u32(dst + 16, 16);           // PCM fmt chunk size
    put_u16(dst + 20, 1);            // format = PCM
    put_u16(dst + 22, channels);
    put_u32(dst + 24, sample_rate);
    put_u32(dst + 28, byte_rate);
    put_u16(dst + 32, block_align);
    put_u16(dst + 34, bits);

    memcpy(dst + 36, "data", 4);
    put_u32(dst + 40, pcm_bytes);

    return WAV_HEADER_SIZE;
}

bool wav_parse_header(const uint8_t *src, size_t len, wav_info_t *out)
{
    if (src == NULL || out == NULL || len < 12) return false;
    if (memcmp(src, "RIFF", 4) != 0 || memcmp(src + 8, "WAVE", 4) != 0) return false;

    bool have_fmt = false;
    size_t pos = 12;

    while (pos + 8 <= len) {
        const uint8_t *id = src + pos;
        uint32_t size = get_u32(src + pos + 4);
        size_t body = pos + 8;

        if (memcmp(id, "fmt ", 4) == 0) {
            if (body + 16 > len) return false;
            out->channels        = get_u16(src + body + 2);
            out->sample_rate     = get_u32(src + body + 4);
            out->bits_per_sample = get_u16(src + body + 14);
            have_fmt = true;
        } else if (memcmp(id, "data", 4) == 0) {
            if (!have_fmt) return false;
            out->data_offset = (uint32_t)body;
            out->data_bytes  = size;
            return true;
        }

        pos = body + size + (size & 1u);   // chunks are word-aligned
    }

    return false;
}
