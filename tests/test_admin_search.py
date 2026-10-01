"""Unified subject search, and the avatar upload presign.

Search exists for one operational moment: a passenger telephones support, the
operator has a phone number or a name, and needs the account *now*. So the
properties tested here are the ones that decide whether that moment works:

* a phone number typed in any of the ways Hong Kong numbers are written finds
  the row, because the operator reads it off whatever screen they have;
* a plate finds its driver, because that is what the caller reads off the car;
* a one-character query says "too short" rather than "no results", because
  those look identical to a human and only one of them means the search works;
* a caller who cannot reach the endpoint cannot enumerate the customer base.

The avatar tests cover the gap on the other side: `users.avatar_key` existed and
was settable, but nothing minted a key, so the "keys are server-generated" rule
was being enforced by rejection rather than by construction.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.search_service import digits_only, normalize_query


def _mk_user(
    client,
    *,
    phone: str,
    display_name: str = "Test Passenger",
    username: str | None = None,
    role: str = "PASSENGER",
    account_status: str = "ACTIVE",
) -> str:
    uid = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, username, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, :u, :r, true, :st, now(), now())",
        {
            "i": str(uid),
            "p": phone,
            "n": display_name,
            "u": username,
            "r": role,
            "st": account_status,
        },
    )
    return str(uid)


def _mk_driver(
    client,
    *,
    phone: str,
    display_name: str = "Test Driver",
    vehicle_reg_mark: str = "AA1234",
    taxi_driver_plate_no: str = "BB5678",
) -> tuple[str, str]:
    uid = _mk_user(client, phone=phone, display_name=display_name, role="DRIVER")
    profile_id = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
        "created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), 'ACTIVE', 'URBAN', :plate, :reg, "
        "'1234', false, now(), now())",
        {
            "i": str(profile_id),
            "u": uid,
            "plate": taxi_driver_plate_no,
            "reg": vehicle_reg_mark,
        },
    )
    return uid, str(profile_id)


class TestNormalisation:
    """Pure functions, tested directly: the phone formats are the whole point of
    the normaliser, and asserting them through HTTP would hide which branch
    matched."""

    @pytest.mark.parametrize(
        ("written", "digits"),
        [
            ("+852 9123 4567", "85291234567"),
            ("85291234567", "85291234567"),
            ("9123 4567", "91234567"),
            ("(+852)91234567", "85291234567"),
            ("+852-9123-4567", "85291234567"),
            ("91234567", "91234567"),
        ],
    )
    def test_digits_are_extracted_from_every_way_hk_numbers_are_written(self, written, digits):
        """The exact digit string per form, not a set-collapsed `or`. Whatever
        the operator types, the 8 significant digits must survive intact — that
        is the property the search depends on."""
        assert digits_only(written) == digits
        assert "91234567" in digits_only(written)

    def test_a_name_with_no_digits_yields_no_digits(self):
        assert digits_only("Chan Tai Man") == ""

    def test_whitespace_is_collapsed(self):
        """A name pasted from a chat arrives with a trailing newline or a double
        space; `"Chan  Tai"` not matching `"Chan Tai"` reads as "search broken"."""
        assert normalize_query("  Chan   Tai  \n") == "Chan Tai"

    def test_none_and_empty_are_safe(self):
        assert normalize_query(None) == ""
        assert digits_only(None) == ""


class TestPhoneSearch:
    @pytest.mark.parametrize(
        "typed",
        [
            "+852 9123 4567",
            "85291234567",
            "91234567",
            "9123 4567",
            "+852-9123-4567",
        ],
    )
    def test_every_common_way_of_typing_the_number_finds_the_account(self, client, ops, typed):
        uid = _mk_user(client, phone="+85291234567", display_name="Chow Mei Ling")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": typed}).json()
        assert [i["id"] for i in body["items"]] == [uid]

    def test_a_middle_run_of_digits_is_a_match_not_a_prefix_match(self, client, ops):
        """`123` matches `+85291234567` — deliberately.

        A caller reads a number off a screen or a SIM tray and reads *part* of
        it; the digits in the middle are the ones they remember. The phone rule
        is therefore a digit-substring, while names and plates are prefixes.
        Asserting the opposite would be asserting the search is less useful than
        it is.
        """
        uid = _mk_user(client, phone="+85291234567")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "123"}).json()
        assert uid in [i["id"] for i in body["items"]]

    def test_a_run_of_digits_nobody_has_returns_nothing(self, client, ops):
        """The counterpart: the substring rule is still a filter, not a
        pass-through. A number that appears nowhere matches nobody."""
        _mk_user(client, phone="+85291234567")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "8888"}).json()
        assert body["items"] == []


class TestNameAndUsernameSearch:
    def test_display_name_prefix(self, client, ops):
        uid = _mk_user(client, phone="+85290000001", display_name="Wong Ka Ming")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "Wong"}).json()
        assert uid in [i["id"] for i in body["items"]]

    def test_name_search_is_case_insensitive(self, client, ops):
        uid = _mk_user(client, phone="+85290000002", display_name="Wong Ka Ming")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "wong"}).json()
        assert uid in [i["id"] for i in body["items"]]

    def test_given_and_family_name_are_searchable_separately(self, client, ops):
        client.exec_sql(
            "INSERT INTO users (id, phone_e164, display_name, given_name, family_name, "
            "role, is_active, account_status, created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), '+85290000003', 'Mei Ling Chow', 'Mei Ling', "
            "'Chow', 'PASSENGER', true, 'ACTIVE', now(), now())",
            {"i": str(uuid.uuid4())},
        )
        for needle in ("Mei", "Chow"):
            body = client.get("/api/v1/admin/search", headers=ops, params={"q": needle}).json()
            assert any(i["display_name"] == "Mei Ling Chow" for i in body["items"]), needle

    def test_a_username_is_searchable(self, client, ops):
        uid = _mk_user(client, phone="+85290000004", username="kming88")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "kming"}).json()
        assert uid in [i["id"] for i in body["items"]]


class TestPlateSearch:
    def test_a_vehicle_registration_finds_its_driver(self, client, ops):
        uid, profile_id = _mk_driver(client, phone="+85291000001", vehicle_reg_mark="JK7788")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "JK7788"}).json()
        rows = [i for i in body["items"] if i["id"] == uid]
        assert len(rows) == 1
        assert rows[0]["kind"] == "DRIVER"
        assert rows[0]["driver_profile_id"] == profile_id
        assert rows[0]["plate"] == "JK7788"

    def test_a_plate_written_with_a_space_still_matches(self, client, ops):
        """Plates are stored without separators but read off a windscreen with
        one."""
        uid, _ = _mk_driver(client, phone="+85291000002", vehicle_reg_mark="JK7788")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "JK 7788"}).json()
        assert uid in [i["id"] for i in body["items"]]

    def test_the_driver_plate_no_is_searchable_too(self, client, ops):
        """Two plate fields: the vehicle's and the driver's own. An operator
        reading one back to a caller needs the search to try both."""
        uid, _ = _mk_driver(
            client, phone="+85291000003", vehicle_reg_mark="AA0001", taxi_driver_plate_no="ZZ9090"
        )
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "ZZ9090"}).json()
        assert uid in [i["id"] for i in body["items"]]

    def test_a_driver_found_by_name_appears_once_not_twice(self, client, ops):
        """The name query and the plate query can both return the same driver.
        Two rows for one person makes the operator think there are two
        accounts."""
        uid, _ = _mk_driver(
            client, phone="+85291000004", display_name="Kwok Ming", vehicle_reg_mark="KM1122"
        )
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "Kwok"}).json()
        assert [i["id"] for i in body["items"]].count(uid) == 1


class TestResultShape:
    def test_a_passenger_row_has_no_driver_fields(self, client, ops):
        uid = _mk_user(client, phone="+85290000010", display_name="Lee Siu Fung")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "Lee"}).json()
        row = next(i for i in body["items"] if i["id"] == uid)
        assert row["kind"] == "PASSENGER"
        assert row["driver_profile_id"] is None
        assert row["plate"] is None
        assert row["driver_status"] is None

    def test_a_driver_without_a_profile_is_still_labelled_driver(self, client, ops):
        """Registration creates the user before the profile. A support call can
        land inside that window, and calling them a passenger would be a lie the
        operator repeats to the caller."""
        uid = _mk_user(client, phone="+85290000011", display_name="Pang Ho", role="DRIVER")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "Pang"}).json()
        row = next(i for i in body["items"] if i["id"] == uid)
        assert row["kind"] == "DRIVER"
        assert row["driver_profile_id"] is None

    def test_the_result_carries_no_id_number_or_document_keys(self, client, ops):
        """A search result is a pointer. Inlining ID numbers makes every
        keystroke a bulk PII read with no audit line."""
        _mk_driver(client, phone="+85291000005", display_name="Yip Chun", vehicle_reg_mark="YC4455")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "Yip"}).json()
        row = body["items"][0]
        assert "hk_id_last4" not in row
        assert "documents" not in row
        assert "email" not in row


class TestQueryGuards:
    def test_a_one_character_query_says_too_short_rather_than_no_results(self, client, ops):
        """Those look identical to a human and only one means the search works."""
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "a"}).json()
        assert body["items"] == []
        assert body["query_too_short"] is True
        assert body["min_query_length"] == 2

    def test_a_two_character_query_is_accepted(self, client, ops):
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "ab"}).json()
        assert body["query_too_short"] is False

    def test_an_empty_query_is_too_short_not_a_full_listing(self, client, ops):
        """The dangerous alternative: an empty query returning everyone, which
        turns a search box into a customer export."""
        _mk_user(client, phone="+85290000020", display_name="Someone")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": ""}).json()
        assert body["items"] == []
        assert body["query_too_short"] is True

    def test_a_wildcard_does_not_match_everything(self, client, ops):
        """`%` is escaped, not interpreted. Otherwise a single `%` is a
        full-table dump through the search endpoint."""
        _mk_user(client, phone="+85290000021", display_name="Someone Else")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "%%"}).json()
        assert body["items"] == []

    def test_an_underscore_is_literal(self, client, ops):
        """`_` is a single-character wildcard in `LIKE`. Escaped, a name with an
        underscore is searchable; unescaped, it matches any character."""
        _mk_user(client, phone="+85290000022", display_name="a_b")
        _mk_user(client, phone="+85290000023", display_name="axb")
        body = client.get("/api/v1/admin/search", headers=ops, params={"q": "a_b"}).json()
        names = [i["display_name"] for i in body["items"]]
        assert "a_b" in names
        assert "axb" not in names

    def test_truncation_is_reported(self, client, ops):
        """`len(items) == limit` does not tell the caller whether there is more,
        and "showing 20 of 20" when there are 400 is the difference between
        narrowing the search and believing you have seen everyone."""
        for i in range(4):
            _mk_user(client, phone=f"+8529100001{i}", display_name=f"Truncate Test {i}")
        body = client.get(
            "/api/v1/admin/search", headers=ops, params={"q": "Truncate Test", "limit": 2}
        ).json()
        assert len(body["items"]) == 2
        assert body["truncated"] is True

    def test_a_query_that_returns_less_than_the_limit_is_not_truncated(self, client, ops):
        _mk_user(client, phone="+85290000030", display_name="Singleton Case")
        body = client.get(
            "/api/v1/admin/search", headers=ops, params={"q": "Singleton Case", "limit": 20}
        ).json()
        assert len(body["items"]) == 1
        assert body["truncated"] is False

    def test_the_limit_is_capped_server_side(self, client, ops):
        r = client.get("/api/v1/admin/search", headers=ops, params={"q": "ab", "limit": 9999})
        assert r.status_code == 422


class TestSearchAccessControl:
    async def test_every_admin_role_can_search(self, client, support, ops, finance):
        """The person answering the phone must be able to look up the caller.
        Gating search would make the widest-used endpoint the least available."""
        _mk_user(client, phone="+85290000040", display_name="Anyone At All")
        for headers in (support, ops, finance):
            r = client.get("/api/v1/admin/search", headers=headers, params={"q": "Anyone"})
            assert r.status_code == 200, r.text

    async def test_an_unauthenticated_caller_is_a_401(self, client):
        assert client.get("/api/v1/admin/search", params={"q": "ab"}).status_code == 401

    async def test_a_passenger_token_cannot_reach_it(self, client):
        """Enumerating the customer base must not be reachable with a passenger
        token, which is the one every account holder has."""
        uid = _mk_user(client, phone="+85290000041", display_name="Not An Admin")
        token = client.sign_in("+85290000041")
        r = client.get(
            "/api/v1/admin/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "Not An Admin"},
        )
        assert r.status_code == 403
        assert uid  # the account exists and was still not findable this way


class TestAvatarPresign:
    async def test_a_user_can_get_a_presigned_avatar_upload(self, client):
        phone = "+85290000050"
        _mk_user(client, phone=phone, display_name="Avatar Owner")
        token = client.sign_in(phone)
        r = client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "image/jpeg", "size_bytes": 1024},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["object_key"]
        assert body["upload_url"]
        assert body["expires_in"] > 0

    async def test_the_key_is_server_generated_and_namespaced(self, client):
        """The client never chooses the key. A caller-supplied key is how one
        account overwrites — or reads — another's object."""
        phone = "+85290000051"
        _mk_user(client, phone=phone, display_name="Key Owner")
        token = client.sign_in(phone)
        body = client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "image/png", "size_bytes": 2048},
        ).json()
        assert body["object_key"].startswith("avatars/")

    async def test_two_requests_produce_different_keys(self, client):
        """A fixed key would mean a second upload overwrites the first, and any
        cached image would show the wrong picture."""
        phone = "+85290000052"
        _mk_user(client, phone=phone, display_name="Two Uploads")
        token = client.sign_in(phone)
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post(
            "/api/v1/identity/avatar/uploads",
            headers=headers,
            json={"content_type": "image/png", "size_bytes": 100},
        ).json()
        second = client.post(
            "/api/v1/identity/avatar/uploads",
            headers=headers,
            json={"content_type": "image/png", "size_bytes": 100},
        ).json()
        assert first["object_key"] != second["object_key"]

    async def test_an_unsupported_content_type_is_refused(self, client):
        """Signing an arbitrary content type is how an HTML page ends up stored
        on a domain we control, which is then a phishing page on our hostname."""
        phone = "+85290000053"
        _mk_user(client, phone=phone, display_name="Bad Type")
        token = client.sign_in(phone)
        r = client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "text/html", "size_bytes": 100},
        )
        assert r.status_code == 400

    async def test_an_oversized_declaration_is_refused_at_signing_time(self, client):
        """Checked when signing, not after. Checking afterwards means the bytes
        are already in the bucket and will sit there."""
        phone = "+85290000054"
        _mk_user(client, phone=phone, display_name="Too Big")
        token = client.sign_in(phone)
        r = client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "image/jpeg", "size_bytes": 50 * 1024 * 1024},
        )
        assert r.status_code == 400

    async def test_a_zero_byte_declaration_is_a_422(self, client):
        phone = "+85290000055"
        _mk_user(client, phone=phone, display_name="Zero")
        token = client.sign_in(phone)
        r = client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "image/jpeg", "size_bytes": 0},
        )
        assert r.status_code == 422

    async def test_an_anonymous_caller_cannot_presign(self, client):
        r = client.post(
            "/api/v1/identity/avatar/uploads",
            json={"content_type": "image/jpeg", "size_bytes": 100},
        )
        assert r.status_code == 401

    async def test_presigning_does_not_write_an_avatar_key(self, client):
        """The key is claimed on `POST /identity/profile`, once the bytes exist.
        A row pointing at an object that may never arrive is worse than no row."""
        phone = "+85290000056"
        uid = _mk_user(client, phone=phone, display_name="Not Claimed")
        token = client.sign_in(phone)
        client.post(
            "/api/v1/identity/avatar/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"content_type": "image/jpeg", "size_bytes": 100},
        )
        rows = await _avatar_key(client, uid)
        assert rows[0]["avatar_key"] is None

    async def test_the_presigned_key_can_then_be_claimed_on_the_profile(self, client):
        """The two halves joined up: presign, then declare. This is the workflow
        that had no first step."""
        phone = "+85290000057"
        uid = _mk_user(client, phone=phone, display_name="Claimer")
        token = client.sign_in(phone)
        headers = {"Authorization": f"Bearer {token}"}
        key = client.post(
            "/api/v1/identity/avatar/uploads",
            headers=headers,
            json={"content_type": "image/jpeg", "size_bytes": 100},
        ).json()["object_key"]

        r = client.post(
            "/api/v1/identity/profile",
            headers=headers,
            json={
                "username": f"claimer{uid[:6]}",
                "given_name": "Claim",
                "family_name": "Er",
                "avatar_key": key,
            },
        )
        assert r.status_code == 200, r.text
        rows = await _avatar_key(client, uid)
        assert rows[0]["avatar_key"] == key

    async def test_a_url_instead_of_a_key_is_still_refused(self, client):
        """The existing guard on the claim path must keep working now that a
        legitimate producer of keys exists."""
        phone = "+85290000058"
        uid = _mk_user(client, phone=phone, display_name="Url Claimer")
        token = client.sign_in(phone)
        r = client.post(
            "/api/v1/identity/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "username": f"urlclaim{uid[:6]}",
                "given_name": "Url",
                "family_name": "Claimer",
                "avatar_key": "https://evil.example.com/x.jpg",
            },
        )
        assert r.status_code == 400


async def _avatar_key(client, user_id: str) -> list[dict]:
    from sqlalchemy import text

    async with client.db_factory() as session:
        result = await session.execute(
            text("SELECT avatar_key FROM users WHERE id = CAST(:i AS uuid)"),
            {"i": user_id},
        )
        return [dict(row) for row in result.mappings()]
