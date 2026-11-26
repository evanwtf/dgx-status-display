/*
 * The UI loop: ESP-IDF glue around view.c. Every UI_PERIOD_MS it reads the
 * serial state, steps the carousel, applies burn-in care and redraws.
 */
#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "display.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "logic/burnin.h"
#include "logic/carousel.h"
#include "logic/link_state.h"
#include "serial_rx.h"
#include "view.h"

#define UI_PERIOD_MS 250
#define STACK_LOG_MS 60000

static const char *TAG = "ui";

static carousel_t s_carousel;
static bool s_first_refresh_done;
static int s_applied_brightness = -1;
static int64_t s_last_change_ms;
static char s_last_occ[STATUS_OCC_MAX + 1];
static int s_last_state = -1;
static bool s_wdt_subscribed;
static int64_t s_last_stack_log_ms = -STACK_LOG_MS;
static uint32_t s_lvgl_stack_free;
static shift_t s_last_shift = {127, 127};
/* Static: too big for the LVGL task stack. */
static rx_snapshot_t s_rx;
static view_model_t s_model;

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* Touch callbacks run in the LVGL task, like tick(). */
static void on_tap(void)
{
    carousel_tap(&s_carousel, now_ms());
    s_last_change_ms = now_ms();
}

static void on_long_press(void)
{
    carousel_long_press(&s_carousel, now_ms());
    s_last_change_ms = now_ms();
}

/* LVGL sends REFR_READY after a refresh has been flushed to the panel. */
static void on_refresh_ready(lv_event_t *e)
{
    (void)e;
    s_first_refresh_done = true;
}

/* The LVGL task feeds the task watchdog from this timer. If the LVGL task
 * hangs, the timer stops, the watchdog fires, and the panic handler reboots
 * the board (CONFIG_ESP_TASK_WDT_PANIC). A hung UI must not sit on a stale
 * LIVE screen. */
static void feed_watchdog(void)
{
    if (!s_wdt_subscribed) {
        esp_err_t err = esp_task_wdt_add(NULL);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "task watchdog subscribe failed: %s", esp_err_to_name(err));
            return;
        }
        s_wdt_subscribed = true;
    }
    esp_task_wdt_reset();
}

static void log_stack(int64_t now)
{
    s_lvgl_stack_free = (uint32_t)uxTaskGetStackHighWaterMark(NULL);
    if (now - s_last_stack_log_ms >= STACK_LOG_MS) {
        ESP_LOGI(TAG, "stack free (high-water): rx %lu B, lvgl %lu B",
                 (unsigned long)serial_rx_stack_free(), (unsigned long)s_lvgl_stack_free);
        s_last_stack_log_ms = now;
    }
}

static void tick(lv_timer_t *t)
{
    (void)t;
    int64_t now = now_ms();
    feed_watchdog();
    log_stack(now);
    serial_rx_get(&s_rx);
    link_state_t st = link_state(&s_rx.link, now);
    carousel_tick(&s_carousel, st, now);

    shift_t sh = burnin_shift(now);
    if (sh.dx != s_last_shift.dx || sh.dy != s_last_shift.dy) {
        view_set_shift(sh.dx, sh.dy);
        s_last_shift = sh;
    }
    /* "No change" for the idle dim: no tap, no link-state change, no new GPU
     * occupant. Page turns do not count. */
    if ((int)st != s_last_state) {
        s_last_state = (int)st;
        s_last_change_ms = now;
    }
    if (strcmp(s_last_occ, s_rx.frame.occ) != 0) {
        snprintf(s_last_occ, sizeof(s_last_occ), "%s", s_rx.frame.occ);
        s_last_change_ms = now;
    }
    /* No brightness before the first frame reaches the panel: the panel
     * starts at 0 (display.c), so nothing flashes. */
    int level = burnin_brightness(st, now - s_last_change_ms);
    if (s_first_refresh_done && level != s_applied_brightness) {
        display_set_brightness((uint8_t)level);
        s_applied_brightness = level;
    }

    view_model_fill(&s_model, &s_rx.frame, st, link_age_s(&s_rx.link, now), &s_carousel);
    s_model.accepted = s_rx.accepted;
    s_model.rejected = s_rx.rejected;
    s_model.fw_version = esp_app_get_description()->version;
    s_model.stack_free_rx = serial_rx_stack_free();
    s_model.stack_free_ui = s_lvgl_stack_free;
    view_render(&s_model);
}

void ui_init(void)
{
    int64_t now = now_ms();
    carousel_init(&s_carousel, now);
    s_last_change_ms = now;
    view_init(lv_screen_active(), on_tap, on_long_press);
    lv_display_add_event_cb(lv_display_get_default(), on_refresh_ready, LV_EVENT_REFR_READY,
                            NULL);
    tick(NULL);
    lv_timer_create(tick, UI_PERIOD_MS, NULL);
}
