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
/* A real frame nests 3 deep. cJSON recurses once per level (about 96 bytes of
 * stack each), so deep input is refused before cJSON sees it. */
#define PROTO_MAX_DEPTH 4
/* Range checks: above any real link or disk. */
#define PROTO_MAX_RATE 1e13
#define PROTO_MAX_LINK_MBPS 1e7
#define PROTO_MAX_DISK_GB 1e9

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
    /* Network rates in bit/s, SSD rates in byte/s: deltas between two host
     * frames. double: a 200 Gb/s rate needs more than float's 24 bits only
     * for the last digits, but the formatters do integer math on it. */
    double lan_rx_bps, lan_tx_bps;
    double lan_mbps; /* LAN link speed, Mb/s */
    double roce_rx_bps, roce_tx_bps;
    double cip_bps; /* IP traffic on the cluster NIC, in + out */
    double ssd_rd_bytes_s, ssd_wr_bytes_s;
    double disk_used_gb, disk_size_gb; /* decimal GB */
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
    FRAME_ERR_TOO_DEEP,
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

/* True when `[`/`{` nesting, counted outside JSON strings, never exceeds
 * max_depth. It does not check that the JSON is valid. */
bool json_depth_ok(const char *s, size_t len, int max_depth);
