#include <unity.h>
#include <string.h>
#include <stdlib.h>
#include "stream.h"

#define MAX_OUT 16
static char out[MAX_OUT][512];
static int out_n;

static void collect(const char *s, void *user) {
    (void)user;
    if (out_n < MAX_OUT) { strncpy(out[out_n], s, 511); out[out_n][511] = 0; out_n++; }
}

void setUp(void) { out_n = 0; memset(out, 0, sizeof(out)); }
void tearDown(void) {}

static void feed(stream_t *s, const char *text) { stream_feed(s, text, strlen(text)); }

static void test_emits_on_period_space(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Hallo Welt. Zweiter Satz");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Hallo Welt.", out[0]);
    stream_free(&s);
}

// Rule 2: a terminator as the final byte must not fire.
static void test_terminator_at_end_does_not_emit(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Noch nicht fertig.");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_all_terminators(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Eins! Zwei? Drei: Vier… x");
    TEST_ASSERT_EQUAL_INT(4, out_n);
    TEST_ASSERT_EQUAL_STRING("Eins!", out[0]);
    TEST_ASSERT_EQUAL_STRING("Zwei?", out[1]);
    TEST_ASSERT_EQUAL_STRING("Drei:", out[2]);
    TEST_ASSERT_EQUAL_STRING("Vier…", out[3]);
    stream_free(&s);
}

static void test_newline_also_terminates(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Satz eins.\nRest");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Satz eins.", out[0]);
    stream_free(&s);
}

// Arrives one token at a time, as the chat endpoint delivers it.
static void test_token_by_token(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    const char *toks[] = {"Das", " ist", " ein", " Satz.", " Und", " noch", " einer."};
    for (int i = 0; i < 7; i++) feed(&s, toks[i]);
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Das ist ein Satz.", out[0]);
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(2, out_n);
    TEST_ASSERT_EQUAL_STRING("Und noch einer.", out[1]);
    stream_free(&s);
}

// Rule 3: an unterminated think block gags the streamer entirely.
static void test_open_think_block_holds_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "⟦think⟧ Ich denke nach. Immer noch. ");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_closed_think_block_releases_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "⟦think⟧ nachdenken ⟦/think⟧ Antwort hier. x");
    TEST_ASSERT_EQUAL_INT(1, out_n);
    stream_free(&s);
}

// Rule 4: odd number of fences = open.
static void test_open_code_fence_holds_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Hier Code:\n```c\nint x = 1. y\n");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_closed_code_fence_releases_output(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Code:\n```c\nint x = 1;\n```\nFertig. x");
    TEST_ASSERT_TRUE(out_n >= 1);
    stream_free(&s);
}

static void test_close_flushes_remainder(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "Kein Satzende hier");
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(1, out_n);
    TEST_ASSERT_EQUAL_STRING("Kein Satzende hier", out[0]);
    stream_free(&s);
}

static void test_close_on_empty_emits_nothing(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "   \n  ");
    stream_close(&s);
    TEST_ASSERT_EQUAL_INT(0, out_n);
    stream_free(&s);
}

static void test_sentences_are_trimmed(void)
{
    stream_t s;
    stream_init(&s, collect, NULL);
    feed(&s, "   Erster Satz.    Zweiter Satz. x");
    TEST_ASSERT_EQUAL_INT(2, out_n);
    TEST_ASSERT_EQUAL_STRING("Erster Satz.", out[0]);
    TEST_ASSERT_EQUAL_STRING("Zweiter Satz.", out[1]);
    stream_free(&s);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_emits_on_period_space);
    RUN_TEST(test_terminator_at_end_does_not_emit);
    RUN_TEST(test_all_terminators);
    RUN_TEST(test_newline_also_terminates);
    RUN_TEST(test_token_by_token);
    RUN_TEST(test_open_think_block_holds_output);
    RUN_TEST(test_closed_think_block_releases_output);
    RUN_TEST(test_open_code_fence_holds_output);
    RUN_TEST(test_closed_code_fence_releases_output);
    RUN_TEST(test_close_flushes_remainder);
    RUN_TEST(test_close_on_empty_emits_nothing);
    RUN_TEST(test_sentences_are_trimmed);
    return UNITY_END();
}
