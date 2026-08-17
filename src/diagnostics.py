from src.logger import logger

def run_diagnostics(data):
    """
    Runs rule-based diagnostic engine.
    Correlates results to find likely root causes, producing structured evidence.
    """
    logger.info("Running advanced diagnostic engine...")
    diagnostics = []

    interfaces = data.get("interfaces", [])
    gateway = data.get("gateway", {})
    internet = data.get("internet", {})
    dns = data.get("dns", [])
    tcp = data.get("tcp", [])

    active_ifaces = [i for i in interfaces if i.get("state") == "UP" and i.get("ipv4")]
    gateway_reachable = gateway.get("reachable", False)
    internet_reachable = internet.get("reachable", False)
    
    dns_success = any(d.get("success", False) for d in dns) if dns else False
    tcp_success = any(t.get("success", False) for t in tcp) if tcp else False

    # RULE 1: Interface unavailable
    if not active_ifaces:
        diagnostics.append({
            "severity": "critical",
            "title": "Network interface offline",
            "likely_cause": "Network interface may be down or lacking IP configuration.",
            "evidence": [
                "No active interface with a valid IPv4 address was found."
            ],
            "recommended_checks": [
                "Check Wi-Fi/Ethernet adapter status.",
                "Verify physical cable/link.",
                "Check DHCP/IP configuration."
            ],
            "confidence": "high"
        })
        return diagnostics  # Stop further checks if no interface exists

    # RULE 2: Gateway unreachable
    if not gateway_reachable:
        diagnostics.append({
            "severity": "critical",
            "title": "Gateway connectivity problem",
            "likely_cause": "Local network or gateway connectivity issue.",
            "evidence": [
                f"Interface is UP with IP ({active_ifaces[0]['ipv4']}).",
                f"Gateway {gateway.get('address', 'Unknown')} is unreachable via ICMP."
            ],
            "recommended_checks": [
                "Check connection to local router/switch.",
                "Verify default gateway IP address.",
                "Check for local IP conflicts or wrong subnet mask.",
                "Restart the local router."
            ],
            "confidence": "high"
        })

    # RULE 3: Internet IP unreachable
    if gateway_reachable and not internet_reachable:
        diagnostics.append({
            "severity": "critical",
            "title": "Internet connectivity problem",
            "likely_cause": "Upstream routing, ISP connectivity, or firewall issue.",
            "evidence": [
                "Gateway is reachable.",
                f"Public IP target {internet.get('target', '8.8.8.8')} is unreachable."
            ],
            "recommended_checks": [
                "Check upstream router/modem status.",
                "Verify ISP network availability (ISP outage).",
                "Check outbound firewall rules blocking ICMP traffic."
            ],
            "confidence": "high"
        })

    # RULE 4: DNS Resolution Failure
    if internet_reachable and dns and not dns_success:
        failed_domains = [d['domain'] for d in dns if not d.get('success')]
        diagnostics.append({
            "severity": "critical",
            "title": "DNS resolution problem",
            "likely_cause": "DNS resolver or DNS configuration problem.",
            "evidence": [
                "Gateway and Internet IPs are reachable.",
                f"DNS resolution failed for {', '.join(failed_domains)}."
            ],
            "recommended_checks": [
                "Check configured DNS servers (e.g., 8.8.8.8, 1.1.1.1).",
                "Check if local DNS cache is stale (ipconfig /flushdns).",
                "Verify ISP or local firewall is not blocking DNS traffic on port 53."
            ],
            "confidence": "high"
        })

    # RULE 5: TCP Service Failure
    if dns_success and tcp and not tcp_success:
        failed_services = [f"{t['host']}:{t['port']}" for t in tcp if not t.get('success')]
        diagnostics.append({
            "severity": "warning",
            "title": "TCP service connectivity issue",
            "likely_cause": "TCP service, proxy, or firewall connectivity problem.",
            "evidence": [
                "Internet and DNS are functioning properly.",
                f"TCP connection failed to {', '.join(failed_services)}."
            ],
            "recommended_checks": [
                "Check local or network firewall blocking outbound ports.",
                "Verify if corporate proxy settings are required.",
                "Target service might be temporarily down."
            ],
            "confidence": "medium"
        })

    # RULE 6: Packet Loss
    internet_loss = internet.get("packet_loss", 0.0)
    if internet_reachable and internet_loss > 0:
        diagnostics.append({
            "severity": "warning" if internet_loss <= 5 else "critical",
            "title": "Network instability / Packet Loss",
            "likely_cause": "Network instability, link-quality issue, or congestion.",
            "evidence": [
                "Internet is reachable but unstable.",
                f"Detected {internet_loss}% packet loss."
            ],
            "recommended_checks": [
                "Check wireless signal strength and interference.",
                "Verify cable quality (Ethernet).",
                "Check for network congestion on the local link."
            ],
            "confidence": "high"
        })

    # RULE 7: High Latency
    internet_latency = internet.get("latency_ms", 0.0)
    if internet_reachable and internet_latency > 150:
        diagnostics.append({
            "severity": "warning",
            "title": "Unusually high latency",
            "likely_cause": "High network delay, route distance, congestion, or wireless quality.",
            "evidence": [
                f"Average latency is {internet_latency}ms (>150ms threshold)."
            ],
            "recommended_checks": [
                "Check network congestion or heavy downloads on local network.",
                "Evaluate wireless connection quality.",
                "Check geographic distance to target servers."
            ],
            "confidence": "medium"
        })

    if not diagnostics:
        diagnostics.append({
            "severity": "info",
            "title": "No major network issues detected",
            "likely_cause": "Network appears healthy across all tested layers.",
            "evidence": [
                "Interface active",
                "Gateway reachable",
                "Internet reachable",
                "DNS resolving",
                "TCP ports accessible",
                "Packet loss 0%"
            ],
            "recommended_checks": [],
            "confidence": "high"
        })

    logger.info(f"Diagnostic engine generated {len(diagnostics)} findings.")
    return diagnostics
