import platform
import subprocess
import re
from src.logger import logger

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _parse_windows_default_gateway(route_output: str):
    """Picks the default gateway with the lowest metric from `route print 0.0.0.0`.

    Machines with several adapters (VPN, Hyper-V/WSL/VirtualBox virtual
    switches, Wi-Fi + Ethernet) have several 0.0.0.0 routes; the first one
    printed is not necessarily the one Windows actually uses. Windows picks
    the lowest metric, so we do too. Columns are:
        Network Destination  Netmask  Gateway  Interface  Metric
    Only the IPv4 "Active Routes" section is considered; "Persistent Routes"
    (which can also list 0.0.0.0 with a different metric meaning) and the
    IPv6 section are skipped.
    """
    best_gateway, best_metric = None, None
    in_active = False
    for raw in route_output.splitlines():
        line = raw.strip()
        if line.startswith("Active Routes"):
            in_active = True
            continue
        if line.startswith("Persistent Routes") or line.startswith("IPv6 Route Table"):
            in_active = False
            continue
        if not in_active or not line.startswith("0.0.0.0"):
            continue
        parts = line.split()
        if len(parts) < 5 or not _IPV4_RE.match(parts[2]):
            continue  # e.g. "On-link" gateway or a truncated line
        try:
            metric = int(parts[4])
        except ValueError:
            continue
        if best_metric is None or metric < best_metric:
            best_gateway, best_metric = parts[2], metric
    return best_gateway, best_metric


def _parse_linux_default_gateway(ip_route_output: str):
    """Same idea for `ip route`: several `default via ...` lines are ranked by
    their `metric N` (absent metric counts as 0, matching the kernel)."""
    best_gateway, best_metric = None, None
    for raw in ip_route_output.splitlines():
        parts = raw.split()
        if len(parts) < 3 or parts[0] != "default" or parts[1] != "via":
            continue
        metric = 0
        if "metric" in parts:
            try:
                metric = int(parts[parts.index("metric") + 1])
            except (ValueError, IndexError):
                pass
        if best_metric is None or metric < best_metric:
            best_gateway, best_metric = parts[2], metric
    return best_gateway, best_metric


def get_default_gateway():
    """Detects the default gateway (lowest-metric default route)."""
    logger.info("Detecting default gateway...")
    os_name = platform.system()
    gateway = "NOT DETECTED"

    try:
        if os_name == "Windows":
            output = subprocess.check_output(["route", "print", "0.0.0.0"], text=True, encoding="cp437", errors="ignore")
            found, metric = _parse_windows_default_gateway(output)
        elif os_name == "Linux":
            output = subprocess.check_output(["ip", "route"], text=True)
            found, metric = _parse_linux_default_gateway(output)
        else:
            found, metric = None, None
        if found:
            gateway = found
            logger.info(f"Selected default route via {gateway} (metric {metric})")
    except Exception as e:
        logger.error(f"Error detecting gateway: {e}")

    logger.info(f"Default gateway detected: {gateway}")
    return {"gateway": gateway}

def get_routing_info():
    """Gets basic routing info for display."""
    os_name = platform.system()
    routing_info = "Not available"
    try:
        if os_name == "Windows":
            output = subprocess.check_output(["route", "print"], text=True, encoding="cp437", errors="ignore")
            lines = output.splitlines()
            # Try to grab just the Active Routes part
            active_routes = []
            capture = False
            for line in lines:
                if "Active Routes:" in line:
                    capture = True
                    continue
                if "Persistent Routes:" in line:
                    break
                if capture and line.strip():
                    active_routes.append(line)
            routing_info = "\n".join(active_routes[:5]) # just show first few lines
        elif os_name == "Linux":
            output = subprocess.check_output(["ip", "route"], text=True)
            routing_info = "\n".join(output.splitlines()[:5])
    except Exception:
        pass
    return {"default_route": routing_info}
