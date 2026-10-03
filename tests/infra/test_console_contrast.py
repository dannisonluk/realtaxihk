"""The console's colour tokens must clear WCAG contrast, and this proves it.

`admin-web/web/tool/check_contrast.py` holds the actual numbers; this module is
the thing that makes them *run*. The tool alone is a file somebody has to
remember to invoke, which is how the console shipped a dark palette whose brand
text sat at 2.91:1 and whose chips sat at 4.17-4.40:1 without anything failing.

Three properties are pinned here, and the second and third matter more than the
first:

  * the shipped `styles.css` passes;
  * the guard is **not vacuous** -- a deliberately reverted token makes it fail,
    so a future edit that weakens the tool cannot quietly turn this file green;
  * the guard's own parsing is sound. Its first version matched only
    `--custom-properties`, which made every `color-scheme` check fail no matter
    what the CSS said, and it did not strip comments, so a comment reading
    `Primer's light scale: ...` swallowed the real declarations that followed it
    and reported them missing. Both are regressions a passing guard would hide.

The tool is loaded in-process rather than spawned: its `main()` returns an exit
code instead of calling `sys.exit`, so there is nothing to subprocess.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
CONSOLE = ROOT / "admin-web" / "web"
TOOL = CONSOLE / "tool" / "check_contrast.py"

CaptureFixture = pytest.CaptureFixture


def _load_tool() -> ModuleType:
    """Import `tool/check_contrast.py` by path -- it is not an installed module."""
    spec = importlib.util.spec_from_file_location("console_check_contrast", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool() -> Iterator[ModuleType]:
    """The guard, with `CSS` restored afterwards so tests cannot leak into each other."""
    module = _load_tool()
    original = module.CSS
    yield module
    # `module.__dict__[...]`, not `module.CSS = ...`: the tool is loaded by path,
    # so it has no static type of its own and a checker rejects assigning to
    # `ModuleType`. `setattr` silences that but trips ruff's B010 ("use plain
    # assignment") -- which is the very assignment the checker just refused. A
    # module's `__dict__` *is* its namespace, so this is the same write with
    # neither checker objecting.
    module.__dict__["CSS"] = original


@pytest.fixture()
def real_css(tool: ModuleType) -> str:
    return Path(tool.CSS).read_text(encoding="utf-8")


def _mutate(tool: ModuleType, tmp_path: Path, source: str, old: str, new: str) -> None:
    """Point the guard at a copy of the stylesheet with one edit applied."""
    assert source.count(old) >= 1, f"anchor {old!r} is no longer in styles.css"
    target = tmp_path / "styles.css"
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    tool.__dict__["CSS"] = target


# --- the shipped stylesheet -------------------------------------------------


def test_the_shipped_stylesheet_passes(tool: ModuleType) -> None:
    assert tool.main() == 0


def test_the_guard_checks_all_three_theme_paths(tool: ModuleType, capsys: CaptureFixture) -> None:
    """A guard that silently skips a theme is worse than no guard.

    `[data-theme="system"]` + a dark OS is a separate token block in the
    stylesheet, so it is a separate chance for the palette to drift.
    """
    assert tool.main() == 0
    printed = capsys.readouterr().out
    for theme in ("light", "dark", "dark-system"):
        assert f"== {theme} ==" in printed


# --- the maths, against known-good reference values -------------------------


def test_the_contrast_ratio_matches_the_wcag_reference(tool: ModuleType) -> None:
    assert tool.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert tool.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    # Primer's `fg.muted` #57606a on `canvas.default` #ffffff, as the console uses it.
    assert tool.contrast("#57606a", "#ffffff") == pytest.approx(6.39, abs=0.01)


def test_the_chip_tint_is_an_srgb_lerp_not_a_lighten(tool: ModuleType) -> None:
    # 6% of black over white is the channel lerp, which rounds to #f0f0f0 --
    # not `#f5f5f5` (a naive 94% white) and not the raw token.
    assert tool.composite("#000000", 0.06, "#ffffff") == "#f0f0f0"


# --- the guard must be able to fail -----------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "why"),
    [
        (
            "--brand-text: #ff7b72;",
            "--brand-text: #d2232a;",
            "the dark brand text is back on the dark brand red (2.91:1 on --surface-2)",
        ),
        (
            "color-scheme: light;",
            "",
            "the light theme loses native light chrome -- scrollbars and <select> go dark",
        ),
        (
            "--warn: #8f5f00;",
            "--warn: #9a6700;",
            "the chip's own 6% tint drags --warn back under 4.5:1",
        ),
        (
            "--seg-edge: #6e7681;",
            "--seg-edge: #d0d7de;",
            "the selected segment's edge stops carrying the state (under 3:1)",
        ),
    ],
)
def test_the_guard_catches_a_real_regression(
    tool: ModuleType,
    tmp_path: Path,
    real_css: str,
    old: str,
    new: str,
    why: str,
) -> None:
    _mutate(tool, tmp_path, real_css, old, new)
    assert tool.main() == 1, why


# --- the guard's own parsing ------------------------------------------------


def test_comments_are_stripped_before_the_tokens_are_read(tool: ModuleType) -> None:
    css = ":root { /* scale: 1.5 */ color-scheme: light; }"
    assert tool.strip_comments(css) == ":root {  color-scheme: light; }"


def test_a_comment_containing_a_colon_cannot_hide_a_declaration(
    tool: ModuleType, tmp_path: Path, real_css: str
) -> None:
    """The regression that made the guard's first run lie.

    Every token in `:root` is introduced by a comment, and those comments contain
    colons. Without stripping, the comment's last word is read as a property name
    and its value runs to the next `;`, taking the real declaration with it --
    so `--warn` looked absent while the file plainly declared it.
    """
    _mutate(
        tool,
        tmp_path,
        real_css,
        "  --warn: #8f5f00;",
        "  /* Primer attention.fg, darkened: #9a6700 is too light */\n  --warn: #8f5f00;",
    )
    assert tool.main() == 0


def test_the_media_query_scoping_of_color_scheme_is_enforced(
    tool: ModuleType, tmp_path: Path, real_css: str
) -> None:
    """`color-scheme` on `[data-theme="system"]` outside a media query is the bug.

    It puts a light desktop on dark native chrome; the tokens stay light, so
    nothing else notices.
    """
    # Make the declaration unconditional, which is exactly the shape the review
    # flagged. It has to sit outside every `@media` block to be caught.
    stray = ':root[data-theme="system"] { color-scheme: dark; }\n'
    _mutate(tool, tmp_path, real_css, ":root {", stray + ":root {")
    assert tool.main() == 1


def test_the_tool_reports_its_own_numbers(capsys: CaptureFixture) -> None:
    """The failures have to be legible -- an operator reads this output."""
    module = _load_tool()
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert re.search(r"--brand-ink on --brand:\s+5\.23", out), out
