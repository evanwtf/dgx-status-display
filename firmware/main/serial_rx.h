/*
 * Receive status frames over USB-Serial-JTAG.
 *
 * The console is on UART0 (sdkconfig.defaults), so ESP-IDF logs never write
 * into this stream. Two things still do:
 *  - the ROM bootloader prints its boot banner on USB-Serial-JTAG at every
 *    reset, before any of this code runs (the host daemon ignores lines that
 *    are not JSON);
 *  - this module writes one hello line at boot, and one when frames resume
 *    after NO DATA.
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
