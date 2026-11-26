/*
 * Split a byte stream into lines of at most PROTO_MAX_LINE bytes.
 *
 * A line longer than the cap is dropped: the reader discards bytes until the
 * next newline and reports LINE_OVERFLOW once for that line. Pure C.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "status.h"

typedef enum {
    LINE_NONE = 0, /* byte consumed, no line yet */
    LINE_READY,    /* a complete line is in buf[0..len) */
    LINE_OVERFLOW, /* an oversize line ended; it was dropped */
} line_event_t;

typedef struct {
    char buf[PROTO_MAX_LINE];
    size_t len;
    int discarding;
    int ready;
} line_reader_t;

void line_reader_init(line_reader_t *r);

/*
 * Feed one byte. On LINE_READY the line is r->buf with length r->len (no
 * newline, a trailing '\r' removed, NUL-terminated). The caller must read
 * it before the next feed.
 */
line_event_t line_reader_feed(line_reader_t *r, uint8_t byte);
