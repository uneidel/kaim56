#include <unity.h>
#include <string.h>
#include "wav.h"

void setUp(void) {}
void tearDown(void) {}

static void test_header_is_44_bytes(void)
{
    uint8_t buf[64];
    TEST_ASSERT_EQUAL_UINT32(44, wav_write_header(buf, 16000, 1, 16, 32000));
}

static void test_header_fields(void)
{
    uint8_t buf[64];
    wav_write_header(buf, 16000, 1, 16, 32000);

    TEST_ASSERT_EQUAL_MEMORY("RIFF", buf, 4);
    TEST_ASSERT_EQUAL_MEMORY("WAVE", buf + 8, 4);
    TEST_ASSERT_EQUAL_MEMORY("fmt ", buf + 12, 4);
    TEST_ASSERT_EQUAL_MEMORY("data", buf + 36, 4);

    TEST_ASSERT_EQUAL_UINT32(36 + 32000, buf[4] | (buf[5]<<8) | (buf[6]<<16) | ((uint32_t)buf[7]<<24));
    TEST_ASSERT_EQUAL_UINT32(32000, buf[28] | (buf[29]<<8) | (buf[30]<<16) | ((uint32_t)buf[31]<<24));
    TEST_ASSERT_EQUAL_UINT16(2, buf[32] | (buf[33]<<8));
}

static void test_roundtrip(void)
{
    uint8_t buf[64];
    wav_info_t info;
    wav_write_header(buf, 16000, 1, 16, 32000);

    TEST_ASSERT_TRUE(wav_parse_header(buf, 44, &info));
    TEST_ASSERT_EQUAL_UINT32(16000, info.sample_rate);
    TEST_ASSERT_EQUAL_UINT16(1, info.channels);
    TEST_ASSERT_EQUAL_UINT16(16, info.bits_per_sample);
    TEST_ASSERT_EQUAL_UINT32(44, info.data_offset);
    TEST_ASSERT_EQUAL_UINT32(32000, info.data_bytes);
}

// Piper emits 22050 Hz mono. FIXTURE-UNVERIFIED: header synthesised, not captured.
static void test_parse_piper_rate(void)
{
    uint8_t buf[64];
    wav_info_t info;
    wav_write_header(buf, 22050, 1, 16, 100);

    TEST_ASSERT_TRUE(wav_parse_header(buf, 44, &info));
    TEST_ASSERT_EQUAL_UINT32(22050, info.sample_rate);
}

// A LIST chunk before "data" must not break parsing.
static void test_parse_skips_unknown_chunks(void)
{
    uint8_t buf[80];
    wav_info_t info;
    memset(buf, 0, sizeof(buf));

    memcpy(buf, "RIFF", 4);
    buf[4] = 72;
    memcpy(buf + 8, "WAVE", 4);
    memcpy(buf + 12, "fmt ", 4);
    buf[16] = 16;
    buf[20] = 1; buf[22] = 1;
    buf[24] = 0x22; buf[25] = 0x56;   // 22050
    buf[34] = 16;
    memcpy(buf + 36, "LIST", 4);
    buf[40] = 4;
    memcpy(buf + 48, "data", 4);
    buf[52] = 10;

    TEST_ASSERT_TRUE(wav_parse_header(buf, 80, &info));
    TEST_ASSERT_EQUAL_UINT32(22050, info.sample_rate);
    TEST_ASSERT_EQUAL_UINT32(56, info.data_offset);
    TEST_ASSERT_EQUAL_UINT32(10, info.data_bytes);
}

static void test_parse_rejects_short_buffer(void)
{
    uint8_t buf[8] = {'R','I','F','F',0,0,0,0};
    wav_info_t info;
    TEST_ASSERT_FALSE(wav_parse_header(buf, 8, &info));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_header_is_44_bytes);
    RUN_TEST(test_header_fields);
    RUN_TEST(test_roundtrip);
    RUN_TEST(test_parse_piper_rate);
    RUN_TEST(test_parse_skips_unknown_chunks);
    RUN_TEST(test_parse_rejects_short_buffer);
    return UNITY_END();
}
