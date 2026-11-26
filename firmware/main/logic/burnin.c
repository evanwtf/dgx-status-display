#include "burnin.h"

/* round(10 * cos/sin(2*pi*k/16)), k = 0..15. */
static const shift_t ORBIT[] = {
    {10, 0},  {9, 4},    {7, 7},   {4, 9},   {0, 10},  {-4, 9},  {-7, 7},  {-9, 4},
    {-10, 0}, {-9, -4},  {-7, -7}, {-4, -9}, {0, -10}, {4, -9},  {7, -7},  {9, -4},
};

#define N_ORBIT ((int64_t)(sizeof(ORBIT) / sizeof(ORBIT[0])))

shift_t burnin_shift(int64_t now_ms)
{
    if (now_ms < 0) {
        now_ms = 0;
    }
    return ORBIT[(now_ms / SHIFT_PERIOD_MS) % N_ORBIT];
}

uint8_t burnin_brightness(link_state_t s, int64_t ms_since_change)
{
    if (s == LINK_NO_DATA) {
        return BRIGHTNESS_NO_DATA;
    }
    return ms_since_change >= IDLE_DIM_AFTER_MS ? BRIGHTNESS_IDLE : BRIGHTNESS_LIVE;
}
