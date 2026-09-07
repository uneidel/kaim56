#include "form.h"
#include <string.h>
#include <stdlib.h>

void form_url_decode(char *str)
{
    char *src = str, *dst = str;
    while (*src) {
        if (*src == '%' && src[1] && src[2]) {
            char hex[3] = {src[1], src[2], 0};
            *dst++ = (char)strtol(hex, NULL, 16);
            src += 3;
        } else if (*src == '+') {
            *dst++ = ' ';
            src++;
        } else {
            *dst++ = *src++;
        }
    }
    *dst = '\0';
}

bool form_get_param(const char *content, const char *param,
                    char *value, size_t max_len)
{
    size_t plen = strlen(param);
    const char *p = content;

    value[0] = '\0';
    while (*p) {
        const char *amp = strchr(p, '&');
        size_t pair_len = amp ? (size_t)(amp - p) : strlen(p);
        const char *eq = memchr(p, '=', pair_len);

        if (eq != NULL && (size_t)(eq - p) == plen && memcmp(p, param, plen) == 0) {
            size_t vlen = pair_len - plen - 1;
            if (vlen >= max_len) vlen = max_len - 1;
            memcpy(value, eq + 1, vlen);
            value[vlen] = '\0';
            form_url_decode(value);
            return true;
        }
        if (amp == NULL) break;
        p = amp + 1;
    }
    return false;
}
