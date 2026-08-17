import platform
import socket
import subprocess
import re
from src.logger import logger

def get_interfaces():
    """Gathers network interface information."""
    logger.info("Gathering network interfaces...")
    interfaces = []

    try:
        import psutil
        logger.info("psutil found. Gathering detailed interface statistics.")
        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()
        net_io_counters = psutil.net_io_counters(pernic=True)

        for interface_name, addrs in net_if_addrs.items():
            ipv4 = ""
            mac = ""
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                elif addr.family == psutil.AF_LINK:
                    mac = addr.address

            state = "UNKNOWN"
            if interface_name in net_if_stats:
                state = "UP" if net_if_stats[interface_name].isup else "DOWN"

            stats = net_io_counters.get(interface_name)
            
            interfaces.append({
                "name": interface_name,
                "ipv4": ipv4,
                "mac": mac,
                "state": state,
                "bytes_sent": stats.bytes_sent if stats else 0,
                "bytes_recv": stats.bytes_recv if stats else 0,
                "packets_sent": stats.packets_sent if stats else 0,
                "packets_recv": stats.packets_recv if stats else 0,
                "errin": stats.errin if stats else 0,
                "errout": stats.errout if stats else 0,
                "dropin": stats.dropin if stats else 0,
                "dropout": stats.dropout if stats else 0,
            })
            
    except ImportError:
        logger.info("psutil not found. Using fallback interface gathering.")
        # Fallback mechanism for basic info (mostly IP and names)
        # This will be very basic compared to psutil, mainly finding active interfaces
        os_name = platform.system()
        if os_name == "Windows":
            interfaces = _get_interfaces_windows_fallback()
        elif os_name == "Linux":
            interfaces = _get_interfaces_linux_fallback()
        else:
            logger.warning(f"OS {os_name} not fully supported for fallback interface gathering.")

    return interfaces

def _get_interfaces_windows_fallback():
    interfaces = []
    try:
        output = subprocess.check_output(["ipconfig", "/all"], text=True, encoding="cp437", errors="ignore")
        current_iface = None
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            
            if line.endswith(":") and not line.startswith(" "):
                if current_iface and current_iface.get("ipv4"):
                    interfaces.append(current_iface)
                current_iface = {"name": line[:-1].strip(), "ipv4": "", "state": "UP"}
            
            if current_iface is not None:
                if "IPv4 Address" in line or "IP Address" in line:
                    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                    if match:
                        current_iface["ipv4"] = match.group(1)
                elif "Media State" in line and "disconnected" in line.lower():
                    current_iface["state"] = "DOWN"
        
        if current_iface and current_iface.get("ipv4"):
            interfaces.append(current_iface)

    except Exception as e:
        logger.error(f"Fallback Windows interface gathering failed: {e}")
    return interfaces

def _get_interfaces_linux_fallback():
    interfaces = []
    try:
        output = subprocess.check_output(["ip", "-o", "addr", "show"], text=True)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                iface_name = parts[1]
                ip_cidr = parts[3]
                ipv4 = ip_cidr.split("/")[0]
                interfaces.append({
                    "name": iface_name,
                    "ipv4": ipv4,
                    "state": "UP"
                })
    except Exception as e:
        logger.error(f"Fallback Linux interface gathering failed: {e}")
    return interfaces
