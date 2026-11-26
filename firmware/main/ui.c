#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "display.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "logic/burnin.h"
#include "logic/fmt.h"
#include "logic/link_state.h"
#include "serial_rx.h"

/* Colors: grays, never white, on black. Nothing on screen is a filled block,
 * so no area of the AMOLED stays lit at high level for hours. */
#define C_BG 0x000000
#define C_VALUE 0xB4B4B4
#define C_CAPTION 0x646464
#define C_LIVE 0x2E7D32
#define C_STALE 0xB07818
#define C_NODATA 0x9A3A3A

#define MARGIN 28
#define W 480
#define INNER (W - 2 * MARGIN)
#define UI_PERIOD_MS 250

typedef enum { PAGE_OVERVIEW = 0, PAGE_DETAIL } page_t;

static lv_obj_t *s_root;
static lv_obj_t *s_overview;
static lv_obj_t *s_detail;
static lv_obj_t *s_nodata;

/* Overview */
static lv_obj_t *s_host, *s_clock, *s_status;
static lv_obj_t *s_gpu_pct, *s_gpu_w, *s_gpu_c;
static lv_obj_t *s_occ, *s_mem, *s_cpu, *s_load, *s_fans, *s_temps, *s_up;
/* Detail */
static lv_obj_t *s_d_clock, *s_d_col[2], *s_d_footer;
/* NO DATA */
static lv_obj_t *s_n_age, *s_n_host;

static page_t s_page = PAGE_OVERVIEW;
static bool s_page_dirty = true;
static int s_last_state = -1;
static shift_t s_last_shift = {127, 127};
static rx_snapshot_t s_rx; /* static: too big for the LVGL task stack */

static lv_obj_t *box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_size(o, W, W);
    lv_obj_set_scrollable(o, false);
    lv_obj_set_clickable(o, false);
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

/* Redraw only when the text changes. */
static void set_text(lv_obj_t *l, const char *text)
{
    const char *cur = lv_label_get_text(l);
    if (cur == NULL || strcmp(cur, text) != 0) {
        lv_label_set_text(l, text);
    }
}

static void set_color(lv_obj_t *l, uint32_t color)
{
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
}

static void on_tap(lv_event_t *e)
{
    (void)e;
    s_page = s_page == PAGE_OVERVIEW ? PAGE_DETAIL : PAGE_OVERVIEW;
    s_page_dirty = true;
}

static void build_overview(void)
{
    s_overview = box(s_root);
    s_host = label(s_overview, &font_medium, C_VALUE, MARGIN, 24, INNER / 2, LV_TEXT_ALIGN_LEFT);
    s_clock = label(s_overview, &font_medium, C_VALUE, W / 2, 24, INNER / 2, LV_TEXT_ALIGN_RIGHT);
    s_status = label(s_overview, &font_small, C_LIVE, MARGIN, 62, INNER, LV_TEXT_ALIGN_LEFT);

    /* GPU: three big readings with captions. */
    const int col = INNER / 3;
    s_gpu_pct = label(s_overview, &font_large, C_VALUE, MARGIN, 104, col, LV_TEXT_ALIGN_CENTER);
    s_gpu_w = label(s_overview, &font_large, C_VALUE, MARGIN + col, 104, col, LV_TEXT_ALIGN_CENTER);
    s_gpu_c = label(s_overview, &font_large, C_VALUE, MARGIN + 2 * col, 104, col,
                    LV_TEXT_ALIGN_CENTER);
    lv_obj_t *cap;
    cap = label(s_overview, &font_small, C_CAPTION, MARGIN, 160, col, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(cap, "GPU util");
    cap = label(s_overview, &font_small, C_CAPTION, MARGIN + col, 160, col, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(cap, "GPU power");
    cap = label(s_overview, &font_small, C_CAPTION, MARGIN + 2 * col, 160, col,
                LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(cap, "GPU temp");

    s_occ = label(s_overview, &font_medium, C_VALUE, MARGIN, 200, INNER, LV_TEXT_ALIGN_LEFT);

    /* Rows: caption at the left margin, value after it. */
    static const char *const captions[] = {"MEM", "CPU", "LOAD", "FANS", "TEMP"};
    lv_obj_t **values[] = {&s_mem, &s_cpu, &s_load, &s_fans, &s_temps};
    for (int i = 0; i < 5; i++) {
        int y = 248 + i * 36;
        cap = label(s_overview, &font_small, C_CAPTION, MARGIN, y + 4, 80, LV_TEXT_ALIGN_LEFT);
        lv_label_set_text(cap, captions[i]);
        const lv_font_t *font = i == 4 ? &font_small : &font_medium;
        *values[i] = label(s_overview, font, C_VALUE, MARGIN + 80, i == 4 ? y + 4 : y, INNER - 80,
                           LV_TEXT_ALIGN_LEFT);
    }
    s_up = label(s_overview, &font_small, C_CAPTION, MARGIN, 430, INNER, LV_TEXT_ALIGN_LEFT);
}

static void build_detail(void)
{
    s_detail = box(s_root);
    lv_obj_t *title =
        label(s_detail, &font_medium, C_VALUE, MARGIN, 24, INNER / 2, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(title, "Temps");
    s_d_clock = label(s_detail, &font_medium, C_VALUE, W / 2, 24, INNER / 2, LV_TEXT_ALIGN_RIGHT);
    for (int i = 0; i < 2; i++) {
        s_d_col[i] = label(s_detail, &font_small, C_VALUE, MARGIN + i * (INNER / 2), 72,
                           INNER / 2 - 8, LV_TEXT_ALIGN_LEFT);
        lv_obj_set_style_text_line_space(s_d_col[i], 4, 0);
    }
    s_d_footer = label(s_detail, &font_small, C_CAPTION, MARGIN, 404, INNER, LV_TEXT_ALIGN_LEFT);
    lv_obj_set_height(s_d_footer, 56);
    lv_label_set_long_mode(s_d_footer, LV_LABEL_LONG_MODE_WRAP);
}

static void build_nodata(void)
{
    s_nodata = box(s_root);
    lv_obj_t *head =
        label(s_nodata, &font_medium, C_NODATA, MARGIN, 190, INNER, LV_TEXT_ALIGN_CENTER);
    lv_label_set_text(head, "DGX not sending");
    s_n_age = label(s_nodata, &font_small, C_CAPTION, MARGIN, 236, INNER, LV_TEXT_ALIGN_CENTER);
    s_n_host = label(s_nodata, &font_small, C_CAPTION, MARGIN, 266, INNER, LV_TEXT_ALIGN_CENTER);
}

/* ---- Update ---- */

static void update_overview(const status_frame_t *f, link_state_t st, int64_t age_s)
{
    char a[64], b[64], c[64], line[160];
    set_text(s_host, f->host);
    set_text(s_clock, fmt_clock(a, sizeof(a), f->t, f->has_tz ? f->tz_s : 0));
    if (st == LINK_LIVE) {
        set_text(s_status, "LIVE");
        set_color(s_status, C_LIVE);
    } else {
        snprintf(line, sizeof(line), "STALE %s", fmt_age(a, sizeof(a), age_s));
        set_text(s_status, line);
        set_color(s_status, C_STALE);
    }
    set_text(s_gpu_pct, fmt_pct(a, sizeof(a), f->gpu_pct, 0));
    set_text(s_gpu_w, fmt_int_unit(a, sizeof(a), f->gpu_w, "W"));
    set_text(s_gpu_c, fmt_temp0(a, sizeof(a), f->gpu_c));

    snprintf(line, sizeof(line), "On GPU: %s", f->occ[0] ? f->occ : FMT_DASH);
    set_text(s_occ, line);
    set_text(s_mem, fmt_mem(a, sizeof(a), f->mem_used_gib, f->mem_total_gib));
    set_text(s_cpu, fmt_pct(a, sizeof(a), f->cpu_pct, 1));
    set_text(s_load, fmt_load(a, sizeof(a), f->load1, f->load5, f->load15));
    snprintf(line, sizeof(line), "%s / %s", fmt_int_unit(a, sizeof(a), f->fan0_rpm, "rpm"),
             fmt_int_unit(b, sizeof(b), f->fan1_rpm, "rpm"));
    set_text(s_fans, line);
    char d[32];
    snprintf(line, sizeof(line), "board %s  nvme %s  nic %s", fmt_temp1(b, sizeof(b), f->board_c),
             fmt_temp1(c, sizeof(c), f->nvme_c), fmt_temp1(d, sizeof(d), f->nic_c));
    set_text(s_temps, line);
    snprintf(line, sizeof(line), "up %s", fmt_uptime(a, sizeof(a), f->up_s));
    set_text(s_up, line);
}

static void update_detail(const rx_snapshot_t *rx)
{
    const status_frame_t *f = &rx->frame;
    char a[32], col[2][STATUS_MAX_TEMPS / 2 * 32 + 1], line[160];
    set_text(s_d_clock, fmt_clock(a, sizeof(a), f->t, f->has_tz ? f->tz_s : 0));
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
    snprintf(line, sizeof(line), "frames %lu  rejected %lu\nfw %s",
             (unsigned long)rx->accepted, (unsigned long)rx->rejected,
             esp_app_get_description()->version);
    set_text(s_d_footer, line);
}

static void update_nodata(const rx_snapshot_t *rx, int64_t age_s)
{
    char a[32], line[96];
    if (age_s < 0) {
        set_text(s_n_age, "no frame since boot");
        set_text(s_n_host, "");
    } else {
        snprintf(line, sizeof(line), "last frame %s ago", fmt_age(a, sizeof(a), age_s));
        set_text(s_n_age, line);
        set_text(s_n_host, rx->frame.host);
    }
}

static void tick(lv_timer_t *t)
{
    (void)t;
    int64_t now = esp_timer_get_time() / 1000;
    serial_rx_get(&s_rx);
    link_state_t st = link_state(&s_rx.link, now);
    int64_t age_s = link_age_s(&s_rx.link, now);

    shift_t sh = burnin_shift(now);
    if (sh.dx != s_last_shift.dx || sh.dy != s_last_shift.dy) {
        lv_obj_set_pos(s_root, sh.dx, sh.dy);
        s_last_shift = sh;
    }
    if ((int)st != s_last_state) {
        display_set_brightness(burnin_brightness(st));
        s_last_state = (int)st;
        s_page_dirty = true;
    }
    if (s_page_dirty) {
        bool nodata = st == LINK_NO_DATA;
        lv_obj_set_hidden(s_nodata, !nodata);
        lv_obj_set_hidden(s_overview, nodata || s_page != PAGE_OVERVIEW);
        lv_obj_set_hidden(s_detail, nodata || s_page != PAGE_DETAIL);
        s_page_dirty = false;
    }

    if (st == LINK_NO_DATA) {
        update_nodata(&s_rx, age_s);
    } else if (s_page == PAGE_OVERVIEW) {
        update_overview(&s_rx.frame, st, age_s);
    } else {
        update_detail(&s_rx);
    }
}

void ui_init(void)
{
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(C_BG), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_scrollable(scr, false);

    s_root = box(scr);
    lv_obj_set_clickable(s_root, true);
    lv_obj_add_event_cb(s_root, on_tap, LV_EVENT_CLICKED, NULL);

    build_overview();
    build_detail();
    build_nodata();
    lv_obj_set_hidden(s_overview, true);
    lv_obj_set_hidden(s_detail, true);

    tick(NULL);
    lv_timer_create(tick, UI_PERIOD_MS, NULL);
}
