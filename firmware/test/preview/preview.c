/*
 * Host preview: render the real view.c with the same LVGL version and the
 * same LVGL config as the firmware, into 480x480 PNGs. No SDL, no board.
 *
 * It draws the way the board does: partial buffers of DRAW_LINES lines, the
 * CO5300 rounder on every invalidated area, and esp_lvgl_port's software
 * rotation into a panel framebuffer. The PNGs show the panel.
 *
 * Checks (a failure exits 1):
 * - numbers do not move as they tick: for each tile tier, two values of
 *   different length put their last digit in the same column;
 * - every flushed window, after rotation, has even x1/y1 and odd x2/y2;
 * - every lit pixel sits inside the panel's rounded outline, at every
 *   burn-in orbit offset and every rotation.
 *
 * Build and run: scripts/firmware.py preview (writes firmware/preview/).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "logic/burnin.h"
#include "logic/carousel.h"
#include "logic/co5300.h"
#include "logic/far.h"
#include "logic/settings.h"
#include "logic/history.h"
#include "logic/status.h"
#include "lvgl.h"
#include "png.h"
#include "view.h"

#define W 480

/* Same as the firmware: display.c DRAW_BUF_LINES. */
#define DRAW_LINES 40
/* Lit pixels must stay this far inside the panel outline. */
#define OUTLINE_SAFE_PX 2
static uint16_t s_draw[W * DRAW_LINES];
static uint16_t s_rot[W * DRAW_LINES]; /* the port's draw_buffs[2] */
static uint16_t s_fb[W * W];           /* the panel */
static int s_bad_windows;
static uint8_t s_rgb[W * W * 3];
static int s_failed;
static chart_style_t s_style = CHART_LEVEL;
static view_mode_t s_mode = MODE_NEAR;
static history_t s_hist;

static bool preview_heights(hist_metric_t m, const status_frame_t *f, int height,
                            int16_t out[HIST_LEN])
{
    return hist_heights(&s_hist, m, f, height, out);
}
/* Level | history, side by side, for each page. */
/* Contact sheet: every screen at half size, SHEET_COLS per row. */
#define HALF (W / 2)
#define SEP 8
#define SHEET_COLS 4
#define SHEET_MAX 96
#define SHEET_W (SHEET_COLS * HALF + (SHEET_COLS - 1) * SEP)
static uint8_t s_sheet[SHEET_W * ((SHEET_MAX / SHEET_COLS) * (HALF + SEP)) * 3];
static int s_sheet_n;
/* The README image: the six pages, NO DATA and the detail page, 2 x 4. */
#define README_W (2 * HALF + SEP)
#define README_H (4 * HALF + 3 * SEP)
static uint8_t s_readme[README_W * README_H * 3];

/* The same real frame as firmware/test/test_logic.c: `dgx-status --dry-run
 * --once` on a DGX Spark, with the host name replaced. */
static const char *REAL =
    "{\"v\":1,\"t\":1795664697,\"tz_s\":-18000,\"host\":\"test-host\",\"up_s\":1214815,"
    "\"load1\":0.35,\"load5\":0.48,\"load15\":0.36,\"cpu_pct\":0.3,"
    "\"mem_used_gib\":7.2,\"mem_total_gib\":127.9,\"gpu_pct\":0,\"gpu_w\":4,"
    "\"gpu_c\":44,\"occ\":\"idle\",\"fan0_rpm\":2790,\"fan1_rpm\":4185,"
    "\"board_c\":45.8,\"nvme_c\":44.9,\"nic_c\":47.0,"
    "\"th\":[[\"tz0\",45.8],[\"tz1\",43.9],[\"nvme\",44.9],[\"nic0\",47.0],"
    "[\"wifi\",42.0]],"
    /* From a 2026-11-26 dry run on the same machine. */
    "\"lan_rx_bps\":127199,\"lan_tx_bps\":471094,\"lan_mbps\":2500,"
    "\"roce_rx_bps\":0,\"roce_tx_bps\":0,\"cip_bps\":0,\"ssd_rd_Bps\":74401,"
    "\"ssd_wr_Bps\":264537,\"disk_used_gb\":3339,\"disk_size_gb\":4032,\"llm_up\":0}";

static uint32_t tick_cb(void)
{
    return 0;
}

/* display.c rounder_cb, through esp_lvgl_port's invalidate callback. */
static void on_invalidate(lv_event_t *e)
{
    lv_area_t *a = lv_event_get_param(e);
    if (a == NULL) {
        return;
    }
    px_area_t p = {a->x1, a->y1, a->x2, a->y2};
    co5300_round(&p);
    a->x1 = p.x1;
    a->y1 = p.y1;
    a->x2 = p.x2;
    a->y2 = p.y2;
}

/* esp_lvgl_port 2.9.0 lvgl_port_flush_callback, software-rotation path,
 * writing into s_fb in place of esp_lcd_panel_draw_bitmap. */
static void flush_cb(lv_display_t *d, const lv_area_t *area, uint8_t *px)
{
    const lv_display_rotation_t rot = lv_display_get_rotation(d);
    const int32_t ww = lv_area_get_width(area), hh = lv_area_get_height(area);
    px_area_t out = {area->x1, area->y1, area->x2, area->y2};
    const uint16_t *src = (const uint16_t *)px;
    if (rot != LV_DISPLAY_ROTATION_0) {
        const lv_color_format_t cf = lv_display_get_color_format(d);
        const uint32_t w_stride = lv_draw_buf_width_to_stride((uint32_t)ww, cf);
        const uint32_t h_stride = lv_draw_buf_width_to_stride((uint32_t)hh, cf);
        if (rot == LV_DISPLAY_ROTATION_180) {
            lv_draw_sw_rotate(px, s_rot, hh, ww, (int32_t)h_stride, (int32_t)h_stride, rot, cf);
        } else {
            lv_draw_sw_rotate(px, s_rot, ww, hh, (int32_t)w_stride, (int32_t)h_stride, rot, cf);
        }
        port_rotate_area(&out, (lrot_t)rot, W);
        src = s_rot;
    }
    if (!co5300_ok(&out)) {
        if (s_bad_windows++ == 0) {
            fprintf(stderr, "FAIL window (%d,%d)-(%d,%d) at rotation %d\n", (int)out.x1,
                    (int)out.y1, (int)out.x2, (int)out.y2, (int)rot);
        }
    }
    const int32_t ow = out.x2 - out.x1 + 1;
    for (int32_t y = out.y1; y <= out.y2; y++) {
        if (y < 0 || y >= W || out.x1 < 0 || out.x2 >= W) {
            continue;
        }
        memcpy(&s_fb[y * W + out.x1], &src[(y - out.y1) * ow], (size_t)ow * sizeof(uint16_t));
    }
    lv_display_flush_ready(d);
}

/* Outside the panel's rounded outline, less OUTLINE_SAFE_PX, by how much
 * (in px); 0 when inside. Pixel centres. */
static double outside_by(int x, int y)
{
    const double r = VIEW_PANEL_CORNER_R, lim = r - OUTLINE_SAFE_PX;
    const double cx = x + 0.5, cy = y + 0.5;
    const double qx = cx < r ? r - cx : (cx > W - r ? cx - (W - r) : 0);
    const double qy = cy < r ? r - cy : (cy > W - r ? cy - (W - r) : 0);
    if (qx > 0 && qy > 0) {
        const double d = sqrt(qx * qx + qy * qy);
        return d > lim ? d - lim : 0;
    }
    const double e = fmin(fmin(cx, W - cx), fmin(cy, W - cy));
    return e < OUTLINE_SAFE_PX ? OUTLINE_SAFE_PX - e : 0;
}

/* Count lit pixels outside the outline; report the worst one. */
static int outline_misses(const char *what, int dx, int dy)
{
    int n = 0;
    double worst = 0;
    int wx = 0, wy = 0;
    for (int y = 0; y < W; y++) {
        for (int x = 0; x < W; x++) {
            if (s_fb[y * W + x] == 0) {
                continue;
            }
            const double o = outside_by(x, y);
            if (o > 0) {
                n++;
                if (o > worst) {
                    worst = o;
                    wx = x;
                    wy = y;
                }
            }
        }
    }
    if (n > 0) {
        fprintf(stderr, "FAIL outline %s shift (%d,%d): %d px outside, worst (%d,%d) by %.1f px\n",
                what, dx, dy, n, wx, wy, worst);
    }
    return n;
}

static void render(const view_model_t *m)
{
    view_render(m);
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(NULL);
}

/* Render m at every burn-in offset and check the outline; leave it drawn
 * at offset 0. */
static void check_orbit(const char *what, const view_model_t *m)
{
    int fails = 0;
    for (int k = 0; k < 16; k++) {
        shift_t sh = burnin_shift((int64_t)k * SHIFT_PERIOD_MS);
        view_set_shift(sh.dx, sh.dy);
        render(m);
        fails += outline_misses(what, sh.dx, sh.dy);
    }
    s_failed += fails > 0;
    view_set_shift(0, 0);
    render(m);
}

static void save(const char *dir, const char *name)
{
    for (int i = 0; i < W * W; i++) {
        uint16_t p = s_fb[i];
        uint8_t r = (uint8_t)((p >> 11) & 0x1F), g = (uint8_t)((p >> 5) & 0x3F),
                b = (uint8_t)(p & 0x1F);
        s_rgb[i * 3] = (uint8_t)((r << 3) | (r >> 2));
        s_rgb[i * 3 + 1] = (uint8_t)((g << 2) | (g >> 4));
        s_rgb[i * 3 + 2] = (uint8_t)((b << 3) | (b >> 2));
    }
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.png", dir, name);
    if (png_write_rgb(path, s_rgb, W, W) != 0) {
        fprintf(stderr, "cannot write %s\n", path);
        s_failed++;
        return;
    }
    printf("wrote %s\n", path);
}

/* Downscale the current frame 2:1 (box filter) into a sheet. */
static void downscale_into(uint8_t *sheet, int sheet_w, int ox, int oy)
{
    for (int y = 0; y < HALF; y++) {
        for (int x = 0; x < HALF; x++) {
            for (int ch = 0; ch < 3; ch++) {
                int sum = 0;
                for (int k = 0; k < 4; k++) {
                    sum += s_rgb[((2 * y + k / 2) * W + 2 * x + k % 2) * 3 + ch];
                }
                sheet[((oy + y) * sheet_w + ox + x) * 3 + ch] = (uint8_t)(sum / 4);
            }
        }
    }
}

static void add_to_sheet(void)
{
    if (s_sheet_n >= SHEET_MAX) {
        return;
    }
    int n = s_sheet_n++;
    downscale_into(s_sheet, SHEET_W, (n % SHEET_COLS) * (HALF + SEP), (n / SHEET_COLS) * (HALF + SEP));
}

static void add_to_readme(int cell)
{
    downscale_into(s_readme, README_W, (cell % 2) * (HALF + SEP), (cell / 2) * (HALF + SEP));
}

static void show(const char *dir, const char *name, const status_frame_t *f, link_state_t st,
                 int64_t age_s, carousel_t c)
{
    view_model_t m;
    memset(&m, 0, sizeof(m));
    view_model_fill(&m, f, st, age_s, &c, s_mode);
    m.style = s_style;
    m.hist_heights = preview_heights;
    m.accepted = 3600;
    m.rejected = 2;
    m.fw_version = "preview";
    m.stack_free_rx = 2400;
    m.stack_free_ui = 3100;
    check_orbit(name, &m);
    save(dir, name);
    add_to_sheet();
}

/*
 * A synthetic 2-minute history ending at frame `now`:
 * GPU usage ramps up with a dip, fans rise, GPU temperature crosses the
 * warm threshold, and a 15 s gap (daemon restart) sits in the middle.
 */
static void synthetic_history(const status_frame_t *now)
{
    hist_init(&s_hist);
    status_frame_t f = *now;
    int64_t t = 0;
    for (int i = 0; i < HIST_LEN; i++) {
        if (i >= 60 && i < 75) {
            continue; /* the gap: no frames for 15 s */
        }
        float k = (float)i / (HIST_LEN - 1); /* 0 .. 1 */
        float ramp = 12 + (now->gpu_pct - 12) * k;
        f.gpu_pct = (i >= 35 && i < 48) ? 18 : ramp; /* the dip */
        f.gpu_w = 8 + f.gpu_pct * (now->gpu_w - 8) / (now->gpu_pct > 0 ? now->gpu_pct : 1);
        f.gpu_c = 58 + (now->gpu_c - 58) * k;
        f.fan0_rpm = 2700 + (now->fan0_rpm - 2700) * k;
        f.fan1_rpm = 3000 + (now->fan1_rpm - 3000) * k * k;
        f.board_c = 50 + (now->board_c - 50) * k;
        f.nvme_c = 45 + (now->nvme_c - 45) * k;
        f.cpu_pct = 20 + (now->cpu_pct - 20) * k + ((i % 9) < 3 ? 6 : 0);
        f.mem_used_gib = 30 + (now->mem_used_gib - 30) * k;
        t = (int64_t)i * 1000;
        hist_push_frame(&s_hist, i == HIST_LEN - 1 ? now : &f, t);
    }
}

static carousel_t on_page(page_id_t p)
{
    carousel_t c = {.page = p, .detail = false, .no_data = false};
    return c;
}

/* Absolute x of the last character of label l. */
static int32_t label_last_x(lv_obj_t *l)
{
    lv_obj_update_layout(l);
    const char *t = lv_label_get_text(l);
    uint32_t n = 0; /* UTF-8 code points */
    for (const char *c = t; *c; c++) {
        n += ((unsigned char)*c & 0xC0) != 0x80;
    }
    lv_point_t p;
    lv_label_get_letter_pos(l, n - 1, &p);
    lv_area_t a;
    lv_obj_get_coords(l, &a);
    return a.x1 + p.x;
}

/* Absolute x of the last character of tile i's value. */
static int32_t last_char_x(int i)
{
    return label_last_x(view_tile_value(i));
}

/* Far mode: page p with two values; the last digit keeps its column. */
static void check_far_column(const char *what, far_page_t p, status_frame_t *f, float *field,
                             float short_v, float long_v)
{
    carousel_t c = on_page((page_id_t)p);
    view_model_t m;
    int32_t x[2];
    char text[2][FAR_VALUE_MAX];
    float v[2] = {short_v, long_v};
    for (int k = 0; k < 2; k++) {
        *field = v[k];
        memset(&m, 0, sizeof(m));
        view_model_fill(&m, f, LINK_LIVE, 0, &c, MODE_FAR);
        render(&m);
        x[k] = label_last_x(view_far_value());
        snprintf(text[k], sizeof(text[k]), "%s", lv_label_get_text(view_far_value()));
    }
    int ok = x[0] == x[1] && strlen(text[0]) != strlen(text[1]);
    printf("%s far %-8s \"%s\" x=%d, \"%s\" x=%d\n", ok ? "ok  " : "FAIL", what, text[0],
           (int)x[0], text[1], (int)x[1]);
    s_failed += !ok;
}

/* Render page p with two frames and compare tile i's last-digit column. */
static void check_column(const char *what, page_id_t p, int i, status_frame_t *f,
                         float *field, float short_v, float long_v)
{
    carousel_t c = on_page(p);
    view_model_t m;
    int32_t x[2];
    char text[2][TILE_VALUE_MAX];
    float v[2] = {short_v, long_v};
    for (int k = 0; k < 2; k++) {
        *field = v[k];
        memset(&m, 0, sizeof(m));
        view_model_fill(&m, f, LINK_LIVE, 0, &c, MODE_NEAR);
        render(&m);
        x[k] = last_char_x(i);
        snprintf(text[k], sizeof(text[k]), "%s", lv_label_get_text(view_tile_value(i)));
    }
    int ok = x[0] == x[1] && strlen(text[0]) != strlen(text[1]);
    printf("%s %-8s \"%s\" x=%d, \"%s\" x=%d\n", ok ? "ok  " : "FAIL", what, text[0],
           (int)x[0], text[1], (int)x[1]);
    s_failed += !ok;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    lv_init();
    lv_tick_set_cb(tick_cb);
    lv_display_t *d = lv_display_create(W, W);
    lv_display_set_color_format(d, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(d, s_draw, NULL, sizeof(s_draw), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(d, flush_cb);
    lv_display_add_event_cb(d, on_invalidate, LV_EVENT_INVALIDATE_AREA, NULL);
    view_init(lv_screen_active(), NULL, NULL, NULL);
    view_set_animations(false);

    status_frame_t real;
    if (frame_parse(REAL, strlen(REAL), &real) != FRAME_OK) {
        fprintf(stderr, "the real frame does not parse\n");
        return 1;
    }
    memset(s_sheet, 0x30, sizeof(s_sheet)); /* gray gutters between cells */
    memset(s_readme, 0x30, sizeof(s_readme));
    show(dir, "01-gpu", &real, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "02-cooling", &real, LINK_LIVE, 0, on_page(PAGE_COOLING));
    show(dir, "03-host", &real, LINK_LIVE, 0, on_page(PAGE_HOST));

    status_frame_t busy = real;
    busy.gpu_pct = 97;
    busy.gpu_w = 180;
    busy.gpu_c = 85;
    snprintf(busy.occ, sizeof(busy.occ), "llama.cpp");
    busy.fan0_rpm = 9000;
    busy.fan1_rpm = 13500;
    busy.board_c = 72;
    busy.nvme_c = 61;
    busy.cpu_pct = 63;
    busy.mem_used_gib = 101.4f;
    busy.load1 = 12.48f;
    busy.lan_rx_bps = 2.3e9;
    busy.lan_tx_bps = 184e6;
    busy.roce_rx_bps = 180e9;
    busy.roce_tx_bps = 176.4e9;
    busy.cip_bps = 18.4e6;
    busy.ssd_rd_bytes_s = 6e9;
    busy.ssd_wr_bytes_s = 940e6;
    busy.llm_up = 1;
    snprintf(busy.llm_model, sizeof(busy.llm_model), "MiMo-v2.6-Flash");
    busy.llm_out_tps = 45;
    busy.llm_in_tps = 2000;
    busy.llm_req = 3;
    busy.llm_kv_pct = 62;
    busy.llm_ttft_ms = 467;
    busy.llm_spec_pct = 66.6;
    show(dir, "04-busy-gpu", &busy, LINK_LIVE, 0, on_page(PAGE_GPU));
    add_to_readme(0);
    show(dir, "05-busy-cooling", &busy, LINK_LIVE, 0, on_page(PAGE_COOLING));
    add_to_readme(1);
    show(dir, "06-busy-host", &busy, LINK_LIVE, 0, on_page(PAGE_HOST));
    add_to_readme(2);

    status_frame_t hot = busy;
    hot.gpu_c = 92;
    hot.board_c = 82;
    hot.nvme_c = 75;
    show(dir, "07-hot-gpu", &hot, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "08-hot-cooling", &hot, LINK_LIVE, 0, on_page(PAGE_COOLING));
    show(dir, "09-stale", &busy, LINK_STALE, 6, on_page(PAGE_GPU));

    status_frame_t missing;
    status_frame_clear(&missing);
    missing.t = real.t;
    missing.has_tz = real.has_tz;
    missing.tz_s = real.tz_s;
    show(dir, "10-missing", &missing, LINK_LIVE, 0, on_page(PAGE_HOST));

    carousel_t nodata = {.page = PAGE_GPU, .no_data = true};
    show(dir, "11-no-data", &real, LINK_NO_DATA, 133, nodata);
    add_to_readme(6);
    show(dir, "12-no-data-boot", &missing, LINK_NO_DATA, -1, nodata);
    carousel_t detail = {.page = PAGE_GPU, .detail = true};
    show(dir, "13-detail", &real, LINK_LIVE, 0, detail);
    add_to_readme(7);

    /* Network and storage: idle (the real frame), busy, and missing
     * counters (the first frame after a restart, or a counter reset). */
    show(dir, "22-network-idle", &real, LINK_LIVE, 0, on_page(PAGE_NETWORK));
    show(dir, "23-storage-idle", &real, LINK_LIVE, 0, on_page(PAGE_STORAGE));
    show(dir, "24-network-busy", &busy, LINK_LIVE, 0, on_page(PAGE_NETWORK));
    add_to_readme(3);
    show(dir, "25-storage-busy", &busy, LINK_LIVE, 0, on_page(PAGE_STORAGE));
    add_to_readme(4);
    status_frame_t gone = busy;
    gone.roce_rx_bps = NAN;
    gone.roce_tx_bps = NAN;
    gone.ssd_wr_bytes_s = NAN;
    gone.lan_mbps = NAN;
    show(dir, "26-network-missing", &gone, LINK_LIVE, 0, on_page(PAGE_NETWORK));
    show(dir, "27-storage-missing", &gone, LINK_LIVE, 0, on_page(PAGE_STORAGE));

    /* LLM: busy, the exporter down (dashes), the detail page. Idle hides
     * the page: the carousel steps from Storage straight to GPU. */
    show(dir, "28-llm-busy", &busy, LINK_LIVE, 0, on_page(PAGE_LLM));
    add_to_readme(5);
    status_frame_t down = busy;
    down.llm_up = down.llm_out_tps = down.llm_in_tps = down.llm_req = NAN;
    down.llm_kv_pct = down.llm_spec_pct = down.llm_ttft_ms = NAN;
    down.llm_model[0] = '\0';
    show(dir, "29-llm-exporter-down", &down, LINK_LIVE, 0, on_page(PAGE_LLM));
    carousel_t llm_detail_c = {.page = PAGE_LLM, .detail = true};
    show(dir, "30-llm-detail", &busy, LINK_LIVE, 0, llm_detail_c);
    {
        carousel_t skip;
        carousel_init(&skip, 0);
        carousel_tick(&skip, LINK_LIVE, 0);
        skip.page = PAGE_STORAGE;
        carousel_set_hidden(&skip, PAGE_LLM, page_hidden(PAGE_LLM, &real), 0);
        carousel_tick(&skip, LINK_LIVE, CAROUSEL_PAGE_MS);
        printf("llm idle (llm_up %.0f): Storage steps to %s\n", real.llm_up,
               page_name(skip.page));
        if (real.llm_up != 0 || skip.page != PAGE_GPU) {
            fprintf(stderr, "FAIL the idle LLM page is not skipped\n");
            s_failed++;
        }
    }

    /* The 2-minute history style, on the busy frame (and the hot one). */
    synthetic_history(&busy);
    s_style = CHART_HISTORY;
    show(dir, "14-history-gpu", &busy, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "15-history-cooling", &busy, LINK_LIVE, 0, on_page(PAGE_COOLING));
    show(dir, "16-history-host", &busy, LINK_LIVE, 0, on_page(PAGE_HOST));
    synthetic_history(&hot);
    show(dir, "17-history-hot-gpu", &hot, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "18-detail-history", &real, LINK_LIVE, 0, detail);

    /* KEY turns the screen clockwise; the panel shows it turned. */
    s_style = CHART_LEVEL;
    static const char *const turned[3] = {"19-turn-90", "20-turn-180", "21-turn-270"};
    for (int k = 1; k <= 3; k++) {
        lv_display_set_rotation(d, (lv_display_rotation_t)lrot_for_cw_turns(k));
        show(dir, turned[k - 1], &busy, LINK_LIVE, 0, on_page(PAGE_GPU));
    }
    lv_display_set_rotation(d, LV_DISPLAY_ROTATION_0);


    /* ---- Far mode: one metric per page, read from 2 m ---- */
    s_mode = MODE_FAR;
    s_style = CHART_LEVEL;
    {
        static const char *const state_name[3] = {"idle", "busy", "hot"};
        const status_frame_t *states[3] = {&real, &busy, &hot};
        for (int st = 0; st < 3; st++) {
            for (int p = 0; p < FAR_PAGE_COUNT; p++) {
                char name[64];
                snprintf(name, sizeof(name), "far-%s-%d-%s", state_name[st], p,
                         far_page_name((far_page_t)p));
                for (char *ch = name; *ch; ch++) {
                    *ch = (char)(*ch >= 'A' && *ch <= 'Z' ? *ch - 'A' + 'a' : *ch);
                }
                if (far_hidden((far_page_t)p, states[st])) {
                    printf("%s: hidden\n", name);
                    continue;
                }
                show(dir, name, states[st], LINK_LIVE, 0, on_page((page_id_t)p));
            }
        }
        /* The idle frame hides LLM and Model; a busy one shows both. */
        if (!far_hidden(FAR_LLM, &real) || !far_hidden(FAR_MODEL, &real) ||
            far_hidden(FAR_LLM, &busy) || far_hidden(FAR_MODEL, &busy)) {
            fprintf(stderr, "FAIL far LLM and Model hiding\n");
            s_failed++;
        }
    }
    show(dir, "far-missing-gpu", &missing, LINK_LIVE, 0, on_page((page_id_t)FAR_GPU));
    show(dir, "far-missing-memory", &missing, LINK_LIVE, 0, on_page((page_id_t)FAR_MEMORY));
    show(dir, "far-stale-gpu", &busy, LINK_STALE, 6, on_page((page_id_t)FAR_GPU));
    show(dir, "far-stale-temp", &hot, LINK_STALE, 6, on_page((page_id_t)FAR_TEMP));
    {
        status_frame_t big = busy;
        big.llm_out_tps = 2350; /* "2.4" k tok/s */
        show(dir, "far-llm-k", &big, LINK_LIVE, 0, on_page((page_id_t)FAR_LLM));
        big.llm_out_tps = 940;
        show(dir, "far-llm-940", &big, LINK_LIVE, 0, on_page((page_id_t)FAR_LLM));
        big.gpu_c = 100; /* three digits and the unit */
        show(dir, "far-temp-100", &big, LINK_LIVE, 0, on_page((page_id_t)FAR_TEMP));
        big.gpu_pct = 100;
        show(dir, "far-gpu-100", &big, LINK_LIVE, 0, on_page((page_id_t)FAR_GPU));
    }
    {
        /* Names: short, medium, long, several, and the occupant alone. */
        static const char *const names[][2] = {
            {"far-name-short", "Qwen/Qwen3-8B"},
            {"far-name-medium", "XiaomiMiMo/MiMo-v2.6-Flash"},
            {"far-name-long", "harborwater/Ornith-1.5-35B-A3B-AWQ-INT4"},
            {"far-name-longer", "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8"},
            {"far-name-two", "Qwen/Qwen3-8B, google/gemma-3-27b-it"},
            {"far-name-nobreak", "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUV"},
        };
        for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
            status_frame_t n = busy;
            snprintf(n.llm_model, sizeof(n.llm_model), "%s", names[i][1]);
            show(dir, names[i][0], &n, LINK_LIVE, 0, on_page((page_id_t)FAR_MODEL));
        }
        status_frame_t occ = real;
        snprintf(occ.occ, sizeof(occ.occ), "llama.cpp");
        show(dir, "far-name-occupant", &occ, LINK_LIVE, 0, on_page((page_id_t)FAR_MODEL));
        snprintf(occ.occ, sizeof(occ.occ), "vllm"); /* one line, 100 px caps */
        show(dir, "far-name-vllm", &occ, LINK_LIVE, 0, on_page((page_id_t)FAR_MODEL));
    }
    show(dir, "far-no-data", &real, LINK_NO_DATA, 133, nodata);
    show(dir, "far-no-data-boot", &missing, LINK_NO_DATA, -1, nodata);
    show(dir, "far-detail-llm", &busy, LINK_LIVE, 0,
         (carousel_t){.page = (page_id_t)FAR_LLM, .detail = true});
    {
        /* The mode with nothing stored: far, on its first page. */
        const view_mode_t def = settings_mode(NULL);
        printf("default mode (nothing stored): %s\n", view_mode_name(def));
        if (def != MODE_FAR) {
            fprintf(stderr, "FAIL the default mode is not far\n");
            s_failed++;
        }
        s_mode = def;
        carousel_t first;
        carousel_init(&first, 0);
        carousel_set_page_count(&first, FAR_PAGE_COUNT, 0);
        carousel_tick(&first, LINK_LIVE, 0);
        show(dir, "far-default", &busy, LINK_LIVE, 0, first);
    }
    synthetic_history(&busy);
    s_style = CHART_HISTORY;
    show(dir, "far-history-gpu", &busy, LINK_LIVE, 0, on_page((page_id_t)FAR_GPU));
    show(dir, "far-history-memory", &busy, LINK_LIVE, 0, on_page((page_id_t)FAR_MEMORY));
    synthetic_history(&hot);
    show(dir, "far-history-hot-temp", &hot, LINK_LIVE, 0, on_page((page_id_t)FAR_TEMP));
    s_style = CHART_LEVEL;
    static const char *const far_turned[3] = {"far-turn-90", "far-turn-180", "far-turn-270"};
    for (int k = 1; k <= 3; k++) {
        lv_display_set_rotation(d, (lv_display_rotation_t)lrot_for_cw_turns(k));
        show(dir, far_turned[k - 1], &busy, LINK_LIVE, 0, on_page((page_id_t)FAR_GPU));
    }
    lv_display_set_rotation(d, (lv_display_rotation_t)lrot_for_cw_turns(1));
    {
        status_frame_t n = busy;
        snprintf(n.llm_model, sizeof(n.llm_model), "harborwater/Ornith-1.5-35B-A3B-AWQ-INT4");
        show(dir, "far-turn-90-name-long", &n, LINK_LIVE, 0, on_page((page_id_t)FAR_MODEL));
        show(dir, "far-turn-90-memory", &n, LINK_LIVE, 0, on_page((page_id_t)FAR_MEMORY));
    }
    lv_display_set_rotation(d, LV_DISPLAY_ROTATION_0);
    s_mode = MODE_NEAR;

    /* Level and history side by side: one row per page. */
    {
        static uint8_t styles[(2 * HALF + SEP) * (3 * HALF + 2 * SEP) * 3];
        const int sw = 2 * HALF + SEP;
        memset(styles, 0x30, sizeof(styles));
        for (int row = 0; row < 3; row++) {
            for (int col = 0; col < 2; col++) {
                s_style = col ? CHART_HISTORY : CHART_LEVEL;
                synthetic_history(&busy);
                view_model_t m;
                memset(&m, 0, sizeof(m));
                carousel_t c = on_page((page_id_t)row);
                view_model_fill(&m, &busy, LINK_LIVE, 0, &c, MODE_NEAR);
                m.style = s_style;
                m.hist_heights = preview_heights;
                render(&m);
                for (int i = 0; i < W * W; i++) {
                    uint16_t px = s_fb[i];
                    uint8_t r = (uint8_t)((px >> 11) & 0x1F), g = (uint8_t)((px >> 5) & 0x3F),
                            b = (uint8_t)(px & 0x1F);
                    s_rgb[i * 3] = (uint8_t)((r << 3) | (r >> 2));
                    s_rgb[i * 3 + 1] = (uint8_t)((g << 2) | (g >> 4));
                    s_rgb[i * 3 + 2] = (uint8_t)((b << 3) | (b >> 2));
                }
                downscale_into(styles, sw, col * (HALF + SEP), row * (HALF + SEP));
            }
        }
        char spath[512];
        snprintf(spath, sizeof(spath), "%s/styles.png", dir);
        if (png_write_rgb(spath, styles, sw, 3 * HALF + 2 * SEP) != 0) {
            s_failed++;
        } else {
            printf("wrote %s\n", spath);
        }
        s_style = CHART_LEVEL;
    }

    char path[512];
    int rows = (s_sheet_n + SHEET_COLS - 1) / SHEET_COLS;
    snprintf(path, sizeof(path), "%s/contact-sheet.png", dir);
    if (png_write_rgb(path, s_sheet, SHEET_W, rows * (HALF + SEP) - SEP) != 0) {
        s_failed++;
    } else {
        printf("wrote %s\n", path);
    }
    snprintf(path, sizeof(path), "%s/pages.png", dir);
    if (png_write_rgb(path, s_readme, README_W, README_H) != 0) {
        s_failed++;
    } else {
        printf("wrote %s\n", path);
    }

    /* Numbers must not move as they tick. */
    status_frame_t f = busy;
    check_column("util %", PAGE_GPU, 0, &f, &f.gpu_pct, 9, 100);
    check_column("power W", PAGE_GPU, 1, &f, &f.gpu_w, 4, 180);
    check_column("GPU C", PAGE_GPU, 2, &f, &f.gpu_c, 9, 85);
    check_column("fan kRPM", PAGE_COOLING, 1, &f, &f.fan1_rpm, 2700, 13500);
    check_column("memory", PAGE_HOST, 1, &f, &f.mem_used_gib, 7.2f, 121.9f);
    check_column("load", PAGE_HOST, 2, &f, &f.load1, 0.35f, 12.34f);
    check_far_column("gpu %", FAR_GPU, &f, &f.gpu_pct, 9, 100);
    check_far_column("power W", FAR_POWER, &f, &f.gpu_w, 4, 180);
    check_far_column("temp C", FAR_TEMP, &f, &f.gpu_c, 9, 85);
    check_far_column("memory", FAR_MEMORY, &f, &f.mem_used_gib, 7.2f, 101.4f);
    check_far_column("cpu %", FAR_CPU, &f, &f.cpu_pct, 0, 63);

    /* Same LV_MEM_SIZE as the firmware, after every page and the detail
     * page have been drawn. The host is 64-bit, so its pointers and LVGL
     * objects are larger: the board's own figure (the minute log line) is
     * lower. Compare runs of the preview, not the preview with the board. */
    lv_mem_monitor_t mon;
    lv_mem_monitor(&mon);
    printf("lv heap: %u%% used, %lu of %lu B free, largest %lu B, peak %lu B\n",
           (unsigned)mon.used_pct, (unsigned long)mon.free_size, (unsigned long)mon.total_size,
           (unsigned long)mon.free_biggest_size, (unsigned long)mon.max_used);
    if (s_bad_windows > 0) {
        fprintf(stderr, "FAIL %d flushed windows break the CO5300 rule\n", s_bad_windows);
        s_failed++;
    }
    printf("%s\n", s_failed ? "preview: FAILED" : "preview: ok");
    return s_failed ? 1 : 0;
}
