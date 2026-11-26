#include "view.h"

#include <stdio.h>
#include <string.h>

#include "fonts.h"
#include "logic/fmt.h"

/* Colors: grays on black, never white. Tiles are outlines, never fills, so
 * no area of the AMOLED stays lit at a high level for hours. */
#define C_BG 0x000000
#define C_VALUE 0xC8C8C8
#define C_LABEL 0x7A7A7A
#define C_UNIT 0x8C8C8C
#define C_BORDER 0x262626
#define C_DOT_OFF 0x303030
#define C_DOT_ON 0x9A9A9A
#define C_LIVE 0x2E9E3A
#define C_AMBER 0xD08A1E
#define C_RED 0xD8443C
#define C_NODATA 0xA84040

/* Geometry, in pixels. The panel is 480 x 480. MARGIN leaves room for the
 * burn-in orbit (up to 10 px) without cutting a tile. */
#define W 480
#define MARGIN 14
#define STRIP_Y 8
#define STRIP_H 40
#define GRID_Y 58
#define GAP 8
#define TILE_W ((W - 2 * MARGIN - GAP) / 2)        /* 222 */
#define TILE_H ((W - GRID_Y - MARGIN - GAP) / 2)   /* 200 */
#define TILE_PAD 12
#define TEXT_W (TILE_W - 2 * TILE_PAD)             /* 198 */
#define VALUE_BASELINE 142 /* tile-local y; every tier sits on it */
#define LABEL_Y 8
#define UNIT_BOTTOM (TILE_H - 8)

static lv_obj_t *s_root;
static lv_obj_t *s_pages;  /* the four tiles and the strip */
static lv_obj_t *s_detail;
static lv_obj_t *s_nodata;

/* Strip */
static lv_obj_t *s_dot, *s_status, *s_clock, *s_page_dot[PAGE_COUNT];
/* Tiles */
static lv_obj_t *s_tile_label[TILES_PER_PAGE];
static lv_obj_t *s_tile_value[TILES_PER_PAGE];
static lv_obj_t *s_tile_unit[TILES_PER_PAGE];
static tier_t s_tile_tier[TILES_PER_PAGE];
static bool s_tile_text[TILES_PER_PAGE];
/* Detail */
static lv_obj_t *s_d_clock, *s_d_col[2], *s_d_footer;
/* NO DATA */
static lv_obj_t *s_n_age, *s_n_caption;

static view_cb_t s_on_tap, s_on_long_press;

static const lv_font_t *tier_font(tier_t t)
{
    switch (t) {
    case TIER_XL: return &jbm_num_110;
    case TIER_L: return &jbm_num_82;
    case TIER_M: return &jbm_mix_66;
    case TIER_TEXT: return &jbm_text_36;
    }
    return &jbm_text_36;
}

static uint32_t tone_color(tone_t t)
{
    switch (t) {
    case TONE_AMBER: return C_AMBER;
    case TONE_RED: return C_RED;
    case TONE_NEUTRAL: break;
    }
    return C_VALUE;
}

static lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_scrollable(o, false);
    lv_obj_set_clickable(o, false);
    return o;
}

static lv_obj_t *dot(lv_obj_t *parent, int x, int y, int d, uint32_t color)
{
    lv_obj_t *o = box(parent, x, y, d, d);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(o, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    return o;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y,
                       int w, lv_text_align_t align)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(l, align, 0);
    lv_obj_set_pos(l, x, y);
    lv_obj_set_width(l, w);
    lv_label_set_long_mode(l, LV_LABEL_LONG_MODE_CLIP);
    lv_label_set_text(l, "");
    return l;
}

static void set_text(lv_obj_t *l, const char *text)
{
    const char *cur = lv_label_get_text(l);
    if (cur == NULL || strcmp(cur, text) != 0) {
        lv_label_set_text(l, text);
    }
}

static void set_color(lv_obj_t *o, uint32_t color, bool text)
{
    if (text) {
        lv_obj_set_style_text_color(o, lv_color_hex(color), 0);
    } else {
        lv_obj_set_style_bg_color(o, lv_color_hex(color), 0);
    }
}

/* Top-left y for a label in `font` whose baseline must sit at `baseline`. */
static int y_for_baseline(const lv_font_t *font, int baseline)
{
    return baseline - (font->line_height - font->base_line);
}

/* Put a value label in its tier's font and alignment. Numbers are right-
 * aligned against the same edge, so the last digit keeps its column. */
static void style_value(int i, tier_t tier, bool is_text)
{
    if (s_tile_tier[i] == tier && s_tile_text[i] == is_text) {
        return;
    }
    lv_obj_t *v = s_tile_value[i];
    const lv_font_t *font = tier_font(tier);
    lv_obj_set_style_text_font(v, font, 0);
    lv_obj_set_pos(v, TILE_PAD, y_for_baseline(font, VALUE_BASELINE));
    lv_obj_set_style_text_align(v, is_text ? LV_TEXT_ALIGN_LEFT : LV_TEXT_ALIGN_RIGHT, 0);
    lv_label_set_long_mode(v, is_text ? LV_LABEL_LONG_MODE_DOTS : LV_LABEL_LONG_MODE_CLIP);
    lv_obj_set_height(v, font->line_height);
    s_tile_tier[i] = tier;
    s_tile_text[i] = is_text;
}

static void on_event(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    /* SHORT_CLICKED, not CLICKED: LVGL sends CLICKED after a long press too. */
    if (code == LV_EVENT_SHORT_CLICKED && s_on_tap) {
        s_on_tap();
    } else if (code == LV_EVENT_LONG_PRESSED && s_on_long_press) {
        s_on_long_press();
    }
}

static void build_pages(void)
{
    s_pages = box(s_root, 0, 0, W, W);

    /* Strip: link dot and state at the left, clock in the middle, page dots
     * at the right. */
    s_dot = dot(s_pages, MARGIN + 4, STRIP_Y + 13, 14, C_LIVE);
    s_status = label(s_pages, &font_medium, C_LABEL, MARGIN + 28, STRIP_Y + 4, 150,
                     LV_TEXT_ALIGN_LEFT);
    s_clock = label(s_pages, &font_medium, C_VALUE, W / 2 - 70, STRIP_Y + 4, 140,
                    LV_TEXT_ALIGN_CENTER);
    for (int i = 0; i < PAGE_COUNT; i++) {
        int x = W - MARGIN - 4 - 10 - (PAGE_COUNT - 1 - i) * 20;
        s_page_dot[i] = dot(s_pages, x, STRIP_Y + 15, 10, C_DOT_OFF);
    }

    for (int i = 0; i < TILES_PER_PAGE; i++) {
        int x = MARGIN + (i % 2) * (TILE_W + GAP);
        int y = GRID_Y + (i / 2) * (TILE_H + GAP);
        lv_obj_t *tile = box(s_pages, x, y, TILE_W, TILE_H);
        lv_obj_t *frame = box(tile, 0, 0, TILE_W, TILE_H);
        lv_obj_set_style_border_color(frame, lv_color_hex(C_BORDER), 0);
        lv_obj_set_style_border_width(frame, 2, 0);
        lv_obj_set_style_radius(frame, 18, 0);

        s_tile_label[i] = label(tile, &font_medium, C_LABEL, TILE_PAD + 2, LABEL_Y, TEXT_W,
                                LV_TEXT_ALIGN_LEFT);
        s_tile_value[i] = label(tile, &jbm_num_110, C_VALUE, TILE_PAD, 0, TEXT_W,
                                LV_TEXT_ALIGN_RIGHT);
        s_tile_tier[i] = TIER_TEXT;
        s_tile_text[i] = true;
        style_value(i, TIER_XL, false);
        s_tile_unit[i] = label(tile, &font_unit, C_UNIT, TILE_PAD, UNIT_BOTTOM -
                               font_unit.line_height, TEXT_W, LV_TEXT_ALIGN_RIGHT);
    }
}

static void build_detail(void)
{
    s_detail = box(s_root, 0, 0, W, W);
    const int inner = W - 2 * 28;
    lv_obj_t *title = label(s_detail, &font_medium, C_VALUE, 28, 24, inner / 2,
                            LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(title, "Temps");
    s_d_clock = label(s_detail, &font_medium, C_VALUE, W / 2, 24, inner / 2,
                      LV_TEXT_ALIGN_RIGHT);
    for (int i = 0; i < 2; i++) {
        s_d_col[i] = label(s_detail, &font_small, C_VALUE, 28 + i * (inner / 2), 72,
                           inner / 2 - 8, LV_TEXT_ALIGN_LEFT);
        lv_obj_set_style_text_line_space(s_d_col[i], 4, 0);
    }
    /* Three lines of Montserrat 20, ending above the bottom margin. */
    s_d_footer = label(s_detail, &font_small, C_LABEL, 28, 372, inner, LV_TEXT_ALIGN_LEFT);
    lv_obj_set_height(s_d_footer, 3 * (font_small.line_height + 2));
    lv_label_set_long_mode(s_d_footer, LV_LABEL_LONG_MODE_WRAP);
}

static void build_nodata(void)
{
    s_nodata = box(s_root, 0, 0, W, W);
    lv_obj_t *head = label(s_nodata, &font_large, C_NODATA, 0, 104, W, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(head, "NO DATA");
    lv_obj_t *sub = label(s_nodata, &font_medium, C_LABEL, 0, 172, W, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(sub, "DGX not sending");
    s_n_caption = label(s_nodata, &font_medium, C_LABEL, 0, 250, W, LV_TEXT_ALIGN_CENTER);
    s_n_age = label(s_nodata, &jbm_num_82, C_VALUE, 0, 290, W, LV_TEXT_ALIGN_CENTER);
}

void view_init(lv_obj_t *screen, view_cb_t on_tap, view_cb_t on_long_press)
{
    fonts_init();
    s_on_tap = on_tap;
    s_on_long_press = on_long_press;
    lv_obj_set_style_bg_color(screen, lv_color_hex(C_BG), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_scrollable(screen, false);

    s_root = box(screen, 0, 0, W, W);
    lv_obj_set_clickable(s_root, true);
    lv_obj_add_event_cb(s_root, on_event, LV_EVENT_SHORT_CLICKED, NULL);
    lv_obj_add_event_cb(s_root, on_event, LV_EVENT_LONG_PRESSED, NULL);

    build_pages();
    build_detail();
    build_nodata();
    lv_obj_set_hidden(s_pages, true);
    lv_obj_set_hidden(s_detail, true);
}

void view_model_fill(view_model_t *m, const status_frame_t *f, link_state_t st,
                     int64_t age_s, const carousel_t *c)
{
    m->kind = carousel_view(c);
    m->page = c->page;
    m->link = st;
    m->frame = f;
    page_tiles(c->page, f, m->tiles);
    if (f->t > 0) {
        fmt_clock_hm(m->clock, sizeof(m->clock), f->t, f->has_tz ? f->tz_s : 0);
    } else {
        m->clock[0] = '\0';
    }
    fmt_age(m->age, sizeof(m->age), age_s);
}

static void render_pages(const view_model_t *m)
{
    char line[48];
    if (m->link == LINK_LIVE) {
        set_text(s_status, "LIVE");
        set_color(s_dot, C_LIVE, false);
        set_color(s_status, C_LABEL, true);
    } else {
        snprintf(line, sizeof(line), "STALE %s", m->age);
        set_text(s_status, line);
        set_color(s_dot, C_AMBER, false);
        set_color(s_status, C_AMBER, true);
    }
    set_text(s_clock, m->clock);
    for (int i = 0; i < PAGE_COUNT; i++) {
        set_color(s_page_dot[i], i == (int)m->page ? C_DOT_ON : C_DOT_OFF, false);
    }
    for (int i = 0; i < TILES_PER_PAGE; i++) {
        const tile_t *t = &m->tiles[i];
        style_value(i, t->tier, t->is_text);
        set_text(s_tile_label[i], t->label ? t->label : "");
        set_text(s_tile_value[i], t->value);
        set_color(s_tile_value[i], tone_color(t->tone), true);
        set_text(s_tile_unit[i], t->unit);
    }
}

static void render_detail(const view_model_t *m)
{
    const status_frame_t *f = m->frame;
    char a[32], col[2][STATUS_MAX_TEMPS / 2 * 32 + 1], line[200];
    set_text(s_d_clock, m->clock);
    const int per_col = (f->n_temps + 1) / 2;
    for (int c = 0; c < 2; c++) {
        size_t n = 0;
        col[c][0] = '\0';
        for (int i = c * per_col; i < f->n_temps && i < (c + 1) * per_col; i++) {
            int w = snprintf(col[c] + n, sizeof(col[c]) - n, "%s%-8s %s", n ? "\n" : "",
                             f->temps[i].label, fmt_temp1(a, sizeof(a), f->temps[i].c));
            if (w < 0 || (size_t)w >= sizeof(col[c]) - n) {
                break;
            }
            n += (size_t)w;
        }
        set_text(s_d_col[c], f->n_temps ? col[c] : (c == 0 ? FMT_DASH : ""));
    }
    snprintf(line, sizeof(line),
             "%s, fw %s\nframes %lu, rejected %lu\nstack free: rx %lu B, ui %lu B", f->host,
             m->fw_version ? m->fw_version : "?", (unsigned long)m->accepted,
             (unsigned long)m->rejected, (unsigned long)m->stack_free_rx,
             (unsigned long)m->stack_free_ui);
    set_text(s_d_footer, line);
}

static void render_nodata(const view_model_t *m)
{
    set_text(s_n_caption, strcmp(m->age, "never") == 0 ? "no frame since boot" : "last frame");
    set_text(s_n_age, strcmp(m->age, "never") == 0 ? "" : m->age);
}

void view_render(const view_model_t *m)
{
    lv_obj_set_hidden(s_pages, m->kind != VIEW_PAGE);
    lv_obj_set_hidden(s_detail, m->kind != VIEW_DETAIL);
    lv_obj_set_hidden(s_nodata, m->kind != VIEW_NO_DATA);
    switch (m->kind) {
    case VIEW_PAGE: render_pages(m); break;
    case VIEW_DETAIL: render_detail(m); break;
    case VIEW_NO_DATA: render_nodata(m); break;
    }
}

void view_set_shift(int dx, int dy)
{
    lv_obj_set_pos(s_root, dx, dy);
}

lv_obj_t *view_tile_value(int i)
{
    return s_tile_value[i];
}
