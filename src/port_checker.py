import socket
import time
from src.logger import logger

def check_ports(target_host, ports=None):
    """Checks TCP connectivity to specific ports."""
    if ports is None:
        ports = [443, 80]
        
    logger.info(f"Checking TCP ports {ports} on {target_host}...")
    results = []

    for port in ports:
        result = {
            "host": target_host,
            "port": int(port),
            "success": False,
            "response_ms": 0.0
        }
        
        start_time = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect((target_host, int(port)))
            result["success"] = True
        except (socket.timeout, ConnectionRefusedError, socket.gaierror) as e:
            logger.warning(f"Port {port} on {target_host} is closed or unreachable: {e}")
        except Exception as e:
            logger.error(f"Unexpected error checking port {port} on {target_host}: {e}")
        finally:
            s.close()
            
        result["response_ms"] = round((time.time() - start_time) * 1000, 2)
        results.append(result)
        logger.info(f"Port result {target_host}:{port} -> {'OPEN' if result['success'] else 'CLOSED'} ({result['response_ms']}ms)")

    return results
