#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef void (*stream_sentence_fn)(const char *sentence, void *user);

typedef struct {
    char              *buf;
    size_t             len;
    size_t             cap;
    stream_sentence_fn cb;
    void              *user;
} stream_t;

bool stream_init(stream_t *s, stream_sentence_fn cb, void *user);

// Appends data and emits every complete sentence it now contains.
bool stream_feed(stream_t *s, const char *data, size_t len);

// Emits whatever is left as a final sentence.
void stream_close(stream_t *s);

void stream_free(stream_t *s);
