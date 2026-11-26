"""Render the systemd unit for one user and checkout. `make install-service`
runs this, then installs the result with sudo.

    uv run python scripts/service.py render --user "$USER" --repo "$REPO" \
        --installed /etc/systemd/system/dgx-status.service --out UNIT_FILE

It refuses:

- an empty user, or root (make under sudo): run make as the normal user;
- a checkout path that holds a character the unit cannot carry safely
  (`|`, `&`, `'`, `\\`, a space or a newline), or that is not absolute;
- a template that leaves an `@NAME@` placeholder;
- an installed unit for another checkout (its WorkingDirectory), unless
  `--force`.

Stdlib only. Every refusal logs the reason and exits 1.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("service")

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "deploy" / "dgx-status.service"
BAD_PATH_CHARS = ("|", "&", "'", "\\", " ", "\n")


class ServiceError(Exception):
    """A refusal, with the reason."""


def render(template: str, user: str, repo: str) -> str:
    """Fill @USER@ and @REPO@. Refuse an unsafe value or a leftover @NAME@."""
    if not user or user == "root":
        raise ServiceError(
            f"user is {user or 'empty'}: run make as the account that runs the"
            " daemon, not under sudo (make asks for sudo itself)"
        )
    if not re.fullmatch(r"[a-z_][a-z0-9_.-]*\$?", user):
        raise ServiceError(f"{user!r} is not a plain user name")
    bad = [c for c in BAD_PATH_CHARS if c in repo]
    if bad:
        raise ServiceError(f"the checkout path holds {bad[0]!r}; move the checkout")
    if not repo.startswith("/"):
        raise ServiceError(f"{repo} is not an absolute path")
    text = template.replace("@USER@", user).replace("@REPO@", repo)
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        raise ServiceError(f"the unit still holds {left[0]}")
    return text


def working_directory(unit: str) -> str | None:
    """The WorkingDirectory= value of a unit file, or None."""
    for line in unit.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "WorkingDirectory":
            return value.strip()
    return None


def check_installed(installed: Path, repo: str, *, force: bool) -> None:
    """Refuse to replace a unit that serves another checkout, unless force."""
    if not installed.exists():
        return
    other = working_directory(installed.read_text(encoding="utf-8"))
    if other is None or other == repo:
        return
    logger.warning("%s serves %s", installed, other)
    logger.warning("this checkout is %s", repo)
    if not force:
        raise ServiceError(
            "the installed unit serves another checkout; FORCE=1 replaces it"
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="write the unit for one user and checkout")
    r.add_argument("--user", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--installed", type=Path, help="the unit file installed now")
    r.add_argument("--force", action="store_true", help="replace another checkout's")
    r.add_argument("--template", type=Path, default=TEMPLATE, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    try:
        text = render(args.template.read_text(encoding="utf-8"), args.user, args.repo)
        if args.installed is not None:
            check_installed(args.installed, args.repo, force=args.force)
    except ServiceError as e:
        logger.error("refused: %s", e)
        return 1
    args.out.write_text(text, encoding="utf-8")
    logger.info("rendered the unit for %s in %s", args.user, args.repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
