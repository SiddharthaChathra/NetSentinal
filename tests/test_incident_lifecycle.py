"""Incidents have to be able to close.

The engine could only ever open them. A two-second blip left an OPEN incident
forever, and because new incidents are de-duplicated against open ones with
the same title, that stale row then suppressed the next genuine occurrence of
the same problem. These tests cover the full open -> clear -> reopen cycle.
"""
from unittest.mock import patch

import pytest

from src.anomaly_engine import ANOMALY_TITLES, CLEAR_CONSECUTIVE_SAMPLES, evaluate_device_state
from src.incident_engine import resolve_cleared_incidents

HEALTHY = {"latency_ms": 30.0, "packet_loss": 0.0, "dns_healthy": True, "gateway_reachable": True}
FLAT_BASELINE = {"average": 0.0, "median": 0.0, "p95": 0.0, "stddev": 0.0}


def _state(telemetry, recent_losses=None, baseline=None):
    with patch("src.anomaly_engine.get_baseline", return_value=baseline or FLAT_BASELINE):
        return evaluate_device_state("d1", telemetry, recent_losses=recent_losses or [])


class TestClearingIsDetected:
    def test_a_healthy_report_clears_dns_and_gateway(self):
        cleared = _state(HEALTHY, recent_losses=[0.0] * 5)["cleared"]
        assert ANOMALY_TITLES["dns"] in cleared
        assert ANOMALY_TITLES["gateway"] in cleared

    def test_loss_clears_only_after_several_quiet_reports(self):
        # One quiet report is not enough — loss is intermittent by nature.
        assert ANOMALY_TITLES["packet_loss"] not in _state(HEALTHY, recent_losses=[0.0])["cleared"]
        quiet = [0.0] * CLEAR_CONSECUTIVE_SAMPLES
        assert ANOMALY_TITLES["packet_loss"] in _state(HEALTHY, recent_losses=quiet)["cleared"]

    def test_loss_still_happening_is_not_cleared(self):
        cleared = _state({**HEALTHY, "packet_loss": 25.0},
                         recent_losses=[25.0, 0.0, 25.0])["cleared"]
        assert ANOMALY_TITLES["packet_loss"] not in cleared

    def test_a_failure_is_never_reported_as_both_raised_and_cleared(self):
        state = _state({**HEALTHY, "dns_healthy": False}, recent_losses=[0.0] * 5)
        raised = {a["title"] for a in state["anomalies"]}
        assert ANOMALY_TITLES["dns"] in raised
        assert not (raised & state["cleared"])

    def test_an_ongoing_outage_stays_open(self):
        state = _state({**HEALTHY, "gateway_reachable": False}, recent_losses=[0.0] * 5)
        assert ANOMALY_TITLES["gateway"] not in state["cleared"]


class TestResolutionWritesThrough:
    def _store(self, open_rows):
        updates = []

        class _Q:
            def __init__(self): self._id = None
            def select(self, *a): return self
            def eq(self, col, val):
                if col == "id":
                    self._id = val
                return self
            def update(self, payload):
                self._payload = payload
                return self
            def execute(self):
                if getattr(self, "_payload", None) is not None:
                    updates.append((self._id, self._payload))
                    return type("R", (), {"data": [{"id": self._id}]})()
                return type("R", (), {"data": open_rows})()

        sb = type("SB", (), {"table": staticmethod(lambda n: _Q())})()
        return sb, updates

    def test_a_cleared_incident_is_resolved(self):
        sb, updates = self._store([{"id": "inc-1", "title": ANOMALY_TITLES["packet_loss"]}])
        with patch("src.incident_engine.is_database_configured", return_value=True), \
             patch("src.incident_engine.get_supabase", return_value=sb):
            closed = resolve_cleared_incidents("d1", {ANOMALY_TITLES["packet_loss"]}, user_id="u1")
        assert closed == 1
        assert updates[0][0] == "inc-1"
        assert updates[0][1]["status"] == "RESOLVED"
        assert updates[0][1]["resolved_at"]

    def test_an_unrelated_open_incident_is_left_alone(self):
        sb, updates = self._store([
            {"id": "inc-1", "title": ANOMALY_TITLES["packet_loss"]},
            {"id": "inc-2", "title": ANOMALY_TITLES["dns"]},
        ])
        with patch("src.incident_engine.is_database_configured", return_value=True), \
             patch("src.incident_engine.get_supabase", return_value=sb):
            resolve_cleared_incidents("d1", {ANOMALY_TITLES["packet_loss"]})
        assert [u[0] for u in updates] == ["inc-1"]

    def test_incidents_this_engine_does_not_own_are_never_touched(self):
        """A finding raised from a diagnostic, or by hand, is not ours to close."""
        sb, updates = self._store([{"id": "inc-9", "title": "Someone's manual incident"}])
        with patch("src.incident_engine.is_database_configured", return_value=True), \
             patch("src.incident_engine.get_supabase", return_value=sb):
            resolve_cleared_incidents("d1", set(ANOMALY_TITLES.values()))
        assert updates == []

    def test_nothing_to_clear_costs_no_queries(self):
        with patch("src.incident_engine.get_supabase") as sb:
            assert resolve_cleared_incidents("d1", set()) == 0
        sb.assert_not_called()

    def test_a_database_failure_is_survivable(self):
        with patch("src.incident_engine.is_database_configured", return_value=True), \
             patch("src.incident_engine.get_supabase", side_effect=RuntimeError("down")):
            assert resolve_cleared_incidents("d1", {ANOMALY_TITLES["dns"]}) == 0


class TestTheRatchetIsGone:
    def test_open_clear_reopen(self):
        """The cycle that was impossible before: a problem occurs, ends, and
        can be reported again — rather than the first one blocking the rest."""
        # 1. sustained loss -> raised
        first = _state({**HEALTHY, "packet_loss": 25.0}, recent_losses=[25.0, 25.0, 0.0])
        assert {a["title"] for a in first["anomalies"]} == {ANOMALY_TITLES["packet_loss"]}

        # 2. quiet for several reports -> cleared, so the open row gets closed
        second = _state(HEALTHY, recent_losses=[0.0] * CLEAR_CONSECUTIVE_SAMPLES)
        assert ANOMALY_TITLES["packet_loss"] in second["cleared"]

        # 3. it happens again -> raised again, not swallowed by a stale row
        third = _state({**HEALTHY, "packet_loss": 50.0}, recent_losses=[50.0, 25.0, 0.0])
        assert {a["title"] for a in third["anomalies"]} == {ANOMALY_TITLES["packet_loss"]}
