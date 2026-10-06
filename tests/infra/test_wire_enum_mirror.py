"""Every enum that crosses the wire must exist in the Dart mirror, member for member.

The app decodes enums through `fromWire`, which **throws** on a value it does not
know rather than falling back to a default. That is the right call -- silently
mapping an unknown state to, say, `CREATED` would show a driver the wrong screen
-- but it means a member added on the server and forgotten in Dart is not a
cosmetic bug: it is a screen that dies. It has already happened once, with
`LedgerEntryType`, and nothing caught it.

Nothing else can catch it either:

- `response_model=` cannot. Every one of these fields is declared `str` on the
  response schemas (`OrderOut.status`, `LedgerEntryOut.entry_type`, ...), so
  FastAPI validates the value as a string and is happy with anything.
- `verify_contract.dart` cannot, on its own. It decodes the captured fixtures
  with the real models, so it proves the *values that happen to appear in a
  fixture* decode. A member no fixture exercises is invisible to it.

So this is the only check that compares the two member sets directly. It works
by name: each enum in `mobile/lib/models/enums.dart` mirrors the backend enum of
the same name, and the `wire` token on each Dart member must equal the server's
value.

Two rules, and the second is what stops this rotting:

1. A name in both places must have an identical member set.
2. Every backend enum must be *classified* -- either it is mirrored, or it is
   named in `NOT_MIRRORED` with a reason. A new backend enum therefore fails
   this test until someone decides which it is. Without rule 2 the check would
   quietly ignore exactly the case it exists for.

There is a negative control at the bottom: the comparison is fed a deliberately
broken pair and must report it, so this file cannot pass by being vacuous.
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DART_ENUMS = ROOT / "mobile" / "lib" / "models" / "enums.dart"
APP = ROOT / "app"

# Backend enums the app deliberately does not mirror, and why. Each reason is a
# decision, not a to-do: if one of these ever needs to reach a screen, mirror it
# and delete the entry rather than editing the reason.
NOT_MIRRORED: dict[str, str] = {
    "AdminRole": "admin console only; the console mirrors it in TypeScript.",
    "DestinationStatus": "premium-destination lifecycle, managed in the console.",
    "DisputeCategory": "admin dispute console.",
    "DisputePartyKind": "admin dispute console.",
    "DisputeResolution": "admin dispute console (the ruling an operator picks).",
    "DisputeSeverity": "admin dispute console.",
    "DisputeSource": "admin dispute console.",
    "DisputeStatus": "admin dispute console.",
    "DocumentKind": "licence document metadata; admin review only.",
    "FixedOfferStatus": (
        "the driver's fixed-offer screen reads the raw token "
        "(`FixedOffer.status` is a `String`), so there is no Dart enum to keep in step."
    ),
    "Gender": (
        "a local string list in `profile_setup_screen.dart` on purpose: nothing "
        "branches on the value, it is only echoed back, so an enum would just be "
        "a second place for a new server member to throw."
    ),
    "Granularity": "analytics bucket size; internal to `analytics_service`, never serialized.",
    "LicenceReviewStatus": "licence review queue, admin only.",
    "OrderEventType": (
        "the order timeline is rendered in the console; the app has no timeline view."
    ),
    "OrderFareMode": (
        "`Order.fareMode` / `Fare.fareMode` are `String?` in Dart; the app labels "
        "the fare it is given rather than the mode it came from."
    ),
    "OrderParty": (
        "who raised an interruption; the app already knows which side it is, so it "
        "never decodes the counterparty token."
    ),
    "PaymentMethod": (
        "`DriverPaymentMethods` is a static string list, and the app posts the "
        "tokens it offers; nothing branches on a member."
    ),
    "RecurringFrequency": "recurring rides are not in the app yet.",
    "RecurringStatus": "recurring rides are not in the app yet.",
}

_ENUM_LINE = re.compile(r"^enum (\w+) \{$")
# Deliberately not anchored to the closing paren: most members are
# `name('WIRE')`, but `Tunnel` carries extra label arguments
# (`crossHarbour('cross_harbour', '過海隧道', 'Cross-harbour')`) and the wire token
# is still the first one. Requiring `)` here silently parsed `Tunnel` as empty.
_MEMBER_LINE = re.compile(r"^  (\w+)\('([^']*)'")


def _backend_enums() -> dict[str, set[str]]:
    """Every `str`-valued Enum declared under `app/`, by name.

    Parsed rather than imported: an import would only see what some `__init__`
    chose to re-export, and a class nobody exported is exactly the kind of thing
    that slips through.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("Enum" in ast.unparse(base) for base in node.bases):
                continue
            values = {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            }
            if values:
                found[node.name] = values
    return found


def _dart_enums() -> dict[str, set[str]]:
    """Every enum in `enums.dart`, mapped to the wire tokens its members carry.

    The enum's closing brace sits at column 0, which is what makes this a scan
    rather than a parser: the bodies contain `switch` expressions with braces of
    their own, but those are indented.
    """
    lines = DART_ENUMS.read_text(encoding="utf-8").splitlines()
    found: dict[str, set[str]] = {}
    index = 0
    while index < len(lines):
        match = _ENUM_LINE.match(lines[index])
        if not match:
            index += 1
            continue
        name = match.group(1)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index] != "}":
            body.append(lines[index])
            index += 1
        tokens = {m.group(2) for line in body if (m := _MEMBER_LINE.match(line))}
        if not tokens:
            raise AssertionError(
                f"{DART_ENUMS.name}::{name} parsed to zero members -- the member "
                "syntax changed and this scan is now blind"
            )
        found[name] = tokens
        index += 1
    return found


def _mismatches(dart: dict[str, set[str]], backend: dict[str, set[str]]) -> list[str]:
    """Names whose member sets differ, each with what is missing on which side."""
    problems: list[str] = []
    for name in sorted(set(dart) & set(backend)):
        if dart[name] == backend[name]:
            continue
        missing_in_dart = backend[name] - dart[name]
        missing_on_server = dart[name] - backend[name]
        detail = []
        if missing_in_dart:
            detail.append(f"the app cannot decode {sorted(missing_in_dart)}")
        if missing_on_server:
            detail.append(
                f"the app expects {sorted(missing_on_server)}, which the server never sends"
            )
        problems.append(f"{name}: " + "; ".join(detail))
    return problems


def test_every_dart_enum_matches_its_backend_twin() -> None:
    dart = _dart_enums()
    backend = _backend_enums()

    orphans = sorted(set(dart) - set(backend))
    assert not orphans, (
        f"`enums.dart` declares {orphans}, which no backend enum defines. Either the "
        "server enum was renamed and the app was not, or the app invented a wire type."
    )

    assert not (problems := _mismatches(dart, backend)), (
        "the Dart mirror and the server disagree on enum members:\n  "
        + "\n  ".join(problems)
        + "\n\nA member added on the server must be added to `enums.dart` too: "
        "`fromWire` throws on an unknown token, so a screen that receives one dies."
    )


def test_every_backend_enum_is_classified() -> None:
    """A new enum forces a decision instead of silently going unchecked."""
    dart = _dart_enums()
    backend = _backend_enums()

    unclassified = sorted(set(backend) - set(dart) - set(NOT_MIRRORED))
    assert not unclassified, (
        f"{unclassified} exist on the server and are neither mirrored in "
        "`mobile/lib/models/enums.dart` nor listed in `NOT_MIRRORED`. Decide which "
        "it is -- mirroring it, or recording why the app does not need it."
    )

    stale = sorted(set(NOT_MIRRORED) & set(dart))
    assert not stale, (
        f"{stale} are listed in `NOT_MIRRORED` but are mirrored after all. Delete the "
        "entry rather than leaving a reason that contradicts the code."
    )

    missing = sorted(set(NOT_MIRRORED) - set(backend))
    assert not missing, f"`NOT_MIRRORED` names {missing}, which no longer exist on the server."


def test_every_exclusion_states_a_reason() -> None:
    thin = sorted(name for name, reason in NOT_MIRRORED.items() if len(reason.strip()) < 10)
    assert not thin, f"{thin} are excluded without a real reason."


def test_the_comparison_reports_a_missing_member() -> None:
    """The negative control: a check that cannot fail is not a check."""
    problems = _mismatches(
        dart={"OrderStatus": {"CREATED", "COMPLETED"}},
        backend={"OrderStatus": {"CREATED", "COMPLETED", "INTERRUPTED"}},
    )
    assert len(problems) == 1
    assert "INTERRUPTED" in problems[0]


def test_the_comparison_reports_a_member_the_server_dropped() -> None:
    problems = _mismatches(
        dart={"OrderStatus": {"CREATED", "COMPLETED"}},
        backend={"OrderStatus": {"CREATED"}},
    )
    assert len(problems) == 1
    assert "COMPLETED" in problems[0]


def test_the_comparison_stays_quiet_on_a_match() -> None:
    assert _mismatches(dart={"A": {"X"}}, backend={"A": {"X"}}) == []
