"""The release checks. They run unattended, so each refusal has a test."""

from __future__ import annotations

import hashlib
import json
import zipfile
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
    check_built,
    check_merged,
    check_tag,
    declared_versions,
    flash_layout,
    main,
    stage,
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


def test_a_pyproject_that_is_not_toml_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / PYPROJECT).write_text('[project\nversion = "1.2.3"\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="not valid TOML"):
        check_tag("v1.2.3", repo)


def test_an_init_that_is_not_python_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / INIT).write_text('__version__ = "1.2.3\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="not valid Python"):
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
    text = "## 1.0.0 (2026-01-02)\n\nIntro.\n\n### Fixes\n\n- a\n\n## 0.9.0\n\n- b\n"
    assert changelog_section(text, "1.0.0") == "Intro.\n\n### Fixes\n\n- a\n"


def test_a_heading_inside_a_code_fence_is_not_a_section() -> None:
    text = "## 1.0.0 (2026-01-02)\n\n```md\n## 0.9.0\n```\n\n- a\n"
    assert "## 0.9.0" in changelog_section(text, "1.0.0")


def test_a_shorter_fence_inside_a_longer_one_does_not_close_it() -> None:
    text = "## 1.0.0 (2026-01-02)\n\n````md\n```\n## 0.9.0\n```\n````\n\n- a\n"
    notes = changelog_section(text, "1.0.0")
    assert "## 0.9.0" in notes
    assert notes.endswith("- a\n")


def test_a_tilde_fence_hides_a_heading() -> None:
    text = "## 1.0.0 (2026-01-02)\n\n~~~\n## 1.0.0 (2026-01-02)\n~~~\n\n- a\n"
    assert "- a" in changelog_section(text, "1.0.0")


def test_a_backtick_line_does_not_close_a_tilde_fence() -> None:
    text = "## 1.0.0 (2026-01-02)\n\n~~~\n```\n## 1.0.0 (2026-01-02)\n~~~\n- a\n"
    assert "- a" in changelog_section(text, "1.0.0")


def test_a_closer_indented_by_two_spaces_closes_the_fence() -> None:
    """After the fence closes, a later section is a real heading again."""
    text = "## 1.0.0 (2026-01-02)\n\n```\nx\n  ```\n\n## 0.9.0 (2026-01-01)\n\n- b\n"
    assert "- b" not in changelog_section(text, "1.0.0")


def test_a_closer_indented_by_four_spaces_does_not_close_the_fence() -> None:
    text = "## 1.0.0 (2026-01-02)\n\n```\nx\n    ```\n## 0.9.0 (2026-01-01)\n- b\n"
    assert "- b" in changelog_section(text, "1.0.0")


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
        changelog_section("# Changelog\n\n## 1.2.3 (2026-11-26)\n", "1.2.3")


@pytest.mark.parametrize("heading", ["1.2.3 (unreleased)", "1.2.3 (Unreleased)"])
def test_an_unreleased_section_is_refused(heading: str) -> None:
    with pytest.raises(ReleaseError, match="unreleased"):
        changelog_section(f"## {heading}\n\n- a\n", "1.2.3")


@pytest.mark.parametrize(
    "heading",
    ["1.2.3", "1.2.3 2026-11-26", "1.2.3 (26-11-2026)", "1.2.3 (2026-11-26) beta"],
)
def test_a_heading_without_a_date_is_refused(heading: str) -> None:
    with pytest.raises(ReleaseError, match="YYYY-MM-DD"):
        changelog_section(f"## {heading}\n\n- a\n", "1.2.3")


@pytest.mark.parametrize("date", ["2026-02-30", "2026-13-01", "2026-00-10"])
def test_a_heading_with_an_impossible_date_is_refused(date: str) -> None:
    with pytest.raises(ReleaseError, match="no real date"):
        changelog_section(f"## 1.2.3 ({date})\n\n- a\n", "1.2.3")


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


def test_a_missing_merged_image_is_refused(tmp_path: Path) -> None:
    make_build(tmp_path)
    with pytest.raises(ReleaseError, match=r"m\.bin: no such file"):
        check_merged(tmp_path, tmp_path / "m.bin")


def test_a_long_merged_image_is_refused(tmp_path: Path) -> None:
    image = merged_image(*make_build(tmp_path))
    (tmp_path / "m.bin").write_bytes(image + b"\xff")
    with pytest.raises(ReleaseError, match="bytes, expected"):
        check_merged(tmp_path, tmp_path / "m.bin")


# --- flash_args ------------------------------------------------------------


def test_flash_layout_reads_options_and_pairs() -> None:
    text = "--flash_mode dio --flash_size 16MB\n0x0 a.bin\n0x8000 b/c.bin\n"
    assert flash_layout(text) == [(0, "a.bin"), (0x8000, "b/c.bin")]


def test_an_offset_without_a_file_is_refused() -> None:
    with pytest.raises(ReleaseError, match="has no file"):
        flash_layout("0x0 a.bin\n0x8000\n")


def test_a_word_that_is_not_an_offset_is_refused() -> None:
    with pytest.raises(ReleaseError, match="is not an offset"):
        flash_layout("0x0 a.bin\nzero b.bin\n")


def test_flash_args_with_no_files_is_refused() -> None:
    with pytest.raises(ReleaseError, match="no files"):
        flash_layout("--flash_mode dio --flash_size 16MB\n")


# --- built -----------------------------------------------------------------


def make_wheel(path: Path, version: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"dgx_status-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: dgx-status\nVersion: {version}\n",
        )


def make_outputs(
    root: Path, *, fw: str = "1.2.3", wheel: str = "1.2.3", sdist: str = "1.2.3"
) -> tuple[Path, Path]:
    build, dist = root / "build", root / "dist"
    build.mkdir()
    dist.mkdir()
    (build / "project_description.json").write_text(
        json.dumps({"project_name": "dgx_status_display", "project_version": fw}),
        encoding="utf-8",
    )
    make_wheel(dist / "dgx_status-1.2.3-py3-none-any.whl", wheel)
    (dist / f"dgx_status-{sdist}.tar.gz").write_bytes(b"sdist")
    return build, dist


def test_matching_build_outputs_pass(tmp_path: Path) -> None:
    assert check_built("v1.2.3", *make_outputs(tmp_path)) == "1.2.3"


def test_a_firmware_build_of_another_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match=r"firmware build says '0\.1\.0'"):
        check_built("v1.2.3", *make_outputs(tmp_path, fw="0.1.0"))


def test_a_wheel_of_another_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match=r"says '0\.1\.0'"):
        check_built("v1.2.3", *make_outputs(tmp_path, wheel="0.1.0"))


def test_a_wheel_whose_name_disagrees_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    whl = dist / "dgx_status-1.2.3-py3-none-any.whl"
    whl.rename(dist / "dgx_status-1.2.4-py3-none-any.whl")
    with pytest.raises(ReleaseError, match=r"does not carry 1\.2\.3"):
        check_built("v1.2.3", build, dist)


def test_an_sdist_of_another_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match=r"does not carry 1\.2\.3"):
        check_built("v1.2.3", *make_outputs(tmp_path, sdist="0.1.0"))


def test_two_wheels_are_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    make_wheel(dist / "dgx_status-0.1.0-py3-none-any.whl", "0.1.0")
    with pytest.raises(ReleaseError, match="2 files match"):
        check_built("v1.2.3", build, dist)


def test_no_wheel_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    (dist / "dgx_status-1.2.3-py3-none-any.whl").unlink()
    with pytest.raises(ReleaseError, match="0 files match"):
        check_built("v1.2.3", build, dist)


def test_a_wheel_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    (dist / "dgx_status-1.2.3-py3-none-any.whl").write_bytes(b"not a zip")
    with pytest.raises(ReleaseError, match="not a zip"):
        check_built("v1.2.3", build, dist)


def test_a_wheel_without_a_version_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    with zipfile.ZipFile(dist / "dgx_status-1.2.3-py3-none-any.whl", "w") as zf:
        zf.writestr("dgx_status-1.2.3.dist-info/METADATA", "Name: dgx-status\n")
    with pytest.raises(ReleaseError, match="has no Version"):
        check_built("v1.2.3", build, dist)


def test_a_missing_project_description_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    (build / "project_description.json").unlink()
    with pytest.raises(ReleaseError, match="no such file"):
        check_built("v1.2.3", build, dist)


def test_a_project_description_that_is_not_json_is_refused(tmp_path: Path) -> None:
    build, dist = make_outputs(tmp_path)
    (build / "project_description.json").write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseError, match="not valid JSON"):
        check_built("v1.2.3", build, dist)


def test_built_refuses_a_malformed_tag(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match="not v and a plain"):
        check_built("1.2.3", *make_outputs(tmp_path))


# --- stage -----------------------------------------------------------------


def make_stage_inputs(root: Path) -> tuple[Path, Path, Path]:
    build, dist = make_outputs(root)
    parts = make_build(build)
    (build / "dgx_status_display-merged.bin").write_bytes(merged_image(*parts))
    (build / "dgx_status_display.elf").write_bytes(b"\x7fELF")
    unit = root / "dgx-status.service"
    unit.write_text("[Unit]\n", encoding="utf-8")
    return build, dist, unit


def zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_stage_writes_every_asset_and_its_checksum(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    out = tmp_path / "out"
    names = stage("v1.2.3", build, dist, unit, out)
    fw = "dgx-status-display-1.2.3-esp32c6"
    assert set(names) == {
        f"{fw}-merged.bin",
        f"{fw}.zip",
        f"{fw}.elf",
        "dgx_status-1.2.3-py3-none-any.whl",
        "dgx_status-1.2.3.tar.gz",
        "dgx-status.service",
        "SHA256SUMS",
    }
    assert sorted(p.name for p in out.iterdir()) == sorted(names)
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(sums) == len(names) - 1
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    assert zip_names(out / f"{fw}.zip") == {
        f"{fw}/flash_args",
        f"{fw}/bootloader/bootloader.bin",
        f"{fw}/partition_table/partition-table.bin",
        f"{fw}/app.bin",
    }


def test_a_file_added_to_flash_args_lands_in_the_zip(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    (build / "ota").mkdir()
    (build / "ota/ota_data_initial.bin").write_bytes(b"\xff" * 32)
    with (build / "flash_args").open("a", encoding="utf-8") as f:
        f.write("0xd000 ota/ota_data_initial.bin\n")
    out = tmp_path / "out"
    stage("v1.2.3", build, dist, unit, out)
    fw = "dgx-status-display-1.2.3-esp32c6"
    names = zip_names(out / f"{fw}.zip")
    assert f"{fw}/ota/ota_data_initial.bin" in names
    with zipfile.ZipFile(out / f"{fw}.zip") as zf:
        args = zf.read(f"{fw}/flash_args").decode()
        assert "ota/ota_data_initial.bin" in args
        assert zf.read(f"{fw}/ota/ota_data_initial.bin") == b"\xff" * 32


def test_stage_refuses_a_part_that_flash_args_names_but_is_missing(
    tmp_path: Path,
) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    (build / "app.bin").unlink()
    with pytest.raises(ReleaseError, match=r"app\.bin, which is missing"):
        stage("v1.2.3", build, dist, unit, tmp_path / "out")


def test_stage_refuses_a_path_outside_the_build(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    (build / "flash_args").write_text("0x0 ../secret.bin\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="outside the build"):
        stage("v1.2.3", build, dist, unit, tmp_path / "out")


def test_stage_refuses_a_missing_merged_image(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    (build / "dgx_status_display-merged.bin").unlink()
    with pytest.raises(ReleaseError, match="no such file"):
        stage("v1.2.3", build, dist, unit, tmp_path / "out")


def test_stage_refuses_an_out_dir_that_is_not_empty(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.bin").write_bytes(b"x")
    with pytest.raises(ReleaseError, match="not empty"):
        stage("v1.2.3", build, dist, unit, out)


def test_stage_refuses_a_malformed_tag(tmp_path: Path) -> None:
    build, dist, unit = make_stage_inputs(tmp_path)
    with pytest.raises(ReleaseError, match="not v and a plain"):
        stage("1.2.3", build, dist, unit, tmp_path / "out")
