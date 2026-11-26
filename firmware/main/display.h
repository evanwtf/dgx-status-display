/* Panel, touch and LVGL bring-up for the CO5300 AMOLED. */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "lvgl.h"

/* Power the panel, start QSPI, the touch controller and the LVGL task. */
esp_err_t display_init(void);

/* Panel brightness, 0-255 (register 0x51). Call with the LVGL lock held. */
void display_set_brightness(uint8_t level);

/* Fonts with an em dash (U+2014). Montserrat has no em dash; these fall back
 * to Montserrat for every other glyph. Valid after display_init(). */
extern lv_font_t font_small;  /* Montserrat 20 */
extern lv_font_t font_medium; /* Montserrat 28 */
extern lv_font_t font_large;  /* Montserrat 48 */
