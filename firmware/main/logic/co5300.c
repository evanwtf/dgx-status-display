#include "co5300.h"

void co5300_round(px_area_t *a)
{
    a->x1 = (a->x1 >> 1) << 1;
    a->y1 = (a->y1 >> 1) << 1;
    a->x2 = ((a->x2 >> 1) << 1) + 1;
    a->y2 = ((a->y2 >> 1) << 1) + 1;
}

bool co5300_ok(const px_area_t *a)
{
    return (a->x1 & 1) == 0 && (a->y1 & 1) == 0 && (a->x2 & 1) == 1 && (a->y2 & 1) == 1;
}

void port_rotate_area(px_area_t *a, lrot_t r, int32_t n)
{
    const int32_t w = a->x2 - a->x1 + 1;
    const int32_t h = a->y2 - a->y1 + 1;
    const int32_t hres = n, vres = n;
    switch (r) {
    case LROT_0:
        return;
    case LROT_90:
        a->y2 = vres - a->x1 - 1;
        a->x1 = a->y1;
        a->x2 = a->x1 + h - 1;
        a->y1 = a->y2 - w + 1;
        break;
    case LROT_180:
        a->y2 = vres - a->y1 - 1;
        a->y1 = a->y2 - h + 1;
        a->x2 = hres - a->x1 - 1;
        a->x1 = a->x2 - w + 1;
        break;
    case LROT_270:
        a->x1 = hres - a->y2 - 1;
        a->y2 = a->x2;
        a->x2 = a->x1 + h - 1;
        a->y1 = a->y2 - w + 1;
        break;
    }
}

lrot_t lrot_for_cw_turns(int quarter_turns_cw)
{
    static const lrot_t MAP[4] = {LROT_0, LROT_270, LROT_180, LROT_90};
    return MAP[((quarter_turns_cw % 4) + 4) % 4];
}
