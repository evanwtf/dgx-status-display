#include "tiles.h"

#include <math.h>
#include <stdint.h>
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
    case PAGE_NETWORK: return "Network";
    case PAGE_STORAGE: return "Storage";
    case PAGE_LLM: return "LLM";
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

/*
 * One rate formatter for both families. divs[i] is the size of units[i] in
 * the base unit (bit/s or byte/s). Rounding is half up, in integers, and is
 * done before the unit is chosen, so 999.96 kb/s shows "1.0" Mb/s, never
 * "1000" kb/s.
 */
static void fmt_rate(double v, const uint64_t *divs, const char *const *units, int n,
                     char *value, size_t vcap, char *unit, size_t ucap)
{
    if (!isfinite(v) || v < 0) {
        snprintf(value, vcap, "%s", FMT_DASH);
        snprintf(unit, ucap, "%s", "");
        return;
    }
    const uint64_t x = (uint64_t)llround(v);
    if (x == 0) {
        snprintf(value, vcap, "0");
        snprintf(unit, ucap, "%s", units[0]);
        return;
    }
    for (int i = 0; i < n; i++) {
        const uint64_t d = divs[i];
        const uint64_t tenths = (x * 10 + d / 2) / d;
        snprintf(unit, ucap, "%s", units[i]);
        if (tenths < 1000) { /* 0.0 .. 99.9 */
            snprintf(value, vcap, "%u.%u", (unsigned)(tenths / 10), (unsigned)(tenths % 10));
            return;
        }
        const uint64_t whole = (x + d / 2) / d;
        if (whole < 1000) { /* 100 .. 999 */
            snprintf(value, vcap, "%u", (unsigned)whole);
            return;
        }
    }
    snprintf(value, vcap, ">999");
}

void fmt_bits_rate(double bps, char *value, size_t vcap, char *unit, size_t ucap)
{
    static const uint64_t DIVS[] = {1000ULL, 1000000ULL, 1000000000ULL};
    static const char *const UNITS[] = {"kb/s", "Mb/s", "Gb/s"};
    fmt_rate(bps, DIVS, UNITS, 3, value, vcap, unit, ucap);
}

void fmt_bytes_rate(double bytes_s, char *value, size_t vcap, char *unit, size_t ucap)
{
    static const uint64_t DIVS[] = {1000000ULL, 1000000000ULL};
    static const char *const UNITS[] = {"MB/s", "GB/s"};
    fmt_rate(bytes_s, DIVS, UNITS, 2, value, vcap, unit, ucap);
}

bool page_hidden(page_id_t p, const status_frame_t *f)
{
    return p == PAGE_LLM && f->llm_up == 0;
}

bool llm_detail(char *buf, size_t cap, const status_frame_t *f)
{
    if (!(f->llm_up > 0)) {
        return false;
    }
    char ttft[24], spec[24];
    if (isfinite(f->llm_ttft_ms)) {
        snprintf(ttft, sizeof(ttft), "%ld ms", (long)lround(f->llm_ttft_ms));
    } else {
        snprintf(ttft, sizeof(ttft), "%s", FMT_DASH);
    }
    if (isfinite(f->llm_spec_pct)) {
        snprintf(spec, sizeof(spec), "%ld %%", (long)lround(f->llm_spec_pct));
    } else {
        snprintf(spec, sizeof(spec), "%s", FMT_DASH);
    }
    snprintf(buf, cap, "%s\nFirst token  %s\nDraft accepted  %s",
             f->llm_model[0] ? f->llm_model : FMT_DASH, ttft, spec);
    return true;
}

void fmt_tokens_rate(double tps, char *value, size_t vcap, char *unit, size_t ucap)
{
    /* Counted in tenths of a token, so the 0.1 place survives the integer
     * math below 100 tok/s. */
    static const uint64_t DIVS[] = {10ULL, 10000ULL};
    static const char *const UNITS[] = {"tok/s", "k tok/s"};
    fmt_rate(isfinite(tps) ? tps * 10.0 : tps, DIVS, UNITS, 2, value, vcap, unit, ucap);
}

char *fmt_tb(char *buf, size_t cap, double gb, int max_decimals)
{
    if (!isfinite(gb) || gb < 0) {
        snprintf(buf, cap, "%s", FMT_DASH);
        return buf;
    }
    const uint64_t g = (uint64_t)llround(gb);
    const uint64_t hundredths = (g + 5) / 10; /* 1 TB = 1000 GB */
    const uint64_t tenths = (g + 50) / 100;
    const uint64_t whole = (g + 500) / 1000;
    if (max_decimals >= 2 && hundredths < 1000) {
        snprintf(buf, cap, "%u.%02u", (unsigned)(hundredths / 100), (unsigned)(hundredths % 100));
    } else if (max_decimals >= 1 && tenths < 1000) {
        snprintf(buf, cap, "%u.%u", (unsigned)(tenths / 10), (unsigned)(tenths % 10));
    } else if (whole < 1000) {
        snprintf(buf, cap, "%u", (unsigned)whole);
    } else {
        snprintf(buf, cap, ">999");
    }
    return buf;
}

/* v / full_scale clamped to 0..1; -1 (no fill) when either is unknown. */
static float level(float v, float full_scale)
{
    if (!isfinite(v) || !isfinite(full_scale) || full_scale <= 0) {
        return -1.0f;
    }
    float f = v / full_scale;
    return f < 0 ? 0.0f : (f > 1 ? 1.0f : f);
}

static float level_d(double v, double full_scale)
{
    if (!isfinite(v) || !isfinite(full_scale) || full_scale <= 0) {
        return -1.0f;
    }
    double f = v / full_scale;
    return (float)(f < 0 ? 0.0 : (f > 1 ? 1.0 : f));
}

static hue_t tone_hue(tone_t t)
{
    switch (t) {
    case TONE_AMBER: return HUE_WARM;
    case TONE_RED: return HUE_HOT;
    case TONE_NEUTRAL: break;
    }
    return HUE_COOL;
}

static void num(tile_t *t, const char *label, float v, int decimals, tier_t tier,
                const char *unit, metric_t m)
{
    t->label = label;
    fmt_fixed(t->value, sizeof(t->value), v, decimals, tier_width(tier));
    snprintf(t->unit, sizeof(t->unit), "%s", unit);
    t->tier = tier;
    t->tone = tone_for(m, v);
    t->hue = m == METRIC_NONE ? HUE_MOSS : tone_hue(t->tone);
    t->fill = -1.0f;
    t->hist = HIST_NONE;
    t->is_text = false;
}

/* A rate tile: bits (network) or bytes (storage), a level against a full
 * scale, and a history chart. */
static void rate_tile(tile_t *t, const char *label, double v, bool bits, hue_t hue,
                      double full_scale, hist_metric_t hist)
{
    t->label = label;
    if (bits) {
        fmt_bits_rate(v, t->value, sizeof(t->value), t->unit, sizeof(t->unit));
    } else {
        fmt_bytes_rate(v, t->value, sizeof(t->value), t->unit, sizeof(t->unit));
    }
    t->tier = TIER_L;
    t->tone = TONE_NEUTRAL;
    t->hue = hue;
    t->fill = level_d(v, full_scale);
    t->hist = hist;
    t->is_text = false;
}

static void with_level(tile_t *t, hue_t hue, float v, float full_scale)
{
    t->hue = hue;
    t->fill = level(v, full_scale);
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
    t->label = "Running";
    t->unit[0] = '\0';
    t->tone = TONE_NEUTRAL;
    t->hue = HUE_MOSS;
    t->fill = -1.0f;
    t->hist = HIST_NONE;
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
        num(&out[0], "Usage", f->gpu_pct, 0, TIER_XL, "%", METRIC_NONE);
        with_level(&out[0], HUE_TEAL, f->gpu_pct, 100.0f);
        out[0].hist = HIST_GPU_PCT;
        num(&out[1], "Power", f->gpu_w, 0, TIER_XL, "W", METRIC_NONE);
        with_level(&out[1], HUE_INDIGO, f->gpu_w, POWER_FULL_SCALE_W);
        out[1].hist = HIST_GPU_W;
        num(&out[2], "Temp", f->gpu_c, 0, TIER_XL, FMT_DEG "C", METRIC_GPU_C);
        out[2].hist = HIST_GPU_C;
        occupant(&out[3], f);
        break;
    case PAGE_COOLING: {
        const float rpm[2] = {f->fan0_rpm, f->fan1_rpm};
        out[0].label = "Fan 1";
        out[1].label = "Fan 2";
        for (int i = 0; i < 2; i++) {
            fmt_krpm(out[i].value, sizeof(out[i].value), rpm[i]);
            snprintf(out[i].unit, sizeof(out[i].unit), "kRPM");
            out[i].tier = TIER_L;
            with_level(&out[i], HUE_VIOLET, rpm[i], FAN_FULL_SCALE_RPM);
            out[i].hist = i == 0 ? HIST_FAN0 : HIST_FAN1;
        }
        num(&out[2], "Board", f->board_c, 0, TIER_XL, FMT_DEG "C", METRIC_BOARD_C);
        out[2].hist = HIST_BOARD_C;
        num(&out[3], "SSD", f->nvme_c, 0, TIER_XL, FMT_DEG "C", METRIC_NVME_C);
        out[3].hist = HIST_NVME_C;
        break;
    }
    case PAGE_HOST: {
        num(&out[0], "CPU", f->cpu_pct, 0, TIER_XL, "%", METRIC_NONE);
        with_level(&out[0], HUE_TEAL, f->cpu_pct, 100.0f);
        out[0].hist = HIST_CPU_PCT;
        char total[16];
        fmt_fixed(total, sizeof(total), f->mem_total_gib, 0, 4);
        num(&out[1], "Memory", f->mem_used_gib, 1, TIER_M, "", METRIC_NONE);
        snprintf(out[1].unit, sizeof(out[1].unit), "/ %s GiB", total);
        with_level(&out[1], HUE_INDIGO, f->mem_used_gib, f->mem_total_gib);
        out[1].hist = HIST_MEM_GIB;
        /* No CPU count in the frame, so no full scale: no fill. */
        num(&out[2], "Load avg", f->load1, 2, TIER_M, "", METRIC_NONE);
        out[3].label = "Up";
        out[3].tier = TIER_XL;
        out[3].hue = HUE_MOSS;
        out[3].fill = -1.0f;
        out[3].hist = HIST_NONE;
        fmt_uptime_tile(f->up_s, out[3].value, sizeof(out[3].value), out[3].unit,
                        sizeof(out[3].unit));
        break;
    }
    case PAGE_NETWORK: {
        const double link = isfinite(f->lan_mbps) ? f->lan_mbps * 1e6 : NAN;
        rate_tile(&out[0], "LAN in", f->lan_rx_bps, true, HUE_NET, link, HIST_LAN_RX);
        rate_tile(&out[1], "LAN out", f->lan_tx_bps, true, HUE_NET, link, HIST_LAN_TX);
        rate_tile(&out[2], "RoCE in", f->roce_rx_bps, true, HUE_NET, NIC_FULL_SCALE_BPS,
                  HIST_ROCE_RX);
        rate_tile(&out[3], "RoCE out", f->roce_tx_bps, true, HUE_NET, NIC_FULL_SCALE_BPS,
                  HIST_ROCE_TX);
        break;
    }
    case PAGE_STORAGE: {
        rate_tile(&out[0], "SSD read", f->ssd_rd_bytes_s, false, HUE_STORE,
                  SSD_FULL_SCALE_BYTES_S, HIST_SSD_RD);
        rate_tile(&out[1], "SSD write", f->ssd_wr_bytes_s, false, HUE_STORE,
                  SSD_FULL_SCALE_BYTES_S, HIST_SSD_WR);
        /* Used in TB, the size in the unit slot: the same pattern as Memory.
         * The fill already shows the fraction, so a % would say it twice. */
        tile_t *d = &out[2];
        d->label = "Disk used";
        fmt_tb(d->value, sizeof(d->value), f->disk_used_gb, 2);
        char size[16];
        fmt_tb(size, sizeof(size), f->disk_size_gb, 1);
        snprintf(d->unit, sizeof(d->unit), "/ %s TB", size);
        d->tier = TIER_L;
        d->hue = HUE_STORE;
        d->fill = level_d(f->disk_used_gb, f->disk_size_gb);
        d->hist = HIST_NONE; /* it moves too slowly for a 2-minute chart */
        rate_tile(&out[3], "Cluster IP", f->cip_bps, true, HUE_NET, NIC_FULL_SCALE_BPS,
                  HIST_CIP);
        break;
    }
    case PAGE_LLM: {
        const char *labels[2] = {"Output", "Prompt"};
        const double tps[2] = {f->llm_out_tps, f->llm_in_tps};
        for (int i = 0; i < 2; i++) {
            out[i].label = labels[i];
            fmt_tokens_rate(tps[i], out[i].value, sizeof(out[i].value), out[i].unit,
                            sizeof(out[i].unit));
            out[i].tier = TIER_L;
            out[i].hue = HUE_LLM;
            out[i].fill = -1.0f; /* no natural full scale for tokens/s */
            out[i].hist = HIST_NONE;
        }
        num(&out[2], "Requests", (float)f->llm_req, 0, TIER_XL, "running", METRIC_NONE);
        out[2].hue = HUE_LLM;
        num(&out[3], "KV cache", (float)f->llm_kv_pct, 0, TIER_XL, "%", METRIC_NONE);
        with_level(&out[3], HUE_LLM, (float)f->llm_kv_pct, 100.0f);
        out[3].hist = HIST_LLM_KV;
        break;
    }
    }
}
