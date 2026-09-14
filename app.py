import argparse
import sys
import json
import uvicorn
from src.logger import logger
from src.aggregator import run_full_pipeline
from src.reporter import print_terminal_report, print_json_report

def main():
    parser = argparse.ArgumentParser(description="NetSentinel - Intelligent Network Troubleshooting Platform")
    parser.add_argument("--quick", action="store_true", help="Run a quick diagnostic check")
    parser.add_argument("--domain", type=str, default="google.com,github.com", help="Comma-separated domains to check")
    parser.add_argument("--host", type=str, default="google.com", help="Host for TCP checks")
    parser.add_argument("--port", type=str, default="443,80", help="Comma-separated TCP ports to check")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--demo", type=str, choices=["healthy", "dns-failure", "gateway-failure", "port-failure", "high-latency", "packet-loss"], help="Run a simulated demo scenario")
    parser.add_argument("--web", action="store_true", help="Start the NetSentinel web dashboard")
    parser.add_argument("--backup-target", type=str, help="Hostname/IP of a backup target to check for backup readiness (NFS/SMB/iSCSI/replication ports + SLA estimate)")
    parser.add_argument("--dataset-size-gb", type=float, default=500.0, help="Assumed backup dataset size in GB, used for SLA estimation (default: 500)")
    parser.add_argument("--sla-hours", type=float, default=4.0, help="Backup SLA window in hours (default: 4)")
    parser.add_argument("--backup-scenario", type=str, choices=["dns-flap", "port-blocked", "throughput-drop"], help="Run a simulated backup-readiness failure scenario instead of a live check")

    args = parser.parse_args()

    if args.web:
        logger.info("Starting NetSentinel Web Dashboard via Uvicorn...")
        print("Starting NetSentinel Web Dashboard at http://localhost:8000")
        uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=False)
        return

    logger.info("Starting NetSentinel CLI run")
    
    domains = [d.strip() for d in args.domain.split(",")] if args.domain else None
    ports = [int(p.strip()) for p in args.port.split(",")] if args.port else None
    
    if args.demo:
        result = run_full_pipeline(is_demo=True, demo_scenario=args.demo)
    else:
        result = run_full_pipeline(quick=args.quick, custom_domains=domains, custom_host=args.host, custom_ports=ports)
    
    # We convert Pydantic model to dict for reporter
    data = result.model_dump()

    # Backup Readiness is purely additive: these keys are only present when
    # explicitly requested, so existing consumers of the default report shape
    # (--json or terminal) are unaffected when neither flag is passed.
    if args.backup_scenario:
        from src.backup_readiness import run_simulated_backup_scenario
        data["backup_simulated_scenario"] = run_simulated_backup_scenario(
            args.backup_scenario, args.dataset_size_gb, args.sla_hours
        )
    elif args.backup_target:
        from src.backup_readiness import check_backup_target
        from src.models import Device
        adhoc_device = Device(
            id="cli-backup-target",
            name=args.backup_target,
            hostname=args.backup_target,
            platform="unknown",
            architecture="unknown",
            ip_address=args.backup_target,
            agent_version="cli",
            status="UNKNOWN",
            is_backup_target=True,
        )
        target_status, backup_diags = check_backup_target(
            adhoc_device, args.dataset_size_gb, args.sla_hours
        )
        data["backup_readiness"] = target_status
        data["backup_diagnostics"] = backup_diags

    if args.json:
        print_json_report(data)
    else:
        if args.demo:
            print("\n*** RUNNING IN SIMULATED/DEMO MODE ***")
        print_terminal_report(data)
        
    logger.info("NetSentinel run complete.")

if __name__ == "__main__":
    main()
