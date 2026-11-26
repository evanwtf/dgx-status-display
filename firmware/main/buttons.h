/*
 * The board's buttons (board.h): BOOT, KEY and PWR. Each press is logged at
 * INFO, and the daemon relays the line. A short KEY press (under
 * BUTTON_SHORT_MS) calls on_key_short; BOOT and PWR have no action yet.
 */
#pragma once

/* Configure the pins and start the poll timer. Call from ui_init() with the
 * LVGL lock held: the timer runs in the LVGL task, where touch also uses the
 * I2C bus, so PMU reads never overlap a touch read. */
void buttons_start(void (*on_key_short)(void));
