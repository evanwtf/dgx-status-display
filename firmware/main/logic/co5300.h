/*
 * Flush-area rules for the CO5300 panel. Pure C, host-tested.
 *
 * The CO5300 takes a window only when x1 and y1 are even and x2 and y2 are
 * odd (Waveshare demo: rounder_event_cb). LVGL rounds the logical area;
 * esp_lvgl_port's software rotation then maps it to panel coordinates.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    int32_t x1, y1, x2, y2; /* inclusive */
} px_area_t;

/* Logical rotations, as LVGL numbers them (lv_display_rotation_t). */
typedef enum {
    LROT_0 = 0,
    LROT_90 = 1,
    LROT_180 = 2,
    LROT_270 = 3,
} lrot_t;

/* Widen to even x1/y1 and odd x2/y2. */
void co5300_round(px_area_t *a);
/* True when the panel accepts the window. */
bool co5300_ok(const px_area_t *a);

/* Map a logical area to panel coordinates on a square panel of side n. A
 * copy of lvgl_port_rotate_area() in esp_lvgl_port 2.9.0
 * (src/lvgl9/esp_lvgl_port_disp.c), so the host can check its result. */
void port_rotate_area(px_area_t *a, lrot_t r, int32_t n);

/* The LVGL rotation that shows the screen turned `quarter_turns_cw` times
 * clockwise (0..3). LVGL's 90 turns the image counter-clockwise on the
 * panel, so a clockwise turn is LVGL's 270. */
lrot_t lrot_for_cw_turns(int quarter_turns_cw);
