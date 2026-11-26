/*
 * Debounce and press-and-hold state for one button, read by polling. Pure
 * logic, host-tested. The caller reads the pin and the clock.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* A press released before this is a short press. */
#define BUTTON_SHORT_MS 1000

/* The raw level must stay the same this long before the state changes. */
#define BUTTON_DEBOUNCE_MS 30

typedef enum {
    BUTTON_NONE = 0,
    BUTTON_DOWN, /* the debounced state changed to pressed */
    BUTTON_UP,   /* the debounced state changed to released */
} button_event_t;

typedef struct {
    bool pressed;     /* the debounced state */
    bool raw;         /* the last raw reading */
    bool armed;       /* false while a press that was held at start continues */
    uint32_t raw_ms;  /* when the raw reading last changed */
    uint32_t down_ms; /* when the debounced state became pressed */
} button_t;

/* Start from the current reading. A button held at start gives no events
 * until it is released and pressed again. */
void button_init(button_t *b, bool raw_pressed, uint32_t now_ms);

/* Feed one reading. Returns the debounced event, if any. On BUTTON_UP,
 * *held_ms (when not NULL) is the debounced press length. Clock wrap is
 * safe: all times are differences of uint32_t. */
button_event_t button_update(button_t *b, bool raw_pressed, uint32_t now_ms, uint32_t *held_ms);

/* True for a press of under BUTTON_SHORT_MS (from BUTTON_UP's held_ms). */
bool button_is_short(uint32_t held_ms);
