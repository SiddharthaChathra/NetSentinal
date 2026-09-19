"""
Backup Readiness module.

Extends the existing rule-based diagnostic engine to answer a narrower,
backup-operator-specific question for hosts tagged as backup targets:
"if I kicked off a backup job against this host right now, would it
actually work, and would it finish inside my SLA window?"

Design notes (why this is a separate module and not bolted onto
diagnostics.py / health_score.py):
  - It operates over a *list of targets* (tagged devices), not the single
    local machine the core pipeline (aggregator.py) diagnoses.
  - It reuses the project's existing primitives instead of forking them:
    DNS resolution -> src.dns_monitor.check_dns
    Reachability/latency/packet-loss -> src.connectivity.ping_host
    Port scanning -> src.port_checker.check_backup_ports (itself a thin
      wrapper around the existing check_ports() scanner)
  - Every function here is pure given its inputs except check_backup_target(),
    which is the only one that performs live network I/O. That split keeps
    the scoring/SLA/diagnostic-rule logic trivially unit-testable without
    mocking sockets.
"""
import ipaddress
import math
import uuid
from datetime import datetime, timezone

from src.logger import logger
from src.dns_monitor import check_dns
from src.connectivity import ping_host
from src.port_checker import check_backup_ports, get_backup_port_map, ALL_BACKUP_PROTOCOLS
from src.latency_monitor import classify_latency

# --- SLA estimation ---------------------------------------------------------
#
# NetSentinel does not run an active bandwidth/throughput benchmark anywhere
# in the codebase — the only live signals available for a target are
# latency, packet loss, and (from the caller) an assumed dataset size and
# SLA window. This layer turns those passive signals into a rough,
# directional "will this backup job finish in time?" estimate. It is
# intentionally conservative and approximate; see the docstring on
# estimate_sla() for the exact formula and its assumptions.

SLA_ESTIMATE_CAP_HOURS = 999.0  # keeps the estimate finite/JSON-safe instead of inf
DEFAULT_NOMINAL_LINK_MBPS = 1000.0  # generic "healthy LAN/WAN" placeholder; override with a known real link speed when available

# Latency -> throughput multiplier. TCP-based protocols (NFS/SMB/iSCSI/
# replication) lose effective throughput as RTT grows (ack/window overhead).
# We approximate this with a multiplier per the project's existing latency
# buckets (src.latency_monitor.classify_latency) rather than modeling TCP
# window math exactly.
_LATENCY_SLA_FACTORS = {
    "EXCELLENT": 1.00,  # < 50ms
    "GOOD": 0.85,        # <= 100ms
    "HIGH": 0.55,        # <= 200ms
    "VERY HIGH": 0.25,   # > 200ms
    "UNKNOWN": 0.25,      # negative/garbage latency reading -> be conservative
}


def _latency_factor(latency_ms: float) -> float:
    return _LATENCY_SLA_FACTORS.get(classify_latency(latency_ms), 0.25)


def _loss_factor(packet_loss_pct: float) -> float:
    """Approximates the well-known Mathis-style result that TCP throughput
    degrades roughly with 1/sqrt(loss). Clamped so it never divides by zero
    or collapses to zero throughput."""
    loss_pct = max(0.0, min(100.0, packet_loss_pct))
    factor = 1.0 / (1.0 + 12.0 * math.sqrt(loss_pct / 100.0))
    return max(0.05, factor)


def estimate_sla(latency_ms: float, packet_loss_pct: float, dataset_size_gb: float,
                  sla_window_hours: float, nominal_link_mbps: float = DEFAULT_NOMINAL_LINK_MBPS) -> dict:
    """
    Estimates whether a backup job would complete within its SLA window.

    Formula (all assumptions, not measurements):
      1. `nominal_link_mbps` = the link's theoretical best-case throughput on
         a perfectly healthy path (0 loss, negligible latency). Defaults to
         1000 Mbps; callers should pass a real known link speed when they
         have one — this is a placeholder, not a benchmark result.
      2. latency_factor: see _LATENCY_SLA_FACTORS above.
      3. loss_factor: see _loss_factor() above (Mathis-style approximation).
      4. effective_mbps = nominal_link_mbps * latency_factor * loss_factor
      5. estimated_transfer_hours =
             (dataset_size_gb * 8192) / (effective_mbps * 3600)
         [8192 = megabits per gigabyte: 1 GB = 1024 MB = 1024 * 8 Mb]
      6. Capped at SLA_ESTIMATE_CAP_HOURS so a dead link produces a large-but-
         finite number instead of inf (which is not valid JSON).

    This is meant to flag *directional* SLA risk from signals we can measure
    passively (latency/loss), not to replace a real throughput benchmark.
    A human should sanity-check `nominal_link_mbps` against the target's
    actual known link speed before trusting this in a specific environment.
    """
    if dataset_size_gb <= 0:
        return {
            "estimated_transfer_hours": 0.0,
            "will_meet_sla": True,
            "effective_mbps": round(nominal_link_mbps, 2),
        }

    latency_factor = _latency_factor(latency_ms)
    loss_factor = _loss_factor(packet_loss_pct)
    effective_mbps = max(0.01, nominal_link_mbps * latency_factor * loss_factor)

    hours = (dataset_size_gb * 8192.0) / (effective_mbps * 3600.0)
    hours = min(hours, SLA_ESTIMATE_CAP_HOURS)

    return {
        "estimated_transfer_hours": round(hours, 2),
        "will_meet_sla": hours <= sla_window_hours,
        "effective_mbps": round(effective_mbps, 2),
    }


# --- Backup Readiness Score --------------------------------------------------
#
# Same additive-deduction, clamp-to-[0,100], tiered-status pattern as
# src.health_score.calculate_health_score() — just re-weighted for what
# actually breaks a backup job.
#
# Weighting (documented):
#   Reachability down (L3 unreachable):          -50  (the job cannot even start; the single
#                                                       worst state, and the only one that reads
#                                                       as not-ready on its own)
#   DNS unresolved:                               -25  (job fails at mount/resolve stage)
#   Backup ports: all closed on a reachable host: -30  (no backup service reachable at all)
#                 some (but not all) closed:      -22  (we don't know which protocol the backup
#                                                       job is actually configured to use, so a
#                                                       closed NFS/SMB/iSCSI port is a real risk
#                                                       even if an unrelated port is open)
#   SLA fit (estimated transfer time vs. window):
#       <= 70% of window                            -0   (comfortable margin)
#       <= 100% of window                           -18  (tight — will barely make it)
#       > 100% of window (will miss SLA)            -35
#
# Verdict tiers:  >= 85 ready  |  55-84 at-risk  |  < 55 not-ready
#
# Design intent: any single non-fatal problem (DNS, a closed port, a tight or
# missed SLA) on an otherwise healthy host lands in "at-risk" (55-84). Only a
# host that is down at L3 (-50) reads as not-ready by itself; everything else
# reaches not-ready by stacking (e.g. a closed port *and* an SLA miss = -57 ->
# 43). This keeps "not-ready" meaningful as "there is more than one thing
# wrong, or the host is gone" rather than firing on every single warning.
# If this project would rather treat a single blocked backup port as
# not-ready on its own, raise the "some closed" constant below past 45.

READY_THRESHOLD = 85
AT_RISK_THRESHOLD = 55


def score_backup_target(reachability: str, dns_resolved: bool, ports: list, sla: dict) -> dict:
    score = 100

    if reachability == "down":
        score -= 50

    if not dns_resolved:
        score -= 25

    if ports:
        open_count = sum(1 for p in ports if p.get("open"))
        if open_count == 0:
            score -= 30
        elif open_count < len(ports):
            score -= 22

    window = sla.get("sla_window_hours", 0)
    hours = sla.get("estimated_transfer_hours", 0)
    if window > 0:
        ratio = hours / window
        if ratio <= 0.7:
            pass
        elif ratio <= 1.0:
            score -= 18
        else:
            score -= 35

    score = max(0, min(100, score))

    if score >= READY_THRESHOLD:
        verdict = "ready"
    elif score >= AT_RISK_THRESHOLD:
        verdict = "at-risk"
    else:
        verdict = "not-ready"

    return {"score": score, "verdict": verdict}


# --- Correlation rules (backup-specific diagnostics) ------------------------
#
# Each rule below is gated on the previous stage passing, same
# dependency-chain shape as src.diagnostics.run_diagnostics(): a host that's
# down at L3 doesn't also get a DNS/port/SLA message, because those checks
# are meaningless if the host isn't up. This is what makes the four edge
# cases (L3 down / DNS-only / port-only / throughput-only) produce visibly
# different messages instead of one generic "backup target unhealthy" blob.

def diagnose_backup_target(device_id: str, device_name: str, reachability: str, dns_resolved: bool,
                            latency_ms: float, packet_loss_pct: float, ports: list,
                            sla: dict, sla_window_hours: float, down_reason: dict = None,
                            ports_checked_locally: bool = False, platform: str = None) -> list:
    """`down_reason` optionally overrides RULE 1's wording ({message,
    recommendation}) - an agent that stopped reporting is a different
    situation from a host the server cannot ping.
    `ports_checked_locally` means the agent probed its own loopback: a
    closed port then means "service not listening", not "firewall".
    `platform` (Windows/Linux/Darwin) picks the right verify command."""
    diags = []
    now = datetime.now(timezone.utc).isoformat()

    def _finding(severity, category, message, recommendation):
        return {
            "id": str(uuid.uuid4()),
            "severity": severity,
            "category": category,
            "message": message,
            "recommendation": recommendation,
            "affected_target_id": device_id,
            "timestamp": now,
        }

    # RULE 1: Host unreachable at L3 — nothing downstream matters.
    if reachability == "down":
        if down_reason:
            diags.append(_finding("critical", "gateway", down_reason["message"], down_reason["recommendation"]))
        else:
            diags.append(_finding(
                "critical", "gateway",
                f"Backup target '{device_name}' is unreachable at the network layer — the backup job cannot start.",
                "Verify the host is powered on and check routing/firewall rules between this agent and the target."
            ))
        return diags

    # RULE 2: DNS resolution failing for an otherwise-reachable backup target.
    if not dns_resolved:
        diags.append(_finding(
            "critical", "dns",
            "Backup target unresolvable — job will fail at mount stage, not during transfer. Check DNS entry, not bandwidth.",
            f"Verify the DNS record for '{device_name}' and confirm this agent's resolver can reach it."
        ))
        return diags

    # RULE 3: Backup-protocol port(s) closed on an otherwise reachable host.
    if ports:
        closed = [p for p in ports if not p.get("open")]
        if closed and len(closed) == len(ports):
            services = ", ".join(p["service"] for p in ports)
            if ports_checked_locally:
                checks = "; ".join(f"{p['service']}: {verify_listening_command(p['port'], platform)}" for p in ports)
                diags.append(_finding(
                    "critical", "backup-protocol",
                    f"None of the backup services '{device_name}' is meant to serve ({services}) are listening "
                    "(checked by the agent on the host itself).",
                    f"Install/start the service(s) on this host, or untick them under 'Serves backups over' on the "
                    f"Devices page if they are not needed. Verify on the host with: {checks}"
                ))
            else:
                diags.append(_finding(
                    "critical", "backup-protocol",
                    f"Host '{device_name}' is reachable but none of its backup-related service ports ({services}) are open.",
                    "Confirm the backup/replication service is installed and running on the target, then check firewall rules."
                ))
        elif closed and ports_checked_locally:
            for p in closed:
                diags.append(_finding(
                    "warning", "backup-protocol",
                    f"The {p['service']} service is not listening on port {p['port']} of '{device_name}' (checked by the agent on the host itself).",
                    f"If this machine is meant to serve {p['service']} backups, install/start the {p['service']} service; "
                    f"otherwise untick {p['service']} under 'Serves backups over' on the Devices page. "
                    f"Verify on the host with: {verify_listening_command(p['port'], platform)}"
                ))
        elif closed:
            for p in closed:
                diags.append(_finding(
                    "warning", "firewall",
                    f"Host '{device_name}' is reachable but the {p['service']} port ({p['port']}) is closed — likely a firewall rule, not a network outage.",
                    f"Check firewall/security-group rules allowing inbound {p['port']}/{p['service']} from this agent's IP, and confirm the {p['service']} service is running on the target."
                ))

    # RULE 4: SLA risk from latency/packet-loss-driven throughput degradation.
    hours = sla.get("estimated_transfer_hours", 0)
    if sla_window_hours > 0:
        ratio = hours / sla_window_hours
        if ratio > 0.7:
            severity = "critical" if not sla.get("will_meet_sla") else "warning"
            will_text = "will NOT" if not sla.get("will_meet_sla") else "will barely"
            diags.append(_finding(
                severity, "throughput",
                f"Estimated backup transfer time is {hours}h against a {sla_window_hours}h SLA window "
                f"(latency {latency_ms}ms, packet loss {packet_loss_pct}%) — {will_text} meet SLA.",
                "Investigate WAN/link bandwidth, consider incremental backups to shrink the dataset size, or extend the SLA window."
            ))

    return diags


# --- Agent-reported status -------------------------------------------------
#
# The server usually cannot reach an agent-managed host at all: it lives on
# a private LAN, its hostname isn't in public DNS, and its IP is RFC1918.
# Probing it from the server would always say "down" — true from the
# server's vantage point and useless to the operator. For such devices the
# agent is the only thing that can see the host, so readiness is derived
# from what the agent reports about itself:
#   reachability  <- how recently the agent checked in (AGENT_STALE_SECONDS)
#   latency/loss  <- the agent's own internet measurement (link quality)
#   dns_resolved  <- the agent's DNS health check
#   ports         <- NFS/SMB/iSCSI/replication checked by the agent on 127.0.0.1
# Scoring, SLA estimation and the correlation rules are shared with the
# server-probed path; only the signal source differs.

AGENT_STALE_SECONDS = 180  # agent reports every 60s; 3 misses = offline


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def status_from_telemetry(device, telemetry: dict, dataset_size_gb: float = 500.0,
                          sla_window_hours: float = 4.0, now: datetime = None) -> tuple:
    """Builds (target_status, diagnostics) for an agent-managed device from
    its most recent telemetry row. Same output shape as check_backup_target()."""
    now = now or datetime.now(timezone.utc)
    reported_at = _parse_ts(telemetry.get("timestamp") or now)
    age_s = max(0.0, (now - reported_at).total_seconds())
    stale = age_s > AGENT_STALE_SECONDS

    latency_ms = float(telemetry.get("latency_ms") or 0.0)
    packet_loss = float(telemetry.get("packet_loss") or 0.0)
    internet_ok = bool(telemetry.get("internet_reachable", True))
    dns_resolved = bool(telemetry.get("dns_healthy", True))

    if stale:
        reachability = "down"
    elif not internet_ok or packet_loss > 0:
        reachability = "degraded"
    else:
        reachability = "up"

    wanted = set(getattr(device, "backup_protocols", None) or ALL_BACKUP_PROTOCOLS)
    raw_ports = telemetry.get("backup_ports") or []
    ports = [
        {"port": int(p["port"]), "service": str(p.get("service", "")), "open": bool(p.get("open"))}
        for p in raw_ports
        if isinstance(p, dict) and "port" in p and p.get("service") in wanted
    ] if reachability != "down" else []

    sla = estimate_sla(latency_ms, packet_loss, dataset_size_gb, sla_window_hours)
    sla["sla_window_hours"] = sla_window_hours
    scored = score_backup_target(reachability, dns_resolved, ports, sla)

    down_reason = None
    if stale:
        minutes = int(age_s // 60)
        down_reason = {
            "message": (
                f"The NetSentinel agent on '{device.name}' last reported {minutes} minute(s) ago — "
                "the host may be offline, asleep, or the agent has stopped."
            ),
            "recommendation": "Check the machine is on and connected, then make sure the agent (agent/agent.py --start) is still running on it.",
        }

    diagnostics = diagnose_backup_target(
        device.id, device.name, reachability, dns_resolved,
        latency_ms, packet_loss, ports, sla, sla_window_hours,
        down_reason=down_reason, ports_checked_locally=True,
        platform=getattr(device, "platform", None),
    )

    target_status = {
        "id": device.id,
        "name": device.name,
        "is_backup_target": True,
        "reachability": reachability,
        "dns_resolved": dns_resolved,
        "latency_ms": latency_ms,
        "packet_loss_pct": packet_loss,
        "ports": ports,
        "backup_readiness": {
            "score": scored["score"],
            "verdict": scored["verdict"],
            "sla_window_hours": sla_window_hours,
            "estimated_transfer_hours": sla["estimated_transfer_hours"],
            "will_meet_sla": sla["will_meet_sla"],
        },
    }
    return target_status, diagnostics


def verify_listening_command(port: int, platform: str = None) -> str:
    """The one-liner an operator pastes on the host to see whether anything
    is listening on `port`. Chosen by the device's reported platform."""
    name = (platform or "").lower()
    if name.startswith("win"):
        return f"netstat -an | findstr :{port}"
    if name.startswith("darwin") or name.startswith("mac"):
        return f"lsof -iTCP:{port} -sTCP:LISTEN"
    return f"sudo ss -ltnp | grep ':{port}'"


# --- Live target check (the only function here that touches the network) ---

def _looks_like_ip(value) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


def check_backup_target(device, dataset_size_gb: float = 500.0, sla_window_hours: float = 4.0,
                         replication_port=None) -> tuple:
    """Runs live DNS + reachability + backup-port checks against a single
    tagged backup-target device. Returns (target_status_dict, diagnostics_list),
    both using snake_case keys matching the Pydantic model field names in
    src.models (the API layer serializes these to the camelCase contract).

    Caller is responsible for only invoking this for devices where
    is_backup_target is True — generic (non-backup) targets never reach
    this function, so they never run backup-specific checks.
    """
    hostname = (getattr(device, "hostname", "") or "").strip()
    ip_address = getattr(device, "ip_address", "") or ""

    # Only attempt DNS resolution when we actually have a distinct hostname
    # to resolve (not a bare IP, and not the same value already used as the
    # device's known IP — which is the case for ad-hoc CLI targets that only
    # supply a single host string).
    needs_dns = bool(hostname) and hostname != ip_address and not _looks_like_ip(hostname)
    dns_resolved = True
    resolved_ip = None
    if needs_dns:
        dns_result = check_dns([hostname])
        entry = dns_result[0] if dns_result else {}
        dns_resolved = entry.get("success", False)
        resolved_ip = entry.get("resolved_ip")

    # Probe by resolved IP when we have one; otherwise fall back to the
    # device's last-known IP (this is what lets "DNS broken but host still
    # up" be distinguishable from "host actually down").
    probe_host = resolved_ip or ip_address or hostname
    ping = ping_host(probe_host)

    if ping["reachable"] and ping["packet_loss"] == 0:
        reachability = "up"
    elif ping["reachable"]:
        reachability = "degraded"
    else:
        reachability = "down"

    ports = []
    if reachability != "down":
        ports = check_backup_ports(probe_host, replication_port,
                                   protocols=getattr(device, "backup_protocols", None))

    sla = estimate_sla(ping["latency_ms"], ping["packet_loss"], dataset_size_gb, sla_window_hours)
    sla["sla_window_hours"] = sla_window_hours

    scored = score_backup_target(reachability, dns_resolved, ports, sla)
    diagnostics = diagnose_backup_target(
        device.id, device.name, reachability, dns_resolved,
        ping["latency_ms"], ping["packet_loss"], ports, sla, sla_window_hours
    )

    target_status = {
        "id": device.id,
        "name": device.name,
        "is_backup_target": True,
        "reachability": reachability,
        "dns_resolved": dns_resolved,
        "latency_ms": ping["latency_ms"],
        "packet_loss_pct": ping["packet_loss"],
        "ports": ports,
        "backup_readiness": {
            "score": scored["score"],
            "verdict": scored["verdict"],
            "sla_window_hours": sla_window_hours,
            "estimated_transfer_hours": sla["estimated_transfer_hours"],
            "will_meet_sla": sla["will_meet_sla"],
        },
    }
    return target_status, diagnostics


# --- Aggregate report builder (what the API endpoint returns) --------------

def build_backup_readiness_report(devices: list, health_score: int = 0, dataset_size_gb: float = 500.0,
                                   sla_window_hours: float = 4.0, replication_port=None,
                                   telemetry_by_device: dict = None) -> dict:
    """Builds the full contract-shaped report from a list of Device objects.
    Filters to is_backup_target devices only — this is the enforcement point
    for "non-backup targets don't run backup-specific checks".
    simulated_scenarios is always empty here; the API layer overlays demo
    data on top when a scenario is requested.
    """
    backup_devices = [d for d in devices if getattr(d, "is_backup_target", False)]
    # A device with a telemetry entry is agent-managed: evaluate it from the
    # agent's report. Anything else is probed live from the server.
    telemetry_by_device = telemetry_by_device or {}

    targets = []
    all_diagnostics = []
    for device in backup_devices:
        try:
            telemetry = telemetry_by_device.get(device.id)
            if telemetry:
                status, diags = status_from_telemetry(device, telemetry, dataset_size_gb, sla_window_hours)
            else:
                status, diags = check_backup_target(device, dataset_size_gb, sla_window_hours, replication_port)
            targets.append(status)
            all_diagnostics.extend(diags)
        except Exception as e:
            logger.error(f"Backup readiness check failed for device {device.id}: {e}")

    backup_readiness_score = (
        round(sum(t["backup_readiness"]["score"] for t in targets) / len(targets))
        if targets else 0
    )

    return {
        "health_score": health_score,
        "backup_readiness_score": backup_readiness_score,
        "targets": targets,
        "diagnostics": all_diagnostics,
        "simulated_scenarios": [],
    }


# --- Simulated failure scenarios --------------------------------------------
#
# Extends the project's existing simulated-scenario convention (see
# aggregator.py's _get_demo_data / app.py's --demo flag) with backup-specific
# cases. These run synthetic input through the *real* scoring/diagnostic
# functions above (not a separate fake pipeline), so they double as a live
# preview for the frontend and as a sanity check that the rules fire as
# expected without needing real NFS/SMB/iSCSI infrastructure.

_SCENARIO_NAMES = {
    "dns-flap": "Demo: Backup DNS Flap",
    "port-blocked": "Demo: Backup Port Blocked",
    "throughput-drop": "Demo: Backup Throughput Drop",
}


def _synthetic_backup_scenario_inputs(scenario_type: str):
    if scenario_type == "dns-flap":
        # Host up via known IP, but the name it would be mounted by doesn't resolve.
        return {
            "reachability": "up", "dns_resolved": False,
            "latency_ms": 20.0, "packet_loss_pct": 0.0,
            "ports": [{"port": p, "service": s, "open": True} for p, s in get_backup_port_map().items()],
        }
    if scenario_type == "port-blocked":
        # Host + DNS fine, but a firewall rule blocks the NFS port specifically.
        return {
            "reachability": "up", "dns_resolved": True,
            "latency_ms": 20.0, "packet_loss_pct": 0.0,
            "ports": [{"port": p, "service": s, "open": (s != "NFS")} for p, s in get_backup_port_map().items()],
        }
    if scenario_type == "throughput-drop":
        # Everything reachable, but latency/loss are bad enough to blow the SLA window.
        return {
            "reachability": "up", "dns_resolved": True,
            "latency_ms": 420.0, "packet_loss_pct": 9.0,
            "ports": [{"port": p, "service": s, "open": True} for p, s in get_backup_port_map().items()],
        }
    raise ValueError(f"Unknown backup scenario type: {scenario_type}")


def simulate_backup_target(scenario_type: str, dataset_size_gb: float = 500.0,
                            sla_window_hours: float = 4.0) -> tuple:
    """Runs one canned backup failure scenario through the real scoring and
    correlation-rule pipeline and returns (target_status, diagnostics) in the
    same shape check_backup_target() produces for a live device — so the
    frontend can render a full target card and its diagnostics without any
    real NFS/SMB/iSCSI infrastructure."""
    name = _SCENARIO_NAMES.get(scenario_type)
    if name is None:
        raise ValueError(f"Unknown backup scenario type: {scenario_type}")

    inputs = _synthetic_backup_scenario_inputs(scenario_type)
    sla = estimate_sla(inputs["latency_ms"], inputs["packet_loss_pct"], dataset_size_gb, sla_window_hours)
    sla["sla_window_hours"] = sla_window_hours
    scored = score_backup_target(inputs["reachability"], inputs["dns_resolved"], inputs["ports"], sla)

    target_id = f"demo-{scenario_type}"
    diagnostics = diagnose_backup_target(
        target_id, name, inputs["reachability"], inputs["dns_resolved"],
        inputs["latency_ms"], inputs["packet_loss_pct"], inputs["ports"], sla, sla_window_hours
    )

    target_status = {
        "id": target_id,
        "name": name,
        "is_backup_target": True,
        "reachability": inputs["reachability"],
        "dns_resolved": inputs["dns_resolved"],
        "latency_ms": inputs["latency_ms"],
        "packet_loss_pct": inputs["packet_loss_pct"],
        "ports": inputs["ports"],
        "backup_readiness": {
            "score": scored["score"],
            "verdict": scored["verdict"],
            "sla_window_hours": sla_window_hours,
            "estimated_transfer_hours": sla["estimated_transfer_hours"],
            "will_meet_sla": sla["will_meet_sla"],
        },
    }
    return target_status, diagnostics


def run_simulated_backup_scenario(scenario_type: str, dataset_size_gb: float = 500.0,
                                   sla_window_hours: float = 4.0) -> dict:
    """Returns a simulatedScenarios-shaped entry ({id, name, type, status})
    for one canned scenario. status="fail" means the simulated condition
    would put the backup job at risk or cause it to fail outright — i.e. the
    scenario successfully demonstrates the problem it's named after."""
    target_status, _ = simulate_backup_target(scenario_type, dataset_size_gb, sla_window_hours)
    return {
        "id": str(uuid.uuid4()),
        "name": target_status["name"],
        "type": scenario_type,
        "status": "pass" if target_status["backup_readiness"]["verdict"] == "ready" else "fail",
    }
