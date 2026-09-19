"""GET /api/report — the downloadable report must be internally consistent
and honest about what was scanned."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app, _last_runs
from src.models import Device
from src.health_score import calculate_health_score


@pytest.fixture
def client():
    _last_runs.clear()
    return TestClient(app)


# The exact situation from the downloaded report: ICMP-silent cloud gateway,
# everything else fine.
CLOUD_HOST = {
    "interfaces": [{"name": "eth0", "ipv4": "10.196.191.29", "state": "UP"}],
    "gateway": {"address": "169.254.1.1", "reachable": False, "packet_loss": 100.0, "latency_ms": 0.0},
    "internet": {"target": "8.8.8.8", "reachable": True, "packet_loss": 0.0, "latency_ms": 7.256},
    "dns": [{"domain": "google.com", "success": True}],
    "tcp": [{"host": "google.com", "port": 443, "success": True}],
    "diagnostics": [{"severity": "info", "title": "Gateway does not answer ICMP"}],
}


class TestStatusLabel:
    def test_info_only_findings_are_not_warnings(self):
        health = calculate_health_score(CLOUD_HOST)
        assert health["score"] == 97
        assert health["status"] == "HEALTHY"

    def test_real_warning_keeps_the_label(self):
        data = dict(CLOUD_HOST, diagnostics=[{"severity": "warning", "title": "High latency"}])
        assert calculate_health_score(data)["status"] == "HEALTHY (WITH WARNINGS)"

    def test_perfect_score_is_healthy(self):
        data = dict(CLOUD_HOST, gateway={"reachable": True, "packet_loss": 0.0, "latency_ms": 1.0}, diagnostics=[])
        assert calculate_health_score(data) == {"score": 100, "status": "HEALTHY"}


class TestReportGuest:
    def test_no_run_yet(self, client):
        body = client.get("/api/report").json()
        assert body["report_version"] == "2"
        assert body["hosted_scan"] is None
        assert body["devices"] == []
        assert body["backup_readiness"] is None
        assert body["summary"]["hosted_scan_status"] == "NOT RUN"
        assert body["generated_at"].endswith("+00:00")

    def test_hosted_scan_is_labelled_and_consistent(self, client):
        client.post("/api/diagnostic-run?demo=healthy", headers={"X-Guest-Session": "guest-report-1"})
        body = client.get("/api/report", headers={"X-Guest-Session": "guest-report-1"}).json()
        scan = body["hosted_scan"]
        assert "NetSentinel server on its own network" in scan["note"]
        assert scan["server_hostname"] == "DEMO-PC"
        assert scan["metrics"]["gateway"] == "PASS"
        assert scan["status"] == "HEALTHY"
        assert body["summary"]["open_warnings"] == 0

    def test_icmp_silent_gateway_is_not_a_fail(self, client):
        from src.models import DiagnosticResult, DiagnosticFinding
        from src import api
        run = DiagnosticResult(
            timestamp="2026-09-19T15:48:16+00:00", health_score=97, status="HEALTHY",
            system={"hostname": "srv-render", "local_ip": "10.196.191.29"}, interfaces=[],
            gateway=CLOUD_HOST["gateway"], internet=CLOUD_HOST["internet"], dns=CLOUD_HOST["dns"], tcp=CLOUD_HOST["tcp"],
            routes={}, stability={}, duration_ms=8953,
            diagnostics=[DiagnosticFinding(severity="info", title="Gateway does not answer ICMP", likely_cause="",
                                           evidence=[], recommended_checks=[], confidence="high")],
        )
        api._set_user_run(None, run, "guest-report-2")
        body = client.get("/api/report", headers={"X-Guest-Session": "guest-report-2"}).json()
        assert body["hosted_scan"]["metrics"]["gateway"] == "PASS (ICMP filtered)"
        assert body["hosted_scan"]["status"] == "HEALTHY"
        assert body["summary"]["open_warnings"] == 0


class TestReportSignedIn:
    def _laptop(self):
        return Device(id="dev-laptop", name="LAPTOP-ATJOIONA", hostname="LAPTOP-ATJOIONA", platform="Windows",
                      architecture="AMD64", ip_address="192.168.168.126", agent_version="1.0.0", status="ONLINE",
                      is_backup_target=True, backup_protocols=["SMB"])

    def _telemetry(self, age_s=10):
        return {"device_id": "dev-laptop", "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(),
                "latency_ms": 24.0, "packet_loss": 0.0, "gateway_reachable": True, "internet_reachable": True,
                "dns_healthy": True, "tcp_healthy": True, "interface_errors": 0, "interface_drops": 0,
                "gateway_ip": "192.168.168.251",
                "backup_ports": [{"port": 445, "service": "SMB", "open": True}, {"port": 2049, "service": "NFS", "open": False}]}

    def test_devices_and_backup_readiness_included(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api._fetch_devices", return_value=[self._laptop()]), \
                 patch("src.api._latest_telemetry_for", return_value={"dev-laptop": self._telemetry()}):
                body = client.get("/api/report").json()
        finally:
            app.dependency_overrides.pop(get_optional_user, None)

        assert body["account"]["signed_in"] is True
        assert len(body["devices"]) == 1
        dev = body["devices"][0]
        assert dev["name"] == "LAPTOP-ATJOIONA"
        assert dev["backup_protocols"] == ["SMB"]
        assert dev["latest_telemetry"]["gateway_ip"] == "192.168.168.251"
        assert dev["latest_telemetry"]["stale"] is False

        br = body["backup_readiness"]
        assert br["targets"][0]["reachability"] == "up"
        assert br["targets"][0]["backupReadiness"]["verdict"] == "ready"
        assert [p["service"] for p in br["targets"][0]["ports"]] == ["SMB"]
        assert body["summary"] == {
            "hosted_scan_status": "NOT RUN", "hosted_scan_health_score": None,
            "devices": 1, "devices_reporting": 1, "backup_targets": 1,
            "backup_readiness_score": 100, "open_warnings": 0,
        }

    def test_stale_agent_is_flagged(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api._fetch_devices", return_value=[self._laptop()]), \
                 patch("src.api._latest_telemetry_for", return_value={"dev-laptop": self._telemetry(age_s=3600)}):
                body = client.get("/api/report").json()
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        assert body["devices"][0]["latest_telemetry"]["stale"] is True
        assert body["summary"]["devices_reporting"] == 0
        assert body["backup_readiness"]["targets"][0]["reachability"] == "down"
