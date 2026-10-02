"""Locate the repository root from any script under `scripts/<group>/`.

Every script in this directory needs the repo root on `sys.path` before it can
`import app`. That was written out fifteen times across fourteen files, in three
spellings:

    Path(__file__).resolve().parent.parent.parent
    pathlib.Path(__file__).resolve().parents[2]
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

The depth was therefore encoded in each script, and `scripts/README.md` carried a
warning that adding a group means fixing every script inside it. This module is
that fix: the depth now lives in exactly one place, next to this docstring.

**Why the bootstrap line still appears in every script.** A helper that finds the
repo root cannot itself be found without the root — importing it requires
`scripts/` on `sys.path`, which is the thing being computed. So each script keeps
a single `sys.path.insert` and then imports this module:

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from _root import REPO_ROOT, repo_root  # noqa: E402

One short line plus one import replaces the depth expression, and moving a script
between groups no longer requires editing it. `tests/test_scripts_root.py` fails
if a script hard-codes its own depth again.

This is deliberately not a package (`scripts/__init__.py` does not exist): adding
one would make `scripts` importable, which changes how pytest collects from this
tree and is not worth the coupling for a helper three lines long.
"""

from __future__ import annotations

import pathlib

#: Absolute path to the repository root (the directory containing `app/`).
REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent


def repo_root() -> pathlib.Path:
    """The repository root as a `Path`.

    A function as well as a constant because the `os.path`-based scripts wanted a
    `str` for `env["PYTHONPATH"]` and the `pathlib` ones wanted a `Path`; having
    both available means neither call site has to convert.
    """
    return REPO_ROOT
