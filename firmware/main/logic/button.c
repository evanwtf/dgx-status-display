#include "button.h"

#include <stddef.h>

void button_init(button_t *b, bool raw_pressed, uint32_t now_ms)
{
    b->pressed = raw_pressed;
    b->raw = raw_pressed;
    b->armed = !raw_pressed;
    b->raw_ms = now_ms;
    b->down_ms = now_ms;
}

button_event_t button_update(button_t *b, bool raw_pressed, uint32_t now_ms, uint32_t *held_ms)
{
    if (raw_pressed != b->raw) {
        b->raw = raw_pressed;
        b->raw_ms = now_ms;
        return BUTTON_NONE;
    }
    if (raw_pressed == b->pressed || (uint32_t)(now_ms - b->raw_ms) < BUTTON_DEBOUNCE_MS) {
        return BUTTON_NONE;
    }
    /* The raw level is steady and differs from the debounced state. The
     * change happened at raw_ms, not now. */
    b->pressed = raw_pressed;
    if (!b->armed) {
        /* The end of a press held at start: arm for the next one. */
        b->armed = true;
        return BUTTON_NONE;
    }
    if (raw_pressed) {
        b->down_ms = b->raw_ms;
        return BUTTON_DOWN;
    }
    if (held_ms != NULL) {
        *held_ms = (uint32_t)(b->raw_ms - b->down_ms);
    }
    return BUTTON_UP;
}
