/*
 * Overview and detail pages, the NO DATA screen, and burn-in care.
 * Call ui_init() with the LVGL lock held. It starts an LVGL timer that
 * redraws from serial_rx state four times a second.
 */
#pragma once

void ui_init(void);
