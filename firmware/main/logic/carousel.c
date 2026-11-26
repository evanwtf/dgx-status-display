#include "carousel.h"

void carousel_init(carousel_t *c, int64_t now_ms)
{
    c->page = PAGE_GPU;
    c->detail = false;
    c->no_data = true;
    c->page_since_ms = now_ms;
    c->detail_since_ms = now_ms;
    c->hidden = 0;
}

static bool is_hidden(const carousel_t *c, page_id_t p)
{
    return (c->hidden >> (unsigned)p) & 1u;
}

static void next_page(carousel_t *c, int64_t now_ms)
{
    int p = (int)c->page;
    for (int i = 0; i < PAGE_COUNT; i++) {
        p = (p + 1) % PAGE_COUNT;
        if (!is_hidden(c, (page_id_t)p)) {
            break;
        }
    }
    c->page = (page_id_t)p;
    c->page_since_ms = now_ms;
}

void carousel_set_hidden(carousel_t *c, page_id_t p, bool hidden, int64_t now_ms)
{
    if (p == PAGE_GPU || (unsigned)p >= PAGE_COUNT) {
        return; /* at least one page always shows */
    }
    const uint32_t bit = 1u << (unsigned)p;
    c->hidden = hidden ? (c->hidden | bit) : (c->hidden & ~bit);
    if (hidden && c->page == p) {
        next_page(c, now_ms);
    }
}

void carousel_tick(carousel_t *c, link_state_t st, int64_t now_ms)
{
    if (st == LINK_NO_DATA) {
        c->no_data = true;
        c->detail = false;
        return;
    }
    if (c->no_data) {
        c->no_data = false;
        c->page_since_ms = now_ms;
        return;
    }
    if (c->detail) {
        if (now_ms - c->detail_since_ms >= CAROUSEL_DETAIL_MS) {
            c->detail = false;
            c->page_since_ms = now_ms;
        }
        return;
    }
    if (now_ms - c->page_since_ms >= CAROUSEL_PAGE_MS) {
        next_page(c, now_ms);
    }
}

void carousel_tap(carousel_t *c, int64_t now_ms)
{
    if (c->no_data) {
        return;
    }
    if (c->detail) {
        c->detail = false;
        c->page_since_ms = now_ms;
        return;
    }
    next_page(c, now_ms);
}

void carousel_long_press(carousel_t *c, int64_t now_ms)
{
    if (c->no_data) {
        return;
    }
    c->detail = true;
    c->detail_since_ms = now_ms;
}

view_kind_t carousel_view(const carousel_t *c)
{
    if (c->no_data) {
        return VIEW_NO_DATA;
    }
    return c->detail ? VIEW_DETAIL : VIEW_PAGE;
}
