/*
 * Lines the display sends to the host (docs/protocol.md). Pure C.
 *
 *   {"hello":"dgx-status-display","fw":"0.1.0","elf":"1a2b...","rst":"panic","why":"..."}
 *   {"diag":1,"up_s":75,"stk_rx":2100,"stk_ui":3300,"lv_used_pct":41,...}
 *
 * Each function writes one JSON object plus a newline and returns its length,
 * or 0 when it does not fit in cap (nothing useful is sent then).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#define REPORT_WHY_MAX 96 /* characters of the panic reason, before escaping */

/* elf, rst and why are optional: NULL or "" leaves the field out. `why` is
 * cut to REPORT_WHY_MAX characters and escaped for JSON; bytes that are not
 * printable ASCII become '?'. */
size_t report_hello(char *buf, size_t cap, const char *fw, const char *elf, const char *rst,
                    const char *why);

typedef struct {
    int64_t up_s;
    uint32_t stk_rx;      /* lowest free stack of the RX task, bytes */
    uint32_t stk_ui;      /* lowest free stack of the LVGL task, bytes */
    uint32_t lv_used_pct; /* LVGL heap */
    uint32_t lv_free;
    uint32_t lv_big;      /* largest free block in the LVGL heap */
    uint32_t heap;        /* ESP-IDF heap free now */
    uint32_t heap_min;    /* ESP-IDF heap low-water mark */
} report_diag_t;

size_t report_diag(char *buf, size_t cap, const report_diag_t *d);
