#include "pmu_key.h"

uint8_t pmu_key_pwroff_value(uint8_t reg22)
{
    return (uint8_t)(reg22 & (uint8_t)~PMU_PWROFF_KEY_BIT);
}

uint8_t pmu_key_level_value(uint8_t reg27)
{
    return (uint8_t)((reg27 & (uint8_t)~PMU_OFFLEVEL_MASK) | PMU_OFFLEVEL_10S);
}

uint8_t pmu_key_irq_enable_value(uint8_t reg41)
{
    return (uint8_t)(reg41 | PMU_KEY_ALL);
}

uint32_t pmu_key_offlevel_ms(uint8_t reg27)
{
    return 4000u + 2000u * (uint32_t)((reg27 & PMU_OFFLEVEL_MASK) >> 2);
}

uint32_t pmu_key_irqlevel_ms(uint8_t reg27)
{
    return 1000u + 500u * (uint32_t)((reg27 >> 4) & 0x03);
}
