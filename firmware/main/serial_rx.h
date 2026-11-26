/*
 * Receive status frames over USB-Serial-JTAG.
 *
 * The console is on UART0 (sdkconfig.defaults), so ESP-IDF logs never write
 * into this stream. Two things still do:
 *  - the ROM bootloader prints its boot banner on USB-Serial-JTAG at every
 *    reset, before any of this code runs (the host daemon ignores lines that
 *    are not JSON);
 *  - this module: one hello line at boot (with the reset reason) and when
 *    frames resume after NO DATA, and one diag line every 60 s. Only the
 *    RX task writes, so lines never interleave.
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "logic/link_state.h"
#include "logic/status.h"

typedef struct {
    status_frame_t frame; /* the last accepted frame */
    link_t link;
    uint32_t seq;      /* increments on each accepted frame */
    uint32_t accepted; /* frames accepted since boot */
    uint32_t rejected; /* lines rejected since boot (bad, oversize) */
} rx_snapshot_t;

esp_err_t serial_rx_start(void);

/* Copy the current state. Safe from any task. */
void serial_rx_get(rx_snapshot_t *out);

/* Lowest free stack the RX task has had, in bytes (ESP-IDF FreeRTOS counts
 * bytes). 0 before the task starts. */
uint32_t serial_rx_stack_free(void);

/* The UI task's numbers for the next diag line: its lowest free stack and
 * the LVGL heap (lv_mem_monitor). Safe from any task. */
void serial_rx_set_ui_diag(uint32_t stk_ui, uint32_t lv_used_pct, uint32_t lv_free,
                           uint32_t lv_big);
