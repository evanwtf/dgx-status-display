/*
 * Host unit tests for main/logic/: frame parsing, line splitting, link
 * state, formatting and burn-in policy. Each test anchors known values.
 *
 * Build and run: make -C firmware/test (in the pinned espressif/idf image).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "burnin.h"
#include "carousel.h"
#include "fmt.h"
#include "history.h"
#include "line_reader.h"
#include "palette.h"
#include "link_state.h"
#include "report.h"
#include "settings.h"
#include "status.h"
#include "tiles.h"

static int g_failed;
static int g_checks;

#define CHECK(cond)                                                              \
    do {                                                                         \
        g_checks++;                                                              \
        if (!(cond)) {                                                           \
            g_failed++;                                                          \
            fprintf(stderr, "%s:%d: FAIL: %s\n", __FILE__, __LINE__, #cond);     \
        }                                                                        \
    } while (0)

#define CHECK_STR(got, want)                                                     \
    do {                                                                         \
        g_checks++;                                                              \
        if (strcmp((got), (want)) != 0) {                                        \
            g_failed++;                                                          \
            fprintf(stderr, "%s:%d: FAIL: got \"%s\", want \"%s\"\n", __FILE__,  \
                    __LINE__, (got), (want));                                    \
        }                                                                        \
    } while (0)

#define CHECK_NEAR(got, want) CHECK(fabs((double)(got) - (double)(want)) < 1e-4)

static frame_result_t parse(const char *s, status_frame_t *f)
{
    return frame_parse(s, strlen(s), f);
}

/* A real frame from `dgx-status --dry-run --once` on a DGX Spark, 2026-11-25,
 * with the host name replaced. */
static const char *REAL =
    "{\"v\":1,\"t\":1795664697,\"tz_s\":-18000,\"host\":\"test-host\",\"up_s\":1214815,"
    "\"load1\":0.35,\"load5\":0.48,\"load15\":0.36,\"cpu_pct\":0.3,"
    "\"mem_used_gib\":7.2,\"mem_total_gib\":121.7,\"gpu_pct\":0,\"gpu_w\":4,"
    "\"gpu_c\":44,\"occ\":\"idle\",\"fan0_rpm\":2790,\"fan1_rpm\":4185,"
    "\"board_c\":45.8,\"nvme_c\":44.9,\"nic_c\":47.0,"
    "\"th\":[[\"tz0\",45.8],[\"tz1\",43.9],[\"nvme\",44.9],[\"nic0\",47.0],"
    "[\"wifi\",42.0]]}";

static void test_parse_real_frame(void)
{
    status_frame_t f;
    CHECK(parse(REAL, &f) == FRAME_OK);
    CHECK(f.t == 1795664697);
    CHECK(f.has_tz && f.tz_s == -18000);
    CHECK_STR(f.host, "test-host");
    CHECK_STR(f.occ, "idle");
    CHECK_NEAR(f.up_s, 1214815);
    CHECK_NEAR(f.load1, 0.35f);
    CHECK_NEAR(f.load15, 0.36f);
    CHECK_NEAR(f.cpu_pct, 0.3f);
    CHECK_NEAR(f.mem_used_gib, 7.2f);
    CHECK_NEAR(f.mem_total_gib, 121.7f);
    CHECK_NEAR(f.gpu_pct, 0);
    CHECK_NEAR(f.gpu_w, 4);
    CHECK_NEAR(f.gpu_c, 44);
    CHECK_NEAR(f.fan0_rpm, 2790);
    CHECK_NEAR(f.fan1_rpm, 4185);
    CHECK_NEAR(f.board_c, 45.8f);
    CHECK_NEAR(f.nvme_c, 44.9f);
    CHECK_NEAR(f.nic_c, 47.0f);
    CHECK(f.n_temps == 5);
    CHECK_STR(f.temps[0].label, "tz0");
    CHECK_NEAR(f.temps[4].c, 42.0f);
}

static void test_parse_minimal_frame_is_all_unknown(void)
{
    status_frame_t f;
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"test-host\"}", &f) == FRAME_OK);
    CHECK(f.t == 5);
    CHECK(!f.has_tz);
    CHECK(f.occ[0] == '\0');
    CHECK(isnan(f.up_s));
    CHECK(isnan(f.gpu_w));
    CHECK(isnan(f.fan0_rpm));
    CHECK(isnan(f.board_c));
    CHECK(f.n_temps == 0);
}

static void test_parse_rejects(void)
{
    status_frame_t f;
    /* Truncated line: the serial link dropped bytes. */
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"dg", &f) == FRAME_ERR_JSON);
    CHECK(parse("garbage", &f) == FRAME_ERR_JSON);
    CHECK(parse("", &f) == FRAME_ERR_EMPTY);
    CHECK(parse("[1,2]", &f) == FRAME_ERR_NOT_OBJECT);
    CHECK(parse("{\"v\":2,\"t\":5,\"host\":\"test-host\"}", &f) == FRAME_ERR_VERSION);
    CHECK(parse("{\"v\":\"1\",\"t\":5,\"host\":\"test-host\"}", &f) == FRAME_ERR_VERSION);
    CHECK(parse("{\"t\":5,\"host\":\"test-host\"}", &f) == FRAME_ERR_VERSION);
    CHECK(parse("{\"v\":1,\"host\":\"test-host\"}", &f) == FRAME_ERR_REQUIRED);
    CHECK(parse("{\"v\":1,\"t\":5}", &f) == FRAME_ERR_REQUIRED);
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"\"}", &f) == FRAME_ERR_REQUIRED);
    CHECK(parse("{\"v\":1,\"t\":-1,\"host\":\"test-host\"}", &f) == FRAME_ERR_REQUIRED);
    CHECK(parse("{\"v\":1,\"t\":\"5\",\"host\":\"test-host\"}", &f) == FRAME_ERR_REQUIRED);
    /* JSON has no NaN. A sender that writes one gets the frame rejected. */
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"test-host\",\"gpu_w\":NaN}", &f) == FRAME_ERR_JSON);
    /* A rejected frame leaves the output cleared, not half filled. */
    CHECK(f.host[0] == '\0');
    CHECK(isnan(f.gpu_w));
}

static void test_parse_bad_optional_fields_become_unknown(void)
{
    status_frame_t f;
    const char *s =
        "{\"v\":1,\"t\":5,\"host\":\"test-host\",\"gpu_c\":500,\"cpu_pct\":-1,"
        "\"gpu_w\":\"4\",\"gpu_pct\":101,\"fan0_rpm\":1e999,\"board_c\":45.5,"
        "\"mem_used_gib\":200,\"mem_total_gib\":121.7,\"tz_s\":999999,"
        "\"occ\":7,\"load1\":-0.5}";
    CHECK(parse(s, &f) == FRAME_OK);
    CHECK(isnan(f.gpu_c));    /* absurd temperature */
    CHECK(isnan(f.cpu_pct));  /* negative percent */
    CHECK(isnan(f.gpu_w));    /* wrong type */
    CHECK(isnan(f.gpu_pct));  /* over 100 */
    CHECK(isnan(f.fan0_rpm)); /* overflows to infinity */
    CHECK_NEAR(f.board_c, 45.5f);
    /* used > total: both are wrong, show neither. */
    CHECK(isnan(f.mem_used_gib));
    CHECK(isnan(f.mem_total_gib));
    CHECK(!f.has_tz);
    CHECK(f.occ[0] == '\0');
    CHECK(isnan(f.load1));
}

static void test_parse_size_cap(void)
{
    status_frame_t f;
    char line[PROTO_MAX_LINE + 8];
    const char *head = "{\"v\":1,\"t\":5,\"host\":\"test-host\",\"pad\":\"";
    size_t head_len = strlen(head);

    /* 1023 bytes of content plus the newline is exactly the cap: accepted. */
    size_t len = PROTO_MAX_LINE - 1;
    memset(line, 'x', sizeof(line));
    memcpy(line, head, head_len);
    memcpy(line + len - 2, "\"}", 2);
    CHECK(frame_parse(line, len, &f) == FRAME_OK);

    /* One byte more: rejected before cJSON sees it. */
    len = PROTO_MAX_LINE;
    memset(line, 'x', sizeof(line));
    memcpy(line, head, head_len);
    memcpy(line + len - 2, "\"}", 2);
    CHECK(frame_parse(line, len, &f) == FRAME_ERR_TOO_LONG);
}

static void test_parse_truncates_and_caps(void)
{
    status_frame_t f;
    char s[1024];
    int n = snprintf(s, sizeof(s),
                     "{\"v\":1,\"t\":5,\"host\":\"%s\",\"th\":[",
                     "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"); /* 44 chars */
    /* 26 entries: more than STATUS_MAX_TEMPS, and still under the line cap. */
    for (int i = 0; i < 26; i++) {
        n += snprintf(s + n, sizeof(s) - (size_t)n, "%s[\"sensor-long-label-%02d\",%d]",
                      i ? "," : "", i, 40 + i);
    }
    n += snprintf(s + n, sizeof(s) - (size_t)n, ",[\"bad\",\"x\"],[1,2],[\"hot\",900]]}");
    CHECK(n > 0 && (size_t)n < PROTO_MAX_LINE - 1);
    CHECK(parse(s, &f) == FRAME_OK);
    CHECK(strlen(f.host) == STATUS_HOST_MAX);
    CHECK(f.n_temps == STATUS_MAX_TEMPS);
    CHECK(strlen(f.temps[0].label) == STATUS_LABEL_MAX);
    CHECK_NEAR(f.temps[23].c, 63);
}

static void test_parse_nesting_depth(void)
{
    status_frame_t f;
    /* A real frame is 3 deep: object, "th" array, one [label, value] pair. */
    CHECK(parse(REAL, &f) == FRAME_OK);
    /* Depth 4 is the limit: accepted (the unknown key is ignored). */
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"test-host\",\"x\":[[[1]]]}", &f) == FRAME_OK);
    /* Depth 5: rejected before cJSON runs. */
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"test-host\",\"x\":[[[[1]]]]}", &f) ==
          FRAME_ERR_TOO_DEEP);
    /* 1000 x '[' would overflow the RX task stack inside cJSON. */
    char deep[1001];
    memset(deep, '[', 1000);
    deep[1000] = '\0';
    CHECK(frame_parse(deep, 1000, &f) == FRAME_ERR_TOO_DEEP);
    /* Brackets inside strings do not count, escaped quotes included. */
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"[[[[[[{{{{{{\"}", &f) == FRAME_OK);
    CHECK_STR(f.host, "[[[[[[{{{{{{");
    CHECK(parse("{\"v\":1,\"t\":5,\"host\":\"a\\\"[[[[[[\"}", &f) == FRAME_OK);
    /* Unbalanced closers are the parser's problem, not a crash. */
    CHECK(parse("]]]]", &f) == FRAME_ERR_JSON);
    CHECK(json_depth_ok("{\"a\":[1]}", 9, 2));
    CHECK(!json_depth_ok("{\"a\":[1]}", 9, 1));
}

static void test_line_reader(void)
{
    line_reader_t r;
    line_reader_init(&r);
    const char *stream = "abc\r\n\nde\n";
    int ready = 0;
    for (const char *p = stream; *p; p++) {
        line_event_t e = line_reader_feed(&r, (uint8_t)*p);
        if (e == LINE_READY) {
            ready++;
            CHECK_STR(r.buf, ready == 1 ? "abc" : "de");
            CHECK(r.len == (ready == 1 ? 3u : 2u));
        }
        CHECK(e != LINE_OVERFLOW);
    }
    CHECK(ready == 2);
}

static void feed_n(line_reader_t *r, size_t n, line_event_t *last)
{
    for (size_t i = 0; i < n; i++) {
        *last = line_reader_feed(r, 'x');
    }
}

static void test_line_reader_cap(void)
{
    line_reader_t r;
    line_event_t e = LINE_NONE;
    line_reader_init(&r);

    /* 1023 bytes + newline: fits exactly. */
    feed_n(&r, PROTO_MAX_LINE - 1, &e);
    CHECK(line_reader_feed(&r, '\n') == LINE_READY);
    CHECK(r.len == PROTO_MAX_LINE - 1);

    /* 1024 bytes + newline: one overflow, then the next line is clean. */
    feed_n(&r, PROTO_MAX_LINE, &e);
    CHECK(e == LINE_NONE);
    feed_n(&r, 5000, &e); /* a runaway line stays dropped */
    CHECK(line_reader_feed(&r, '\n') == LINE_OVERFLOW);
    CHECK(line_reader_feed(&r, 'o') == LINE_NONE);
    CHECK(line_reader_feed(&r, 'k') == LINE_NONE);
    CHECK(line_reader_feed(&r, '\n') == LINE_READY);
    CHECK_STR(r.buf, "ok");
}

static void test_link_state(void)
{
    link_t l;
    link_init(&l);
    CHECK(link_state(&l, 0) == LINK_NO_DATA);
    CHECK(link_state(&l, 999999) == LINK_NO_DATA);
    CHECK(link_age_s(&l, 5000) == -1);

    link_frame_ok(&l, 100000);
    CHECK(link_state(&l, 100000) == LINK_LIVE);
    CHECK(link_state(&l, 103000) == LINK_LIVE);
    CHECK(link_state(&l, 103001) == LINK_STALE);
    CHECK(link_state(&l, 110000) == LINK_STALE);
    CHECK(link_state(&l, 110001) == LINK_NO_DATA);
    CHECK(link_age_s(&l, 107900) == 7);
    CHECK(link_state(&l, 99000) == LINK_LIVE); /* out-of-order clock */
    CHECK(link_age_s(&l, 99000) == 0);

    link_frame_ok(&l, 200000);
    CHECK(link_state(&l, 200500) == LINK_LIVE);
    CHECK_STR(link_state_name(LINK_NO_DATA), "NO DATA");
}

static void test_fmt(void)
{
    char b[64];
    CHECK_STR(fmt_temp1(b, sizeof(b), 45.8f), "45.8\xC2\xB0");
    CHECK_STR(fmt_temp1(b, sizeof(b), NAN), FMT_DASH);
    CHECK_STR(fmt_temp0(b, sizeof(b), 43.6f), "44\xC2\xB0");
    CHECK_STR(fmt_temp0(b, sizeof(b), -3.4f), "-3\xC2\xB0");
    CHECK_STR(fmt_int_unit(b, sizeof(b), 4.4f, "W"), "4 W");
    CHECK_STR(fmt_int_unit(b, sizeof(b), 2790, "rpm"), "2790 rpm");
    CHECK_STR(fmt_int_unit(b, sizeof(b), INFINITY, "W"), FMT_DASH);
    CHECK_STR(fmt_pct(b, sizeof(b), 7, 0), "7%");
    CHECK_STR(fmt_pct(b, sizeof(b), 5.17f, 1), "5.2%");
    CHECK_STR(fmt_pct(b, sizeof(b), NAN, 1), FMT_DASH);
    CHECK_STR(fmt_mem(b, sizeof(b), 7.2f, 121.7f), "7.2/121.7 GiB");
    CHECK_STR(fmt_mem(b, sizeof(b), NAN, 121.7f), FMT_DASH "/121.7 GiB");
    CHECK_STR(fmt_load(b, sizeof(b), 0.35f, 0.48f, 0.36f), "0.35 0.48 0.36");
    CHECK_STR(fmt_load(b, sizeof(b), 0.35f, NAN, 0.36f), "0.35 " FMT_DASH " 0.36");
    /* 1214815 s = 14 d + 5215 s = 14 d 1 h 26 m 55 s. */
    CHECK_STR(fmt_uptime(b, sizeof(b), 1214815), "14d 1:26");
    CHECK_STR(fmt_uptime(b, sizeof(b), 3 * 3600 + 4 * 60 + 59), "3:04");
    CHECK_STR(fmt_uptime(b, sizeof(b), NAN), FMT_DASH);
    /* The real frame: logged at 22:44:57 New York time (UTC-5). */
    CHECK_STR(fmt_clock(b, sizeof(b), 1795664697, -18000), "22:44:57");
    CHECK_STR(fmt_clock(b, sizeof(b), 1795664697, 0), "03:44:57");
    CHECK_STR(fmt_clock(b, sizeof(b), 0, -3600), "23:00:00");
    CHECK_STR(fmt_age(b, sizeof(b), -1), "never");
    CHECK_STR(fmt_age(b, sizeof(b), 7), "7s");
    CHECK_STR(fmt_age(b, sizeof(b), 133), "2m 13s");
    CHECK_STR(fmt_age(b, sizeof(b), 3900), "1h 05m");
    CHECK_STR(fmt_age(b, sizeof(b), 3 * 86400 + 4 * 3600), "3d 4h");
    /* A short buffer truncates; it never overflows. */
    char small[4];
    fmt_mem(small, sizeof(small), 7.2f, 121.7f);
    CHECK(strlen(small) == 3);
}

static void test_burnin(void)
{
    /* Walk one full orbit plus one step, so the wrap-around is checked too. */
    shift_t prev = burnin_shift(0);
    CHECK(prev.dx == 10 && prev.dy == 0);
    int max_abs = 0;
    int distinct = 0;
    for (int64_t k = 1; k <= 17; k++) {
        shift_t s = burnin_shift(k * SHIFT_PERIOD_MS);
        CHECK(abs(s.dx) <= SHIFT_MAX_PX && abs(s.dy) <= SHIFT_MAX_PX);
        CHECK(abs(s.dx - prev.dx) <= SHIFT_MAX_STEP_PX);
        CHECK(abs(s.dy - prev.dy) <= SHIFT_MAX_STEP_PX);
        max_abs = abs(s.dx) > max_abs ? abs(s.dx) : max_abs;
        distinct += (s.dx != prev.dx || s.dy != prev.dy);
        prev = s;
    }
    CHECK(max_abs >= 8);    /* the orbit reaches 8-12 px */
    CHECK(distinct == 17);  /* every step moves */
    CHECK(burnin_shift(16LL * SHIFT_PERIOD_MS).dx == 10); /* period: 16 steps */
    /* Inside one period the offset holds still. */
    shift_t a = burnin_shift(SHIFT_PERIOD_MS + 1);
    shift_t b = burnin_shift(2 * SHIFT_PERIOD_MS - 1);
    CHECK(a.dx == b.dx && a.dy == b.dy);

    CHECK(burnin_brightness(LINK_LIVE, 0) == BRIGHTNESS_LIVE);
    CHECK(burnin_brightness(LINK_LIVE, IDLE_DIM_AFTER_MS - 1) == BRIGHTNESS_LIVE);
    CHECK(burnin_brightness(LINK_LIVE, IDLE_DIM_AFTER_MS) == BRIGHTNESS_IDLE);
    CHECK(burnin_brightness(LINK_STALE, 0) == BRIGHTNESS_LIVE);
    CHECK(burnin_brightness(LINK_NO_DATA, 0) == BRIGHTNESS_NO_DATA);
    CHECK(BRIGHTNESS_NO_DATA < BRIGHTNESS_IDLE && BRIGHTNESS_IDLE < BRIGHTNESS_LIVE);
    CHECK(BRIGHTNESS_LIVE < 255);
}

static void test_fmt_fixed(void)
{
    char b[32];
    /* Fixed decimals: never "7" for 7.0, never "7.12" for 7.12. */
    CHECK_STR(fmt_fixed(b, sizeof(b), 7.0f, 1, 5), "7.0");
    CHECK_STR(fmt_fixed(b, sizeof(b), 7.12f, 1, 5), "7.1");
    CHECK_STR(fmt_fixed(b, sizeof(b), 121.94f, 1, 5), "121.9");
    CHECK_STR(fmt_fixed(b, sizeof(b), 0.35f, 2, 5), "0.35");
    CHECK_STR(fmt_fixed(b, sizeof(b), 12.345f, 2, 5), "12.35");
    CHECK_STR(fmt_fixed(b, sizeof(b), 180.4f, 0, 3), "180");
    CHECK_STR(fmt_fixed(b, sizeof(b), 9.5f, 0, 3), "10");
    CHECK_STR(fmt_fixed(b, sizeof(b), -0.4f, 0, 3), "0");
    CHECK_STR(fmt_fixed(b, sizeof(b), -12.0f, 0, 3), "-12");
    CHECK_STR(fmt_fixed(b, sizeof(b), NAN, 0, 3), FMT_DASH);
    /* Too wide for the tile: an overflow marker, never a clipped number. */
    CHECK_STR(fmt_fixed(b, sizeof(b), 1000.0f, 0, 3), ">99");
    CHECK_STR(fmt_fixed(b, sizeof(b), 1000.0f, 1, 5), ">999");
    CHECK_STR(fmt_fixed(b, sizeof(b), 100.0f, 2, 5), ">99");
    CHECK_STR(fmt_fixed(b, sizeof(b), -100.0f, 0, 3), FMT_DASH);
    CHECK_STR(fmt_clock_hm(b, sizeof(b), 1795664697, -18000), "22:44");
}

static void test_fmt_krpm(void)
{
    char b[16];
    CHECK_STR(fmt_krpm(b, sizeof(b), 0), "0.0");
    CHECK_STR(fmt_krpm(b, sizeof(b), 950), "1.0");
    CHECK_STR(fmt_krpm(b, sizeof(b), 2700), "2.7");
    CHECK_STR(fmt_krpm(b, sizeof(b), 4050), "4.1"); /* half up */
    CHECK_STR(fmt_krpm(b, sizeof(b), 4049), "4.0");
    CHECK_STR(fmt_krpm(b, sizeof(b), 9999), "10.0");
    CHECK_STR(fmt_krpm(b, sizeof(b), 13500), "13.5");
    CHECK_STR(fmt_krpm(b, sizeof(b), 99999), ">99"); /* 100.0 is 5 characters */
    CHECK_STR(fmt_krpm(b, sizeof(b), NAN), FMT_DASH);
    CHECK_STR(fmt_krpm(b, sizeof(b), -1), FMT_DASH);
}

static void test_fmt_uptime_tile(void)
{
    char v[16], u[16];
    fmt_uptime_tile(1214815, v, sizeof(v), u, sizeof(u)); /* 14d 1h 26m */
    CHECK_STR(v, "14");
    CHECK_STR(u, "days");
    fmt_uptime_tile(86400, v, sizeof(v), u, sizeof(u));
    CHECK_STR(v, "1");
    CHECK_STR(u, "day");
    fmt_uptime_tile(5 * 3600 + 59 * 60, v, sizeof(v), u, sizeof(u));
    CHECK_STR(v, "5");
    CHECK_STR(u, "hours");
    fmt_uptime_tile(42 * 60 + 5, v, sizeof(v), u, sizeof(u));
    CHECK_STR(v, "42");
    CHECK_STR(u, "min");
    fmt_uptime_tile(1000.0 * 86400, v, sizeof(v), u, sizeof(u));
    CHECK_STR(v, ">99");
    fmt_uptime_tile(NAN, v, sizeof(v), u, sizeof(u));
    CHECK_STR(v, FMT_DASH);
    CHECK_STR(u, "");
}

static void test_thresholds(void)
{
    CHECK(tone_for(METRIC_GPU_C, 79.9f) == TONE_NEUTRAL);
    CHECK(tone_for(METRIC_GPU_C, 80.0f) == TONE_AMBER);
    CHECK(tone_for(METRIC_GPU_C, 89.9f) == TONE_AMBER);
    CHECK(tone_for(METRIC_GPU_C, 90.0f) == TONE_RED);
    CHECK(tone_for(METRIC_BOARD_C, 69.9f) == TONE_NEUTRAL);
    CHECK(tone_for(METRIC_BOARD_C, 70.0f) == TONE_AMBER);
    CHECK(tone_for(METRIC_BOARD_C, 80.0f) == TONE_RED);
    CHECK(tone_for(METRIC_NVME_C, 75.0f) == TONE_AMBER);
    CHECK(tone_for(METRIC_NVME_C, 85.0f) == TONE_RED);
    CHECK(tone_for(METRIC_NONE, 1000.0f) == TONE_NEUTRAL);
    CHECK(tone_for(METRIC_GPU_C, NAN) == TONE_NEUTRAL);
}

static void test_page_tiles_real_frame(void)
{
    status_frame_t f;
    CHECK(parse(REAL, &f) == FRAME_OK);
    tile_t t[TILES_PER_PAGE];

    page_tiles(PAGE_GPU, &f, t);
    CHECK_STR(t[0].label, "Usage");
    CHECK_STR(t[0].value, "0");
    CHECK_STR(t[0].unit, "%");
    CHECK(t[0].tier == TIER_XL);
    CHECK_STR(t[1].label, "Power");
    CHECK_STR(t[1].value, "4");
    CHECK_STR(t[1].unit, "W");
    CHECK_STR(t[2].label, "Temp");
    CHECK_STR(t[2].value, "44");
    CHECK_STR(t[2].unit, FMT_DEG "C");
    CHECK(t[2].tone == TONE_NEUTRAL);
    CHECK_STR(t[3].label, "Running");
    CHECK_STR(t[3].value, "idle");
    CHECK(t[3].is_text && t[3].tier == TIER_M);

    page_tiles(PAGE_COOLING, &f, t);
    CHECK_STR(t[0].label, "Fan 1");
    CHECK_STR(t[0].value, "2.8"); /* 2790 */
    CHECK_STR(t[0].unit, "kRPM");
    CHECK(t[0].tier == TIER_L);
    CHECK_STR(t[1].value, "4.2"); /* 4185 */
    CHECK_STR(t[2].label, "Board");
    CHECK_STR(t[2].value, "46"); /* 45.8 */
    CHECK_STR(t[3].label, "SSD");
    CHECK_STR(t[3].value, "45"); /* 44.9 */

    page_tiles(PAGE_HOST, &f, t);
    CHECK_STR(t[0].label, "CPU");
    CHECK_STR(t[0].value, "0");
    CHECK_STR(t[1].label, "Memory");
    CHECK_STR(t[1].value, "7.2");
    CHECK_STR(t[1].unit, "/ 122 GiB");
    CHECK(t[1].tier == TIER_M);
    CHECK_STR(t[2].label, "Load avg");
    CHECK_STR(t[2].value, "0.35");
    CHECK_STR(t[3].label, "Up");
    CHECK_STR(t[3].value, "14");
    CHECK_STR(t[3].unit, "days");

    /* Every value fits its tier, for every page. */
    for (int p = 0; p < PAGE_COUNT; p++) {
        page_tiles((page_id_t)p, &f, t);
        for (int i = 0; i < TILES_PER_PAGE; i++) {
            CHECK(t[i].is_text || (int)strlen(t[i].value) <= tier_width(t[i].tier));
        }
    }
}

static void test_page_tiles_busy_and_missing(void)
{
    status_frame_t f;
    status_frame_clear(&f);
    f.gpu_pct = 97;
    f.gpu_w = 180.4f;
    f.gpu_c = 85;
    f.fan0_rpm = 9000;
    f.fan1_rpm = 13500;
    f.board_c = 81;
    snprintf(f.occ, sizeof(f.occ), "llama.cpp");
    tile_t t[TILES_PER_PAGE];

    page_tiles(PAGE_GPU, &f, t);
    CHECK_STR(t[0].value, "97");
    CHECK_STR(t[1].value, "180");
    CHECK_STR(t[2].value, "85");
    CHECK(t[2].tone == TONE_AMBER);
    CHECK_STR(t[3].value, "llama.cpp");
    CHECK(t[3].tier == TIER_TEXT); /* 9 characters: too long for 66 px */

    page_tiles(PAGE_COOLING, &f, t);
    CHECK_STR(t[0].value, "9.0");
    CHECK_STR(t[1].value, "13.5");
    CHECK(t[2].tone == TONE_RED);
    CHECK_STR(t[3].value, FMT_DASH); /* missing NVMe */
    CHECK(t[3].tone == TONE_NEUTRAL);

    page_tiles(PAGE_HOST, &f, t);
    for (int i = 0; i < TILES_PER_PAGE; i++) {
        CHECK_STR(t[i].value, FMT_DASH);
    }
    CHECK_STR(t[1].unit, "/ " FMT_DASH " GiB");

    /* Occupant unknown is a dash, not "idle". */
    f.occ[0] = '\0';
    page_tiles(PAGE_GPU, &f, t);
    CHECK_STR(t[3].value, FMT_DASH);
    CHECK(!t[3].is_text);
    /* Upper case does not fit the 66 px glyph set: it goes to 36 px. */
    snprintf(f.occ, sizeof(f.occ), "vLLM");
    page_tiles(PAGE_GPU, &f, t);
    CHECK(t[3].tier == TIER_TEXT);
    snprintf(f.occ, sizeof(f.occ), "vllm");
    page_tiles(PAGE_GPU, &f, t);
    CHECK(t[3].tier == TIER_M);
}

static void test_carousel(void)
{
    carousel_t c;
    int64_t t0 = 1000;
    carousel_init(&c, t0);
    CHECK(carousel_view(&c) == VIEW_NO_DATA); /* nothing received yet */
    carousel_tick(&c, LINK_LIVE, t0);
    CHECK(carousel_view(&c) == VIEW_PAGE);
    CHECK(c.page == PAGE_GPU);

    /* Advance at 10 s, not before. */
    carousel_tick(&c, LINK_LIVE, t0 + CAROUSEL_PAGE_MS - 1);
    CHECK(c.page == PAGE_GPU);
    carousel_tick(&c, LINK_LIVE, t0 + CAROUSEL_PAGE_MS);
    CHECK(c.page == PAGE_COOLING);
    int64_t t1 = t0 + CAROUSEL_PAGE_MS;

    /* Tap advances and restarts the timer. */
    carousel_tap(&c, t1 + 4000);
    CHECK(c.page == PAGE_HOST);
    carousel_tick(&c, LINK_LIVE, t1 + 4000 + CAROUSEL_PAGE_MS - 1);
    CHECK(c.page == PAGE_HOST);
    carousel_tick(&c, LINK_LIVE, t1 + 4000 + CAROUSEL_PAGE_MS);
    CHECK(c.page == PAGE_GPU); /* wraps */
    int64_t t2 = t1 + 4000 + CAROUSEL_PAGE_MS;

    /* STALE keeps cycling. */
    carousel_tick(&c, LINK_STALE, t2 + CAROUSEL_PAGE_MS);
    CHECK(c.page == PAGE_COOLING);
    int64_t t3 = t2 + CAROUSEL_PAGE_MS;

    /* Long press opens detail; the carousel holds still under it. */
    carousel_long_press(&c, t3 + 1000);
    CHECK(carousel_view(&c) == VIEW_DETAIL);
    carousel_tick(&c, LINK_LIVE, t3 + 1000 + CAROUSEL_DETAIL_MS - 1);
    CHECK(carousel_view(&c) == VIEW_DETAIL);
    CHECK(c.page == PAGE_COOLING);
    /* The timeout returns to the same page, with a fresh timer. */
    int64_t t4 = t3 + 1000 + CAROUSEL_DETAIL_MS;
    carousel_tick(&c, LINK_LIVE, t4);
    CHECK(carousel_view(&c) == VIEW_PAGE);
    CHECK(c.page == PAGE_COOLING);
    carousel_tick(&c, LINK_LIVE, t4 + CAROUSEL_PAGE_MS - 1);
    CHECK(c.page == PAGE_COOLING);

    /* A tap closes detail without moving on. */
    carousel_long_press(&c, t4 + 100);
    carousel_tap(&c, t4 + 200);
    CHECK(carousel_view(&c) == VIEW_PAGE);
    CHECK(c.page == PAGE_COOLING);

    /* NO DATA stops cycling, closes detail and ignores touch. */
    carousel_long_press(&c, t4 + 300);
    carousel_tick(&c, LINK_NO_DATA, t4 + 400);
    CHECK(carousel_view(&c) == VIEW_NO_DATA);
    carousel_tap(&c, t4 + 500);
    carousel_long_press(&c, t4 + 600);
    carousel_tick(&c, LINK_NO_DATA, t4 + 10 * CAROUSEL_PAGE_MS);
    CHECK(carousel_view(&c) == VIEW_NO_DATA);
    CHECK(c.page == PAGE_COOLING);
    /* Frames resume: same page, fresh timer. */
    int64_t t5 = t4 + 11 * CAROUSEL_PAGE_MS;
    carousel_tick(&c, LINK_LIVE, t5);
    CHECK(carousel_view(&c) == VIEW_PAGE);
    CHECK(c.page == PAGE_COOLING);
    carousel_tick(&c, LINK_LIVE, t5 + CAROUSEL_PAGE_MS - 1);
    CHECK(c.page == PAGE_COOLING);
    carousel_tick(&c, LINK_LIVE, t5 + CAROUSEL_PAGE_MS);
    CHECK(c.page == PAGE_HOST);
}

static void test_report_hello(void)
{
    char b[300];
    size_t n = report_hello(b, sizeof(b), "0.1.0", NULL, NULL, NULL);
    CHECK_STR(b, "{\"hello\":\"dgx-status-display\",\"fw\":\"0.1.0\"}\n");
    CHECK(n == strlen(b));
    report_hello(b, sizeof(b), "0.1.0", "1a2b3c4d5e6f7a8b", "poweron", "");
    CHECK_STR(b, "{\"hello\":\"dgx-status-display\",\"fw\":\"0.1.0\","
                 "\"elf\":\"1a2b3c4d5e6f7a8b\",\"rst\":\"poweron\"}\n");
    /* The panic reason is escaped: quotes, backslashes, control bytes and
     * non-ASCII never break the JSON line. */
    report_hello(b, sizeof(b), "0.1.0", NULL, "task_wdt",
                 "wdt: \"main\" \\ late\n\x01\xC3\xA9");
    CHECK_STR(b, "{\"hello\":\"dgx-status-display\",\"fw\":\"0.1.0\",\"rst\":\"task_wdt\","
                 "\"why\":\"wdt: \\\"main\\\" \\\\ late ???\"}\n");
    /* A long reason is cut to REPORT_WHY_MAX characters. */
    char why[300];
    memset(why, 'x', sizeof(why) - 1);
    why[sizeof(why) - 1] = '\0';
    n = report_hello(b, sizeof(b), "0.1.0", NULL, "panic", why);
    CHECK(n > 0 && n < sizeof(b));
    CHECK(strstr(b, "\"why\":\"") != NULL);
    CHECK(strlen(strstr(b, "\"why\":\"")) == strlen("\"why\":\"") + REPORT_WHY_MAX + 3);
    /* Too small a buffer: nothing, never a cut line. */
    CHECK(report_hello(b, 20, "0.1.0", NULL, NULL, NULL) == 0);
    CHECK_STR(b, "");
}

static void test_tile_colors_and_levels(void)
{
    status_frame_t f;
    CHECK(parse(REAL, &f) == FRAME_OK);
    tile_t t[TILES_PER_PAGE];

    page_tiles(PAGE_GPU, &f, t);
    CHECK(t[0].hue == HUE_TEAL);
    CHECK_NEAR(t[0].fill, 0.0f); /* 0 % */
    CHECK(t[1].hue == HUE_INDIGO);
    CHECK_NEAR(t[1].fill, 4.0f / POWER_FULL_SCALE_W);
    CHECK(t[2].hue == HUE_COOL && t[2].fill < 0); /* temps: hue, no fill */
    CHECK(t[3].hue == HUE_BRONZE && t[3].fill < 0);
    CHECK(t[0].hist == HIST_GPU_PCT && t[1].hist == HIST_GPU_W && t[2].hist == HIST_GPU_C);
    CHECK(t[3].hist == HIST_NONE); /* text tiles have no chart */

    page_tiles(PAGE_COOLING, &f, t);
    CHECK(t[0].hue == HUE_VIOLET && t[1].hue == HUE_VIOLET);
    CHECK(t[0].hist == HIST_FAN0 && t[1].hist == HIST_FAN1);
    CHECK(t[2].hist == HIST_BOARD_C && t[3].hist == HIST_NVME_C);
    CHECK_NEAR(t[0].fill, 2790.0f / FAN_FULL_SCALE_RPM);

    page_tiles(PAGE_HOST, &f, t);
    CHECK(t[0].hue == HUE_TEAL);
    CHECK_NEAR(t[1].fill, 7.2f / 121.7f);
    CHECK(t[2].hue == HUE_BRONZE && t[2].fill < 0); /* load: no CPU count */
    CHECK(t[3].hue == HUE_BRONZE && t[3].fill < 0);
    CHECK(t[0].hist == HIST_CPU_PCT && t[1].hist == HIST_MEM_GIB);
    CHECK(t[2].hist == HIST_NONE && t[3].hist == HIST_NONE); /* load: no scale */

    /* Temperature hue follows the threshold table; fills clamp to 0..1. */
    f.gpu_c = 85;
    f.board_c = 82;
    f.nvme_c = 69.9f;
    f.gpu_w = 400;
    f.fan1_rpm = 20000;
    f.gpu_pct = NAN;
    page_tiles(PAGE_GPU, &f, t);
    CHECK(t[2].hue == HUE_WARM);
    CHECK_NEAR(t[1].fill, 1.0f);
    CHECK(t[0].fill < 0); /* unknown: no fill, not an empty one */
    f.gpu_c = 92;
    page_tiles(PAGE_GPU, &f, t);
    CHECK(t[2].hue == HUE_HOT);
    page_tiles(PAGE_COOLING, &f, t);
    CHECK(t[2].hue == HUE_HOT);
    CHECK(t[3].hue == HUE_COOL);
    CHECK_NEAR(t[1].fill, 1.0f);
    CHECK(hue_colors(HUE_WARM)->edge != 0 && hue_colors(HUE_HOT)->edge != 0);
    CHECK(hue_colors(HUE_COOL)->edge == 0);
}

/* WCAG 2 relative luminance and contrast ratio. */
static double channel(uint32_t c8)
{
    double c = (double)c8 / 255.0;
    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);
}

static double luminance(uint32_t rgb)
{
    return 0.2126 * channel((rgb >> 16) & 0xFF) + 0.7152 * channel((rgb >> 8) & 0xFF) +
           0.0722 * channel(rgb & 0xFF);
}

static double contrast(uint32_t a, uint32_t b)
{
    double la = luminance(a), lb = luminance(b);
    double hi = la > lb ? la : lb, lo = la > lb ? lb : la;
    return (hi + 0.05) / (lo + 0.05);
}

static double g_worst_value = 99, g_worst_label = 99;
static char g_worst_value_pair[64], g_worst_label_pair[64];

static void check_pair(const char *what, uint32_t fg, uint32_t bg, double min, bool is_value)
{
    double r = contrast(fg, bg);
    g_checks++;
    if (r < min) {
        g_failed++;
        fprintf(stderr, "FAIL contrast %s: #%06X on #%06X is %.2f:1, needs %.1f:1\n", what,
                (unsigned)fg, (unsigned)bg, r, min);
    }
    double *worst = is_value ? &g_worst_value : &g_worst_label;
    char *pair = is_value ? g_worst_value_pair : g_worst_label_pair;
    if (r < *worst) {
        *worst = r;
        snprintf(pair, 64, "%s #%06X on #%06X", what, (unsigned)fg, (unsigned)bg);
    }
}

static void test_contrast(void)
{
    static const char *const names[HUE_COUNT] = {"teal",   "indigo", "violet", "bronze",
                                                 "cool",   "warm",   "hot"};
    for (int h = 0; h < HUE_COUNT; h++) {
        const hue_colors_t *c = hue_colors((hue_t)h);
        /* A value or a label can sit over the base, the fill or the line. */
        const uint32_t grounds[3] = {c->base, c->fill, c->line};
        for (int g = 0; g < 3; g++) {
            char what[48];
            snprintf(what, sizeof(what), "value on %s %d", names[h], g);
            check_pair(what, COLOR_VALUE, grounds[g], 7.0, true);
            snprintf(what, sizeof(what), "label on %s %d", names[h], g);
            check_pair(what, c->tint, grounds[g], 4.5, false);
        }
        /* Each step up must be visible: fill over base, line over fill. */
        CHECK(luminance(c->fill) > luminance(c->base));
        CHECK(luminance(c->line) > luminance(c->fill));
    }
    /* Text on the black ground: strip, NO DATA page, detail page. */
    check_pair("title", COLOR_TITLE, COLOR_GROUND, 4.5, false);
    check_pair("clock", COLOR_CLOCK, COLOR_GROUND, 4.5, false);
    check_pair("stale", COLOR_STALE, COLOR_GROUND, 4.5, false);
    check_pair("no data head", COLOR_NO_DATA_HEAD, COLOR_GROUND, 4.5, false);
    check_pair("text", COLOR_TEXT, COLOR_GROUND, 4.5, false);
    check_pair("muted text", COLOR_TEXT_MUTED, COLOR_GROUND, 4.5, false);
    check_pair("no data age", COLOR_VALUE, COLOR_GROUND, 7.0, true);
    printf("contrast: worst value %.2f:1 (%s); worst label %.2f:1 (%s)\n", g_worst_value,
           g_worst_value_pair, g_worst_label, g_worst_label_pair);
}

static void test_history_ring(void)
{
    static history_t h;
    hist_init(&h);
    CHECK(h.count == 0);
    CHECK(hist_at(&h, HIST_GPU_PCT, 0) == HIST_GAP); /* empty */

    status_frame_t f;
    status_frame_clear(&f);
    int64_t t = 5000;
    for (int i = 0; i < 130; i++) { /* wraps: 130 > HIST_LEN */
        f.gpu_pct = (float)i;
        hist_push_frame(&h, &f, t);
        t += 1000;
    }
    CHECK(h.count == HIST_LEN);
    CHECK(hist_at(&h, HIST_GPU_PCT, 0) == 100);   /* oldest kept: i = 10, x10 */
    CHECK(hist_at(&h, HIST_GPU_PCT, HIST_LEN - 1) == 1290); /* newest: 129 */
    CHECK(hist_at(&h, HIST_GPU_W, 5) == HIST_GAP); /* never sent: a gap */

    /* Jitter within half a period leaves no gap. */
    hist_push_frame(&h, &f, t + 400);
    CHECK(hist_at(&h, HIST_GPU_PCT, HIST_LEN - 2) != HIST_GAP);
    /* 16 s of silence: 15 gaps, then the new sample. */
    t += 400 + 16000;
    f.gpu_pct = 50;
    hist_push_frame(&h, &f, t);
    CHECK(hist_at(&h, HIST_GPU_PCT, HIST_LEN - 1) == 500);
    for (int i = 2; i <= 16; i++) {
        CHECK(hist_at(&h, HIST_GPU_PCT, HIST_LEN - i) == HIST_GAP);
    }
    CHECK(hist_at(&h, HIST_GPU_PCT, HIST_LEN - 17) != HIST_GAP);
    /* An hour of silence: the whole chart is gaps but the newest. */
    hist_push_frame(&h, &f, t + 3600 * 1000LL);
    for (int i = 0; i < HIST_LEN - 1; i++) {
        CHECK(hist_at(&h, HIST_GPU_PCT, i) == HIST_GAP);
    }
    CHECK(sizeof(h.s) == 2160);
}

static void test_history_heights(void)
{
    static history_t h;
    hist_init(&h);
    status_frame_t f;
    status_frame_clear(&f);
    int16_t out[HIST_LEN];

    /* Empty: every column is blank, and the metric still has a scale. */
    CHECK(hist_heights(&h, HIST_GPU_C, &f, 200, out));
    CHECK(out[HIST_LEN - 1] == -1);

    const float temps[] = {20, 60, 100, 10, 150, NAN};
    for (int i = 0; i < 6; i++) {
        f.gpu_c = temps[i];
        hist_push_frame(&h, &f, 1000 * (i + 1));
    }
    CHECK(hist_heights(&h, HIST_GPU_C, &f, 200, out));
    /* Six samples fill the six right-hand columns, oldest first. */
    CHECK(out[HIST_LEN - 7] == -1);
    CHECK(out[HIST_LEN - 6] == 0);    /* 20 C: the bottom of 20-100 */
    CHECK(out[HIST_LEN - 5] == 100);  /* 60 C: half */
    CHECK(out[HIST_LEN - 4] == 200);  /* 100 C: the top */
    CHECK(out[HIST_LEN - 3] == 0);    /* 10 C: below the scale, clamped */
    CHECK(out[HIST_LEN - 2] == 200);  /* 150 C: above, clamped */
    CHECK(out[HIST_LEN - 1] == -1);   /* missing: a gap, not zero */

    /* Fans keep whole RPM: 13500 is the top, 6750 half. */
    f.fan0_rpm = 6750;
    hist_push_frame(&h, &f, 7000);
    CHECK(hist_heights(&h, HIST_FAN0, &f, 100, out));
    CHECK(out[HIST_LEN - 1] == 50);
    /* Memory scales to the frame's total; no total, no chart. */
    f.mem_used_gib = 30.5f;
    f.mem_total_gib = 122;
    hist_push_frame(&h, &f, 8000);
    CHECK(hist_heights(&h, HIST_MEM_GIB, &f, 122, out));
    CHECK(out[HIST_LEN - 1] == 31);
    f.mem_total_gib = NAN;
    CHECK(!hist_heights(&h, HIST_MEM_GIB, &f, 122, out));
    CHECK(out[HIST_LEN - 1] == -1);
    CHECK(!hist_heights(&h, HIST_NONE, &f, 100, out));
}

typedef struct {
    bool has;
    uint8_t value;
    int saves;
    bool fail_save;
} fake_store_t;

static bool fake_load(void *ctx, uint8_t *out)
{
    fake_store_t *s = ctx;
    if (s->has) {
        *out = s->value;
    }
    return s->has;
}

static bool fake_save(void *ctx, uint8_t v)
{
    fake_store_t *s = ctx;
    s->saves++;
    if (s->fail_save) {
        return false;
    }
    s->has = true;
    s->value = v;
    return true;
}

static void test_settings(void)
{
    fake_store_t fs = {0};
    settings_store_t st = {fake_load, fake_save, &fs};
    CHECK(settings_chart_style(&st) == CHART_LEVEL); /* nothing stored */
    CHECK(settings_chart_style(NULL) == CHART_LEVEL);
    chart_style_t c = settings_toggle_chart_style(&st, CHART_LEVEL);
    CHECK(c == CHART_HISTORY && fs.saves == 1 && fs.value == CHART_HISTORY);
    CHECK(settings_chart_style(&st) == CHART_HISTORY); /* survives a "reboot" */
    c = settings_toggle_chart_style(&st, c);
    CHECK(c == CHART_LEVEL && settings_chart_style(&st) == CHART_LEVEL);
    fs.value = 7; /* garbage in flash */
    CHECK(settings_chart_style(&st) == CHART_LEVEL);
    fs.fail_save = true;
    CHECK(settings_toggle_chart_style(&st, CHART_LEVEL) == CHART_HISTORY);
    CHECK_STR(chart_style_name(CHART_LEVEL), "Level");
    CHECK_STR(chart_style_name(CHART_HISTORY), "2-minute history");
}

int main(void)
{
    test_parse_real_frame();
    test_parse_minimal_frame_is_all_unknown();
    test_parse_rejects();
    test_parse_bad_optional_fields_become_unknown();
    test_parse_size_cap();
    test_parse_truncates_and_caps();
    test_parse_nesting_depth();
    test_line_reader();
    test_line_reader_cap();
    test_link_state();
    test_fmt();
    test_burnin();
    test_fmt_fixed();
    test_fmt_krpm();
    test_fmt_uptime_tile();
    test_thresholds();
    test_page_tiles_real_frame();
    test_page_tiles_busy_and_missing();
    test_carousel();
    test_report_hello();
    test_tile_colors_and_levels();
    test_contrast();
    test_history_ring();
    test_history_heights();
    test_settings();
    printf("%d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
