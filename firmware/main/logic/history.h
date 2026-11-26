/*
 * Two minutes of past readings, for the chart behind a tile. Pure C.
 *
 * One sample per accepted frame (1 Hz). A missing reading, and every second
 * with no frame, is a gap: never a zero, never interpolated. After a reboot
 * the history is empty and the chart grows from the right edge.
 *
 * The big numbers stay instantaneous readings; the chart only shows past
 * instantaneous samples.
 *
 * Samples are int16 in metric units x HIST_SCALE (see hist_metric_t), so
 * 9 metrics x 120 samples take 2160 bytes.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "status.h"

#define HIST_LEN 120
#define HIST_GAP INT16_MIN
/* A frame later than this after the previous one leaves gap samples for the
 * seconds in between. */
#define HIST_PERIOD_MS 1000

typedef enum {
    HIST_NONE = -1,
    HIST_GPU_PCT = 0,
    HIST_GPU_W,
    HIST_GPU_C,
    HIST_FAN0,
    HIST_FAN1,
    HIST_BOARD_C,
    HIST_NVME_C,
    HIST_CPU_PCT,
    HIST_MEM_GIB,
    HIST_METRICS,
} hist_metric_t;

typedef struct {
    int16_t s[HIST_METRICS][HIST_LEN];
    uint16_t head;  /* index of the next write */
    uint16_t count; /* samples held, up to HIST_LEN */
    bool started;
    int64_t last_ms;
} history_t;

void hist_init(history_t *h);
/* Record one accepted frame, received at board time now_ms. */
void hist_push_frame(history_t *h, const status_frame_t *f, int64_t now_ms);
/* Sample i of metric m, oldest first (0 .. count-1); HIST_GAP for a gap. */
int16_t hist_at(const history_t *h, hist_metric_t m, int i);

/* The fixed scale of metric m, in metric units. Memory runs to the total in
 * the frame; false when there is no scale (unknown total). */
bool hist_scale(hist_metric_t m, const status_frame_t *f, float *lo, float *hi);

/*
 * Chart column heights for metric m: out[HIST_LEN], newest at the right
 * (out[HIST_LEN - 1]). Each is 0..height pixels, clamped to the scale, or -1
 * for a gap or a column with no sample yet. False when the metric has no
 * scale; out is then all -1.
 */
bool hist_heights(const history_t *h, hist_metric_t m, const status_frame_t *f, int height,
                  int16_t out[HIST_LEN]);
