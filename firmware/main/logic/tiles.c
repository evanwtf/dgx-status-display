#include "tiles.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "fmt.h"

static const threshold_t THRESHOLDS[] = {
    {METRIC_GPU_C, 80.0f, 90.0f},
    {METRIC_BOARD_C, 70.0f, 80.0f},
    {METRIC_NVME_C, 70.0f, 80.0f},
};

int tier_width(tier_t t)
{
    switch (t) {
    case TIER_XL: return 3;
    case TIER_L: return 4;
    case TIER_M: return 5;
    case TIER_TEXT: return 9;
    }
    return 3;
}

tone_t tone_for(metric_t m, float value)
{
    if (!isfinite(value)) {
        return TONE_NEUTRAL;
    }
    for (size_t i = 0; i < sizeof(THRESHOLDS) / sizeof(THRESHOLDS[0]); i++) {
        if (THRESHOLDS[i].metric == m) {
            if (value >= THRESHOLDS[i].red) {
                return TONE_RED;
            }
            return value >= THRESHOLDS[i].amber ? TONE_AMBER : TONE_NEUTRAL;
        }
    }
    return TONE_NEUTRAL;
}

const char *page_name(page_id_t p)
{
    switch (p) {
    case PAGE_GPU: return "GPU";
    case PAGE_COOLING: return "Cooling";
    case PAGE_HOST: return "Host";
    }
    return "?";
}

static char *overflow(char *buf, size_t cap, int decimals, int width)
{
    /* ">" then nines: a true lower bound that still fits. Width 5 with one
     * decimal holds up to 999.9, so the marker is ">999"; width 3 holds 999,
     * but ">999" would not fit, so ">99". */
    int whole = width - (decimals > 0 ? decimals + 1 : 0);
    int nines = whole < width - 1 ? whole : width - 1;
    if (nines < 1) {
        nines = 1;
    }
    char tmp[16] = ">";
    for (int i = 0; i < nines && i < 14; i++) {
        tmp[i + 1] = '9';
    }
    snprintf(buf, cap, "%s", tmp);
    return buf;
}

char *fmt_fixed(char *buf, size_t cap, float v, int decimals, int width)
{
    if (!isfinite(v)) {
        snprintf(buf, cap, "%s", FMT_DASH);
        return buf;
    }
    char tmp[48];
    if (decimals <= 0) {
        snprintf(tmp, sizeof(tmp), "%ld", lroundf(v));
        if (strcmp(tmp, "-0") == 0) {
            snprintf(tmp, sizeof(tmp), "0");
        }
    } else {
        snprintf(tmp, sizeof(tmp), "%.*f", decimals, (double)v);
    }
    if ((int)strlen(tmp) > width) {
        if (v < 0) {
            snprintf(buf, cap, "%s", FMT_DASH);
            return buf;
        }
        return overflow(buf, cap, decimals, width);
    }
    snprintf(buf, cap, "%s", tmp);
    return buf;
}

char *fmt_krpm(char *buf, size_t cap, float rpm)
{
    if (!isfinite(rpm) || rpm < 0) {
        snprintf(buf, cap, "%s", FMT_DASH);
        return buf;
    }
    /* Integer math: round half up to tenths of a thousand, no float error. */
    long tenths = (lroundf(rpm) + 50) / 100;
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%ld.%ld", tenths / 10, tenths % 10);
    if (strlen(tmp) > 4) {
        return overflow(buf, cap, 1, 4);
    }
    snprintf(buf, cap, "%s", tmp);
    return buf;
}

void fmt_uptime_tile(double up_s, char *value, size_t vcap, char *unit, size_t ucap)
{
    if (!isfinite(up_s) || up_s < 0) {
        snprintf(value, vcap, "%s", FMT_DASH);
        snprintf(unit, ucap, "%s", "");
        return;
    }
    long long s = (long long)up_s;
    long long n;
    const char *u;
    if (s >= 86400) {
        n = s / 86400;
        u = n == 1 ? "day" : "days";
    } else if (s >= 3600) {
        n = s / 3600;
        u = n == 1 ? "hour" : "hours";
    } else {
        n = s / 60;
        u = "min";
    }
    if (n > 999) {
        snprintf(value, vcap, ">99");
    } else {
        snprintf(value, vcap, "%lld", n);
    }
    snprintf(unit, ucap, "%s", u);
}

static void num(tile_t *t, const char *label, float v, int decimals, tier_t tier,
                const char *unit, metric_t m)
{
    t->label = label;
    fmt_fixed(t->value, sizeof(t->value), v, decimals, tier_width(tier));
    snprintf(t->unit, sizeof(t->unit), "%s", unit);
    t->tier = tier;
    t->tone = tone_for(m, v);
    t->is_text = false;
}

/* A short lowercase name fits the 66 px tier; anything else uses 36 px. */
static bool fits_mid_tier(const char *s)
{
    size_t n = strlen(s);
    if (n == 0 || n > (size_t)tier_width(TIER_M)) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' ||
                  c == '-' || c == '_' || c == '+';
        if (!ok) {
            return false;
        }
    }
    return true;
}

static void occupant(tile_t *t, const status_frame_t *f)
{
    t->label = "On GPU";
    t->unit[0] = '\0';
    t->tone = TONE_NEUTRAL;
    if (f->occ[0] == '\0') {
        snprintf(t->value, sizeof(t->value), "%s", FMT_DASH);
        t->tier = TIER_M;
        t->is_text = false;
        return;
    }
    snprintf(t->value, sizeof(t->value), "%s", f->occ);
    t->tier = fits_mid_tier(f->occ) ? TIER_M : TIER_TEXT;
    t->is_text = true;
}

void page_tiles(page_id_t p, const status_frame_t *f, tile_t out[TILES_PER_PAGE])
{
    memset(out, 0, sizeof(tile_t) * TILES_PER_PAGE);
    switch (p) {
    case PAGE_GPU:
        num(&out[0], "GPU util", f->gpu_pct, 0, TIER_XL, "%", METRIC_NONE);
        num(&out[1], "GPU power", f->gpu_w, 0, TIER_XL, "W", METRIC_NONE);
        num(&out[2], "GPU temp", f->gpu_c, 0, TIER_XL, FMT_DEG "C", METRIC_GPU_C);
        occupant(&out[3], f);
        break;
    case PAGE_COOLING:
        out[0].label = "Fan 0";
        out[1].label = "Fan 1";
        fmt_krpm(out[0].value, sizeof(out[0].value), f->fan0_rpm);
        fmt_krpm(out[1].value, sizeof(out[1].value), f->fan1_rpm);
        for (int i = 0; i < 2; i++) {
            snprintf(out[i].unit, sizeof(out[i].unit), "kRPM");
            out[i].tier = TIER_L;
        }
        num(&out[2], "Board", f->board_c, 0, TIER_XL, FMT_DEG "C", METRIC_BOARD_C);
        num(&out[3], "NVMe", f->nvme_c, 0, TIER_XL, FMT_DEG "C", METRIC_NVME_C);
        break;
    case PAGE_HOST: {
        num(&out[0], "CPU", f->cpu_pct, 0, TIER_XL, "%", METRIC_NONE);
        char total[16];
        fmt_fixed(total, sizeof(total), f->mem_total_gib, 0, 4);
        num(&out[1], "Memory", f->mem_used_gib, 1, TIER_M, "", METRIC_NONE);
        snprintf(out[1].unit, sizeof(out[1].unit), "/ %s GiB", total);
        num(&out[2], "Load 1m", f->load1, 2, TIER_M, "", METRIC_NONE);
        out[3].label = "Uptime";
        out[3].tier = TIER_XL;
        fmt_uptime_tile(f->up_s, out[3].value, sizeof(out[3].value), out[3].unit,
                        sizeof(out[3].unit));
        break;
    }
    }
}
