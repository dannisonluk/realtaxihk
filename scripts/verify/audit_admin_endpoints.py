"""Prove every console API call resolves against the backend OpenAPI document.

`tsc --noEmit` proves the console is self-consistent, not that it agrees with
the server. This walks the path literals in
`admin-web/web/src/api/endpoints.ts` and checks each one exists with the HTTP
method the console calls (`get`/`post`/`patch`/`del`/`fetchBlob`).

It deliberately builds a bare FastAPI app from `app.api.router` instead of
calling `create_app()`, so the check does not need Redis or a database. A URL
template such as `/admin/drivers/${encodeURIComponent(driverId)}` is normalized
to OpenAPI's `/admin/drivers/{driver_id}` before the lookup.

Run after changing `endpoints.ts`, `app/api/`, or any URL in either side:

    python scripts/verify/audit_admin_endpoints.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ENDPOINTS = ROOT / "admin-web" / "web" / "src" / "api" / "endpoints.ts"

PARAM_RENAMES = {
    "accountId": "account_id",
    "disputeId": "dispute_id",
    "driverId": "driver_id",
    "driverProfileId": "driver_profile_id",
    "fleetId": "fleet_id",
    "id": "destination_id",
    "orderId": "order_id",
    "refundId": "refund_id",
    "submissionId": "submission_id",
}

CALL_RE = re.compile(
    r"client\.(get|post|patch|del)(?:<[^>]*>)?\s*\(\s*([`'\"])(.*?)\2",
    re.DOTALL,
)
BLOB_RE = re.compile(r"client\.fetchBlob\(\s*([`'\"])(.*?)\1", re.DOTALL)
METHODS = {"GET": "get", "POST": "post", "PATCH": "patch", "DEL": "delete"}


def normalize_path(raw: str) -> str:
    path = raw.strip()

    def replace_param(match: re.Match[str]) -> str:
        name = match.group(1)
        mapped = PARAM_RENAMES.get(name, name)
        return "" if mapped == "" else f"{{{mapped}}}"

    path = re.sub(r"\$\{encodeURIComponent\((\w+)\)\}", replace_param, path)
    return path.split("?", 1)[0].rstrip("/")


def collect_calls() -> list[tuple[str, str]]:
    source = ENDPOINTS.read_text(encoding="utf-8")
    calls = [
        (METHODS[method.upper()], normalize_path(path))
        for method, _, path in CALL_RE.findall(source)
    ]
    calls.extend(("get", normalize_path(path)) for _, path in BLOB_RE.findall(source))
    calls.sort()
    return calls


def main() -> int:
    if not ENDPOINTS.is_file():
        print(f"missing endpoints mirror: {ENDPOINTS}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ROOT))
    from app.main import create_app

    app = create_app()
    spec = app.openapi()

    calls = collect_calls()
    missing: list[tuple[str, str]] = []
    paths = spec.get("paths", {})
    for method, path in calls:
        if path not in paths:
            missing.append((method, path))
            continue
        if method not in paths[path]:
            missing.append((method, path))

    if missing:
        print(f"FAIL — {len(missing)} console calls do not resolve:")
        for method, path in missing:
            print(f"  {method} {path}")
        return 1

    print(f"OK — {len(calls)} admin-web endpoint calls resolve in OpenAPI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
