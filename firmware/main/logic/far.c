#include "far.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "fmt.h"

/* The GPU temperature's fill runs over the history chart's scale. */
#define TEMP_FILL_LO 20.0f
#define TEMP_FILL_HI 100.0f

const char *far_page_name(far_page_t p)
{
    switch (p) {
    case FAR_GPU: return "GPU";
    case FAR_POWER: return "POWER";
    case FAR_TEMP: return "TEMP";
    case FAR_LLM: return "LLM";
    case FAR_MODEL: return "MODEL";
    case FAR_MEMORY: return "MEMORY";
    case FAR_CPU: return "CPU";
    case FAR_PAGE_COUNT: break;
    }
    return "?";
}

static bool engine_up(const status_frame_t *f)
{
    return f->llm_up >= 1; /* NaN fails: unknown is not up */
}

static bool occ_named(const status_frame_t *f)
{
    return f->occ[0] != '\0' && strcmp(f->occ, "idle") != 0;
}

bool far_hidden(far_page_t p, const status_frame_t *f)
{
    switch (p) {
    case FAR_LLM: return !engine_up(f);
    case FAR_MODEL: return !engine_up(f) && !occ_named(f);
    default: return false;
    }
}

bool far_big_digits(const far_t *t)
{
    return t->slot <= 3;
}

static float level(float v, float lo, float hi)
{
    if (!isfinite(v) || !isfinite(lo) || !isfinite(hi) || hi <= lo) {
        return -1.0f;
    }
    float f = (v - lo) / (hi - lo);
    return f < 0 ? 0.0f : (f > 1 ? 1.0f : f);
}

static void upper(char *s)
{
    for (; *s; s++) {
        if (*s >= 'a' && *s <= 'z') {
            *s = (char)(*s - 'a' + 'A');
        }
    }
}

/* Copy src as ASCII: each non-ASCII UTF-8 sequence becomes one '?'. The
 * far fonts hold ASCII only. */
static void ascii_only(const char *src, char *buf, size_t cap)
{
    size_t n = 0;
    for (const unsigned char *c = (const unsigned char *)src; *c != '\0' && n + 1 < cap;) {
        if (*c < 0x80) {
            buf[n++] = (char)*c++;
            continue;
        }
        buf[n++] = '?';
        c++; /* the lead byte, or a stray continuation byte */
        while ((*c & 0xC0) == 0x80) {
            c++;
        }
    }
    buf[n] = '\0';
}

/* s[0, n) without spaces at either end, and without its "org/" part. */
static void model_name(const char *s, size_t n, char *buf, size_t cap)
{
    const char *start = s, *end = s + n;
    for (const char *c = s; c < end; c++) {
        if (*c == '/') {
            start = c + 1;
        }
    }
    while (start < end && *start == ' ') {
        start++;
    }
    while (end > start && end[-1] == ' ') {
        end--;
    }
    snprintf(buf, cap, "%.*s", (int)(end - start), start);
}

void far_name_text(const status_frame_t *f, char *buf, size_t cap)
{
    char name[FAR_TEXT_MAX];
    name[0] = '\0';
    if (engine_up(f) && f->llm_model[0] != '\0') {
        /* The first model, and "+N" for the N others: two names would not
         * fit in two lines at 81 px. */
        const char *m = f->llm_model;
        const char *comma = strchr(m, ',');
        model_name(m, comma ? (size_t)(comma - m) : strlen(m), name, sizeof(name));
        int more = 0;
        for (const char *c = comma; c != NULL; c = strchr(c + 1, ',')) {
            more++;
        }
        if (name[0] != '\0' && more > 0) {
            const size_t n = strlen(name);
            snprintf(name + n, sizeof(name) - n, " +%d", more);
        }
    } else if (occ_named(f)) {
        model_name(f->occ, strlen(f->occ), name, sizeof(name));
    }
    if (name[0] == '\0') { /* nothing known, or "org/" with no name */
        snprintf(buf, cap, "%s", FMT_DASH);
        return;
    }
    ascii_only(name, buf, cap);
    upper(buf);
}

static void number(far_t *t, float v, int decimals, int slot, const char *unit)
{
    fmt_fixed(t->value, sizeof(t->value), v, decimals, slot);
    snprintf(t->unit, sizeof(t->unit), "%s", unit);
    t->slot = slot;
}

void far_page(far_page_t p, const status_frame_t *f, far_t *out)
{
    memset(out, 0, sizeof(*out));
    out->word = far_page_name(p);
    out->tone = TONE_NEUTRAL;
    out->hue = HUE_MOSS;
    out->fill = -1.0f;
    out->hist = HIST_NONE;
    out->slot = 3;
    switch (p) {
    case FAR_GPU:
        number(out, f->gpu_pct, 0, 3, "%");
        out->hue = HUE_TEAL;
        out->fill = level(f->gpu_pct, 0, 100);
        out->hist = HIST_GPU_PCT;
        break;
    case FAR_POWER:
        number(out, f->gpu_w, 0, 3, "W");
        out->hue = HUE_INDIGO;
        out->fill = level(f->gpu_w, 0, POWER_FULL_SCALE_W);
        out->hist = HIST_GPU_W;
        break;
    case FAR_TEMP:
        number(out, f->gpu_c, 0, 3, FMT_DEG "C");
        out->tone = tone_for(METRIC_GPU_C, f->gpu_c);
        out->hue = out->tone == TONE_RED     ? HUE_HOT
                   : out->tone == TONE_AMBER ? HUE_WARM
                                             : HUE_COOL;
        out->fill = level(f->gpu_c, TEMP_FILL_LO, TEMP_FILL_HI);
        out->hist = HIST_GPU_C;
        break;
    case FAR_LLM:
        /* The near tile has no full scale for tokens/s: no fill here. */
        fmt_tokens_rate(f->llm_out_tps, out->value, sizeof(out->value), out->unit,
                        sizeof(out->unit));
        upper(out->unit);
        out->slot = 4;
        out->hue = HUE_LLM;
        break;
    case FAR_MODEL:
        out->is_text = true;
        out->slot = 0;
        far_name_text(f, out->text, sizeof(out->text));
        if (engine_up(f) && f->llm_model[0] != '\0') {
            out->hue = HUE_LLM;
        } else {
            out->word = "RUNNING"; /* a backend, not a model */
        }
        break;
    case FAR_MEMORY: {
        /* The Memory tile's value and unit: "101.4", "/ 128 GIB". */
        char total[16];
        fmt_fixed(total, sizeof(total), f->mem_total_gib, 0, 4);
        fmt_fixed(out->value, sizeof(out->value), f->mem_used_gib, 1, 5);
        snprintf(out->unit, sizeof(out->unit), "/ %s GIB", total);
        out->slot = 5;
        out->hue = HUE_INDIGO;
        out->fill = level(f->mem_used_gib, 0, f->mem_total_gib);
        out->hist = HIST_MEM_GIB;
        break;
    }
    case FAR_CPU:
        number(out, f->cpu_pct, 0, 3, "%");
        out->hue = HUE_TEAL;
        out->fill = level(f->cpu_pct, 0, 100);
        out->hist = HIST_CPU_PCT;
        break;
    case FAR_PAGE_COUNT: break;
    }
}

/* ---- Name layout ---- */

static bool fits(far_width_fn width, void *ctx, const char *s, size_t n, far_name_size_t size,
                 int max_w)
{
    const int w = width(s, n, size, ctx);
    return w >= 0 && w <= max_w;
}

/* Break class of a cut after byte k-1: 2 strong, 1 weak ('.'), 0 none. */
static int break_class(char c)
{
    if (c == ' ' || c == ',' || c == '-' || c == '_' || c == '/' || c == ':') {
        return 2;
    }
    return c == '.' ? 1 : 0;
}

/* Line one is s[0, k) less trailing spaces; line two starts at k, less
 * leading spaces. */
static size_t line1_len(const char *s, size_t k)
{
    while (k > 0 && s[k - 1] == ' ') {
        k--;
    }
    return k;
}

static const char *line2_start(const char *s, size_t k)
{
    const char *r = s + k;
    while (*r == ' ') {
        r++;
    }
    return r;
}

/* The latest break of class `cls` where line one fits and, when `need2`,
 * line two fits too. 0 when none. */
static size_t find_break(const char *s, size_t len, int cls, bool need2, far_width_fn width,
                         void *ctx, far_name_size_t size, int max_w)
{
    for (size_t k = len - 1; k >= 1; k--) {
        const bool ok_cls = cls == 0 || break_class(s[k - 1]) == cls;
        if (!ok_cls) {
            continue;
        }
        const size_t n1 = line1_len(s, k);
        const char *r = line2_start(s, k);
        if (n1 == 0 || *r == '\0') {
            continue;
        }
        if (!fits(width, ctx, s, n1, size, max_w)) {
            continue;
        }
        if (need2 && !fits(width, ctx, r, strlen(r), size, max_w)) {
            continue;
        }
        return k;
    }
    return 0;
}

static void set_lines(far_name_t *out, const char *s, size_t k)
{
    const size_t n1 = line1_len(s, k);
    snprintf(out->line[0], sizeof(out->line[0]), "%.*s", (int)n1, s);
    snprintf(out->line[1], sizeof(out->line[1]), "%s", line2_start(s, k));
    out->lines = 2;
}

static bool two_lines(const char *s, size_t len, far_width_fn width, void *ctx,
                      far_name_size_t size, int max_w, int min_cls, far_name_t *out)
{
    for (int cls = 2; cls >= min_cls; cls--) {
        const size_t k = find_break(s, len, cls, true, width, ctx, size, max_w);
        if (k > 0) {
            out->size = size;
            set_lines(out, s, k);
            return true;
        }
    }
    return false;
}

void far_name_layout(const char *name, int max_w, far_width_fn width, void *ctx,
                     far_name_t *out)
{
    memset(out, 0, sizeof(*out));
    char s[FAR_TEXT_MAX];
    while (*name == ' ') {
        name++;
    }
    snprintf(s, sizeof(s), "%s", name);
    size_t len = strlen(s);
    while (len > 0 && s[len - 1] == ' ') {
        s[--len] = '\0';
    }
    out->lines = 1;
    out->size = FAR_NAME_BIG;
    if (len == 0) {
        return;
    }
    for (int z = 0; z < FAR_NAME_SIZES; z++) {
        const far_name_size_t size = (far_name_size_t)z;
        if (fits(width, ctx, s, len, size, max_w)) {
            out->size = size;
            snprintf(out->line[0], sizeof(out->line[0]), "%s", s);
            return;
        }
        if (len > 1 && two_lines(s, len, width, ctx, size, max_w, 2, out)) {
            return;
        }
    }
    /* No clean break: after a '.' (it splits a version: "V2." "6"), then
     * anywhere. Small only. */
    if (len > 1 && two_lines(s, len, width, ctx, FAR_NAME_SMALL, max_w, 0, out)) {
        return;
    }
    /* Too long for two small lines: the latest break where line one fits,
     * and line two cut to fit with "...". */
    out->size = FAR_NAME_SMALL;
    out->cut = true;
    size_t k = 0;
    for (int cls = 2; cls >= 0 && k == 0; cls--) {
        k = len > 1 ? find_break(s, len, cls, false, width, ctx, FAR_NAME_SMALL, max_w) : 0;
    }
    if (k == 0) {
        /* No break where line one fits: one line, cut with "...". */
        out->lines = 1;
        char *l1 = out->line[0];
        snprintf(l1, sizeof(out->line[0]), "%s", s);
        size_t m = strlen(l1);
        if (m > FAR_TEXT_MAX - 1) {
            m = FAR_TEXT_MAX - 1;
        }
        for (; m > 0; m--) {
            memcpy(l1 + m, "...", 4);
            if (l1[m - 1] != ' ' && fits(width, ctx, l1, m + 3, FAR_NAME_SMALL, max_w)) {
                return;
            }
        }
        memcpy(l1, "...", 4);
        return;
    }
    set_lines(out, s, k);
    /* Cut line two to n characters and "...", n as large as fits. Line
     * buffers hold FAR_TEXT_MAX + 4 bytes, room for the dots. */
    char *l2 = out->line[1];
    size_t n = strlen(l2);
    if (n > FAR_TEXT_MAX - 1) {
        n = FAR_TEXT_MAX - 1;
    }
    for (; n > 0; n--) {
        memcpy(l2 + n, "...", 4);
        /* No space before the dots. */
        if (l2[n - 1] != ' ' && fits(width, ctx, l2, n + 3, FAR_NAME_SMALL, max_w)) {
            return;
        }
    }
    memcpy(l2, "...", 4);
}
