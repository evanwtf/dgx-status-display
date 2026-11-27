/*
 * The UI loop: ESP-IDF glue around view.c. Every UI_PERIOD_MS it reads the
 * serial state, steps the carousel, applies burn-in care and redraws.
 */
#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "axp2101.h"
#include "buttons.h"
#include "display.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "logic/burnin.h"
#include "logic/carousel.h"
#include "logic/co5300.h"
#include "logic/far.h"
#include "logic/link_state.h"
#include "logic/tiles.h"
#include "logic/transition.h"
#include "serial_rx.h"
#include "settings_nvs.h"
#include "view.h"

#define UI_PERIOD_MS 250
#define STACK_LOG_MS 60000

static const char *TAG = "ui";

static carousel_t s_carousel;
static transition_t s_tr;
static lv_timer_t *s_fade_timer;
static link_state_t s_link = LINK_NO_DATA;
static uint32_t s_redraw_ms;    /* the last redraw at 0 */
static bool s_fade_logged;
static bool s_first_refresh_done;
static int s_applied_brightness = -1;
static int64_t s_last_change_ms;
static char s_last_occ[STATUS_OCC_MAX + 1];
static int s_last_state = -1;
/* The task subscribed to the task watchdog: the LVGL task, never main. */
static TaskHandle_t s_wdt_task;
static int64_t s_last_stack_log_ms = -STACK_LOG_MS;
static uint32_t s_lvgl_stack_free;
static shift_t s_last_shift = {127, 127};
/* Static: too big for the LVGL task stack. */
static rx_snapshot_t s_rx;
static view_model_t s_model;
static chart_style_t s_style = CHART_LEVEL;
static turn_t s_turn = TURN_0;
/* The mode BOOT asked for, and the mode on the panel: it changes at the
 * dark moment of the fade, like a rotation. */
static view_mode_t s_mode = MODE_FAR;
static view_mode_t s_mode_shown = MODE_FAR;
/* Render time of one LVGL refresh: the slowest since the last log line. */
static int64_t s_refr_start_us;
static uint32_t s_refr_max_ms;

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* Touch callbacks run in the LVGL task, like tick(). */
static void apply_turn(void)
{
    if (!display_can_rotate()) {
        return;
    }
    lv_display_t *d = lv_display_get_default();
    const lv_display_rotation_t r = (lv_display_rotation_t)lrot_for_cw_turns((int)s_turn);
    if (lv_display_get_rotation(d) != r) {
        lv_display_set_rotation(d, r);
    }
}

static void transition_step(int64_t now);

/* KEY, short press: turn the screen 90 degrees clockwise and store it. Runs
 * in the LVGL task (the buttons timer). */
static void on_key_short(void)
{
    if (!display_can_rotate()) {
        ESP_LOGW(TAG, "rotation unavailable: no RAM for the rotation buffer");
        return;
    }
    s_turn = settings_turn_cw(settings_nvs_turn_store(), s_turn);
    transition_turn(&s_tr); /* applied at the dark moment of a fade */
    s_last_change_ms = now_ms();
    transition_step(now_ms());
    ESP_LOGI(TAG, "rotation: %d deg", turn_degrees(s_turn));
}

static int page_count(view_mode_t m)
{
    return m == MODE_FAR ? FAR_PAGE_COUNT : PAGE_COUNT;
}

/* BOOT, short press: switch between near and far, and store it. The
 * carousel starts the new mode on its first page; the panel fades through
 * black to it. Runs in the LVGL task (the buttons timer). */
static void on_boot_short(void)
{
    s_mode = settings_toggle_mode(settings_nvs_mode_store(), s_mode);
    carousel_set_page_count(&s_carousel, page_count(s_mode), now_ms());
    transition_turn(&s_tr); /* the switch waits for the dark moment */
    s_last_change_ms = now_ms();
    transition_step(now_ms());
    ESP_LOGI(TAG, "mode %s", view_mode_name(s_mode));
}

static void on_tap(void)
{
    transition_tap(&s_tr, &s_carousel, now_ms());
    s_last_change_ms = now_ms();
    transition_step(now_ms());
}

/* The "Charts" row on the detail page: switch, store, keep the page open. */
static void on_chart_toggle(void)
{
    s_style = settings_toggle_chart_style(settings_nvs_chart_store(), s_style);
    carousel_long_press(&s_carousel, now_ms()); /* restart the detail timeout */
    s_last_change_ms = now_ms();
    ESP_LOGI(TAG, "charts: %s", chart_style_name(s_style));
}

static void on_long_press(void)
{
    transition_long_press(&s_tr, &s_carousel, now_ms());
    s_last_change_ms = now_ms();
    transition_step(now_ms());
}

/* LVGL sends REFR_READY after a refresh has been flushed to the panel. */
static void on_refresh_ready(lv_event_t *e)
{
    (void)e;
    s_first_refresh_done = true;
    if (s_refr_start_us) {
        uint32_t ms = (uint32_t)((esp_timer_get_time() - s_refr_start_us) / 1000);
        s_refr_max_ms = ms > s_refr_max_ms ? ms : s_refr_max_ms;
        s_refr_start_us = 0;
    }
}

static void on_refresh_start(lv_event_t *e)
{
    (void)e;
    s_refr_start_us = esp_timer_get_time();
}

/* The LVGL task feeds the task watchdog from this timer. If the LVGL task
 * hangs, the timer stops, the watchdog fires, and the panic handler reboots
 * the board (CONFIG_ESP_TASK_WDT_PANIC). A hung UI must not sit on a stale
 * LIVE screen.
 *
 * Only ever from the LVGL timer. esp_task_wdt_add(NULL) subscribes the
 * *calling* task. ui_init() runs in the main task, which ends when app_main
 * returns; FreeRTOS does not unsubscribe a deleted task, so a subscription
 * made there is never fed and panics the board 10 s after boot. */
static void feed_watchdog(void)
{
    TaskHandle_t self = xTaskGetCurrentTaskHandle();
    if (s_wdt_task == NULL) {
        esp_err_t err = esp_task_wdt_add(self);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "task watchdog subscribe failed: %s", esp_err_to_name(err));
            return;
        }
        s_wdt_task = self;
        ESP_LOGI(TAG, "task watchdog: subscribed %s", pcTaskGetName(self));
    }
    if (self != s_wdt_task) {
        ESP_LOGE(TAG, "watchdog fed from %s, not the subscribed task", pcTaskGetName(self));
        return;
    }
    esp_task_wdt_reset();
}

/* Stack and heap high-water marks, logged once a minute. Logs reach the
 * host over USB (the daemon relays I lines at DEBUG), so a near miss is a
 * warning there. */
#define STACK_LOW_BYTES 768
#define LV_HEAP_HIGH_PCT 85

static void log_stack(int64_t now)
{
    s_lvgl_stack_free = (uint32_t)uxTaskGetStackHighWaterMark(NULL);
    if (now - s_last_stack_log_ms < STACK_LOG_MS) {
        return;
    }
    s_last_stack_log_ms = now;
    lv_mem_monitor_t mon;
    lv_mem_monitor(&mon);
    uint32_t rx = serial_rx_stack_free();
    bool near_miss = rx < STACK_LOW_BYTES || s_lvgl_stack_free < STACK_LOW_BYTES ||
                     mon.used_pct >= LV_HEAP_HIGH_PCT;
    esp_log_level_t level = near_miss ? ESP_LOG_WARN : ESP_LOG_INFO;
    ESP_LOG_LEVEL(level, TAG,
                  "free: stack rx %lu B, lvgl %lu B; lv heap %u%% used, largest %lu B; "
                  "heap %lu B, min %lu B; slowest refresh %lu ms; turn %d; "
                  "last fade %lu ms (redraw %lu ms); pwr hold power-off %s",
                  (unsigned long)rx, (unsigned long)s_lvgl_stack_free, (unsigned)mon.used_pct,
                  (unsigned long)mon.free_biggest_size, (unsigned long)esp_get_free_heap_size(),
                  (unsigned long)esp_get_minimum_free_heap_size(),
                  (unsigned long)s_refr_max_ms, turn_degrees(s_turn),
                  (unsigned long)s_tr.last_ms, (unsigned long)s_redraw_ms,
                  axp2101_key_poweroff_state());
    s_refr_max_ms = 0;
}

static void render(int64_t now);

static void apply_shift(int64_t now)
{
    shift_t sh = burnin_shift(now);
    if (sh.dx != s_last_shift.dx || sh.dy != s_last_shift.dy) {
        view_set_shift(sh.dx, sh.dy);
        s_last_shift = sh;
    }
}

static void set_brightness(uint8_t level)
{
    /* No brightness before the first frame reaches the panel: the panel
     * starts at 0 (display.c), so nothing flashes. 0x51 goes through
     * esp_lcd_panel_io_tx_param(), which waits for every queued color
     * transaction first (esp_lcd_panel_io_spi.c, IDF v5.5.6, lines 216-224). */
    if (s_first_refresh_done && level != s_applied_brightness) {
        display_set_brightness(level);
        s_applied_brightness = level;
    }
}

/* One fade step: from tick(), touch, KEY, and every FADE_STEP_MS from the
 * fade timer while a transition runs. All in the LVGL task. */
static void transition_step(int64_t now)
{
    const uint8_t target = burnin_brightness(s_link, now - s_last_change_ms);
    const tr_out_t o = transition_tick(&s_tr, &s_carousel, target, now);
    set_brightness(o.level);
    if (o.swap) {
        s_mode_shown = s_mode;
        apply_turn();
        apply_shift(now);
        render(now);
        if (s_tr.phase == TR_DARK) {
            /* The panel is at 0: draw the whole new view now, so no partly
             * drawn frame is ever lit. */
            const int64_t t0 = esp_timer_get_time();
            lv_obj_invalidate(lv_screen_active());
            lv_refr_now(NULL);
            s_redraw_ms = (uint32_t)((esp_timer_get_time() - t0) / 1000);
            transition_swapped(&s_tr, (uint32_t)now_ms());
        }
    }
    if (o.done && !s_fade_logged) {
        s_fade_logged = true;
        ESP_LOGI(TAG, "fade: %lu ms (out %d ms, redraw %lu ms, in %d ms)",
                 (unsigned long)s_tr.last_ms, FADE_OUT_MS, (unsigned long)s_redraw_ms,
                 FADE_IN_MS);
    }
    if (s_fade_timer) {
        if (transition_busy(&s_tr)) {
            lv_timer_resume(s_fade_timer);
        } else {
            lv_timer_pause(s_fade_timer);
        }
    }
}

static void fade_tick(lv_timer_t *t)
{
    (void)t;
    transition_step(now_ms());
}

/* t is NULL only for the first call, from ui_init() in the main task. */
static void tick(lv_timer_t *t)
{
    int64_t now = now_ms();
    if (t != NULL) {
        feed_watchdog();
        log_stack(now);
    }
    serial_rx_get(&s_rx);
    link_state_t st = link_state(&s_rx.link, now);
    s_link = st;
    if (s_mode == MODE_FAR) {
        for (int p = 0; p < FAR_PAGE_COUNT; p++) {
            carousel_set_hidden(&s_carousel, (page_id_t)p,
                                far_hidden((far_page_t)p, &s_rx.frame), now);
        }
    } else {
        carousel_set_hidden(&s_carousel, PAGE_LLM, page_hidden(PAGE_LLM, &s_rx.frame), now);
    }
    carousel_tick(&s_carousel, st, now);

    /* A burn-in step that falls due during a fade waits for the dark
     * moment (apply_shift() in transition_step). */
    if (!transition_busy(&s_tr)) {
        apply_shift(now);
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
    transition_step(now);
    render(now);
}

/* Draw the view the panel shows, which lags the carousel during a fade. */
static void render(int64_t now)
{
    const carousel_t shown = transition_shown(&s_tr, &s_carousel);
    view_model_fill(&s_model, &s_rx.frame, s_link, link_age_s(&s_rx.link, now), &shown,
                    s_mode_shown);
    s_model.style = s_style;
    s_model.hist_heights = serial_rx_hist_heights;
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
    transition_init(&s_tr, &s_carousel);
    s_last_change_ms = now;
    s_style = settings_chart_style(settings_nvs_chart_store());
    s_turn = settings_turn(settings_nvs_turn_store());
    apply_turn();
    ESP_LOGI(TAG, "rotation: %d deg (stored)", turn_degrees(s_turn));
    s_mode = settings_mode(settings_nvs_mode_store()); /* far when none is stored */
    s_mode_shown = s_mode;
    carousel_set_page_count(&s_carousel, page_count(s_mode), now);
    ESP_LOGI(TAG, "mode %s (stored)", view_mode_name(s_mode));
    view_init(lv_screen_active(), on_tap, on_long_press, on_chart_toggle);
    lv_display_add_event_cb(lv_display_get_default(), on_refresh_ready, LV_EVENT_REFR_READY,
                            NULL);
    lv_display_add_event_cb(lv_display_get_default(), on_refresh_start, LV_EVENT_REFR_START,
                            NULL);
    tick(NULL);
    lv_timer_create(tick, UI_PERIOD_MS, NULL);
    s_fade_timer = lv_timer_create(fade_tick, FADE_STEP_MS, NULL);
    lv_timer_pause(s_fade_timer);
    buttons_start(on_key_short, on_boot_short);
}
