"""Check the console's colour tokens actually meet WCAG contrast.

Why this exists
---------------
The dark palette shipped with `--brand: #d2232a` in *both* themes, so
brand-coloured text on a dark surface sat at 3.31:1 -- and the active sidebar
item, which is brand text on a brand tint, at 3.08:1. Both are below the 4.5:1
that 15px/600 text needs. Nothing failed, because nothing was measuring.

This reads `src/styles.css` (not `dist/`, so it runs without a build), resolves
each theme's tokens, and computes the ratios. It is deliberately a *tool* rather
than a test: `tests/infra/test_console_contrast.py` runs it and asserts the exit code,
so the numbers live in one place and both a human and CI read the same ones.

What it checks
--------------
 1. Every text token against every surface it can land on, both themes: 4.5:1.
 2. Every chip tone against its *own* 6% tint over the surface -- the composite,
    because `color-mix(in srgb, currentColor 6%, transparent)` sits the text on
    a background derived from the text, which is exactly how four of the five
    tones ended up at 4.17-4.40:1.
 3. The selected segmented control's edge against its track: 3:1, per WCAG
    1.4.11 -- the fill cannot reach it (see `styles.css`).
 4. The primary button's ink against its brand fill: 4.5:1.
 5. `color-scheme`, which is not a token and so is invisible to
    `check_theme_tokens.py`'s token-set comparison. It has to follow the *same*
    two conditions as the tokens.

Exit code 0 when everything passes, 1 otherwise, with the failures listed.
"""

from __future__ import annotations

import pathlib
import re
import sys

CSS = pathlib.Path(__file__).resolve().parent.parent / "src" / "styles.css"

# The tokens that are *text*. Each must clear 4.5:1 on every surface below.
TEXT_TOKENS = ("text", "text-dim", "brand-text", "loss", "warn", "danger")

# Tokens that are only ever a *non-text* indicator -- an outline, a border, a
# 2px ring. WCAG 1.4.11 asks 3:1 of these, not 4.5:1, and holding them to the
# text bar would be a false failure: `--focus` is never used as a colour on
# text anywhere in the console.
NON_TEXT_TOKENS = ("focus", "seg-edge")

# Everything a text token can be drawn on top of.
SURFACES = ("bg", "surface", "surface-2", "hover")

# The tones `.chip--{tone}` can take. `ok` maps to `--loss`, `neutral` to
# `--text-dim`, which is why those tokens appear here too.
CHIP_TONES = ("loss", "warn", "danger", "brand-text", "text-dim")

TEXT_MIN = 4.5
EDGE_MIN = 3.0

# The tint `.chip` mixes into the surface, as a fraction.
CHIP_TINT = 0.06


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def composite(foreground: str, alpha: float, background: str) -> str:
    """`color-mix(in srgb, foreground <alpha>, background)` -- sRGB lerp."""
    f = foreground.lstrip("#")
    b = background.lstrip("#")
    parts = []
    for i in (0, 2, 4):
        fv = int(f[i : i + 2], 16)
        bv = int(b[i : i + 2], 16)
        parts.append(round(fv * alpha + bv * (1 - alpha)))
    return "#{:02x}{:02x}{:02x}".format(*parts)


def strip_comments(css: str) -> str:
    """Remove `/* ... */` before parsing.

    Not cosmetic: the token blocks are heavily commented, and a comment that
    contains a colon (`* Values are GitHub Primer's light scale: ...`) is
    indistinguishable from a declaration to a naive `name: value` regex. It
    matches the comment's word as a "property" and then eats every real
    declaration up to the next `;`, so `--brand-text` and `--danger` vanish
    while the file plainly declares them.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def block(css: str, pattern: str, label: str) -> dict[str, str]:
    """Pull every `name: value` declaration out of the first block matching `pattern`.

    Deliberately *not* restricted to `--custom-properties`. `color-scheme` is an
    ordinary declaration that the appearance check below has to see, and an
    earlier version of this helper matched only `--*` names -- which made every
    `color-scheme` test fail no matter what the CSS said.
    """
    match = re.search(pattern, css, re.S)
    if not match:
        print(f"FAIL: could not find the {label} token block")
        sys.exit(1)
    body = strip_comments(match.group(1))
    tokens = {}
    for name, value in re.findall(r"([-\w]+)\s*:\s*([^;}]+)", body):
        tokens[name.lstrip("-")] = value.strip()
    return tokens


def main() -> int:
    css = strip_comments(CSS.read_text(encoding="utf-8"))

    themes = {
        "light": block(css, r":root\s*\{([^}]*)\}", "light (`:root`)"),
        "dark": block(css, r'\[data-theme="dark"\]\s*\{([^}]*)\}', 'dark (`[data-theme="dark"]`)'),
        "dark-system": block(
            css,
            r'@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\[data-theme=["\']?system["\']?\]\s*\{([^}]*)\}',
            "dark-via-media",
        ),
    }

    failures: list[str] = []
    print(f"css: {CSS.name}")

    for name, tokens in themes.items():
        print(f"\n== {name} ==")

        missing = [t for t in (*TEXT_TOKENS, *SURFACES) if t not in tokens]
        if missing:
            failures.append(f"{name}: missing tokens {missing}")
            print(f"  MISSING: {missing}")
            continue

        for fg in TEXT_TOKENS:
            row = []
            for bg in SURFACES:
                ratio = contrast(tokens[fg], tokens[bg])
                if ratio < TEXT_MIN:
                    failures.append(f"{name}: --{fg} on --{bg} is {ratio:.2f}:1")
                row.append(f"{bg} {ratio:5.2f}")
            worst = min(contrast(tokens[fg], tokens[bg]) for bg in SURFACES)
            print(f"  --{fg:11s} " + " | ".join(row) + f"   worst {worst:5.2f}")

        # 2. chips, against the composite they actually render on.
        print("  chips (text on its own 6% tint over --surface):")
        for tone in CHIP_TONES:
            tint = composite(tokens[tone], CHIP_TINT, tokens["surface"])
            ratio = contrast(tokens[tone], tint)
            if ratio < TEXT_MIN:
                failures.append(f"{name}: chip {tone} on its own tint is {ratio:.2f}:1")
            print(f"    {tone:11s} bg {tint}  {ratio:5.2f}")

        # 3. the non-text indicators: the focus ring and the selected segment's
        #    edge, which is what actually carries the "selected" state.
        print(f"  non-text indicators (need {EDGE_MIN}):")
        for token in NON_TEXT_TOKENS:
            for bg in ("surface-2", "hover"):
                ratio = contrast(tokens[token], tokens[bg])
                if ratio < EDGE_MIN:
                    failures.append(f"{name}: --{token} on --{bg} is {ratio:.2f}:1 (needs 3:1)")
                print(f"    --{token:10s} on --{bg:10s} {ratio:5.2f}")

        # 4. the primary button.
        ink = contrast(tokens["brand-ink"], tokens["brand"])
        if ink < TEXT_MIN:
            failures.append(f"{name}: --brand-ink on --brand is {ink:.2f}:1")
        print(f"  --brand-ink on --brand:    {ink:5.2f}")

    # 5. `color-scheme` must follow the same two conditions as the tokens.
    #    Declaring it on `[data-theme="system"]` unconditionally is what put a
    #    light desktop on dark scrollbars and a dark `<select>` popup; declaring
    #    it only in the explicit block leaves the `system`+dark-OS path without
    #    it. Both directions are checked.
    print("\n== appearance (`color-scheme`) ==")
    no_media = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)

    if re.search(r':root\[data-theme=["\']?system["\']?\]\s*\{[^}]*color-scheme', no_media, re.S):
        failures.append(
            '`color-scheme` is set on `[data-theme="system"]` outside a media query, so a '
            "light desktop on the default `system` preference renders dark native chrome"
        )

    for label, pattern, want in (
        ("light (`:root`)", r":root\s*\{([^}]*)\}", "light"),
        ('dark (`[data-theme="dark"]`)', r'\[data-theme="dark"\]\s*\{([^}]*)\}', "dark"),
        (
            "dark-via-media",
            r'@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\[data-theme=["\']?system["\']?\]\s*\{([^}]*)\}',
            "dark",
        ),
    ):
        declared = block(css, pattern, label).get("color-scheme")
        if declared != want:
            failures.append(
                f"{label} declares `color-scheme: {declared}`, want `{want}`"
                if declared
                else f"{label} does not declare `color-scheme: {want}`"
            )
        print(f"  {label:28s} color-scheme: {declared or 'MISSING'} (want {want})")

    print()
    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("OK: every text token clears 4.5:1, every segment edge clears 3:1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
