from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import os
import uuid

from src.models import (
    DiagnosticResult, HistoryEntry, Device, Telemetry, 
    DiagnosticRun, Incident, Alert, Baseline
)
from src.aggregator import run_full_pipeline
from src.history import get_history, save_diagnostic_run_supabase, get_history_supabase
from src.auth import get_current_user, get_optional_user, verify_agent_token
from src.database import get_supabase, is_database_configured
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

def _get_user_run(user) -> Optional[DiagnosticResult]:
    uid = _get_user_id(user) or "guest"
    return _last_runs.get(uid)

def _set_user_run(user, result: DiagnosticResult):
    uid = _get_user_id(user) or "guest"
    _last_runs[uid] = result

@app.get("/api/health")
def api_health():
    db_status = "connected" if is_database_configured() else "unconfigured"
    return {"status": "healthy", "database": db_status, "version": "2.0.0"}

@app.post("/api/diagnostic-run", response_model=DiagnosticResult)
def perform_diagnostic_run(
    mode: Optional[str] = None,
    demo: Optional[str] = None,
    domain: Optional[str] = "google.com,github.com",
    host: Optional[str] = "google.com",
    port: Optional[str] = "443,80",
    user = Depends(get_optional_user)
):
    quick = mode == "quick"
    
    domains = [d.strip() for d in domain.split(',')] if domain else None
    ports = [int(p.strip()) for p in port.split(',')] if port else None
    
    if demo:
        result = run_full_pipeline(is_demo=True, demo_scenario=demo)
    else:
        result = run_full_pipeline(quick=quick, custom_domains=domains, custom_host=host, custom_ports=ports)
        
    _set_user_run(user, result)
    
    uid = _get_user_id(user)
    if uid and not result.is_demo:
        save_diagnostic_run_supabase(result, uid)
        
    return result

@app.get("/api/diagnostic-run", response_model=Optional[DiagnosticResult])
def get_last_diagnostic_run(user = Depends(get_optional_user)):
    return _get_user_run(user)

@app.get("/api/export")
def export_last_run(user = Depends(get_optional_user)):
    return _get_user_run(user)

# Legacy component endpoints
@app.get("/api/system")
def get_system(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.system if run else {}

@app.get("/api/interfaces")
def get_interfaces(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.interfaces if run else []

@app.get("/api/gateway")
def get_gateway(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.gateway if run else {}

@app.get("/api/internet")
def get_internet(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.internet if run else {}

@app.get("/api/dns")
def get_dns(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.dns if run else []

@app.get("/api/tcp")
def get_tcp(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.tcp if run else []

@app.get("/api/routes")
def get_routes(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.routes if run else {}

@app.get("/api/diagnostics")
def get_diagnostics(user = Depends(get_optional_user)):
    run = _get_user_run(user)
    return run.diagnostics if run else {}

@app.get("/api/history", response_model=List[HistoryEntry])
def get_diagnostic_history(limit: int = 100, user = Depends(get_optional_user)):
    uid = _get_user_id(user)
    if uid:
        return get_history_supabase(uid, limit)
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

# --- Platform API: Devices ---

@app.get("/api/devices", response_model=List[Device])
def get_devices(user = Depends(get_optional_user)):
    uid = _get_user_id(user)
    if is_database_configured() and uid:
        try:
            res = get_supabase().table("devices").select("*").eq("user_id", uid).execute()
            if res.data and len(res.data) > 0:
                return [Device(**d) for d in res.data]
        except Exception as e:
            logger.warning(f"Database query with user_id failed: {e}. Trying un-scoped fallback...")
            try:
                res = get_supabase().table("devices").select("*").execute()
                if res.data and len(res.data) > 0:
                    return [Device(**d) for d in res.data]
            except Exception as e2:
                logger.error(f"Fallback database query failed: {e2}")

    # Fallback: return the real local device
    info = _get_local_device_info()
    local_device = Device(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, info["hostname"])),
        name=info["hostname"],
        hostname=info["hostname"],
        platform=info["platform"],
        architecture=info["architecture"],
        ip_address=info["ip_address"],
        agent_version="local",
        status="ONLINE",
    )
    return [local_device]

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
        logger.warning(f"Failed to query device {device_id} with user_id: {e}. Trying un-scoped fallback...")
        try:
            res = get_supabase().table("devices").select("*").eq("id", device_id).execute()
            if res.data:
                return Device(**res.data[0])
        except Exception as e2:
            logger.error(f"Fallback device query failed: {e2}")
    raise HTTPException(status_code=404, detail="Device not found")

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
        return [Incident(**i) for i in res.data]
    except Exception as e:
        logger.warning(f"Failed to query incidents with user_id: {e}. Trying un-scoped fallback...")
        try:
            query = get_supabase().table("incidents").select("*")
            if status:
                query = query.eq("status", status)
            res = query.execute()
            return [Incident(**i) for i in res.data]
        except Exception as e2:
            logger.error(f"Fallback incidents query failed: {e2}")
            return []

@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, user = Depends(get_current_user)):
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        return {"status": "ok"}
    try:
        get_supabase().table("incidents").update({
            "status": "ACKNOWLEDGED",
            "acknowledged_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", incident_id).eq("user_id", uid).execute()
    except Exception as e:
        logger.warning(f"Failed to acknowledge incident with user_id: {e}. Trying un-scoped fallback...")
        try:
            get_supabase().table("incidents").update({
                "status": "ACKNOWLEDGED",
                "acknowledged_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", incident_id).execute()
        except Exception as e2:
            logger.error(f"Fallback incident acknowledge failed: {e2}")
    return {"status": "acknowledged"}

@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, user = Depends(get_current_user)):
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        return {"status": "ok"}
    try:
        get_supabase().table("incidents").update({
            "status": "RESOLVED",
            "resolved_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", incident_id).eq("user_id", uid).execute()
    except Exception as e:
        logger.warning(f"Failed to resolve incident with user_id: {e}. Trying un-scoped fallback...")
        try:
            get_supabase().table("incidents").update({
                "status": "RESOLVED",
                "resolved_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", incident_id).execute()
        except Exception as e2:
            logger.error(f"Fallback incident resolve failed: {e2}")
    return {"status": "resolved"}

# --- Platform API: Agent Ingestion (Protected via Agent Token) ---

@app.post("/api/agent/register", response_model=Device)
def register_agent(device: Device, valid = Depends(verify_agent_token)):
    device.last_seen = datetime.now(timezone.utc)
    if is_database_configured():
        get_supabase().table("devices").upsert(device.model_dump(mode='json')).execute()
    return device

@app.post("/api/agent/heartbeat")
def agent_heartbeat(payload: Dict[str, Any], valid = Depends(verify_agent_token)):
    device_id = payload.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device_id")
        
    if is_database_configured():
        get_supabase().table("devices").update({
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "status": "ONLINE"
        }).eq("id", device_id).execute()
    return {"status": "received"}

@app.post("/api/agent/telemetry")
def ingest_telemetry(telemetry: Telemetry, valid = Depends(verify_agent_token)):
    if is_database_configured():
        telemetry_dict = telemetry.model_dump(mode='json')
        get_supabase().table("telemetry").insert(telemetry_dict).execute()
        
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
