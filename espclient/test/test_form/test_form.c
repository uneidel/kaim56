#include <unity.h>
#include <string.h>
#include "form.h"

void setUp(void) {}
void tearDown(void) {}

static void test_simple_field(void)
{
    char v[64];
    TEST_ASSERT_TRUE(form_get_param("url=abc&user=bob", "url", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("abc", v);
    TEST_ASSERT_TRUE(form_get_param("url=abc&user=bob", "user", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("bob", v);
}

static void test_missing_field_yields_empty(void)
{
    char v[64];
    TEST_ASSERT_FALSE(form_get_param("url=abc", "pass", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("", v);
}

// The bug this module exists to prevent. The old implementation did
// strstr(content, "user="), which finds "user=" inside "superuser=" and
// returns bob. Walking &-separated pairs returns alice.
static void test_field_name_inside_another_field_name(void)
{
    char v[128];
    const char *body = "superuser=bob&user=alice";
    TEST_ASSERT_TRUE(form_get_param(body, "user", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("alice", v);
}

// A field name that is a prefix of another must not match it.
static void test_prefix_field_name_not_matched(void)
{
    char v[64];
    TEST_ASSERT_TRUE(form_get_param("max_seconds=99&max_s=30", "max_s", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("30", v);
}

static void test_last_field_without_trailing_amp(void)
{
    char v[64];
    TEST_ASSERT_TRUE(form_get_param("a=1&b=2&c=3", "c", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("3", v);
}

static void test_empty_value(void)
{
    char v[64];
    TEST_ASSERT_TRUE(form_get_param("a=&b=2", "a", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("", v);
}

static void test_url_decoding(void)
{
    char v[128];
    TEST_ASSERT_TRUE(form_get_param("prompt=Hallo+Welt%21&x=1", "prompt", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("Hallo Welt!", v);
}

static void test_value_truncated_to_capacity(void)
{
    char v[5];
    TEST_ASSERT_TRUE(form_get_param("a=abcdefgh", "a", v, sizeof(v)));
    TEST_ASSERT_EQUAL_STRING("abcd", v);
}

static void test_decode_percent_and_plus(void)
{
    char s[64];
    strcpy(s, "a%20b+c%2Fd");
    form_url_decode(s);
    TEST_ASSERT_EQUAL_STRING("a b c/d", s);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_simple_field);
    RUN_TEST(test_missing_field_yields_empty);
    RUN_TEST(test_field_name_inside_another_field_name);
    RUN_TEST(test_prefix_field_name_not_matched);
    RUN_TEST(test_last_field_without_trailing_amp);
    RUN_TEST(test_empty_value);
    RUN_TEST(test_url_decoding);
    RUN_TEST(test_value_truncated_to_capacity);
    RUN_TEST(test_decode_percent_and_plus);
    return UNITY_END();
}
