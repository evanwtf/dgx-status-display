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
 *  - A hidden page is skipped. The LLM page hides while no engine is up.
 *    When the page on screen becomes hidden, the carousel moves on at once,
 *    with a fresh timer. Page GPU is never hidden.
 *  - The page count is PAGE_COUNT in near mode and FAR_PAGE_COUNT in far
 *    mode; `page` then holds a far_page_t (far.h). Page 0 is GPU in both.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "link_state.h"
#include "tiles.h"

#define CAROUSEL_PAGE_MS 6000
#define CAROUSEL_DETAIL_MS 20000

typedef enum { VIEW_PAGE = 0, VIEW_DETAIL, VIEW_NO_DATA } view_kind_t;

typedef struct {
    page_id_t page;
    bool detail;
    bool no_data;
    int64_t page_since_ms;
    int64_t detail_since_ms;
    uint32_t hidden; /* bit p set: page p is skipped */
    int n_pages;     /* PAGE_COUNT, or FAR_PAGE_COUNT in far mode */
} carousel_t;

void carousel_init(carousel_t *c, int64_t now_ms);
/* Call often (the UI calls it every 250 ms). */
void carousel_tick(carousel_t *c, link_state_t st, int64_t now_ms);
void carousel_tap(carousel_t *c, int64_t now_ms);
void carousel_long_press(carousel_t *c, int64_t now_ms);
view_kind_t carousel_view(const carousel_t *c);
/* Hide or show page p. Hiding the page on screen moves on now. */
void carousel_set_hidden(carousel_t *c, page_id_t p, bool hidden, int64_t now_ms);
/* Switch to n pages (a mode change): page 0, none hidden, detail closed,
 * fresh timer. */
void carousel_set_page_count(carousel_t *c, int n, int64_t now_ms);
