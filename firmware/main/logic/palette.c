#include "palette.h"

/* Bases sit near 0.02 relative luminance, fills near 0.05 and lines near
 * 0.086, so each step reads as brighter while warm-white digits keep 7:1. */
static const hue_colors_t HUES[HUE_COUNT] = {
    [HUE_TEAL] = {0x0B302E, 0x114743, 0x0F5D58, 0xBFEDE8, 0},
    [HUE_INDIGO] = {0x1D2552, 0x2C3A7D, 0x3247B6, 0xC9D0FF, 0},
    [HUE_VIOLET] = {0x2E1A4A, 0x4E2D7E, 0x6832B7, 0xDCC8FF, 0},
    [HUE_BRONZE] = {0x30260F, 0x4D3D1D, 0x684F1E, 0xF0D9A8, 0},
    [HUE_COOL] = {0x132B4D, 0x1E406D, 0x1D5299, 0xD6E4FF, 0},
    [HUE_WARM] = {0x412706, 0x5A3809, 0x794702, 0xFFE0B0, 0xFFB547},
    [HUE_HOT] = {0x5E1010, 0x7C1919, 0xA61212, 0xFFD3CC, 0xFF5E4D},
};

const hue_colors_t *hue_colors(hue_t h)
{
    return &HUES[(unsigned)h < HUE_COUNT ? h : HUE_BRONZE];
}
