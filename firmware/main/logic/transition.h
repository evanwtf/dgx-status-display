/*
 * Page changes fade through black. Pure C.
 *
 * The carousel decides which view should show. This module decides when the
 * panel shows it:
 *
 *   dwell -> fade out (FADE_OUT_MS) -> dark: redraw -> fade in (FADE_IN_MS)
 *         -> the next dwell starts when the fade-in ends.
 *
 * The fade uses the panel brightness, not LVGL opacity, so the redraw at 0
 * is never seen half done. The level follows a quadratic curve (the eye
 * reads brightness roughly as a power law, so a linear ramp looks fast at
 * the bottom and slow at the top).
 *
 * - The target level is read on every step, so an idle dim during a fade
 *   ends the fade at the new level.
 * - NO DATA, entering or leaving, changes at once, without a fade.
 * - A tap or long press during a transition is queued; it runs when the
 *   fade-in ends. At most one is queued; later ones are dropped.
 * - A rotation (KEY) or a mode switch (BOOT) waits for the dark moment, so
 *   the full redraw it causes is never seen. Both use transition_turn().
 * - Times are uint32_t milliseconds: the difference survives a wrap.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "carousel.h"

typedef enum {
    TR_IDLE, /* the dwell: shown == the carousel's view */
    TR_OUT,
    TR_DARK, /* at 0: the caller redraws, then calls transition_swapped() */
    TR_IN,
} tr_phase_t;

typedef enum {
    TR_ACT_NONE,
    TR_ACT_TAP,
    TR_ACT_LONG_PRESS,
} tr_act_t;

typedef struct {
    tr_phase_t phase;
    uint32_t since_ms; /* start of the phase */
    uint32_t out_start_ms;
    /* The view on the panel. While a fade runs it lags the carousel. */
    view_kind_t shown_kind;
    page_id_t shown_page;
    tr_act_t queued;
    bool turn;      /* a rotation waits for the dark moment */
    uint32_t last_ms; /* the last complete transition, fade out to fade in end */
} transition_t;

typedef struct {
    uint8_t level; /* panel brightness to send now */
    /* Apply the waiting changes now (rotation, a burn-in step) and redraw
     * the shown view. In TR_DARK the caller then forces a full refresh and
     * calls transition_swapped(). */
    bool swap;
    bool done; /* a fade-in ended on this step */
} tr_out_t;

void transition_init(transition_t *t, const carousel_t *c);

/* The brightness on the curve: `elapsed` of `total` ms towards `target`
 * when rising, or away from it when falling. Exact at both ends. */
uint8_t fade_level(uint8_t target, uint32_t elapsed, uint32_t total, bool rising);

bool transition_busy(const transition_t *t);

/* Touch: runs now when idle, else queues (at most one). */
void transition_tap(transition_t *t, carousel_t *c, int64_t now_ms);
void transition_long_press(transition_t *t, carousel_t *c, int64_t now_ms);
/* KEY or BOOT: a rotation or a mode switch is waiting. The next step
 * starts a fade for it. */
void transition_turn(transition_t *t);

/* One step: after carousel_tick(), with the brightness the burn-in rules
 * want now. */
tr_out_t transition_tick(transition_t *t, carousel_t *c, uint8_t target, int64_t now_ms);

/* The redraw at 0 is on the panel: start the fade in. */
void transition_swapped(transition_t *t, uint32_t now_ms);

/* The carousel state to draw: the carousel with the shown view. */
carousel_t transition_shown(const transition_t *t, const carousel_t *c);
