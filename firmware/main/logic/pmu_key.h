/*
 * AXP2101 power-key (PWRON) register logic. Pure, host-tested; axp2101.c does
 * the I2C. Source: AXP2101 datasheet V1.4, sections 6.13.2.19 (REG 22),
 * 6.13.2.24 (REG 27), 6.13.2.41 (REG 41) and 6.13.2.44 (REG 49). Waveshare
 * ships it in the board repo (commit 294543798f1a44e2f2c4d2976522323f2beee11d):
 * 01_Arduino_Libraries/XPowersLib/datasheet/AXP2101_Datasheet_V1.4_en.pdf
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define PMU_REG_PWROFF_EN 0x22 /* power-off sources */
#define PMU_REG_KEY_LEVEL 0x27 /* IRQLEVEL, OFFLEVEL, ONLEVEL */
#define PMU_REG_IRQ_EN2 0x41   /* datasheet "IRQ Enable 1": key and VBUS IRQs */
#define PMU_REG_IRQ_STS2 0x49  /* datasheet "IRQ Status 1": write 1 to clear */

/* REG 22 bit 1: "PWRON > OFFLEVEL as POWEROFF Source enable". Reset value
 * comes from EFUSE, so the firmware cannot assume it. */
#define PMU_PWROFF_KEY_BIT 0x02
/* REG 27 bits 3:2, OFFLEVEL: 00 4 s, 01 6 s (reset), 10 8 s, 11 10 s. */
#define PMU_OFFLEVEL_MASK 0x0C
#define PMU_OFFLEVEL_10S 0x0C

/* REG 41 and REG 49, the PWRON bits. */
#define PMU_KEY_POS_EDGE 0x01
#define PMU_KEY_NEG_EDGE 0x02
#define PMU_KEY_LONG 0x04
#define PMU_KEY_SHORT 0x08
#define PMU_KEY_ALL 0x0F

/* REG 22 with key power-off disabled. The other bits stay. */
uint8_t pmu_key_pwroff_value(uint8_t reg22);
/* REG 27 with OFFLEVEL at the 10 s maximum. The other bits stay: IRQLEVEL
 * (the long-press IRQ time) and ONLEVEL (the power-on hold). */
uint8_t pmu_key_level_value(uint8_t reg27);
/* REG 41 with the four PWRON IRQs on. The VBUS and battery bits stay. */
uint8_t pmu_key_irq_enable_value(uint8_t reg41);
/* OFFLEVEL in ms (4000..10000) from a REG 27 value. */
uint32_t pmu_key_offlevel_ms(uint8_t reg27);
/* IRQLEVEL (the long-press IRQ time) in ms (1000..2500) from REG 27. */
uint32_t pmu_key_irqlevel_ms(uint8_t reg27);
