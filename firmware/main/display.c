#include "display.h"

#include <string.h>

#include "axp2101.h"
#include "board.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_sh8601.h"
#include "esp_lcd_touch_cst9217.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"

static const char *TAG = "display";

/*
 * CO5300 init sequence. The same 2.16-inch panel sequence appears in two
 * permissively licensed sources (identical except MADCTL):
 *  - Espressif esp-brookesia (CC0-1.0), commit 2bb43d86:
 *    https://github.com/espressif/esp-brookesia/blob/2bb43d86b0f5f7fd70b6323eddfd7c0476095c93/hal/brookesia_hal_boards/boards/waveshare/esp32_s3_touch_amoled_2_16/setup_device.c#L19-L37
 *  - Waveshare-ESP32-components (Apache-2.0), commit 23a6012d:
 *    https://github.com/waveshareteam/Waveshare-ESP32-components/blob/23a6012d5e87d27399cf90fb851cab3689e29159/bsp/esp32_s3_touch_amoled_2_16/esp32_s3_touch_amoled_2_16.c#L53-L71
 * Those are for the S3 board (MADCTL 0x36 = 0xA0). The C6 values (MADCTL
 * 0x36 = 0x30, which matches the C6 touch orientation flags) come from
 * Waveshare's C6 demo, 02_Example/ESP-IDF-v5.5.3/09_LVGL_V9_Test
 * (display_bsp.cpp). One change from all three: 0x51 (brightness) starts at
 * 0x00, not 0xFF, so the panel does not flash full white. ui.c sets the
 * working level once LVGL's first refresh has finished.
 */
static const sh8601_lcd_init_cmd_t s_init_cmds[] = {
    {0x11, (uint8_t[]){0x00}, 0, 600},
    {0xFE, (uint8_t[]){0x20}, 1, 0},
    {0x19, (uint8_t[]){0x10}, 1, 0},
    {0x1C, (uint8_t[]){0xA0}, 1, 0},
    {0xFE, (uint8_t[]){0x00}, 1, 0},
    {0xC4, (uint8_t[]){0x80}, 1, 0},
    {0x3A, (uint8_t[]){0x55}, 1, 0},
    {0x35, (uint8_t[]){0x00}, 1, 0},
    {0x36, (uint8_t[]){0x30}, 1, 0},
    {0x53, (uint8_t[]){0x20}, 1, 0},
    {0x51, (uint8_t[]){0x00}, 1, 0},
    {0x63, (uint8_t[]){0xFF}, 1, 0},
    {0x2A, (uint8_t[]){0x00, 0x00, 0x01, 0xDF}, 4, 0},
    {0x2B, (uint8_t[]){0x00, 0x00, 0x01, 0xDF}, 4, 0},
    {0x29, (uint8_t[]){0x00}, 0, 100},
};

/*
 * Partial draw buffers. The C6 has no PSRAM, so a full 480x480 RGB565 frame
 * (450 KiB) does not fit. Two buffers of 480 x 40 lines (37.5 KiB each) in
 * internal DMA-capable RAM: LVGL draws one while the other goes out on QSPI.
 */
#define DRAW_BUF_LINES 40

static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;

/* The CO5300 needs every flush area to start on an even pixel and end on an
 * odd one. Same rounding as the Waveshare demo's rounder_event_cb. */
static void rounder_cb(lv_area_t *area)
{
    area->x1 = (area->x1 >> 1) << 1;
    area->y1 = (area->y1 >> 1) << 1;
    area->x2 = ((area->x2 >> 1) << 1) + 1;
    area->y2 = ((area->y2 >> 1) << 1) + 1;
}

void display_set_brightness(uint8_t level)
{
    /* QSPI write command: 0x02 in the top byte, the DCS register in bits 8-15.
     * Same encoding as the demo's Set_Backlight. */
    const int cmd = (0x02 << 24) | (0x51 << 8);
    esp_err_t err = esp_lcd_panel_io_tx_param(s_io, cmd, &level, 1);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "brightness: %s", esp_err_to_name(err));
    }
}

/* ---- Bring-up ---- */

esp_err_t display_init(void)
{
    const i2c_master_bus_config_t i2c_cfg = {
        .i2c_port = BOARD_I2C_PORT,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t i2c_bus = NULL;
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c_cfg, &i2c_bus), TAG, "i2c bus");
    const esp_err_t pmu = axp2101_init(i2c_bus);
    if (pmu != ESP_OK) {
        /* Keep going: the PMU's power-on defaults may still power the panel,
         * and the rest of the firmware is still useful. */
        ESP_LOGE(TAG, "PMU init failed (%s); no panel power cycle", esp_err_to_name(pmu));
    }

    const spi_bus_config_t bus_cfg = SH8601_PANEL_BUS_QSPI_CONFIG(
        BOARD_LCD_SCLK, BOARD_LCD_D0, BOARD_LCD_D1, BOARD_LCD_D2, BOARD_LCD_D3,
        BOARD_LCD_H_RES * DRAW_BUF_LINES * sizeof(uint16_t));
    ESP_RETURN_ON_ERROR(spi_bus_initialize(BOARD_LCD_HOST, &bus_cfg, SPI_DMA_CH_AUTO), TAG,
                        "spi bus");

    const esp_lcd_panel_io_spi_config_t io_cfg = SH8601_PANEL_IO_QSPI_CONFIG(BOARD_LCD_CS, NULL, NULL);
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)BOARD_LCD_HOST, &io_cfg,
                                                 &s_io),
                        TAG, "panel io");

    sh8601_vendor_config_t vendor = {
        .init_cmds = s_init_cmds,
        .init_cmds_size = sizeof(s_init_cmds) / sizeof(s_init_cmds[0]),
        .flags.use_qspi_interface = 1,
    };
    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = GPIO_NUM_NC,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .vendor_config = &vendor,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_sh8601(s_io, &panel_cfg, &s_panel), TAG, "panel");
    if (pmu == ESP_OK) {
        ESP_RETURN_ON_ERROR(axp2101_panel_power_cycle(), TAG, "panel power");
    }
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "panel init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_panel, true), TAG, "panel on");

    /* Touch: orientation flags as in the Waveshare demo. */
    const esp_lcd_touch_config_t tp_cfg = {
        .x_max = BOARD_LCD_H_RES,
        .y_max = BOARD_LCD_V_RES,
        .rst_gpio_num = BOARD_TOUCH_RST,
        .int_gpio_num = BOARD_TOUCH_INT,
        .levels = {.reset = 0, .interrupt = 0},
        .flags = {.swap_xy = 1, .mirror_x = 0, .mirror_y = 1},
    };
    esp_lcd_panel_io_i2c_config_t tp_io_cfg = ESP_LCD_TOUCH_IO_I2C_CST9217_CONFIG();
    tp_io_cfg.scl_speed_hz = 400000;
    esp_lcd_panel_io_handle_t tp_io = NULL;
    esp_lcd_touch_handle_t touch = NULL;
    esp_err_t touch_err = esp_lcd_new_panel_io_i2c(i2c_bus, &tp_io_cfg, &tp_io);
    if (touch_err == ESP_OK) {
        touch_err = esp_lcd_touch_new_i2c_cst9217(tp_io, &tp_cfg, &touch);
    }
    if (touch_err != ESP_OK) {
        /* A dead touch controller must not take the status display down. */
        ESP_LOGE(TAG, "touch init failed: %s; continuing without touch",
                 esp_err_to_name(touch_err));
        touch = NULL;
    }

    const lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    ESP_RETURN_ON_ERROR(lvgl_port_init(&port_cfg), TAG, "lvgl port");

    const lvgl_port_display_cfg_t disp_cfg = {
        .io_handle = s_io,
        .panel_handle = s_panel,
        .buffer_size = BOARD_LCD_H_RES * DRAW_BUF_LINES,
        .double_buffer = true,
        .hres = BOARD_LCD_H_RES,
        .vres = BOARD_LCD_V_RES,
        .monochrome = false,
        .rounder_cb = rounder_cb,
        .color_format = LV_COLOR_FORMAT_RGB565,
        .rotation = {.swap_xy = false, .mirror_x = false, .mirror_y = false},
        .flags = {.buff_dma = true, .swap_bytes = true},
    };
    /* esp_lvgl_port calls esp_lcd_panel_swap_xy(panel, false) for rotation 0
     * whatever the config, and the sh8601 driver logs "swap_xy is not
     * supported" for any call. The panel needs no swap. sw_rotate would skip
     * the call, but also the mirror call that sets the panel's orientation
     * today, so keep the behavior and only hide that one log line. */
    esp_log_level_set("sh8601", ESP_LOG_NONE);
    lv_display_t *disp = lvgl_port_add_disp(&disp_cfg);
    esp_log_level_set("sh8601", ESP_LOG_INFO);
    ESP_RETURN_ON_FALSE(disp != NULL, ESP_FAIL, TAG, "lvgl display");

    if (touch != NULL) {
        const lvgl_port_touch_cfg_t touch_cfg = {.disp = disp, .handle = touch};
        if (lvgl_port_add_touch(&touch_cfg) == NULL) {
            ESP_LOGE(TAG, "lvgl touch input failed; continuing without touch");
        }
    }

    ESP_LOGI(TAG, "display ready");
    return ESP_OK;
}
