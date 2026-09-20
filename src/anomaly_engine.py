from typing import Optional, Dict, Any
from src.baseline_engine import get_baseline
from src.logger import logger

PROBES_PER_SAMPLE = 4  # connectivity.ping_host sends 4 ICMP echoes

# Titles this engine owns. Only these are ever auto-resolved; an incident
# raised from a diagnostic finding or by hand is left alone.
ANOMALY_TITLES = {
    "latency": "Latency Anomaly Detected",
    "packet_loss": "Packet Loss Anomaly Detected",
    "dns": "DNS Failure Detected",
    "gateway": "Gateway Unreachable",
}

# Loss has to be absent this many reports running before it counts as over,
# for the same reason it has to recur before it counts as a problem.
CLEAR_CONSECUTIVE_SAMPLES = 3


def evaluate_device_state(device_id: str, telemetry: Dict[str, Any],
                          recent_losses: Optional[list] = None) -> Dict[str, Any]:
    """Both halves of the picture: what is wrong now, and what has stopped
    being wrong.

    The second half exists because the incident engine was a ratchet - it
    could only ever open incidents. A two-second blip left an OPEN incident
    forever, and because new incidents are de-duplicated against open ones
    with the same title, that stale row then SUPPRESSED any later report of
    the same problem. Detecting "this is over" is what makes the dedup safe.
    """
    anomalies = detect_anomalies(device_id, telemetry, recent_losses)
    raised = {a["title"] for a in anomalies}
    cleared = set()

    if telemetry.get("dns_healthy", True):
        cleared.add(ANOMALY_TITLES["dns"])
    if telemetry.get("gateway_reachable", True):
        cleared.add(ANOMALY_TITLES["gateway"])

    # Loss: quiet for the last few reports, not merely quiet right now.
    samples = [l for l in (recent_losses or []) if l is not None][:CLEAR_CONSECUTIVE_SAMPLES]
    if len(samples) >= CLEAR_CONSECUTIVE_SAMPLES and all(l <= 0 for l in samples):
        cleared.add(ANOMALY_TITLES["packet_loss"])

    if ANOMALY_TITLES["latency"] not in raised:
        cleared.add(ANOMALY_TITLES["latency"])

    # Never claim something is both wrong and over.
    cleared -= raised
    return {"anomalies": anomalies, "cleared": cleared}


def detect_anomalies(device_id: str, telemetry: Dict[str, Any],
                     recent_losses: Optional[list] = None) -> list:
    """
    Evaluates current telemetry against historical baselines.
    Returns a list of anomaly dictionaries if any are detected.

    `recent_losses` is packet loss from the last few reports, newest first.
    It exists because a ping run is only 4 packets, so ONE dropped packet is
    25% loss — enough to raise a warning incident off a single sample that
    nobody would consider a fault. Loss now has to recur before it counts.
    """
    anomalies = []
    
    # 1. Latency Anomaly
    current_latency = telemetry.get("latency_ms", 0.0)
    latency_baseline = get_baseline(device_id, "latency")
    
    if latency_baseline["average"] > 0:
        # If latency is > p95 OR > average + 3*stddev
        threshold = max(latency_baseline["p95"], latency_baseline["average"] + 3 * latency_baseline["stddev"])
        # Give some absolute leeway so we don't alert on 5ms going to 15ms if p95 is 10ms.
        threshold = max(threshold, 50.0)
        
        if current_latency > threshold:
            anomalies.append({
                "metric": "latency",
                "current": current_latency,
                "baseline_avg": latency_baseline["average"],
                "threshold": threshold,
                "severity": "warning",
                "title": "Latency Anomaly Detected",
                "reason": f"Current latency ({current_latency}ms) is significantly above baseline ({latency_baseline['average']}ms)."
            })

    # 2. Packet Loss Anomaly
    current_loss = telemetry.get("packet_loss", 0.0)
    if current_loss > 2.0:  # Absolute threshold for loss is usually preferred initially
        loss_baseline = get_baseline(device_id, "packet_loss")
        # Ignore the current sample in the history so "recurring" means it
        # happened on a DIFFERENT run, not that we counted this one twice.
        previous = [l for l in (recent_losses or [])[1:] if l is not None]
        recurred = any(l > 0 for l in previous)
        if current_loss > (loss_baseline["average"] + 2.0) and recurred:
            dropped = round(current_loss / 100 * PROBES_PER_SAMPLE)
            anomalies.append({
                "metric": "packet_loss",
                "current": current_loss,
                "baseline_avg": loss_baseline.get("average", 0.0),
                "threshold": loss_baseline.get("average", 0.0) + 2.0,
                "severity": "warning",
                "title": "Packet Loss Anomaly Detected",
                "reason": (
                    f"Packet loss {current_loss}% ({dropped} of {PROBES_PER_SAMPLE} probes), "
                    f"and loss also occurred in {sum(1 for l in previous if l > 0)} of the "
                    f"previous {len(previous)} report(s)."
                ),
            })
            
    # 3. DNS Failure Anomaly
    if not telemetry.get("dns_healthy", True):
         anomalies.append({
             "metric": "dns",
             "current": 0,
             "severity": "critical",
             "title": "DNS Failure Detected",
             "reason": "DNS resolution has failed."
         })
         
    # 4. Gateway Failure Anomaly
    if not telemetry.get("gateway_reachable", True):
         anomalies.append({
             "metric": "gateway",
             "current": 0,
             "severity": "critical",
             "title": "Gateway Unreachable",
             "reason": "The default gateway is unreachable."
         })

    return anomalies
