"""Regression tests for the issues found in the production QA sweep."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api import app, _last_runs
from src.diagnostics import run_diagnostics
from src.health_score import calculate_health_score


@pytest.fixture
def client():
    _last_runs.clear()
    return TestClient(app)


CLOUD_HOST = {
    # Exactly what the Render container reports: link-local gateway drops ICMP,
    # `lo` listed first, internet fine.
    "interfaces": [
        {"name": "lo", "ipv4": "127.0.0.1", "state": "UP"},
        {"name": "eth0", "ipv4": "10.193.14.183", "state": "UP"},
    ],
    "gateway": {"address": "169.254.1.1", "reachable": False, "packet_loss": 100.0, "latency_ms": 0.0},
    "internet": {"target": "8.8.8.8", "reachable": True, "packet_loss": 0.0, "latency_ms": 6.7},
    "dns": [{"domain": "google.com", "success": True}],
    "tcp": [{"host": "google.com", "port": 443, "success": True}],
}


class TestIcmpSilentGateway:
    def test_is_not_a_critical_finding(self):
        diags = run_diagnostics(CLOUD_HOST)
        titles = {d["title"]: d for d in diags}
        assert "Gateway connectivity problem" not in titles
        assert titles["Gateway does not answer ICMP"]["severity"] == "info"
        assert "No major network issues detected" in titles

    def test_evidence_does_not_cite_loopback(self):
        data = dict(CLOUD_HOST, internet={"reachable": False}, gateway={"reachable": False, "address": "169.254.1.1"})
        diags = run_diagnostics(data)
        gw = next(d for d in diags if d["title"] == "Gateway connectivity problem")
        assert "127.0.0.1" not in " ".join(gw["evidence"])
        assert "10.193.14.183" in " ".join(gw["evidence"])

    def test_score_stays_healthy(self):
        health = calculate_health_score(CLOUD_HOST)
        assert health["score"] == 97
        assert health["status"].startswith("HEALTHY")

    def test_real_gateway_outage_is_still_critical(self):
        data = dict(CLOUD_HOST, internet={"reachable": False, "packet_loss": 100.0, "latency_ms": 0.0},
                    dns=[{"success": False}], tcp=[{"success": False}])
        diags = run_diagnostics(data)
        assert any(d["title"] == "Gateway connectivity problem" and d["severity"] == "critical" for d in diags)
        assert calculate_health_score(data)["score"] == 0


class TestGuestRunIsolation:
    def test_two_guests_do_not_share_a_run(self, client):
        a = {"X-Guest-Session": "guest-aaaa-1111"}
        b = {"X-Guest-Session": "guest-bbbb-2222"}
        client.post("/api/diagnostic-run?demo=dns-failure", headers=a)

        assert client.get("/api/diagnostic-run", headers=a).json()["is_demo"] is True
        assert client.get("/api/diagnostic-run", headers=b).json() is None
        assert client.get("/api/diagnostics", headers=b).json() == []

    def test_malformed_session_header_falls_back_safely(self, client):
        res = client.post("/api/diagnostic-run?demo=healthy", headers={"X-Guest-Session": "bad header!"})
        assert res.status_code == 200
        assert client.get("/api/diagnostic-run").json()["is_demo"] is True


class TestInputValidation:
    def test_bad_port_is_400_not_500(self, client):
        res = client.post("/api/diagnostic-run?port=abc")
        assert res.status_code == 400
        assert "Invalid port" in res.json()["detail"]

    def test_out_of_range_port_is_400(self, client):
        assert client.post("/api/diagnostic-run?port=70000").status_code == 400

    def test_unknown_demo_is_400(self, client):
        res = client.post("/api/diagnostic-run?demo=bogus")
        assert res.status_code == 400
        assert "Unknown demo scenario" in res.json()["detail"]

    def test_bad_host_is_400(self, client):
        assert client.post("/api/diagnostic-run?host=not a host").status_code == 400

    def test_history_limit_bounds(self, client):
        assert client.get("/api/history?limit=0").status_code == 400
        assert client.get("/api/history?limit=5000").status_code == 400
        assert client.get("/api/history?hours=-1").status_code == 400

    def test_backup_param_bounds(self, client):
        assert client.get("/api/backup/readiness?sla_hours=0").status_code == 400
        assert client.get("/api/backup/readiness?dataset_size_gb=-5").status_code == 400


class TestHealthReportsRealDbState:
    def test_unreachable_db_is_reported(self, client):
        from src import api
        api._db_probe_cache.update(at=0.0, status="unconfigured")
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", side_effect=RuntimeError("NXDOMAIN")):
            body = client.get("/api/health").json()
        assert body["status"] == "healthy"
        assert body["database"] == "unreachable"

    def test_probe_is_cached(self, client):
        from src import api
        api._db_probe_cache.update(at=0.0, status="unconfigured")
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            client.get("/api/health")
            client.get("/api/health")
        assert sb.call_count == 1


class TestAgentEndpointsDegradeGracefully:
    @pytest.fixture(autouse=True)
    def no_agent_token(self):
        from src.auth import verify_agent_token
        app.dependency_overrides[verify_agent_token] = lambda: True
        yield
        app.dependency_overrides.pop(verify_agent_token, None)

    def test_register_with_dead_db_is_503(self, client):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", side_effect=RuntimeError("down")):
            res = client.post("/api/agent/register", json={
                "name": "n", "hostname": "h", "platform": "Linux", "architecture": "x86_64",
                "ip_address": "10.0.0.1", "agent_version": "1", "status": "ONLINE",
            })
        assert res.status_code == 503
        assert "Database unavailable" in res.json()["detail"]

    def test_heartbeat_with_dead_db_is_503(self, client):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", side_effect=RuntimeError("down")):
            res = client.post("/api/agent/heartbeat", json={"device_id": "x"})
        assert res.status_code == 503

    def test_heartbeat_non_string_id_is_400(self, client):
        assert client.post("/api/agent/heartbeat", json={"device_id": 5}).status_code == 400


class TestTenantIsolation:
    """A DB error must degrade to 'nothing', never to 'everyone's data'."""

    @pytest.fixture
    def signed_in(self):
        from src.auth import get_optional_user, get_current_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "user-A"}
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-A"}
        yield
        app.dependency_overrides.pop(get_optional_user, None)
        app.dependency_overrides.pop(get_current_user, None)

    def test_signed_in_user_with_no_devices_gets_empty_list_not_the_server(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
            body = client.get("/api/devices").json()
        assert body == []

    def test_device_query_error_does_not_fall_back_to_all_users(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.select.return_value.eq.return_value.execute.side_effect = RuntimeError("boom")
            body = client.get("/api/devices").json()
            # exactly one query attempted; no un-scoped retry
            assert sb.return_value.table.return_value.select.return_value.eq.call_count == 1
        assert body == []

    def test_incident_query_error_returns_empty(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.select.return_value.eq.return_value.execute.side_effect = RuntimeError("boom")
            assert client.get("/api/incidents").json() == []

    def test_acknowledge_is_scoped_and_404s_for_foreign_incident(self, client, signed_in):
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            chain = sb.return_value.table.return_value.update.return_value.eq.return_value.eq.return_value.execute
            chain.return_value.data = []  # nothing matched (id, user_id)
            res = client.post("/api/incidents/someone-elses/acknowledge")
            assert res.status_code == 404
            # both .eq filters (id AND user_id) were applied
            assert sb.return_value.table.return_value.update.return_value.eq.return_value.eq.call_count == 1

    def test_guest_gets_labelled_hosted_server(self, client):
        from src.api import HOSTED_SERVER_AGENT_VERSION
        body = client.get("/api/devices").json()
        assert len(body) == 1
        assert body[0]["agent_version"] == HOSTED_SERVER_AGENT_VERSION
        assert "hosted" in body[0]["name"].lower()
        assert body[0]["is_backup_target"] is False


class TestSetupGuide:
    def test_guest_guide_withholds_user_id(self, client):
        body = client.get("/api/setup").json()
        assert body["user_id"] is None
        assert body["signed_in"] is False
        assert body["hosted_mode"] is True
        assert len(body["steps"]) == 4
        env_step = body["steps"][2]
        assert any(line.startswith("API_BASE_URL=") for line in env_step["commands"])
        assert any("<sign in" in line for line in env_step["commands"])
        assert "agent/agent.py --register" in " ".join(body["steps"][3]["commands"])

    def test_signed_in_guide_includes_user_id(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "user-xyz"}
        try:
            body = client.get("/api/setup").json()
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        assert body["user_id"] == "user-xyz"
        assert body["steps"][0]["done"] is True
        assert "NETSENTINEL_USER_ID=user-xyz" in body["steps"][2]["commands"]

    def test_agent_registration_carries_user_id(self, client):
        """The agent's payload must be able to bind the device to an account."""
        from src.auth import verify_agent_token
        app.dependency_overrides[verify_agent_token] = lambda: True
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb:
                res = client.post("/api/agent/register", json={
                    "user_id": "user-xyz", "name": "n", "hostname": "h", "platform": "Linux",
                    "architecture": "x86_64", "ip_address": "10.0.0.1", "agent_version": "1", "status": "ONLINE",
                })
                upserted = sb.return_value.table.return_value.upsert.call_args[0][0]
        finally:
            app.dependency_overrides.pop(verify_agent_token, None)
        assert res.status_code == 200
        assert upserted["user_id"] == "user-xyz"


class TestHistoryRange:
    def test_hours_is_forwarded_as_since(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api.get_history_supabase", return_value=[]) as gh:
                client.get("/api/history?limit=10&hours=6")
            _, kwargs = gh.call_args
            assert kwargs["since"] is not None
            with patch("src.api.get_history_supabase", return_value=[]) as gh:
                client.get("/api/history?limit=10")
            assert gh.call_args.kwargs["since"] is None
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
