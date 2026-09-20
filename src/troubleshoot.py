"""The "why is my network slow?" analysis.

This used to be computed in the browser from hardcoded constants: the packet
loss shown was the literal `0.3`, the baseline was the literal `24`, and the
evidence bullets ("Packet loss rate is normal") were fixed strings printed
whether or not anything had been measured. The only real number on the panel
was latency.

Everything here is derived from measurements the system actually took, and
where there is nothing to derive from it says so rather than inventing a
plausible-looking figure. Kept as pure functions so the reasoning can be
tested without a database.
"""
from datetime import datetime, timezone
from statistics import mean, median, pstdev
from typing import Any, Dict, List, Optional

# Below this many samples a "baseline" is noise, and comparing against it
# would manufacture anomalies out of ordinary variation.
MIN_BASELINE_SAMPLES = 20

# A single ping run is 4 packets, so one dropped packet reads as 25%. Loss is
# only called out when it persists across runs.
LOSS_NOTABLE_PCT = 2.0
LATENCY_SLOW_MS = 100.0
LATENCY_VERY_SLOW_MS = 300.0


def summarise(values: List[float]) -> Dict[str, Any]:
    """Descriptive stats plus the sample count, because a number computed from
    three samples should never be presented like one computed from a thousand."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {"samples": 0, "average": None, "median": None, "p95": None, "stddev": None}
    ordered = sorted(clean)
    idx = min(int(0.95 * len(ordered)), len(ordered) - 1)
    return {
        "samples": len(clean),
        "average": round(mean(clean), 1),
        "median": round(median(clean), 1),
        "p95": round(ordered[idx], 1),
        "stddev": round(pstdev(clean), 1) if len(clean) > 1 else 0.0,
    }


def _age_seconds(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def build_analysis(
    *,
    source: str,
    device_name: Optional[str],
    measured_at: Optional[str],
    latency_ms: Optional[float],
    packet_loss_pct: Optional[float],
    gateway_reachable: Optional[bool],
    internet_reachable: Optional[bool],
    dns_healthy: Optional[bool],
    latency_history: List[float],
    loss_history: List[float],
    probes_per_sample: int = 4,
) -> Dict[str, Any]:
    """Assemble the analysis from measurements.

    `source` is "agent" (this account's own machine), "hosted-scan" (the
    NetSentinel server's network, which is NOT the user's), or "none".
    """
    latency_stats = summarise(latency_history)
    loss_stats = summarise(loss_history)
    have_baseline = latency_stats["samples"] >= MIN_BASELINE_SAMPLES

    evidence: List[str] = []
    recommendations: List[str] = []
    findings: List[str] = []
    severity = "normal"

    if source == "none" or latency_ms is None:
        return {
            "status": "unknown",
            "source": {"kind": "none", "device": None, "measured_at": None, "probes_per_sample": probes_per_sample},
            "latency": {"current_ms": None, "baseline": latency_stats},
            "packet_loss": {"current_pct": None, "baseline": loss_stats},
            "likely_issue": "Nothing measured yet.",
            "evidence": ["No diagnostic run or agent report to analyse."],
            "recommended_checks": [
                "Run a diagnostic from the Overview page to measure this server's network.",
                "Install the agent on a machine to measure that machine's network.",
            ],
            "confidence": "none",
            "baseline_ready": False,
        }

    # --- latency -----------------------------------------------------------
    baseline_ms = latency_stats["average"] if have_baseline else None
    if baseline_ms is not None:
        # p95 is the "unusually slow for this network" line; the absolute
        # floor stops a very fast network flagging a 12ms blip as an anomaly.
        slow_line = max(latency_stats["p95"], baseline_ms * 2, 50.0)
        if latency_ms > slow_line:
            severity = "degraded"
            findings.append("latency above this network's normal range")
            evidence.append(
                f"Latency {latency_ms:.0f} ms is above this network's 95th percentile "
                f"({latency_stats['p95']:.0f} ms) over the last {latency_stats['samples']} measurements."
            )
        else:
            evidence.append(
                f"Latency {latency_ms:.0f} ms is within the normal range for this network "
                f"(average {baseline_ms:.0f} ms over {latency_stats['samples']} measurements)."
            )
    else:
        evidence.append(
            f"Latency {latency_ms:.0f} ms. Not enough history yet to say what is normal here "
            f"({latency_stats['samples']} of {MIN_BASELINE_SAMPLES} measurements needed)."
        )
        if latency_ms > LATENCY_VERY_SLOW_MS:
            severity = "degraded"
            findings.append("very high latency")
        elif latency_ms > LATENCY_SLOW_MS:
            severity = "degraded"
            findings.append("high latency")

    # --- packet loss -------------------------------------------------------
    if packet_loss_pct is None:
        evidence.append("Packet loss was not measured in this sample.")
    elif packet_loss_pct <= 0:
        evidence.append(f"No packet loss in the last sample ({probes_per_sample} probes).")
    else:
        recent = [l for l in loss_history[-5:] if l is not None]
        sustained = sum(1 for l in recent if l > 0) >= 2
        # One dropped packet out of four reads as 25%, which looks alarming
        # and usually is not. Say what it was actually measured from.
        detail = (
            f"Packet loss {packet_loss_pct:.0f}% in the last sample "
            f"(that is {round(packet_loss_pct / 100 * probes_per_sample)} of {probes_per_sample} probes)"
        )
        if sustained and packet_loss_pct >= LOSS_NOTABLE_PCT:
            severity = "degraded"
            findings.append("sustained packet loss")
            evidence.append(f"{detail}, and loss has appeared in {sum(1 for l in recent if l > 0)} of the last {len(recent)} samples.")
        else:
            evidence.append(f"{detail}. A single dropped probe is normal on most links and has not recurred.")

    # --- hard failures dominate everything else ----------------------------
    if dns_healthy is False:
        severity = "critical"
        findings.insert(0, "DNS is not resolving")
        evidence.append("DNS lookups failed at the last check.")
        recommendations.append("Check your DNS settings, or try 1.1.1.1 / 8.8.8.8 as a resolver.")
    if internet_reachable is False:
        severity = "critical"
        findings.insert(0, "no route to the internet")
        evidence.append("The public internet target did not respond.")
        recommendations.append("Confirm the router has an upstream connection.")
    elif gateway_reachable is False:
        # Only interesting when the internet is NOT reachable; a gateway that
        # drops ICMP while traffic flows is normal and is not a fault.
        evidence.append("The default gateway did not answer ping, but the internet is reachable through it — that is normal on many networks.")

    # --- what to actually do ----------------------------------------------
    if severity == "normal":
        likely_issue = "No degradation detected."
        recommendations.extend([
            "If something specific feels slow, test that site or service directly — the link itself is healthy.",
            "Check local CPU and background downloads on the machine you are using.",
        ])
    else:
        likely_issue = "Detected: " + ", ".join(findings) + "."
        if "sustained packet loss" in findings:
            recommendations.append("Check Wi-Fi signal strength, or try a wired connection to see if loss disappears.")
        if any("latency" in f for f in findings):
            recommendations.append("Check whether anything on the network is uploading or downloading heavily.")
        recommendations.append("Compare against another machine on the same network to tell local from upstream.")

    # --- how much this is worth trusting -----------------------------------
    if severity == "critical":
        confidence = "high"
    elif not have_baseline:
        confidence = "low"
    elif latency_stats["samples"] >= 100:
        confidence = "high"
    else:
        confidence = "medium"

    age = _age_seconds(measured_at)
    return {
        "status": severity,
        "source": {
            "kind": source,
            "device": device_name,
            "measured_at": measured_at,
            "age_seconds": int(age) if age is not None else None,
            "probes_per_sample": probes_per_sample,
        },
        "latency": {"current_ms": round(latency_ms, 1), "baseline": latency_stats},
        "packet_loss": {
            "current_pct": round(packet_loss_pct, 1) if packet_loss_pct is not None else None,
            "baseline": loss_stats,
        },
        "likely_issue": likely_issue,
        "evidence": evidence,
        "recommended_checks": recommendations,
        "confidence": confidence,
        "baseline_ready": have_baseline,
    }
