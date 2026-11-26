#include "history.h"

#include <math.h>
#include <string.h>

/* Stored units: x10 for everything but fan RPM (13500 x 10 would overflow). */
static float unit_scale(hist_metric_t m)
{
    return (m == HIST_FAN0 || m == HIST_FAN1) ? 1.0f : 10.0f;
}

static float frame_value(const status_frame_t *f, hist_metric_t m)
{
    switch (m) {
    case HIST_GPU_PCT: return f->gpu_pct;
    case HIST_GPU_W: return f->gpu_w;
    case HIST_GPU_C: return f->gpu_c;
    case HIST_FAN0: return f->fan0_rpm;
    case HIST_FAN1: return f->fan1_rpm;
    case HIST_BOARD_C: return f->board_c;
    case HIST_NVME_C: return f->nvme_c;
    case HIST_CPU_PCT: return f->cpu_pct;
    case HIST_MEM_GIB: return f->mem_used_gib;
    default: return NAN;
    }
}

static int16_t encode(float v, hist_metric_t m)
{
    if (!isfinite(v)) {
        return HIST_GAP;
    }
    float s = v * unit_scale(m);
    if (s > 32767.0f) {
        s = 32767.0f;
    } else if (s < -32767.0f) {
        s = -32767.0f;
    }
    return (int16_t)lroundf(s);
}

void hist_init(history_t *h)
{
    memset(h, 0, sizeof(*h));
}

static void push_row(history_t *h, const status_frame_t *f)
{
    for (int m = 0; m < HIST_METRICS; m++) {
        h->s[m][h->head] = f ? encode(frame_value(f, (hist_metric_t)m), (hist_metric_t)m)
                             : HIST_GAP;
    }
    h->head = (uint16_t)((h->head + 1) % HIST_LEN);
    if (h->count < HIST_LEN) {
        h->count++;
    }
}

void hist_push_frame(history_t *h, const status_frame_t *f, int64_t now_ms)
{
    if (h->started) {
        /* Whole seconds with no frame: one gap each, at most a full chart. */
        int64_t missed = (now_ms - h->last_ms + HIST_PERIOD_MS / 2) / HIST_PERIOD_MS - 1;
        if (missed > HIST_LEN) {
            missed = HIST_LEN;
        }
        for (int64_t i = 0; i < missed; i++) {
            push_row(h, NULL);
        }
    }
    push_row(h, f);
    h->started = true;
    h->last_ms = now_ms;
}

int16_t hist_at(const history_t *h, hist_metric_t m, int i)
{
    if (m < 0 || m >= HIST_METRICS || i < 0 || i >= h->count) {
        return HIST_GAP;
    }
    int oldest = (h->head - h->count + HIST_LEN) % HIST_LEN;
    return h->s[m][(oldest + i) % HIST_LEN];
}

bool hist_scale(hist_metric_t m, const status_frame_t *f, float *lo, float *hi)
{
    switch (m) {
    case HIST_GPU_PCT:
    case HIST_CPU_PCT: *lo = 0; *hi = 100; return true;
    case HIST_GPU_W: *lo = 0; *hi = 240; return true; /* POWER_FULL_SCALE_W */
    case HIST_FAN0:
    case HIST_FAN1: *lo = 0; *hi = 13500; return true; /* FAN_FULL_SCALE_RPM */
    case HIST_GPU_C:
    case HIST_BOARD_C:
    case HIST_NVME_C: *lo = 20; *hi = 100; return true;
    case HIST_MEM_GIB:
        if (f && isfinite(f->mem_total_gib) && f->mem_total_gib > 0) {
            *lo = 0;
            *hi = f->mem_total_gib;
            return true;
        }
        return false;
    default: return false;
    }
}

bool hist_heights(const history_t *h, hist_metric_t m, const status_frame_t *f, int height,
                  int16_t out[HIST_LEN])
{
    for (int i = 0; i < HIST_LEN; i++) {
        out[i] = -1;
    }
    float lo, hi;
    if (!hist_scale(m, f, &lo, &hi) || hi <= lo || height <= 0) {
        return false;
    }
    int first = HIST_LEN - h->count; /* columns left of the oldest sample stay empty */
    for (int i = 0; i < h->count; i++) {
        int16_t s = hist_at(h, m, i);
        if (s == HIST_GAP) {
            continue;
        }
        float v = (float)s / unit_scale(m);
        float frac = (v - lo) / (hi - lo);
        frac = frac < 0 ? 0 : (frac > 1 ? 1 : frac);
        out[first + i] = (int16_t)lroundf(frac * (float)height);
    }
    return true;
}
