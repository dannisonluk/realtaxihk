"""Money helpers must annotate what their body actually accepts.

Why this exists
---------------
`app/core/money.py`'s four formatters were annotated `v: Decimal` while their
bodies were `Decimal(v)` — and `money_str`'s own docstring said it "accepts
anything `Decimal()` accepts (a `Decimal` from the DB, a string, an int)".
A too-narrow annotation on a widening body does not make the call unsafe; it
only makes the type checker disagree with the code.

That was not theoretical. `Settings.weekly_fee_hkd` is an `int` (`200`), and
`money_str(settings.weekly_fee_hkd)` is the *intended* way to render it as
`"200.00"` — the call site carries a comment saying so. So the editor flagged a
correct line (`app/api/admin/settlement.py` -- it was `app/api/admin.py:689`/`:725`
before that module was split, plus
`tests/test_admin_settlement_preview.py:265` and a string literal in
`tests/test_analytics_admin.py`), while CI stayed green, because ruff does not
infer types.

The fix is the shared `MoneyInput` union in `app/core/money.py`. This test is
what keeps it: without it, the next helper written in the same shape would copy
`Decimal` from its neighbour and the same false error would come back.

What is asserted
----------------
1. No function under `app/` annotates a parameter as bare `Decimal` while its own
   body converts that parameter with `Decimal(...)`.
2. The canonical formatters in `app/core/money.py` take `MoneyInput`.
3. The detector itself can fail — otherwise "no violations" and "matched nothing"
   would be indistinguishable. This repo has been burned by that before (a
   boundary test whose "naive fix" reported 0 violations because it never leaked).
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
_MONEY_MODULE = _APP / "core" / "money.py"
_CANONICAL_FORMATTERS = {"money_str", "meter_str", "quantize_money", "ratio_str"}


def _converts_with_decimal(func: ast.AST, name: str) -> bool:
    """True if `func`'s body contains a `Decimal(<name>)` call."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Decimal"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == name
        ):
            return True
    return False


def _narrow_params(source: str) -> list[tuple[str, str]]:
    """`(function, parameter)` pairs that are annotated `Decimal` but widened.

    A parameter annotated exactly `Decimal` whose body then calls
    `Decimal(<param>)` is declaring a contract narrower than the one it
    implements. `Decimal | int | str` and `MoneyInput` are both fine.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in [*node.args.args, *node.args.kwonlyargs]:
            if arg.annotation is None:
                continue
            if ast.unparse(arg.annotation) != "Decimal":
                continue
            if _converts_with_decimal(node, arg.arg):
                found.append((node.name, arg.arg))
    return found


def _app_sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(_APP.parent).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(_APP.rglob("*.py"))
    ]


def test_no_narrow_decimal_parameters_in_app() -> None:
    offenders = [
        f"{path}: {func}({param})"
        for path, source in _app_sources()
        for func, param in _narrow_params(source)
    ]
    assert offenders == [], (
        "these parameters are annotated `Decimal` but their body converts with "
        "`Decimal(...)`, so a legitimate int/str call site is a false type error. "
        "Annotate them `MoneyInput` (see app/core/money.py):\n  " + "\n  ".join(offenders)
    )


def test_the_canonical_formatters_take_the_shared_union() -> None:
    tree = ast.parse(_MONEY_MODULE.read_text(encoding="utf-8"))
    annotations: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _CANONICAL_FORMATTERS:
            annotations[node.name] = ast.unparse(node.args.args[0].annotation)

    missing = _CANONICAL_FORMATTERS - set(annotations)
    assert not missing, f"formatter(s) not found in {_MONEY_MODULE.name}: {sorted(missing)}"
    for name, annotation in annotations.items():
        assert annotation == "MoneyInput", (
            f"{name} takes {annotation!r}; all four formatters share `MoneyInput` so "
            "there is one name to grep and one place to change"
        )


def test_the_detector_can_actually_fail() -> None:
    """Control: a guard that matches nothing would pass on any codebase."""
    narrow = (
        "from decimal import Decimal\n\ndef f(v: Decimal) -> str:\n    return str(Decimal(v))\n"
    )
    assert _narrow_params(narrow) == [("f", "v")]

    widened = "def f(v: MoneyInput) -> str:\n    return str(Decimal(v))\n"
    assert _narrow_params(widened) == []

    # Annotated `Decimal`, but the body never converts it — not this smell.
    untouched = "def f(v: Decimal) -> str:\n    return str(v)\n"
    assert _narrow_params(untouched) == []
