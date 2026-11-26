/* The NVS store behind logic/settings.h: namespace "dgxstatus", one u8 per key. */
#pragma once

#include "logic/settings.h"

/* Initialize NVS (erasing it if its layout is old or full) and return the
 * store for the chart style. Always returns a store; on NVS failure it
 * loads nothing and saves nothing, so the default applies. */
const settings_store_t *settings_nvs_chart_store(void);
