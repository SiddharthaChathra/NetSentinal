import time
from datetime import datetime
from src.logger import logger
from src.system_info import get_system_info
from src.interface_monitor import get_interfaces
from src.gateway_monitor import get_default_gateway, get_routing_info
from src.connectivity import check_gateway_connectivity, check_internet_connectivity
from src.dns_monitor import check_dns
from src.port_checker import check_ports
from src.latency_monitor import analyze_stability
from src.diagnostics import run_diagnostics
from src.health_score import calculate_health_score
from src.history import save_diagnostic_run
from src.models import DiagnosticResult

def run_full_pipeline(quick=False, custom_domains=None, custom_host=None, custom_ports=None, is_demo=False, demo_scenario=None) -> DiagnosticResult:
    """Executes the full diagnostic pipeline and returns a unified DiagnosticResult."""
    start_time = time.time()
    
    if is_demo:
        return _get_demo_data(demo_scenario, start_time)

    logger.info(f"Starting diagnostic pipeline (quick={quick})")
    
    data = {"is_demo": False}
    data["system"] = get_system_info()
    data["interfaces"] = get_interfaces()
    
    gw_info = get_default_gateway()
    if gw_info["gateway"] != "NOT DETECTED":
        data["gateway"] = check_gateway_connectivity(gw_info["gateway"])
    else:
        data["gateway"] = {"address": "NOT DETECTED", "reachable": False, "packet_loss": 100.0, "latency_ms": 0.0}
        
    data["routes"] = get_routing_info()
    data["internet"] = check_internet_connectivity()
    data["stability"] = analyze_stability(data["internet"])
    
    domains = custom_domains if custom_domains else ["google.com", "github.com"]
    if quick: domains = domains[:1]
    data["dns"] = check_dns(domains)
    
    host = custom_host if custom_host else "google.com"
    ports = custom_ports if custom_ports else [443, 80]
    if quick: ports = [443]
    data["tcp"] = check_ports(host, ports)
    
    data["diagnostics"] = run_diagnostics(data)
    health = calculate_health_score(data)
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    result = DiagnosticResult(
        timestamp=datetime.now().isoformat(),
        health_score=health["score"],
        status=health["status"],
        system=data["system"],
        interfaces=data["interfaces"],
        gateway=data["gateway"],
        internet=data["internet"],
        dns=data["dns"],
        tcp=data["tcp"],
        routes=data["routes"],
        stability=data["stability"],
        diagnostics=data["diagnostics"],
        duration_ms=duration_ms,
        is_demo=False
    )
    
    save_diagnostic_run(result)
    return result

def _get_demo_data(scenario, start_time) -> DiagnosticResult:
    logger.info(f"Running in DEMO mode: {scenario}")
    
    data = {
        "is_demo": True,
        "system": {"hostname": "DEMO-PC", "os": "Linux", "os_version": "5.15", "python_version": "3.11", "local_ip": "192.168.1.50", "architecture": "x86_64", "timestamp": datetime.now().isoformat()},
        "interfaces": [{"name": "eth0", "ipv4": "192.168.1.50", "state": "UP", "bytes_sent": 1024000, "bytes_recv": 2048000}],
        "gateway": {"address": "192.168.1.1", "reachable": True, "latency_ms": 2.0, "packet_loss": 0.0},
        "internet": {"target": "8.8.8.8", "reachable": True, "latency_ms": 15.0, "packet_loss": 0.0, "min_latency_ms": 14.0, "max_latency_ms": 18.0},
        "dns": [{"domain": "google.com", "success": True, "response_ms": 23.0}, {"domain": "github.com", "success": True, "response_ms": 25.0}],
        "tcp": [{"host": "google.com", "port": 443, "success": True, "response_ms": 40.0}, {"host": "google.com", "port": 80, "success": True, "response_ms": 38.0}],
        "routes": {"default_route": "default via 192.168.1.1 dev eth0"}
    }

    if scenario == "dns-failure":
        data["dns"][0]["success"] = False
        data["dns"][1]["success"] = False
    elif scenario == "gateway-failure":
        data["gateway"]["reachable"] = False
        data["internet"]["reachable"] = False
        data["dns"][0]["success"] = False
        data["dns"][1]["success"] = False
        data["tcp"][0]["success"] = False
        data["tcp"][1]["success"] = False
    elif scenario == "port-failure":
        data["tcp"][0]["success"] = False
        data["tcp"][1]["success"] = False
    elif scenario == "packet-loss":
        data["internet"]["packet_loss"] = 15.0
    elif scenario == "high-latency":
        data["internet"]["latency_ms"] = 250.0

    data["stability"] = analyze_stability(data["internet"])
    data["diagnostics"] = run_diagnostics(data)
    health = calculate_health_score(data)
    
    duration_ms = int((time.time() - start_time) * 1000)

    result = DiagnosticResult(
        timestamp=datetime.now().isoformat(),
        health_score=health["score"],
        status=health["status"],
        system=data["system"],
        interfaces=data["interfaces"],
        gateway=data["gateway"],
        internet=data["internet"],
        dns=data["dns"],
        tcp=data["tcp"],
        routes=data["routes"],
        stability=data["stability"],
        diagnostics=data["diagnostics"],
        duration_ms=duration_ms,
        is_demo=True
    )
    
    # We do not save demo data to history to keep it clean.
    return result
