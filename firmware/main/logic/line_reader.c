#include "line_reader.h"

#include <string.h>

void line_reader_init(line_reader_t *r)
{
    memset(r, 0, sizeof(*r));
}

line_event_t line_reader_feed(line_reader_t *r, uint8_t byte)
{
    if (r->ready) {
        /* The caller read the previous line. Start a new one. */
        r->ready = 0;
        r->len = 0;
    }
    if (byte == '\n') {
        if (r->discarding) {
            r->discarding = 0;
            r->len = 0;
            return LINE_OVERFLOW;
        }
        if (r->len > 0 && r->buf[r->len - 1] == '\r') {
            r->len--;
        }
        r->buf[r->len] = '\0';
        if (r->len == 0) {
            return LINE_NONE; /* blank line: nothing to parse */
        }
        r->ready = 1;
        return LINE_READY;
    }
    if (r->discarding) {
        return LINE_NONE;
    }
    /* The newline counts toward the cap, so a line holds at most
     * PROTO_MAX_LINE - 1 bytes before it. That also leaves room for the NUL. */
    if (r->len >= PROTO_MAX_LINE - 1) {
        r->discarding = 1;
        r->len = 0;
        return LINE_NONE;
    }
    r->buf[r->len++] = (char)byte;
    return LINE_NONE;
}
