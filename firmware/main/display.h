/* Panel, touch and LVGL bring-up for the CO5300 AMOLED. */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "lvgl.h"

/* Power the panel, start QSPI, the touch controller and the LVGL task. */
esp_err_t display_init(void);

/* Panel brightness, 0-255 (register 0x51). Call with the LVGL lock held. */
void display_set_brightness(uint8_t level);
