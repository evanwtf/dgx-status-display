/*
 * Minimum AXP2101 PMU setup for the panel. The register values come from the
 * XiaoZhi board file for this board (MIT):
 * https://github.com/78/xiaozhi-esp32/blob/a31349491ba9805c5e7b7f7b4dc6f33172cdc655/main/boards/waveshare/esp32-c6-touch-amoled-2.16/esp32-c6-touch-amoled-2.16.cc#L29-L59
 * and match Waveshare's C6 IDF demo (components/pmicpower/power_bsp.cpp).
 */
#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

/* Check the chip ID (0x4A). On a mismatch return ESP_ERR_NOT_FOUND and write
 * nothing. Otherwise set DCDC1 and ALDO1-4 to 3.3 V where they differ, and
 * switch ALDO1-4 on. */
esp_err_t axp2101_init(i2c_master_bus_handle_t bus);

/* ALDO3 powers the panel. Off, then on: the panel's power-on reset.
 * ESP_ERR_INVALID_STATE without a verified PMU. */
esp_err_t axp2101_panel_power_cycle(void);

/* Power key. axp2101_init() turns key power-off off (REG 22 bit 1), sets the
 * power-off hold to 10 s (REG 27, the maximum, in case bit 1 does not
 * stick) and turns on the four PWRON IRQs (REG 41). */

/* Read and clear the PWRON IRQ bits (REG 49, PMU_KEY_* in logic/pmu_key.h).
 * Call from the LVGL task: touch uses the same bus there. *bits is 0 when
 * nothing happened. ESP_ERR_INVALID_STATE without a verified PMU. */
esp_err_t axp2101_key_poll(uint8_t *bits);
