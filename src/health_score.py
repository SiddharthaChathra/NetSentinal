def calculate_health_score(data):
    """
    Calculates a simple project-specific health score from 0-100.
    """
    score = 100

    gateway = data.get("gateway", {})
    internet = data.get("internet", {})
    gateway_reachable = gateway.get("reachable", False)
    internet_reachable = internet.get("reachable", False)

    # Gateway Reachability (Critical, -40) — but only when it is actually
    # blocking traffic. A gateway that drops ICMP while the internet is
    # reachable through it is forwarding fine; that costs a token -3 so the
    # score still reads as healthy rather than WARNING on cloud/container hosts.
    if not gateway_reachable:
        score -= 40 if not internet_reachable else 3

    # Internet Reachability (Critical, -30)
    if not internet_reachable:
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

    # Determine status string. "WITH WARNINGS" must reflect actual warning/
    # critical findings — informational notes (e.g. an ICMP-silent gateway
    # that costs a few points) should not make a healthy network read as
    # having warnings.
    has_warnings = any(
        (d.get("severity") if isinstance(d, dict) else getattr(d, "severity", None)) in ("warning", "critical")
        for d in data.get("diagnostics", []) or []
    )
    status = "CRITICAL"
    if score == 100 or (score >= 90 and not has_warnings):
        status = "HEALTHY"
    elif score >= 80:
        status = "HEALTHY (WITH WARNINGS)" if has_warnings else "HEALTHY"
    elif score >= 60:
        status = "WARNING"
        
    return {
        "score": score,
        "status": status
    }
