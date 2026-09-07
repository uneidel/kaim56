#include "stream.h"
#include <stdlib.h>
#include <string.h>

#define THINK_OPEN  "⟦think⟧"
#define THINK_CLOSE "⟦/think⟧"
#define ELLIPSIS_2  0xE2
#define ELLIPSIS_1  0x80
#define ELLIPSIS_0  0xA6                                   // U+2026 as E2 80 A6

static bool is_space(char c)
{
    return c == ' ' || c == '\n' || c == '\r' || c == '\t';
}

// A sentence terminator whose final byte sits at index i.
static bool terminator_ends_at(const char *b, size_t i)
{
    char c = b[i];
    if (c == '.' || c == '!' || c == '?' || c == ':') return true;
    if (i >= 2 && (unsigned char)b[i - 2] == ELLIPSIS_2 &&
                  (unsigned char)b[i - 1] == ELLIPSIS_1 &&
                  (unsigned char)b[i]     == ELLIPSIS_0) return true;
    return false;
}

static size_t count_occurrences(const char *hay, size_t hay_len, const char *needle)
{
    size_t nl = strlen(needle), n = 0;
    if (nl == 0 || hay_len < nl) return 0;
    for (size_t i = 0; i + nl <= hay_len; i++) {
        if (memcmp(hay + i, needle, nl) == 0) { n++; i += nl - 1; }
    }
    return n;
}

// Rules 3 and 4: any unclosed construct gags the whole stream.
static bool output_is_gagged(const stream_t *s)
{
    if (count_occurrences(s->buf, s->len, THINK_OPEN) >
        count_occurrences(s->buf, s->len, THINK_CLOSE)) return true;
    if (count_occurrences(s->buf, s->len, "```") % 2u == 1u) return true;
    return false;
}

static void emit_trimmed(stream_t *s, const char *start, size_t len)
{
    while (len > 0 && is_space(*start)) { start++; len--; }
    while (len > 0 && is_space(start[len - 1])) len--;
    if (len == 0) return;

    char *sentence = malloc(len + 1);
    if (sentence == NULL) return;
    memcpy(sentence, start, len);
    sentence[len] = '\0';
    s->cb(sentence, s->user);
    free(sentence);
}

static void consume(stream_t *s, size_t upto)
{
    memmove(s->buf, s->buf + upto, s->len - upto);
    s->len -= upto;
}

bool stream_init(stream_t *s, stream_sentence_fn cb, void *user)
{
    s->cap = 1024;
    s->buf = malloc(s->cap);
    if (s->buf == NULL) return false;
    s->len = 0;
    s->cb = cb;
    s->user = user;
    return true;
}

bool stream_feed(stream_t *s, const char *data, size_t len)
{
    if (s->len + len + 1 > s->cap) {
        size_t cap = s->cap;
        while (cap < s->len + len + 1) cap *= 2;
        char *grown = realloc(s->buf, cap);
        if (grown == NULL) return false;
        s->buf = grown;
        s->cap = cap;
    }
    memcpy(s->buf + s->len, data, len);
    s->len += len;

    if (output_is_gagged(s)) return true;

    // Rule 2: never consider the final byte as a terminator, so stop at len-2.
    for (size_t i = 0; s->len >= 2 && i <= s->len - 2; i++) {
        if (terminator_ends_at(s->buf, i) && is_space(s->buf[i + 1])) {
            emit_trimmed(s, s->buf, i + 1);
            size_t skip = i + 1;
            while (skip < s->len && is_space(s->buf[skip])) skip++;
            consume(s, skip);
            i = (size_t)-1;   // restart the scan on the shortened buffer
        }
    }
    return true;
}

void stream_close(stream_t *s)
{
    if (s->len > 0) {
        emit_trimmed(s, s->buf, s->len);
        s->len = 0;
    }
}

void stream_free(stream_t *s)
{
    free(s->buf);
    s->buf = NULL;
    s->len = s->cap = 0;
}
