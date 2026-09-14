import os
import socket
import time
from src.logger import logger

# --- Backup Readiness: protocol ports checked on hosts tagged as backup targets ---
# Fixed backup-protocol ports plus one configurable "generic replication" port
# (e.g. a vendor-specific replication/DR service) sourced from the
# BACKUP_REPLICATION_PORT env var, defaulting to 10000 if unset.
BACKUP_PORT_SERVICES = {
    2049: "NFS",
    445: "SMB",
    3260: "iSCSI",
}

DEFAULT_REPLICATION_PORT = int(os.environ.get("BACKUP_REPLICATION_PORT", 10000))


def get_backup_port_map(replication_port=None):
    """Returns the full {port: service_name} map used for backup-readiness
    checks, including the configurable generic replication port."""
    ports = dict(BACKUP_PORT_SERVICES)
    repl_port = int(replication_port) if replication_port else DEFAULT_REPLICATION_PORT
    ports[repl_port] = "Replication"
    return ports


def check_backup_ports(target_host, replication_port=None):
    """Checks all backup-relevant ports (NFS, SMB, iSCSI, + configurable
    replication port) on a host. Reuses check_ports() — this is not a
    parallel scanning mechanism, just a curated port list for backup targets.

    Returns a list of {"port", "service", "open", "response_ms"} dicts.
    """
    port_map = get_backup_port_map(replication_port)
    raw_results = check_ports(target_host, list(port_map.keys()))
    return [
        {
            "port": r["port"],
            "service": port_map.get(r["port"], "Unknown"),
            "open": r["success"],
            "response_ms": r["response_ms"],
        }
        for r in raw_results
    ]


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
