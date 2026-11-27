/*
 * Far mode: one metric per page, in text sized to read from 2 m. Pure C.
 *
 * The owner reads the board from 2 m. An eye chart on the board set the
 * sizes (docs/changelog.md, far mode):
 *  - words: about 100 px cap height (Barlow 140); 81 px (Barlow 112) when
 *    a word is too wide for 140;
 *  - values: 141 px digits (JetBrains Mono 187); 109 px (147) for a value
 *    slot of 4 or more characters;
 *  - units and names: 81 px cap height (Barlow 112) or more.
 * Nothing that carries meaning is under 80 px, so words, units and names
 * are upper case: lower-case letters at these sizes are under 60 px.
 *
 * Each page: the word at the top, the value and its unit, and the
 * metric's level fill across the whole screen. The page uses the same hue
 * and fill scale as the metric's near-mode tile (tiles.c).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "history.h"
#include "palette.h"
#include "status.h"
#include "tiles.h"

typedef enum {
    FAR_GPU = 0,
    FAR_POWER,
    FAR_TEMP,
    FAR_LLM,
    FAR_MODEL,
    FAR_MEMORY,
    FAR_CPU,
    FAR_PAGE_COUNT,
} far_page_t;

#define FAR_VALUE_MAX 16
#define FAR_UNIT_MAX 24
/* An upper-cased name, plus room for "..." */
#define FAR_TEXT_MAX (STATUS_OCC_MAX + 4)

typedef struct {
    const char *word;        /* "GPU", "POWER", ... */
    char value[FAR_VALUE_MAX];
    char unit[FAR_UNIT_MAX]; /* upper case; "" for none */
    /* Characters the value slot holds. The view right-aligns the value in
     * a slot this wide, so the last digit keeps its column as it ticks.
     * 3 or fewer: the 141 px digits; 4 or more: the 109 px digits. */
    int slot;
    bool is_text;            /* the Model page: `text`, not `value` */
    char text[FAR_TEXT_MAX]; /* the name, upper case */
    tone_t tone;
    hue_t hue;
    float fill;              /* level fill, 0..1 of the screen; < 0 = none */
    hist_metric_t hist;      /* the history chart's metric; HIST_NONE = none */
} far_t;

/* The page in mode `far` is page `p` of FAR_PAGE_COUNT. */
const char *far_page_name(far_page_t p);

/* True when page p has nothing to show:
 *  - LLM: unless an engine is up (llm_up 1 or more), as in near mode;
 *  - Model: when no engine is up and `occ` is "idle" or unknown. */
bool far_hidden(far_page_t p, const status_frame_t *f);

/* Fill page p from frame f. */
void far_page(far_page_t p, const status_frame_t *f, far_t *out);

/* The 141 px digits (true) or the 109 px digits (false) for this page. */
bool far_big_digits(const far_t *t);

/* The Model page's name: the engines' models while an engine is up (each
 * without its "org/" prefix), else the GPU occupant. Upper case. A dash
 * when neither is known. */
void far_name_text(const status_frame_t *f, char *buf, size_t cap);

/* ---- The name's layout ---- */

typedef enum {
    FAR_NAME_BIG = 0, /* Barlow 140: 100 px caps */
    FAR_NAME_SMALL,   /* Barlow 112: 81 px caps */
    FAR_NAME_SIZES,
} far_name_size_t;

/* The width in px of the first n bytes of s at `size`, or -1 when the font
 * lacks one of the glyphs. The view measures with LVGL; tests use a fake. */
typedef int (*far_width_fn)(const char *s, size_t n, far_name_size_t size, void *ctx);

typedef struct {
    far_name_size_t size;
    int lines; /* 1 or 2 */
    char line[2][FAR_TEXT_MAX + 4]; /* + "..." */
    bool cut;  /* the second line ends in "..." */
} far_name_t;

/*
 * Lay out `name` in at most two lines of max_w px, as large as fits:
 * one line big, two lines big, one line small, two lines small, and last
 * two lines small with the second cut and ended with "...".
 *
 * Two lines break after a separator: a space, a comma, '-', '_', '/' or
 * ':'. Only when no such break fits, and only at the small size, after a
 * '.' (it splits a version number), and then anywhere. Of the breaks that
 * fit, the latest wins. A space at the break is dropped.
 */
void far_name_layout(const char *name, int max_w, far_width_fn width, void *ctx,
                     far_name_t *out);
