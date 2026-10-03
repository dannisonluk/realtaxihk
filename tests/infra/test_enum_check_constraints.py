"""Every enum column must carry a database-level CHECK constraint.

`SAEnum(X, native_enum=False)` defaults `create_constraint` to `False`, so the
column is emitted as a bare `VARCHAR` with nothing stopping an out-of-range
write. That default is what produced the state this file guards against: of the
20 enum-shaped columns in the shipped schema, only 2 had a constraint
(`driver_licence_submissions.status`, `driver_documents.kind`) because
`a4b8e2f6c150` hand-wrote `op.create_check_constraint` for them.

Why it matters, stated concretely: the *write* is silent and the *read* is not.
`INSERT ... role='BOGUS'` succeeds against a bare `VARCHAR`, and the next load
of that row raises `LookupError` from SQLAlchemy's `_object_value_for_elem` —
not `ValueError`, so an `except ValueError` fallback does not catch it. One bad
row therefore 500s every request that reads it until someone edits the database
by hand. A CHECK constraint rejects the write at the only point where rejecting
is cheap.

These tests do not connect to a database. They assert on the metadata, because
the metadata is what produces both the migrations and `create_all`, and the
defect was a property of the model declarations rather than of any one schema.
Trade-off: a constraint that exists in the model but was never migrated to a
live database would pass here — `tests/test_migration_schema_parity.py` covers
that direction by comparing the two schemas directly.
"""

from __future__ import annotations

from typing import Any

from app.models import Base

# The two constraints that predate this guard and exist in the shipped schema.
# Named here so a rename shows up as a diff rather than passing silently.
_PREEXISTING = {
    ("driver_licence_submissions", "status", "ck_licence_review_status"),
    ("driver_documents", "kind", "ck_document_kind"),
}


def _enum_columns() -> list[tuple[str, str, Any]]:
    """Every (table, column, type) where the column is a SQLAlchemy Enum."""
    out: list[tuple[str, str, Any]] = []
    for table_name, table in sorted(Base.metadata.tables.items()):
        for column in table.columns:
            if type(column.type).__name__ == "Enum":
                out.append((table_name, column.name, column.type))
    return out


def test_there_is_at_least_one_enum_column_to_check():
    """A guard on the guard: if the walker breaks, the tests below pass vacuously."""
    assert len(_enum_columns()) >= 20


def test_every_enum_column_declares_create_constraint():
    missing = [
        f"{table}.{column}"
        for table, column, coltype in _enum_columns()
        if not getattr(coltype, "create_constraint", False)
    ]
    assert not missing, (
        "these enum columns would be emitted without a CHECK constraint "
        f"(add create_constraint=True): {missing}"
    )


def test_every_enum_constraint_is_named_with_the_ck_prefix():
    """The convention is `ck_<table>_<column>`; a mismatch means a rename slipped.

    `name=` drives the rendered constraint name when `create_constraint=True`,
    so an un-renamed `name` (e.g. `user_role`) silently produces a constraint
    called `user_role` while every other one starts with `ck_`.
    """
    offenders = [
        f"{table}.{column} -> {coltype.name!r}"
        for table, column, coltype in _enum_columns()
        if getattr(coltype, "create_constraint", False)
        and not str(coltype.name or "").startswith("ck_")
    ]
    assert not offenders, f"enum constraints without a ck_ name: {offenders}"


def test_the_two_preexisting_constraints_kept_their_names():
    """`a4b8e2f6c150` created these two by hand; a rename would orphan them.

    The migration that adds the other 18 does not touch these, so if the model's
    `name=` drifted the live database would keep `ck_*` while fresh databases
    (and `create_all`) got something else — the two schemas would diverge on
    exactly the constraints this file exists to protect.
    """
    actual = {
        (table, column, coltype.name)
        for table, column, coltype in _enum_columns()
        if (table, column) in {(t, c) for t, c, _ in _PREEXISTING}
    }
    assert actual == _PREEXISTING
