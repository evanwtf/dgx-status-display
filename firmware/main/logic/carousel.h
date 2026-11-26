/*
 * Which screen to show, and when to move on. Pure C.
 *
 *  - The pages cycle every CAROUSEL_PAGE_MS.
 *  - A tap moves to the next page and restarts the timer.
 *  - A long press opens the detail list. A tap, or CAROUSEL_DETAIL_MS, goes
 *    back to the carousel.
 *  - LINK_NO_DATA stops everything and shows the NO DATA screen. When frames
 *    come back, the carousel resumes on the page it left, with a fresh timer.
 *    STALE keeps cycling.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "link_state.h"
#include "tiles.h"

#define CAROUSEL_PAGE_MS 10000
#define CAROUSEL_DETAIL_MS 20000

typedef enum { VIEW_PAGE = 0, VIEW_DETAIL, VIEW_NO_DATA } view_kind_t;

typedef struct {
    page_id_t page;
    bool detail;
    bool no_data;
    int64_t page_since_ms;
    int64_t detail_since_ms;
} carousel_t;

void carousel_init(carousel_t *c, int64_t now_ms);
/* Call often (the UI calls it every 250 ms). */
void carousel_tick(carousel_t *c, link_state_t st, int64_t now_ms);
void carousel_tap(carousel_t *c, int64_t now_ms);
void carousel_long_press(carousel_t *c, int64_t now_ms);
view_kind_t carousel_view(const carousel_t *c);
