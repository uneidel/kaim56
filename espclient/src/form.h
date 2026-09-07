#pragma once
#include <stddef.h>
#include <stdbool.h>

// Decodes %XX and + in place.
void form_url_decode(char *str);

// Reads one field from an application/x-www-form-urlencoded body by walking
// &-separated pairs, so a value containing "name=" cannot be mistaken for the
// field itself. Returns false when the field is absent (value is set to "").
bool form_get_param(const char *content, const char *param,
                    char *value, size_t max_len);
