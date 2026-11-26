#include "serial_rx.h"

#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_core_dump.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "logic/history.h"
#include "logic/line_reader.h"
#include "logic/report.h"

static const char *TAG = "serial_rx";

#define RX_BUF_BYTES 2048
#define TX_BUF_BYTES 256
#define READ_CHUNK 256
#define RX_STACK_BYTES 4096
/* Below the LVGL port task (priority 4, ESP_LVGL_PORT_INIT_CONFIG): a flood
 * of input must not starve the display. */
#define RX_TASK_PRIORITY 3
/* At most one log line per this interval for rejected input. */
#define REJECT_LOG_MS 10000

static SemaphoreHandle_t s_lock;
static rx_snapshot_t s_state;
/* The parser's scratch frame lives here, not on the task stack. */
static status_frame_t s_scratch;
static line_reader_t s_reader;
/* Two minutes of past readings, fed by each accepted frame (s_lock). */
static history_t s_hist;
static TaskHandle_t s_task;
static int64_t s_last_reject_log_ms = -REJECT_LOG_MS;
static uint32_t s_rejects_since_log;
/* Why this boot happened, read once at start; sent in every hello. */
static const char *s_rst = "unknown";
static char s_why[REPORT_WHY_MAX + 1];
static char s_elf[17];

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static const char *reset_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_EXT: return "ext";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    case ESP_RST_EFUSE: return "efuse";
    case ESP_RST_PWR_GLITCH: return "pwr_glitch";
    case ESP_RST_CPU_LOCKUP: return "cpu_lockup";
    default: return "unknown";
    }
}

/* Reset reason, and after a crash the panic reason from the core dump in
 * flash (CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH). The dump survives the reset;
 * RTC memory would need a panic hook. */
static void read_boot_cause(void)
{
    esp_reset_reason_t r = esp_reset_reason();
    s_rst = reset_name(r);
    s_why[0] = '\0';
    bool crashed = r == ESP_RST_PANIC || r == ESP_RST_INT_WDT || r == ESP_RST_TASK_WDT ||
                   r == ESP_RST_WDT || r == ESP_RST_CPU_LOCKUP;
    if (crashed) {
        char why[200];
        if (esp_core_dump_get_panic_reason(why, sizeof(why)) == ESP_OK) {
            /* Cut to fit; report_hello() cuts and escapes it again. */
            strncpy(s_why, why, sizeof(s_why) - 1);
            s_why[sizeof(s_why) - 1] = '\0';
        }
    }
    esp_app_get_elf_sha256(s_elf, sizeof(s_elf));
    ESP_LOGW(TAG, "boot: reset reason %s%s%s", s_rst, s_why[0] ? ": " : "", s_why);
}

static void send_line(const char *line, size_t n)
{
    if (n > 0) {
        /* Short timeout: with no host reading, the line is dropped. */
        usb_serial_jtag_write_bytes(line, n, pdMS_TO_TICKS(20));
    }
}

static void send_hello(void)
{
    char line[256];
    size_t n = report_hello(line, sizeof(line), esp_app_get_description()->version, s_elf,
                            s_rst, s_why);
    send_line(line, n);
}

/* Count a rejected line. Log the first one, then at most one summary line
 * per REJECT_LOG_MS, so a noisy host cannot flood the UART. */
static void note_reject(const char *why)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_state.rejected++;
    xSemaphoreGive(s_lock);
    s_rejects_since_log++;
    int64_t now = now_ms();
    if (now - s_last_reject_log_ms >= REJECT_LOG_MS) {
        ESP_LOGW(TAG, "rejected %lu line(s) in the last %d s; latest: %s",
                 (unsigned long)s_rejects_since_log, REJECT_LOG_MS / 1000, why);
        s_last_reject_log_ms = now;
        s_rejects_since_log = 0;
    }
}

static void handle_line(const char *line, size_t len)
{
    frame_result_t rc = frame_parse(line, len, &s_scratch);
    if (rc != FRAME_OK) {
        note_reject(frame_result_name(rc));
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    int64_t now = now_ms();
    bool resumed = link_state(&s_state.link, now) == LINK_NO_DATA;
    s_state.frame = s_scratch;
    link_frame_ok(&s_state.link, now);
    hist_push_frame(&s_hist, &s_scratch, now);
    s_state.seq++;
    s_state.accepted++;
    xSemaphoreGive(s_lock);
    if (resumed) {
        send_hello();
    }
}

static void rx_task(void *arg)
{
    (void)arg;
    static uint8_t chunk[READ_CHUNK];
    send_hello();
    for (;;) {
        int n = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), pdMS_TO_TICKS(100));
        for (int i = 0; i < n; i++) {
            switch (line_reader_feed(&s_reader, chunk[i])) {
            case LINE_READY:
                handle_line(s_reader.buf, s_reader.len);
                break;
            case LINE_OVERFLOW:
                note_reject("too long");
                break;
            case LINE_NONE:
                break;
            }
        }
    }
}

esp_err_t serial_rx_start(void)
{
    s_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_lock != NULL, ESP_ERR_NO_MEM, TAG, "mutex");
    status_frame_clear(&s_state.frame);
    link_init(&s_state.link);
    line_reader_init(&s_reader);
    hist_init(&s_hist);
    read_boot_cause();

    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = RX_BUF_BYTES,
        .tx_buffer_size = TX_BUF_BYTES,
    };
    ESP_RETURN_ON_ERROR(usb_serial_jtag_driver_install(&cfg), TAG, "usb serial jtag driver");
    BaseType_t ok =
        xTaskCreate(rx_task, "serial_rx", RX_STACK_BYTES, NULL, RX_TASK_PRIORITY, &s_task);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "rx task");
    return ESP_OK;
}

void serial_rx_get(rx_snapshot_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_state;
    xSemaphoreGive(s_lock);
}

uint32_t serial_rx_stack_free(void)
{
    return s_task ? (uint32_t)uxTaskGetStackHighWaterMark(s_task) : 0;
}

bool serial_rx_hist_heights(hist_metric_t m, const status_frame_t *f, int height,
                            int16_t out[HIST_LEN])
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ok = hist_heights(&s_hist, m, f, height, out);
    xSemaphoreGive(s_lock);
    return ok;
}
