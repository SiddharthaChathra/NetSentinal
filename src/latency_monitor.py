def classify_latency(latency_ms):
    """Classifies latency into project-specific categories."""
    if latency_ms < 0:
        return "UNKNOWN"
    elif latency_ms < 50:
        return "EXCELLENT"
    elif latency_ms <= 100:
        return "GOOD"
    elif latency_ms <= 200:
        return "HIGH"
    else:
        return "VERY HIGH"

def classify_packet_loss(loss_percentage):
    """Classifies packet loss into project-specific categories."""
    if loss_percentage < 0:
        return "UNKNOWN"
    elif loss_percentage == 0:
        return "HEALTHY"
    elif loss_percentage <= 5:
        return "WARNING"
    else:
        return "HIGH PACKET LOSS"

def analyze_stability(ping_results):
    """Analyzes network stability from ping results."""
    # We will use the internet ping results which usually contains min, max, avg
    if not ping_results.get("reachable", False):
        return {"status": "UNREACHABLE"}
    
    min_rtt = ping_results.get("min_latency_ms", 0.0)
    avg_rtt = ping_results.get("latency_ms", 0.0)
    max_rtt = ping_results.get("max_latency_ms", 0.0)
    loss = ping_results.get("packet_loss", 0.0)
    
    # Simple variance logic
    jitter = max_rtt - min_rtt
    
    stability = "GOOD"
    if loss > 5 or jitter > 100 or avg_rtt > 200:
        stability = "POOR"
    elif loss > 0 or jitter > 50 or avg_rtt > 100:
        stability = "FAIR"
        
    return {
        "min_rtt": min_rtt,
        "avg_rtt": avg_rtt,
        "max_rtt": max_rtt,
        "packet_loss": loss,
        "jitter": jitter,
        "stability": stability
    }
