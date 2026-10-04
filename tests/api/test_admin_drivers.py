"""`/admin/drivers` — the KYC queue.

Two things this route can get wrong quietly, and both are about the *filter*:

1. **An unrecognised `status_filter`.** `DriverStatus(status_filter)` raises
   `ValueError`, and `register_exception_handlers` has no `ValueError` branch, so
   the request surfaced as a **500** — a server error for a caller typo, saying
   nothing about which values are accepted. `/admin/orders` and `/admin/refunds`
   both get this right (400 with an `allowed` list, and a `pattern` respectively),
   which is what makes the omission here a miss rather than a policy.
2. **`total` ignoring the filter.** A page returning only `SUSPENDED` drivers
   while reporting the size of the whole table makes the console's pager describe
   a set nobody asked for. `FleetService.list_page` and `/admin/refunds` both
   carry the predicate into the count.

The fixtures insert driver profiles directly: the question is what the admin
*list* returns, and walking the KYC flow would make every assertion here depend
on the review endpoints being correct.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def _make_driver(client, *, status: str = "ACTIVE") -> str:
    """A `driver_profiles` row in `status`, returning the profile id.

    Two UUID spaces are in play: `users.id` is the account and
    `driver_profiles.id` is the driver the list endpoint reports.
    """
    profile_id = uuid.uuid4()
    user_id = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, 'DRIVER', true, 'ACTIVE', now(), now())",
        {
            "i": str(user_id),
            "p": f"+8529{user_id.int % 10**7:07d}",
            "n": f"d{user_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
        "created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :s, "
        "'URBAN', :plate, :reg, '1234', false, now(), now())",
        {
            "i": str(profile_id),
            "u": str(user_id),
            "s": status,
            "plate": f"D{profile_id.int % 10**5:05d}",
            "reg": f"AA{profile_id.int % 10**4:04d}",
        },
    )
    return str(profile_id)


class TestListFilters:
    async def test_status_filter_narrows_the_page_and_the_total(self, client):
        """`total` is what the console paginates on, so it has to describe the
        filtered set. Counting the whole table while returning one status makes
        the pager offer pages that do not exist."""
        _make_driver(client, status="ACTIVE")
        _make_driver(client, status="SUSPENDED")
        _make_driver(client, status="SUSPENDED")

        r = client.get(
            "/api/v1/admin/drivers",
            headers=client.admin_headers(),
            params={"status_filter": "SUSPENDED"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert [i["status"] for i in body["items"]] == ["SUSPENDED", "SUSPENDED"]
        assert body["total"] == 2, "the count must carry the page's predicate"

    async def test_without_a_filter_the_total_is_the_whole_table(self, client):
        """The other half of the previous test: the fix must not narrow the count
        when nothing was asked for."""
        _make_driver(client, status="ACTIVE")
        _make_driver(client, status="TERMINATED")

        r = client.get("/api/v1/admin/drivers", headers=client.admin_headers())
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 2

    async def test_unknown_status_is_a_400_not_a_500(self, client):
        """A filter that 500s tells the operator nothing, and a filter that
        silently matches nothing is how they conclude no driver was ever
        suspended. The allowed list comes from the enum, so it cannot drift from
        the values the column actually holds."""
        r = client.get(
            "/api/v1/admin/drivers",
            headers=client.admin_headers(),
            params={"status_filter": "NOT_A_STATUS"},
        )
        assert r.status_code == 400, r.text
        details = r.json()["details"]
        assert details["reason"] == "UNKNOWN_STATUS"
        assert "SUSPENDED" in details["allowed"]
        assert "PENDING_KYC" in details["allowed"]
