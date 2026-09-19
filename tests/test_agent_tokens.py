"""Per-user agent tokens, the setup guide that hands them out, and the
service-key access mode that makes per-user data work under RLS."""
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api import app


@pytest.fixture
def client():
    return TestClient(app)


REGISTER_PAYLOAD = {
    "name": "n", "hostname": "h", "platform": "Linux", "architecture": "x86_64",
    "ip_address": "10.0.0.1", "agent_version": "1", "status": "ONLINE",
}


class TestSetupGuide:
    def test_guest_guide_withholds_token(self, client):
        body = client.get("/api/setup").json()
        assert body["user_id"] is None
        assert body["agent_token"] is None
        assert body["signed_in"] is False
        assert len(body["steps"]) == 4
        env_step = body["steps"][2]["commands"]
        assert any(line.startswith("API_BASE_URL=") for line in env_step)
        assert any(line.startswith("AGENT_TOKEN=<sign in") for line in env_step)
        assert not any("NETSENTINEL_USER_ID" in line for line in env_step)

    def test_signed_in_guide_includes_personal_token(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "user-xyz"}
        try:
            with patch("src.api.get_or_create_agent_token", return_value="nsa_test123") as gen:
                body = client.get("/api/setup").json()
            gen.assert_called_once_with("user-xyz")
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        assert body["agent_token"] == "nsa_test123"
        assert body["steps"][0]["done"] is True
        assert "AGENT_TOKEN=nsa_test123" in body["steps"][2]["commands"]


class TestAgentTokenEndpoints:
    @pytest.fixture
    def signed_in(self):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-A"}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_get_token_creates_on_first_use(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.auth.get_supabase") as sb:
            table = sb.return_value.table.return_value
            table.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
            body = client.get("/api/agent-token").json()
            inserted = table.insert.call_args[0][0]
        assert body["token"].startswith("nsa_")
        assert inserted == {"user_id": "user-A", "token": body["token"]}

    def test_get_token_returns_existing(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.auth.get_supabase") as sb:
            table = sb.return_value.table.return_value
            table.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"token": "nsa_existing"}]
            body = client.get("/api/agent-token").json()
            table.insert.assert_not_called()
        assert body["token"] == "nsa_existing"

    def test_rotate_issues_new_token(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.auth.get_supabase") as sb:
            body = client.post("/api/agent-token/rotate").json()
            upserted = sb.return_value.table.return_value.upsert.call_args[0][0]
        assert body["token"].startswith("nsa_")
        assert upserted["user_id"] == "user-A" and upserted["token"] == body["token"]

    def test_token_endpoints_require_auth(self, client):
        assert client.get("/api/agent-token").status_code == 401
        assert client.post("/api/agent-token/rotate").status_code == 401


class TestAgentTokenVerification:
    """verify_agent_token must map a personal token to its owner and reject junk."""

    @pytest.fixture(autouse=True)
    def env(self):
        from src import auth
        auth._token_cache.clear()
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.auth.is_database_configured", return_value=True), \
             patch.dict(os.environ, {"AGENT_TOKEN": "admin-secret"}):
            yield

    def _register(self, client, token, payload_user="payload-user"):
        return client.post(
            "/api/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={**REGISTER_PAYLOAD, "user_id": payload_user},
        )

    def test_personal_token_owner_overrides_payload_user_id(self, client):
        with patch("src.auth.get_supabase") as sb_auth, patch("src.api.get_supabase") as sb_api:
            sb_auth.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"user_id": "owner-1"}]
            res = self._register(client, "nsa_valid", payload_user="attacker")
            upserted = sb_api.return_value.table.return_value.upsert.call_args[0][0]
        assert res.status_code == 200
        assert upserted["user_id"] == "owner-1"

    def test_unknown_personal_token_is_401(self, client):
        with patch("src.auth.get_supabase") as sb_auth:
            sb_auth.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
            assert self._register(client, "nsa_bogus").status_code == 401

    def test_admin_token_still_works_and_keeps_payload_user(self, client):
        with patch("src.api.get_supabase") as sb_api:
            res = self._register(client, "admin-secret", payload_user="user-from-env")
            upserted = sb_api.return_value.table.return_value.upsert.call_args[0][0]
        assert res.status_code == 200
        assert upserted["user_id"] == "user-from-env"

    def test_wrong_token_is_401(self, client):
        assert self._register(client, "not-it").status_code == 401

    def test_missing_token_is_401(self, client):
        res = client.post("/api/agent/register", json=REGISTER_PAYLOAD)
        assert res.status_code == 401

    def test_token_lookup_db_failure_is_503_not_401(self, client):
        """A DB outage must not read as 'your token is wrong'."""
        with patch("src.auth.get_supabase", side_effect=RuntimeError("down")):
            assert self._register(client, "nsa_valid").status_code == 503


class TestDatabaseAccessMode:
    def test_health_reports_access_mode(self, client):
        with patch("src.api.database_access_mode", return_value="publishable"):
            assert client.get("/api/health").json()["database_access"] == "publishable"

    def test_service_key_preferred(self):
        from src import database
        base = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "pub"}
        with patch.dict(os.environ, {**base, "SUPABASE_SERVICE_ROLE_KEY": "svc"}):
            assert database.database_access_mode() == "service"
        with patch.dict(os.environ, base):
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            os.environ.pop("SUPABASE_SECRET_KEY", None)
            assert database.database_access_mode() == "publishable"


class TestTelemetryIntegrityErrors:
    """A foreign-key/integrity failure is the agent's bad data, not an outage."""

    @pytest.fixture(autouse=True)
    def as_agent(self):
        from src.auth import verify_agent_token, AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity("u1", "user")
        yield
        app.dependency_overrides.pop(verify_agent_token, None)

    def _post(self, client):
        return client.post("/api/agent/telemetry", json={
            "device_id": "gone-device", "timestamp": "2026-09-19T10:00:00+00:00", "latency_ms": 1,
            "packet_loss": 0, "gateway_reachable": True, "internet_reachable": True, "dns_healthy": True,
            "tcp_healthy": True, "interface_errors": 0, "interface_drops": 0,
        })

    def test_fk_violation_is_422_not_503(self, client):
        class APIError(Exception):
            code = "23503"; message = 'insert or update on table "telemetry" violates foreign key constraint "telemetry_device_id_fkey"'
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.insert.return_value.execute.side_effect = APIError()
            res = self._post(client)
        assert res.status_code == 422
        assert "foreign key" in res.json()["detail"]
        assert "--register" in res.json()["detail"]

    def test_transport_failure_is_still_503(self, client):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", side_effect=RuntimeError("connection reset")):
            assert self._post(client).status_code == 503


class TestIdempotentRegistration:
    """Re-registering the same machine must reuse its device, not add another."""

    @pytest.fixture(autouse=True)
    def as_owner(self):
        from src.auth import verify_agent_token, AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity("owner-1", "user")
        yield
        app.dependency_overrides.pop(verify_agent_token, None)

    def _register(self, client, sb, existing_rows):
        table = sb.return_value.table.return_value
        table.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = existing_rows
        res = client.post("/api/agent/register", json={**REGISTER_PAYLOAD, "hostname": "LAPTOP-ATJOIONA"})
        return res, table.upsert.call_args[0][0]

    def test_existing_hostname_reuses_device_id_and_tag(self, client):
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            res, upserted = self._register(client, sb, [{"id": "dev-original", "created_at": "2026-08-01T00:00:00+00:00", "is_backup_target": True}])
        assert res.status_code == 200
        assert res.json()["id"] == "dev-original"
        assert upserted["id"] == "dev-original"
        assert upserted["is_backup_target"] is True      # tag survives re-registration
        assert upserted["created_at"].startswith("2026-08-01")
        assert upserted["user_id"] == "owner-1"

    def test_new_hostname_gets_new_device(self, client):
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            res, upserted = self._register(client, sb, [])
        assert res.status_code == 200
        assert upserted["id"] == res.json()["id"]
        assert upserted["is_backup_target"] is False

    def test_lookup_is_scoped_to_owner_and_hostname(self, client):
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            self._register(client, sb, [])
            eq_calls = sb.return_value.table.return_value.select.return_value.eq
            first = eq_calls.call_args_list[0][0]
            second = eq_calls.return_value.eq.call_args_list[0][0]
        assert first == ("user_id", "owner-1")
        assert second == ("hostname", "LAPTOP-ATJOIONA")


class TestDeleteDevice:
    @pytest.fixture
    def signed_in(self):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "owner-1"}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_owner_can_delete(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            chain = sb.return_value.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute
            chain.return_value.data = [{"id": "dev-1"}]
            res = client.delete("/api/devices/dev-1")
            eqs = sb.return_value.table.return_value.delete.return_value.eq
        assert res.status_code == 200 and res.json() == {"id": "dev-1", "deleted": True}
        assert eqs.call_args_list[0][0] == ("id", "dev-1")
        assert eqs.return_value.eq.call_args_list[0][0] == ("user_id", "owner-1")

    def test_foreign_or_missing_device_is_404(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            assert client.delete("/api/devices/someone-elses").status_code == 404

    def test_requires_auth(self, client):
        assert client.delete("/api/devices/dev-1").status_code == 401

    def test_db_failure_is_503(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", side_effect=RuntimeError("down")):
            assert client.delete("/api/devices/dev-1").status_code == 503
