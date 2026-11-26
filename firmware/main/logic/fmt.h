/*
 * Format readings as display text. Pure C.
 *
 * An unknown value (NAN) formats as FMT_DASH. Every function writes a
 * NUL-terminated string into buf (size cap) and returns buf.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#define FMT_DASH "\xE2\x80\x94" /* U+2014 em dash, UTF-8 */
#define FMT_DEG "\xC2\xB0"      /* U+00B0 degree sign, UTF-8 */

/* "45.8°" with one decimal, or "—". */
char *fmt_temp1(char *buf, size_t cap, float c);
/* "44°" rounded to an integer, or "—". */
char *fmt_temp0(char *buf, size_t cap, float c);
/* Integer with a unit suffix: "4 W", "2790 rpm". */
char *fmt_int_unit(char *buf, size_t cap, float v, const char *unit);
/* Percent: decimals 0 -> "7%", 1 -> "5.2%". */
char *fmt_pct(char *buf, size_t cap, float v, int decimals);
/* "7.2/121.7 GiB". Each side may be "—". */
char *fmt_mem(char *buf, size_t cap, float used, float total);
/* "0.35 0.48 0.36". */
char *fmt_load(char *buf, size_t cap, float l1, float l5, float l15);
/* Uptime: "3:04" (h:mm) under a day, "14d 1:26" from a day. */
char *fmt_uptime(char *buf, size_t cap, double up_s);
/* Host clock "22:44:57" from epoch seconds plus a UTC offset. */
char *fmt_clock(char *buf, size_t cap, int64_t t, int32_t tz_s);
/* Host clock "22:44" (HH:MM). */
char *fmt_clock_hm(char *buf, size_t cap, int64_t t, int32_t tz_s);
/* Age: "7s", "2m 13s", "1h 05m", "3d 4h", or "never" for a negative age. */
char *fmt_age(char *buf, size_t cap, int64_t age_s);
