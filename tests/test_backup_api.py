"""Integration tests for the Backup Readiness API.

Runs a full check cycle through the real FastAPI app (TestClient) with only
the network primitives mocked, and verifies the JSON response matches the
shared frontend data contract exactly — field names (camelCase), types, and
nesting — plus no regression for non-backup targets and legacy endpoints.
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api import app
from src.models import Device
from src.port_checker import get_backup_port_map


# The exact contract the frontend is built against. Values are type
# exemplars; enum fields are validated against their allowed values below.
CONTRACT = {
    "healthScore": int,
    "backupReadinessScore": int,
    "targets": [{
        "id": str,
        "name": str,
        "isBackupTarget": bool,
        "reachability": str,
        "dnsResolved": bool,
        "latencyMs": float,
        "packetLossPct": float,
        "ports": [{"port": int, "service": str, "open": bool}],
        "backupReadiness": {
            "score": int,
            "verdict": str,
            "slaWindowHours": float,
            "estimatedTransferHours": float,
            "willMeetSla": bool,
        },
    }],
    "diagnostics": [{
        "id": str,
        "severity": str,
        "category": str,
        "message": str,
        "recommendation": str,
        "affectedTargetId": str,
        "timestamp": str,
    }],
    "simulatedScenarios": [{"id": str, "name": str, "type": str, "status": str}],
}

ENUMS = {
    "reachability": {"up", "degraded", "down"},
    "verdict": {"ready", "at-risk", "not-ready"},
    "severity": {"info", "warning", "critical"},
    "category": {"gateway", "routing", "dns", "firewall", "backup-protocol", "throughput"},
    "type": {"dns-flap", "port-blocked", "throughput-drop"},
    "status": {"pass", "fail"},
}


def assert_matches_contract(actual, expected, path="$"):
    """Recursively asserts exact key sets and value types. Lists are checked
    element-wise against the single exemplar in the contract."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {type(actual).__name__}"
        assert set(actual.keys()) == set(expected.keys()), (
            f"{path}: key mismatch\n  extra:   {set(actual) - set(expected)}\n  missing: {set(expected) - set(actual)}"
        )
        for key, exp in expected.items():
            assert_matches_contract(actual[key], exp, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected array, got {type(actual).__name__}"
        for i, item in enumerate(actual):
            assert_matches_contract(item, expected[0], f"{path}[{i}]")
    else:
        if expected is float:
            assert isinstance(actual, (int, float)) and not isinstance(actual, bool), f"{path}: expected number, got {actual!r}"
        else:
            assert type(actual) is expected, f"{path}: expected {expected.__name__}, got {type(actual).__name__} ({actual!r})"
        leaf = path.rsplit(".", 1)[-1]
        if leaf in ENUMS:
            assert actual in ENUMS[leaf], f"{path}: {actual!r} not in {ENUMS[leaf]}"


def _tagged_device(**overrides):
    base = dict(
        id="dev-backup-1", name="nas-backup-01", hostname="nas-backup-01.corp.local",
        platform="Linux", architecture="x86_64", ip_address="10.10.0.20",
        agent_version="1.0", status="ONLINE", is_backup_target=True,
    )
    base.update(overrides)
    return Device(**base)


def _ports(**closed):
    return [{"port": p, "service": s, "open": not closed.get(s, False), "response_ms": 1.0}
            for p, s in get_backup_port_map().items()]


def _dns(ok=True):
    return [{"domain": "nas-backup-01.corp.local", "success": ok,
             "resolved_ip": "10.10.0.20" if ok else None, "response_ms": 3.0}]


def _ping(reachable=True, loss=0.0, latency=12.0):
    return {"target": "10.10.0.20", "reachable": reachable, "packet_loss": loss,
            "latency_ms": latency, "min_latency_ms": latency, "max_latency_ms": latency}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def network():
    """Patches every network primitive the backup module touches and hands
    back the mocks so a test can shape the scenario."""
    with patch("src.backup_readiness.check_dns") as dns, \
         patch("src.backup_readiness.ping_host") as ping, \
         patch("src.backup_readiness.check_backup_ports") as ports:
        dns.return_value = _dns()
        ping.return_value = _ping()
        ports.return_value = _ports()
        yield {"dns": dns, "ping": ping, "ports": ports}


class TestContractShape:

    def test_empty_report_matches_contract(self, client):
        """No tagged devices (default local fallback) -> valid, empty report."""
        res = client.get("/api/backup/readiness")
        assert res.status_code == 200
        body = res.json()
        assert_matches_contract(body, CONTRACT)
        assert body["targets"] == []
        assert body["diagnostics"] == []
        assert body["simulatedScenarios"] == []
        assert body["backupReadinessScore"] == 0

    def test_full_cycle_against_tagged_target_matches_contract(self, client, network):
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            res = client.get("/api/backup/readiness?dataset_size_gb=100&sla_hours=6")
        assert res.status_code == 200
        body = res.json()
        assert_matches_contract(body, CONTRACT)

        assert len(body["targets"]) == 1
        target = body["targets"][0]
        assert target["id"] == "dev-backup-1"
        assert target["name"] == "nas-backup-01"
        assert target["isBackupTarget"] is True
        assert target["reachability"] == "up"
        assert target["dnsResolved"] is True
        assert target["latencyMs"] == 12.0
        assert target["packetLossPct"] == 0.0
        assert {p["service"] for p in target["ports"]} == {"NFS", "SMB", "iSCSI", "Replication"}
        assert target["backupReadiness"]["slaWindowHours"] == 6.0
        assert target["backupReadiness"]["verdict"] == "ready"
        assert target["backupReadiness"]["willMeetSla"] is True
        assert body["backupReadinessScore"] == 100

    def test_response_uses_camel_case_not_snake_case(self, client, network):
        """The legacy endpoints are snake_case; this one must not leak that."""
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness").json()
        raw = str(body)
        for leaked in ("is_backup_target", "dns_resolved", "latency_ms", "packet_loss_pct",
                       "backup_readiness", "sla_window_hours", "estimated_transfer_hours",
                       "will_meet_sla", "affected_target_id", "health_score", "simulated_scenarios"):
            assert leaked not in raw, f"snake_case field leaked into contract response: {leaked}"

    def test_port_entries_expose_only_contract_fields(self, client, network):
        """check_backup_ports returns response_ms internally; the contract does not."""
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness").json()
        for p in body["targets"][0]["ports"]:
            assert set(p.keys()) == {"port", "service", "open"}


class TestScenariosThroughApi:

    def test_dns_broken_target(self, client, network):
        network["dns"].return_value = _dns(ok=False)
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness").json()
        assert_matches_contract(body, CONTRACT)

        target = body["targets"][0]
        assert target["dnsResolved"] is False
        assert target["reachability"] == "up"  # host still up via known IP
        assert target["backupReadiness"]["verdict"] == "at-risk"

        assert len(body["diagnostics"]) == 1
        diag = body["diagnostics"][0]
        assert diag["category"] == "dns"
        assert diag["severity"] == "critical"
        assert diag["affectedTargetId"] == "dev-backup-1"
        assert "Check DNS entry, not bandwidth" in diag["message"]

    def test_nfs_port_blocked_by_firewall(self, client, network):
        network["ports"].return_value = _ports(NFS=True)
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness").json()
        assert_matches_contract(body, CONTRACT)

        target = body["targets"][0]
        nfs = next(p for p in target["ports"] if p["service"] == "NFS")
        assert nfs["open"] is False
        assert nfs["port"] == 2049
        assert target["backupReadiness"]["verdict"] == "at-risk"
        assert target["backupReadiness"]["score"] == 78

        assert len(body["diagnostics"]) == 1
        diag = body["diagnostics"][0]
        assert diag["category"] == "firewall"
        assert "NFS port (2049) is closed" in diag["message"]
        assert "firewall rule, not a network outage" in diag["message"]
        assert "firewall" in diag["recommendation"].lower()

    def test_host_down(self, client, network):
        network["ping"].return_value = _ping(reachable=False, loss=100.0, latency=0.0)
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness").json()
        assert_matches_contract(body, CONTRACT)

        target = body["targets"][0]
        assert target["reachability"] == "down"
        assert target["ports"] == []
        assert target["backupReadiness"]["verdict"] == "not-ready"
        assert body["diagnostics"][0]["category"] == "gateway"
        network["ports"].assert_not_called()

    def test_throughput_sla_miss(self, client, network):
        network["ping"].return_value = _ping(loss=8.0, latency=350.0)
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness?dataset_size_gb=500&sla_hours=4").json()
        assert_matches_contract(body, CONTRACT)

        target = body["targets"][0]
        assert target["reachability"] == "degraded"
        readiness = target["backupReadiness"]
        assert readiness["willMeetSla"] is False
        assert readiness["estimatedTransferHours"] > readiness["slaWindowHours"]

        categories = [d["category"] for d in body["diagnostics"]]
        assert categories == ["throughput"]
        assert "will NOT meet SLA" in body["diagnostics"][0]["message"]

    def test_multiple_targets_aggregate_score(self, client, network):
        healthy = _tagged_device(id="a", name="a")
        broken = _tagged_device(id="b", name="b")
        untagged = _tagged_device(id="c", name="c", is_backup_target=False)

        def ping_by_host(host):
            return _ping()
        network["ping"].side_effect = ping_by_host

        def ports_by_host(host, *args, **kwargs):
            return _ports()
        network["ports"].side_effect = ports_by_host

        # Break only the DNS of device "b" by returning different results per hostname
        network["dns"].side_effect = lambda domains: _dns(ok=(domains[0] != "b.corp"))
        broken.hostname = "b.corp"
        healthy.hostname = "a.corp"

        with patch("src.api._fetch_devices", return_value=[healthy, broken, untagged]):
            body = client.get("/api/backup/readiness").json()
        assert_matches_contract(body, CONTRACT)

        assert [t["id"] for t in body["targets"]] == ["a", "b"]  # "c" excluded
        assert body["backupReadinessScore"] == round((100 + 75) / 2)
        assert [d["affectedTargetId"] for d in body["diagnostics"]] == ["b"]


class TestSimulatedScenarioParam:

    @pytest.mark.parametrize("scenario, expected_category", [
        ("dns-flap", "dns"),
        ("port-blocked", "firewall"),
        ("throughput-drop", "throughput"),
    ])
    def test_demo_yields_a_complete_preview(self, client, scenario, expected_category):
        """A demo must give the frontend a full target card + diagnostics +
        scenario verdict, not just the scenario entry (mirrors /api/diagnostic-run?demo=)."""
        res = client.get(f"/api/backup/readiness?demo={scenario}")
        assert res.status_code == 200
        body = res.json()
        assert_matches_contract(body, CONTRACT)

        assert len(body["simulatedScenarios"]) == 1
        entry = body["simulatedScenarios"][0]
        assert entry["type"] == scenario
        assert entry["status"] == "fail"
        assert set(entry.keys()) == {"id", "name", "type", "status"}

        assert len(body["targets"]) == 1
        target = body["targets"][0]
        assert target["id"] == f"demo-{scenario}"
        assert target["name"] == entry["name"]
        assert target["isBackupTarget"] is True
        assert target["backupReadiness"]["verdict"] != "ready"
        assert body["backupReadinessScore"] == target["backupReadiness"]["score"]

        assert len(body["diagnostics"]) >= 1
        assert body["diagnostics"][0]["category"] == expected_category
        assert all(d["affectedTargetId"] == target["id"] for d in body["diagnostics"])

    def test_demo_target_sits_alongside_real_targets(self, client, network):
        with patch("src.api._fetch_devices", return_value=[_tagged_device()]):
            body = client.get("/api/backup/readiness?demo=port-blocked").json()
        assert_matches_contract(body, CONTRACT)
        assert [t["id"] for t in body["targets"]] == ["dev-backup-1", "demo-port-blocked"]
        assert body["backupReadinessScore"] == round((100 + 78) / 2)

    def test_unknown_demo_is_a_400(self, client):
        res = client.get("/api/backup/readiness?demo=nonsense")
        assert res.status_code == 400
        assert "Unknown backup scenario" in res.json()["detail"]


class TestHealthScorePassthrough:

    def test_health_score_reflects_last_diagnostic_run(self, client):
        run = client.post("/api/diagnostic-run?demo=healthy").json()
        body = client.get("/api/backup/readiness").json()
        assert body["healthScore"] == run["health_score"]


class TestBackupTargetTagging:
    """Tagging mutates a user's device, so it is auth-protected. Tests
    override the auth dependency (standard FastAPI pattern) and stub the
    Supabase update so no real write happens."""

    @pytest.fixture(autouse=True)
    def authed(self):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}
        try:
            # The suite runs with no Supabase credentials so it never touches a
            # real project; this class is specifically about the write path, so
            # it says so explicitly rather than depending on the environment.
            with patch("src.api.is_database_configured", return_value=True),                  patch("src.api.get_supabase") as sb:
                sb.return_value.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "dev-1"}]
                yield sb
        finally:
            # Must run even when the test fails, or the override leaks into
            # every later test in the session.
            app.dependency_overrides.pop(get_current_user, None)

    def test_tag_endpoint_returns_new_state(self, client, authed):
        res = client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": True})
        assert res.status_code == 200
        assert res.json() == {"id": "dev-1", "is_backup_target": True}

        update_payload = authed.return_value.table.return_value.update.call_args[0][0]
        assert update_payload["is_backup_target"] is True
        assert "updated_at" in update_payload

    def test_untag(self, client):
        res = client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": False})
        assert res.status_code == 200
        assert res.json()["is_backup_target"] is False

    def test_tag_defaults_to_true(self, client):
        res = client.post("/api/devices/dev-1/backup-target", json={})
        assert res.json()["is_backup_target"] is True

    def test_unauthenticated_tagging_is_rejected(self, anon_client):
        from src.auth import get_current_user
        app.dependency_overrides.pop(get_current_user, None)
        res = anon_client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": True})
        assert res.status_code == 401
        assert res.json()["code"] == "not_authenticated"

    def test_db_failure_is_a_503_not_a_silent_success(self, client, authed):
        authed.return_value.table.return_value.update.side_effect = RuntimeError("db down")
        res = client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": True})
        assert res.status_code == 503


class TestNoRegressionForGenericTargets:

    def test_devices_endpoint_unchanged_and_untagged_by_default(self, client):
        """Legacy snake_case shape preserved; new field is additive and defaults False."""
        res = client.get("/api/devices")
        assert res.status_code == 200
        devices = res.json()
        assert len(devices) >= 1
        d = devices[0]
        for legacy_field in ("id", "name", "hostname", "platform", "ip_address", "agent_version", "status"):
            assert legacy_field in d
        assert d["is_backup_target"] is False

    def test_untagged_device_is_never_probed(self, client, network):
        with patch("src.api._fetch_devices", return_value=[_tagged_device(is_backup_target=False)]):
            body = client.get("/api/backup/readiness").json()
        assert body["targets"] == []
        network["dns"].assert_not_called()
        network["ping"].assert_not_called()
        network["ports"].assert_not_called()

    def test_generic_diagnostic_run_still_works(self, client):
        """The core health-check pipeline is untouched by the backup module."""
        res = client.post("/api/diagnostic-run?demo=healthy")
        assert res.status_code == 200
        body = res.json()
        assert body["health_score"] == 100
        assert "backup_readiness" not in body
        assert "backupReadiness" not in body
