"""The login gate: nothing is reachable without a valid session, the
"am I logged in" check answers both ways without erroring, logout really ends
the session, and the onboarding flag is per-account.

These correspond one-to-one with the six end-to-end scenarios in the
access-control change.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import session as session_mod
from src import user_profile
from src.api import app
from src.auth_middleware import PUBLIC_PATHS, is_public_path
from tests.conftest import OTHER_TOKEN, OTHER_USER_ID, TEST_EMAIL, TEST_TOKEN, TEST_USER_ID


@pytest.fixture
def client():
    return TestClient(app)


# Every path the frontend can reach, other than the agent endpoints (which
# carry their own nsa_ token) and the three public ones.
PROTECTED_PATHS = [
    ("GET", "/api/diagnostic-run"),
    ("POST", "/api/diagnostic-run"),
    ("GET", "/api/diagnostics"),
    ("GET", "/api/system"),
    ("GET", "/api/interfaces"),
    ("GET", "/api/gateway"),
    ("GET", "/api/internet"),
    ("GET", "/api/dns"),
    ("GET", "/api/tcp"),
    ("GET", "/api/routes"),
    ("GET", "/api/export"),
    ("GET", "/api/history"),
    ("GET", "/api/local-device"),
    ("GET", "/api/devices"),
    ("GET", "/api/devices/dev-1"),
    ("DELETE", "/api/devices/dev-1"),
    ("POST", "/api/devices/dev-1/backup-target"),
    ("GET", "/api/backup/protocols"),
    ("GET", "/api/backup/readiness"),
    ("GET", "/api/telemetry/latest"),
    ("GET", "/api/incidents"),
    ("POST", "/api/incidents/inc-1/acknowledge"),
    ("POST", "/api/incidents/inc-1/resolve"),
    ("GET", "/api/setup"),
    ("GET", "/api/agent-token"),
    ("POST", "/api/agent-token/rotate"),
    ("GET", "/api/report"),
    ("GET", "/api/report.pdf"),
    ("GET", "/api/auth/onboarding"),
    ("POST", "/api/auth/onboarding/complete"),
]


class TestScenario1NewVisitorReachesNothing:
    """A brand-new visitor with no session cannot reach any protected route."""

    @pytest.mark.parametrize("method,path", PROTECTED_PATHS)
    def test_every_protected_endpoint_is_401(self, anon_client, method, path):
        res = anon_client.request(method, path, json={})
        assert res.status_code == 401, f"{method} {path} returned {res.status_code}"
        body = res.json()
        assert body["code"] == "not_authenticated"
        assert body["authenticated"] is False
        assert body["login_url"] == "/auth"

    def test_401_is_explicit_not_a_500_or_empty_body(self, anon_client):
        res = anon_client.get("/api/devices")
        assert res.status_code == 401
        assert res.headers["www-authenticate"].startswith("Bearer")
        assert "Sign in" in res.json()["detail"]

    def test_an_invalid_token_is_401_not_500(self, anon_client):
        res = anon_client.get("/api/devices", headers={"Authorization": "Bearer totally-made-up"})
        assert res.status_code == 401
        assert res.json()["code"] == "session_expired"

    def test_a_malformed_header_is_401(self, anon_client):
        for header in ("", "Basic abc", "Bearer", "Bearer    "):
            res = anon_client.get("/api/devices", headers={"Authorization": header})
            assert res.status_code == 401, header

    def test_the_gate_does_not_leak_data_in_the_401(self, anon_client):
        body = anon_client.get("/api/report").json()
        assert set(body) == {"detail", "code", "authenticated", "login_url"}

    def test_health_stays_public(self, anon_client):
        """The cold-start probe must answer without a session, or a booting
        backend is indistinguishable from a rejected one."""
        res = anon_client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

    def test_agent_endpoints_are_not_gated_by_the_session_middleware(self, anon_client):
        """Agents authenticate with their own nsa_ token; they must reach
        their endpoint and be judged there, not bounced by the browser gate."""
        # Reaches the handler rather than the gate's 401.
        assert anon_client.post("/api/agent/heartbeat", json={"device_id": "d1"}).status_code == 200

        # And it is still credential-checked — by verify_agent_token, with the
        # agent's own message, not the browser gate's.
        with patch("src.auth.is_database_configured", return_value=True):
            res = anon_client.post("/api/agent/heartbeat", json={"device_id": "d1"})
        assert res.status_code == 401
        assert res.json()["detail"] == "Missing agent token"
        assert "code" not in res.json()   # not the session gate's body shape

    def test_public_list_is_exactly_what_we_expect(self):
        assert PUBLIC_PATHS == {"/api/health", "/api/auth/session", "/api/auth/logout"}
        assert is_public_path("/api/agent/telemetry")
        assert not is_public_path("/api/devices")
        assert not is_public_path("/api/backup/readiness")

    def test_docs_are_gated_while_the_login_gate_is_on(self, anon_client):
        assert not is_public_path("/openapi.json")
        assert anon_client.get("/openapi.json").status_code == 401


class TestScenario2SessionCheckOnAppLoad:
    """The "am I logged in" check the frontend calls before it paints."""

    def test_answers_200_with_false_for_a_visitor(self, anon_client):
        res = anon_client.get("/api/auth/session")
        assert res.status_code == 200          # not 401 — the page must be able to ask
        body = res.json()
        assert body["authenticated"] is False
        assert body["user"] is None
        assert body["org"] is None
        assert body["login_url"] == "/auth"

    def test_answers_200_with_the_user_when_signed_in(self, client):
        body = client.get("/api/auth/session").json()
        assert body["authenticated"] is True
        assert body["user"] == {"id": TEST_USER_ID, "email": TEST_EMAIL}
        assert body["org"] == {"id": TEST_USER_ID, "scope": "account"}

    def test_an_expired_token_reads_as_signed_out_not_an_error(self, anon_client):
        res = anon_client.get("/api/auth/session", headers={"Authorization": "Bearer expired-token"})
        assert res.status_code == 200
        assert res.json()["authenticated"] is False

    def test_it_does_not_touch_the_database_probe(self, client):
        """/api/health probes Postgres; the session check must not, or every
        visit pays for a database round-trip before the first paint."""
        with patch("src.api._database_status") as probe, patch("src.api._schema_status") as schema:
            client.get("/api/auth/session")
        probe.assert_not_called()
        schema.assert_not_called()

    def test_repeat_calls_validate_the_token_only_once(self, client):
        """Token validation is cached, so the several requests a page load
        fires cost at most one round-trip to the auth server."""
        session_mod.clear_session_cache()
        with patch.object(session_mod, "_fetch_user", wraps=session_mod._fetch_user) as fetch:
            client.get("/api/auth/session")
            client.get("/api/auth/session")
            client.get("/api/auth/session")
        assert fetch.call_count == 1


class TestScenario2bOnboardingTrigger:
    """The tour fires on first successful login, and the flag is per-account."""

    def test_a_brand_new_account_is_told_to_show_the_tour(self, client):
        with patch("src.user_profile.is_database_configured", return_value=False):
            body = client.get("/api/auth/session").json()
        assert body["onboarding"]["should_show_tour"] is True
        assert body["onboarding"]["completed"] is False

    def test_completing_it_is_stored_against_the_account(self):
        rows = {}

        class _Table:
            def __init__(self, name): self.name = name
            def select(self, *a): return self
            def eq(self, col, val): self._uid = val; return self
            def limit(self, n): return self
            def execute(self):
                row = rows.get(getattr(self, "_uid", None))
                return type("R", (), {"data": [row] if row else []})()
            def upsert(self, payload): rows[payload["user_id"]] = {**rows.get(payload["user_id"], {}), **payload}; return self
            def insert(self, payload): rows[payload["user_id"]] = payload; return self
            def update(self, payload): return self

        client_stub = type("SB", (), {"table": staticmethod(lambda n: _Table(n))})()

        with patch("src.user_profile.is_database_configured", return_value=True), \
             patch("src.user_profile.get_supabase", return_value=client_stub):
            assert user_profile.onboarding_state(TEST_USER_ID)["should_show_tour"] is True
            user_profile.complete_onboarding(TEST_USER_ID)
            after = user_profile.onboarding_state(TEST_USER_ID)
            # The same account on a different device reads the same flag.
            assert after["should_show_tour"] is False
            assert after["completed"] is True
            # A different account is unaffected.
            assert user_profile.onboarding_state(OTHER_USER_ID)["should_show_tour"] is True

    def test_a_missing_profile_table_does_not_break_the_session_check(self, client):
        """Until migration 007 is applied the table does not exist. The app
        must still sign people in; they just get the tour treated as unseen."""
        with patch("src.user_profile.is_database_configured", return_value=True), \
             patch("src.user_profile.get_supabase", side_effect=RuntimeError(
                 "Could not find the table 'public.user_profiles'")):
            res = client.get("/api/auth/session")
        assert res.status_code == 200
        assert res.json()["authenticated"] is True
        assert res.json()["onboarding"]["should_show_tour"] is True

    def test_complete_requires_a_session(self, anon_client):
        assert anon_client.post("/api/auth/onboarding/complete").status_code == 401


class TestScenario6Logout:
    """Logout clears state server-side and leaves protected routes shut."""

    def test_logout_revokes_the_session_and_evicts_the_cache(self, client):
        client.get("/api/devices")                       # warm the token cache
        assert session_mod._session_cache, "token should be cached before logout"

        res = client.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json()["signed_out"] is True
        assert res.json()["login_url"] == "/auth"
        assert TEST_TOKEN not in session_mod._session_cache

    def test_logout_calls_supabase_to_revoke_the_refresh_token(self):
        signed_out = {}

        class _Admin:
            def sign_out(self, jwt, *a): signed_out["jwt"] = jwt

        class _Auth:
            admin = _Admin()

        with patch("src.session.is_database_configured", return_value=True), \
             patch("src.session.get_supabase", return_value=type("SB", (), {"auth": _Auth()})()):
            result = session_mod.revoke_session("some-token")
        assert signed_out["jwt"] == "some-token"
        assert result["revoked"] is True

    def test_revocation_failure_is_reported_not_swallowed(self):
        class _Admin:
            def sign_out(self, jwt, *a): raise RuntimeError("supabase down")

        with patch("src.session.is_database_configured", return_value=True), \
             patch("src.session.get_supabase", return_value=type("SB", (), {"auth": type("A", (), {"admin": _Admin()})()})()):
            result = session_mod.revoke_session("t")
        assert result["revoked"] is False
        assert "supabase down" in result["detail"]
        assert result["cache_cleared"] is True   # local session is gone regardless

    def test_logout_without_a_session_still_succeeds(self, anon_client):
        """Signing out with an already-expired token must not error, or a user
        with a stale session can never cleanly get back to the login page."""
        res = anon_client.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json()["signed_out"] is True

    def test_the_account_run_cache_is_dropped_on_logout(self, client):
        client.post("/api/diagnostic-run?demo=healthy")
        assert client.get("/api/diagnostic-run").json() is not None
        client.post("/api/auth/logout")
        assert client.get("/api/diagnostic-run").json() is None

    def test_after_logout_a_revoked_token_is_rejected(self, client):
        client.get("/api/devices")
        client.post("/api/auth/logout")
        # Simulate Supabase having invalidated the session: the cache was
        # evicted by logout, so the next request re-validates and is refused.
        with patch.object(session_mod, "_fetch_user", side_effect=ValueError("revoked")):
            assert client.get("/api/devices").status_code == 401


class TestScenario7TenantScoping:
    """Gating must not blur account boundaries."""

    def test_the_session_resolves_to_its_own_scope(self, client, other_client):
        assert client.get("/api/auth/session").json()["org"]["id"] == TEST_USER_ID
        assert other_client.get("/api/auth/session").json()["org"]["id"] == OTHER_USER_ID

    def test_device_queries_are_filtered_by_the_session_user_only(self, client):
        captured = {}

        class _Q:
            def select(self, *a): return self
            def eq(self, col, val): captured[col] = val; return self
            def execute(self): return type("R", (), {"data": []})()

        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=type("SB", (), {"table": staticmethod(lambda n: _Q())})()):
            client.get("/api/devices")
        assert captured == {"user_id": TEST_USER_ID}

    def test_a_user_id_in_the_request_cannot_override_the_session(self, client):
        """Scope comes from the validated token and nowhere else."""
        captured = {}

        class _Q:
            def select(self, *a): return self
            def eq(self, col, val): captured[col] = val; return self
            def execute(self): return type("R", (), {"data": []})()

        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=type("SB", (), {"table": staticmethod(lambda n: _Q())})()):
            client.get(f"/api/devices?user_id={OTHER_USER_ID}",
                       headers={"X-User-Id": OTHER_USER_ID})
        assert captured["user_id"] == TEST_USER_ID

    def test_two_sessions_do_not_share_cached_runs(self, client, other_client):
        client.post("/api/diagnostic-run?demo=dns-failure")
        assert other_client.get("/api/diagnostic-run").json() is None


class TestGateConfiguration:
    def test_local_dev_without_a_database_is_open(self, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "0")
        assert session_mod.auth_required() is False
        principal = session_mod.resolve_access_token(None)
        assert principal is not None and principal.source == "local-dev"

    def test_auth_required_forces_the_gate_on(self, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "1")
        assert session_mod.auth_required() is True
        assert session_mod.resolve_access_token(None) is None

    def test_a_supabase_outage_fails_closed(self, monkeypatch):
        """If the auth server cannot be reached the request is refused. The
        alternative — failing open — would unlock the whole app during an
        outage."""
        monkeypatch.setenv("AUTH_REQUIRED", "1")
        session_mod.clear_session_cache()
        with patch.object(session_mod, "_fetch_user", side_effect=RuntimeError("connection refused")):
            assert session_mod.resolve_access_token("any-token") is None

    def test_failed_validations_are_cached_briefly(self, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "1")
        session_mod.clear_session_cache()
        with patch.object(session_mod, "_fetch_user", side_effect=ValueError("bad")) as fetch:
            session_mod.resolve_access_token("junk")
            session_mod.resolve_access_token("junk")
        assert fetch.call_count == 1   # no stampede against the auth server


def _telemetry(device_id: str) -> dict:
    return {
        "device_id": device_id, "timestamp": "2026-09-19T10:00:00+00:00",
        "latency_ms": 10.0, "packet_loss": 0.0, "gateway_reachable": True,
        "internet_reachable": True, "dns_healthy": True, "tcp_healthy": True,
        "interface_errors": 0, "interface_drops": 0,
    }


class TestAgentTokenTenancy:
    """An agent token identifies an account. It must not be able to write into
    another account's fleet — gating the browser is pointless if the ingest
    path leaks across tenants."""

    @staticmethod
    def _supabase_with_owner(owner_id):
        """A Supabase stub that returns `owner_id` for the device lookup and
        records everything written."""
        writes = {"inserts": [], "updates": []}

        class _Table:
            def __init__(self, name): self.name = name; self._payload = None
            def select(self, *a): self._op = "select"; return self
            def eq(self, *a): return self
            def limit(self, n): return self
            def insert(self, payload): writes["inserts"].append(payload); self._op = "insert"; return self
            def update(self, payload): writes["updates"].append(payload); self._op = "update"; return self
            def execute(self):
                data = [{"user_id": owner_id}] if getattr(self, "_op", None) == "select" else []
                return type("R", (), {"data": data})()

        return type("SB", (), {"table": staticmethod(lambda n: _Table(n))})(), writes

    def _as_agent(self, user_id):
        from src.auth import AgentIdentity, verify_agent_token
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity(user_id, "user")
        return verify_agent_token

    def test_telemetry_for_another_accounts_device_is_refused(self, client):
        dep = self._as_agent("agent-owner-A")
        sb, writes = self._supabase_with_owner("someone-else-B")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase", return_value=sb):
                res = client.post("/api/agent/telemetry", json=_telemetry("dev-owned-by-B"))
        finally:
            app.dependency_overrides.pop(dep, None)
        assert res.status_code == 404   # 404, not 403: do not confirm the device exists
        assert writes["inserts"] == []         # and nothing was written

    def test_heartbeat_for_another_accounts_device_is_refused(self, client):
        dep = self._as_agent("agent-owner-A")
        sb, writes = self._supabase_with_owner("someone-else-B")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase", return_value=sb):
                res = client.post("/api/agent/heartbeat", json={"device_id": "dev-owned-by-B"})
        finally:
            app.dependency_overrides.pop(dep, None)
        assert res.status_code == 404
        assert writes["updates"] == []

    def test_telemetry_for_its_own_device_is_accepted(self, client):
        dep = self._as_agent("agent-owner-A")
        sb, writes = self._supabase_with_owner("agent-owner-A")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase", return_value=sb), \
                 patch("src.alert_engine.evaluate_alerts") as alerts, \
                 patch("src.anomaly_engine.detect_anomalies", return_value=[]), \
                 patch("src.incident_engine.evaluate_and_create_incidents") as incidents:
                res = client.post("/api/agent/telemetry", json=_telemetry("dev-owned-by-A"))
        finally:
            app.dependency_overrides.pop(dep, None)
        assert res.status_code == 200
        assert len(writes["inserts"]) == 1
        # The owner is carried into the derived rows, or /api/incidents (which
        # filters by user_id) would never show them to the account concerned.
        assert alerts.call_args.kwargs["user_id"] == "agent-owner-A"
        assert incidents.call_args.kwargs["user_id"] == "agent-owner-A"

    def test_the_legacy_admin_token_is_still_trusted_for_any_device(self, client):
        from src.auth import AgentIdentity, verify_agent_token
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity(None, "admin")
        sb, writes = self._supabase_with_owner("some-user")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase", return_value=sb), \
                 patch("src.alert_engine.evaluate_alerts"), \
                 patch("src.anomaly_engine.detect_anomalies", return_value=[]), \
                 patch("src.incident_engine.evaluate_and_create_incidents"):
                res = client.post("/api/agent/heartbeat", json={"device_id": "any-device"})
        finally:
            app.dependency_overrides.pop(verify_agent_token, None)
        assert res.status_code == 200
        assert len(writes["updates"]) == 1
