#include "fmt.h"

#include <math.h>
#include <stdio.h>

static char *dash(char *buf, size_t cap)
{
    snprintf(buf, cap, "%s", FMT_DASH);
    return buf;
}

char *fmt_temp1(char *buf, size_t cap, float c)
{
    if (!isfinite(c)) {
        return dash(buf, cap);
    }
    snprintf(buf, cap, "%.1f%s", (double)c, FMT_DEG);
    return buf;
}

char *fmt_temp0(char *buf, size_t cap, float c)
{
    if (!isfinite(c)) {
        return dash(buf, cap);
    }
    snprintf(buf, cap, "%ld%s", lroundf(c), FMT_DEG);
    return buf;
}

char *fmt_int_unit(char *buf, size_t cap, float v, const char *unit)
{
    if (!isfinite(v)) {
        return dash(buf, cap);
    }
    snprintf(buf, cap, "%ld %s", lroundf(v), unit);
    return buf;
}

char *fmt_pct(char *buf, size_t cap, float v, int decimals)
{
    if (!isfinite(v)) {
        return dash(buf, cap);
    }
    if (decimals <= 0) {
        snprintf(buf, cap, "%ld%%", lroundf(v));
    } else {
        snprintf(buf, cap, "%.1f%%", (double)v);
    }
    return buf;
}

char *fmt_mem(char *buf, size_t cap, float used, float total)
{
    char u[16];
    char t[16];
    if (isfinite(used)) {
        snprintf(u, sizeof(u), "%.1f", (double)used);
    } else {
        snprintf(u, sizeof(u), "%s", FMT_DASH);
    }
    if (isfinite(total)) {
        snprintf(t, sizeof(t), "%.1f", (double)total);
    } else {
        snprintf(t, sizeof(t), "%s", FMT_DASH);
    }
    snprintf(buf, cap, "%s/%s GiB", u, t);
    return buf;
}

static void load_part(char *dst, size_t cap, float v)
{
    if (isfinite(v)) {
        snprintf(dst, cap, "%.2f", (double)v);
    } else {
        snprintf(dst, cap, "%s", FMT_DASH);
    }
}

char *fmt_load(char *buf, size_t cap, float l1, float l5, float l15)
{
    char a[16];
    char b[16];
    char c[16];
    load_part(a, sizeof(a), l1);
    load_part(b, sizeof(b), l5);
    load_part(c, sizeof(c), l15);
    snprintf(buf, cap, "%s %s %s", a, b, c);
    return buf;
}

char *fmt_uptime(char *buf, size_t cap, double up_s)
{
    if (!isfinite(up_s) || up_s < 0) {
        return dash(buf, cap);
    }
    int64_t s = (int64_t)up_s;
    int64_t days = s / 86400;
    int64_t hours = (s % 86400) / 3600;
    int64_t mins = (s % 3600) / 60;
    if (days > 0) {
        snprintf(buf, cap, "%lldd %lld:%02lld", (long long)days, (long long)hours,
                 (long long)mins);
    } else {
        snprintf(buf, cap, "%lld:%02lld", (long long)hours, (long long)mins);
    }
    return buf;
}

char *fmt_clock(char *buf, size_t cap, int64_t t, int32_t tz_s)
{
    int64_t local = t + tz_s;
    int64_t day_s = local % 86400;
    if (day_s < 0) {
        day_s += 86400;
    }
    snprintf(buf, cap, "%02lld:%02lld:%02lld", (long long)(day_s / 3600),
             (long long)((day_s % 3600) / 60), (long long)(day_s % 60));
    return buf;
}

char *fmt_age(char *buf, size_t cap, int64_t age_s)
{
    if (age_s < 0) {
        snprintf(buf, cap, "never");
    } else if (age_s < 60) {
        snprintf(buf, cap, "%llds", (long long)age_s);
    } else if (age_s < 3600) {
        snprintf(buf, cap, "%lldm %02llds", (long long)(age_s / 60), (long long)(age_s % 60));
    } else if (age_s < 86400) {
        snprintf(buf, cap, "%lldh %02lldm", (long long)(age_s / 3600),
                 (long long)((age_s % 3600) / 60));
    } else {
        snprintf(buf, cap, "%lldd %lldh", (long long)(age_s / 86400),
                 (long long)((age_s % 86400) / 3600));
    }
    return buf;
}
