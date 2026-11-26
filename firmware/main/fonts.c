/* Montserrat fonts with an em dash. LVGL only: the host preview builds it. */
#include "fonts.h"

#include <string.h>

lv_font_t font_small;
lv_font_t font_medium;
lv_font_t font_unit;
lv_font_t font_large;

/* ---- Em dash: one A8 glyph, a filled bar, with Montserrat as fallback ---- */

typedef struct {
    lv_font_fmt_txt_glyph_dsc_t glyphs[2]; /* id 0 is reserved */
    lv_font_fmt_txt_cmap_t cmap;
    lv_font_fmt_txt_dsc_t dsc;
} dash_font_t;

#define DASH_MAX_W 48
#define DASH_MAX_H 8
static uint8_t s_ink[DASH_MAX_W * DASH_MAX_H];
static dash_font_t s_dash_small, s_dash_medium, s_dash_unit, s_dash_large;

static void make_dash_font(lv_font_t *out, dash_font_t *d, const lv_font_t *base, int size)
{
    /* Take the bar's thickness and height from the font's own hyphen. */
    lv_font_glyph_dsc_t hy;
    memset(&hy, 0, sizeof(hy));
    if (!lv_font_get_glyph_dsc(base, &hy, '-', 0)) {
        hy.box_h = (uint16_t)(size / 10 + 1);
        hy.ofs_y = (int16_t)(size / 4);
    }
    int w = size * 3 / 4;
    if (w > DASH_MAX_W) {
        w = DASH_MAX_W;
    }
    int h = hy.box_h > DASH_MAX_H ? DASH_MAX_H : hy.box_h;

    memset(d, 0, sizeof(*d));
    d->glyphs[1].bitmap_index = 0;
    d->glyphs[1].adv_w = (uint32_t)((w + 4) * 16);
    d->glyphs[1].box_w = (uint8_t)w;
    d->glyphs[1].box_h = (uint8_t)h;
    d->glyphs[1].ofs_x = 2;
    d->glyphs[1].ofs_y = (int8_t)hy.ofs_y;
    d->cmap.range_start = 0x2014;
    d->cmap.range_length = 1;
    d->cmap.glyph_id_start = 1;
    d->cmap.type = LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY;
    d->dsc.glyph_bitmap = s_ink;
    d->dsc.glyph_dsc = d->glyphs;
    d->dsc.cmaps = &d->cmap;
    d->dsc.cmap_num = 1;
    d->dsc.bpp = 8;
    d->dsc.bitmap_format = LV_FONT_FMT_TXT_PLAIN;

    *out = *base; /* line height, baseline, cap and x height */
    out->dsc = &d->dsc;
    out->get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt;
    out->get_glyph_bitmap = lv_font_get_bitmap_fmt_txt;
    out->release_glyph = NULL;
    out->static_bitmap = 1;
    out->fallback = base;
}

void fonts_init(void)
{
    memset(s_ink, 0xFF, sizeof(s_ink));
    make_dash_font(&font_small, &s_dash_small, &lv_font_montserrat_20, 20);
    make_dash_font(&font_medium, &s_dash_medium, &lv_font_montserrat_28, 28);
    make_dash_font(&font_unit, &s_dash_unit, &lv_font_montserrat_32, 32);
    make_dash_font(&font_large, &s_dash_large, &lv_font_montserrat_48, 48);
}
