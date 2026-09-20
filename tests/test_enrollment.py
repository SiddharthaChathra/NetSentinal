"""Enrolment codes: the mechanism that lets a downloaded binary join an
account without anyone copying a long-lived credential between machines.

A code is a bearer credential for the seconds it lives, so most of what is
worth testing here is what it REFUSES to do.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import enrollment
from src.api import app
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_limits():
    enrollment.reset_rate_limit()
    yield
    enrollment.reset_rate_limit()


class FakeCodeStore:
    """A stand-in for the enrollment_codes table that honours the parts the
    logic depends on: single-use via the `is_(used_at, null)` guard, and
    lookup by exact code."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def table(self, name):
        assert name == "enrollment_codes"
        return _Query(self)


class _Query:
    def __init__(self, store):
        self.store = store
        self._code = None
        self._update = None
        self._require_unused = False

    def select(self, *a):
        return self

    def insert(self, payload):
        self.store.rows[payload["code"]] = dict(payload)
        return self

    def update(self, payload):
        self._update = payload
        return self

    def eq(self, col, value):
        if col == "code":
            self._code = value
        return self

    def is_(self, col, value):
        if col == "used_at" and value == "null":
            self._require_unused = True
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self._update is not None:
            row = self.store.rows.get(self._code)
            if row and not (self._require_unused and row.get("used_at")):
                row.update(self._update)
            return type("R", (), {"data": [row] if row else []})()
        row = self.store.rows.get(self._code)
        return type("R", (), {"data": [row] if row else []})()


def _live_row(user_id=TEST_USER_ID, code="ABCD2345", minutes_left=10, used=False):
    now = datetime.now(timezone.utc)
    return {
        code: {
            "code": code,
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=minutes_left)).isoformat(),
            "used_at": now.isoformat() if used else None,
        }
    }


class TestCodeFormat:
    def test_codes_avoid_confusable_characters(self):
        """Read off one screen, typed on another machine, often from a phone
        photo. I, L, O and U are where transcription goes wrong."""
        for _ in range(200):
            code = enrollment.generate_code()
            assert len(code) == enrollment.CODE_LENGTH
            assert not set(code) & set("ILOU")

    def test_display_is_grouped(self):
        assert enrollment.format_code("ABCD2345") == "ABCD-2345"

    @pytest.mark.parametrize("typed,expected", [
        ("ABCD2345", "ABCD2345"),
        ("abcd2345", "ABCD2345"),
        ("ABCD-2345", "ABCD2345"),
        ("abcd 2345", "ABCD2345"),
        (" ABCD-2345 ", "ABCD2345"),
        ("ABCD_2345", "ABCD2345"),
    ])
    def test_normalisation_accepts_what_people_actually_type(self, typed, expected):
        assert enrollment.normalise_code(typed) == expected

    def test_confusable_letters_are_mapped_not_rejected(self):
        # A typed O can only have meant 0, because O is not in the alphabet.
        assert enrollment.normalise_code("0BCD2345") == "0BCD2345"
        assert enrollment.normalise_code("OBCD2345") == "0BCD2345"
        assert enrollment.normalise_code("1BCD2345") == "1BCD2345"
        assert enrollment.normalise_code("IBCD2345") == "1BCD2345"
        assert enrollment.normalise_code("LBCD2345") == "1BCD2345"

    @pytest.mark.parametrize("junk", ["", None, "short", "WAYTOOLONGCODE", "ABCD234!", "        "])
    def test_rubbish_is_rejected_before_a_database_round_trip(self, junk):
        assert enrollment.normalise_code(junk) is None


class TestRedemption:
    def _redeem(self, store, code, client_key="1.2.3.4", hostname="NEW-PC"):
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch("src.auth.get_or_create_agent_token", return_value="nsa_issued_token"):
            return enrollment.redeem_code(code, client_key, hostname)

    def test_a_valid_code_yields_the_owning_accounts_token(self):
        store = FakeCodeStore(_live_row())
        assert self._redeem(store, "ABCD2345") == "nsa_issued_token"

    def test_a_code_is_single_use(self):
        store = FakeCodeStore(_live_row())
        self._redeem(store, "ABCD2345")
        with pytest.raises(enrollment.EnrollmentError):
            self._redeem(store, "ABCD2345")

    def test_an_expired_code_is_refused(self):
        store = FakeCodeStore(_live_row(minutes_left=-1))
        with pytest.raises(enrollment.EnrollmentError):
            self._redeem(store, "ABCD2345")

    def test_an_unknown_code_is_refused(self):
        with pytest.raises(enrollment.EnrollmentError):
            self._redeem(FakeCodeStore(), "ZZZZ9999")

    def test_failures_are_indistinguishable(self):
        """Unknown, expired and already-used must read identically, or the
        endpoint becomes an oracle for which codes exist."""
        messages = set()
        for store, code in [
            (FakeCodeStore(), "ZZZZ9999"),
            (FakeCodeStore(_live_row(minutes_left=-1)), "ABCD2345"),
            (FakeCodeStore(_live_row(used=True)), "ABCD2345"),
        ]:
            with pytest.raises(enrollment.EnrollmentError) as e:
                self._redeem(store, code)
            messages.add(str(e.value))
        assert len(messages) == 1, messages

    def test_the_code_is_spent_before_the_token_is_issued(self):
        """If marking it used fails, no token may be handed out — otherwise a
        credential escapes against a code that stays redeemable."""
        store = FakeCodeStore(_live_row())
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch.object(_Query, "execute", side_effect=[
                 type("R", (), {"data": [store.rows["ABCD2345"]]})(),   # the lookup
                 RuntimeError("write failed"),                          # marking it used
             ]), \
             patch("src.auth.get_or_create_agent_token") as issue:
            with pytest.raises(enrollment.EnrollmentError):
                enrollment.redeem_code("ABCD2345", "1.2.3.4")
        issue.assert_not_called()

    def test_brute_force_is_rate_limited(self):
        store = FakeCodeStore()
        for _ in range(enrollment._MAX_ATTEMPTS):
            with pytest.raises(enrollment.EnrollmentError):
                self._redeem(store, "ZZZZ9999", client_key="attacker")
        with pytest.raises(enrollment.EnrollmentError) as e:
            self._redeem(store, "ZZZZ9999", client_key="attacker")
        assert "Too many attempts" in str(e.value)

    def test_the_rate_limit_is_per_client(self):
        store = FakeCodeStore(_live_row())
        for _ in range(enrollment._MAX_ATTEMPTS):
            with pytest.raises(enrollment.EnrollmentError):
                self._redeem(store, "ZZZZ9999", client_key="attacker")
        # Someone else enrolling at the same time is unaffected.
        assert self._redeem(store, "ABCD2345", client_key="innocent") == "nsa_issued_token"


class TestEndpoints:
    def test_creating_a_code_requires_a_session(self, anon_client):
        assert anon_client.post("/api/devices/enroll-code").status_code == 401

    def test_a_code_is_scoped_to_the_caller(self, client):
        store = FakeCodeStore()
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store):
            body = client.post("/api/devices/enroll-code").json()
        assert body["formatted_code"] == enrollment.format_code(body["code"])
        assert body["expires_in_seconds"] == enrollment.CODE_TTL_SECONDS
        # The stored row belongs to the signed-in account and nobody else.
        stored = store.rows[body["code"]]
        assert stored["user_id"] == TEST_USER_ID
        # Download links travel with it so the UI needs one round-trip.
        assert body["downloads"]["windows"]["filename"].endswith(".exe")
        assert body["downloads"]["linux"]["url"].startswith("https://github.com/")

    def test_enrolling_needs_no_session(self, anon_client):
        """The machine being set up has no credential yet — the code IS the
        credential. It must reach the endpoint without the login gate."""
        store = FakeCodeStore(_live_row())
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch("src.auth.get_or_create_agent_token", return_value="nsa_issued_token"):
            res = anon_client.post("/api/agent/enroll", json={"code": "ABCD-2345", "hostname": "NEW-PC"})
        assert res.status_code == 200
        assert res.json()["token"] == "nsa_issued_token"
        assert res.json()["api_base_url"].startswith("http")

    def test_a_bad_code_is_400_with_a_usable_message(self, anon_client):
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=FakeCodeStore()):
            res = anon_client.post("/api/agent/enroll", json={"code": "ZZZZ9999"})
        assert res.status_code == 400
        assert "expire" in res.json()["detail"].lower()

    def test_a_missing_code_is_400_not_500(self, anon_client):
        assert anon_client.post("/api/agent/enroll", json={}).status_code == 400
        assert anon_client.post("/api/agent/enroll", json={"code": 12345}).status_code == 400

    def test_release_info_is_public_and_names_both_platforms(self, anon_client):
        body = anon_client.get("/api/agent/releases").json()
        assert set(body["downloads"]) == {"windows", "linux"}
        assert body["downloads"]["windows"]["url"].endswith("netsentinel-agent-windows.exe")
        assert body["downloads"]["linux"]["url"].endswith("netsentinel-agent-linux")
        assert body["version"]

    def test_enrolment_records_which_machine_claimed_the_code(self, anon_client):
        store = FakeCodeStore(_live_row())
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch("src.auth.get_or_create_agent_token", return_value="nsa_issued_token"):
            anon_client.post("/api/agent/enroll", json={"code": "ABCD2345", "hostname": "LAPTOP-2"})
        assert store.rows["ABCD2345"]["used_by_hostname"] == "LAPTOP-2"
        assert store.rows["ABCD2345"]["used_at"]


class TestMultiDeviceEnrolment:
    def test_two_machines_enrol_into_the_same_account_with_separate_codes(self, client, anon_client):
        """The whole point: one account, several machines, no credential ever
        copied between them."""
        store = FakeCodeStore()
        issued = []

        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch("src.auth.get_or_create_agent_token", return_value="nsa_account_token"):
            for _ in range(2):
                issued.append(client.post("/api/devices/enroll-code").json()["code"])

            tokens = []
            for code, host in zip(issued, ["LAPTOP-1", "LAPTOP-2"]):
                res = anon_client.post("/api/agent/enroll", json={"code": code, "hostname": host})
                assert res.status_code == 200, res.text
                tokens.append(res.json()["token"])

        assert issued[0] != issued[1]                     # a fresh code each time
        assert tokens[0] == tokens[1] == "nsa_account_token"  # same account
        assert {r["user_id"] for r in store.rows.values()} == {TEST_USER_ID}
        assert [r["used_by_hostname"] for r in store.rows.values()] == ["LAPTOP-1", "LAPTOP-2"]

    def test_one_accounts_code_cannot_enrol_into_another(self, other_client, anon_client):
        """A code issued to account B must yield B's token, never the
        caller's guess of whose it is."""
        store = FakeCodeStore()
        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store):
            code = other_client.post("/api/devices/enroll-code").json()["code"]
        assert store.rows[code]["user_id"] == OTHER_USER_ID

        seen = {}
        def _issue(user_id):
            seen["user_id"] = user_id
            return "nsa_other_account_token"

        with patch("src.enrollment.is_database_configured", return_value=True), \
             patch("src.enrollment.get_supabase", return_value=store), \
             patch("src.auth.get_or_create_agent_token", side_effect=_issue):
            res = anon_client.post("/api/agent/enroll", json={"code": code})
        assert res.status_code == 200
        assert seen["user_id"] == OTHER_USER_ID
