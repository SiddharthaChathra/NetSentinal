import socket
import time
from src.logger import logger

def check_dns(domains):
    """Checks DNS resolution for a list of domains."""
    results = []
    logger.info(f"Checking DNS for: {domains}")
    for domain in domains:
        result = {
            "domain": domain,
            "success": False,
            "resolved_ip": None,
            "response_ms": 0.0
        }
        start_time = time.time()
        try:
            # socket.getaddrinfo returns a list of tuples: (family, type, proto, canonname, sockaddr)
            addr_info = socket.getaddrinfo(domain, None)
            if addr_info:
                result["resolved_ip"] = addr_info[0][4][0]
                result["success"] = True
        except socket.gaierror:
            logger.warning(f"DNS resolution failed for {domain}")
        except Exception as e:
            logger.error(f"Unexpected DNS error for {domain}: {e}")
        
        result["response_ms"] = round((time.time() - start_time) * 1000, 2)
        results.append(result)
        logger.info(f"DNS result for {domain}: {result['success']} ({result['resolved_ip']}) in {result['response_ms']}ms")
        
    return results
