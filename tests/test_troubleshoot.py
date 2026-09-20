"""The guided-troubleshooting analysis.

These tests exist because the panel used to be fiction: the packet loss it
displayed was the literal 0.3, the baseline was the literal 24, and the
evidence bullets were fixed strings shown whether or not anything had been
measured. So most of what is asserted here is that a number on screen traces
back to a measurement, and that the absence of measurements is stated rather
than filled in.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import troubleshoot
from src.api import app
from src.models import Device


@pytest.fixture
def client():
    return TestClient(app)


def _analysis(**overrides):
    base = dict(
        source="agent", device_name="LAPTOP-1", measured_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=30.0, packet_loss_pct=0.0, gateway_reachable=True,
        internet_reachable=True, dns_healthy=True,
        latency_history=[30.0] * 50, loss_history=[0.0] * 50,
    )
    base.update(overrides)
    return troubleshoot.build_analysis(**base)


class TestNothingIsInvented:
    def test_no_measurements_says_so_rather_than_guessing(self):
        a = troubleshoot.build_analysis(
            source="none", device_name=None, measured_at=None, latency_ms=None,
            packet_loss_pct=None, gateway_reachable=None, internet_reachable=None,
            dns_healthy=None, latency_history=[], loss_history=[],
        )
        assert a["status"] == "unknown"
        assert a["latency"]["current_ms"] is None
        assert a["packet_loss"]["current_pct"] is None
        assert a["confidence"] == "none"
        assert "Nothing measured yet" in a["likely_issue"]

    def test_packet_loss_is_the_measured_value_not_a_constant(self):
        """The old panel showed 0.3% regardless of reality."""
        for measured in (0.0, 25.0, 50.0):
            a = _analysis(packet_loss_pct=measured)
            assert a["packet_loss"]["current_pct"] == measured
        assert _analysis(packet_loss_pct=0.0)["packet_loss"]["current_pct"] != 0.3

    def test_the_baseline_is_computed_from_history_not_assumed(self):
        """The old panel always claimed a 24ms baseline."""
        a = _analysis(latency_history=[100.0] * 40, latency_ms=100.0)
        assert a["latency"]["baseline"]["average"] == 100.0
        assert a["latency"]["baseline"]["samples"] == 40

    def test_a_thin_history_refuses_to_call_itself_a_baseline(self):
        a = _analysis(latency_history=[30.0, 31.0, 29.0], latency_ms=30.0)
        assert a["baseline_ready"] is False
        assert any("not enough history" in e.lower() for e in a["evidence"])

    def test_evidence_mentions_the_actual_numbers(self):
        a = _analysis(latency_ms=42.0, latency_history=[40.0] * 60)
        joined = " ".join(a["evidence"])
        assert "42" in joined
        assert str(a["latency"]["baseline"]["samples"]) in joined


class TestPacketLossIsReportedHonestly:
    def test_one_dropped_probe_is_explained_not_alarmed_about(self):
        """4 probes means one lost packet reads as 25%. Saying '25% packet
        loss' without that context is how a healthy link looks broken."""
        a = _analysis(packet_loss_pct=25.0, loss_history=[0.0] * 40 + [25.0])
        joined = " ".join(a["evidence"])
        assert "1 of 4 probes" in joined
        assert a["status"] == "normal"          # a single blip is not degradation
        assert "has not recurred" in joined

    def test_recurring_loss_is_treated_as_degradation(self):
        a = _analysis(packet_loss_pct=25.0, loss_history=[0.0] * 30 + [25.0, 0.0, 25.0, 25.0])
        assert a["status"] == "degraded"
        assert "sustained packet loss" in a["likely_issue"]

    def test_zero_loss_states_the_sample_size(self):
        a = _analysis(packet_loss_pct=0.0)
        assert any("4 probes" in e for e in a["evidence"])


class TestFailuresDominate:
    def test_dns_failure_is_critical(self):
        a = _analysis(dns_healthy=False)
        assert a["status"] == "critical"
        assert "DNS" in a["likely_issue"]
        assert a["confidence"] == "high"

    def test_no_internet_is_critical(self):
        a = _analysis(internet_reachable=False)
        assert a["status"] == "critical"

    def test_an_icmp_silent_gateway_is_not_a_fault(self):
        """Consistent with the rest of the app: a gateway that drops ping
        while traffic flows through it is normal."""
        a = _analysis(gateway_reachable=False, internet_reachable=True)
        assert a["status"] == "normal"
        assert any("that is normal" in e for e in a["evidence"])


class TestConfidenceMeansSomething:
    def test_confidence_is_low_without_a_baseline(self):
        assert _analysis(latency_history=[30.0, 31.0])["confidence"] == "low"

    def test_confidence_rises_with_evidence(self):
        assert _analysis(latency_history=[30.0] * 30)["confidence"] == "medium"
        assert _analysis(latency_history=[30.0] * 150)["confidence"] == "high"


class TestEndpoint:
    def test_requires_a_session(self, anon_client):
        assert anon_client.get("/api/troubleshoot").status_code == 401

    def test_no_data_yet_is_a_clean_unknown(self, client):
        with patch("src.api._fetch_devices", return_value=[]):
            body = client.get("/api/troubleshoot").json()
        assert body["status"] == "unknown"
        assert body["source"]["kind"] == "none"

    def test_prefers_the_accounts_own_machine_over_the_hosted_scan(self, client):
        device = Device(id="d1", name="LAPTOP-1", hostname="LAPTOP-1", platform="Windows",
                        architecture="AMD64", ip_address="10.0.0.2", agent_version="1.1.0",
                        status="ONLINE")
        row = {
            "device_id": "d1", "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_ms": 31.0, "packet_loss": 0.0, "gateway_reachable": True,
            "internet_reachable": True, "dns_healthy": True,
        }
        with patch("src.api._fetch_devices", return_value=[device]), \
             patch("src.api._latest_telemetry_for", return_value={"d1": row}), \
             patch("src.api._troubleshoot_series", return_value=([31.0] * 40, [0.0] * 40)):
            body = client.get("/api/troubleshoot").json()
        assert body["source"]["kind"] == "agent"
        assert body["source"]["device"] == "LAPTOP-1"
        assert body["latency"]["current_ms"] == 31.0
        assert body["baseline_ready"] is True

    def test_the_hosted_scan_fallback_admits_whose_network_it_measured(self, client):
        client.post("/api/diagnostic-run?demo=healthy")
        with patch("src.api._fetch_devices", return_value=[]):
            body = client.get("/api/troubleshoot").json()
        assert body["source"]["kind"] == "hosted-scan"
        assert body["latency"]["current_ms"] is not None
