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


class TestRunIsolationBetweenAccounts:
    """Diagnostic runs were keyed per guest browser session; with guest access
    gone they are keyed per account, and must not cross between accounts."""

    def test_two_accounts_do_not_share_a_run(self, client, other_client):
        client.post("/api/diagnostic-run?demo=dns-failure")

        assert client.get("/api/diagnostic-run").json()["is_demo"] is True
        assert other_client.get("/api/diagnostic-run").json() is None
        assert other_client.get("/api/diagnostics").json() == []

    def test_a_run_is_not_reachable_without_a_session(self, client, anon_client):
        client.post("/api/diagnostic-run?demo=healthy")
        assert anon_client.get("/api/diagnostic-run").status_code == 401
        assert anon_client.get("/api/diagnostics").status_code == 401

    def test_guest_session_header_is_ignored(self, client, other_client):
        """The old X-Guest-Session header must not resurrect a shared slot:
        identity comes from the token and nothing else."""
        client.post("/api/diagnostic-run?demo=dns-failure", headers={"X-Guest-Session": "guest-aaaa-1111"})
        spoofed = other_client.get("/api/diagnostic-run", headers={"X-Guest-Session": "guest-aaaa-1111"})
        assert spoofed.json() is None


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
        # Two probes run on a cold /api/health — connectivity and schema —
        # and each is cached, so the second request adds no calls at all.
        assert sb.call_count == 2


class TestAgentEndpointsDegradeGracefully:
    @pytest.fixture(autouse=True)
    def no_agent_token(self):
        from src.auth import verify_agent_token
        from src.auth import AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity(None, "open")
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
        assert res.json()["detail"] == "Could not register device: down"

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
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-A"}
        try:
            yield
        finally:
            # finally, not a bare pop after yield: a failing test would
            # otherwise leak this override into every later test.
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


class TestHistoryRange:
    def test_hours_is_forwarded_as_since(self, client):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api.get_history_supabase", return_value=[]) as gh:
                client.get("/api/history?limit=10&hours=6")
            _, kwargs = gh.call_args
            assert kwargs["since"] is not None
            with patch("src.api.get_history_supabase", return_value=[]) as gh:
                client.get("/api/history?limit=10")
            assert gh.call_args.kwargs["since"] is None
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestSchemaSelfCheck:
    def _reset(self):
        from src import api
        api._schema_cache.update(at=0.0, result=None)
        api._db_probe_cache.update(at=0.0, status="unconfigured")

    def test_missing_user_id_column_is_reported(self, client):
        from src import api
        self._reset()

        class APIError(Exception):
            def __init__(self, message, code="42703"):
                super().__init__(message); self.message = message; self.code = code

        def fake_select(cols):
            class Q:
                def limit(self_inner, n):
                    class E:
                        def execute(self_e):
                            if "user_id" in cols.split(","):
                                raise APIError("column devices.user_id does not exist")
                            return type("R", (), {"data": []})()
                    return E()
            return Q()

        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            table = sb.return_value.table
            def table_side_effect(name):
                t = type("T", (), {})()
                t.select = fake_select if name == "devices" else (lambda cols: fake_select("x"))
                return t
            table.side_effect = table_side_effect
            body = client.get("/api/health").json()
        assert body["schema"]["ok"] is False
        assert "devices.user_id" in body["schema"]["missing"]
        assert not any(m.startswith("incidents") for m in body["schema"]["missing"])

    def test_complete_schema_is_ok(self, client):
        self._reset()
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.select.return_value.limit.return_value.execute.return_value.data = []
            body = client.get("/api/health").json()
        assert body["schema"] == {"ok": True, "missing": []}
