import platform
import socket
import sys
from datetime import datetime
from src.logger import logger

def get_system_info():
    """Gathers and returns basic system information."""
    logger.info("Gathering system information...")
    try:
        hostname = socket.gethostname()
    except Exception as e:
        logger.warning(f"Could not get hostname: {e}")
        hostname = "UNKNOWN"

    try:
        # Dummy socket to get local IP used for internet routing
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # We don't actually need to connect
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        logger.warning(f"Could not determine local IP: {e}")
        local_ip = "127.0.0.1"

    info = {
        "hostname": hostname,
        "os": platform.system(),
        "os_version": platform.release(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "local_ip": local_ip,
        "architecture": platform.machine(),
        "timestamp": datetime.now().isoformat()
    }
    
    logger.info(f"System info collected: {info['os']} on {info['hostname']}")
    return info
