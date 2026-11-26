#include "axp2101.h"

#include <stdbool.h>

#include "board.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "axp2101";

/* Register map: XPowersLib REG/AXP2101Constants.h, bundled in the Waveshare
 * demo (components/pmicpower/src). */
#define REG_CHIP_ID 0x03       /* reads 0x4A on an AXP2101 */
#define REG_DC1_VOLTAGE 0x82   /* (mV - 1500) / 100 */
#define REG_LDO_ONOFF0 0x90    /* bit0 ALDO1 .. bit3 ALDO4 */
#define REG_ALDO1_VOLTAGE 0x92 /* 0x92..0x95 = ALDO1..4: (mV - 500) / 100 */

#define CHIP_ID_AXP2101 0x4A
#define ALDO3_BIT 0x04
#define ALDO1_4_BITS 0x0F

static i2c_master_dev_handle_t s_dev;
/* Set only after the chip ID reads 0x4A. No PMU write happens before. */
static bool s_verified;

#define DC1_3V3 ((3300 - 1500) / 100)  /* 0x12 */
#define ALDO_3V3 ((3300 - 500) / 100)  /* 0x1C */
#define VOLTAGE_MASK 0x1F

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(s_dev, buf, sizeof(buf), 100);
}

static esp_err_t rd(uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, val, 1, 100);
}

/* Write a voltage register only when its value differs: the pattern of the
 * vendor IDF demo (power_bsp.cpp: if (getDC1Voltage() != 3300) set...). */
static esp_err_t set_voltage(uint8_t reg, uint8_t code)
{
    uint8_t val = 0;
    ESP_RETURN_ON_ERROR(rd(reg, &val), TAG, "read 0x%02x", reg);
    if ((val & VOLTAGE_MASK) == code) {
        return ESP_OK;
    }
    ESP_LOGW(TAG, "reg 0x%02x: 0x%02x -> 0x%02x", reg, val & VOLTAGE_MASK, code);
    return wr(reg, (uint8_t)((val & (uint8_t)~VOLTAGE_MASK) | code));
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
        /* Not the chip we expect: write nothing to it. */
        ESP_LOGE(TAG, "chip id 0x%02x, expected 0x%02x; PMU left untouched", id,
                 CHIP_ID_AXP2101);
        return ESP_ERR_NOT_FOUND;
    }

    /* DCDC1 powers the C6 itself, so it is already on. Set its voltage only
     * if it differs; never touch its enable bit. */
    ESP_RETURN_ON_ERROR(set_voltage(REG_DC1_VOLTAGE, DC1_3V3), TAG, "dc1 voltage");
    for (uint8_t i = 0; i < 4; i++) {
        ESP_RETURN_ON_ERROR(set_voltage(REG_ALDO1_VOLTAGE + i, ALDO_3V3), TAG, "aldo voltage");
    }
    uint8_t ldo = 0;
    ESP_RETURN_ON_ERROR(rd(REG_LDO_ONOFF0, &ldo), TAG, "read ldo on/off");
    if ((ldo & ALDO1_4_BITS) != ALDO1_4_BITS) {
        ESP_RETURN_ON_ERROR(wr(REG_LDO_ONOFF0, ldo | ALDO1_4_BITS), TAG, "aldo1-4 on");
    }
    s_verified = true;
    ESP_LOGI(TAG, "PMU ready (chip id 0x%02x)", id);
    return ESP_OK;
}

esp_err_t axp2101_panel_power_cycle(void)
{
    if (!s_verified) {
        return ESP_ERR_INVALID_STATE;
    }
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
