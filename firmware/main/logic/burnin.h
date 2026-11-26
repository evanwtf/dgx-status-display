/*
 * AMOLED burn-in care. Pure C.
 *
 * - Pixel shift: the whole layout walks a 16-step orbit of radius 10 px, one
 *   step every SHIFT_PERIOD_MS (a full loop in 48 minutes). Each step moves
 *   at most 4 px, so no step is a visible jump, and no lit pixel stays in one
 *   place.
 * - Brightness: moderate while LIVE or STALE. After IDLE_DIM_AFTER_MS with no
 *   change (no tap, no link-state change, no new GPU occupant), a gentle step
 *   down. Lowest in NO DATA.
 */
#pragma once

#include <stdint.h>

#include "link_state.h"

#define SHIFT_PERIOD_MS (3 * 60 * 1000)
#define SHIFT_MAX_PX 10
#define SHIFT_MAX_STEP_PX 4

#define IDLE_DIM_AFTER_MS (10 * 60 * 1000)

/* Panel brightness (register 0x51, 0-255). */
#define BRIGHTNESS_LIVE 110
#define BRIGHTNESS_IDLE 70
#define BRIGHTNESS_NO_DATA 30

typedef struct {
    int8_t dx;
    int8_t dy;
} shift_t;

/* The offset for the given uptime. Every offset is within +/-SHIFT_MAX_PX. */
shift_t burnin_shift(int64_t now_ms);

/* Brightness for the link state and the time since the last change. */
uint8_t burnin_brightness(link_state_t s, int64_t ms_since_change);
