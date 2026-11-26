/*
 * User settings that survive a reboot, behind a small store interface so the
 * logic is host-tested. The firmware's store is NVS (settings_nvs.c).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    CHART_LEVEL = 0,   /* the level fill: the default */
    CHART_HISTORY = 1, /* a 2-minute chart behind the value */
} chart_style_t;

typedef struct {
    /* Read the stored byte; false when there is none or the read fails. */
    bool (*load)(void *ctx, uint8_t *out);
    /* Store the byte; false on failure. */
    bool (*save)(void *ctx, uint8_t value);
    void *ctx;
} settings_store_t;

/* The stored style, or CHART_LEVEL when nothing valid is stored. */
chart_style_t settings_chart_style(const settings_store_t *s);
/* Switch to the other style and store it. Returns the new style; it applies
 * even when the save fails (it then lasts until reboot). */
chart_style_t settings_toggle_chart_style(const settings_store_t *s, chart_style_t current);
const char *chart_style_name(chart_style_t c);

/* Screen rotation, in clockwise quarter turns as the viewer sees it. */
typedef enum {
    TURN_0 = 0,
    TURN_90 = 1,
    TURN_180 = 2,
    TURN_270 = 3,
} turn_t;

/* The stored rotation, or TURN_0 when nothing valid is stored. */
turn_t settings_turn(const settings_store_t *s);
/* Turn 90 degrees clockwise and store it. Returns the new rotation; it
 * applies even when the save fails. */
turn_t settings_turn_cw(const settings_store_t *s, turn_t current);
int turn_degrees(turn_t t);
