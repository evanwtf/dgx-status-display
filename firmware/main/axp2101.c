#include "axp2101.h"

#include "board.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "axp2101";

/* Register map: XPowersLib REG/AXP2101Constants.h, bundled in the Waveshare
 * demo (components/pmicpower/src). */
#define REG_CHIP_ID 0x03       /* reads 0x4A on an AXP2101 */
#define REG_DC_ONOFF 0x80
#define REG_DC1_VOLTAGE 0x82   /* (mV - 1500) / 100 */
#define REG_LDO_ONOFF0 0x90    /* bit0 ALDO1 .. bit3 ALDO4 */
#define REG_ALDO1_VOLTAGE 0x92 /* 0x92..0x95 = ALDO1..4: (mV - 500) / 100 */

#define CHIP_ID_AXP2101 0x4A
#define ALDO3_BIT 0x04
#define ALDO1_4_BITS 0x0F
#define DC1_BIT 0x01

static i2c_master_dev_handle_t s_dev;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(s_dev, buf, sizeof(buf), 100);
}

static esp_err_t rd(uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, val, 1, 100);
}

esp_err_t axp2101_init(i2c_master_bus_handle_t bus)
{
    const i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_PMU_ADDR,
        .scl_speed_hz = 100000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus, &cfg, &s_dev), TAG, "add device");

    uint8_t id = 0;
    ESP_RETURN_ON_ERROR(rd(REG_CHIP_ID, &id), TAG, "read chip id");
    if (id != CHIP_ID_AXP2101) {
        ESP_LOGW(TAG, "chip id 0x%02x, expected 0x%02x", id, CHIP_ID_AXP2101);
    }

    /* DCDC1 3.3 V and on. Read-modify-write: leave DC2-5 as they are. */
    ESP_RETURN_ON_ERROR(wr(REG_DC1_VOLTAGE, (3300 - 1500) / 100), TAG, "dc1 voltage");
    uint8_t dc = 0;
    ESP_RETURN_ON_ERROR(rd(REG_DC_ONOFF, &dc), TAG, "read dc on/off");
    ESP_RETURN_ON_ERROR(wr(REG_DC_ONOFF, dc | DC1_BIT), TAG, "dc1 on");

    for (uint8_t i = 0; i < 4; i++) {
        ESP_RETURN_ON_ERROR(wr(REG_ALDO1_VOLTAGE + i, (3300 - 500) / 100), TAG, "aldo voltage");
    }
    uint8_t ldo = 0;
    ESP_RETURN_ON_ERROR(rd(REG_LDO_ONOFF0, &ldo), TAG, "read ldo on/off");
    ESP_RETURN_ON_ERROR(wr(REG_LDO_ONOFF0, ldo | ALDO1_4_BITS), TAG, "aldo1-4 on");
    ESP_LOGI(TAG, "PMU ready (chip id 0x%02x)", id);
    return ESP_OK;
}

esp_err_t axp2101_panel_power_cycle(void)
{
    /* Same sequence as Waveshare's DisplayPort_DispReset: on, off, on, 100 ms
     * apart. */
    uint8_t ldo = 0;
    ESP_RETURN_ON_ERROR(rd(REG_LDO_ONOFF0, &ldo), TAG, "read ldo on/off");
    ESP_RETURN_ON_ERROR(wr(REG_LDO_ONOFF0, ldo | ALDO3_BIT), TAG, "aldo3 on");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(wr(REG_LDO_ONOFF0, ldo & (uint8_t)~ALDO3_BIT), TAG, "aldo3 off");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(wr(REG_LDO_ONOFF0, ldo | ALDO3_BIT), TAG, "aldo3 on");
    vTaskDelay(pdMS_TO_TICKS(100));
    return ESP_OK;
}
