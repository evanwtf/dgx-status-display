#include "report.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

/* Append s to buf at *n as a JSON string body. False when it does not fit. */
static bool put_escaped(char *buf, size_t cap, size_t *n, const char *s, size_t max_chars)
{
    for (size_t i = 0; s[i] && i < max_chars; i++) {
        unsigned char c = (unsigned char)s[i];
        char tmp[3] = {0};
        if (c == '"' || c == '\\') {
            tmp[0] = '\\';
            tmp[1] = (char)c;
        } else if (c < 0x20 || c >= 0x7F) {
            tmp[0] = c == '\n' || c == '\t' || c == '\r' ? ' ' : '?';
        } else {
            tmp[0] = (char)c;
        }
        size_t k = strlen(tmp);
        if (*n + k >= cap) {
            return false;
        }
        memcpy(buf + *n, tmp, k);
        *n += k;
    }
    buf[*n] = '\0';
    return true;
}

static bool put(char *buf, size_t cap, size_t *n, const char *s)
{
    size_t k = strlen(s);
    if (*n + k >= cap) {
        return false;
    }
    memcpy(buf + *n, s, k + 1);
    *n += k;
    return true;
}

static bool field(char *buf, size_t cap, size_t *n, const char *key, const char *value,
                  size_t max_chars)
{
    return put(buf, cap, n, ",\"") && put(buf, cap, n, key) && put(buf, cap, n, "\":\"") &&
           put_escaped(buf, cap, n, value, max_chars) && put(buf, cap, n, "\"");
}

size_t report_hello(char *buf, size_t cap, const char *fw, const char *elf, const char *rst,
                    const char *why)
{
    if (cap == 0) {
        return 0;
    }
    size_t n = 0;
    buf[0] = '\0';
    bool ok = put(buf, cap, &n, "{\"hello\":\"dgx-status-display\"") &&
              field(buf, cap, &n, "fw", fw ? fw : "", 32);
    if (ok && elf && *elf) {
        ok = field(buf, cap, &n, "elf", elf, 64);
    }
    if (ok && rst && *rst) {
        ok = field(buf, cap, &n, "rst", rst, 16);
    }
    if (ok && why && *why) {
        ok = field(buf, cap, &n, "why", why, REPORT_WHY_MAX);
    }
    ok = ok && put(buf, cap, &n, "}\n");
    if (!ok) {
        buf[0] = '\0';
        return 0;
    }
    return n;
}
