/*
 * Receive status frames over USB-Serial-JTAG.
 *
 * The board also writes to the host on this port:
 *  - the ROM boot banner at every reset, and ESP-IDF log lines (USB-Serial-
 *    JTAG is the secondary console, on purpose: sdkconfig.defaults);
 *  - this module: one hello line at boot (with the reset reason) and when
 *    frames resume after NO DATA.
 * The daemon reads JSON objects as messages and relays every other line to
 * its log (docs/protocol.md).
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
