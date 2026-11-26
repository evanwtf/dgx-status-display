/*
 * dgx-status-display: a status screen for a DGX Spark on the Waveshare
 * ESP32-C6-Touch-AMOLED-2.16. The host daemon sends one JSON frame per
 * second over USB-Serial-JTAG (docs/protocol.md).
 */
#include "display.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "serial_rx.h"
#include "ui.h"

static const char *TAG = "main";

void app_main(void)
{
    ESP_LOGI(TAG, "dgx-status-display %s", esp_app_get_description()->version);
    ESP_ERROR_CHECK(serial_rx_start());
    ESP_ERROR_CHECK(display_init());
    if (lvgl_port_lock(0)) {
        ui_init();
        lvgl_port_unlock();
    }
}
