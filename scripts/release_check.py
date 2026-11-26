"""Refuse a release that is not ready. The release workflow runs each check.

    python scripts/release_check.py version v0.0.1
    python scripts/release_check.py notes 0.0.1 notes.md
    python scripts/release_check.py merged firmware/build merged.bin
    python scripts/release_check.py built v0.0.1 firmware/build dist
    python scripts/release_check.py stage v0.0.1 firmware/build dist \
        deploy/dgx-status.service OUT

`version TAG` refuses a tag that is not `v` and a plain `X.Y.Z`, and a tag
that disagrees with any declared version: `pyproject.toml` (the wheel),
`src/dgx_status/__init__.py` (`dgx-status --version`) and
`firmware/CMakeLists.txt` (`PROJECT_VER`, the board's hello). Each file is
parsed, not searched.

`notes VERSION OUT` writes that version's section of `docs/changelog.md`,
without its heading, to OUT. The heading must be `## X.Y.Z (YYYY-MM-DD)`
with a real date. It refuses a missing, empty, duplicate or "unreleased"
section. A `## ` line inside a code fence is not a heading. The notes come
from the changelog, not from a tag message: the shell expands backticks in a
hand-typed `git tag -m`.

`merged BUILD_DIR IMAGE` refuses a merged image unless each file that
BUILD_DIR/flash_args lists sits at its offset byte for byte, with 0xFF in
the gaps.

`built TAG BUILD_DIR DIST_DIR` refuses build outputs that carry another
version: `project_version` in BUILD_DIR/project_description.json, the
`Version:` in the one wheel's METADATA, and the sdist's name.

`stage TAG BUILD_DIR DIST_DIR UNIT OUT` copies the release assets into OUT
and writes SHA256SUMS over them. The firmware zip holds every file that
flash_args names, at the path it names, inside one folder.

Stdlib only. Every refusal logs the reason and exits 1.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import email.parser
import hashlib
import json
import logging
import re
import shlex
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

logger = logging.getLogger("release_check")

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = "pyproject.toml"
INIT = "src/dgx_status/__init__.py"
CMAKE = "firmware/CMakeLists.txt"
CHANGELOG = "docs/changelog.md"
MERGED = "dgx_status_display-merged.bin"  # scripts/firmware.py merge writes it
ELF = "dgx_status_display.elf"
DIST_NAME = "dgx_status"  # the wheel and sdist file name prefix
ASSET_PREFIX = "dgx-status-display"

# ASCII digits only: \d also matches other scripts' digits. No leading zeros.
_NUM = "(?:0|[1-9][0-9]*)"
VERSION_RE = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}")
TAG_RE = re.compile(rf"v({VERSION_RE.pattern})")
_PROJECT_VER_RE = re.compile(
    r'^[ \t]*set\([ \t]*PROJECT_VER[ \t]+"([^"\n]*)"[ \t]*\)', re.MULTILINE
)
# A CommonMark code fence: up to 3 spaces, then 3 or more ` or ~.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING_DATE_RE = re.compile(r"\(([0-9]{4}-[0-9]{2}-[0-9]{2})\)")


class ReleaseError(Exception):
    """A check refused the release. The message says why."""


def read_text(path: Path) -> str:
    """Read a file as strict UTF-8, or refuse."""
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise ReleaseError(f"{path.name}: no such file") from None
    except UnicodeDecodeError as e:
        raise ReleaseError(f"{path.name}: not UTF-8 ({e.reason})") from None


def pyproject_version(text: str) -> str:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ReleaseError(f"{PYPROJECT}: not valid TOML ({e})") from None
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str):
        raise ReleaseError(f"{PYPROJECT}: no [project] version string")
    return version


def init_version(text: str) -> str:
    """The one module-level `__version__ = "..."` assignment."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise ReleaseError(f"{INIT}: not valid Python ({e.msg})") from None
    found: list[str] = []
    for node in tree.body:
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        else:
            continue
        if "__version__" not in names:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            raise ReleaseError(f"{INIT}: __version__ is not a string literal")
        found.append(value.value)
    if len(found) != 1:
        raise ReleaseError(f"{INIT}: {len(found)} __version__ assignments, need 1")
    return found[0]


def cmake_version(text: str) -> str:
    """The one `set(PROJECT_VER "...")` line."""
    found = _PROJECT_VER_RE.findall(text)
    if len(found) != 1:
        raise ReleaseError(f"{CMAKE}: {len(found)} PROJECT_VER lines, need 1")
    return str(found[0])


def declared_versions(root: Path) -> dict[str, str]:
    """Every place the repo declares its version, by file."""
    return {
        PYPROJECT: pyproject_version(read_text(root / PYPROJECT)),
        INIT: init_version(read_text(root / INIT)),
        CMAKE: cmake_version(read_text(root / CMAKE)),
    }


def tag_version(tag: str) -> str:
    """The X.Y.Z in a `vX.Y.Z` tag, or refuse."""
    m = TAG_RE.fullmatch(tag)
    if m is None:
        raise ReleaseError(f"tag {tag!r} is not v and a plain X.Y.Z")
    return m.group(1)


def check_tag(tag: str, root: Path) -> str:
    """Return the version in a `vX.Y.Z` tag that every file declares."""
    version = tag_version(tag)
    wrong = [
        f"{name} says {declared!r}"
        for name, declared in declared_versions(root).items()
        if declared != version
    ]
    if wrong:
        raise ReleaseError(f"tag {tag} disagrees: " + "; ".join(wrong))
    return version


def changelog_section(text: str, version: str) -> str:
    """The body of the one `## <version> ...` section, stripped."""
    if VERSION_RE.fullmatch(version) is None:
        raise ReleaseError(f"version {version!r} is not a plain X.Y.Z")
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    fence: str | None = None  # the open fence's run of ` or ~
    for line in text.splitlines():
        m = _FENCE_RE.match(line)
        if m is not None:
            run, rest = m.group(1), m.group(2)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
        elif fence is None and line.startswith("## "):
            heading = line[3:].strip()
            current = [] if heading.split(maxsplit=1)[:1] == [version] else None
            if current is not None:
                sections.append((heading, current))
            continue
        if current is not None:
            current.append(line)
    if not sections:
        raise ReleaseError(f"{CHANGELOG}: no section for {version}")
    if len(sections) > 1:
        raise ReleaseError(f"{CHANGELOG}: {len(sections)} sections for {version}")
    heading, lines = sections[0]
    if "unreleased" in heading.lower():
        raise ReleaseError(f"{CHANGELOG}: the {version} heading says unreleased")
    rest = heading[len(version) :].strip()
    m = _HEADING_DATE_RE.fullmatch(rest)
    if m is None:
        raise ReleaseError(
            f"{CHANGELOG}: the {version} heading is not '## {version} (YYYY-MM-DD)'"
        )
    try:
        datetime.date.fromisoformat(m.group(1))
    except ValueError:
        raise ReleaseError(
            f"{CHANGELOG}: the {version} heading has no real date ({m.group(1)})"
        ) from None
    body = "\n".join(lines).strip()
    if not body:
        raise ReleaseError(f"{CHANGELOG}: the {version} section is empty")
    return body + "\n"


def write_notes(version: str, out: Path, root: Path) -> str:
    notes = changelog_section(read_text(root / CHANGELOG), version)
    out.write_text(notes, encoding="utf-8")
    return notes


def flash_layout(flash_args: str) -> list[tuple[int, str]]:
    """The (offset, file) pairs in an ESP-IDF flash_args file."""
    words = shlex.split(flash_args)
    pairs: list[tuple[int, str]] = []
    i = 0
    while i < len(words):
        if words[i].startswith("-"):
            i += 2  # an option and its value, such as --flash_mode dio
            continue
        if i + 1 >= len(words):
            raise ReleaseError(f"flash_args: offset {words[i]!r} has no file")
        try:
            offset = int(words[i], 0)
        except ValueError:
            raise ReleaseError(f"flash_args: {words[i]!r} is not an offset") from None
        pairs.append((offset, words[i + 1]))
        i += 2
    if not pairs:
        raise ReleaseError("flash_args: no files")
    return pairs


def check_merged(build_dir: Path, image: Path) -> list[tuple[int, str, int]]:
    """Refuse a merged image that differs from the parts it was made from."""
    layout = flash_layout(read_text(build_dir / "flash_args"))
    parts: list[tuple[int, str, bytes]] = []
    for offset, name in layout:
        try:
            parts.append((offset, name, (build_dir / name).read_bytes()))
        except FileNotFoundError:
            raise ReleaseError(f"flash_args names {name}, which is missing") from None
    try:
        merged = image.read_bytes()
    except FileNotFoundError:
        raise ReleaseError(f"{image.name}: no such file") from None
    end = max(offset + len(data) for offset, _, data in parts)
    if len(merged) != end:
        raise ReleaseError(f"{image.name}: {len(merged)} bytes, expected {end}")
    expected = bytearray(b"\xff" * end)
    taken = 0
    for offset, name, data in sorted(parts):
        if offset < taken:
            raise ReleaseError(f"flash_args: {name} overlaps another file")
        expected[offset : offset + len(data)] = data
        taken = offset + len(data)
    if merged != expected:
        first = next(
            i for i, (a, b) in enumerate(zip(merged, expected, strict=True)) if a != b
        )
        raise ReleaseError(f"{image.name}: differs from its parts at {first:#x}")
    return [(offset, name, len(data)) for offset, name, data in sorted(parts)]


def find_one(dist_dir: Path, pattern: str) -> Path:
    """The one file in dist_dir that matches pattern, or refuse."""
    found = sorted(dist_dir.glob(pattern))
    if len(found) != 1:
        raise ReleaseError(f"{dist_dir.name}: {len(found)} files match {pattern}")
    return found[0]


def wheel_version(wheel: Path) -> str:
    """The `Version:` field in a wheel's METADATA."""
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ReleaseError(f"{wheel.name}: {len(names)} METADATA files")
            raw = zf.read(names[0])
    except zipfile.BadZipFile:
        raise ReleaseError(f"{wheel.name}: not a zip file") from None
    try:
        meta = email.parser.HeaderParser().parsestr(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ReleaseError(f"{wheel.name}: METADATA is not UTF-8") from None
    version = meta.get("Version")
    if version is None:
        raise ReleaseError(f"{wheel.name}: METADATA has no Version")
    return str(version)


def check_built(tag: str, build_dir: Path, dist_dir: Path) -> str:
    """Refuse a firmware build, wheel or sdist that carries another version."""
    version = tag_version(tag)
    desc_path = build_dir / "project_description.json"
    try:
        desc = json.loads(read_text(desc_path))
    except json.JSONDecodeError:
        raise ReleaseError(f"{desc_path.name}: not valid JSON") from None
    fw = desc.get("project_version") if isinstance(desc, dict) else None
    if fw != version:
        raise ReleaseError(f"firmware build says {fw!r}, tag says {version}")
    wheel = find_one(dist_dir, f"{DIST_NAME}-*.whl")
    in_wheel = wheel_version(wheel)
    if in_wheel != version:
        raise ReleaseError(f"{wheel.name} says {in_wheel!r}, tag says {version}")
    if not wheel.name.startswith(f"{DIST_NAME}-{version}-"):
        raise ReleaseError(f"{wheel.name}: the name does not carry {version}")
    sdist = find_one(dist_dir, f"{DIST_NAME}-*.tar.gz")
    if sdist.name != f"{DIST_NAME}-{version}.tar.gz":
        raise ReleaseError(f"{sdist.name}: the name does not carry {version}")
    return version


def _copy(src: Path, dest: Path) -> None:
    if not src.is_file():
        raise ReleaseError(f"{src.name}: no such file")
    shutil.copyfile(src, dest)


def stage(
    tag: str, build_dir: Path, dist_dir: Path, unit: Path, out: Path
) -> list[str]:
    """Copy the release assets into out and write SHA256SUMS. Return the names."""
    version = tag_version(tag)
    layout = flash_layout(read_text(build_dir / "flash_args"))
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ReleaseError(f"{out}: not empty")
    fw = f"{ASSET_PREFIX}-{version}-esp32c6"
    _copy(build_dir / MERGED, out / f"{fw}-merged.bin")
    _copy(build_dir / ELF, out / f"{fw}.elf")
    # One folder with the paths flash_args names, so that
    # `esptool.py write_flash @flash_args` works inside it.
    members = ["flash_args", *(name for _, name in layout)]
    with zipfile.ZipFile(out / f"{fw}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for name in members:
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ReleaseError(f"flash_args: {name} is outside the build")
            src = build_dir / rel
            if not src.is_file():
                raise ReleaseError(f"flash_args names {name}, which is missing")
            zf.write(src, f"{fw}/{rel}")
    wheel = find_one(dist_dir, f"{DIST_NAME}-{version}-*.whl")
    _copy(wheel, out / wheel.name)
    sdist = f"{DIST_NAME}-{version}.tar.gz"
    _copy(dist_dir / sdist, out / sdist)
    _copy(unit, out / unit.name)
    names = sorted(p.name for p in out.iterdir())
    sums = "".join(
        f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}\n" for n in names
    )
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return [*names, "SHA256SUMS"]


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("version", help="the tag equals every declared version")
    v.add_argument("tag", help="for example v0.0.1")
    n = sub.add_parser("notes", help="write a version's changelog section")
    n.add_argument("version", help="for example 0.0.1")
    n.add_argument("out", type=Path)
    m = sub.add_parser("merged", help="the merged image equals its parts")
    m.add_argument("build_dir", type=Path)
    m.add_argument("image", type=Path)
    b = sub.add_parser("built", help="the build outputs carry the tag's version")
    b.add_argument("tag")
    b.add_argument("build_dir", type=Path)
    b.add_argument("dist_dir", type=Path)
    st = sub.add_parser("stage", help="copy the assets and write SHA256SUMS")
    st.add_argument("tag")
    st.add_argument("build_dir", type=Path)
    st.add_argument("dist_dir", type=Path)
    st.add_argument("unit", type=Path, help="the systemd unit template")
    st.add_argument("out", type=Path)
    args = p.parse_args(argv)
    try:
        if args.cmd == "version":
            version = check_tag(args.tag, root)
            logger.info(
                "%s agrees with %s, %s and %s", args.tag, PYPROJECT, INIT, CMAKE
            )
            logger.info("version %s", version)
        elif args.cmd == "notes":
            notes = write_notes(args.version, args.out, root)
            logger.info("wrote %d lines of notes to %s", notes.count("\n"), args.out)
        elif args.cmd == "built":
            version = check_built(args.tag, args.build_dir, args.dist_dir)
            logger.info("the firmware, wheel and sdist all say %s", version)
        elif args.cmd == "stage":
            for name in stage(
                args.tag, args.build_dir, args.dist_dir, args.unit, args.out
            ):
                logger.info("staged %s", name)
        else:
            for offset, name, size in check_merged(args.build_dir, args.image):
                logger.info("%#08x %s (%d bytes) matches", offset, name, size)
            logger.info("%s matches flash_args", args.image)
    except ReleaseError as e:
        logger.error("refused: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
