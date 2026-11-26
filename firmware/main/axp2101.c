#include "axp2101.h"

#include <stdbool.h>

#include "board.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "logic/pmu_key.h"

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

/* Write a register only when the new value differs. Logs the change. */
static esp_err_t update(uint8_t reg, uint8_t (*next)(uint8_t), uint8_t *now)
{
    uint8_t val = 0;
    ESP_RETURN_ON_ERROR(rd(reg, &val), TAG, "read 0x%02x", reg);
    const uint8_t want = next(val);
    if (want != val) {
        ESP_RETURN_ON_ERROR(wr(reg, want), TAG, "write 0x%02x", reg);
        ESP_LOGI(TAG, "reg 0x%02x: 0x%02x -> 0x%02x", reg, val, want);
    }
    *now = want;
    return ESP_OK;
}

/* Evan must not switch the board off by holding PWR by accident. */
static esp_err_t key_setup(void)
{
    uint8_t r22 = 0, r27 = 0, r41 = 0;
    ESP_RETURN_ON_ERROR(update(PMU_REG_PWROFF_EN, pmu_key_pwroff_value, &r22), TAG, "pwroff");
    ESP_RETURN_ON_ERROR(update(PMU_REG_KEY_LEVEL, pmu_key_level_value, &r27), TAG, "key level");
    ESP_RETURN_ON_ERROR(update(PMU_REG_IRQ_EN2, pmu_key_irq_enable_value, &r41), TAG, "key irq");
    /* Drop key IRQs from before this boot. */
    ESP_RETURN_ON_ERROR(wr(PMU_REG_IRQ_STS2, PMU_KEY_ALL), TAG, "clear key irq");
    ESP_LOGI(TAG, "power key: hold power-off %s, off after %lu ms, long press %lu ms",
             (r22 & PMU_PWROFF_KEY_BIT) ? "ON" : "off",
             (unsigned long)pmu_key_offlevel_ms(r27), (unsigned long)pmu_key_irqlevel_ms(r27));
    return ESP_OK;
}

const char *axp2101_key_poweroff_state(void)
{
    uint8_t r22 = 0;
    if (!s_verified || rd(PMU_REG_PWROFF_EN, &r22) != ESP_OK) {
        return "unknown";
    }
    return (r22 & PMU_PWROFF_KEY_BIT) ? "ON" : "off";
}

esp_err_t axp2101_key_poll(uint8_t *bits)
{
    *bits = 0;
    if (!s_verified) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t sts = 0;
    ESP_RETURN_ON_ERROR(rd(PMU_REG_IRQ_STS2, &sts), TAG, "read key irq");
    sts &= PMU_KEY_ALL;
    if (sts != 0) {
        /* Write 1 to clear only the key bits; VBUS and battery bits stay. */
        ESP_RETURN_ON_ERROR(wr(PMU_REG_IRQ_STS2, sts), TAG, "clear key irq");
    }
    *bits = sts;
    return ESP_OK;
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
    const esp_err_t key = key_setup();
    if (key != ESP_OK) {
        /* The panel still works; only the power-key settings are unknown. */
        ESP_LOGE(TAG, "power key setup failed: %s", esp_err_to_name(key));
    }
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
