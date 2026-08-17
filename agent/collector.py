from datetime import datetime, timezone
import sys
from pathlib import Path

# Add parent directory to path so we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

from src.gateway_monitor import get_default_gateway
from src.connectivity import check_gateway_connectivity, check_internet_connectivity
from src.interface_monitor import get_interfaces
from src.dns_monitor import check_dns
from src.port_checker import check_ports

def collect_telemetry(device_id: str) -> dict:
    """Collects system network state using the core NetSentinel modules."""
    
    # 1. Gateway
    gw_info = get_default_gateway()
    gateway_ip = gw_info.get("gateway") if isinstance(gw_info, dict) else gw_info
    
    gateway_reachable = False
    gateway_latency = 0.0
    gateway_loss = 0.0
    
    if gateway_ip and gateway_ip != "NOT DETECTED":
        res_gw = check_gateway_connectivity(gateway_ip)
        gateway_reachable = res_gw.get("reachable", False)
        gateway_latency = res_gw.get("latency_ms", 0.0)
        gateway_loss = res_gw.get("packet_loss", 100.0)
        
    # 2. Internet
    res_int = check_internet_connectivity("8.8.8.8")
    int_reachable = res_int.get("reachable", False)
    int_loss = res_int.get("packet_loss", 100.0)
    int_latency = res_int.get("latency_ms", 0.0)
    
    # 3. DNS
    dns_healthy = False
    if int_reachable:
        dns_res = check_dns(["google.com"])
        dns_healthy = any(res.get("success", False) for res in dns_res) if dns_res else False
        
    # 4. TCP
    tcp_healthy = False
    if dns_healthy:
        tcp_res = check_ports("google.com", [443])
        tcp_healthy = any(res.get("success", False) for res in tcp_res) if tcp_res else False
        
    # 5. Interfaces
    interfaces = get_interfaces()
    interface_errors = sum(iface.get("errin", 0) + iface.get("errout", 0) for iface in interfaces)
    interface_drops = sum(iface.get("dropin", 0) + iface.get("dropout", 0) for iface in interfaces)
    
    return {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_ms": int_latency,
        "packet_loss": int_loss,
        "gateway_reachable": gateway_reachable,
        "internet_reachable": int_reachable,
        "dns_healthy": dns_healthy,
        "tcp_healthy": tcp_healthy,
        "interface_errors": interface_errors,
        "interface_drops": interface_drops
    }
