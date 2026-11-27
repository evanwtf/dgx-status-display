#include "transition.h"

#include "burnin.h"

void transition_init(transition_t *t, const carousel_t *c)
{
    *t = (transition_t){
        .phase = TR_IDLE,
        .shown_kind = carousel_view(c),
        .shown_page = c->page,
    };
}

/* The exact rise needs 255.5 * T^6 < 2^64 (the target, plus half the
 * denominator for the rounding), so T <= 645 ms. 640 keeps a margin. A
 * longer `total` is scaled down to that range first, for both curves. */
#define FADE_EXACT_MAX_MS 640u

uint8_t fade_level(uint8_t target, uint32_t elapsed, uint32_t total, bool rising)
{
    if (total == 0 || elapsed >= total) {
        return rising ? target : 0;
    }
    uint64_t e = elapsed;
    uint64_t t = total;
    while (t > FADE_EXACT_MAX_MS) {
        e >>= 1;
        t >>= 1;
    }
    if (!rising) {
        /* p = lit fraction in ms of `t`; level = target * (p/t)^2, rounded
         * half up, in integers. */
        const uint64_t p = t - e;
        const uint64_t den = t * t;
        return (uint8_t)(((uint64_t)target * p * p + den / 2) / den);
    }
    /* x = e/t, s = x^2 (3 - 2x) = e^2 (3t - 2e) / t^3, level = target * s^2,
     * rounded half up. With n = e^2 (3t - 2e) and d = t^3: n <= d, and
     * target * n^2 + d^2/2 < 2^64 while t <= FADE_EXACT_MAX_MS. */
    const uint64_t n = e * e * (3 * t - 2 * e);
    const uint64_t d = t * t * t;
    const uint64_t d2 = d * d;
    return (uint8_t)(((uint64_t)target * n * n + d2 / 2) / d2);
}

bool transition_busy(const transition_t *t)
{
    return t->phase != TR_IDLE;
}

static void queue(transition_t *t, tr_act_t a)
{
    if (t->queued == TR_ACT_NONE && !t->mode) {
        t->queued = a;
    }
}

static void run(carousel_t *c, tr_act_t a, int64_t now_ms)
{
    if (a == TR_ACT_TAP) {
        carousel_tap(c, now_ms);
    } else if (a == TR_ACT_LONG_PRESS) {
        carousel_long_press(c, now_ms);
    }
}

void transition_tap(transition_t *t, carousel_t *c, int64_t now_ms)
{
    if (transition_busy(t)) {
        queue(t, TR_ACT_TAP);
    } else {
        run(c, TR_ACT_TAP, now_ms);
    }
}

void transition_long_press(transition_t *t, carousel_t *c, int64_t now_ms)
{
    if (transition_busy(t)) {
        queue(t, TR_ACT_LONG_PRESS);
    } else {
        run(c, TR_ACT_LONG_PRESS, now_ms);
    }
}

void transition_turn(transition_t *t)
{
    t->turn = true;
}

void transition_mode(transition_t *t)
{
    t->turn = true;
    t->mode = true;
    t->queued = TR_ACT_NONE;
}

static bool differs(const transition_t *t, const carousel_t *c)
{
    const view_kind_t k = carousel_view(c);
    return k != t->shown_kind || (k != VIEW_NO_DATA && c->page != t->shown_page);
}

static void show_now(transition_t *t, const carousel_t *c)
{
    t->shown_kind = carousel_view(c);
    t->shown_page = c->page;
}

tr_out_t transition_tick(transition_t *t, carousel_t *c, uint8_t target, int64_t now_ms)
{
    const uint32_t now = (uint32_t)now_ms;
    tr_out_t out = {.level = target};

    /* NO DATA in or out: at once, no fade. A fade in progress ends here. */
    const view_kind_t want = carousel_view(c);
    if ((want == VIEW_NO_DATA) != (t->shown_kind == VIEW_NO_DATA)) {
        show_now(t, c);
        t->phase = TR_IDLE;
        t->queued = TR_ACT_NONE;
        t->turn = false;
        t->mode = false;
        out.swap = true;
        out.render = true;
        return out;
    }
    if (want == VIEW_NO_DATA) {
        if (t->turn) { /* turn at once: there is nothing to hide */
            t->turn = false;
            t->mode = false;
            out.swap = true;
        }
        out.render = true;
        return out;
    }

    if (t->phase == TR_IDLE) {
        if (!differs(t, c) && !t->turn) {
            out.render = true;
            return out;
        }
        t->phase = TR_OUT;
        t->since_ms = now;
        t->out_start_ms = now;
        out.started = true;
    }
    const uint32_t e = now - t->since_ms; /* wraps cleanly */
    switch (t->phase) {
    case TR_OUT:
        if (e < FADE_OUT_MS) {
            out.level = fade_level(target, e, FADE_OUT_MS, false);
            return out;
        }
        t->phase = TR_DARK;
        /* fall through */
    case TR_DARK:
        show_now(t, c); /* hidden pages were skipped by the carousel already */
        out.level = 0;
        out.swap = true;
        out.render = true;
        return out;
    case TR_IN:
        if (e < FADE_IN_MS) {
            out.level = fade_level(target, e, FADE_IN_MS, true);
            return out;
        }
        t->phase = TR_IDLE;
        t->last_ms = now - t->out_start_ms;
        if (t->mode && !t->turn) { /* the new mode stays on its first page */
            t->mode = false;
            t->queued = TR_ACT_NONE;
        }
        out.done = true;
        out.render = true; /* the newest data, held back during the fade */
        /* The dwell (or the detail timeout) starts now. */
        c->page_since_ms = now_ms;
        if (c->detail) {
            c->detail_since_ms = now_ms;
        }
        {
            const tr_act_t a = t->queued;
            t->queued = TR_ACT_NONE;
            run(c, a, now_ms); /* the next step starts its fade */
        }
        return out;
    case TR_IDLE:
        break;
    }
    return out;
}

void transition_swapped(transition_t *t, uint32_t now_ms)
{
    t->turn = false;
    t->phase = TR_IN;
    t->since_ms = now_ms;
}

carousel_t transition_shown(const transition_t *t, const carousel_t *c)
{
    carousel_t s = *c;
    s.page = t->shown_page;
    s.detail = t->shown_kind == VIEW_DETAIL;
    s.no_data = t->shown_kind == VIEW_NO_DATA;
    return s;
}
