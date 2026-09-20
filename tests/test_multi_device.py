"""Several devices on one account.

Everything here is about the difference between one device and many: a single
device hides every one of these bugs, because one device can never be crowded
out of a shared query window by another.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import api
from src.api import app, _latest_telemetry_for, _telemetry_history_for
from src.models import Device
from tests.conftest import TEST_USER_ID


@pytest.fixture
def client():
    return TestClient(app)


def _device(idx, backup=True):
    return Device(id=f"dev-{idx}", name=f"LAPTOP-{idx}", hostname=f"LAPTOP-{idx}", platform="Windows",
                  architecture="AMD64", ip_address=f"192.168.31.{idx}", agent_version="1.0.0",
                  status="ONLINE", is_backup_target=backup, backup_protocols=["SMB"])


def _row(device_id, minutes_ago):
    return {
        "device_id": device_id,
        "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
        "latency_ms": 25.0, "packet_loss": 0.0, "gateway_reachable": True,
        "internet_reachable": True, "dns_healthy": True, "tcp_healthy": True,
        "interface_errors": 0, "interface_drops": 0, "gateway_ip": "192.168.31.1",
        "backup_ports": [{"port": 445, "service": "SMB", "open": True}],
    }


class _FakeTelemetryTable:
    """Stands in for the telemetry table, honouring in_/eq/order/limit the way
    PostgREST does — which is the whole point: the old code was correct
    against a mock that ignored the limit, and wrong against a real database.
    """

    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls
        self._ids = None
        self._since = None
        self._desc = True
        self._limit = None

    def select(self, *a):
        return self

    def in_(self, col, values):
        self._ids = list(values)
        return self

    def eq(self, col, value):
        self._ids = [value]
        return self

    def gte(self, col, value):
        self._since = value
        return self

    def order(self, col, desc=False):
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = [r for r in self._rows if self._ids is None or r["device_id"] in self._ids]
        if self._since:
            rows = [r for r in rows if r["timestamp"] >= self._since]
        rows.sort(key=lambda r: r["timestamp"], reverse=self._desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._calls.append({"ids": self._ids, "limit": self._limit, "returned": len(rows)})
        return type("R", (), {"data": rows})()


def _supabase(rows):
    calls = []
    client = type("SB", (), {"table": staticmethod(lambda n: _FakeTelemetryTable(rows, calls))})()
    return client, calls


class TestLatestTelemetryAcrossDevices:
    def test_a_quiet_device_is_not_crowded_out_by_a_busy_one(self):
        """The bug this guards: one bulk query takes the newest 20*N rows
        across all devices, so a device whose agent stopped an hour ago has
        every row pushed out by its livelier siblings, and then reads as
        'never had an agent' instead of 'agent is down'."""
        busy = [_row("dev-1", m) for m in range(0, 120)]      # reporting every minute
        quiet = [_row("dev-2", 300)]                          # last seen 5 hours ago
        sb, calls = _supabase(busy + quiet)

        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            latest = _latest_telemetry_for(["dev-1", "dev-2"])

        assert set(latest) == {"dev-1", "dev-2"}
        assert latest["dev-2"]["timestamp"] == quiet[0]["timestamp"]
        # One bulk query, then exactly one targeted lookup for the device the
        # window missed - not a query per device.
        assert len(calls) == 2
        assert calls[1]["ids"] == ["dev-2"] and calls[1]["limit"] == 1

    def test_all_devices_live_costs_a_single_query(self):
        rows = [_row(f"dev-{i}", m) for i in (1, 2, 3) for m in range(0, 5)]
        sb, calls = _supabase(rows)
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            latest = _latest_telemetry_for(["dev-1", "dev-2", "dev-3"])
        assert set(latest) == {"dev-1", "dev-2", "dev-3"}
        assert len(calls) == 1

    def test_a_device_with_no_telemetry_at_all_stays_absent(self):
        """Absent means 'never had an agent', which is what makes the backup
        checker probe it from the server instead of reading its telemetry."""
        sb, calls = _supabase([_row("dev-1", 1)])
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            latest = _latest_telemetry_for(["dev-1", "dev-never"])
        assert set(latest) == {"dev-1"}

    def test_the_backfill_is_bounded(self):
        ids = [f"dev-{i}" for i in range(60)]
        sb, calls = _supabase([])
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            _latest_telemetry_for(ids)
        assert len(calls) == 1 + api._MAX_TELEMETRY_BACKFILL


class TestHistoryAcrossDevices:
    def test_two_devices_keep_their_most_recent_data(self):
        """The old fixed 2000-row, oldest-first limit meant two devices at
        60s intervals silently lost the newest third of a 24h window, so the
        graphs showed stale data."""
        rows = [_row(f"dev-{i}", m) for i in (1, 2) for m in range(0, 1440)]
        sb, calls = _supabase(rows)
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            hist = _telemetry_history_for(["dev-1", "dev-2"])

        assert set(hist) == {"dev-1", "dev-2"}
        for device_id in ("dev-1", "dev-2"):
            series = hist[device_id]
            # Oldest-first, as the graph code expects.
            assert series == sorted(series, key=lambda r: r["timestamp"])
            # And the newest sample is present, not truncated away.
            newest = max(r["timestamp"] for r in rows if r["device_id"] == device_id)
            assert series[-1]["timestamp"] == newest

    def test_the_row_budget_scales_with_the_fleet_but_is_capped(self):
        sb, calls = _supabase([])
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            _telemetry_history_for(["a", "b", "c"])
        assert calls[0]["limit"] == api._HISTORY_ROWS_PER_DEVICE * 3

        sb, calls = _supabase([])
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            _telemetry_history_for([f"d{i}" for i in range(100)])
        assert calls[0]["limit"] == api._HISTORY_ROWS_MAX


class TestEndToEndWithSeveralDevices:
    def test_devices_endpoint_returns_the_whole_fleet(self, client):
        fleet = [_device(i) for i in (1, 2, 3)]
        with patch("src.api._fetch_devices", return_value=fleet):
            body = client.get("/api/devices").json()
        assert [d["hostname"] for d in body] == ["LAPTOP-1", "LAPTOP-2", "LAPTOP-3"]

    def test_backup_readiness_scores_every_tagged_device(self, client):
        fleet = [_device(i) for i in (1, 2, 3)]
        telemetry = {f"dev-{i}": _row(f"dev-{i}", 0) for i in (1, 2, 3)}
        with patch("src.api._fetch_devices", return_value=fleet), \
             patch("src.api._latest_telemetry_for", return_value=telemetry):
            body = client.get("/api/backup/readiness").json()
        assert len(body["targets"]) == 3
        assert {t["name"] for t in body["targets"]} == {"LAPTOP-1", "LAPTOP-2", "LAPTOP-3"}
        assert all(t["reachability"] == "up" for t in body["targets"])

    def test_one_offline_device_is_reported_down_not_missing(self, client):
        fleet = [_device(1), _device(2)]
        telemetry = {"dev-1": _row("dev-1", 0), "dev-2": _row("dev-2", 300)}  # dev-2 stale
        with patch("src.api._fetch_devices", return_value=fleet), \
             patch("src.api._latest_telemetry_for", return_value=telemetry):
            body = client.get("/api/backup/readiness").json()
        by_name = {t["name"]: t for t in body["targets"]}
        assert by_name["LAPTOP-1"]["reachability"] == "up"
        assert by_name["LAPTOP-2"]["reachability"] == "down"

    def test_the_report_covers_every_device(self, client):
        fleet = [_device(i) for i in (1, 2)]
        telemetry = {f"dev-{i}": _row(f"dev-{i}", 0) for i in (1, 2)}
        with patch("src.api._fetch_devices", return_value=fleet), \
             patch("src.api._latest_telemetry_for", return_value=telemetry):
            body = client.get("/api/report").json()
        assert body["summary"]["devices"] == 2
        assert body["summary"]["devices_reporting"] == 2
        assert body["summary"]["backup_targets"] == 2

    def test_the_whole_fleet_shares_one_agent_token(self, client):
        """Each machine uses the same per-account token; identity comes from
        the hostname it registers, not from a per-device credential."""
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_or_create_agent_token", return_value="nsa_one_token") as gen:
            first = client.get("/api/agent-token").json()
            second = client.get("/api/agent-token").json()
        assert first["token"] == second["token"] == "nsa_one_token"
        assert gen.call_args_list[0].args == (TEST_USER_ID,)


class TestDerivedOnlineStatus:
    """Nothing ever wrote OFFLINE, so a stopped agent read as ONLINE forever.
    With a fleet that makes the dashboard's online/offline counter useless."""

    def _fetch(self, rows):
        class _Q:
            def select(self, *a): return self
            def eq(self, *a): return self
            def execute(self): return type("R", (), {"data": rows})()

        sb = type("SB", (), {"table": staticmethod(lambda n: _Q())})()
        with patch("src.api.is_database_configured", return_value=True), \
             patch("src.api.get_supabase", return_value=sb):
            return api._fetch_devices({"id": TEST_USER_ID})

    @staticmethod
    def _stored(idx, seconds_ago, status="ONLINE"):
        seen = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        return {
            "id": f"dev-{idx}", "name": f"LAPTOP-{idx}", "hostname": f"LAPTOP-{idx}",
            "platform": "Windows", "architecture": "AMD64", "ip_address": "10.0.0.1",
            "agent_version": "1.0.0", "status": status, "last_seen": seen.isoformat(),
        }

    def test_a_stopped_agent_reads_offline_even_though_the_row_says_online(self):
        devices = self._fetch([self._stored(1, seconds_ago=7200)])
        assert devices[0].status == "OFFLINE"

    def test_a_reporting_agent_reads_online(self):
        devices = self._fetch([self._stored(1, seconds_ago=30)])
        assert devices[0].status == "ONLINE"

    def test_a_mixed_fleet_is_counted_correctly(self):
        devices = self._fetch([
            self._stored(1, seconds_ago=10),      # live
            self._stored(2, seconds_ago=3600),    # agent stopped an hour ago
            self._stored(3, seconds_ago=45),      # live
        ])
        assert [d.status for d in devices] == ["ONLINE", "OFFLINE", "ONLINE"]
        assert sum(d.status == "ONLINE" for d in devices) == 2

    def test_a_device_that_never_reported_is_offline(self):
        row = self._stored(1, seconds_ago=0)
        row["last_seen"] = None
        assert self._fetch([row])[0].status == "OFFLINE"


class TestAnomalyDetectionIsNotNoise:
    """A ping run is 4 packets, so one dropped packet is 25% loss. That was
    raising a WARNING incident off a single sample."""

    def _detect(self, loss, recent, baseline_avg=0.0):
        from src.anomaly_engine import detect_anomalies
        telemetry = {"latency_ms": 30.0, "packet_loss": loss, "dns_healthy": True,
                     "gateway_reachable": True}
        with patch("src.anomaly_engine.get_baseline",
                   return_value={"average": baseline_avg, "median": 0.0, "p95": 0.0, "stddev": 0.0}):
            return detect_anomalies("d1", telemetry, recent_losses=recent)

    def test_a_single_dropped_probe_is_not_an_incident(self):
        # newest first: this sample, then a clean history
        assert self._detect(25.0, [25.0, 0.0, 0.0, 0.0, 0.0]) == []

    def test_loss_that_recurs_is_an_incident(self):
        found = self._detect(25.0, [25.0, 0.0, 25.0, 0.0, 25.0])
        assert len(found) == 1
        assert found[0]["metric"] == "packet_loss"

    def test_the_incident_says_how_many_probes_were_lost(self):
        found = self._detect(25.0, [25.0, 25.0])
        assert "1 of 4 probes" in found[0]["reason"]

    def test_no_history_means_no_packet_loss_incident(self):
        """First ever report cannot establish that loss recurred."""
        assert self._detect(25.0, [25.0]) == []

    def test_real_failures_still_fire_immediately(self):
        """Sustained-loss gating must not delay a DNS or gateway outage."""
        from src.anomaly_engine import detect_anomalies
        with patch("src.anomaly_engine.get_baseline",
                   return_value={"average": 0.0, "median": 0.0, "p95": 0.0, "stddev": 0.0}):
            found = detect_anomalies("d1", {
                "latency_ms": 30.0, "packet_loss": 0.0,
                "dns_healthy": False, "gateway_reachable": False,
            }, recent_losses=[])
        titles = {f["title"] for f in found}
        assert titles == {"DNS Failure Detected", "Gateway Unreachable"}
        assert all(f["severity"] == "critical" for f in found)


class TestBaselinesAreActuallyComputed:
    """metric_baselines was never written to: nothing called
    update_device_baselines, so get_baseline always returned zeros and the
    latency-anomaly branch (guarded by average > 0) could never fire."""

    def test_telemetry_ingest_updates_the_baseline(self):
        from src import baseline_engine
        baseline_engine._last_baseline_update.clear()
        with patch("src.baseline_engine.update_device_baselines") as update:
            assert baseline_engine.maybe_update_baselines("d1", user_id="u1") is True
        update.assert_called_once_with("d1", user_id="u1")

    def test_it_is_throttled_rather_than_run_every_report(self):
        from src import baseline_engine
        baseline_engine._last_baseline_update.clear()
        with patch("src.baseline_engine.update_device_baselines") as update:
            baseline_engine.maybe_update_baselines("d1")
            baseline_engine.maybe_update_baselines("d1")
            baseline_engine.maybe_update_baselines("d1")
        assert update.call_count == 1

    def test_a_failing_baseline_update_never_fails_the_telemetry_post(self):
        from src import baseline_engine
        baseline_engine._last_baseline_update.clear()
        with patch("src.baseline_engine.update_device_baselines", side_effect=RuntimeError("db down")):
            assert baseline_engine.maybe_update_baselines("d1") is False

    def test_the_computation_is_real_statistics(self):
        from src.baseline_engine import calculate_statistical_baseline
        stats = calculate_statistical_baseline([10.0, 20.0, 30.0, 40.0])
        assert stats["average"] == 25.0
        assert stats["median"] == 25.0
        assert stats["stddev"] > 0


class TestBaselineUpsertShape:
    """Production answered 42P10 — "no unique or exclusion constraint matching
    the ON CONFLICT specification" — on every attempt. metric_baselines has
    `id TEXT PRIMARY KEY` and no unique constraint on (device_id, metric), so
    the conflict target did not exist; and with no id supplied the insert
    would then have failed on a null primary key."""

    def _run(self, user_id=None):
        captured = {}

        class _Q:
            def __init__(self, name): self.name = name
            def select(self, *a): return self
            def eq(self, *a): return self
            def order(self, *a, **k): return self
            def limit(self, n): return self
            def upsert(self, payload, **kwargs):
                captured["payload"] = payload
                captured["kwargs"] = kwargs
                return self
            def execute(self):
                if self.name == "telemetry":
                    return type("R", (), {"data": [
                        {"latency_ms": 10.0 + i, "packet_loss": 0.0} for i in range(30)
                    ]})()
                return type("R", (), {"data": []})()

        sb = type("SB", (), {"table": staticmethod(lambda n: _Q(n))})()
        with patch("src.baseline_engine.is_database_configured", return_value=True), \
             patch("src.baseline_engine.get_supabase", return_value=sb):
            from src.baseline_engine import update_device_baselines
            update_device_baselines("dev-1", user_id=user_id)
        return captured

    def test_no_on_conflict_target_is_passed(self):
        """The primary key is the conflict target now, so PostgREST must not
        be told to use a constraint that does not exist."""
        assert self._run()["kwargs"] == {}

    def test_every_row_carries_a_primary_key(self):
        rows = self._run()["payload"]
        assert all(r.get("id") for r in rows)

    def test_the_key_is_stable_for_a_device_and_metric(self):
        from src.baseline_engine import baseline_row_id
        assert baseline_row_id("dev-1", "latency") == baseline_row_id("dev-1", "latency")
        assert baseline_row_id("dev-1", "latency") != baseline_row_id("dev-1", "packet_loss")
        assert baseline_row_id("dev-1", "latency") != baseline_row_id("dev-2", "latency")

    def test_both_metrics_are_written(self):
        rows = self._run()["payload"]
        assert {r["metric"] for r in rows} == {"latency", "packet_loss"}

    def test_the_owner_is_recorded_so_rls_can_see_the_row(self):
        rows = self._run(user_id="user-7")["payload"]
        assert all(r["user_id"] == "user-7" for r in rows)

    def test_statistics_come_from_the_telemetry(self):
        rows = self._run()["payload"]
        latency = next(r for r in rows if r["metric"] == "latency")
        assert latency["average"] == pytest.approx(24.5)   # mean of 10..39
        assert latency["p95"] == pytest.approx(38.0)
        loss = next(r for r in rows if r["metric"] == "packet_loss")
        assert loss["average"] == 0.0
