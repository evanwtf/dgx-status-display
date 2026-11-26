"""The committed fonts match their specs; the pins are well formed.

The full byte-for-byte check needs Docker and the network:
`uv run python scripts/gen_fonts.py --check` (CI runs it in the firmware job).
"""

from __future__ import annotations

import re

import pytest
from gen_fonts import FONTS, NODE_IMAGE, OUT_DIR, FontSpec, docker_argv

TILE_TEXT_PX = 198
ADVANCE_EM = 0.6


@pytest.mark.parametrize("spec", FONTS, ids=lambda s: s.name)
def test_committed_font_was_made_from_its_spec(spec: FontSpec) -> None:
    head = (OUT_DIR / f"{spec.name}.c").read_text().splitlines()[:6]
    assert f" * Opts: {' '.join(spec.argv())}" in head


@pytest.mark.parametrize("spec", FONTS, ids=lambda s: s.name)
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
