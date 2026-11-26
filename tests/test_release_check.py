"""The release checks. They run unattended, so each refusal has a test."""

from __future__ import annotations

from pathlib import Path

import pytest
from release_check import (
    CHANGELOG,
    CMAKE,
    INIT,
    PYPROJECT,
    ROOT,
    ReleaseError,
    changelog_section,
    check_merged,
    check_tag,
    declared_versions,
    main,
)

PYPROJECT_TEXT = '[project]\nname = "dgx-status"\nversion = "{v}"\n'
INIT_TEXT = '"""Doc."""\n\nfrom __future__ import annotations\n\n__version__ = "{v}"\n'
CMAKE_TEXT = (
    "cmake_minimum_required(VERSION 3.16)\n"
    '# The firmware version.\nset(PROJECT_VER "{v}")\n'
    "project(dgx_status_display)\n"
)
CHANGELOG_TEXT = """# Changelog

## 1.2.3 (2026-11-26)

The `notes` with backticks survive.

- one
- two

## 1.2.2 (2026-11-01)

- older
"""


def make_repo(
    root: Path, *, py: str = "1.2.3", init: str = "1.2.3", cmake: str = "1.2.3"
) -> Path:
    for rel, text in (
        (PYPROJECT, PYPROJECT_TEXT.format(v=py)),
        (INIT, INIT_TEXT.format(v=init)),
        (CMAKE, CMAKE_TEXT.format(v=cmake)),
        (CHANGELOG, CHANGELOG_TEXT),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --- version ---------------------------------------------------------------


def test_the_repo_declares_one_version_in_all_three_files() -> None:
    """pyproject.toml, __init__.py and PROJECT_VER: drift is silent otherwise."""
    versions = declared_versions(ROOT)
    assert set(versions) == {PYPROJECT, INIT, CMAKE}
    assert len(set(versions.values())) == 1, versions


def test_a_matching_tag_passes(tmp_path: Path) -> None:
    assert check_tag("v1.2.3", make_repo(tmp_path)) == "1.2.3"


@pytest.mark.parametrize(
    ("field", "file"),
    [("py", PYPROJECT), ("init", INIT), ("cmake", CMAKE)],
)
def test_a_mismatch_in_any_file_is_refused(
    tmp_path: Path, field: str, file: str
) -> None:
    repo = make_repo(tmp_path, **{field: "1.2.4"})
    with pytest.raises(ReleaseError, match=file):
        check_tag("v1.2.3", repo)


@pytest.mark.parametrize(
    "tag",
    [
        "1.2.3",
        "v1.2",
        "v1.2.3.4",
        "v1.2.3-rc1",
        "v01.2.3",
        "V1.2.3",
        "v1.2.3 ",
        "v1.2.٣",  # an Arabic-Indic digit: \d would accept it
        "",
    ],
)
def test_a_malformed_tag_is_refused(tmp_path: Path, tag: str) -> None:
    with pytest.raises(ReleaseError, match="not v and a plain"):
        check_tag(tag, make_repo(tmp_path))


def test_a_commented_out_project_ver_does_not_count(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / CMAKE).write_text('# set(PROJECT_VER "1.2.3")\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="0 PROJECT_VER lines"):
        check_tag("v1.2.3", repo)


def test_two_project_ver_lines_are_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    text = CMAKE_TEXT.format(v="1.2.3") + 'set(PROJECT_VER "9.9.9")\n'
    (repo / CMAKE).write_text(text, encoding="utf-8")
    with pytest.raises(ReleaseError, match="2 PROJECT_VER lines"):
        check_tag("v1.2.3", repo)


def test_a_version_in_a_docstring_does_not_count(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / INIT).write_text('"""__version__ = "1.2.3"."""\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="0 __version__ assignments"):
        check_tag("v1.2.3", repo)


def test_a_computed_version_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / INIT).write_text('__version__ = "1.2" + ".3"\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="not a string literal"):
        check_tag("v1.2.3", repo)


def test_a_pyproject_without_a_version_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / PYPROJECT).write_text('[project]\nname = "x"\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="no \\[project\\] version"):
        check_tag("v1.2.3", repo)


@pytest.mark.parametrize("file", [PYPROJECT, INIT, CMAKE])
def test_a_version_file_that_is_not_utf8_is_refused(tmp_path: Path, file: str) -> None:
    repo = make_repo(tmp_path)
    (repo / file).write_bytes(b'version = "1.2.3" \xff\xfe\n')
    with pytest.raises(ReleaseError, match="not UTF-8"):
        check_tag("v1.2.3", repo)


def test_a_missing_version_file_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / CMAKE).unlink()
    with pytest.raises(ReleaseError, match="no such file"):
        check_tag("v1.2.3", repo)


# --- notes -----------------------------------------------------------------


def test_notes_are_the_section_body_without_its_heading() -> None:
    notes = changelog_section(CHANGELOG_TEXT, "1.2.3")
    assert notes == "The `notes` with backticks survive.\n\n- one\n- two\n"


def test_notes_stop_at_the_next_section_but_keep_subheadings() -> None:
    text = "## 1.0.0\n\nIntro.\n\n### Fixes\n\n- a\n\n## 0.9.0\n\n- b\n"
    assert changelog_section(text, "1.0.0") == "Intro.\n\n### Fixes\n\n- a\n"


def test_a_heading_inside_a_code_fence_is_not_a_section() -> None:
    text = "## 1.0.0\n\n```md\n## 0.9.0\n```\n\n- a\n"
    assert "## 0.9.0" in changelog_section(text, "1.0.0")


def test_a_missing_section_is_refused() -> None:
    with pytest.raises(ReleaseError, match=r"no section for 9\.9\.9"):
        changelog_section(CHANGELOG_TEXT, "9.9.9")


def test_a_prefix_is_not_a_match() -> None:
    """1.2.3 must not take the 1.2.30 or 1.2.3-rc1 section."""
    text = "## 1.2.30\n\n- a\n\n## 1.2.3-rc1\n\n- b\n"
    with pytest.raises(ReleaseError, match="no section"):
        changelog_section(text, "1.2.3")


@pytest.mark.parametrize("body", ["", "\n", "\n   \n\n"])
def test_an_empty_section_is_refused(body: str) -> None:
    text = f"## 1.2.3 (2026-11-26)\n{body}## 1.2.2\n\n- a\n"
    with pytest.raises(ReleaseError, match="empty"):
        changelog_section(text, "1.2.3")


def test_an_empty_last_section_is_refused() -> None:
    with pytest.raises(ReleaseError, match="empty"):
        changelog_section("# Changelog\n\n## 1.2.3\n", "1.2.3")


@pytest.mark.parametrize("heading", ["1.2.3 (unreleased)", "1.2.3 (Unreleased)"])
def test_an_unreleased_section_is_refused(heading: str) -> None:
    with pytest.raises(ReleaseError, match="unreleased"):
        changelog_section(f"## {heading}\n\n- a\n", "1.2.3")


def test_a_duplicate_section_is_refused() -> None:
    text = "## 1.2.3 (2026-11-26)\n\n- a\n\n## 1.2.3\n\n- b\n"
    with pytest.raises(ReleaseError, match="2 sections"):
        changelog_section(text, "1.2.3")


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "1.2.3-rc1", ""])
def test_a_malformed_notes_version_is_refused(version: str) -> None:
    with pytest.raises(ReleaseError, match="not a plain"):
        changelog_section(CHANGELOG_TEXT, version)


def test_a_changelog_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / CHANGELOG).write_bytes(b"## 1.2.3\n\n- caf\xe9\n")
    assert main(["notes", "1.2.3", str(tmp_path / "notes.md")], root=repo) == 1
    assert not (tmp_path / "notes.md").exists()


def test_the_real_changelog_has_notes_for_the_declared_version() -> None:
    version = declared_versions(ROOT)[PYPROJECT]
    text = (ROOT / CHANGELOG).read_text(encoding="utf-8")
    assert changelog_section(text, version).strip()


# --- the command line ------------------------------------------------------


def test_main_writes_notes_and_exits_0(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    out = tmp_path / "notes.md"
    assert main(["version", "v1.2.3"], root=repo) == 0
    assert main(["notes", "1.2.3", str(out)], root=repo) == 0
    assert out.read_text(encoding="utf-8").startswith("The `notes` with")


def test_main_exits_1_on_a_refusal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = make_repo(tmp_path, cmake="1.2.4")
    assert main(["version", "v1.2.3"], root=repo) == 1
    assert "refused" in caplog.text
    assert CMAKE in caplog.text


# --- merged ----------------------------------------------------------------


def make_build(build: Path) -> tuple[bytes, bytes, bytes]:
    boot, table, app = b"\xe9boot" * 10, b"\xaa\x50" * 8, b"\xe9app" * 50
    (build / "bootloader").mkdir(parents=True)
    (build / "partition_table").mkdir()
    (build / "bootloader/bootloader.bin").write_bytes(boot)
    (build / "partition_table/partition-table.bin").write_bytes(table)
    (build / "app.bin").write_bytes(app)
    (build / "flash_args").write_text(
        "--flash_mode dio --flash_freq 80m --flash_size 16MB\n"
        "0x0 bootloader/bootloader.bin\n"
        "0x200 app.bin\n"
        "0x100 partition_table/partition-table.bin\n",
        encoding="utf-8",
    )
    return boot, table, app


def merged_image(boot: bytes, table: bytes, app: bytes) -> bytearray:
    image = bytearray(b"\xff" * (0x200 + len(app)))
    image[0 : len(boot)] = boot
    image[0x100 : 0x100 + len(table)] = table
    image[0x200:] = app
    return image


def test_a_merged_image_that_equals_its_parts_passes(tmp_path: Path) -> None:
    parts = make_build(tmp_path)
    (tmp_path / "m.bin").write_bytes(merged_image(*parts))
    got = check_merged(tmp_path, tmp_path / "m.bin")
    assert [(o, s) for o, _, s in got] == [(0, 50), (0x100, 16), (0x200, 200)]


def test_a_changed_byte_is_refused(tmp_path: Path) -> None:
    image = merged_image(*make_build(tmp_path))
    image[0x205] ^= 1
    (tmp_path / "m.bin").write_bytes(image)
    with pytest.raises(ReleaseError, match="at 0x205"):
        check_merged(tmp_path, tmp_path / "m.bin")


def test_a_gap_that_is_not_0xff_is_refused(tmp_path: Path) -> None:
    image = merged_image(*make_build(tmp_path))
    image[0x1F0] = 0
    (tmp_path / "m.bin").write_bytes(image)
    with pytest.raises(ReleaseError, match="at 0x1f0"):
        check_merged(tmp_path, tmp_path / "m.bin")


def test_a_short_merged_image_is_refused(tmp_path: Path) -> None:
    image = merged_image(*make_build(tmp_path))
    (tmp_path / "m.bin").write_bytes(image[:-1])
    with pytest.raises(ReleaseError, match="bytes, expected"):
        check_merged(tmp_path, tmp_path / "m.bin")


def test_a_missing_part_is_refused(tmp_path: Path) -> None:
    parts = make_build(tmp_path)
    (tmp_path / "m.bin").write_bytes(merged_image(*parts))
    (tmp_path / "app.bin").unlink()
    with pytest.raises(ReleaseError, match=r"app\.bin, which is missing"):
        check_merged(tmp_path, tmp_path / "m.bin")


def test_overlapping_parts_are_refused(tmp_path: Path) -> None:
    make_build(tmp_path)
    (tmp_path / "m.bin").write_bytes(b"\xff" * (0x10 + 200))
    (tmp_path / "flash_args").write_text(
        "0x0 bootloader/bootloader.bin\n0x10 app.bin\n", encoding="utf-8"
    )
    with pytest.raises(ReleaseError, match=r"app\.bin overlaps"):
        check_merged(tmp_path, tmp_path / "m.bin")
