/*
 * Waveshare ESP32-C6-Touch-AMOLED-2.16: pins and bus facts.
 *
 * Source: github.com/waveshareteam/ESP32-C6-Touch-AMOLED-2.16 at commit
 * 294543798f1a44e2f2c4d2976522323f2beee11d. Three files agree on every pin:
 *   - 02_Example/ESP-IDF-v5.5.3/09_LVGL_V9_Test: DisplayPort constructor
 *     defaults (display_bsp.h) and main.cpp (I2cMasterBus(7,8,0), PMU 0x34)
 *   - 02_Example/Arduino-v3.3.3/09_LVGL_V9_Test/user_config.h
 *   - 02_Example/XiaoZhi-v2.2.5/main/boards/waveshare/
 *     esp32-c6-touch-amoled-2.16/config.h
 * The ESP-IDF demo's main/user_config.h swaps CS and touch INT (CS=5,
 * INT=15). No code in that demo reads those two defines; ignore them.
 */
#pragma once

#include "driver/gpio.h"
#include "driver/spi_master.h"

/* CO5300 AMOLED panel, QSPI on SPI2. No reset pin: AXP2101 ALDO3 cycles
 * the panel power instead. No backlight pin: brightness is register 0x51. */
#define BOARD_LCD_HOST SPI2_HOST
#define BOARD_LCD_CS GPIO_NUM_15
#define BOARD_LCD_SCLK GPIO_NUM_0
#define BOARD_LCD_D0 GPIO_NUM_1
#define BOARD_LCD_D1 GPIO_NUM_2
#define BOARD_LCD_D2 GPIO_NUM_3
#define BOARD_LCD_D3 GPIO_NUM_4
#define BOARD_LCD_H_RES 480
#define BOARD_LCD_V_RES 480

/* Shared I2C bus: AXP2101 PMU (0x34) and CST9220 touch (0x5A). */
#define BOARD_I2C_PORT I2C_NUM_0
#define BOARD_I2C_SCL GPIO_NUM_7
#define BOARD_I2C_SDA GPIO_NUM_8
#define BOARD_PMU_ADDR 0x34

/* CST9220 touch controller (driven by the cst9217 driver, as in the demo). */
#define BOARD_TOUCH_RST GPIO_NUM_11
#define BOARD_TOUCH_INT GPIO_NUM_5
