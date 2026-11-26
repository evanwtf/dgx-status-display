/*
 * Lines the display sends to the host (docs/protocol.md). Pure C.
 *
 *   {"hello":"dgx-status-display","fw":"0.1.0","elf":"1a2b...","rst":"panic","why":"..."}
 *
 * Each function writes one JSON object plus a newline and returns its length,
 * or 0 when it does not fit in cap (nothing useful is sent then).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#define REPORT_WHY_MAX 96 /* characters of the panic reason, before escaping */

/* elf, rst and why are optional: NULL or "" leaves the field out. `why` is
 * cut to REPORT_WHY_MAX characters and escaped for JSON; bytes that are not
 * printable ASCII become '?'. */
size_t report_hello(char *buf, size_t cap, const char *fw, const char *elf, const char *rst,
                    const char *why);
