/*
 * The carousel pages as data. Pure C.
 *
 * Six pages of four tiles. The LLM page shows only while an engine is up
 * (carousel.h). Each tile has a label, one big value and a
 * unit. The view draws a tile; this module decides what goes in it.
 *
 * Numbers must not move as they tick:
 *  - each tile has a fixed font tier, sized for its worst-case value;
 *  - each field has fixed decimal places;
 *  - the view right-aligns the value against a fixed edge, so the last digit
 *    keeps its column, and the unit sits in a fixed slot below.
 * A value too wide for its tier shows an overflow marker (">99"), never a
 * clipped number.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "history.h"
#include "palette.h"
#include "status.h"

#define PAGE_COUNT 6
#define TILES_PER_PAGE 4
#define TILE_VALUE_MAX (STATUS_OCC_MAX + 1)
#define TILE_UNIT_MAX 24
/* The level fill's full scale for power. Provisional: the GB10's GPU stays
 * well under it in practice; raise it if a reading clips. */
#define POWER_FULL_SCALE_W 240.0f
#define FAN_FULL_SCALE_RPM 13500.0f
/* RoCE and cluster IP, per direction: the ConnectX-7 sits behind two PCIe
 * Gen5 x4 links (32 GT/s x4 each, about 126 Gb/s raw), and field reports
 * measure about 200 Gb/s for the whole NIC, not per port. */
#define NIC_FULL_SCALE_BPS 200e9
/* The SSD: PCIe Gen5 x4 (15.75 GB/s after 128b/130b coding). The drive's
 * rated speed is not published for its part number; 14 GB/s is the class
 * of Gen5 x4 drives. */
#define SSD_FULL_SCALE_BYTES_S 14e9

typedef enum {
    PAGE_GPU = 0,
    PAGE_COOLING,
    PAGE_HOST,
    PAGE_NETWORK,
    PAGE_STORAGE,
    PAGE_LLM,
} page_id_t;

/* Font tiers. Each holds a fixed number of monospace characters. */
typedef enum {
    TIER_XL = 0, /* 3 characters, 110 px */
    TIER_L,      /* 4 characters, 82 px */
    TIER_M,      /* 5 characters, 66 px; also short lowercase names */
    TIER_TEXT,   /* 9 characters, 36 px; longer names, cut with "..." */
} tier_t;

/* Characters each tier holds in a tile. */
int tier_width(tier_t t);

typedef enum { TONE_NEUTRAL = 0, TONE_AMBER, TONE_RED } tone_t;

typedef enum {
    METRIC_NONE = 0,
    METRIC_GPU_C,
    METRIC_BOARD_C,
    METRIC_NVME_C,
} metric_t;

/* The one threshold table: amber at or above `amber`, red at or above `red`. */
typedef struct {
    metric_t metric;
    float amber;
    float red;
} threshold_t;

tone_t tone_for(metric_t m, float value);

typedef struct {
    const char *label;
    char value[TILE_VALUE_MAX];
    char unit[TILE_UNIT_MAX];
    tier_t tier;
    tone_t tone;
    hue_t hue;    /* the tile's colors; temperatures follow their tone */
    float fill;   /* level fill, 0..1 of the tile height; < 0 = no fill */
    hist_metric_t hist; /* the metric its history chart shows; HIST_NONE = none */
    bool is_text; /* a name, not a number: left-aligned, may change size */
} tile_t;

const char *page_name(page_id_t p);

/* Fill the four tiles of page p from frame f. */
void page_tiles(page_id_t p, const status_frame_t *f, tile_t out[TILES_PER_PAGE]);

/* ---- Value formatters (exported for tests) ---- */

/* v with `decimals` places, at most `width` characters; FMT_DASH when
 * unknown; an overflow marker (">99", ">999") when too wide. */
char *fmt_fixed(char *buf, size_t cap, float v, int decimals, int width);
/* Fan speed in thousands, one decimal, rounded half up on whole RPM:
 * 2700 -> "2.7", 4050 -> "4.1", 13500 -> "13.5". At most 4 characters. */
char *fmt_krpm(char *buf, size_t cap, float rpm);
/* Uptime as its largest whole unit: value "14", unit "days"; "5" "hours";
 * "42" "min". At most 3 characters. */
void fmt_uptime_tile(double up_s, char *value, size_t vcap, char *unit, size_t ucap);

/* Rates, integer math, at most 4 characters, so the digits keep their
 * columns: "940", "1.2", "18.4", "196". The unit steps when the value
 * reaches 1000: kb/s, Mb/s, Gb/s for bits; MB/s, GB/s for bytes. Zero is
 * "0"; unknown is a dash with no unit; past the last unit, ">999". */
void fmt_bits_rate(double bps, char *value, size_t vcap, char *unit, size_t ucap);
void fmt_bytes_rate(double bytes_s, char *value, size_t vcap, char *unit, size_t ucap);
/* Decimal TB from GB, at most 4 characters: 3340 -> "3.34", 12500 ->
 * "12.5", 128000 -> "128"; ">999" past that. `max_decimals` caps the
 * places (the size in the unit slot uses 1: "4.0"). */
char *fmt_tb(char *buf, size_t cap, double gb, int max_decimals);
/* Tokens per second, the same rules: "45.0", "940", then "2.0" with the
 * unit "k tok/s". */
/* True when page p has nothing to show: the LLM page while the exporter
 * reports no engine up (llm_up 0). An unreadable exporter (llm_up unknown)
 * keeps the page, so its dashes show the fault. */
bool page_hidden(page_id_t p, const status_frame_t *f);
/* The detail page's LLM text: models, then TTFT and speculative
 * acceptance when known. Returns false when no engine is up. */
bool llm_detail(char *buf, size_t cap, const status_frame_t *f);
void fmt_tokens_rate(double tps, char *value, size_t vcap, char *unit, size_t ucap);
