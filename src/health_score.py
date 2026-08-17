def calculate_health_score(data):
    """
    Calculates a simple project-specific health score from 0-100.
    """
    score = 100
    
    # Gateway Reachability (Critical, -40)
    gateway = data.get("gateway", {})
    if not gateway.get("reachable", False):
        score -= 40
        
    # Internet Reachability (Critical, -30)
    internet = data.get("internet", {})
    if not internet.get("reachable", False):
        score -= 30
        
    # DNS Resolution (-15 if all fail)
    dns_results = data.get("dns", [])
    if dns_results:
        if not any(d.get("success", False) for d in dns_results):
            score -= 15
            
    # TCP Service Connectivity (-15 if all fail)
    tcp_results = data.get("tcp", [])
    if tcp_results:
        if not any(t.get("success", False) for t in tcp_results):
            score -= 15

    # Packet Loss Deductions (up to -20)
    loss = internet.get("packet_loss", 0.0)
    if loss > 0:
        if loss >= 20:
            score -= 20
        else:
            score -= int(loss)
            
    # Latency Deductions (up to -10)
    latency = internet.get("latency_ms", 0.0)
    if latency > 100:
        if latency > 300:
            score -= 10
        else:
            score -= 5

    # Ensure bounds
    score = max(0, min(100, score))

    # Determine status string
    status = "CRITICAL"
    if score == 100:
        status = "HEALTHY"
    elif score >= 80:
        status = "HEALTHY (WITH WARNINGS)"
    elif score >= 60:
        status = "WARNING"
        
    return {
        "score": score,
        "status": status
    }
