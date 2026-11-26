#include "buttons.h"

#include <stdbool.h>
#include <stdint.h>

#include "axp2101.h"
#include "board.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "logic/button.h"
#include "logic/pmu_key.h"
#include "lvgl.h"

/* Log lines read "button: boot down", "button: boot up (held 120 ms)". */
static const char *TAG = "button";

#define GPIO_POLL_MS 20
#define PMU_POLL_MS 50
/* Consecutive PMU read failures before the poll stops for good. */
#define PMU_MAX_FAILS 5

typedef struct {
    const char *name;
    gpio_num_t pin;
    bool active_high;
    button_t state;
} gpio_button_t;

static gpio_button_t s_buttons[] = {
    {.name = "boot", .pin = BOARD_BTN_BOOT, .active_high = false},
    {.name = "key", .pin = BOARD_BTN_KEY, .active_high = false},
    {.name = "pwr", .pin = BOARD_BTN_PWR, .active_high = true},
};
#define N_BUTTONS (sizeof(s_buttons) / sizeof(s_buttons[0]))

static void (*s_on_key_short)(void);
static uint32_t s_last_pmu_ms;
static int s_pmu_fails;

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static bool read_pressed(const gpio_button_t *b)
{
    return (gpio_get_level(b->pin) != 0) == b->active_high;
}

static void poll_pmu(void)
{
    if (s_pmu_fails >= PMU_MAX_FAILS) {
        return;
    }
    uint8_t bits = 0;
    const esp_err_t err = axp2101_key_poll(&bits);
    if (err == ESP_ERR_INVALID_STATE) {
        s_pmu_fails = PMU_MAX_FAILS; /* no PMU: nothing to poll */
        return;
    }
    if (err != ESP_OK) {
        if (++s_pmu_fails == PMU_MAX_FAILS) {
            ESP_LOGE(TAG, "pwr: PMU read failed %d times (%s); PMU poll stopped",
                     PMU_MAX_FAILS, esp_err_to_name(err));
        }
        return;
    }
    s_pmu_fails = 0;
    /* One poll can hold several bits; the order within it is unknown. */
    if (bits & PMU_KEY_NEG_EDGE) {
        ESP_LOGI(TAG, "pwr pmu edge neg");
    }
    if (bits & PMU_KEY_POS_EDGE) {
        ESP_LOGI(TAG, "pwr pmu edge pos");
    }
    if (bits & PMU_KEY_SHORT) {
        ESP_LOGI(TAG, "pwr short");
    }
    if (bits & PMU_KEY_LONG) {
        ESP_LOGI(TAG, "pwr long");
    }
}

static void poll(lv_timer_t *t)
{
    (void)t;
    const uint32_t now = now_ms();
    for (size_t i = 0; i < N_BUTTONS; i++) {
        gpio_button_t *b = &s_buttons[i];
        uint32_t held = 0;
        switch (button_update(&b->state, read_pressed(b), now, &held)) {
        case BUTTON_DOWN:
            ESP_LOGI(TAG, "%s down", b->name);
            break;
        case BUTTON_UP:
            ESP_LOGI(TAG, "%s up (held %lu ms)", b->name, (unsigned long)held);
            if (b->pin == BOARD_BTN_KEY && button_is_short(held) && s_on_key_short != NULL) {
                s_on_key_short();
            }
            break;
        case BUTTON_NONE:
            break;
        }
    }
    if ((uint32_t)(now - s_last_pmu_ms) >= PMU_POLL_MS) {
        s_last_pmu_ms = now;
        poll_pmu();
    }
}

void buttons_start(void (*on_key_short)(void))
{
    s_on_key_short = on_key_short;
    /* Input only: GPIO9 is a strap pin and must never be driven. BOOT and
     * KEY have 10K pull-ups on the board; the internal one is a backstop.
     * PWR (GPIO18) is pulled up by R11 and pulled down by T1: no internal
     * pull. */
    const gpio_config_t low_active = {
        .pin_bit_mask = (1ULL << BOARD_BTN_BOOT) | (1ULL << BOARD_BTN_KEY),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    const gpio_config_t pwr = {
        .pin_bit_mask = 1ULL << BOARD_BTN_PWR,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&low_active);
    if (err == ESP_OK) {
        err = gpio_config(&pwr);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gpio setup failed: %s; buttons off", esp_err_to_name(err));
        return;
    }
    const uint32_t now = now_ms();
    for (size_t i = 0; i < N_BUTTONS; i++) {
        gpio_button_t *b = &s_buttons[i];
        const bool pressed = read_pressed(b);
        button_init(&b->state, pressed, now);
        ESP_LOGI(TAG, "%s on GPIO%d: %s at start", b->name, (int)b->pin, pressed ? "down" : "up");
    }
    s_last_pmu_ms = now;
    lv_timer_create(poll, GPIO_POLL_MS, NULL);
}
