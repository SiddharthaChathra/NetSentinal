import json
from src.latency_monitor import classify_latency, classify_packet_loss

def print_terminal_report(data):
    """Prints the final structured output to the terminal."""
    
    print("\n==============================================================")
    print("                 NETSENTINEL")
    print("        NETWORK HEALTH & DIAGNOSTICS")
    print("==============================================================\n")
    
    # System Info
    sys_info = data.get("system", {})
    print("SYSTEM")
    print("-" * 62)
    print(f"Hostname          : {sys_info.get('hostname', 'N/A')}")
    print(f"OS                : {sys_info.get('os', 'N/A')} {sys_info.get('os_version', '')}")
    print(f"Local IP          : {sys_info.get('local_ip', 'N/A')}")
    print()

    # Interfaces
    print("INTERFACE")
    print("-" * 62)
    interfaces = data.get("interfaces", [])
    if not interfaces:
        print("[UNAVAILABLE] No interface data found.")
    else:
        for iface in interfaces:
            # Only print UP interfaces with an IP or the first one if we need to
            if iface.get('state') == 'UP' or iface.get('ipv4'):
                print(f"{iface.get('name', 'N/A'):<17} : {iface.get('state', 'UNKNOWN')}")
                if iface.get('ipv4'):
                    print(f"{'IP':<17} : {iface.get('ipv4')}")
    print()

    # Gateway
    print("GATEWAY")
    print("-" * 62)
    gw = data.get("gateway", {})
    print(f"Gateway           : {gw.get('address', 'NOT DETECTED')}")
    status = "PASS" if gw.get('reachable') else "FAIL"
    print(f"Status            : {status}")
    if gw.get('reachable'):
        print(f"Latency           : {gw.get('latency_ms', 0)} ms")
    print()

    # Internet
    print("INTERNET")
    print("-" * 62)
    internet = data.get("internet", {})
    print(f"Target            : {internet.get('target', 'N/A')}")
    status = "PASS" if internet.get('reachable') else "FAIL"
    print(f"Status            : {status}")
    
    loss = internet.get('packet_loss', 100.0)
    print(f"Packet Loss       : {loss}% ({classify_packet_loss(loss)})")
    
    if internet.get('reachable'):
        lat = internet.get('latency_ms', 0)
        print(f"Latency           : {lat} ms ({classify_latency(lat)})")
    print()

    # DNS
    dns_results = data.get("dns", [])
    if dns_results:
        print("DNS")
        print("-" * 62)
        for d in dns_results:
            status = "PASS" if d.get('success') else "FAIL"
            print(f"{d.get('domain', 'N/A'):<17} : {status}")
        print()

    # TCP
    tcp_results = data.get("tcp", [])
    if tcp_results:
        print("TCP SERVICES")
        print("-" * 62)
        for t in tcp_results:
            status = "OPEN" if t.get('success') else "CLOSED"
            print(f"{t.get('port')} {t.get('host'):<13} : {status}")
        print()

    # Routing
    print("ROUTING")
    print("-" * 62)
    print(data.get("routes", {}).get("default_route", "Not available"))
    print()
    
    # Stability (Optional Feature)
    stability = data.get("stability", {})
    if stability:
        print("NETWORK STABILITY")
        print("-" * 62)
        print(f"Min RTT           : {stability.get('min_rtt', 0)} ms")
        print(f"Avg RTT           : {stability.get('avg_rtt', 0)} ms")
        print(f"Max RTT           : {stability.get('max_rtt', 0)} ms")
        print(f"Packet Loss       : {stability.get('packet_loss', 0)}%")
        print(f"Stability         : {stability.get('stability', 'UNKNOWN')}")
        print()

    # Health
    print("HEALTH")
    print("-" * 62)
    print(f"Score             : {data.get('health_score', 0)}/100")
    print(f"Status            : {data.get('status', 'UNKNOWN')}")
    print()

    # Diagnostics
    print("DIAGNOSTICS")
    print("-" * 62)
    diagnostics = data.get("diagnostics", [])
    for idx, diag in enumerate(diagnostics):
        if idx > 0: print()
        sev = diag.get("severity", "info")
        title = diag.get("title", "")
        if sev == "info" and "No major" in title:
            print("[OK] No major network issues detected.")
        else:
            sev_label = f"[{sev.upper()}]"
            print(f"{sev_label} {title}")
            print(f"  Cause       : {diag.get('likely_cause', 'Unknown')}")
            print(f"  Confidence  : {diag.get('confidence', 'N/A')}")
            evidence = diag.get('evidence', [])
            if evidence:
                print(f"  Evidence    :")
                for e in evidence:
                    print(f"    • {e}")
            checks = diag.get('recommended_checks', [])
            if checks:
                print(f"  Suggestions :")
                for check in checks:
                    print(f"    - {check}")
                
    print("\n==============================================================\n")

def print_json_report(data):
    """Prints the final output as JSON."""
    print(json.dumps(data, indent=2))
