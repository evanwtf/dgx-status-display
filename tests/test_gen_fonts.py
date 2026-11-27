"""The committed fonts match their specs; the pins are well formed.

The full byte-for-byte check needs Docker and the network:
`uv run python scripts/gen_fonts.py --check` (CI runs it in the firmware job).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from gen_fonts import FONTS, NODE_IMAGE, OUT_DIR, FontSpec, docker_argv

TILE_TEXT_PX = 198
ADVANCE_EM = 0.6


@pytest.mark.parametrize("spec", FONTS, ids=lambda s: s.name)
def test_committed_font_was_made_from_its_spec(spec: FontSpec) -> None:
    head = (OUT_DIR / f"{spec.name}.c").read_text().splitlines()[:6]
    assert f" * Opts: {' '.join(spec.argv())}" in head


@pytest.mark.parametrize(
    "spec", [s for s in FONTS if s.monospace], ids=lambda s: s.name
)
def test_committed_font_advance_is_monospace(spec: FontSpec) -> None:
    text = (OUT_DIR / f"{spec.name}.c").read_text()
    advances = {int(a) for a in re.findall(r"\.adv_w = (\d+), \.box_w", text)} - {0}
    assert advances == {round(spec.size * ADVANCE_EM * 16)}


def test_no_stray_font_files() -> None:
    assert sorted(p.stem for p in OUT_DIR.glob("*.c")) == sorted(s.name for s in FONTS)


@pytest.mark.parametrize(
    ("name", "worst_case"),
    [
        ("jbm_num_110", "100"),
        ("jbm_num_82", "13.5"),
        ("jbm_mix_66", "121.9"),
        ("jbm_text_36", "llama.cpp"),
    ],
)
def test_worst_case_value_fits_the_tile(name: str, worst_case: str) -> None:
    spec = next(s for s in FONTS if s.name == name)
    assert len(worst_case) * spec.size * ADVANCE_EM <= TILE_TEXT_PX


def test_docker_run_is_pinned_and_offline() -> None:
    argv = docker_argv(OUT_DIR, FONTS[0], 1000, 1000)
    assert "--network=none" in argv
    assert NODE_IMAGE in argv
    assert re.fullmatch(r"node:[\d.]+-alpine@sha256:[0-9a-f]{64}", NODE_IMAGE)


@pytest.mark.parametrize("spec", FONTS, ids=lambda s: s.name)
def test_every_font_has_every_requested_glyph(spec: FontSpec) -> None:
    """lv_font_conv drops a glyph the TTF lacks; the UI would draw nothing."""
    text = (OUT_DIR / f"{spec.name}.c").read_text()
    wanted = {ord(c) for c in spec.symbols}
    for r in spec.ranges:
        lo, _, hi = r.partition("-")
        wanted |= set(range(int(lo, 16), int(hi or lo, 16) + 1))
    for cp in wanted:
        if cp == 0x20:
            continue
        assert f"U+{cp:04X}" in text, f"{spec.name} lacks U+{cp:04X}"


def test_title_font_covers_every_page_title() -> None:
    """brl_32 is a subset: a new page title needs its letters added."""
    root = Path(__file__).resolve().parent.parent
    tiles = (root / "firmware/main/logic/tiles.c").read_text()
    titles = re.findall(r'case PAGE_\w+: return "([^"]+)";', tiles)
    assert len(titles) >= 5
    spec = next(f for f in FONTS if f.name == "brl_32")
    for title in [*titles, "All temperatures", "LLM engines"]:
        missing = set(title) - set(spec.symbols)
        assert not missing, f"{title!r} needs {sorted(missing)} in brl_32"


# Far mode: the screen's lit square is 452 px wide (480 less a 14 px margin
# each side); view.c centers each value slot in it.
FAR_SQUARE_PX = 452


@pytest.mark.parametrize(
    ("name", "slot"),
    [("far_num_187", 3), ("far_num_147", 5)],
)
def test_far_value_slot_fits_the_screen(name: str, slot: int) -> None:
    spec = next(s for s in FONTS if s.name == name)
    assert slot * spec.size * ADVANCE_EM <= FAR_SQUARE_PX


def test_far_fonts_cover_every_far_word() -> None:
    """Each far page word draws in far_brl_140 or, when too wide, in
    far_brl_112: both fonts need its letters."""
    root = Path(__file__).resolve().parent.parent
    far = (root / "firmware/main/logic/far.c").read_text()
    words = re.findall(r'case FAR_\w+: return "([^"]+)";', far)
    assert len(words) == 7
    words += re.findall(r'out->word = "([^"]+)";', far)
    assert "RUNNING" in words
    big = next(f for f in FONTS if f.name == "far_brl_140")
    for word in words:
        missing = set(word) - set(big.symbols)
        assert not missing, f"{word!r} needs {sorted(missing)} in far_brl_140"
        assert word == word.upper()
