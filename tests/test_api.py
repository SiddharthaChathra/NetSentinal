"""Tests for the FastAPI REST API endpoints."""
import pytest
from fastapi.testclient import TestClient
from src.api import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_running(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "database" in data
        assert "version" in data


class TestDiagnosticRunEndpoint:
    def test_post_demo_healthy(self, client):
        """POST with demo=healthy should return a full DiagnosticResult."""
        response = client.post("/api/diagnostic-run?demo=healthy")
        assert response.status_code == 200
        data = response.json()
        assert "health_score" in data
        assert "status" in data
        assert "diagnostics" in data
        assert "system" in data
        assert "gateway" in data
        assert data["is_demo"] is True

    def test_post_demo_dns_failure(self, client):
        response = client.post("/api/diagnostic-run?demo=dns-failure")
        assert response.status_code == 200
        data = response.json()
        assert data["is_demo"] is True
        # DNS should be failed
        dns_pass = any(d["success"] for d in data["dns"])
        assert dns_pass is False

    def test_post_demo_gateway_failure(self, client):
        response = client.post("/api/diagnostic-run?demo=gateway-failure")
        data = response.json()
        assert data["gateway"]["reachable"] is False
        assert data["health_score"] < 50

    def test_get_last_run_after_post(self, client):
        """GET /api/diagnostic-run should return the cached last run."""
        client.post("/api/diagnostic-run?demo=healthy")
        response = client.get("/api/diagnostic-run")
        assert response.status_code == 200
        data = response.json()
        assert data is not None
        assert data["is_demo"] is True


class TestComponentEndpoints:
    @pytest.fixture(autouse=True)
    def seed_data(self, client):
        """Ensure a diagnostic run exists before testing component endpoints."""
        client.post("/api/diagnostic-run?demo=healthy")

    def test_get_system(self, client):
        res = client.get("/api/system")
        assert res.status_code == 200
        data = res.json()
        assert "hostname" in data

    def test_get_interfaces(self, client):
        res = client.get("/api/interfaces")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_gateway(self, client):
        res = client.get("/api/gateway")
        assert res.status_code == 200
        data = res.json()
        assert "reachable" in data

    def test_get_internet(self, client):
        res = client.get("/api/internet")
        assert res.status_code == 200
        data = res.json()
        assert "reachable" in data

    def test_get_dns(self, client):
        res = client.get("/api/dns")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_tcp(self, client):
        res = client.get("/api/tcp")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_routes(self, client):
        res = client.get("/api/routes")
        assert res.status_code == 200

    def test_get_diagnostics(self, client):
        res = client.get("/api/diagnostics")
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestExportEndpoint:
    def test_export_returns_last_run(self, client):
        client.post("/api/diagnostic-run?demo=healthy")
        res = client.get("/api/export")
        assert res.status_code == 200
        data = res.json()
        assert data["is_demo"] is True


class TestHistoryEndpoint:
    def test_history_returns_list(self, client):
        res = client.get("/api/history")
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestFrontendServing:
    def test_root_returns_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
