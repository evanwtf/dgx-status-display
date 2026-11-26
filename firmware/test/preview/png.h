/* A minimal PNG writer: 8-bit RGB, stored (uncompressed) deflate. */
#pragma once

#include <stdint.h>

/* 0 on success. */
int png_write_rgb(const char *path, const uint8_t *rgb, int w, int h);
