"""Refuse a release that is not ready. The release workflow runs each check.

    python scripts/release_check.py version v0.0.1
    python scripts/release_check.py notes 0.0.1 notes.md
    python scripts/release_check.py merged firmware/build merged.bin

`version TAG` refuses a tag that is not `v` and a plain `X.Y.Z`, and a tag
that disagrees with any declared version: `pyproject.toml` (the wheel),
`src/dgx_status/__init__.py` (`dgx-status --version`) and
`firmware/CMakeLists.txt` (`PROJECT_VER`, the board's hello). Each file is
parsed, not searched.

`notes VERSION OUT` writes that version's section of `docs/changelog.md`,
without its heading, to OUT. It refuses a missing, empty, duplicate or
"unreleased" section. The notes come from the changelog, not from a tag
message: the shell expands backticks in a hand-typed `git tag -m`.

`merged BUILD_DIR IMAGE` refuses a merged image unless each file that
BUILD_DIR/flash_args lists sits at its offset byte for byte, with 0xFF in
the gaps.

Stdlib only. Every refusal logs the reason and exits 1.
"""

from __future__ import annotations

import argparse
import ast
import logging
import re
import shlex
import sys
import tomllib
from pathlib import Path

logger = logging.getLogger("release_check")

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = "pyproject.toml"
INIT = "src/dgx_status/__init__.py"
CMAKE = "firmware/CMakeLists.txt"
CHANGELOG = "docs/changelog.md"

# ASCII digits only: \d also matches other scripts' digits. No leading zeros.
_NUM = "(?:0|[1-9][0-9]*)"
VERSION_RE = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}")
TAG_RE = re.compile(rf"v({VERSION_RE.pattern})")
_PROJECT_VER_RE = re.compile(
    r'^[ \t]*set\([ \t]*PROJECT_VER[ \t]+"([^"\n]*)"[ \t]*\)', re.MULTILINE
)
_FENCE_RE = re.compile(r"^(```|~~~)")


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


def check_tag(tag: str, root: Path) -> str:
    """Return the version in a `vX.Y.Z` tag that every file declares."""
    m = TAG_RE.fullmatch(tag)
    if m is None:
        raise ReleaseError(f"tag {tag!r} is not v and a plain X.Y.Z")
    version = m.group(1)
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
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
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
