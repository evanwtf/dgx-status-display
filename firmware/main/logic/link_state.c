#include "link_state.h"

void link_init(link_t *l)
{
    l->ever = false;
    l->last_ok_ms = 0;
}

void link_frame_ok(link_t *l, int64_t now_ms)
{
    l->ever = true;
    l->last_ok_ms = now_ms;
}

link_state_t link_state(const link_t *l, int64_t now_ms)
{
    if (!l->ever) {
        return LINK_NO_DATA;
    }
    int64_t age = now_ms - l->last_ok_ms;
    if (age < 0) {
        age = 0; /* clock read out of order: treat as fresh */
    }
    if (age <= LINK_LIVE_MAX_MS) {
        return LINK_LIVE;
    }
    if (age <= LINK_STALE_MAX_MS) {
        return LINK_STALE;
    }
    return LINK_NO_DATA;
}

int64_t link_age_s(const link_t *l, int64_t now_ms)
{
    if (!l->ever) {
        return -1;
    }
    int64_t age = now_ms - l->last_ok_ms;
    return age < 0 ? 0 : age / 1000;
}

const char *link_state_name(link_state_t s)
{
    switch (s) {
    case LINK_LIVE: return "LIVE";
    case LINK_STALE: return "STALE";
    case LINK_NO_DATA: return "NO DATA";
    }
    return "?";
}
