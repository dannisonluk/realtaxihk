"""Shared scaffolding for every ORM model in `app.models`.

This module exists so the model files can be split by bounded context
(`user.py`, `order.py`, `fleet.py`, `admin.py`, `licence.py`) without each one
re-declaring the same base class and column helpers. It is **private**
(leading underscore) and is not re-exported from `app.models.__init__`; callers
should keep importing `Base` from `app.models`, which is the public path.

Why `Base` lives in its own file
--------------------------------
SQLAlchemy's declarative registry is keyed on the `DeclarativeBase` subclass.
In a single-module `models.py` it was obvious that every mapped class shared
one base. Once the models are split across files, a second `Base` created by
accident would produce two independent registries, and the symptom is not a
crash — it is a `relationship()` that resolves to nothing or two mapper
registrations for one table. One definition, imported by all five model
modules, is what makes that impossible.

Design notes (unchanged from the pre-split module docstring):
- Money columns: Numeric(10,2) — HKD has no sub-cent usage on meter fares;
  ledger keeps signed amounts with an append-only constraint.
- Geo columns: PostGIS geography(Point,4326) (srid 4326 matches Google Maps).
- Orders carry a snapshot of the fare estimate (tariff_version + totals) so
  historical orders stay auditable even after tariff changes.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """The single declarative base for the whole domain.

    Import this from `app.models` (public) rather than from here, so the
    private layout can change without breaking callers.
    """


__all__ = ["Base", "Mapped", "mapped_column", "relationship"]
