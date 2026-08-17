import platform
import subprocess
import re
from src.logger import logger

def get_default_gateway():
    """Detects the default gateway."""
    logger.info("Detecting default gateway...")
    os_name = platform.system()
    gateway = "NOT DETECTED"

    try:
        if os_name == "Windows":
            output = subprocess.check_output(["route", "print", "0.0.0.0"], text=True, encoding="cp437", errors="ignore")
            # Look for line starting with 0.0.0.0
            for line in output.splitlines():
                if line.strip().startswith("0.0.0.0"):
                    parts = line.split()
                    if len(parts) >= 3:
                        gateway = parts[2]
                        break
        elif os_name == "Linux":
            output = subprocess.check_output(["ip", "route"], text=True)
            for line in output.splitlines():
                if line.startswith("default via"):
                    parts = line.split()
                    if len(parts) >= 3:
                        gateway = parts[2]
                        break
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
