#include "burnin.h"

/* Nine offsets around the centre. Neighbors differ by at most 4 px, so a
 * step is not a visible jump. */
static const shift_t SHIFTS[] = {
    {0, 0}, {2, 1}, {4, 2}, {2, 4}, {0, 2}, {-2, 4}, {-4, 2}, {-2, -2}, {2, -4},
};

#define N_SHIFTS ((int64_t)(sizeof(SHIFTS) / sizeof(SHIFTS[0])))

shift_t burnin_shift(int64_t now_ms)
{
    if (now_ms < 0) {
        now_ms = 0;
    }
    return SHIFTS[(now_ms / SHIFT_PERIOD_MS) % N_SHIFTS];
}

uint8_t burnin_brightness(link_state_t s)
{
    return s == LINK_NO_DATA ? BRIGHTNESS_NO_DATA : BRIGHTNESS_LIVE;
}
