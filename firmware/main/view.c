#include "view.h"

#include <stdio.h>
#include <string.h>

#include "fonts.h"
#include "logic/fmt.h"
#include "logic/palette.h"

/*
 * Layout, in pixels. The panel is 480 x 480 with rounded corners.
 *
 * The four tiles read as one rounded square split by a black cross: each
 * tile's outer corner (the one nearest a screen corner) follows the panel's
 * corner, concentric with it; its three inner corners are small. MARGIN
 * leaves room for the burn-in orbit (up to 10 px).
 */
#define W 480
#define MARGIN 14
/* The strip sits low and inset so its corners clear the panel's curve at
 * every burn-in offset (the preview checks it). */
#define STRIP_Y 13
#define GRID_Y 56
#define GAP 8
#define TILE_W ((W - 2 * MARGIN - GAP) / 2)          /* 222 */
#define TILE_H ((W - GRID_Y - MARGIN - GAP) / 2)     /* 201 */
#define TILE_PAD 16                                  /* labels and units */
#define TEXT_W (TILE_W - 2 * TILE_PAD)               /* 190 */
#define VALUE_PAD 12                                 /* values: 9 x 21.6 px fit */
#define VALUE_W (TILE_W - 2 * VALUE_PAD)             /* 198 */
#define VALUE_BASELINE 142 /* tile-local y; every tier sits on it */
#define LABEL_Y 8
#define UNIT_BOTTOM (TILE_H - 8)
#define INNER_R 6
/* Concentric with the panel corner (view.h). Tune that on the device. */
#define OUTER_R (VIEW_PANEL_CORNER_R - MARGIN) /* 40 */
#define EDGE_H 3
#define FILL_ANIM_MS 400

static lv_obj_t *s_root;
static lv_obj_t *s_pages; /* the four tiles and the strip */
static lv_obj_t *s_detail;
static lv_obj_t *s_nodata;

/* Strip */
static lv_obj_t *s_title, *s_stale, *s_clock, *s_dot;
/* Tiles */
typedef struct {
    lv_obj_t *box;
    lv_obj_t *fill;
    lv_obj_t *edge;
    lv_obj_t *label;
    lv_obj_t *value;
    lv_obj_t *unit;
    tier_t tier;
    bool is_text;
    int hue;     /* -1 before the first render */
    int fill_px; /* the fill's target height; -1 = hidden */
    /* History chart: column heights (-1 = gap) and its colors. */
    lv_obj_t *chart;
    int16_t cols[HIST_LEN];
    uint32_t chart_fill, chart_line;
} tile_view_t;
static tile_view_t s_tile[TILES_PER_PAGE];
static int s_last_page = -1;
static bool s_animate = true;
/* Detail */
static lv_obj_t *s_d_clock, *s_d_col[2], *s_d_footer;
static lv_obj_t *s_d_chart_value;
/* NO DATA */
static lv_obj_t *s_n_age, *s_n_hint;

static view_cb_t s_on_tap, s_on_long_press, s_on_chart_toggle;

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

static void paint(lv_obj_t *o, uint32_t color, int radius)
{
    lv_obj_set_style_bg_color(o, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, radius, 0);
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

static void set_text_color(lv_obj_t *o, uint32_t color)
{
    lv_obj_set_style_text_color(o, lv_color_hex(color), 0);
}

/* Top-left y for a label in `font` whose baseline must sit at `baseline`. */
static int y_for_baseline(const lv_font_t *font, int baseline)
{
    return baseline - (font->line_height - font->base_line);
}

/* Numbers are right-aligned against one edge, so the last digit keeps its
 * column. Names are left-aligned and cut with "...". */
static void style_value(tile_view_t *t, tier_t tier, bool is_text)
{
    if (t->tier == tier && t->is_text == is_text) {
        return;
    }
    const lv_font_t *font = tier_font(tier);
    lv_obj_set_style_text_font(t->value, font, 0);
    lv_obj_set_pos(t->value, VALUE_PAD, y_for_baseline(font, VALUE_BASELINE));
    lv_obj_set_style_text_align(t->value, is_text ? LV_TEXT_ALIGN_LEFT : LV_TEXT_ALIGN_RIGHT, 0);
    lv_label_set_long_mode(t->value,
                           is_text ? LV_LABEL_LONG_MODE_DOTS : LV_LABEL_LONG_MODE_CLIP);
    lv_obj_set_height(t->value, font->line_height);
    t->tier = tier;
    t->is_text = is_text;
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

/*
 * The big outer corner. LVGL gives one radius per object, so each tile is
 * drawn with small corners and a black quarter-ring is laid over its outer
 * corner: an arc centred where the big corner's centre is, from radius
 * OUTER_R out past the square's corner. It hides the tile outside the curve
 * and paints only black ground elsewhere.
 */
static void corner_mask(lv_obj_t *parent, int cx, int cy, int start_deg)
{
    const int ro = OUTER_R * 1415 / 1000 + 3;
    lv_obj_t *a = lv_arc_create(parent);
    lv_obj_remove_style_all(a);
    lv_obj_set_size(a, 2 * ro, 2 * ro);
    lv_obj_set_pos(a, cx - ro, cy - ro);
    lv_obj_set_clickable(a, false);
    lv_arc_set_bg_angles(a, (lv_value_precise_t)start_deg, (lv_value_precise_t)(start_deg + 90));
    lv_obj_set_style_arc_width(a, ro - OUTER_R, LV_PART_MAIN);
    lv_obj_set_style_arc_color(a, lv_color_hex(COLOR_GROUND), LV_PART_MAIN);
    lv_obj_set_style_arc_opa(a, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(a, false, LV_PART_MAIN);
}

/* How far a column at x (0..TILE_W-1) must stay from the tile's top and
 * bottom edge, so it stays inside the small rounded corners. */
static int corner_inset(int x)
{
    int d = x < INNER_R ? x : (x >= TILE_W - INNER_R ? TILE_W - 1 - x : INNER_R);
    if (d >= INNER_R) {
        return 0;
    }
    int dx = INNER_R - d;
    int dy = 0;
    while ((dy + 1) * (dy + 1) + dx * dx <= INNER_R * INNER_R) {
        dy++;
    }
    return INNER_R - dy;
}

/* The chart: one column per sample, oldest at the left; a filled area in the
 * hue's fill shade with a CHART_LINE_PX line in its line shade on top. A gap
 * column draws nothing. The line is 3 px, not 2: the contrast rule for the
 * digits above it caps how bright it can be, so it gains width instead. */
#define CHART_LINE_PX 3

static void draw_chart(lv_event_t *e)
{
    tile_view_t *t = lv_event_get_user_data(e);
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_area_t a;
    lv_obj_get_coords(lv_event_get_target_obj(e), &a);
    lv_draw_rect_dsc_t fill, line;
    lv_draw_rect_dsc_init(&fill);
    fill.bg_color = lv_color_hex(t->chart_fill);
    lv_draw_rect_dsc_init(&line);
    line.bg_color = lv_color_hex(t->chart_line);
    int prev_top = -1;
    for (int i = 0; i < HIST_LEN; i++) {
        int x0 = i * TILE_W / HIST_LEN, x1 = (i + 1) * TILE_W / HIST_LEN - 1;
        int h = t->cols[i];
        if (h < 0) {
            prev_top = -1;
            continue;
        }
        int inset = corner_inset(x0) > corner_inset(x1) ? corner_inset(x0) : corner_inset(x1);
        int bottom = TILE_H - 1 - inset;
        int top = TILE_H - 1 - h;
        if (top < inset) {
            top = inset;
        }
        if (top > bottom - 1) {
            top = bottom - 1;
        }
        lv_area_t r = {a.x1 + x0, a.y1 + top, a.x1 + x1, a.y1 + bottom};
        lv_draw_rect(layer, &fill, &r);
        /* The line, joined to the previous column's top. */
        int lt = top, lb = top + CHART_LINE_PX - 1;
        if (prev_top >= 0) {
            lt = prev_top < lt ? prev_top : lt;
            lb = prev_top + CHART_LINE_PX - 1 > lb ? prev_top + CHART_LINE_PX - 1 : lb;
        }
        lb = lb > bottom ? bottom : lb;
        lv_area_t l = {a.x1 + x0, a.y1 + lt, a.x1 + x1, a.y1 + lb};
        lv_draw_rect(layer, &line, &l);
        prev_top = top;
    }
}

static void build_tile(int i)
{
    tile_view_t *t = &s_tile[i];
    int x = MARGIN + (i % 2) * (TILE_W + GAP);
    int y = GRID_Y + (i / 2) * (TILE_H + GAP);
    t->box = box(s_pages, x, y, TILE_W, TILE_H);
    paint(t->box, hue_colors(HUE_MOSS)->base, INNER_R);

    t->fill = box(t->box, 0, 0, TILE_W, 0);
    paint(t->fill, hue_colors(HUE_MOSS)->fill, INNER_R);
    lv_obj_align(t->fill, LV_ALIGN_BOTTOM_LEFT, 0, 0);
    lv_obj_set_hidden(t->fill, true);
    t->fill_px = -1;

    t->chart = box(t->box, 0, 0, TILE_W, TILE_H);
    lv_obj_add_event_cb(t->chart, draw_chart, LV_EVENT_DRAW_MAIN, t);
    lv_obj_set_hidden(t->chart, true);
    for (int k = 0; k < HIST_LEN; k++) {
        t->cols[k] = -1;
    }

    t->edge = box(t->box, INNER_R, 0, TILE_W - 2 * INNER_R, EDGE_H);
    paint(t->edge, 0, 0);
    lv_obj_set_hidden(t->edge, true);

    t->label = label(t->box, &brl_30, 0, TILE_PAD, LABEL_Y, TEXT_W, LV_TEXT_ALIGN_LEFT);
    t->value = label(t->box, &jbm_num_110, COLOR_VALUE, VALUE_PAD, 0, VALUE_W,
                     LV_TEXT_ALIGN_RIGHT);
    t->tier = TIER_TEXT;
    t->is_text = true;
    style_value(t, TIER_XL, false);
    t->unit = label(t->box, &brl_30, 0, TILE_PAD, UNIT_BOTTOM - brl_30.line_height, TEXT_W,
                    LV_TEXT_ALIGN_RIGHT);
    t->hue = -1;
}

static void build_pages(void)
{
    s_pages = box(s_root, 0, 0, W, W);
    for (int i = 0; i < TILES_PER_PAGE; i++) {
        build_tile(i);
    }
    /* Outer corners: top-left 180-270 deg, top-right 270-360, bottom-left
     * 90-180, bottom-right 0-90 (LVGL: 0 deg is 3 o'clock, clockwise). */
    const int l = MARGIN, r = MARGIN + 2 * TILE_W + GAP;
    const int top = GRID_Y, bottom = GRID_Y + 2 * TILE_H + GAP;
    corner_mask(s_pages, l + OUTER_R, top + OUTER_R, 180);
    corner_mask(s_pages, r - OUTER_R, top + OUTER_R, 270);
    corner_mask(s_pages, l + OUTER_R, bottom - OUTER_R, 90);
    corner_mask(s_pages, r - OUTER_R, bottom - OUTER_R, 0);

    /* Strip, drawn last so no mask can cover it: title left; stale age,
     * clock and link dot right. */
    s_title = label(s_pages, &brl_32, COLOR_TITLE, MARGIN + TILE_PAD, STRIP_Y + 6, 200,
                    LV_TEXT_ALIGN_LEFT);
    const int dot = 14;
    const int dot_x = W - MARGIN - TILE_PAD - dot; /* in line with the tile text */
    s_dot = box(s_pages, dot_x, STRIP_Y + 18, dot, dot);
    paint(s_dot, COLOR_DOT_LIVE, LV_RADIUS_CIRCLE);
    s_clock = label(s_pages, &brl_30, COLOR_CLOCK, dot_x - 12 - 80, STRIP_Y + 6, 80,
                    LV_TEXT_ALIGN_RIGHT);
    s_stale = label(s_pages, &brl_30, COLOR_STALE, dot_x - 12 - 80 - 140, STRIP_Y + 6, 130,
                    LV_TEXT_ALIGN_RIGHT);
}

static void on_chart_row(lv_event_t *e)
{
    (void)e;
    if (s_on_chart_toggle) {
        s_on_chart_toggle();
    }
}

static void build_detail(void)
{
    s_detail = box(s_root, 0, 0, W, W);
    const int x = 28, inner = W - 2 * 28;
    lv_obj_t *title =
        label(s_detail, &brl_32, COLOR_TITLE, x, 20, inner - 90, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(title, "All temperatures");
    s_d_clock = label(s_detail, &brl_30, COLOR_CLOCK, W - x - 90, 22, 90, LV_TEXT_ALIGN_RIGHT);
    for (int i = 0; i < 2; i++) {
        s_d_col[i] = label(s_detail, &brl_30, COLOR_TEXT, x + i * (inner / 2), 76,
                           inner / 2 - 8, LV_TEXT_ALIGN_LEFT);
        lv_obj_set_style_text_line_space(s_d_col[i], 4, 0);
    }
    /* The chart style: a tap here switches it (and stays on this page). */
    lv_obj_t *row = box(s_detail, x, 300, inner, 52);
    lv_obj_set_style_border_color(row, lv_color_hex(COLOR_TEXT_MUTED), 0);
    lv_obj_set_style_border_width(row, 2, 0);
    lv_obj_set_style_radius(row, 12, 0);
    lv_obj_set_clickable(row, true);
    lv_obj_add_event_cb(row, on_chart_row, LV_EVENT_SHORT_CLICKED, NULL);
    lv_obj_t *name = label(row, &brl_30, COLOR_TEXT_MUTED, 16, 7, 120, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(name, "Charts");
    s_d_chart_value =
        label(row, &brl_30, COLOR_TEXT, 120, 7, inner - 136, LV_TEXT_ALIGN_RIGHT);
    for (int i = 0; i < 2; i++) {
        lv_obj_set_height(s_d_col[i], 300 - 76 - 8); /* never under the row */
    }

    /* Three lines of Barlow 24, ending above the bottom margin. */
    s_d_footer = label(s_detail, &brl_24, COLOR_TEXT_MUTED, x, 362, inner, LV_TEXT_ALIGN_LEFT);
    lv_obj_set_height(s_d_footer, 3 * (brl_24.line_height + 2));
    lv_label_set_long_mode(s_d_footer, LV_LABEL_LONG_MODE_WRAP);
}

/* NO DATA can stay up for hours: black, no fills, text only. */
static void build_nodata(void)
{
    s_nodata = box(s_root, 0, 0, W, W);
    lv_obj_t *head =
        label(s_nodata, &brl_44, COLOR_NO_DATA_HEAD, 0, 120, W, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(head, "No data from the DGX");
    s_n_age = label(s_nodata, &jbm_num_82, COLOR_VALUE, 0, 196, W, LV_TEXT_ALIGN_CENTER);
    s_n_hint = label(s_nodata, &brl_30, COLOR_TEXT, 0, 318, W, LV_TEXT_ALIGN_CENTER);
}

void view_init(lv_obj_t *screen, view_cb_t on_tap, view_cb_t on_long_press,
               view_cb_t on_chart_toggle)
{
    s_on_tap = on_tap;
    s_on_long_press = on_long_press;
    s_on_chart_toggle = on_chart_toggle;
    lv_obj_set_style_bg_color(screen, lv_color_hex(COLOR_GROUND), 0);
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

void view_set_animations(bool on)
{
    s_animate = on;
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

static void fill_height_cb(void *obj, int32_t h)
{
    lv_obj_set_height((lv_obj_t *)obj, h);
}

/* Move the level fill to `px`. Animated (ease out) when the same tile on the
 * same page changes value; set at once on a page change. */
static void set_fill(tile_view_t *t, int px, bool animate)
{
    if (px == t->fill_px) {
        return;
    }
    lv_anim_delete(t->fill, fill_height_cb);
    if (px < 0) {
        lv_obj_set_hidden(t->fill, true);
    } else if (animate && t->fill_px >= 0) {
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, t->fill);
        lv_anim_set_exec_cb(&a, fill_height_cb);
        lv_anim_set_values(&a, lv_obj_get_height(t->fill), px);
        lv_anim_set_duration(&a, FILL_ANIM_MS);
        lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
        lv_anim_start(&a);
    } else {
        lv_obj_set_height(t->fill, px);
        lv_obj_set_hidden(t->fill, false);
    }
    t->fill_px = px;
}

/* Show the chart for this tile's metric, or hide it. Invalidates only the
 * tile, and only when a column changed. */
static bool render_chart(tile_view_t *t, const tile_t *m, const view_model_t *vm)
{
    static int16_t cols[HIST_LEN];
    bool on = vm->style == CHART_HISTORY && vm->hist_heights != NULL &&
              m->hist != HIST_NONE && vm->hist_heights(m->hist, vm->frame, TILE_H, cols);
    lv_obj_set_hidden(t->chart, !on);
    if (!on) {
        return false;
    }
    const hue_colors_t *c = hue_colors(m->hue);
    if (memcmp(cols, t->cols, sizeof(cols)) != 0 || c->fill != t->chart_fill ||
        c->line != t->chart_line) {
        memcpy(t->cols, cols, sizeof(cols));
        t->chart_fill = c->fill;
        t->chart_line = c->line;
        lv_obj_invalidate(t->chart);
    }
    return true;
}

static void render_tile(tile_view_t *t, const tile_t *m, bool same_page,
                        const view_model_t *vm)
{
    if (t->hue != (int)m->hue) {
        const hue_colors_t *c = hue_colors(m->hue);
        paint(t->box, c->base, INNER_R);
        lv_obj_set_style_bg_color(t->fill, lv_color_hex(c->fill), 0);
        set_text_color(t->label, c->tint);
        set_text_color(t->unit, c->tint);
        lv_obj_set_hidden(t->edge, c->edge == 0);
        if (c->edge) {
            lv_obj_set_style_bg_color(t->edge, lv_color_hex(c->edge), 0);
        }
        t->hue = (int)m->hue;
    }
    style_value(t, m->tier, m->is_text);
    set_text(t->label, m->label ? m->label : "");
    set_text(t->value, m->value);
    set_text(t->unit, m->unit);
    bool chart = render_chart(t, m, vm);
    int px = chart || m->fill < 0 ? -1 : (int)(m->fill * (float)TILE_H + 0.5f);
    set_fill(t, px, s_animate && same_page);
}

static void render_pages(const view_model_t *m)
{
    set_text(s_title, page_name(m->page));
    set_text(s_clock, m->clock);
    if (m->link == LINK_LIVE) {
        set_text(s_stale, "");
        lv_obj_set_style_bg_color(s_dot, lv_color_hex(COLOR_DOT_LIVE), 0);
    } else {
        char line[40];
        snprintf(line, sizeof(line), "Stale %s", m->age);
        set_text(s_stale, line);
        lv_obj_set_style_bg_color(s_dot, lv_color_hex(COLOR_STALE), 0);
    }
    bool same_page = s_last_page == (int)m->page;
    for (int i = 0; i < TILES_PER_PAGE; i++) {
        render_tile(&s_tile[i], &m->tiles[i], same_page, m);
    }
    s_last_page = (int)m->page;
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
             "%s, fw %s\nFrames %lu, rejected %lu\nStack free: rx %lu B, ui %lu B", f->host,
             m->fw_version ? m->fw_version : "?", (unsigned long)m->accepted,
             (unsigned long)m->rejected, (unsigned long)m->stack_free_rx,
             (unsigned long)m->stack_free_ui);
    set_text(s_d_footer, line);
    set_text(s_d_chart_value, chart_style_name(m->style));
    s_last_page = -1;
}

static void render_nodata(const view_model_t *m)
{
    bool never = strcmp(m->age, "never") == 0;
    set_text(s_n_age, never ? "" : m->age);
    set_text(s_n_hint, never ? "No frame since boot.\nCheck that dgx-status is running."
                             : "Check that dgx-status is running.");
    lv_label_set_long_mode(s_n_hint, LV_LABEL_LONG_MODE_WRAP);
    s_last_page = -1;
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
    return s_tile[i].value;
}
