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

The dispute split is the one case where the role is not a fixed property of the
route: `POST /disputes/{id}/resolve` is reachable by OPERATIONS for a decision
that moves no money, and by FINANCE for a resolution that moves money. The reason
is separation of duties — whoever judges that a driver behaved badly must not
thereby authorise the payout. Enforced in the handler because the decision is in
the body, not the path. The role check is a **whitelist matched to the decision**,
not a rank floor: rank made FINANCE ⊇ OPERATIONS, so a single FINANCE admin could
judge and pay in one request. SUPER_ADMIN is the documented break-glass in both
sets, and a money resolution additionally refuses the admin who is assigned to
the case (the assigned judge) unless they are SUPER_ADMIN.

Deliberately NOT `SUPPORT`: answering a question and deciding one are different
jobs, and merged, front-line support inherits the KYC gate."""

from __future__ import annotations

from app.core.deps import require_role
from app.models import AdminRole

_require_operations = require_role(AdminRole.OPERATIONS)


_require_finance = require_role(AdminRole.FINANCE)


_require_super = require_role(AdminRole.SUPER_ADMIN)
