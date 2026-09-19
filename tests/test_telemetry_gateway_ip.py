"""gateway_ip travels agent -> telemetry -> /api/telemetry/latest."""
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.models import Device, Telemetry

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agent"))
import collector  # noqa: E402  (agent module; patched by name below)


@pytest.fixture
def client():
    return TestClient(app)


def _row(device_id, gateway_ip, ts="2026-09-19T10:00:00+00:00"):
    return {"id": f"t-{device_id}", "device_id": device_id, "timestamp": ts, "latency_ms": 20.0,
            "packet_loss": 0.0, "gateway_reachable": True, "internet_reachable": True,
            "dns_healthy": True, "tcp_healthy": True, "interface_errors": 0, "interface_drops": 0,
            "backup_ports": None, "gateway_ip": gateway_ip}


class TestCollector:
    @patch("collector.get_interfaces", return_value=[])
    @patch("collector.check_ports", return_value=[{"success": True}])
    @patch("collector.check_dns", return_value=[{"success": True}])
    @patch("collector.check_internet_connectivity", return_value={"reachable": True, "packet_loss": 0.0, "latency_ms": 20.0})
    @patch("collector.check_gateway_connectivity", return_value={"reachable": True, "latency_ms": 1.0, "packet_loss": 0.0})
    @patch("collector.get_default_gateway", return_value={"gateway": "192.168.168.251"})
    @patch("collector.check_backup_ports", return_value=[])
    def test_gateway_ip_is_reported(self, *_):
        t = collector.collect_telemetry("dev-1")
        assert t["gateway_ip"] == "192.168.168.251"

    @patch("collector.get_interfaces", return_value=[])
    @patch("collector.check_internet_connectivity", return_value={"reachable": False, "packet_loss": 100.0, "latency_ms": 0.0})
    @patch("collector.get_default_gateway", return_value={"gateway": "NOT DETECTED"})
    @patch("collector.check_backup_ports", return_value=[])
    def test_undetected_gateway_is_null_not_sentinel(self, *_):
        assert collector.collect_telemetry("dev-1")["gateway_ip"] is None


class TestModel:
    def test_gateway_ip_optional_for_old_agents(self):
        base = dict(device_id="d", timestamp=datetime.now(timezone.utc), latency_ms=1, packet_loss=0,
                    gateway_reachable=True, internet_reachable=True, dns_healthy=True, tcp_healthy=True,
                    interface_errors=0, interface_drops=0)
        assert Telemetry(**base).gateway_ip is None
        assert Telemetry(**base, gateway_ip="10.0.0.1").gateway_ip == "10.0.0.1"


class TestIngest:
    def test_gateway_ip_is_stored(self, client):
        from src.auth import verify_agent_token, AgentIdentity
        app.dependency_overrides[verify_agent_token] = lambda: AgentIdentity("u1", "user")
        try:
            with patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb, \
                 patch("src.alert_engine.evaluate_alerts"), \
                 patch("src.anomaly_engine.detect_anomalies", return_value=[]), \
                 patch("src.incident_engine.evaluate_and_create_incidents"):
                res = client.post("/api/agent/telemetry", json={**_row("dev-1", "192.168.1.1"), "id": None} | {"id": "x"})
                inserted = sb.return_value.table.return_value.insert.call_args[0][0]
        finally:
            app.dependency_overrides.pop(verify_agent_token, None)
        assert res.status_code == 200
        assert inserted["gateway_ip"] == "192.168.1.1"


class TestLatestEndpoint:
    def _device(self, id_):
        return Device(id=id_, name=id_, hostname=id_, platform="Linux", architecture="x86_64",
                      ip_address="10.0.0.1", agent_version="1", status="ONLINE")

    def test_guest_gets_empty(self, client):
        assert client.get("/api/telemetry/latest").json() == {}

    def test_returns_newest_row_per_owned_device(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api._fetch_devices", return_value=[self._device("a"), self._device("b")]), \
                 patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb:
                chain = sb.return_value.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute
                chain.return_value.data = [
                    _row("a", "192.168.1.1", "2026-09-19T10:05:00+00:00"),
                    _row("a", "10.9.9.9", "2026-09-19T09:00:00+00:00"),   # older, must lose
                    _row("b", "172.16.0.1", "2026-09-19T10:01:00+00:00"),
                ]
                body = client.get("/api/telemetry/latest").json()
                requested_ids = sb.return_value.table.return_value.select.return_value.in_.call_args[0][1]
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        assert set(body) == {"a", "b"}
        assert body["a"]["gateway_ip"] == "192.168.1.1"
        assert body["b"]["gateway_ip"] == "172.16.0.1"
        assert set(requested_ids) == {"a", "b"}  # scoped to the caller's own devices

    def test_user_with_no_devices_gets_empty(self, client):
        from src.auth import get_optional_user
        app.dependency_overrides[get_optional_user] = lambda: {"id": "u1"}
        try:
            with patch("src.api._fetch_devices", return_value=[]), \
                 patch("src.api.is_database_configured", return_value=True), \
                 patch("src.api.get_supabase") as sb:
                body = client.get("/api/telemetry/latest").json()
                sb.return_value.table.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_optional_user, None)
        assert body == {}
