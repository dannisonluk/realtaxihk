"""Check the built CSS keeps the two dark token blocks in step.

Both ways a dark theme can be reached -- an explicit `data-theme="dark"` and the
`prefers-color-scheme: dark` media query -- must define the *same* set of custom
properties, or one of the two routes renders with a half-applied palette.
"""

import pathlib
import re
import sys

web = pathlib.Path(__file__).resolve().parent.parent
css_path = next((web / "dist" / "assets").glob("index-*.css"))
css = css_path.read_text(encoding="utf-8")
html = (web / "dist" / "index.html").read_text(encoding="utf-8")

PROP = re.compile(r"--[a-z0-9-]+\s*:")


def props(block: str) -> list[str]:
    return sorted(set(PROP.findall(block)))


explicit = re.search(r'\[data-theme=["\']?dark["\']?\]\s*\{(.*?)\}', css, re.S)
print(f"css: {css_path.name}")

if not explicit:
    print('FAIL: no explicit [data-theme="dark"] block')
    sys.exit(1)

explicit_props = props(explicit.group(1))
print(f"explicit dark block:   {len(explicit_props)} properties")

# The media block must be *scoped* to the `system` preference, so an explicit
# light/dark choice is never overridden by the OS. Then compare the two token
# sets. Both must be read from their own selector body -- scanning to the end of
# the file picks up later rules and reports a mismatch that does not exist.
media = re.search(
    r'@media\s*\(prefers-color-scheme:\s*dark\)\s*\{(\s*:root\[data-theme=["\']?system["\']?\]\s*\{)(.*?)}\s*}',
    css,
    re.S,
)
if not media:
    print("FAIL: no prefers-color-scheme dark block scoped to [data-theme=system]")
    sys.exit(1)

scoped_selector = media.group(1).strip()
media_props = props(media.group(2))
print(f"system-media dark block: {len(media_props)} properties (scoped: {scoped_selector})")

if explicit_props == media_props:
    print("OK: identical token sets")
else:
    print("MISMATCH")
    print("  explicit-only:", sorted(set(explicit_props) - set(media_props)))
    print("  media-only:   ", sorted(set(media_props) - set(explicit_props)))
    sys.exit(1)

# The boot script must be inline and ahead of the bundle, or the first paint
# flashes the wrong theme. It is matched on `dataset.theme` -- the *property*
# form of the attribute -- because that is what the source actually writes;
# looking for the literal string "data-theme" here finds nothing and reads as a
# missing script when nothing is missing.
boot = re.search(r"documentElement\.dataset\.theme", html)
boot_is_inline = bool(boot)
boot_is_first = False
if boot:
    bundle = html.find('type="module"')
    boot_is_first = bundle == -1 or boot.start() < bundle
print(f"boot script inline:      {boot_is_inline}")
print(f"boot script before bundle: {boot_is_first}")
if not (boot_is_inline and boot_is_first):
    print("FAIL: theme boot script missing or runs after the bundle")
    sys.exit(1)

if "localStorage" not in html:
    print("FAIL: boot script does not consult storage")
    sys.exit(1)

print("OK: theme boot script present and pre-bundle")
