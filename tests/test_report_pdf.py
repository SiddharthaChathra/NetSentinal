"""GET /api/report.pdf renders a valid PDF from the same document as /api/report."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app, _last_runs
from src.models import Device
from src.report_pdf import build_pdf_report


@pytest.fixture
def client():
    _last_runs.clear()
    return TestClient(app)


def _pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    import re
    text = " ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    return re.sub(r"\s+", " ", text)


class TestBuilder:
    def test_empty_report_renders(self):
        pdf = build_pdf_report({"title": "T", "report_version": "2", "generated_at": datetime.now(timezone.utc).isoformat(),
                                "account": {"signed_in": False}, "summary": {}, "hosted_scan": None, "devices": [], "backup_readiness": None})
        assert pdf.startswith(b"%PDF-")
        assert b"%%EOF" in pdf[-64:]

    def test_full_report_has_every_section(self):
        now = datetime.now(timezone.utc)
        tel = [{"device_id": "d1", "timestamp": (now - timedelta(minutes=m)).isoformat(), "latency_ms": 20 + m % 7, "packet_loss": 0}
               for m in range(120, 0, -5)]
        report = {
            "title": "NetSentinel Diagnostic & Observability Report", "report_version": "2",
            "generated_at": now.isoformat(), "account": {"signed_in": True},
            "parameters": {"dataset_size_gb": 100, "sla_window_hours": 4},
            "summary": {"hosted_scan_status": "HEALTHY", "hosted_scan_health_score": 97, "devices": 1, "devices_reporting": 1,
                        "backup_targets": 1, "backup_readiness_score": 100, "open_warnings": 0},
            "hosted_scan": {"note": "server note", "run_at": now.isoformat(), "is_demo": False, "server_hostname": "srv", "server_ip": "10.0.0.1",
                            "health_score": 97, "status": "HEALTHY",
                            "metrics": {"latency_ms": 7.3, "packet_loss_pct": 0, "gateway": "PASS (ICMP filtered)", "internet": "PASS", "dns": "PASS", "tcp": "PASS"},
                            "diagnostics": [{"severity": "info", "title": "Gateway does not answer ICMP", "likely_cause": "x", "evidence": ["e1"], "recommended_checks": ["r1"], "confidence": "high"}],
                            "duration_ms": 8000},
            "devices": [{"id": "d1", "name": "LAPTOP", "hostname": "LAPTOP", "platform": "Windows", "ip_address": "192.168.1.5", "agent_version": "1.0.0",
                         "is_backup_target": True, "backup_protocols": ["SMB"], "last_seen": now.isoformat(),
                         "latest_telemetry": {"reported_at": now.isoformat(), "age_seconds": 20, "stale": False, "gateway_ip": "192.168.1.1",
                                              "gateway_reachable": True, "internet_reachable": True, "dns_healthy": True, "latency_ms": 24.4567,
                                              "packet_loss_pct": 0, "backup_ports": [{"port": 445, "service": "SMB", "open": True}, {"port": 2049, "service": "NFS", "open": False}]}}],
            "backup_readiness": {"healthScore": 97, "backupReadinessScore": 100,
                                 "targets": [{"id": "d1", "name": "LAPTOP", "isBackupTarget": True, "reachability": "up", "dnsResolved": True, "latencyMs": 24.4567,
                                              "packetLossPct": 0, "ports": [{"port": 445, "service": "SMB", "open": True}],
                                              "backupReadiness": {"score": 100, "verdict": "ready", "slaWindowHours": 4, "estimatedTransferHours": 0.23, "willMeetSla": True}}],
                                 "diagnostics": [], "simulatedScenarios": []},
        }
        history = [{"timestamp": (now - timedelta(hours=h)).isoformat(), "score": 95, "latency": 8.0, "packet_loss": 0} for h in range(24, 0, -1)]
        pdf = build_pdf_report(report, history=history, telemetry_history={"d1": tel})
        assert pdf.startswith(b"%PDF-")
        text = _pdf_text(pdf)
        for heading in ("Executive Summary", "Hosted Server Scan", "Layer-by-layer results", "Managed Devices", "Trends",
                        "Hosted scan history", "Agent telemetry", "Backup Readiness", "how it was computed", "Methodology", "Data provenance"):
            assert heading in text, heading
        assert "24.5 ms" in text            # rounded, not 24.4567
        assert "PASS (ICMP filtered)" in text


class TestEndpoint:
    def test_guest_gets_a_pdf(self, client):
        res = client.get("/api/report.pdf")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert 'filename="netsentinel-report-' in res.headers["content-disposition"]
        assert res.content.startswith(b"%PDF-")

    def test_signed_in_includes_history_and_telemetry(self, client):
        from src.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
        dev = Device(id="d1", name="LAPTOP", hostname="LAPTOP", platform="Windows", architecture="x", ip_address="1.2.3.4",
                     agent_version="1", status="ONLINE", is_backup_target=True)
        try:
            with patch("src.api._fetch_devices", return_value=[dev]), \
                 patch("src.api._latest_telemetry_for", return_value={}), \
                 patch("src.api.get_history_supabase", return_value=[]) as gh, \
                 patch("src.api._telemetry_history_for", return_value={}) as th:
                res = client.get("/api/report.pdf")
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert res.status_code == 200 and res.content.startswith(b"%PDF-")
        gh.assert_called_once()
        th.assert_called_once_with(["d1"])

    def test_render_failure_is_500_with_message(self, client):
        with patch("src.report_pdf.build_pdf_report", side_effect=RuntimeError("boom")):
            res = client.get("/api/report.pdf")
        assert res.status_code == 500
        assert "PDF" in res.json()["detail"]
