#include "speakable.h"
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define THINK_OPEN  "⟦think⟧"
#define THINK_CLOSE "⟦/think⟧"
#define TOOL_MARK   "🔧"
#define CODE_NOTE   "Codeblock übersprungen"

typedef struct { char *p; size_t len, cap; } sbuf_t;

static bool sb_init(sbuf_t *b, size_t cap)
{
    b->p = malloc(cap); b->len = 0; b->cap = cap;
    if (b->p == NULL) return false;
    b->p[0] = '\0';        // a filter that removes everything must yield ""
    return true;
}

static bool sb_putn(sbuf_t *b, const char *s, size_t n)
{
    if (b->len + n + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 64;
        while (cap < b->len + n + 1) cap *= 2;
        char *g = realloc(b->p, cap);
        if (g == NULL) return false;
        b->p = g; b->cap = cap;
    }
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = '\0';
    return true;
}

static bool sb_put(sbuf_t *b, const char *s) { return sb_putn(b, s, strlen(s)); }
static bool sb_putc(sbuf_t *b, char c)       { return sb_putn(b, &c, 1); }

static bool starts_with(const char *s, const char *pre)
{
    return strncmp(s, pre, strlen(pre)) == 0;
}

// Rules 1 and 2: drop think blocks, turn fenced code into a spoken note.
static char *strip_blocks(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 64)) return NULL;

    const char *p = in;
    while (*p) {
        if (starts_with(p, THINK_OPEN)) {
            const char *end = strstr(p, THINK_CLOSE);
            if (end == NULL) break;                    // unterminated: drop the rest
            p = end + strlen(THINK_CLOSE);
            continue;
        }
        if (starts_with(p, "```")) {
            const char *end = strstr(p + 3, "```");
            sb_put(&out, " " CODE_NOTE " ");
            if (end == NULL) break;                    // unterminated fence: drop the rest
            p = end + 3;
            continue;
        }
        sb_putc(&out, *p++);
    }
    return out.p;
}

// Rule 3: a line whose first non-space glyph is the tool mark disappears.
static char *strip_tool_lines(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    const char *line = in;
    while (*line) {
        const char *nl = strchr(line, '\n');
        size_t n = nl ? (size_t)(nl - line) : strlen(line);

        const char *t = line;
        while (t < line + n && (*t == ' ' || *t == '\t')) t++;

        if (!starts_with(t, TOOL_MARK)) {
            sb_putn(&out, line, n);
            sb_putc(&out, '\n');
        }
        if (nl == NULL) break;
        line = nl + 1;
    }
    return out.p;
}

static bool url_char(char c)
{
    return c != ' ' && c != '\t' && c != '\n' && c != '\r' && c != '\0' && c != ')';
}

// Rules 4 and 5: [text](url) keeps text; a bare URL vanishes.
static char *strip_links(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    const char *p = in;
    while (*p) {
        if (*p == '[') {
            const char *close = strchr(p, ']');
            if (close != NULL && close[1] == '(') {
                const char *paren = strchr(close + 2, ')');
                if (paren != NULL) {
                    sb_putn(&out, p + 1, (size_t)(close - p - 1));
                    p = paren + 1;
                    continue;
                }
            }
        }
        if (starts_with(p, "http://") || starts_with(p, "https://") || starts_with(p, "www.")) {
            while (url_char(*p)) p++;
            continue;
        }
        sb_putc(&out, *p++);
    }
    return out.p;
}

// Rules 6 and 7: decorations go, whitespace collapses, result is trimmed.
static char *strip_decoration_and_collapse(const char *in)
{
    sbuf_t out;
    if (!sb_init(&out, strlen(in) + 1)) return NULL;

    bool pending_space = false;
    for (const char *p = in; *p; p++) {
        char c = *p;
        if (c == '*' || c == '_' || c == '`' || c == '#' || c == '>' || c == '|') continue;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { pending_space = true; continue; }
        if (pending_space && out.len > 0) sb_putc(&out, ' ');
        pending_space = false;
        sb_putc(&out, c);
    }
    return out.p;
}

char *speakable(const char *input)
{
    if (input == NULL) return NULL;

    char *a = strip_blocks(input);                    if (a == NULL) return NULL;
    char *b = strip_tool_lines(a);         free(a);   if (b == NULL) return NULL;
    char *c = strip_links(b);              free(b);   if (c == NULL) return NULL;
    char *d = strip_decoration_and_collapse(c); free(c);
    if (d == NULL) return NULL;

    if (d[0] == '\0') { free(d); return NULL; }       // rule 8
    return d;
}
