import platform
import subprocess
import re
from src.logger import logger

def ping_host(host, count=4):
    """Pings a host and returns latency, packet loss, and status."""
    logger.info(f"Pinging {host}...")
    os_name = platform.system()
    
    result = {
        "target": host,
        "reachable": False,
        "packet_loss": 100.0,
        "latency_ms": 0.0,
        "min_latency_ms": 0.0,
        "max_latency_ms": 0.0,
    }

    if host == "NOT DETECTED" or not host:
        return result

    try:
        if os_name == "Windows":
            cmd = ["ping", "-n", str(count), "-w", "2000", host]
        else:
            cmd = ["ping", "-c", str(count), "-W", "2", host]

        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, errors="ignore", encoding="cp437" if os_name == "Windows" else "utf-8")
        result["reachable"] = True

        # Parse packet loss
        if os_name == "Windows":
            loss_match = re.search(r"\((\d+)% loss", output)
            if loss_match:
                result["packet_loss"] = float(loss_match.group(1))
            
            # Parse latency
            lat_match = re.search(r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms", output)
            if lat_match:
                result["min_latency_ms"] = float(lat_match.group(1))
                result["max_latency_ms"] = float(lat_match.group(2))
                result["latency_ms"] = float(lat_match.group(3))
        else:
            loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
            if loss_match:
                result["packet_loss"] = float(loss_match.group(1))
            
            lat_match = re.search(r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/", output)
            if lat_match:
                result["min_latency_ms"] = float(lat_match.group(1))
                result["latency_ms"] = float(lat_match.group(2))
                result["max_latency_ms"] = float(lat_match.group(3))

        if result["packet_loss"] == 100.0:
             result["reachable"] = False

    except subprocess.CalledProcessError as e:
        logger.warning(f"Ping to {host} failed.")
        # Try to extract packet loss even if failed
        output = e.output
        if output:
            if os_name == "Windows":
                loss_match = re.search(r"\((\d+)% loss", output)
                if loss_match:
                    result["packet_loss"] = float(loss_match.group(1))
            else:
                loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
                if loss_match:
                    result["packet_loss"] = float(loss_match.group(1))
    except Exception as e:
        logger.error(f"Unexpected error pinging {host}: {e}")

    logger.info(f"Ping result for {host}: reachable={result['reachable']}, loss={result['packet_loss']}%, latency={result['latency_ms']}ms")
    return result

def check_gateway_connectivity(gateway_ip):
    res = ping_host(gateway_ip)
    # Rename 'target' to 'address' for gateway
    res['address'] = res.pop('target')
    return res

def check_internet_connectivity(internet_ip="8.8.8.8"):
    return ping_host(internet_ip)
