/*
 * AMOLED burn-in care. Pure C.
 *
 * - Pixel shift: the whole layout moves by a few pixels every SHIFT_PERIOD_MS.
 *   It walks a fixed loop of offsets, so no pixel stays lit in one place.
 * - Brightness: moderate when the link is LIVE or STALE, lower in NO DATA.
 */
#pragma once

#include <stdint.h>

#include "link_state.h"

#define SHIFT_PERIOD_MS (3 * 60 * 1000)
#define SHIFT_MAX_PX 4

/* Panel brightness (0x51 register value, 0-255). */
#define BRIGHTNESS_LIVE 110
#define BRIGHTNESS_NO_DATA 30

typedef struct {
    int8_t dx;
    int8_t dy;
} shift_t;

/* The offset for the given uptime. Every offset is within +/-SHIFT_MAX_PX. */
shift_t burnin_shift(int64_t now_ms);

uint8_t burnin_brightness(link_state_t s);
