#include "png.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t s_crc_table[256];

static void crc_init(void)
{
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) {
            c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        }
        s_crc_table[n] = c;
    }
}

static uint32_t crc(uint32_t c, const uint8_t *p, size_t n)
{
    c ^= 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        c = s_crc_table[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFu;
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static int chunk(FILE *f, const char *type, const uint8_t *data, size_t n)
{
    uint8_t head[8];
    put32(head, (uint32_t)n);
    memcpy(head + 4, type, 4);
    uint32_t c = crc(0, head + 4, 4);
    c = crc(c, data, n);
    uint8_t tail[4];
    put32(tail, c);
    return fwrite(head, 1, 8, f) != 8 || (n && fwrite(data, 1, n, f) != n) ||
           fwrite(tail, 1, 4, f) != 4;
}

int png_write_rgb(const char *path, const uint8_t *rgb, int w, int h)
{
    crc_init();
    size_t row = (size_t)w * 3 + 1;
    size_t raw_n = row * (size_t)h;
    uint8_t *raw = malloc(raw_n);
    size_t blocks = (raw_n + 65534) / 65535;
    size_t z_n = 2 + raw_n + blocks * 5 + 4;
    uint8_t *z = malloc(z_n);
    if (!raw || !z) {
        free(raw);
        free(z);
        return -1;
    }
    for (int y = 0; y < h; y++) {
        raw[(size_t)y * row] = 0; /* filter: none */
        memcpy(raw + (size_t)y * row + 1, rgb + (size_t)y * (size_t)w * 3, (size_t)w * 3);
    }
    size_t o = 0;
    z[o++] = 0x78;
    z[o++] = 0x01;
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < raw_n; i += 65535) {
        size_t n = raw_n - i < 65535 ? raw_n - i : 65535;
        z[o++] = (uint8_t)(i + n == raw_n); /* BFINAL, BTYPE=00 */
        z[o++] = (uint8_t)n;
        z[o++] = (uint8_t)(n >> 8);
        z[o++] = (uint8_t)~n;
        z[o++] = (uint8_t)(~n >> 8);
        memcpy(z + o, raw + i, n);
        o += n;
    }
    for (size_t i = 0; i < raw_n; i++) {
        a = (a + raw[i]) % 65521;
        b = (b + a) % 65521;
    }
    put32(z + o, (b << 16) | a);
    o += 4;

    uint8_t ihdr[13];
    put32(ihdr, (uint32_t)w);
    put32(ihdr + 4, (uint32_t)h);
    ihdr[8] = 8;  /* bit depth */
    ihdr[9] = 2;  /* RGB */
    ihdr[10] = 0;
    ihdr[11] = 0;
    ihdr[12] = 0;
    FILE *f = fopen(path, "wb");
    int err = f == NULL;
    if (!err) {
        static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
        err = fwrite(sig, 1, 8, f) != 8 || chunk(f, "IHDR", ihdr, 13) ||
              chunk(f, "IDAT", z, o) || chunk(f, "IEND", NULL, 0);
        err |= fclose(f) != 0;
    }
    free(raw);
    free(z);
    return err ? -1 : 0;
}
