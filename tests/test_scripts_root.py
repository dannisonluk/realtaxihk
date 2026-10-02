"""`scripts/**` must not re-derive the repo root by counting parents (P2-11).

The depth expression (`parent.parent.parent`, `parents[2]`, three nested
`os.path.dirname`) appeared fifteen times across fourteen scripts. Every copy is a
script that breaks if it moves between groups, and the failure is an ImportError
that says nothing about why.

`scripts/_root.py` holds the depth once. This test is what keeps it there: without
it, the next script added would naturally copy the expression from its neighbour,
and the coupling would grow back with nobody noticing.

The second test covers the other half of the same regrouping: references to
sibling scripts by path, which went stale silently. `security_verify.py` and
`security_probe.py` both still invoked `scripts/create_admin.py` after that file
moved to `scripts/ops/`, so the security harness could not start at all. Nothing
caught it because both are run by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# An expression that walks up three levels: `a.parent.parent.parent`,
# `parents[2]`, or `dirname(dirname(dirname(...)))`.
_DEPTH_PATTERNS = (
    re.compile(r"\.parent\.parent\.parent"),
    re.compile(r"parents\[2\]"),
    re.compile(r"dirname\s*\(\s*dirname\s*\(\s*dirname"),
)

# The helper itself is where the depth is allowed to live.
_ALLOWED = {"_root.py"}


def _script_files() -> list[Path]:
    return sorted(p for p in _SCRIPTS.rglob("*.py") if p.name not in _ALLOWED)


def test_there_is_something_to_check() -> None:
    """A walker that finds no files would pass every other test silently."""
    files = _script_files()
    assert len(files) >= 10, f"only found {len(files)} scripts; the glob is wrong"


@pytest.mark.parametrize("path", _script_files(), ids=lambda p: p.name)
def test_no_script_counts_its_own_depth(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for pattern in _DEPTH_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{path.relative_to(_SCRIPTS.parent)} derives the repo root itself "
            f"({match.group(0)!r}). Import from `scripts/_root.py` instead:\n"
            f"    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
            f"    from _root import REPO_ROOT  # noqa: E402"
        )


def test_the_helper_resolves_the_readme() -> None:
    """`REPO_ROOT` must be the directory that actually contains the project."""
    import sys

    sys.path.insert(0, str(_SCRIPTS))
    try:
        from _root import REPO_ROOT

        assert (REPO_ROOT / "README.md").is_file()
        assert (REPO_ROOT / "app").is_dir()
        assert (REPO_ROOT / "pyproject.toml").is_file()
    finally:
        sys.path.remove(str(_SCRIPTS))


# A quoted `something.py` in a script is a sibling invocation, e.g.
# `ROOT / "scripts" / "ops" / "create_admin.py"`.
_SCRIPT_REF = re.compile(r'"([a-z_]+\.py)"')


def _referenced_names() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in _script_files():
        for name in _SCRIPT_REF.findall(path.read_text(encoding="utf-8")):
            out.append((path, name))
    return out


def test_there_are_references_to_check() -> None:
    refs = _referenced_names()
    assert refs, "no sibling-script references found; the pattern is wrong"


@pytest.mark.parametrize(
    ("path", "name"),
    _referenced_names(),
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_every_referenced_script_exists(path: Path, name: str) -> None:
    """A referenced filename must exist somewhere under `scripts/`.

    Checked by name rather than by resolving the full expression: the point is to
    catch the file having *moved* (which is what happened), and re-deriving the
    path here would duplicate the very bug being guarded against.
    """
    found = list(_SCRIPTS.rglob(name))
    assert found, (
        f"{path.relative_to(_SCRIPTS.parent)} references {name!r}, which does not "
        f"exist anywhere under scripts/. Did it move between groups?"
    )
