"""Domain service layer, grouped by bounded context.

The groups mirror the modules the architecture document already names, so the
layout is not a taxonomy invented here:

  auth/     authentication, OTP, identity, phone re-verification
  licence/  driver documents: submission, review, object storage
  order/    orders, dispatch, the fare engine, geo lookups
  ledger/   the append-only ledger, its settlement jobs, refunds
  fleet/    fleets — licensed operators, billed as a roster
  admin/    the admin console's own services
  infra/    cross-cutting: notification, background jobs

Imports use the full path (`from app.services.ledger.ledger_service import
LedgerService`) rather than re-exports from each group's `__init__`. A re-export
layer would read better, but it would make every group's `__init__` an import hub
for modules that already import each other, and the cycle it invites is a worse
failure than a longer path. `app/models/` can re-export safely because model
modules only ever import `_base`.
"""
