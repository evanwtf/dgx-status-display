/*
 * The screen: carousel pages of tiles, a top strip, the detail list and the
 * NO DATA screen. LVGL and main/logic only, no ESP-IDF headers, so the host
 * preview (firmware/test/preview/) renders this exact code.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "logic/carousel.h"
#include "logic/history.h"
#include "logic/link_state.h"
#include "logic/settings.h"
#include "logic/status.h"
#include "logic/tiles.h"
#include "lvgl.h"

typedef struct {
    view_kind_t kind;
    page_id_t page;
    tile_t tiles[TILES_PER_PAGE];
    link_state_t link;
    char clock[8];   /* "22:44", or "" before the first frame */
    char age[24];    /* time since the last frame: "5s", "2m 13s", "never" */
    const status_frame_t *frame;
    chart_style_t style;   /* level fill or 2-minute history */
    /* For CHART_HISTORY: fills chart column heights (hist_heights() rules);
     * NULL draws no chart. A function, so the history can stay behind the
     * serial task's lock instead of being copied. */
    bool (*hist_heights)(hist_metric_t m, const status_frame_t *f, int height,
                         int16_t out[HIST_LEN]);
    /* Detail footer. */
    uint32_t accepted;
    uint32_t rejected;
    const char *fw_version;
    uint32_t stack_free_rx;
    uint32_t stack_free_ui;
} view_model_t;

typedef void (*view_cb_t)(void);

/* Build every widget on `screen`. Call once, with the LVGL lock held. */
void view_init(lv_obj_t *screen, view_cb_t on_tap, view_cb_t on_long_press,
               view_cb_t on_chart_toggle);

/* Fill the parts of the model that come from the frame, the link state and
 * the carousel. The caller sets style, hist and the detail footer. */
void view_model_fill(view_model_t *m, const status_frame_t *f, link_state_t st,
                     int64_t age_s, const carousel_t *c);

/* Draw the model. Sets text only where it changed. */
void view_render(const view_model_t *m);

/* Animate the level fill when a value changes (default on). The host
 * preview turns it off: its clock does not run. */
void view_set_animations(bool on);

/* Move the whole layout (burn-in pixel shift). */
void view_set_shift(int dx, int dy);

/* The value label of tile i (0-3). For the alignment test in the preview. */
lv_obj_t *view_tile_value(int i);
