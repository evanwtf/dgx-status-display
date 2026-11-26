/*
 * One parsed status frame (protocol v1, docs/protocol.md).
 *
 * Pure C: no ESP-IDF or LVGL headers. The host unit tests build this code
 * with the host compiler.
 *
 * Unknown values: a float field holds NAN. A string field is empty.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION 1
#define PROTO_MAX_LINE 1024 /* bytes, newline included */
#define STATUS_HOST_MAX 32
#define STATUS_OCC_MAX 48
#define STATUS_LABEL_MAX 15
#define STATUS_MAX_TEMPS 24

typedef struct {
    char label[STATUS_LABEL_MAX + 1];
    float c;
} status_temp_t;

typedef struct {
    int64_t t;        /* host epoch seconds (required) */
    bool has_tz;      /* tz_s was present and valid */
    int32_t tz_s;     /* host UTC offset in seconds */
    char host[STATUS_HOST_MAX + 1]; /* required, non-empty */
    char occ[STATUS_OCC_MAX + 1];   /* empty = unknown */
    double up_s;
    float load1, load5, load15;
    float cpu_pct;
    float mem_used_gib, mem_total_gib;
    float gpu_pct, gpu_w, gpu_c;
    float fan0_rpm, fan1_rpm;
    float board_c, nvme_c, nic_c;
    int n_temps;
    status_temp_t temps[STATUS_MAX_TEMPS];
} status_frame_t;

typedef enum {
    FRAME_OK = 0,
    FRAME_ERR_EMPTY,
    FRAME_ERR_TOO_LONG,
    FRAME_ERR_JSON,
    FRAME_ERR_NOT_OBJECT,
    FRAME_ERR_VERSION,
    FRAME_ERR_REQUIRED,
} frame_result_t;

/* Set every reading to unknown. */
void status_frame_clear(status_frame_t *f);

/*
 * Parse one line (without its newline) into *out. `len` is the byte count;
 * the line need not be NUL-terminated. On any error *out is left cleared.
 * A bad optional field becomes unknown; it does not reject the frame.
 */
frame_result_t frame_parse(const char *line, size_t len, status_frame_t *out);

const char *frame_result_name(frame_result_t r);
