/*
 * Minimum AXP2101 PMU setup for the panel. Register writes come from
 * Waveshare's XiaoZhi board file for this board (class Pmic, see board.h).
 */
#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

/* Set DCDC1 and ALDO1-4 to 3.3 V and switch ALDO1-4 on. */
esp_err_t axp2101_init(i2c_master_bus_handle_t bus);

/* ALDO3 powers the panel. Off, then on: the panel's power-on reset. */
esp_err_t axp2101_panel_power_cycle(void);
