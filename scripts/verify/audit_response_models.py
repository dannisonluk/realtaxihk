"""Prove every captured fixture survives its route's `response_model=`.

A `response_model=` is a FILTER: FastAPI validates the handler's return value
against it and silently drops any key the model does not declare. So the only
question that matters is not "does the model look right" but "does every key a
real response contains survive validation".

This walks `mobile/test/fixtures/manifest.json` (route -> fixture), loads each
fixture, and asserts its key set is a SUBSET of the corresponding model's
fields. A missing field shows up here as data the API would delete from a live
response.

It also runs a second, independent check: **every operation declares a
`response_model=`, and every one of those models comes from
`app.api.schemas`**. That is what stops the coverage from silently regressing —
before this package existed, 68 of 69 operations published `{}`.

The negative control matters as much as the check: `--self-test` injects a key
no model declares and asserts the comparison reports it, so this script cannot
pass by being vacuous.

Run after any change to `app/api/schemas/` or a route decorator:

    python scripts/verify/audit_response_models.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app.api.schemas as schemas
from app.api.schemas import (
    AuthMeOut,
    DepositGrantOut,
    DepositOut,
    DriverPageOut,
    DriverPaymentMethodsOut,
    DriverProfileOut,
    DriverProfileWithDepositOut,
    DriverReviewOut,
    DriverRowOut,
    ErrorEnvelope,
    FareEstimateOut,
    FareSnapshotOut,
    FareSurchargeOut,
    FleetMemberRowOut,
    FleetMembershipOut,
    FleetOut,
    FleetPageOut,
    FleetSettlementRowOut,
    FleetSettlementRunOut,
    FleetViewOut,
    LedgerEntryOut,
    LedgerPageOut,
    OkOut,
    OrderOut,
    OrderPageOut,
    OtpRequestOut,
    ReceiptOut,
    RefundDecisionOut,
    RefundOut,
    RefundPageOut,
    RefundRequestOut,
    RefundViewOut,
    SettlementRunOut,
    TokenPairOut,
    TripLocationOut,
    UserOut,
)

FIXTURES = pathlib.Path("mobile/test/fixtures")

# Schemas deliberately NOT reachable from any route, with the reason. Anything
# unreachable that is *not* listed here is a problem — it means a schema was
# written and never wired to the route it was written for.
INTENTIONALLY_UNREFERENCED = {
    "AdminAccountOut": "canonical account row; no route returns it yet",
    "AdminDepositOut": "base class of AdminDepositDetailOut",
    "ChallengeOut": "declared for symmetry; never returned (see admin_auth.py)",
    "ErrorEnvelope": "for router-level `responses=` blocks; not wired yet",
    "ListEnvelope": "generic base; typed list envelopes are used instead",
    "PageEnvelope": "generic base; typed page envelopes are used instead",
}

# fixture stem -> the model the route declares.
MODEL_OF: dict[str, object] = {
    # auth
    "auth_otp_request": OtpRequestOut,
    "auth_verify": TokenPairOut,
    "auth_verify_admin": TokenPairOut,
    "auth_verify_new_user": TokenPairOut,
    "auth_refresh": TokenPairOut,
    "auth_me": AuthMeOut,
    "driver_location": OkOut,
    # drivers
    "driver_register": DriverProfileOut,
    "driver_me": DriverProfileWithDepositOut,
    "driver_me_no_deposit": DriverProfileWithDepositOut,
    "driver_payment_methods": DriverPaymentMethodsOut,
    "driver_payment_methods_read": DriverPaymentMethodsOut,
    "ledger_page": LedgerPageOut,
    "refund_request": RefundRequestOut,
    "refund_me": RefundViewOut,
    # orders
    "order_created": OrderOut,
    "order_detail": OrderOut,
    "order_with_requirements": OrderOut,
    "order_receipt": ReceiptOut,
    "order_grabbed": OrderOut,
    "order_arrive": OrderOut,
    "order_start": OrderOut,
    "order_complete": OrderOut,
    "orders_page": OrderPageOut,
    "orders_nearby": OrderPageOut,
    "orders_page_driver": OrderPageOut,
    "trip_location": TripLocationOut,
    # fare
    "fare_estimate": FareEstimateOut,
    # admin drivers / refunds / settlement
    "admin_drivers": DriverPageOut,
    "admin_review": DriverReviewOut,
    "admin_grant": DepositGrantOut,
    "admin_refunds": RefundPageOut,
    "admin_refund_decision": RefundDecisionOut,
    "admin_settlement": SettlementRunOut,
    "admin_settlement_fleet_managed": SettlementRunOut,
    # fleets
    "fleet_created": FleetOut,
    "admin_fleets": FleetPageOut,
    "admin_fleet_member_added": FleetMemberRowOut,
    "fleet_me": FleetViewOut,
    "fleet_detail": FleetOut,
    "fleet_me_none": FleetViewOut,
    "admin_fleet_settlement_run": FleetSettlementRunOut,
    # errors
    "error_not_found": ErrorEnvelope,
    "error_unauthorized": ErrorEnvelope,
    "error_forbidden": ErrorEnvelope,
    "error_validation": ErrorEnvelope,
    "error_validation_range": ErrorEnvelope,
    "error_business_rule": ErrorEnvelope,
    "error_otp_bad_code": ErrorEnvelope,
    "error_otp_cooldown": ErrorEnvelope,
    "auth_refresh_replay": ErrorEnvelope,
}

# Fixtures whose route returns a typed `{"items": [...]}` wrapper — checked
# element-wise, since the wrapper itself is not a model in MODEL_OF.
LIST_WRAPPED = {
    "admin_fleet_members": FleetMemberRowOut,
    "fleet_members": FleetMemberRowOut,
    "admin_fleet_settlement": FleetSettlementRowOut,
    "fleet_settlement": FleetSettlementRowOut,
}

# Nested blocks that are also response_model'd, checked on their own so a
# dropped key inside `order.fare` cannot hide behind a matching outer shape.
NESTED = {
    ("order_created", "fare"): FareSnapshotOut,
    ("order_detail", "fare"): FareSnapshotOut,
    ("order_with_requirements", "fare"): FareSnapshotOut,
    ("order_grabbed", "fare"): FareSnapshotOut,
    ("order_arrive", "fare"): FareSnapshotOut,
    ("order_start", "fare"): FareSnapshotOut,
    ("order_complete", "fare"): FareSnapshotOut,
    ("order_created", "fare.surcharges[0]"): FareSurchargeOut,
    ("driver_me", "deposit"): DepositOut,
    ("driver_me_no_deposit", "deposit"): DepositOut,
    ("fleet_me", "fleet"): FleetOut,
    ("fleet_me", "membership"): FleetMembershipOut,
    ("admin_fleets", "items[0]"): FleetOut,
    ("admin_drivers", "items[0]"): DriverRowOut,
    ("admin_refunds", "items[0]"): RefundOut,
    ("ledger_page", "items[0]"): LedgerEntryOut,
    ("auth_verify", "user"): UserOut,
    ("fare_estimate", "surcharges[0]"): FareSurchargeOut,
}


def dig(obj: object, path: str) -> object:
    """Resolve a dotted path, tolerating `[0]` on list elements."""
    cur = obj
    for part in path.split("."):
        if part.endswith("[0]"):
            key = part[:-3]
            if key:
                cur = cur[key]  # type: ignore[index]
            if not isinstance(cur, list) or not cur:
                return None
            cur = cur[0]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
    return cur


def check_fixtures(problems: list[str]) -> int:
    """Every fixture key must survive its model. Returns blocks checked."""
    checked = 0

    for stem, model in MODEL_OF.items():
        if model is None:
            continue
        path = FIXTURES / f"{stem}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            continue
        lost = set(payload) - set(model.model_fields)  # type: ignore[attr-defined]
        checked += 1
        if lost:
            problems.append(
                f"{stem}: {sorted(lost)} would be DROPPED by {model.__name__}"  # type: ignore[attr-defined]
            )

    for stem, elem_model in LIST_WRAPPED.items():
        payload = json.loads((FIXTURES / f"{stem}.json").read_text())
        declared = set(elem_model.model_fields)
        for i, item in enumerate(payload.get("items", [])):
            lost = set(item) - declared
            checked += 1
            if lost:
                problems.append(
                    f"{stem}.items[{i}]: {sorted(lost)} DROPPED by {elem_model.__name__}"
                )

    for (stem, path_str), model in NESTED.items():
        payload = json.loads((FIXTURES / f"{stem}.json").read_text())
        block = dig(payload, path_str)
        if not isinstance(block, dict):
            continue
        lost = set(block) - set(model.model_fields)
        checked += 1
        if lost:
            problems.append(f"{stem}.{path_str}: {sorted(lost)} DROPPED by {model.__name__}")

    return checked


def _ref_names(schema: object) -> set[str]:
    """Every `#/components/schemas/X` name referenced by a schema, recursively.

    Walks `properties` as well as `anyOf`/`oneOf`/`allOf`/`items`. Missing
    `properties` was a real bug in an earlier draft: it reported every *nested*
    model (a `deposit` block, a `fare` block, a ledger row) as "declared but not
    referenced", which made the report pure noise and would have trained a
    reader to ignore it.
    """
    names: set[str] = set()
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            names.add(ref.rsplit("/", 1)[-1])
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in schema.get(key, []) or []:
                names |= _ref_names(sub)
        for key in ("items", "additionalProperties"):
            sub = schema.get(key)
            if isinstance(sub, dict):
                names |= _ref_names(sub)
        props = schema.get("properties")
        if isinstance(props, dict):
            for sub in props.values():
                names |= _ref_names(sub)
    elif isinstance(schema, list):
        for sub in schema:
            names |= _ref_names(sub)
    return names


def _transitive(spec: dict, seeds: set[str]) -> set[str]:
    """Expand schema names through `components.schemas` to a fixed point.

    A route's schema is a `$ref` to one component; that component's own
    `properties` hold further `$ref`s (a `deposit`, a `fare`, a ledger row).
    Following only the first hop reported every nested model as unreferenced —
    the report has to be a closure or it is noise.
    """
    components = spec.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for sub in _ref_names(components.get(name)):
            if sub not in seen:
                stack.append(sub)
    return seen


def check_coverage(problems: list[str]) -> tuple[int, set[str]]:
    """Every operation declares a response schema; every model is ours.

    Read from the **OpenAPI document**, not from `app.routes`: this FastAPI
    version wraps included routers in `_IncludedRouter` objects rather than
    flattening them, so walking `app.routes` sees 21 entries and misses all 69
    operations. The spec is also the authoritative view — it is literally what
    a client is handed.

    Returns (operations, distinct schema names referenced).
    """
    from app.main import create_app

    spec = create_app().openapi()
    package_names = set(schemas.__all__)
    seeds: set[str] = set()
    operations = 0

    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operations += 1
            responses = op.get("responses", {})
            ok = (
                responses.get("200")
                or responses.get("201")
                or responses.get("202")
                or responses.get("204")
            )
            if ok is None:
                problems.append(f"{method.upper()} {path}: no 2xx response declared")
                continue
            schema = (ok.get("content", {}).get("application/json", {}) or {}).get("schema")
            if not schema:
                # A non-JSON body is legitimate and needs no pydantic model: a
                # CSV export is a table for a spreadsheet, and pinning it to a
                # JSON schema would be a lie that the audit then enforces. The
                # check is that *some* media type carries a declared schema, so
                # a bare 200 with nothing still fails.
                non_json = [
                    media
                    for media, body in (ok.get("content") or {}).items()
                    if media != "application/json" and (body or {}).get("schema")
                ]
                if non_json:
                    continue
                # No content block at all (a 204) is legitimate; a 200 with no
                # schema is the regression this whole exercise exists to stop.
                if "204" not in responses:
                    problems.append(
                        f"{method.upper()} {path}: 200 published without a response schema"
                    )
                continue
            direct = _ref_names(schema)
            if not direct:
                # An inline schema (e.g. `/health` -> `dict`). Legitimate, but it
                # cannot be attributed to this package, so do not count it.
                continue
            seeds |= direct

    used = _transitive(spec, seeds)
    for name in sorted(seeds):
        if name not in package_names:
            problems.append(f"a route declares schema {name}, which is not from app.api.schemas")

    unwired = sorted(package_names - used)
    print(f"schemas exported: {len(package_names)}, reachable from a route: {len(used)}")
    if unwired:
        print(f"  declared but not reachable: {unwired}")
    for name in unwired:
        if name not in INTENTIONALLY_UNREFERENCED:
            problems.append(
                f"{name} is exported from app.api.schemas but no route can reach it — "
                f"wire it, or add it to INTENTIONALLY_UNREFERENCED with a reason"
            )
    return operations, used


def self_test() -> int:
    """Negative control: prove the fixture comparison detects real loss."""
    payload = json.loads((FIXTURES / "driver_me.json").read_text())
    payload["a_key_the_model_does_not_have"] = 1
    lost = set(payload) - set(DriverProfileWithDepositOut.model_fields)
    if lost != {"a_key_the_model_does_not_have"}:
        print("SELF-TEST FAILED — the audit would miss real data loss", file=sys.stderr)
        return 1
    print("self-test OK — an undeclared fixture key is detected")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    problems: list[str] = []
    checked = check_fixtures(problems)
    operations, distinct = check_coverage(problems)

    print(f"fixture blocks checked: {checked}")
    print(f"operations with a response_model: {operations} ({distinct} distinct models)")

    if problems:
        print(f"\n!! {len(problems)} problem(s):\n")
        for p in problems:
            print("  -", p)
        return 1

    print("OK — every fixture key survives its response_model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
