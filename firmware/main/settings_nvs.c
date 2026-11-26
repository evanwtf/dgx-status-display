#include "settings_nvs.h"

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "settings";
static const char *NS = "dgxstatus";
static bool s_ready;

/* ctx is the NVS key. */
static bool nvs_load_u8(void *ctx, uint8_t *out)
{
    const char *key = ctx;
    nvs_handle_t h;
    if (!s_ready || nvs_open(NS, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    esp_err_t err = nvs_get_u8(h, key, out);
    nvs_close(h);
    return err == ESP_OK;
}

static bool nvs_save_u8(void *ctx, uint8_t value)
{
    const char *key = ctx;
    nvs_handle_t h;
    if (!s_ready || nvs_open(NS, NVS_READWRITE, &h) != ESP_OK) {
        return false;
    }
    esp_err_t err = nvs_set_u8(h, key, value);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "saving %s failed: %s", key, esp_err_to_name(err));
    }
    return err == ESP_OK;
}

static const settings_store_t s_chart = {nvs_load_u8, nvs_save_u8, (void *)"chart"};
static const settings_store_t s_turn = {nvs_load_u8, nvs_save_u8, (void *)"turn"};

static void ensure_ready(void)
{
    if (!s_ready) {
        esp_err_t err = nvs_flash_init();
        if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
            ESP_LOGW(TAG, "NVS layout changed; erasing it");
            if (nvs_flash_erase() == ESP_OK) {
                err = nvs_flash_init();
            }
        }
        s_ready = err == ESP_OK;
        if (!s_ready) {
            ESP_LOGE(TAG, "NVS unavailable (%s); settings will not persist",
                     esp_err_to_name(err));
        }
    }
}

const settings_store_t *settings_nvs_chart_store(void)
{
    ensure_ready();
    return &s_chart;
}

const settings_store_t *settings_nvs_turn_store(void)
{
    ensure_ready();
    return &s_turn;
}
