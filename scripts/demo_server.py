"""A local NetSentinel with realistic data in memory, for screenshots and demos.

Running the app locally against no database leaves every data-driven page
empty — no history, no telemetry, no incidents — which is useless for
documentation. This starts the real backend against an in-memory store
seeded with a plausible three-machine fleet, so every page renders the way
it does for an account that has been running for a while.

Nothing here touches Supabase, and the data is synthetic, so README captures
made from it contain no real hostnames or addresses.

    python scripts/demo_server.py            # http://localhost:8000
    python scripts/demo_server.py --port 8100

Point the frontend at it:

    NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
"""
import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before src.api is imported: the gate is off so a seeded browser
# session is enough, while the database paths stay switched on.
os.environ["AUTH_REQUIRED"] = "0"
for _var in ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
             "SUPABASE_SECRET_KEY", "SUPABASE_PUBLISHABLE_KEY",
             "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"):
    os.environ.pop(_var, None)

from scripts.verify_add_device_e2e import FakeSupabase  # noqa: E402  (shared fake)

# Whatever the disabled gate resolves every request to.
USER_ID = "local-dev-user"
NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def seed(store: FakeSupabase) -> None:
    """A fleet that looks like it has been monitored for a day."""
    random.seed(7)  # same picture every run

    fleet = [
        {
            "name": "OPS-LAPTOP-04", "hostname": "OPS-LAPTOP-04", "platform": "Windows",
            "architecture": "AMD64", "ip": "10.20.4.37", "gateway": "10.20.4.1",
            "backup": False, "protocols": None, "latency": 24.0, "loss_chance": 0.04,
            "minutes_since_report": 0,
        },
        {
            "name": "BACKUP-NAS-01", "hostname": "BACKUP-NAS-01", "platform": "Linux",
            "architecture": "x86_64", "ip": "10.20.4.58", "gateway": "10.20.4.1",
            "backup": True, "protocols": ["NFS", "SMB"], "latency": 8.0, "loss_chance": 0.0,
            "minutes_since_report": 0,
        },
        {
            "name": "BUILD-SERVER-02", "hostname": "BUILD-SERVER-02", "platform": "Linux",
            "architecture": "x86_64", "ip": "10.20.4.71", "gateway": "10.20.4.1",
            "backup": True, "protocols": ["iSCSI", "Replication"], "latency": 11.0,
            "loss_chance": 0.02, "minutes_since_report": 0,
        },
    ]

    devices, telemetry = [], []
    for spec in fleet:
        device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, spec["hostname"]))
        devices.append({
            "id": device_id, "user_id": USER_ID, "name": spec["name"], "hostname": spec["hostname"],
            "platform": spec["platform"], "architecture": spec["architecture"],
            "ip_address": spec["ip"], "agent_version": "1.1.0", "status": "ONLINE",
            "is_backup_target": spec["backup"], "backup_protocols": spec["protocols"],
            "last_seen": iso(NOW - timedelta(minutes=spec["minutes_since_report"])),
            "created_at": iso(NOW - timedelta(days=6)), "updated_at": iso(NOW),
        })

        # Two hours of minute-by-minute reports: enough for a real baseline
        # (20+ samples) and a trend graph with shape to it.
        for minutes_ago in range(120, spec["minutes_since_report"] - 1, -1):
            jitter = random.gauss(0, spec["latency"] * 0.18)
            spike = random.random() < 0.05
            latency = max(1.0, spec["latency"] + jitter + (spec["latency"] * 1.6 if spike else 0))
            loss = 25.0 if random.random() < spec["loss_chance"] else 0.0
            ports = []
            if spec["protocols"]:
                port_map = {"NFS": 2049, "SMB": 445, "iSCSI": 3260, "Replication": 10000}
                for name, port in port_map.items():
                    ports.append({"port": port, "service": name,
                                  "open": name in (spec["protocols"] or [])})
            telemetry.append({
                "id": str(uuid.uuid4()), "device_id": device_id,
                "timestamp": iso(NOW - timedelta(minutes=minutes_ago)),
                "latency_ms": round(latency, 1), "packet_loss": loss,
                "gateway_reachable": True, "internet_reachable": True,
                "dns_healthy": True, "tcp_healthy": True,
                "interface_errors": 0, "interface_drops": 0,
                "gateway_ip": spec["gateway"], "backup_ports": ports or None,
            })

    # Saved diagnostic runs behind the History page and the latency trend.
    history = []
    # `history.id` is a serial integer, not a UUID, and get_history_supabase
    # orders by it — so newest must carry the highest id.
    for index, hours_ago in enumerate(range(24, -1, -1)):
        latency = round(random.uniform(6.5, 12.0), 1)
        score = random.choice([100, 100, 97, 97, 94])
        history.append({
            "id": index + 1, "user_id": USER_ID,
            "timestamp": iso(NOW - timedelta(hours=hours_ago)),
            "score": score, "status": "HEALTHY" if score >= 90 else "WARNING",
            "gateway_status": "PASS", "internet_status": "PASS",
            "dns_status": "PASS", "tcp_status": "PASS",
            "latency": latency, "packet_loss": 0.0, "is_demo": False,
        })

    nas_id = devices[1]["id"]
    incidents = [
        {
            "id": str(uuid.uuid4()), "user_id": USER_ID, "device_id": devices[0]["id"],
            "title": "Packet Loss Anomaly Detected", "severity": "warning", "status": "OPEN",
            "likely_cause": "Packet loss 25.0% (1 of 4 probes), and loss also occurred in 2 of the previous 4 report(s).",
            "confidence": "MEDIUM",
            "evidence": ["Packet loss 25.0% (1 of 4 probes)",
                         "Loss recurred in 2 of the previous 4 reports",
                         "Baseline loss for this device is 0.4%"],
            "recommended_actions": ["Check wireless signal strength, or try a wired connection.",
                                    "Compare against another machine on the same network."],
            "started_at": iso(NOW - timedelta(minutes=18)),
        },
        {
            "id": str(uuid.uuid4()), "user_id": USER_ID, "device_id": nas_id,
            "title": "Latency Anomaly Detected", "severity": "warning", "status": "RESOLVED",
            "likely_cause": "Current latency (68.0ms) is significantly above baseline (8.2ms).",
            "confidence": "MEDIUM",
            "evidence": ["Latency 68 ms against a 95th percentile of 14 ms over 118 measurements"],
            "recommended_actions": ["Check whether anything on the network is transferring heavily."],
            "started_at": iso(NOW - timedelta(hours=5)),
            "resolved_at": iso(NOW - timedelta(hours=4, minutes=20)),
        },
    ]

    store.tables["devices"] = devices
    store.tables["telemetry"] = telemetry
    store.tables["history"] = history
    store.tables["incidents"] = incidents
    store.tables["agent_tokens"] = [{"user_id": USER_ID, "token": "nsa_demo_token_not_a_real_credential"}]
    print(f"Seeded {len(devices)} devices, {len(telemetry)} telemetry rows, "
          f"{len(history)} diagnostic runs, {len(incidents)} incidents.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import src.database as database
    store = FakeSupabase()
    database.get_supabase = lambda: store
    database.is_database_configured = lambda: True

    import src.alert_engine, src.anomaly_engine, src.api, src.auth  # noqa: E402
    import src.baseline_engine, src.enrollment, src.history, src.incident_engine  # noqa: E402
    import src.user_profile  # noqa: E402

    for module in (src.api, src.auth, src.enrollment, src.history, src.baseline_engine,
                   src.incident_engine, src.alert_engine, src.user_profile):
        module.get_supabase = lambda: store
        module.is_database_configured = lambda: True

    seed(store)
    src.baseline_engine.update_device_baselines(store.tables["devices"][0]["id"], user_id=USER_ID)

    import uvicorn
    print(f"Demo API on http://localhost:{args.port} — synthetic data, no Supabase.")
    uvicorn.run(src.api.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
