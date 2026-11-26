/*
 * Every color the UI draws. Pure C: the host test computes the WCAG contrast
 * of every text color over every background it can sit on (test_logic.c).
 *
 * Rules (tested): values >= 7:1, labels and units >= 4.5:1, over a tile's
 * base, its level fill and its chart line. That caps how bright a fill or
 * line can be under warm-white digits: about 0.09 relative luminance.
 */
#pragma once

#include <stdint.h>

typedef enum {
    HUE_TEAL = 0, /* GPU usage, CPU */
    HUE_INDIGO,   /* power, memory */
    HUE_VIOLET,   /* fans */
    HUE_MOSS,     /* running, up, load: moss green, 95 deg, apart from amber */
    HUE_COOL,     /* temperatures: below the amber threshold */
    HUE_WARM,     /* at or above amber */
    HUE_HOT,      /* at or above red */
    HUE_NET,      /* network: LAN, RoCE, cluster IP; rose, 318 deg */
    HUE_STORE,    /* storage: SSD and disk; green, 136 deg, between moss and teal */
    HUE_COUNT,
} hue_t;

typedef struct {
    uint32_t base; /* the tile */
    uint32_t fill; /* the level fill and the chart area: same hue, brighter */
    uint32_t line; /* the chart's top line: brighter again */
    uint32_t tint; /* labels and units */
    uint32_t edge; /* 3 px top edge for warm and hot; 0 = none */
} hue_colors_t;

const hue_colors_t *hue_colors(hue_t h);

#define COLOR_GROUND 0x000000u
#define COLOR_VALUE 0xFFF8ECu       /* warm white: every value */
#define COLOR_TITLE 0xE2C894u       /* Spark gold: the page title */
#define COLOR_CLOCK 0xEDE6D6u
#define COLOR_DOT_LIVE 0xE2C894u
#define COLOR_STALE 0xFFB547u       /* the STALE dot and its age text */
#define COLOR_DOT_NO_DATA 0xFF5E4Du
#define COLOR_NO_DATA_HEAD 0xFF8A7Au
#define COLOR_TEXT 0xEDE6D6u        /* text on black: NO DATA hint, detail */
#define COLOR_TEXT_MUTED 0xB9B0A0u  /* secondary text on black: detail labels */
