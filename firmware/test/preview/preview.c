/*
 * Host preview: render the real view.c with the same LVGL version and the
 * same LVGL config as the firmware, into 480x480 PNGs. No SDL, no board.
 *
 * It also checks that numbers do not move as they tick: for each tile tier,
 * two values of different length must put their last digit in the same
 * column. A failure exits 1.
 *
 * Build and run: scripts/firmware.py preview (writes firmware/preview/).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "logic/carousel.h"
#include "logic/status.h"
#include "lvgl.h"
#include "png.h"
#include "view.h"

#define W 480

static uint16_t s_fb[W * W];
static uint8_t s_rgb[W * W * 3];
static int s_failed;

/* The same real frame as firmware/test/test_logic.c: `dgx-status --dry-run
 * --once` on a DGX Spark, with the host name replaced. */
static const char *REAL =
    "{\"v\":1,\"t\":1795664697,\"tz_s\":-18000,\"host\":\"test-host\",\"up_s\":1214815,"
    "\"load1\":0.35,\"load5\":0.48,\"load15\":0.36,\"cpu_pct\":0.3,"
    "\"mem_used_gib\":7.2,\"mem_total_gib\":121.7,\"gpu_pct\":0,\"gpu_w\":4,"
    "\"gpu_c\":44,\"occ\":\"idle\",\"fan0_rpm\":2790,\"fan1_rpm\":4185,"
    "\"board_c\":45.8,\"nvme_c\":44.9,\"nic_c\":47.0,"
    "\"th\":[[\"tz0\",45.8],[\"tz1\",43.9],[\"nvme\",44.9],[\"nic0\",47.0],"
    "[\"wifi\",42.0]]}";

static uint32_t tick_cb(void)
{
    return 0;
}

static void flush_cb(lv_display_t *d, const lv_area_t *area, uint8_t *px)
{
    (void)area;
    (void)px;
    lv_display_flush_ready(d);
}

static void render(const view_model_t *m)
{
    view_render(m);
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(NULL);
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

static void show(const char *dir, const char *name, const status_frame_t *f, link_state_t st,
                 int64_t age_s, carousel_t c)
{
    view_model_t m;
    memset(&m, 0, sizeof(m));
    view_model_fill(&m, f, st, age_s, &c);
    m.accepted = 3600;
    m.rejected = 2;
    m.fw_version = "preview";
    m.stack_free_rx = 2400;
    m.stack_free_ui = 3100;
    render(&m);
    save(dir, name);
}

static carousel_t on_page(page_id_t p)
{
    carousel_t c = {.page = p, .detail = false, .no_data = false};
    return c;
}

/* Absolute x of the last character of tile i's value. */
static int32_t last_char_x(int i)
{
    lv_obj_t *l = view_tile_value(i);
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
        view_model_fill(&m, f, LINK_LIVE, 0, &c);
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
    lv_display_set_buffers(d, s_fb, NULL, sizeof(s_fb), LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(d, flush_cb);
    view_init(lv_screen_active(), NULL, NULL);

    status_frame_t real;
    if (frame_parse(REAL, strlen(REAL), &real) != FRAME_OK) {
        fprintf(stderr, "the real frame does not parse\n");
        return 1;
    }
    show(dir, "1-gpu", &real, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "2-cooling", &real, LINK_LIVE, 0, on_page(PAGE_COOLING));
    show(dir, "3-host", &real, LINK_LIVE, 0, on_page(PAGE_HOST));

    status_frame_t busy = real;
    busy.gpu_pct = 97;
    busy.gpu_w = 180;
    busy.gpu_c = 85;
    snprintf(busy.occ, sizeof(busy.occ), "llama.cpp");
    busy.fan0_rpm = 9000;
    busy.fan1_rpm = 13500;
    busy.board_c = 72;
    busy.nvme_c = 81;
    busy.cpu_pct = 63;
    busy.mem_used_gib = 101.4f;
    busy.load1 = 12.48f;
    show(dir, "4-busy-gpu", &busy, LINK_LIVE, 0, on_page(PAGE_GPU));
    show(dir, "5-busy-cooling", &busy, LINK_LIVE, 0, on_page(PAGE_COOLING));
    show(dir, "6-busy-host", &busy, LINK_LIVE, 0, on_page(PAGE_HOST));
    show(dir, "7-stale", &busy, LINK_STALE, 6, on_page(PAGE_GPU));

    status_frame_t missing;
    status_frame_clear(&missing);
    missing.t = real.t;
    missing.has_tz = real.has_tz;
    missing.tz_s = real.tz_s;
    show(dir, "8-missing", &missing, LINK_LIVE, 0, on_page(PAGE_HOST));

    carousel_t nodata = {.page = PAGE_GPU, .no_data = true};
    show(dir, "9-no-data", &real, LINK_NO_DATA, 133, nodata);
    show(dir, "10-no-data-boot", &missing, LINK_NO_DATA, -1, nodata);
    carousel_t detail = {.page = PAGE_GPU, .detail = true};
    show(dir, "11-detail", &real, LINK_LIVE, 0, detail);

    /* Numbers must not move as they tick. */
    status_frame_t f = busy;
    check_column("util %", PAGE_GPU, 0, &f, &f.gpu_pct, 9, 100);
    check_column("power W", PAGE_GPU, 1, &f, &f.gpu_w, 4, 180);
    check_column("GPU C", PAGE_GPU, 2, &f, &f.gpu_c, 9, 85);
    check_column("fan kRPM", PAGE_COOLING, 1, &f, &f.fan1_rpm, 2700, 13500);
    check_column("memory", PAGE_HOST, 1, &f, &f.mem_used_gib, 7.2f, 121.9f);
    check_column("load", PAGE_HOST, 2, &f, &f.load1, 0.35f, 12.34f);

    printf("%s\n", s_failed ? "preview: FAILED" : "preview: ok");
    return s_failed ? 1 : 0;
}
