#include "settings.h"

#include <stddef.h>

chart_style_t settings_chart_style(const settings_store_t *s)
{
    uint8_t v = 0;
    if (s == NULL || s->load == NULL || !s->load(s->ctx, &v)) {
        return CHART_LEVEL;
    }
    return v == CHART_HISTORY ? CHART_HISTORY : CHART_LEVEL;
}

chart_style_t settings_toggle_chart_style(const settings_store_t *s, chart_style_t current)
{
    chart_style_t next = current == CHART_HISTORY ? CHART_LEVEL : CHART_HISTORY;
    if (s != NULL && s->save != NULL) {
        (void)s->save(s->ctx, (uint8_t)next);
    }
    return next;
}

const char *chart_style_name(chart_style_t c)
{
    return c == CHART_HISTORY ? "2-minute history" : "Level";
}

turn_t settings_turn(const settings_store_t *s)
{
    uint8_t v = 0;
    if (s == NULL || s->load == NULL || !s->load(s->ctx, &v) || v > TURN_270) {
        return TURN_0;
    }
    return (turn_t)v;
}

turn_t settings_turn_cw(const settings_store_t *s, turn_t current)
{
    const turn_t next = (turn_t)(((unsigned)current + 1u) % 4u);
    if (s != NULL && s->save != NULL) {
        (void)s->save(s->ctx, (uint8_t)next);
    }
    return next;
}

int turn_degrees(turn_t t)
{
    return 90 * (int)t;
}

view_mode_t settings_mode(const settings_store_t *s)
{
    uint8_t v = 0;
    if (s == NULL || s->load == NULL || !s->load(s->ctx, &v)) {
        return MODE_FAR;
    }
    return v == MODE_NEAR ? MODE_NEAR : MODE_FAR;
}

view_mode_t settings_toggle_mode(const settings_store_t *s, view_mode_t current)
{
    const view_mode_t next = current == MODE_FAR ? MODE_NEAR : MODE_FAR;
    if (s != NULL && s->save != NULL) {
        (void)s->save(s->ctx, (uint8_t)next);
    }
    return next;
}

const char *view_mode_name(view_mode_t m)
{
    return m == MODE_NEAR ? "near" : "far";
}
