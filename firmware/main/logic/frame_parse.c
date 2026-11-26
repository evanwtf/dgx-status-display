/* Parse a protocol v1 line with cJSON. See status.h. */
#include "status.h"

#include <math.h>
#include <string.h>

#include "cJSON.h"

void status_frame_clear(status_frame_t *f)
{
    memset(f, 0, sizeof(*f));
    f->up_s = NAN;
    f->load1 = f->load5 = f->load15 = NAN;
    f->cpu_pct = NAN;
    f->mem_used_gib = f->mem_total_gib = NAN;
    f->gpu_pct = f->gpu_w = f->gpu_c = NAN;
    f->fan0_rpm = f->fan1_rpm = NAN;
    f->board_c = f->nvme_c = f->nic_c = NAN;
}

const char *frame_result_name(frame_result_t r)
{
    switch (r) {
    case FRAME_OK: return "ok";
    case FRAME_ERR_EMPTY: return "empty";
    case FRAME_ERR_TOO_LONG: return "too long";
    case FRAME_ERR_JSON: return "bad json";
    case FRAME_ERR_NOT_OBJECT: return "not an object";
    case FRAME_ERR_VERSION: return "wrong version";
    case FRAME_ERR_REQUIRED: return "missing field";
    }
    return "?";
}

/* A finite number inside [lo, hi], or NAN. */
static double num_in(const cJSON *obj, const char *key, double lo, double hi)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (!cJSON_IsNumber(item)) {
        return NAN;
    }
    double v = item->valuedouble;
    if (!isfinite(v) || v < lo || v > hi) {
        return NAN;
    }
    return v;
}

/* Copy a string field, truncated to `cap` bytes. False if absent or empty. */
static bool str_into(const cJSON *obj, const char *key, char *dst, size_t cap)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (!cJSON_IsString(item) || item->valuestring == NULL || item->valuestring[0] == '\0') {
        return false;
    }
    strncpy(dst, item->valuestring, cap);
    dst[cap] = '\0';
    return true;
}

#define TEMP_LO (-40.0)
#define TEMP_HI 150.0

static void parse_temps(const cJSON *obj, status_frame_t *out)
{
    const cJSON *th = cJSON_GetObjectItemCaseSensitive(obj, "th");
    if (!cJSON_IsArray(th)) {
        return;
    }
    const cJSON *entry = NULL;
    cJSON_ArrayForEach(entry, th) {
        if (out->n_temps >= STATUS_MAX_TEMPS) {
            break;
        }
        if (!cJSON_IsArray(entry) || cJSON_GetArraySize(entry) != 2) {
            continue;
        }
        const cJSON *label = cJSON_GetArrayItem(entry, 0);
        const cJSON *value = cJSON_GetArrayItem(entry, 1);
        if (!cJSON_IsString(label) || label->valuestring[0] == '\0' || !cJSON_IsNumber(value)) {
            continue;
        }
        double c = value->valuedouble;
        if (!isfinite(c) || c < TEMP_LO || c > TEMP_HI) {
            continue;
        }
        status_temp_t *t = &out->temps[out->n_temps++];
        strncpy(t->label, label->valuestring, STATUS_LABEL_MAX);
        t->label[STATUS_LABEL_MAX] = '\0';
        t->c = (float)c;
    }
}

frame_result_t frame_parse(const char *line, size_t len, status_frame_t *out)
{
    status_frame_clear(out);
    if (len == 0) {
        return FRAME_ERR_EMPTY;
    }
    /* The newline counts toward the 1024-byte cap. */
    if (len + 1 > PROTO_MAX_LINE) {
        return FRAME_ERR_TOO_LONG;
    }
    cJSON *root = cJSON_ParseWithLength(line, len);
    if (root == NULL) {
        return FRAME_ERR_JSON;
    }
    frame_result_t rc = FRAME_OK;
    if (!cJSON_IsObject(root)) {
        rc = FRAME_ERR_NOT_OBJECT;
        goto done;
    }

    const cJSON *v = cJSON_GetObjectItemCaseSensitive(root, "v");
    if (!cJSON_IsNumber(v) || v->valuedouble != PROTO_VERSION) {
        rc = FRAME_ERR_VERSION;
        goto done;
    }
    double t = num_in(root, "t", 0, 1e12);
    if (isnan(t) || !str_into(root, "host", out->host, STATUS_HOST_MAX)) {
        rc = FRAME_ERR_REQUIRED;
        goto done;
    }
    out->t = (int64_t)t;

    double tz = num_in(root, "tz_s", -14 * 3600, 14 * 3600);
    if (!isnan(tz)) {
        out->has_tz = true;
        out->tz_s = (int32_t)tz;
    }
    if (!str_into(root, "occ", out->occ, STATUS_OCC_MAX)) {
        out->occ[0] = '\0';
    }
    out->up_s = num_in(root, "up_s", 0, 1e10);
    out->load1 = (float)num_in(root, "load1", 0, 10000);
    out->load5 = (float)num_in(root, "load5", 0, 10000);
    out->load15 = (float)num_in(root, "load15", 0, 10000);
    out->cpu_pct = (float)num_in(root, "cpu_pct", 0, 100);
    out->mem_used_gib = (float)num_in(root, "mem_used_gib", 0, 4096);
    out->mem_total_gib = (float)num_in(root, "mem_total_gib", 0, 4096);
    if (!isnan(out->mem_used_gib) && !isnan(out->mem_total_gib) &&
        out->mem_used_gib > out->mem_total_gib) {
        out->mem_used_gib = NAN;
        out->mem_total_gib = NAN;
    }
    out->gpu_pct = (float)num_in(root, "gpu_pct", 0, 100);
    out->gpu_w = (float)num_in(root, "gpu_w", 0, 2000);
    out->gpu_c = (float)num_in(root, "gpu_c", TEMP_LO, TEMP_HI);
    out->fan0_rpm = (float)num_in(root, "fan0_rpm", 0, 30000);
    out->fan1_rpm = (float)num_in(root, "fan1_rpm", 0, 30000);
    out->board_c = (float)num_in(root, "board_c", TEMP_LO, TEMP_HI);
    out->nvme_c = (float)num_in(root, "nvme_c", TEMP_LO, TEMP_HI);
    out->nic_c = (float)num_in(root, "nic_c", TEMP_LO, TEMP_HI);
    parse_temps(root, out);

done:
    cJSON_Delete(root);
    if (rc != FRAME_OK) {
        status_frame_clear(out);
    }
    return rc;
}
