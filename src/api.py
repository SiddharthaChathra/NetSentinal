from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import os
import re
import time
import uuid

from src.models import (
    DiagnosticResult, HistoryEntry, Device, Telemetry,
    DiagnosticRun, Incident, Alert, Baseline, BackupReadinessReport
)
from src.aggregator import run_full_pipeline
from src.history import get_history, save_diagnostic_run_supabase, get_history_supabase
from src.auth import (
    get_current_user, get_optional_user, verify_agent_token, AgentIdentity,
    get_or_create_agent_token, rotate_agent_token,
)
from src.database import get_supabase, is_database_configured, database_access_mode
from src.backup_readiness import (
    build_backup_readiness_report, run_simulated_backup_scenario, simulate_backup_target
)
from src.logger import logger

def _get_user_id(user) -> Optional[str]:
    if not user:
        return None
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)

app = FastAPI(title="NetSentinel Platform API", description="Network Observability Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Legacy & CLI Endpoints (Preserved) ---

_last_runs: Dict[str, Optional[DiagnosticResult]] = {}
_MAX_GUEST_RUNS = 500
_GUEST_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")

def _run_key(user, guest_session: Optional[str]) -> str:
    """Authenticated users are keyed by uid. Guests are keyed by the
    X-Guest-Session header the frontend generates per browser tab, so one
    anonymous visitor's run is never served to another. A guest that sends
    no (or a malformed) header falls back to a shared slot, which is the
    pre-existing behaviour for non-browser clients like curl."""
    uid = _get_user_id(user)
    if uid:
        return f"user:{uid}"
    if guest_session and _GUEST_SESSION_RE.match(guest_session):
        return f"guest:{guest_session}"
    return "guest"

def _get_user_run(user, guest_session: Optional[str] = None) -> Optional[DiagnosticResult]:
    return _last_runs.get(_run_key(user, guest_session))

def _set_user_run(user, result: DiagnosticResult, guest_session: Optional[str] = None):
    key = _run_key(user, guest_session)
    # Bound memory: drop the oldest guest slots once we hold too many.
    if key not in _last_runs and len(_last_runs) >= _MAX_GUEST_RUNS:
        for old in [k for k in _last_runs if k.startswith("guest:")][: len(_last_runs) - _MAX_GUEST_RUNS + 1]:
            _last_runs.pop(old, None)
    _last_runs[key] = result

def _guest_session_dep(x_guest_session: Optional[str] = Header(default=None)) -> Optional[str]:
    return x_guest_session

_DB_PROBE_TTL_S = 60
_db_probe_cache = {"at": 0.0, "status": "unconfigured"}

def _database_status() -> str:
    """Actually probes the database (cached for 60s) instead of reporting
    'connected' just because credentials are present in the environment."""
    if not is_database_configured():
        return "unconfigured"
    now = time.monotonic()
    if now - _db_probe_cache["at"] < _DB_PROBE_TTL_S:
        return _db_probe_cache["status"]
    try:
        get_supabase().table("devices").select("id").limit(1).execute()
        status_str = "connected"
    except Exception as e:
        logger.warning(f"Database probe failed: {e}")
        status_str = "unreachable"
    _db_probe_cache.update(at=now, status=status_str)
    return status_str

@app.get("/api/health")
def api_health():
    return {"status": "healthy", "database": _database_status(), "database_access": database_access_mode(), "version": "2.0.0"}

VALID_DEMO_SCENARIOS = {"healthy", "dns-failure", "gateway-failure", "port-failure", "high-latency", "packet-loss"}
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

def _parse_ports(port: Optional[str]) -> Optional[List[int]]:
    if not port:
        return None
    ports = []
    for raw in port.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if not raw.isdigit() or not (1 <= int(raw) <= 65535):
            raise HTTPException(status_code=400, detail=f"Invalid port '{raw}': must be an integer between 1 and 65535")
        ports.append(int(raw))
    if len(ports) > 20:
        raise HTTPException(status_code=400, detail="At most 20 ports may be checked per run")
    return ports or None

def _parse_hosts(value: Optional[str], what: str, max_items: int) -> Optional[List[str]]:
    if not value:
        return None
    hosts = [h.strip() for h in value.split(",") if h.strip()]
    for h in hosts:
        if not _HOSTNAME_RE.match(h):
            raise HTTPException(status_code=400, detail=f"Invalid {what} '{h}'")
    if len(hosts) > max_items:
        raise HTTPException(status_code=400, detail=f"At most {max_items} {what}s may be checked per run")
    return hosts or None

@app.post("/api/diagnostic-run", response_model=DiagnosticResult)
def perform_diagnostic_run(
    mode: Optional[str] = None,
    demo: Optional[str] = None,
    domain: Optional[str] = "google.com,github.com",
    host: Optional[str] = "google.com",
    port: Optional[str] = "443,80",
    user = Depends(get_optional_user),
    guest_session: Optional[str] = Depends(_guest_session_dep),
):
    quick = mode == "quick"

    if demo:
        if demo not in VALID_DEMO_SCENARIOS:
            raise HTTPException(status_code=400, detail=f"Unknown demo scenario '{demo}'. Valid: {sorted(VALID_DEMO_SCENARIOS)}")
        result = run_full_pipeline(is_demo=True, demo_scenario=demo)
    else:
        domains = _parse_hosts(domain, "domain", 10)
        hosts = _parse_hosts(host, "host", 1)
        ports = _parse_ports(port)
        result = run_full_pipeline(
            quick=quick, custom_domains=domains, custom_host=hosts[0] if hosts else None, custom_ports=ports
        )

    _set_user_run(user, result, guest_session)

    uid = _get_user_id(user)
    if uid and not result.is_demo:
        save_diagnostic_run_supabase(result, uid)

    return result

@app.get("/api/diagnostic-run", response_model=Optional[DiagnosticResult])
def get_last_diagnostic_run(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    return _get_user_run(user, guest_session)

@app.get("/api/export")
def export_last_run(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    return _get_user_run(user, guest_session)

# Legacy component endpoints
@app.get("/api/system")
def get_system(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.system if run else {}

@app.get("/api/interfaces")
def get_interfaces(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.interfaces if run else []

@app.get("/api/gateway")
def get_gateway(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.gateway if run else {}

@app.get("/api/internet")
def get_internet(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.internet if run else {}

@app.get("/api/dns")
def get_dns(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.dns if run else []

@app.get("/api/tcp")
def get_tcp(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.tcp if run else []

@app.get("/api/routes")
def get_routes(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.routes if run else {}

@app.get("/api/diagnostics")
def get_diagnostics(user = Depends(get_optional_user), guest_session: Optional[str] = Depends(_guest_session_dep)):
    run = _get_user_run(user, guest_session)
    return run.diagnostics if run else []

@app.get("/api/history", response_model=List[HistoryEntry])
def get_diagnostic_history(
    limit: int = 100,
    hours: Optional[float] = None,
    user = Depends(get_optional_user),
):
    """`hours` restricts results to runs newer than now-<hours> so the UI's
    1h/6h/24h/7d/30d range selector can be honoured server-side."""
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    if hours is not None and (hours <= 0 or hours > 24 * 366):
        raise HTTPException(status_code=400, detail="hours must be between 0 and 8784")
    uid = _get_user_id(user)
    if uid:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat() if hours else None
        return get_history_supabase(uid, limit, since=since)
    return []

# --- Local Device Info (No Auth Required) ---

def _get_local_device_info() -> dict:
    """Returns the real local machine's network identity."""
    from src.system_info import get_system_info
    from src.interface_monitor import get_interfaces
    sys_info = get_system_info()
    interfaces = get_interfaces()
    return {
        "hostname": sys_info.get("hostname", "UNKNOWN"),
        "platform": sys_info.get("os", "Unknown"),
        "os_version": sys_info.get("os_version", ""),
        "architecture": sys_info.get("architecture", "Unknown"),
        "ip_address": sys_info.get("local_ip", "127.0.0.1"),
        "interfaces": interfaces,
        "timestamp": sys_info.get("timestamp"),
    }

@app.get("/api/local-device")
def get_local_device():
    """Returns the current host's real network identity — no auth required."""
    return _get_local_device_info()

def _db_unavailable(action: str, e: Exception) -> HTTPException:
    logger.error(f"Database error during {action}: {e}")
    _db_probe_cache.update(at=time.monotonic(), status="unreachable")
    return HTTPException(status_code=503, detail=f"Database unavailable; could not {action}")

# --- Platform API: Devices ---

HOSTED_SERVER_AGENT_VERSION = "hosted-server"

def _hosted_server_device() -> Device:
    """The machine this API is running on, presented as a clearly-labelled
    demo device. It is what guests see and what the hosted "Run Diagnostic"
    actually measures — NOT the visitor's own network. `agent_version` is
    the marker the frontend uses to explain that and to point at the agent
    setup guide."""
    info = _get_local_device_info()
    return Device(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, info["hostname"])),
        name="NetSentinel Server (hosted demo)",
        hostname=info["hostname"],
        platform=info["platform"],
        architecture=info["architecture"],
        ip_address=info["ip_address"],
        agent_version=HOSTED_SERVER_AGENT_VERSION,
        status="ONLINE",
    )

def _fetch_devices(user) -> List[Device]:
    """Signed-in users get exactly their own registered devices — an empty
    list if they have none, so the UI can show the agent setup guide.
    Guests (or no database) get the hosted server as a labelled demo device.
    Shared by /api/devices and the backup-readiness endpoint."""
    uid = _get_user_id(user)
    if is_database_configured() and uid:
        try:
            res = get_supabase().table("devices").select("*").eq("user_id", uid).execute()
            return [Device(**d) for d in (res.data or [])]
        except Exception as e:
            # Never fall back to an un-scoped query here: that would hand one
            # user every other user's devices whenever the DB hiccups.
            logger.error(f"Device query for user {uid} failed: {e}")
            return []

    return [_hosted_server_device()]

@app.get("/api/devices", response_model=List[Device])
def get_devices(user = Depends(get_optional_user)):
    return _fetch_devices(user)

PUBLIC_API_BASE_URL = os.environ.get("PUBLIC_API_BASE_URL", "https://netsentinal.onrender.com")
REPO_URL = "https://github.com/SiddharthaChathra/NetSentinal"

def _personal_agent_token(uid: Optional[str]) -> Optional[str]:
    """The signed-in user's own agent token (created on first request).
    None for guests or when the database can't be reached."""
    if not uid or not is_database_configured():
        return None
    try:
        return get_or_create_agent_token(uid)
    except Exception as e:
        logger.error(f"Could not fetch/create agent token for user {uid}: {e}")
        return None

@app.get("/api/agent-token")
def get_agent_token(user = Depends(get_current_user)):
    """Returns the caller's personal agent token, creating it on first use."""
    uid = _get_user_id(user)
    if not is_database_configured():
        return {"token": None, "note": "No database configured; agents connect without a token locally."}
    try:
        return {"token": get_or_create_agent_token(uid)}
    except Exception as e:
        raise _db_unavailable("fetch agent token", e)

@app.post("/api/agent-token/rotate")
def rotate_my_agent_token(user = Depends(get_current_user)):
    """Issues a new token; the previous one stops working immediately.
    Every agent using the old token must be updated."""
    uid = _get_user_id(user)
    if not is_database_configured():
        return {"token": None}
    try:
        return {"token": rotate_agent_token(uid)}
    except Exception as e:
        raise _db_unavailable("rotate agent token", e)

@app.get("/api/setup")
def get_setup_guide(user = Depends(get_optional_user)):
    """Single source of truth for 'how do I monitor my own machine'. The
    frontend renders this so the instructions can never drift from what
    the backend actually needs. For a signed-in user the .env block is
    complete and copy-pasteable, including their personal agent token."""
    uid = _get_user_id(user)
    signed_in = uid is not None
    token = _personal_agent_token(uid)
    env_lines = [
        f"API_BASE_URL={PUBLIC_API_BASE_URL}",
        f"AGENT_TOKEN={token if token else '<sign in to get your personal token>'}",
    ]
    return {
        "user_id": uid,
        "agent_token": token,
        "hosted_mode": True,
        "hosted_mode_note": (
            "This site runs its diagnostics from the NetSentinel server, not from your computer. "
            "To monitor your own machines, install the lightweight agent on each one."
        ),
        "requires_sign_in": True,
        "signed_in": signed_in,
        "agent_token_required": True,
        "api_base_url": PUBLIC_API_BASE_URL,
        "repo_url": REPO_URL,
        "steps": [
            {
                "title": "Sign in",
                "body": "Devices are saved to your account, so sign in (or create an account) first.",
                "done": signed_in,
            },
            {
                "title": "Get the agent",
                "body": (
                    "On the machine you want to monitor, download NetSentinel and install the agent's "
                    "dependencies inside a virtual environment. Modern Ubuntu/Debian refuse a plain "
                    "`pip install` (\"externally-managed-environment\") — the venv step avoids that. "
                    "Requires git and Python 3.10+."
                ),
                "commands": [
                    f"git clone {REPO_URL}",
                    "cd NetSentinal",
                    "",
                    "# Linux / macOS  (if this fails on Ubuntu: sudo apt install python3-venv)",
                    "python3 -m venv .venv",
                    "source .venv/bin/activate",
                    "",
                    "# Windows (PowerShell)",
                    "python -m venv .venv",
                    ".venv\\Scripts\\Activate.ps1",
                    "",
                    "# then, on any OS:",
                    "pip install -r agent/requirements.txt",
                ],
            },
            {
                "title": "Point the agent at this server",
                "body": (
                    "Create a file named .env in the NetSentinal folder containing the two lines below. "
                    "The token is personal to your account — it is what links the device to you. "
                    "Keep it private; you can generate a new one from this page at any time."
                ),
                "commands": env_lines,
            },
            {
                "title": "Register and start",
                "body": (
                    "With the virtual environment still active, register the machine once, then leave "
                    "the agent running. It appears on the Devices page within a minute. "
                    "(If you open a new terminal later, re-run the activate command from step 2 first.)"
                ),
                "commands": [
                    "python agent/agent.py --register",
                    "python agent/agent.py --start",
                ],
            },
        ],
    }

@app.get("/api/devices/{device_id}", response_model=Device)
def get_device(device_id: str, user = Depends(get_current_user)):
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        res = get_supabase().table("devices").select("*").eq("id", device_id).eq("user_id", uid).execute()
        if res.data:
            return Device(**res.data[0])
    except Exception as e:
        raise _db_unavailable("fetch device", e)
    raise HTTPException(status_code=404, detail="Device not found")

@app.post("/api/devices/{device_id}/backup-target")
def set_backup_target(device_id: str, payload: Dict[str, Any], user = Depends(get_current_user)):
    """Tags (or untags) a monitored device as a backup target. Only tagged
    devices are included in /api/backup/readiness and run backup-specific
    checks (NFS/SMB/iSCSI/replication ports, SLA estimation)."""
    is_backup = bool(payload.get("is_backup_target", True))
    uid = _get_user_id(user)
    if is_database_configured() and uid:
        try:
            get_supabase().table("devices").update(
                {"is_backup_target": is_backup, "updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", device_id).eq("user_id", uid).execute()
        except Exception as e:
            logger.error(f"Failed to update backup-target tag for device {device_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update device")
    return {"id": device_id, "is_backup_target": is_backup}

# --- Platform API: Backup Readiness ---

@app.get("/api/backup/readiness", response_model=BackupReadinessReport)
def get_backup_readiness(
    dataset_size_gb: float = 500.0,
    sla_hours: float = 4.0,
    demo: Optional[str] = None,
    user = Depends(get_optional_user),
    guest_session: Optional[str] = Depends(_guest_session_dep),
):
    """Returns backup-readiness status for every device tagged as a backup
    target: reachability, DNS resolution, NFS/SMB/iSCSI/replication port
    checks, an SLA-fit estimate, and correlated diagnostics — matching the
    shared frontend data contract exactly.

    `dataset_size_gb` / `sla_hours` let the caller model their own backup
    job size and SLA window; defaults are a generic 500GB / 4h job.
    `demo` (dns-flap | port-blocked | throughput-drop) runs one simulated
    backup failure scenario through the real scoring pipeline, mirroring the
    existing /api/diagnostic-run?demo= convention — useful for previewing
    the UI without live backup infrastructure.
    """
    if dataset_size_gb < 0 or dataset_size_gb > 1_000_000:
        raise HTTPException(status_code=400, detail="dataset_size_gb must be between 0 and 1,000,000")
    if sla_hours <= 0 or sla_hours > 8760:
        raise HTTPException(status_code=400, detail="sla_hours must be between 0 and 8760")

    devices = _fetch_devices(user)
    last_run = _get_user_run(user, guest_session)
    health_score = last_run.health_score if last_run else 0

    report = build_backup_readiness_report(
        devices,
        health_score=health_score,
        dataset_size_gb=dataset_size_gb,
        sla_window_hours=sla_hours,
    )

    if demo:
        # Like /api/diagnostic-run?demo=, a demo yields a *complete* preview:
        # a synthetic target card, its correlated diagnostics, and the
        # scenario verdict — all produced by the real scoring/rule pipeline.
        try:
            demo_target, demo_diags = simulate_backup_target(demo, dataset_size_gb, sla_hours)
            scenario = run_simulated_backup_scenario(demo, dataset_size_gb, sla_hours)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        report["targets"].append(demo_target)
        report["diagnostics"].extend(demo_diags)
        report["simulated_scenarios"] = [scenario]
        report["backup_readiness_score"] = round(
            sum(t["backup_readiness"]["score"] for t in report["targets"]) / len(report["targets"])
        )

    return report

# --- Platform API: Incidents (Protected) ---

@app.get("/api/incidents", response_model=List[Incident])
def get_incidents(status: Optional[str] = None, user = Depends(get_optional_user)):
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        return []  # Guests see no incidents (they are session-local only)
    try:
        query = get_supabase().table("incidents").select("*").eq("user_id", uid)
        if status:
            query = query.eq("status", status)
        res = query.execute()
        return [Incident(**i) for i in (res.data or [])]
    except Exception as e:
        # No un-scoped fallback: that would expose other users' incidents.
        logger.error(f"Incident query for user {uid} failed: {e}")
        return []

def _transition_incident(incident_id: str, uid: str, new_status: str, stamp_field: str) -> dict:
    """Scoped to the caller's own incidents only — a DB error must never
    widen the update to other users' rows."""
    if not is_database_configured() or not uid:
        return {"status": "ok"}
    try:
        res = get_supabase().table("incidents").update({
            "status": new_status,
            stamp_field: datetime.now(timezone.utc).isoformat()
        }).eq("id", incident_id).eq("user_id", uid).execute()
    except Exception as e:
        raise _db_unavailable(f"update incident to {new_status}", e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": new_status.lower()}

@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, user = Depends(get_current_user)):
    return _transition_incident(incident_id, _get_user_id(user), "ACKNOWLEDGED", "acknowledged_at")

@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, user = Depends(get_current_user)):
    return _transition_incident(incident_id, _get_user_id(user), "RESOLVED", "resolved_at")

# --- Platform API: Agent Ingestion (Protected via Agent Token) ---


@app.post("/api/agent/register", response_model=Device)
def register_agent(device: Device, identity: AgentIdentity = Depends(verify_agent_token)):
    device.last_seen = datetime.now(timezone.utc)
    # A per-user token is authoritative about ownership: never trust a
    # user_id supplied in the payload over the token that signed the request.
    if identity.user_id:
        device.user_id = identity.user_id
    if is_database_configured():
        try:
            get_supabase().table("devices").upsert(device.model_dump(mode='json')).execute()
        except Exception as e:
            raise _db_unavailable("register device", e)
    return device

@app.post("/api/agent/heartbeat")
def agent_heartbeat(payload: Dict[str, Any], identity: AgentIdentity = Depends(verify_agent_token)):
    device_id = payload.get("device_id")
    if not device_id or not isinstance(device_id, str):
        raise HTTPException(status_code=400, detail="Missing device_id")

    if is_database_configured():
        try:
            get_supabase().table("devices").update({
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "status": "ONLINE"
            }).eq("id", device_id).execute()
        except Exception as e:
            raise _db_unavailable("record heartbeat", e)
    return {"status": "received"}

@app.post("/api/agent/telemetry")
def ingest_telemetry(telemetry: Telemetry, identity: AgentIdentity = Depends(verify_agent_token)):
    if is_database_configured():
        telemetry_dict = telemetry.model_dump(mode='json')
        try:
            get_supabase().table("telemetry").insert(telemetry_dict).execute()
        except Exception as e:
            raise _db_unavailable("store telemetry", e)

        # Trigger real-time alert and anomaly checks
        from src.alert_engine import evaluate_alerts
        from src.anomaly_engine import detect_anomalies
        from src.incident_engine import evaluate_and_create_incidents
        
        try:
            evaluate_alerts(telemetry.device_id, telemetry_dict)
            anomalies = detect_anomalies(telemetry.device_id, telemetry_dict)
            # Correlate anomalies to create/deduplicate open incidents in the database
            evaluate_and_create_incidents(telemetry.device_id, anomalies, [])
        except Exception as e:
            logger.error(f"Error in telemetry processing engines: {e}")
            
    return {"status": "ingested"}

# --- Static Frontend Serving (Legacy web/ folder fallback) ---
web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.exists(web_dir):
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(web_dir, "index.html"))
