"""Role guards, defined once, so the policy is readable in one place.

  KYC / driver state  -> OPERATIONS  (a compliance judgement, not a money one)
  money movement      -> FINANCE     (grant, adjust, settlement, refund)
  dispute judgement   -> OPERATIONS  (conduct is not a money act)
  dispute resolution that moves money -> FINANCE, checked per-request
  admin accounts      -> SUPER_ADMIN (the only role that may change roles)

Read endpoints stay on `require_admin` — every role may look. Only the write
endpoints are narrowed, so a route that missed a guard is merely wide, never open,
and the route-table audit in `tests/test_security_hardening.py` proves each one
still carries a live-state guard.

The global role model is a **rank hierarchy**: FINANCE is senior to OPERATIONS
and also satisfies OPERATIONS gates (owner decision 2026-10-05). The dispute
split is the one route where the role check is not a rank floor but a
decision-matched whitelist: conduct-only decisions require OPERATIONS or
SUPER_ADMIN, money-moving decisions require FINANCE or SUPER_ADMIN, and a money
resolution additionally refuses the admin assigned to the case. That is a
deliberate per-handler business rule — the assigned judge should not authorise
the payout — not a claim that OPERATIONS and FINANCE are mutually exclusive
globally. Enforced in the handler because the decision is in the body, not the
path.

Deliberately NOT `SUPPORT`: answering a question and deciding one are different
jobs, and merged, front-line support inherits the KYC gate."""

from __future__ import annotations

from app.core.deps import require_role
from app.models import AdminRole

_require_operations = require_role(AdminRole.OPERATIONS)


_require_finance = require_role(AdminRole.FINANCE)


_require_super = require_role(AdminRole.SUPER_ADMIN)
