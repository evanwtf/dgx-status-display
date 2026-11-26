/*
 * Link state from the age of the last accepted frame. Pure C.
 *
 *   LIVE     age <= 3 s
 *   STALE    3 s < age <= 10 s
 *   NO_DATA  age > 10 s, or no frame since boot
 *
 * Ages come from the board's own monotonic clock, so the board needs no RTC.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define LINK_LIVE_MAX_MS 3000
#define LINK_STALE_MAX_MS 10000

typedef enum {
    LINK_NO_DATA = 0,
    LINK_LIVE,
    LINK_STALE,
} link_state_t;

typedef struct {
    bool ever;           /* a frame was accepted since boot */
    int64_t last_ok_ms;  /* monotonic time of the last accepted frame */
} link_t;

void link_init(link_t *l);
void link_frame_ok(link_t *l, int64_t now_ms);
link_state_t link_state(const link_t *l, int64_t now_ms);
/* Whole seconds since the last accepted frame; -1 if never. */
int64_t link_age_s(const link_t *l, int64_t now_ms);
const char *link_state_name(link_state_t s);
