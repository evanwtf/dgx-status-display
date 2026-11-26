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
