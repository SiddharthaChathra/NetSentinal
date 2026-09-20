from fastapi import FastAPI, Depends, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
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
    get_current_user, get_optional_user, get_principal, verify_agent_token, AgentIdentity,
    get_or_create_agent_token, rotate_agent_token,
)
from src.auth_middleware import AuthGateMiddleware, PUBLIC_PATHS
from src.session import auth_required, revoke_session, clear_session_cache
from src import user_profile
from src import enrollment
from src import troubleshoot
from src.database import get_supabase, is_database_configured, database_access_mode
from src.backup_readiness import (
    build_backup_readiness_report, run_simulated_backup_scenario, simulate_backup_target
)
from src.port_checker import ALL_BACKUP_PROTOCOLS, normalize_protocols
from src.logger import logger

def _get_user_id(user) -> Optional[str]:
    if not user:
        return None
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)

def _get_user_email(user) -> Optional[str]:
    if not user:
        return None
    if isinstance(user, dict):
        return user.get("email")
    return getattr(user, "email", None)

app = FastAPI(title="NetSentinel Platform API", description="Network Observability Platform API")

# Order matters, and it is the opposite of what it reads like: add_middleware
# pushes onto the stack, so the LAST one added is the OUTERMOST. CORS must be
# outermost — otherwise the gate's 401 goes back without CORS headers and the
# browser reports an opaque network error instead of "you are not signed in",
# which is exactly the scary-error failure mode this change must avoid.
app.add_middleware(AuthGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _log_auth_mode():
    if auth_required():
        logger.info("Auth gate ACTIVE: every endpoint except %s requires a valid session.",
                    ", ".join(sorted(PUBLIC_PATHS)))
    else:
        logger.warning(
            "Auth gate DISABLED (no database configured). Every endpoint is open. "
            "This is intended for local development only — set AUTH_REQUIRED=1 to force it on."
        )

# --- Legacy & CLI Endpoints (Preserved) ---

_last_runs: Dict[str, Optional[DiagnosticResult]] = {}
_MAX_RUN_SLOTS = 500

def _run_key(user) -> str:
    """The in-memory "last diagnostic run" slot for a caller.

    Guest keying (the X-Guest-Session header) is gone with guest access: a
    request without a session no longer reaches any endpoint, so every run
    belongs to an identified account and is keyed by its uid. That also
    removes the last path by which one visitor's run could be served to
    another."""
    uid = _get_user_id(user)
    return f"user:{uid}" if uid else "anonymous"

def _get_user_run(user) -> Optional[DiagnosticResult]:
    return _last_runs.get(_run_key(user))

def _set_user_run(user, result: DiagnosticResult):
    key = _run_key(user)
    # Bound memory: drop the oldest slots once we hold too many.
    if key not in _last_runs and len(_last_runs) >= _MAX_RUN_SLOTS:
        for old in list(_last_runs)[: len(_last_runs) - _MAX_RUN_SLOTS + 1]:
            _last_runs.pop(old, None)
    _last_runs[key] = result

def _clear_user_run(user):
    """Called on logout so a shared browser cannot show the previous
    account's scan results to whoever signs in next."""
    _last_runs.pop(_run_key(user), None)

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

# Columns the backend reads/writes per table. Probed on /api/health so a
# database created from an older schema (which is exactly what happened in
# production) is reported as `schema: {missing: [...]}` instead of surfacing
# as opaque 503s from whichever endpoint hits the gap first.
EXPECTED_SCHEMA = {
    "devices": ["id", "user_id", "name", "hostname", "platform", "architecture", "ip_address",
                "agent_version", "status", "is_backup_target", "backup_protocols", "last_seen", "created_at", "updated_at"],
    "telemetry": ["id", "device_id", "timestamp", "latency_ms", "packet_loss", "gateway_reachable",
                  "internet_reachable", "dns_healthy", "tcp_healthy", "interface_errors", "interface_drops", "backup_ports", "gateway_ip"],
    "incidents": ["id", "user_id", "device_id", "title", "severity", "status", "likely_cause", "confidence",
                  "evidence", "recommended_actions", "started_at", "acknowledged_at", "resolved_at"],
    "alerts": ["id", "user_id", "device_id", "type", "threshold", "current_value", "status", "created_at", "resolved_at"],
    "metric_baselines": ["id", "user_id", "device_id", "metric", "window", "average", "median", "p95", "stddev", "updated_at"],
    "history": ["id", "user_id", "timestamp", "score", "status", "gateway_status", "internet_status",
                "dns_status", "tcp_status", "latency", "packet_loss", "is_demo"],
    "agent_tokens": ["user_id", "token", "created_at", "rotated_at", "last_used_at"],
    "user_profiles": ["user_id", "onboarding_completed_at", "onboarding_version", "last_login_at", "login_count"],
}
_schema_cache = {"at": 0.0, "result": None}

def _schema_status() -> dict:
    """{'ok': bool, 'missing': ['table.column', ...]} — cached for 60s."""
    now = time.monotonic()
    if _schema_cache["result"] is not None and now - _schema_cache["at"] < _DB_PROBE_TTL_S:
        return _schema_cache["result"]
    missing = []
    try:
        sb = get_supabase()
        for table, cols in EXPECTED_SCHEMA.items():
            try:
                sb.table(table).select(",".join(cols)).limit(0).execute()
            except Exception as e:
                msg = _db_error_reason(e)
                m = re.search(r"column (?:\w+\.)?(\w+) does not exist|'(\w+)' column", msg)
                col = (m.group(1) or m.group(2)) if m else None
                if "Could not find the table" in msg or "relation" in msg and "does not exist" in msg:
                    missing.append(f"{table} (table missing)")
                elif col:
                    missing.append(f"{table}.{col}")
                    # Keep probing the remaining columns individually so the
                    # report lists every gap, not just the first one hit.
                    for c in cols:
                        if c == col:
                            continue
                        try:
                            sb.table(table).select(c).limit(0).execute()
                        except Exception:
                            missing.append(f"{table}.{c}")
                else:
                    missing.append(f"{table} ({msg[:60]})")
    except Exception as e:
        result = {"ok": False, "missing": [], "error": _db_error_reason(e)}
        _schema_cache.update(at=now, result=result)
        return result
    result = {"ok": not missing, "missing": missing}
    _schema_cache.update(at=now, result=result)
    return result

@app.get("/api/health")
def api_health():
    body = {
        "status": "healthy",
        "database": _database_status(),
        "database_access": database_access_mode(),
        "version": "2.0.0",
    }
    if body["database"] == "connected":
        body["schema"] = _schema_status()
    return body

# --- Session / access control -------------------------------------------
#
# The app requires a login before any use. /api/auth/session is the one
# endpoint the frontend calls before it knows whether it has a user, so it is
# public and answers 200 in both cases: 401 here would be indistinguishable
# from "the backend is broken" on the very first paint of every visit.


def _account_has_devices(uid: str) -> Optional[bool]:
    """Has this account ever registered an agent?

    Deliberately the cheapest question that answers "is this user set up":
    one indexed, limit-1 lookup, no columns beyond the id. It sits on
    /api/auth/session, which runs on every visit.

    Returns None when the answer is unknown (no database, or the query
    failed). Callers must treat None as "don't know" rather than "no" — being
    wrongly told to install an agent they already have is worse than not being
    prompted at all.
    """
    if not uid or not is_database_configured():
        return None
    try:
        res = get_supabase().table("devices").select("id").eq("user_id", uid).limit(1).execute()
        return bool(res.data)
    except Exception as e:
        logger.warning(f"Could not check device count for user {uid}: {e}")
        return None


@app.get("/api/auth/session")
def get_session(request: Request, user = Depends(get_optional_user)):
    """The "am I logged in" check, on the critical path of every visit.

    Fast by construction:
      * no database probe (unlike /api/health) — nothing here waits on Postgres
      * token validation is cached for 30 s, so a page load that fires several
        requests pays the Supabase round-trip at most once
      * the onboarding lookup is a single indexed primary-key read, and is
        skipped entirely for signed-out callers

    Returns 200 always. `authenticated` is the field to branch on.
    """
    uid = _get_user_id(user)
    if not uid:
        return {
            "authenticated": False,
            "user": None,
            "org": None,
            "onboarding": None,
            "setup": None,
            "landing": None,
            "auth_required": auth_required(),
            "login_url": "/auth",
        }

    # Recording the login is what makes "first successful sign-in" a
    # server-side fact: the profile row is created here, on the first
    # authenticated request the account ever makes, and the state returned is
    # the state from *before* that write.
    onboarding = user_profile.record_login(uid)

    # An account with no registered agent has nothing to look at yet, so it is
    # sent to the setup guide instead of an empty dashboard. `has_devices` is
    # None when we could not find out — in that case do NOT claim setup is
    # needed, or a user who already runs an agent gets nagged to install one.
    has_devices = _account_has_devices(uid)
    needs_setup = has_devices is False

    return {
        "authenticated": True,
        "user": {"id": uid, "email": _get_user_email(user)},
        # Accounts are single-tenant today: the org scope IS the user id, and
        # it is the only key any query is allowed to filter on. Returned
        # explicitly so the frontend can assert it never renders data from a
        # scope other than the session it holds.
        "org": {"id": uid, "scope": "account"},
        "onboarding": onboarding,
        "setup": {"has_devices": has_devices, "needs_setup": needs_setup},
        # Where the frontend should land this user when it has no more
        # specific destination (i.e. they did not follow a deep link). The
        # rule lives here so "first run goes to the guide" is decided in one
        # place rather than re-derived in the client.
        "landing": "/getting-started" if needs_setup else "/",
        "auth_required": auth_required(),
    }


@app.post("/api/auth/logout")
def logout(request: Request, user = Depends(get_optional_user)):
    """Ends the session server-side, not just in the browser.

    Supabase access tokens are stateless JWTs, so three things have to happen
    for a logout to be real, and all three happen here:
      1. the refresh token is revoked at Supabase, so the session cannot be
         renewed and dies at the access token's expiry;
      2. this backend's validation cache drops the token, so it stops being
         accepted here immediately rather than up to 30 s later;
      3. the account's cached diagnostic run is dropped, so the next person to
         sign in on a shared browser cannot be shown the previous user's scan.

    Always 200 — logging out with an already-expired token is a success, not
    an error, or a user with a stale session can never cleanly sign out.
    """
    token = getattr(request.state, "access_token", None)
    result = revoke_session(token)
    uid = _get_user_id(user)
    if uid:
        _clear_user_run(user)
    return {"signed_out": True, "session_revoked": result["revoked"], "detail": result["detail"], "login_url": "/auth"}


@app.get("/api/auth/onboarding")
def get_onboarding(user = Depends(get_current_user)):
    return user_profile.onboarding_state(_get_user_id(user))


@app.post("/api/auth/onboarding/complete")
def complete_onboarding(user = Depends(get_current_user)):
    """Marks the tour as seen for the ACCOUNT, so it does not run again on the
    user's other devices. See src/user_profile.py for why this is per-account
    rather than per-browser."""
    return user_profile.complete_onboarding(_get_user_id(user))


@app.post("/api/auth/onboarding/reset")
def reset_onboarding(user = Depends(get_current_user)):
    """Replay the tour on demand."""
    return user_profile.reset_onboarding(_get_user_id(user))


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
    user = Depends(get_current_user),
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

    _set_user_run(user, result)

    uid = _get_user_id(user)
    if uid and not result.is_demo:
        save_diagnostic_run_supabase(result, uid)

    return result

@app.get("/api/diagnostic-run", response_model=Optional[DiagnosticResult])
def get_last_diagnostic_run(user = Depends(get_current_user)):
    return _get_user_run(user)

@app.get("/api/export")
def export_last_run(user = Depends(get_current_user)):
    return _get_user_run(user)

# Legacy component endpoints
@app.get("/api/system")
def get_system(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.system if run else {}

@app.get("/api/interfaces")
def get_interfaces(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.interfaces if run else []

@app.get("/api/gateway")
def get_gateway(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.gateway if run else {}

@app.get("/api/internet")
def get_internet(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.internet if run else {}

@app.get("/api/dns")
def get_dns(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.dns if run else []

@app.get("/api/tcp")
def get_tcp(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.tcp if run else []

@app.get("/api/routes")
def get_routes(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.routes if run else {}

@app.get("/api/diagnostics")
def get_diagnostics(user = Depends(get_current_user)):
    run = _get_user_run(user)
    return run.diagnostics if run else []

@app.get("/api/history", response_model=List[HistoryEntry])
def get_diagnostic_history(
    limit: int = 100,
    hours: Optional[float] = None,
    user = Depends(get_current_user),
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

# --- Local Device Info (behind the login gate, like everything else) ---

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

def _db_error_reason(e: Exception) -> str:
    """Short, client-safe summary of a Supabase/PostgREST failure. Their
    errors carry a `message` (e.g. "column x does not exist", "violates
    foreign key constraint") which is what an operator needs to act on."""
    msg = getattr(e, "message", None)
    if isinstance(msg, str) and msg:
        reason = msg
    else:
        reason = str(e) or e.__class__.__name__
    reason = re.sub(r"\s+", " ", reason).strip()
    return reason[:160]

def _db_unavailable(action: str, e: Exception) -> HTTPException:
    reason = _db_error_reason(e)
    logger.error(f"Database error during {action}: {e}")
    # A data error (bad column, FK violation, RLS denial) is not an outage;
    # only mark the DB unreachable for transport-level failures.
    if not getattr(e, "code", None):
        _db_probe_cache.update(at=time.monotonic(), status="unreachable")
    return HTTPException(status_code=503, detail=f"Could not {action}: {reason}")

# --- Platform API: Devices ---

HOSTED_SERVER_AGENT_VERSION = "hosted-server"

def _hosted_server_device() -> Device:
    """The machine this API is running on, presented as a clearly-labelled
    demo device. It is what a signed-in account with no agents yet sees, and
    what the hosted "Run Diagnostic" actually measures — NOT the visitor's
    own network. `agent_version` is the marker the frontend uses to explain
    that and to point at the agent setup guide."""
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

def _derive_status(device: Device) -> str:
    """ONLINE/OFFLINE from when the agent last checked in.

    Nothing ever wrote OFFLINE: `status` was set to ONLINE on registration and
    on every heartbeat, and never flipped back, so a machine whose agent had
    been stopped for a week still reported ONLINE. With one device that was
    merely wrong; with a fleet it is actively misleading, because the
    dashboard's "N Online / 0 Offline" counter can never show anything else.

    Deriving it from `last_seen` needs no background sweeper — which the free
    Render tier could not run anyway — and cannot drift out of date.
    """
    from src.backup_readiness import AGENT_STALE_SECONDS

    # The hosted demo device is this server; it is up by definition.
    if device.agent_version == HOSTED_SERVER_AGENT_VERSION:
        return device.status

    last_seen = device.last_seen
    if last_seen is None:
        return "OFFLINE"
    if isinstance(last_seen, str):
        from src.backup_readiness import _parse_ts
        last_seen = _parse_ts(last_seen)
        if last_seen is None:
            return device.status
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    age_s = (datetime.now(timezone.utc) - last_seen).total_seconds()
    return "ONLINE" if age_s <= AGENT_STALE_SECONDS else "OFFLINE"


def _fetch_devices(user) -> List[Device]:
    """Exactly the caller's own registered devices — an empty list if they
    have none, so the UI can show the agent setup guide. The only path that
    returns the hosted demo device is a local checkout with no database, where
    there are no accounts to scope to. Shared by /api/devices and the
    backup-readiness endpoint.

    This `.eq("user_id", uid)` is the tenant boundary for device data: uid
    comes from the validated session and from nowhere else — never from a
    query parameter, header or request body — so a caller cannot ask for
    another account's devices."""
    uid = _get_user_id(user)
    if is_database_configured() and uid:
        try:
            res = get_supabase().table("devices").select("*").eq("user_id", uid).execute()
            devices = [Device(**d) for d in (res.data or [])]
            for device in devices:
                device.status = _derive_status(device)
            return devices
        except Exception as e:
            # Never fall back to an un-scoped query here: that would hand one
            # user every other user's devices whenever the DB hiccups.
            logger.error(f"Device query for user {uid} failed: {e}")
            return []

    return [_hosted_server_device()]

@app.get("/api/devices", response_model=List[Device])
def get_devices(user = Depends(get_current_user)):
    return _fetch_devices(user)

PUBLIC_API_BASE_URL = os.environ.get("PUBLIC_API_BASE_URL", "https://netsentinal.onrender.com")
REPO_URL = "https://github.com/SiddharthaChathra/NetSentinal"

def _personal_agent_token(uid: Optional[str]) -> Optional[str]:
    """The signed-in user's own agent token (created on first request).
    None when the database can't be reached."""
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
def get_setup_guide(user = Depends(get_current_user)):
    """Single source of truth for 'how do I monitor my own machine'. The
    frontend renders this so the instructions can never drift from what
    the backend actually needs. For a signed-in user the .env block is
    complete and copy-pasteable, including their personal agent token."""
    uid = _get_user_id(user)
    token = _personal_agent_token(uid)
    # The caller is always signed in now — the endpoint is behind the gate —
    # so the only reason a token is missing is that the token store is
    # unreachable. Say that, rather than telling a signed-in user to sign in.
    env_lines = [
        f"API_BASE_URL={PUBLIC_API_BASE_URL}",
        f"AGENT_TOKEN={token if token else '<token unavailable — reload this page>'}",
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
        "signed_in": True,
        "agent_token_required": True,
        "api_base_url": PUBLIC_API_BASE_URL,
        "repo_url": REPO_URL,
        "steps": [
            {
                "title": "Sign in",
                "body": "Devices are saved to your account. You are signed in, so this step is done.",
                "done": True,
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
            {
                "title": "Keep it running (recommended)",
                "body": (
                    "The agent only reports while its process is alive, so closing that terminal or "
                    "rebooting stops it — and the device then shows as offline even though the machine "
                    "is fine. Run the installer below once and it will start automatically at login and "
                    "restart itself if it stops."
                ),
                "commands": [
                    "# Windows (PowerShell, from the repo folder)",
                    r"powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1",
                    "",
                    "# Linux / macOS",
                    "bash agent/install_autostart.sh",
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
            device = Device(**res.data[0])
            device.status = _derive_status(device)
            return device
    except Exception as e:
        raise _db_unavailable("fetch device", e)
    raise HTTPException(status_code=404, detail="Device not found")

@app.delete("/api/devices/{device_id}")
def delete_device(device_id: str, user = Depends(get_current_user)):
    """Removes one of the caller's devices. Its telemetry and incidents go
    with it (ON DELETE CASCADE). An agent still running on that machine will
    re-register on its next --register; a running --start loop keeps posting
    with the old id and now gets 422s, so stop it or re-register."""
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        res = get_supabase().table("devices").delete().eq("id", device_id).eq("user_id", uid).execute()
    except Exception as e:
        raise _db_unavailable("delete device", e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"id": device_id, "deleted": True}

@app.post("/api/devices/{device_id}/backup-target")
def set_backup_target(device_id: str, payload: Dict[str, Any], user = Depends(get_current_user)):
    """Tags (or untags) a monitored device as a backup target. Only tagged
    devices are included in /api/backup/readiness and run backup-specific
    checks (NFS/SMB/iSCSI/replication ports, SLA estimation)."""
    is_backup = bool(payload.get("is_backup_target", True))
    update = {"is_backup_target": is_backup, "updated_at": datetime.now(timezone.utc).isoformat()}
    if "backup_protocols" in payload:
        raw = payload.get("backup_protocols")
        if raw is not None and not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="backup_protocols must be a list of protocol names or null")
        try:
            update["backup_protocols"] = normalize_protocols(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    uid = _get_user_id(user)
    if is_database_configured() and uid:
        try:
            res = get_supabase().table("devices").update(update).eq("id", device_id).eq("user_id", uid).execute()
        except Exception as e:
            raise _db_unavailable("update device", e)
        if not res.data:
            raise HTTPException(status_code=404, detail="Device not found")
    body = {"id": device_id, "is_backup_target": is_backup}
    if "backup_protocols" in update:
        body["backup_protocols"] = update["backup_protocols"]
    return body

@app.get("/api/backup/protocols")
def list_backup_protocols():
    """The protocol names a backup target can be configured to serve."""
    return {"protocols": list(ALL_BACKUP_PROTOCOLS)}

# --- Platform API: Telemetry (read side) ---

@app.get("/api/telemetry/latest")
def get_latest_telemetry(user = Depends(get_current_user)):
    """Most recent agent report per device the caller owns, keyed by device
    id. This is what the dashboard should use for anything an agent knows
    about its own host (gateway address, latency, backup ports) — the
    on-demand /api/gateway etc. only reflect a diagnostic run in *this*
    browser session. An account with no agent-managed devices gets {}."""
    uid = _get_user_id(user)
    if not uid or not is_database_configured():
        return {}
    devices = _fetch_devices(user)
    latest = _latest_telemetry_for([d.id for d in devices])
    return {
        device_id: {
            "device_id": device_id,
            "timestamp": row.get("timestamp"),
            "gateway_ip": row.get("gateway_ip"),
            "gateway_reachable": row.get("gateway_reachable"),
            "internet_reachable": row.get("internet_reachable"),
            "dns_healthy": row.get("dns_healthy"),
            "tcp_healthy": row.get("tcp_healthy"),
            "latency_ms": row.get("latency_ms"),
            "packet_loss": row.get("packet_loss"),
            "interface_errors": row.get("interface_errors"),
            "interface_drops": row.get("interface_drops"),
            "backup_ports": row.get("backup_ports"),
        }
        for device_id, row in latest.items()
    }

# --- Guided troubleshooting ----------------------------------------------

TROUBLESHOOT_HISTORY_HOURS = 24.0
TROUBLESHOOT_MAX_SAMPLES = 500


def _troubleshoot_series(device_id: str) -> tuple:
    """(latency samples, loss samples) for a device, oldest first."""
    if not is_database_configured():
        return ([], [])
    since = (datetime.now(timezone.utc) - timedelta(hours=TROUBLESHOOT_HISTORY_HOURS)).isoformat()
    try:
        res = (
            get_supabase().table("telemetry")
            .select("timestamp,latency_ms,packet_loss")
            .eq("device_id", device_id).gte("timestamp", since)
            .order("timestamp", desc=True).limit(TROUBLESHOOT_MAX_SAMPLES).execute()
        )
    except Exception as e:
        logger.warning(f"Could not load telemetry for troubleshooting: {e}")
        return ([], [])
    rows = list(reversed(res.data or []))
    return (
        [r["latency_ms"] for r in rows if r.get("latency_ms") is not None],
        [r["packet_loss"] for r in rows if r.get("packet_loss") is not None],
    )


@app.get("/api/troubleshoot")
def get_troubleshooting(user = Depends(get_current_user)):
    """The "why is my network slow?" analysis, computed from real measurements.

    Prefers the account's own agent-reported machine, because that is the
    network the user is actually asking about. Falls back to the hosted scan
    only when there is no agent, and says which it used — the hosted scan
    measures the NetSentinel server's network, not theirs.
    """
    uid = _get_user_id(user)

    devices = [d for d in _fetch_devices(user) if d.agent_version != HOSTED_SERVER_AGENT_VERSION]
    latest = _latest_telemetry_for([d.id for d in devices]) if devices else {}

    # The most recently reporting machine is the one to analyse.
    best_id, best_row = None, None
    for device in devices:
        row = latest.get(device.id)
        if not row:
            continue
        if best_row is None or str(row.get("timestamp") or "") > str(best_row.get("timestamp") or ""):
            best_id, best_row = device.id, row

    if best_row is not None:
        device = next(d for d in devices if d.id == best_id)
        latency_history, loss_history = _troubleshoot_series(best_id)
        return troubleshoot.build_analysis(
            source="agent",
            device_name=device.hostname or device.name,
            measured_at=best_row.get("timestamp"),
            latency_ms=best_row.get("latency_ms"),
            packet_loss_pct=best_row.get("packet_loss"),
            gateway_reachable=best_row.get("gateway_reachable"),
            internet_reachable=best_row.get("internet_reachable"),
            dns_healthy=best_row.get("dns_healthy"),
            latency_history=latency_history,
            loss_history=loss_history,
        )

    run = _get_user_run(user)
    if run is not None:
        history = get_history_supabase(uid, limit=TROUBLESHOOT_MAX_SAMPLES) if uid and is_database_configured() else []
        return troubleshoot.build_analysis(
            source="hosted-scan",
            device_name=run.system.get("hostname"),
            measured_at=run.timestamp,
            latency_ms=run.internet.get("latency_ms"),
            packet_loss_pct=run.internet.get("packet_loss"),
            gateway_reachable=bool(run.gateway.get("reachable")),
            internet_reachable=bool(run.internet.get("reachable")),
            dns_healthy=bool(run.dns and any(d.get("success") for d in run.dns)),
            latency_history=[h["latency"] for h in reversed(history) if h.get("latency") is not None],
            loss_history=[h["packet_loss"] for h in reversed(history) if h.get("packet_loss") is not None],
        )

    return troubleshoot.build_analysis(
        source="none", device_name=None, measured_at=None, latency_ms=None,
        packet_loss_pct=None, gateway_reachable=None, internet_reachable=None,
        dns_healthy=None, latency_history=[], loss_history=[],
    )


# --- Platform API: Report export ---

REPORT_VERSION = "2"

def _gateway_summary(run: DiagnosticResult) -> str:
    """Consistent with the diagnostic engine: a gateway that drops ICMP while
    the internet is reachable through it is forwarding fine, not failing."""
    gw_ok = bool(run.gateway.get("reachable"))
    inet_ok = bool(run.internet.get("reachable"))
    if gw_ok:
        return "PASS"
    if inet_ok:
        return "PASS (ICMP filtered)"
    return "FAIL"

def _telemetry_summary(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    from src.backup_readiness import _parse_ts, AGENT_STALE_SECONDS
    reported = _parse_ts(row.get("timestamp")) if row.get("timestamp") else None
    age_s = (datetime.now(timezone.utc) - reported).total_seconds() if reported else None
    return {
        "reported_at": reported.isoformat() if reported else None,
        "age_seconds": int(age_s) if age_s is not None else None,
        "stale": bool(age_s is not None and age_s > AGENT_STALE_SECONDS),
        "gateway_ip": row.get("gateway_ip"),
        "gateway_reachable": row.get("gateway_reachable"),
        "internet_reachable": row.get("internet_reachable"),
        "dns_healthy": row.get("dns_healthy"),
        "latency_ms": row.get("latency_ms"),
        "packet_loss_pct": row.get("packet_loss"),
        "backup_ports": row.get("backup_ports"),
    }

def _build_report_document(user, dataset_size_gb: float, sla_hours: float) -> dict:
    """Everything the Reports page exports, assembled server-side so the
    document is internally consistent and honest about scope: the hosted
    scan is labelled as the NetSentinel server's own network, and a
    signed-in user's agent-managed devices and backup readiness are
    included from their latest telemetry."""
    now = datetime.now(timezone.utc)
    uid = _get_user_id(user)
    run = _get_user_run(user)

    hosted_scan = None
    if run:
        hosted_scan = {
            "note": (
                "This scan was performed by the NetSentinel server on its own network, not on your machine. "
                "Per-device results for your own machines are under 'devices'."
            ),
            "run_at": run.timestamp,
            "is_demo": run.is_demo,
            "server_hostname": run.system.get("hostname"),
            "server_ip": run.system.get("local_ip"),
            "health_score": run.health_score,
            "status": run.status,
            "metrics": {
                "latency_ms": run.internet.get("latency_ms"),
                "packet_loss_pct": run.internet.get("packet_loss"),
                "gateway": _gateway_summary(run),
                "internet": "PASS" if run.internet.get("reachable") else "FAIL",
                "dns": "PASS" if run.dns and all(d.get("success") for d in run.dns) else ("PARTIAL" if any(d.get("success") for d in run.dns or []) else "FAIL"),
                "tcp": "PASS" if run.tcp and all(t.get("success") for t in run.tcp) else ("PARTIAL" if any(t.get("success") for t in run.tcp or []) else "FAIL"),
            },
            "diagnostics": [d.model_dump() if hasattr(d, "model_dump") else d for d in run.diagnostics],
            "duration_ms": run.duration_ms,
        }

    devices_out, backup = [], None
    if uid:
        devices = [d for d in _fetch_devices(user)]
        latest = _latest_telemetry_for([d.id for d in devices])
        for d in devices:
            devices_out.append({
                "id": d.id,
                "name": d.name,
                "hostname": d.hostname,
                "platform": d.platform,
                "ip_address": d.ip_address,
                "agent_version": d.agent_version,
                "is_backup_target": d.is_backup_target,
                "backup_protocols": d.backup_protocols or list(ALL_BACKUP_PROTOCOLS),
                "last_seen": d.last_seen.isoformat() if isinstance(d.last_seen, datetime) else d.last_seen,
                "latest_telemetry": _telemetry_summary(latest.get(d.id)),
            })
        if any(d.is_backup_target for d in devices):
            backup = build_backup_readiness_report(
                devices, health_score=run.health_score if run else 0,
                dataset_size_gb=dataset_size_gb, sla_window_hours=sla_hours,
                telemetry_by_device=latest,
            )
            backup = BackupReadinessReport(**backup).model_dump(by_alias=True)

    warnings = 0
    if hosted_scan:
        warnings += sum(1 for d in hosted_scan["diagnostics"] if d.get("severity") in ("warning", "critical"))
    if backup:
        warnings += sum(1 for d in backup["diagnostics"] if d.get("severity") in ("warning", "critical"))

    return {
        "title": "NetSentinel Diagnostic & Observability Report",
        "report_version": REPORT_VERSION,
        "generated_at": now.isoformat(),
        "account": {"signed_in": uid is not None},
        "parameters": {"dataset_size_gb": dataset_size_gb, "sla_window_hours": sla_hours},
        "summary": {
            "hosted_scan_status": hosted_scan["status"] if hosted_scan else "NOT RUN",
            "hosted_scan_health_score": hosted_scan["health_score"] if hosted_scan else None,
            "devices": len(devices_out),
            "devices_reporting": sum(1 for d in devices_out if d["latest_telemetry"] and not d["latest_telemetry"]["stale"]),
            "backup_targets": len(backup["targets"]) if backup else 0,
            "backup_readiness_score": backup["backupReadinessScore"] if backup else None,
            "open_warnings": warnings,
        },
        "hosted_scan": hosted_scan,
        "devices": devices_out,
        "backup_readiness": backup,
    }

@app.get("/api/report")
def get_report(
    dataset_size_gb: float = 500.0,
    sla_hours: float = 4.0,
    user = Depends(get_current_user),
):
    return _build_report_document(user, dataset_size_gb, sla_hours)

# Per-device row budget for the trend graphs, and a ceiling on the whole
# query so a large fleet cannot pull an unbounded result set.
_HISTORY_ROWS_PER_DEVICE = 1500
_HISTORY_ROWS_MAX = 12000

def _telemetry_history_for(device_ids: List[str], hours: float = 24.0,
                           per_device_limit: int = _HISTORY_ROWS_PER_DEVICE) -> Dict[str, list]:
    """Telemetry rows per device over the last `hours`, oldest first — the
    series behind the PDF trend graphs.

    The budget scales with the number of devices, and the query is ordered
    NEWEST-first before being reversed. Both matter once an account has more
    than one device: the agent reports every 60 s, so 24 h is ~1440 rows per
    device, and a fixed 2000-row oldest-first limit meant two devices silently
    lost the most recent third of the window — the graphs would have shown
    stale data while the heading claimed otherwise.
    """
    if not device_ids or not is_database_configured():
        return {}
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    budget = min(per_device_limit * len(device_ids), _HISTORY_ROWS_MAX)
    try:
        res = (
            get_supabase().table("telemetry")
            .select("device_id,timestamp,latency_ms,packet_loss,gateway_reachable,internet_reachable,dns_healthy")
            .in_("device_id", device_ids).gte("timestamp", since)
            .order("timestamp", desc=True).limit(budget).execute()
        )
    except Exception as e:
        logger.warning(f"Could not load telemetry history: {e}")
        return {}
    out: Dict[str, list] = {}
    for row in res.data or []:
        out.setdefault(row["device_id"], []).append(row)
    # Newest-first from the query so the limit keeps recent data; the graphs
    # want oldest-first.
    for rows in out.values():
        rows.reverse()
    return out

@app.get("/api/report.pdf")
def get_report_pdf(
    dataset_size_gb: float = 500.0,
    sla_hours: float = 4.0,
    user = Depends(get_current_user),
):
    """The same document as /api/report, rendered as a printable engineer's
    report (summary, layer-by-layer evidence, device telemetry, trend
    graphs, backup readiness with the SLA arithmetic, methodology)."""
    from src.report_pdf import build_pdf_report
    document = _build_report_document(user, dataset_size_gb, sla_hours)
    uid = _get_user_id(user)
    history = get_history_supabase(uid, limit=300, since=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat()) if uid else []
    telemetry_history = _telemetry_history_for([d["id"] for d in document.get("devices", [])]) if uid else {}
    try:
        pdf = build_pdf_report(document, history=history, telemetry_history=telemetry_history)
    except Exception as e:
        logger.error(f"PDF report generation failed: {e}")
        raise HTTPException(status_code=500, detail="Could not render the PDF report")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="netsentinel-report-{stamp}.pdf"'},
    )

# --- Platform API: Backup Readiness ---

# How many devices we will chase individually when the bulk window misses
# them. Bounds the worst case to a handful of extra round-trips.
_MAX_TELEMETRY_BACKFILL = 25

def _latest_telemetry_for(device_ids: List[str]) -> Dict[str, dict]:
    """Most recent telemetry row per device.

    Two passes, because one bulk query is not enough once an account has
    several devices. The bulk query takes the newest N rows across ALL of
    them, so a device that stopped reporting an hour ago has every one of its
    rows pushed out of that window by its livelier siblings — and it would
    then look like a device that never had an agent at all, rather than one
    whose agent is down. That is exactly backwards: the silent device is the
    one the user needs told about.

    So: one bulk query for the common case where everything is reporting,
    then a targeted limit-1 query for each device the window missed. Devices
    that genuinely have no telemetry are still absent, and get probed from
    the server instead.
    """
    if not device_ids or not is_database_configured():
        return {}
    latest: Dict[str, dict] = {}
    try:
        res = (
            get_supabase().table("telemetry")
            .select("*")
            .in_("device_id", device_ids)
            .order("timestamp", desc=True)
            .limit(20 * len(device_ids))
            .execute()
        )
        for row in res.data or []:
            latest.setdefault(row["device_id"], row)  # rows are newest-first
    except Exception as e:
        logger.warning(f"Could not load telemetry for backup targets: {e}")
        return {}

    missing = [d for d in device_ids if d not in latest]
    for device_id in missing[:_MAX_TELEMETRY_BACKFILL]:
        try:
            res = (
                get_supabase().table("telemetry")
                .select("*")
                .eq("device_id", device_id)
                .order("timestamp", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                latest[device_id] = res.data[0]
        except Exception as e:
            logger.warning(f"Could not load latest telemetry for device {device_id}: {e}")
    return latest

@app.get("/api/backup/readiness", response_model=BackupReadinessReport)
def get_backup_readiness(
    dataset_size_gb: float = 500.0,
    sla_hours: float = 4.0,
    demo: Optional[str] = None,
    user = Depends(get_current_user),
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
    last_run = _get_user_run(user)
    health_score = last_run.health_score if last_run else 0

    report = build_backup_readiness_report(
        devices,
        health_score=health_score,
        dataset_size_gb=dataset_size_gb,
        sla_window_hours=sla_hours,
        telemetry_by_device=_latest_telemetry_for(
            [d.id for d in devices if getattr(d, "is_backup_target", False)]
        ),
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
def get_incidents(status: Optional[str] = None, user = Depends(get_current_user)):
    uid = _get_user_id(user)
    if not is_database_configured() or not uid:
        return []  # No database configured: nothing has been persisted to read
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


# --- Adding a device -----------------------------------------------------
#
# The agent ships as a standalone executable, so a user adding their second
# machine has no repo and no .env to paste a token into. Instead they get a
# short-lived code here and type it into the agent once.

AGENT_RELEASE_TAG = os.environ.get("AGENT_RELEASE_TAG", "latest")
AGENT_VERSION = "1.1.0"


def _agent_downloads() -> dict:
    """Where the built agent binaries live.

    GitHub's /releases/latest/download/<asset> redirects to whatever the most
    recent release is, so these URLs never need updating when a new agent is
    published — the build workflow just has to keep the asset names stable.
    """
    base = f"{REPO_URL}/releases"
    path = "latest/download" if AGENT_RELEASE_TAG == "latest" else f"download/{AGENT_RELEASE_TAG}"
    return {
        "windows": {
            "label": "Windows 10/11 (64-bit)",
            "filename": "netsentinel-agent-windows.exe",
            "url": f"{base}/{path}/netsentinel-agent-windows.exe",
        },
        "linux": {
            "label": "Linux (x86-64)",
            "filename": "netsentinel-agent-linux",
            "url": f"{base}/{path}/netsentinel-agent-linux",
        },
    }


@app.get("/api/agent/releases")
def get_agent_releases():
    """Download links and the current agent version.

    Public (it is under /api/agent/) because the binaries themselves are
    public release assets and the agent calls this to check for updates
    before it has any credential.
    """
    return {
        "version": AGENT_VERSION,
        "downloads": _agent_downloads(),
        "releases_page": f"{REPO_URL}/releases",
        "source_install": {
            "note": "Prefer running from source? The agent still works as a checkout.",
            "repo_url": REPO_URL,
        },
    }


@app.post("/api/devices/enroll-code")
def create_enrollment_code(user = Depends(get_current_user)):
    """Issues a short-lived code the user types into the agent on a new
    machine. Scoped to the caller: the code can only ever enrol a device into
    the account that asked for it."""
    uid = _get_user_id(user)
    try:
        payload = enrollment.create_code(uid)
    except enrollment.EnrollmentError as e:
        raise HTTPException(status_code=503, detail=str(e))
    payload["downloads"] = _agent_downloads()
    payload["agent_version"] = AGENT_VERSION
    return payload


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting. Render sits behind a
    proxy, so the forwarded header is the real caller when present."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/agent/enroll")
def enroll_agent(payload: Dict[str, Any], request: Request):
    """Exchanges an enrolment code for the account's agent token.

    Deliberately unauthenticated — the code IS the credential, which is the
    whole point: a machine being set up has nothing else yet. It is protected
    by being short-lived, single-use and rate-limited per client.
    """
    code = payload.get("code")
    if not isinstance(code, str):
        raise HTTPException(status_code=400, detail="Provide the enrolment code shown on the website.")
    hostname = payload.get("hostname")
    try:
        token = enrollment.redeem_code(code, _client_key(request), hostname if isinstance(hostname, str) else None)
    except enrollment.EnrollmentError as e:
        # 400, not 401: the caller is not failing to authenticate, they typed
        # a code that is not usable. The agent prints this straight through.
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": token, "api_base_url": PUBLIC_API_BASE_URL, "agent_version": AGENT_VERSION}


@app.post("/api/agent/register", response_model=Device)
def register_agent(device: Device, identity: AgentIdentity = Depends(verify_agent_token)):
    """Idempotent: re-registering the same machine (same owner + hostname)
    updates and returns its existing device instead of creating a duplicate,
    so `--register` after a token change or a wiped agent_data folder does
    not litter the Devices page."""
    now = datetime.now(timezone.utc)
    device.last_seen = now
    device.updated_at = now
    # A per-user token is authoritative about ownership: never trust a
    # user_id supplied in the payload over the token that signed the request.
    if identity.user_id:
        device.user_id = identity.user_id
    if is_database_configured():
        try:
            sb = get_supabase()
            if device.user_id:
                existing = (
                    sb.table("devices").select("id,created_at,is_backup_target,backup_protocols")
                    .eq("user_id", device.user_id).eq("hostname", device.hostname)
                    .order("created_at", desc=False).limit(1).execute()
                )
                if existing.data:
                    row = existing.data[0]
                    device.id = row["id"]
                    device.is_backup_target = bool(row.get("is_backup_target", False))
                    device.backup_protocols = row.get("backup_protocols")
                    if row.get("created_at"):
                        device.created_at = row["created_at"]
            sb.table("devices").upsert(device.model_dump(mode='json')).execute()
        except Exception as e:
            raise _db_unavailable("register device", e)
    return device

def _device_owner(device_id: str) -> Optional[str]:
    """The user_id that owns a device, or None if it does not exist.

    Defensive about the response shape: PostgREST returns a list of dicts,
    and anything else is treated as "unknown", which the caller handles.
    """
    res = get_supabase().table("devices").select("user_id").eq("id", device_id).limit(1).execute()
    rows = getattr(res, "data", None)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return rows[0].get("user_id")


def _assert_agent_owns_device(identity: AgentIdentity, device_id: str) -> Optional[str]:
    """An agent token identifies an account. Writing to a device that belongs
    to a different account must be refused — otherwise one customer's agent
    could inject telemetry, alerts and incidents into another customer's
    fleet, which is the exact data-mixing the access-control change is meant
    to rule out.

    Returns the owning user_id (which the engines then stamp onto the rows
    they create). The legacy admin-wide AGENT_TOKEN has no owner and is
    trusted for any device, as before.
    """
    if not is_database_configured():
        return identity.user_id
    try:
        owner = _device_owner(device_id)
    except Exception as e:
        raise _db_unavailable("verify device ownership", e)

    if owner is None:
        # Unknown device: let the caller's insert fail on the foreign key,
        # which already produces a clear 422 telling the agent to re-register.
        return identity.user_id

    if identity.user_id and owner != identity.user_id:
        logger.warning(
            f"Agent for user {identity.user_id} tried to write to device {device_id} owned by {owner}"
        )
        raise HTTPException(status_code=404, detail="Device not found")
    return owner


@app.post("/api/agent/heartbeat")
def agent_heartbeat(payload: Dict[str, Any], identity: AgentIdentity = Depends(verify_agent_token)):
    device_id = payload.get("device_id")
    if not device_id or not isinstance(device_id, str):
        raise HTTPException(status_code=400, detail="Missing device_id")

    if is_database_configured():
        _assert_agent_owns_device(identity, device_id)
        try:
            query = get_supabase().table("devices").update({
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "status": "ONLINE"
            }).eq("id", device_id)
            # Scoped by owner as well as id: the ownership check above is the
            # gate, this makes the write itself un-crossable.
            if identity.user_id:
                query = query.eq("user_id", identity.user_id)
            query.execute()
        except Exception as e:
            raise _db_unavailable("record heartbeat", e)
    return {"status": "received"}

def _recent_losses(device_id: str, limit: int = 5) -> List[float]:
    """Packet loss from the last few reports, newest first.

    A ping run is 4 packets, so one dropped packet is 25% and a single sample
    says almost nothing. Anomaly detection needs to see whether it recurred.
    """
    if not is_database_configured():
        return []
    try:
        res = (
            get_supabase().table("telemetry").select("packet_loss")
            .eq("device_id", device_id).order("timestamp", desc=True).limit(limit).execute()
        )
    except Exception:
        return []
    return [r["packet_loss"] for r in (res.data or []) if r.get("packet_loss") is not None]


@app.post("/api/agent/telemetry")
def ingest_telemetry(telemetry: Telemetry, identity: AgentIdentity = Depends(verify_agent_token)):
    owner_id = identity.user_id
    if is_database_configured():
        owner_id = _assert_agent_owns_device(identity, telemetry.device_id)
        telemetry_dict = telemetry.model_dump(mode='json')
        try:
            get_supabase().table("telemetry").insert(telemetry_dict).execute()
        except Exception as e:
            # Postgres class 23 = integrity violation (e.g. 23503: device_id
            # references a device that no longer exists). That is bad input
            # from the agent, not a database outage — say so with a 4xx so
            # the agent stops retrying it.
            if str(getattr(e, "code", "")).startswith("23"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Telemetry rejected: {_db_error_reason(e)}. "
                           "If this device was re-registered, run the agent with --register again.",
                )
            raise _db_unavailable("store telemetry", e)

        # Trigger real-time alert and anomaly checks
        from src.alert_engine import evaluate_alerts
        from src.anomaly_engine import detect_anomalies
        from src.baseline_engine import maybe_update_baselines
        from src.incident_engine import evaluate_and_create_incidents
        
        try:
            # The owner is passed through so alerts and incidents are written
            # against the right account. Without it they land with a null
            # user_id and /api/incidents — which scopes by user_id — never
            # shows them to anyone.
            evaluate_alerts(telemetry.device_id, telemetry_dict, user_id=owner_id)
            # Nothing ever called this, so metric_baselines was permanently
            # empty: get_baseline returned zeros, the latency-anomaly branch
            # (guarded by `average > 0`) could never fire, and the packet-loss
            # branch compared against a baseline of 0. Throttled, because
            # recomputing over 1000 rows on every 60-second report is not.
            maybe_update_baselines(telemetry.device_id, user_id=owner_id)
            anomalies = detect_anomalies(telemetry.device_id, telemetry_dict,
                                         recent_losses=_recent_losses(telemetry.device_id))
            # Correlate anomalies to create/deduplicate open incidents in the database
            evaluate_and_create_incidents(telemetry.device_id, anomalies, [], user_id=owner_id)
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
