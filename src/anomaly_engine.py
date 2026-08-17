from typing import Optional, Dict, Any
from src.baseline_engine import get_baseline
from src.logger import logger

def detect_anomalies(device_id: str, telemetry: Dict[str, Any]) -> list:
    """
    Evaluates current telemetry against historical baselines.
    Returns a list of anomaly dictionaries if any are detected.
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
        if current_loss > (loss_baseline["average"] + 2.0):
            anomalies.append({
                "metric": "packet_loss",
                "current": current_loss,
                "baseline_avg": loss_baseline.get("average", 0.0),
                "threshold": loss_baseline.get("average", 0.0) + 2.0,
                "severity": "warning",
                "title": "Packet Loss Anomaly Detected",
                "reason": f"Current packet loss ({current_loss}%) is above normal levels."
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
