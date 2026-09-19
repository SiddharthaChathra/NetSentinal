"""Per-target backup protocols: only what a target is meant to serve is
checked and scored."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.models import Device
from src.port_checker import get_backup_port_map, normalize_protocols, ALL_BACKUP_PROTOCOLS
from src.backup_readiness import status_from_telemetry, check_backup_target


def _dev(protocols=None, **over):
    base = dict(id="dev-laptop", name="LAPTOP-ATJOIONA", hostname="LAPTOP-ATJOIONA", platform="Windows",
                architecture="AMD64", ip_address="192.168.168.126", agent_version="1.0.0", status="ONLINE",
                is_backup_target=True, backup_protocols=protocols)
    base.update(over)
    return Device(**base)


# What the agent on a Windows laptop actually reports: SMB open, the rest not running.
LAPTOP_TELEMETRY = dict(
    device_id="dev-laptop", timestamp=datetime.now(timezone.utc).isoformat(), latency_ms=54.0, packet_loss=0.0,
    gateway_reachable=True, internet_reachable=True, dns_healthy=True, tcp_healthy=True,
    interface_errors=0, interface_drops=0,
    backup_ports=[{"port": 2049, "service": "NFS", "open": False}, {"port": 445, "service": "SMB", "open": True},
                  {"port": 3260, "service": "iSCSI", "open": False}, {"port": 10000, "service": "Replication", "open": False}],
)


class TestNormalize:
    def test_none_or_empty_means_all(self):
        assert normalize_protocols(None) is None
        assert normalize_protocols([]) is None

    def test_case_insensitive_dedup_canonical_order(self):
        assert normalize_protocols(["smb", "NFS", "Smb", "iscsi"]) == ["NFS", "SMB", "iSCSI"]

    def test_unknown_rejected(self):
        with pytest.raises(ValueError):
            normalize_protocols(["FTP"])

    def test_port_map_filters(self):
        assert set(get_backup_port_map(protocols=["SMB"]).values()) == {"SMB"}
        assert set(get_backup_port_map().values()) == set(ALL_BACKUP_PROTOCOLS)


class TestScoringHonoursProtocols:
    def test_windows_laptop_serving_smb_is_ready_with_no_warnings(self):
        status, diags = status_from_telemetry(_dev(["SMB"]), LAPTOP_TELEMETRY, dataset_size_gb=100, sla_window_hours=4)
        assert [p["service"] for p in status["ports"]] == ["SMB"]
        assert status["backup_readiness"]["score"] == 100
        assert status["backup_readiness"]["verdict"] == "ready"
        assert diags == []

    def test_default_all_protocols_keeps_previous_behaviour(self):
        status, diags = status_from_telemetry(_dev(None), LAPTOP_TELEMETRY, dataset_size_gb=100, sla_window_hours=4)
        assert len(status["ports"]) == 4
        assert status["backup_readiness"]["score"] == 78
        assert len(diags) == 3

    def test_nas_configured_for_nfs_is_still_flagged_when_nfs_dies(self):
        status, diags = status_from_telemetry(_dev(["NFS"], id="nas", name="nas"), LAPTOP_TELEMETRY)
        assert [p["service"] for p in status["ports"]] == ["NFS"]
        assert status["ports"][0]["open"] is False
        assert status["backup_readiness"]["verdict"] != "ready"
        assert len(diags) == 1 and diags[0]["category"] == "backup-protocol" and "NFS" in diags[0]["message"]

    @patch("src.backup_readiness.check_backup_ports")
    @patch("src.backup_readiness.ping_host")
    @patch("src.backup_readiness.check_dns")
    def test_server_probe_only_scans_chosen_protocols(self, dns, ping, ports):
        dns.return_value = [{"domain": "nas.corp", "success": True, "resolved_ip": "10.0.0.5", "response_ms": 1}]
        ping.return_value = {"target": "10.0.0.5", "reachable": True, "packet_loss": 0.0, "latency_ms": 5.0,
                             "min_latency_ms": 5.0, "max_latency_ms": 5.0}
        ports.return_value = [{"port": 2049, "service": "NFS", "open": True, "response_ms": 1}]
        check_backup_target(_dev(["NFS"], id="nas", name="nas", hostname="nas.corp", ip_address="10.0.0.5"))
        assert ports.call_args.kwargs["protocols"] == ["NFS"]


class TestApi:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def owner(self):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
        with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
            sb.return_value.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "dev-1"}]
            yield sb
        app.dependency_overrides.pop(get_current_user, None)

    def test_protocols_endpoint(self, client):
        assert client.get("/api/backup/protocols").json() == {"protocols": list(ALL_BACKUP_PROTOCOLS)}

    def test_tag_with_protocols(self, client, owner):
        res = client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": True, "backup_protocols": ["smb"]})
        assert res.status_code == 200
        assert res.json() == {"id": "dev-1", "is_backup_target": True, "backup_protocols": ["SMB"]}
        update = owner.return_value.table.return_value.update.call_args[0][0]
        assert update["backup_protocols"] == ["SMB"] and update["is_backup_target"] is True

    def test_empty_list_resets_to_all(self, client, owner):
        res = client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": True, "backup_protocols": []})
        assert res.json()["backup_protocols"] is None
        assert owner.return_value.table.return_value.update.call_args[0][0]["backup_protocols"] is None

    def test_unknown_protocol_is_400(self, client, owner):
        res = client.post("/api/devices/dev-1/backup-target", json={"backup_protocols": ["FTP"]})
        assert res.status_code == 400 and "FTP" in res.json()["detail"]

    def test_non_list_is_400(self, client, owner):
        assert client.post("/api/devices/dev-1/backup-target", json={"backup_protocols": "SMB"}).status_code == 400

    def test_tag_only_leaves_protocols_untouched(self, client, owner):
        client.post("/api/devices/dev-1/backup-target", json={"is_backup_target": False})
        assert "backup_protocols" not in owner.return_value.table.return_value.update.call_args[0][0]

    def test_foreign_device_is_404(self, client, owner):
        owner.return_value.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        assert client.post("/api/devices/not-mine/backup-target", json={}).status_code == 404

    def test_reregistration_preserves_protocols(self, client):
        from src.auth import verify_agent_token, AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity("u1", "user")
        try:
            with patch("src.api.is_database_configured", return_value=True), patch("src.api.get_supabase") as sb:
                table = sb.return_value.table.return_value
                table.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
                    {"id": "dev-1", "created_at": "2026-08-01T00:00:00+00:00", "is_backup_target": True, "backup_protocols": ["SMB"]}]
                res = client.post("/api/agent/register", json={
                    "name": "n", "hostname": "LAPTOP-ATJOIONA", "platform": "Windows", "architecture": "AMD64",
                    "ip_address": "1.2.3.4", "agent_version": "1", "status": "ONLINE"})
                upserted = table.upsert.call_args[0][0]
        finally:
            app.dependency_overrides.pop(verify_agent_token, None)
        assert res.status_code == 200
        assert upserted["backup_protocols"] == ["SMB"]


class TestVerifyHint:
    def test_platform_specific_commands(self):
        from src.backup_readiness import verify_listening_command as v
        assert v(2049, "Windows") == "netstat -an | findstr :2049"
        assert v(2049, "Linux") == "sudo ss -ltnp | grep ':2049'"
        assert v(2049, "Darwin") == "lsof -iTCP:2049 -sTCP:LISTEN"
        assert v(2049, None) == "sudo ss -ltnp | grep ':2049'"

    def test_recommendation_carries_the_hint_for_the_device_platform(self):
        _, diags = status_from_telemetry(_dev(["NFS"], platform="Linux"), LAPTOP_TELEMETRY)
        rec = diags[0]["recommendation"]
        assert "sudo ss -ltnp | grep ':2049'" in rec
        assert "Serves backups over" in rec
        _, diags = status_from_telemetry(_dev(["NFS"], platform="Windows"), LAPTOP_TELEMETRY)
        assert "netstat -an | findstr :2049" in diags[0]["recommendation"]
