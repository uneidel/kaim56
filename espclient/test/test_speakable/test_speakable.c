#include <unity.h>
#include <string.h>
#include <stdlib.h>
#include "speakable.h"

void setUp(void) {}
void tearDown(void) {}

static void check(const char *in, const char *expect)
{
    char *got = speakable(in);
    if (expect == NULL) {
        TEST_ASSERT_NULL(got);
    } else {
        TEST_ASSERT_NOT_NULL(got);
        TEST_ASSERT_EQUAL_STRING(expect, got);
    }
    free(got);
}

static void test_plain_text_survives(void)      { check("Hallo Welt.", "Hallo Welt."); }
static void test_think_block_removed(void)      { check("⟦think⟧geheim⟦/think⟧Antwort", "Antwort"); }
static void test_unterminated_think_removed(void){ check("Antwort ⟦think⟧ offen bis Ende", "Antwort"); }
static void test_tool_line_removed(void)        { check("🔧 tool_call(x)\nEchter Satz.", "Echter Satz."); }
static void test_code_block_replaced(void)      { check("Vorher.\n```c\nint x;\n```\nNachher.", "Vorher. Codeblock übersprungen Nachher."); }
static void test_link_keeps_text(void)          { check("Siehe [die Doku](https://example.com/a) dort.", "Siehe die Doku dort."); }
static void test_bare_url_dropped(void)         { check("Quelle https://example.com/x ist gut.", "Quelle ist gut."); }
static void test_www_url_dropped(void)          { check("Siehe www.example.com hier.", "Siehe hier."); }
static void test_decorations_dropped(void)      { check("**fett** _kursiv_ `code` # H1 > Zitat | Tab", "fett kursiv code H1 Zitat Tab"); }
static void test_whitespace_collapsed(void)     { check("Zu    viel\n\n\nLuft.", "Zu viel Luft."); }
static void test_empty_returns_null(void)       { check("   \n  ", NULL); }
static void test_only_decorations_returns_null(void) { check("*** ___ ###", NULL); }
static void test_only_think_returns_null(void)  { check("⟦think⟧nur denken⟦/think⟧", NULL); }

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_plain_text_survives);
    RUN_TEST(test_think_block_removed);
    RUN_TEST(test_unterminated_think_removed);
    RUN_TEST(test_tool_line_removed);
    RUN_TEST(test_code_block_replaced);
    RUN_TEST(test_link_keeps_text);
    RUN_TEST(test_bare_url_dropped);
    RUN_TEST(test_www_url_dropped);
    RUN_TEST(test_decorations_dropped);
    RUN_TEST(test_whitespace_collapsed);
    RUN_TEST(test_empty_returns_null);
    RUN_TEST(test_only_decorations_returns_null);
    RUN_TEST(test_only_think_returns_null);
    return UNITY_END();
}
