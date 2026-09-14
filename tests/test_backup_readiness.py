"""Unit tests for the Backup Readiness module.

Covers the pure logic (SLA estimation, scoring, correlation rules,
simulated scenarios) with no network I/O, plus check_backup_target() with
its network primitives mocked out. The four "edge case" states the spec
calls out — L3 down / DNS-only failure / port-only failure / pure
throughput issue — are each asserted to produce a *distinct* diagnostic.
"""
import unittest
from unittest.mock import patch

from src.backup_readiness import (
    estimate_sla,
    score_backup_target,
    diagnose_backup_target,
    check_backup_target,
    build_backup_readiness_report,
    run_simulated_backup_scenario,
    simulate_backup_target,
    SLA_ESTIMATE_CAP_HOURS,
    READY_THRESHOLD,
    AT_RISK_THRESHOLD,
)
from src.port_checker import (
    get_backup_port_map,
    check_backup_ports,
    DEFAULT_REPLICATION_PORT,
)
from src.models import Device


def _ports(**closed):
    """Builds a full backup-port list, closing the named services (e.g. NFS=True)."""
    return [
        {"port": p, "service": s, "open": not closed.get(s, False)}
        for p, s in get_backup_port_map().items()
    ]


def _sla(hours, window=4.0):
    return {"estimated_transfer_hours": hours, "will_meet_sla": hours <= window, "sla_window_hours": window}


def _device(**overrides):
    base = dict(
        id="dev-1", name="backup01", hostname="backup01.example.com",
        platform="Linux", architecture="x86_64", ip_address="10.0.0.5",
        agent_version="1.0", status="ONLINE", is_backup_target=True,
    )
    base.update(overrides)
    return Device(**base)


def _ping(reachable=True, loss=0.0, latency=20.0):
    return {
        "target": "10.0.0.5", "reachable": reachable, "packet_loss": loss,
        "latency_ms": latency, "min_latency_ms": latency, "max_latency_ms": latency,
    }


# --- Port checker extension ------------------------------------------------

class TestBackupPorts(unittest.TestCase):

    def test_backup_port_map_includes_nfs_smb_iscsi_and_replication(self):
        port_map = get_backup_port_map()
        self.assertEqual(port_map[2049], "NFS")
        self.assertEqual(port_map[445], "SMB")
        self.assertEqual(port_map[3260], "iSCSI")
        self.assertEqual(port_map[DEFAULT_REPLICATION_PORT], "Replication")

    def test_replication_port_is_configurable(self):
        port_map = get_backup_port_map(replication_port=9999)
        self.assertEqual(port_map[9999], "Replication")
        self.assertNotIn(DEFAULT_REPLICATION_PORT, port_map)

    @patch("src.port_checker.check_ports")
    def test_check_backup_ports_reuses_existing_scanner(self, mock_check_ports):
        """Must delegate to check_ports() rather than opening its own sockets."""
        mock_check_ports.return_value = [
            {"host": "h", "port": 2049, "success": True, "response_ms": 1.0},
            {"host": "h", "port": 445, "success": False, "response_ms": 2.0},
            {"host": "h", "port": 3260, "success": True, "response_ms": 1.0},
            {"host": "h", "port": DEFAULT_REPLICATION_PORT, "success": True, "response_ms": 1.0},
        ]
        results = check_backup_ports("h")

        mock_check_ports.assert_called_once()
        called_host, called_ports = mock_check_ports.call_args[0]
        self.assertEqual(called_host, "h")
        self.assertEqual(set(called_ports), {2049, 445, 3260, DEFAULT_REPLICATION_PORT})

        by_service = {r["service"]: r for r in results}
        self.assertTrue(by_service["NFS"]["open"])
        self.assertFalse(by_service["SMB"]["open"])
        self.assertEqual(by_service["SMB"]["port"], 445)


# --- SLA estimation ---------------------------------------------------------

class TestEstimateSla(unittest.TestCase):

    def test_zero_dataset_transfers_instantly(self):
        result = estimate_sla(latency_ms=20, packet_loss_pct=0, dataset_size_gb=0, sla_window_hours=4)
        self.assertEqual(result["estimated_transfer_hours"], 0.0)
        self.assertTrue(result["will_meet_sla"])

    def test_healthy_link_meets_sla(self):
        result = estimate_sla(latency_ms=20, packet_loss_pct=0, dataset_size_gb=10, sla_window_hours=4)
        self.assertTrue(result["will_meet_sla"])
        self.assertLess(result["estimated_transfer_hours"], 4)
        # 20ms + 0% loss = no penalty, so effective throughput == nominal link
        self.assertEqual(result["effective_mbps"], 1000.0)

    def test_degraded_link_misses_sla(self):
        result = estimate_sla(latency_ms=250, packet_loss_pct=10, dataset_size_gb=500, sla_window_hours=4)
        self.assertFalse(result["will_meet_sla"])
        self.assertGreater(result["estimated_transfer_hours"], 4)
        # Throughput must have been penalised well below the nominal link
        self.assertLess(result["effective_mbps"], 100)

    def test_latency_penalty_is_monotonic(self):
        low = estimate_sla(20, 0, 100, 4)["estimated_transfer_hours"]
        mid = estimate_sla(150, 0, 100, 4)["estimated_transfer_hours"]
        high = estimate_sla(400, 0, 100, 4)["estimated_transfer_hours"]
        self.assertLess(low, mid)
        self.assertLess(mid, high)

    def test_packet_loss_penalty_is_monotonic(self):
        clean = estimate_sla(20, 0, 100, 4)["estimated_transfer_hours"]
        lossy = estimate_sla(20, 5, 100, 4)["estimated_transfer_hours"]
        very_lossy = estimate_sla(20, 30, 100, 4)["estimated_transfer_hours"]
        self.assertLess(clean, lossy)
        self.assertLess(lossy, very_lossy)

    def test_estimate_is_capped_and_json_safe(self):
        """A dead link must yield a large-but-finite number, never inf."""
        result = estimate_sla(latency_ms=1000, packet_loss_pct=100, dataset_size_gb=1_000_000, sla_window_hours=1)
        self.assertEqual(result["estimated_transfer_hours"], SLA_ESTIMATE_CAP_HOURS)
        self.assertFalse(result["will_meet_sla"])

    def test_nominal_link_override_changes_estimate(self):
        slow_link = estimate_sla(20, 0, 100, 4, nominal_link_mbps=100)
        fast_link = estimate_sla(20, 0, 100, 4, nominal_link_mbps=10000)
        self.assertGreater(slow_link["estimated_transfer_hours"], fast_link["estimated_transfer_hours"])


# --- Scoring ----------------------------------------------------------------

class TestScoreBackupTarget(unittest.TestCase):

    def test_fully_healthy_target_is_ready_with_perfect_score(self):
        scored = score_backup_target("up", True, _ports(), _sla(0.5))
        self.assertEqual(scored["score"], 100)
        self.assertEqual(scored["verdict"], "ready")

    def test_host_down_alone_is_not_ready(self):
        # Host down is the only single factor severe enough to read as not-ready by itself
        scored = score_backup_target("down", True, [], _sla(0.5))
        self.assertEqual(scored["score"], 50)
        self.assertEqual(scored["verdict"], "not-ready")

    def test_dns_failure_alone_is_at_risk(self):
        scored = score_backup_target("up", False, _ports(), _sla(0.5))
        self.assertEqual(scored["score"], 75)
        self.assertEqual(scored["verdict"], "at-risk")

    def test_single_closed_port_alone_is_at_risk(self):
        scored = score_backup_target("up", True, _ports(NFS=True), _sla(0.5))
        self.assertEqual(scored["score"], 78)
        self.assertEqual(scored["verdict"], "at-risk")

    def test_all_ports_closed_is_penalised_more_than_one_port(self):
        one = score_backup_target("up", True, _ports(NFS=True), _sla(0.5))["score"]
        all_closed = score_backup_target("up", True, _ports(NFS=True, SMB=True, iSCSI=True, Replication=True), _sla(0.5))["score"]
        self.assertLess(all_closed, one)
        self.assertEqual(all_closed, 70)

    def test_tight_sla_drops_out_of_ready(self):
        # 3.5h of a 4h window = 87.5% -> tight tier
        scored = score_backup_target("up", True, _ports(), _sla(3.5))
        self.assertEqual(scored["score"], 82)
        self.assertEqual(scored["verdict"], "at-risk")

    def test_missed_sla_is_penalised_more_than_tight(self):
        tight = score_backup_target("up", True, _ports(), _sla(3.5))["score"]
        missed = score_backup_target("up", True, _ports(), _sla(9.0))["score"]
        self.assertLess(missed, tight)
        self.assertEqual(missed, 65)

    def test_stacked_failures_reach_not_ready(self):
        # closed port (-22) + missed SLA (-35) = 43
        scored = score_backup_target("up", True, _ports(NFS=True), _sla(9.0))
        self.assertEqual(scored["score"], 43)
        self.assertEqual(scored["verdict"], "not-ready")

    def test_score_is_clamped_to_zero(self):
        scored = score_backup_target("down", False, _ports(NFS=True, SMB=True, iSCSI=True, Replication=True), _sla(999))
        self.assertEqual(scored["score"], 0)
        self.assertEqual(scored["verdict"], "not-ready")

    def test_verdict_thresholds(self):
        self.assertEqual(READY_THRESHOLD, 85)
        self.assertEqual(AT_RISK_THRESHOLD, 55)


# --- Correlation rules ------------------------------------------------------

class TestDiagnoseBackupTarget(unittest.TestCase):

    def _diagnose(self, reachability="up", dns_resolved=True, latency=20.0, loss=0.0, ports=None, hours=0.5, window=4.0):
        ports = _ports() if ports is None else ports
        return diagnose_backup_target(
            "dev-1", "backup01", reachability, dns_resolved, latency, loss, ports, _sla(hours, window), window
        )

    def test_healthy_target_produces_no_diagnostics(self):
        """No false positives on a healthy target."""
        self.assertEqual(self._diagnose(), [])

    def test_l3_down_short_circuits_everything_else(self):
        # Even with DNS broken AND ports closed AND SLA blown, a down host yields ONE finding.
        diags = self._diagnose(reachability="down", dns_resolved=False, ports=_ports(NFS=True), hours=99)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "gateway")
        self.assertEqual(diags[0]["severity"], "critical")
        self.assertIn("unreachable at the network layer", diags[0]["message"])
        self.assertEqual(diags[0]["affected_target_id"], "dev-1")

    def test_dns_failure_blames_dns_not_bandwidth(self):
        # Ports closed + SLA blown too, but DNS failing must be the ONLY thing reported.
        diags = self._diagnose(dns_resolved=False, ports=_ports(NFS=True), hours=99)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "dns")
        self.assertEqual(diags[0]["severity"], "critical")
        self.assertIn("job will fail at mount stage, not during transfer", diags[0]["message"])
        self.assertIn("Check DNS entry, not bandwidth", diags[0]["message"])

    def test_single_closed_port_blames_firewall(self):
        diags = self._diagnose(ports=_ports(NFS=True))
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "firewall")
        self.assertEqual(diags[0]["severity"], "warning")
        self.assertIn("NFS port (2049) is closed", diags[0]["message"])
        self.assertIn("likely a firewall rule, not a network outage", diags[0]["message"])
        self.assertIn("firewall", diags[0]["recommendation"].lower())
        self.assertIn("2049/NFS", diags[0]["recommendation"])

    def test_multiple_closed_ports_get_one_finding_each(self):
        diags = self._diagnose(ports=_ports(NFS=True, SMB=True))
        self.assertEqual(len(diags), 2)
        services = {d["message"].split(" port (")[0].split("the ")[-1] for d in diags}
        self.assertEqual(services, {"NFS", "SMB"})
        self.assertTrue(all(d["category"] == "firewall" for d in diags))

    def test_all_ports_closed_is_backup_protocol_not_firewall(self):
        diags = self._diagnose(ports=_ports(NFS=True, SMB=True, iSCSI=True, Replication=True))
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "backup-protocol")
        self.assertEqual(diags[0]["severity"], "critical")
        self.assertIn("none of its backup-related service ports", diags[0]["message"])
        self.assertIn("NFS, SMB, iSCSI, Replication", diags[0]["message"])

    def test_missed_sla_is_critical_throughput_finding(self):
        diags = self._diagnose(latency=250, loss=10, hours=21.8)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "throughput")
        self.assertEqual(diags[0]["severity"], "critical")
        self.assertIn("21.8h against a 4.0h SLA window", diags[0]["message"])
        self.assertIn("will NOT meet SLA", diags[0]["message"])
        self.assertIn("latency 250ms, packet loss 10%", diags[0]["message"])

    def test_tight_sla_is_warning_throughput_finding(self):
        diags = self._diagnose(hours=3.5)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "throughput")
        self.assertEqual(diags[0]["severity"], "warning")
        self.assertIn("will barely meet SLA", diags[0]["message"])

    def test_comfortable_sla_produces_no_throughput_finding(self):
        # 2.5h of 4h = 62.5% < 70% threshold
        self.assertEqual(self._diagnose(hours=2.5), [])

    def test_port_and_sla_findings_are_independent(self):
        """A closed port and an SLA miss are both real, separate findings."""
        diags = self._diagnose(ports=_ports(NFS=True), hours=9.0)
        categories = sorted(d["category"] for d in diags)
        self.assertEqual(categories, ["firewall", "throughput"])

    def test_four_edge_cases_produce_four_distinct_messages(self):
        """L3 down vs DNS-only vs port-only vs throughput-only must not collapse to one generic message."""
        l3 = self._diagnose(reachability="down")[0]
        dns = self._diagnose(dns_resolved=False)[0]
        port = self._diagnose(ports=_ports(NFS=True))[0]
        throughput = self._diagnose(hours=9.0)[0]

        messages = {l3["message"], dns["message"], port["message"], throughput["message"]}
        categories = {l3["category"], dns["category"], port["category"], throughput["category"]}
        self.assertEqual(len(messages), 4)
        self.assertEqual(categories, {"gateway", "dns", "firewall", "throughput"})

    def test_findings_match_contract_shape(self):
        diag = self._diagnose(ports=_ports(NFS=True))[0]
        self.assertEqual(
            set(diag.keys()),
            {"id", "severity", "category", "message", "recommendation", "affected_target_id", "timestamp"},
        )
        self.assertIn(diag["severity"], {"info", "warning", "critical"})
        self.assertIn(diag["category"], {"gateway", "routing", "dns", "firewall", "backup-protocol", "throughput"})


# --- Live check with mocked network primitives ------------------------------

@patch("src.backup_readiness.check_backup_ports")
@patch("src.backup_readiness.ping_host")
@patch("src.backup_readiness.check_dns")
class TestCheckBackupTarget(unittest.TestCase):

    def test_healthy_target(self, mock_dns, mock_ping, mock_ports):
        mock_dns.return_value = [{"domain": "backup01.example.com", "success": True, "resolved_ip": "10.0.0.5", "response_ms": 5}]
        mock_ping.return_value = _ping()
        mock_ports.return_value = _ports()

        status, diags = check_backup_target(_device(), dataset_size_gb=10, sla_window_hours=4)

        self.assertEqual(status["reachability"], "up")
        self.assertTrue(status["dns_resolved"])
        self.assertEqual(status["backup_readiness"]["verdict"], "ready")
        self.assertEqual(status["backup_readiness"]["score"], 100)
        self.assertTrue(status["backup_readiness"]["will_meet_sla"])
        self.assertEqual(diags, [])
        # Probed by the DNS-resolved IP
        mock_ping.assert_called_once_with("10.0.0.5")

    def test_dns_broken_but_host_up_via_known_ip(self, mock_dns, mock_ping, mock_ports):
        """DNS failure must NOT be misreported as a host outage."""
        mock_dns.return_value = [{"domain": "backup01.example.com", "success": False, "resolved_ip": None, "response_ms": 5}]
        mock_ping.return_value = _ping()
        mock_ports.return_value = _ports()

        status, diags = check_backup_target(_device())

        self.assertEqual(status["reachability"], "up")
        self.assertFalse(status["dns_resolved"])
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["category"], "dns")
        # Fell back to the device's last-known IP for the L3 probe
        mock_ping.assert_called_once_with("10.0.0.5")

    def test_host_down_skips_port_scan(self, mock_dns, mock_ping, mock_ports):
        mock_dns.return_value = [{"domain": "backup01.example.com", "success": True, "resolved_ip": "10.0.0.5", "response_ms": 5}]
        mock_ping.return_value = _ping(reachable=False, loss=100.0, latency=0.0)

        status, diags = check_backup_target(_device())

        self.assertEqual(status["reachability"], "down")
        self.assertEqual(status["ports"], [])
        mock_ports.assert_not_called()
        self.assertEqual(status["backup_readiness"]["verdict"], "not-ready")
        self.assertEqual(diags[0]["category"], "gateway")

    def test_packet_loss_marks_reachability_degraded(self, mock_dns, mock_ping, mock_ports):
        mock_dns.return_value = [{"domain": "backup01.example.com", "success": True, "resolved_ip": "10.0.0.5", "response_ms": 5}]
        mock_ping.return_value = _ping(loss=5.0)
        mock_ports.return_value = _ports()

        status, _ = check_backup_target(_device())
        self.assertEqual(status["reachability"], "degraded")
        self.assertEqual(status["packet_loss_pct"], 5.0)

    def test_ip_only_device_skips_dns_check(self, mock_dns, mock_ping, mock_ports):
        mock_ping.return_value = _ping()
        mock_ports.return_value = _ports()

        status, _ = check_backup_target(_device(hostname="10.0.0.5"))

        mock_dns.assert_not_called()
        self.assertTrue(status["dns_resolved"])

    def test_status_matches_contract_shape(self, mock_dns, mock_ping, mock_ports):
        mock_dns.return_value = [{"domain": "backup01.example.com", "success": True, "resolved_ip": "10.0.0.5", "response_ms": 5}]
        mock_ping.return_value = _ping()
        mock_ports.return_value = _ports()

        status, _ = check_backup_target(_device())

        self.assertEqual(
            set(status.keys()),
            {"id", "name", "is_backup_target", "reachability", "dns_resolved", "latency_ms",
             "packet_loss_pct", "ports", "backup_readiness"},
        )
        self.assertEqual(
            set(status["backup_readiness"].keys()),
            {"score", "verdict", "sla_window_hours", "estimated_transfer_hours", "will_meet_sla"},
        )
        for p in status["ports"]:
            self.assertEqual(set(p.keys()) >= {"port", "service", "open"}, True)


# --- Report builder / tag enforcement --------------------------------------

class TestBuildReport(unittest.TestCase):

    @patch("src.backup_readiness.check_backup_target")
    def test_non_backup_targets_never_run_backup_checks(self, mock_check):
        """The is_backup_target tag is the gate: untagged devices must not be probed."""
        generic = _device(id="generic", is_backup_target=False)
        report = build_backup_readiness_report([generic])

        mock_check.assert_not_called()
        self.assertEqual(report["targets"], [])
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["backup_readiness_score"], 0)

    @patch("src.backup_readiness.check_backup_target")
    def test_only_tagged_devices_are_checked_and_score_is_averaged(self, mock_check):
        def fake_check(device, *args, **kwargs):
            score = 100 if device.id == "a" else 60
            return (
                {"id": device.id, "name": device.name, "is_backup_target": True, "reachability": "up",
                 "dns_resolved": True, "latency_ms": 1, "packet_loss_pct": 0, "ports": [],
                 "backup_readiness": {"score": score, "verdict": "ready", "sla_window_hours": 4,
                                      "estimated_transfer_hours": 1, "will_meet_sla": True}},
                [{"id": "d", "severity": "warning", "category": "firewall", "message": "m",
                  "recommendation": "r", "affected_target_id": device.id, "timestamp": "t"}] if device.id == "b" else [],
            )
        mock_check.side_effect = fake_check

        devices = [_device(id="a"), _device(id="b"), _device(id="c", is_backup_target=False)]
        report = build_backup_readiness_report(devices, health_score=91)

        self.assertEqual(mock_check.call_count, 2)
        self.assertEqual([t["id"] for t in report["targets"]], ["a", "b"])
        self.assertEqual(report["backup_readiness_score"], 80)  # (100 + 60) / 2
        self.assertEqual(report["health_score"], 91)
        self.assertEqual(len(report["diagnostics"]), 1)
        self.assertEqual(report["diagnostics"][0]["affected_target_id"], "b")

    @patch("src.backup_readiness.check_backup_target", side_effect=RuntimeError("boom"))
    def test_one_failing_target_does_not_break_the_report(self, _):
        report = build_backup_readiness_report([_device()])
        self.assertEqual(report["targets"], [])
        self.assertEqual(report["backup_readiness_score"], 0)


# --- Simulated scenarios ----------------------------------------------------

class TestSimulatedScenarios(unittest.TestCase):

    def test_each_scenario_demonstrates_its_failure(self):
        for scenario_type in ("dns-flap", "port-blocked", "throughput-drop"):
            result = run_simulated_backup_scenario(scenario_type)
            self.assertEqual(result["type"], scenario_type)
            self.assertEqual(result["status"], "fail", f"{scenario_type} should not read as ready")
            self.assertEqual(set(result.keys()), {"id", "name", "type", "status"})
            self.assertTrue(result["name"].startswith("Demo:"))

    def test_throughput_drop_passes_with_a_generous_sla(self):
        """The throughput scenario is only a failure relative to the SLA window."""
        result = run_simulated_backup_scenario("throughput-drop", dataset_size_gb=1, sla_window_hours=48)
        self.assertEqual(result["status"], "pass")

    def test_simulated_target_runs_the_real_rules(self):
        """Each scenario's synthetic target must trip exactly the rule it is named after."""
        expected = {"dns-flap": "dns", "port-blocked": "firewall", "throughput-drop": "throughput"}
        for scenario_type, category in expected.items():
            target, diags = simulate_backup_target(scenario_type)
            self.assertEqual(target["id"], f"demo-{scenario_type}")
            self.assertTrue(target["is_backup_target"])
            self.assertNotEqual(target["backup_readiness"]["verdict"], "ready")
            self.assertEqual([d["category"] for d in diags], [category], scenario_type)
            self.assertEqual(diags[0]["affected_target_id"], target["id"])

    def test_port_blocked_scenario_closes_only_nfs(self):
        target, _ = simulate_backup_target("port-blocked")
        closed = [p["service"] for p in target["ports"] if not p["open"]]
        self.assertEqual(closed, ["NFS"])

    def test_unknown_scenario_raises(self):
        with self.assertRaises(ValueError):
            run_simulated_backup_scenario("bogus")


if __name__ == "__main__":
    unittest.main()
