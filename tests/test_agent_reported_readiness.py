"""Backup readiness for agent-managed devices is derived from the agent's
own telemetry, not from a server-side DNS/ping that can never reach a
private LAN host."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.models import Device
from src.backup_readiness import (
    status_from_telemetry, build_backup_readiness_report, AGENT_STALE_SECONDS,
)


def _laptop(**over):
    base = dict(id="dev-laptop", name="LAPTOP-ATJOIONA", hostname="LAPTOP-ATJOIONA",
                platform="Windows", architecture="AMD64", ip_address="192.168.168.126",
                agent_version="1.0.0", status="ONLINE", is_backup_target=True)
    base.update(over)
    return Device(**base)


def _telemetry(age_s=10, **over):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    t = dict(device_id="dev-laptop", timestamp=ts.isoformat(), latency_ms=24.0, packet_loss=0.0,
             gateway_reachable=True, internet_reachable=True, dns_healthy=True, tcp_healthy=True,
             interface_errors=0, interface_drops=0,
             backup_ports=[{"port": 2049, "service": "NFS", "open": True},
                           {"port": 445, "service": "SMB", "open": True},
                           {"port": 3260, "service": "iSCSI", "open": True},
                           {"port": 10000, "service": "Replication", "open": True}])
    t.update(over)
    return t


class TestStatusFromTelemetry:
    def test_healthy_laptop_is_ready(self):
        status, diags = status_from_telemetry(_laptop(), _telemetry(), dataset_size_gb=100, sla_window_hours=4)
        assert status["reachability"] == "up"
        assert status["dns_resolved"] is True
        assert status["latency_ms"] == 24.0
        assert status["backup_readiness"]["verdict"] == "ready"
        assert status["backup_readiness"]["will_meet_sla"] is True
        assert diags == []

    def test_no_server_side_network_calls(self):
        with patch("src.backup_readiness.check_dns") as dns, \
             patch("src.backup_readiness.ping_host") as ping, \
             patch("src.backup_readiness.check_backup_ports") as ports:
            status_from_telemetry(_laptop(), _telemetry())
        dns.assert_not_called(); ping.assert_not_called(); ports.assert_not_called()

    def test_stale_agent_is_down_with_agent_specific_message(self):
        status, diags = status_from_telemetry(_laptop(), _telemetry(age_s=AGENT_STALE_SECONDS + 600))
        assert status["reachability"] == "down"
        assert status["ports"] == []
        assert status["backup_readiness"]["verdict"] == "not-ready"
        assert len(diags) == 1
        assert diags[0]["category"] == "gateway"
        assert "agent on 'LAPTOP-ATJOIONA' last reported" in diags[0]["message"]
        assert "unreachable at the network layer" not in diags[0]["message"]

    def test_agent_dns_failure_maps_to_dns_rule(self):
        status, diags = status_from_telemetry(_laptop(), _telemetry(dns_healthy=False))
        assert status["dns_resolved"] is False
        assert [d["category"] for d in diags] == ["dns"]

    def test_closed_port_reported_by_agent(self):
        t = _telemetry()
        t["backup_ports"][0]["open"] = False  # NFS
        status, diags = status_from_telemetry(_laptop(), t)
        nfs = next(p for p in status["ports"] if p["service"] == "NFS")
        assert nfs["open"] is False
        assert [d["category"] for d in diags] == ["backup-protocol"]
        assert "not listening" in diags[0]["message"] and "firewall" not in diags[0]["message"]

    def test_packet_loss_is_degraded(self):
        status, _ = status_from_telemetry(_laptop(), _telemetry(packet_loss=3.0))
        assert status["reachability"] == "degraded"

    def test_old_agent_without_port_data(self):
        """Agents predating backup_ports still get a sensible result."""
        t = _telemetry(); t["backup_ports"] = None
        status, diags = status_from_telemetry(_laptop(), t)
        assert status["ports"] == []
        assert status["backup_readiness"]["verdict"] == "ready"
        assert diags == []

    def test_accepts_datetime_and_z_suffix_timestamps(self):
        t = _telemetry(); t["timestamp"] = datetime.now(timezone.utc)
        assert status_from_telemetry(_laptop(), t)[0]["reachability"] == "up"
        t["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert status_from_telemetry(_laptop(), t)[0]["reachability"] == "up"


class TestReportRouting:
    @patch("src.backup_readiness.check_backup_target")
    def test_agent_devices_skip_server_probe(self, mock_probe):
        report = build_backup_readiness_report([_laptop()], telemetry_by_device={"dev-laptop": _telemetry()})
        mock_probe.assert_not_called()
        assert report["targets"][0]["reachability"] == "up"
        assert report["backup_readiness_score"] == 100

    @patch("src.backup_readiness.check_backup_target")
    def test_devices_without_telemetry_are_probed(self, mock_probe):
        mock_probe.return_value = (
            {"id": "nas", "name": "nas", "is_backup_target": True, "reachability": "up", "dns_resolved": True,
             "latency_ms": 1, "packet_loss_pct": 0, "ports": [],
             "backup_readiness": {"score": 100, "verdict": "ready", "sla_window_hours": 4,
                                  "estimated_transfer_hours": 1, "will_meet_sla": True}},
            [],
        )
        nas = _laptop(id="nas", name="nas", hostname="nas.corp", ip_address="10.0.0.5")
        build_backup_readiness_report([nas], telemetry_by_device={})
        mock_probe.assert_called_once()


class TestApiWiring:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_latest_row_per_device_is_used(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        older = _telemetry(age_s=3600, latency_ms=999.0)
        newer = _telemetry(age_s=5, latency_ms=24.0)
        try:
            with patch("src.api._fetch_devices", return_value=[_laptop()]), \
                 patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb:
                chain = sb.return_value.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute
                chain.return_value.data = [newer, older]  # newest first, as ordered
                body = client.get("/api/backup/readiness").json()
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        t = body["targets"][0]
        assert t["name"] == "LAPTOP-ATJOIONA"
        assert t["reachability"] == "up"
        assert t["latencyMs"] == 24.0
        assert t["backupReadiness"]["verdict"] == "ready"
        assert body["diagnostics"] == []

    def test_telemetry_ingest_accepts_backup_ports(self, client):
        from src.auth import verify_agent_token, AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity("u1", "user")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb, \
                 patch("src.alert_engine.evaluate_alerts"), \
                 patch("src.anomaly_engine.detect_anomalies", return_value=[]), \
                 patch("src.incident_engine.evaluate_and_create_incidents"):
                res = client.post("/api/agent/telemetry", json=_telemetry())
                inserted = sb.return_value.table.return_value.insert.call_args[0][0]
        finally:
            app.dependency_overrides.pop(verify_agent_token, None)
        assert res.status_code == 200
        assert inserted["backup_ports"][0] == {"port": 2049, "service": "NFS", "open": True}
