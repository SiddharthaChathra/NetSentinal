from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.database import get_supabase, is_database_configured
from src.logger import logger
from src.session import Principal, auth_required, resolve_access_token
import os

security = HTTPBearer(auto_error=False)


def _principal_for(request: Request, credentials: HTTPAuthorizationCredentials) -> "Principal | None":
    """The caller's principal.

    Normally AuthGateMiddleware has already resolved (and cached) it, so this
    is a state lookup. The fallback path validates the header directly, which
    keeps the dependency correct on its own for tests and for any future route
    mounted outside the middleware.
    """
    principal = getattr(request.state, "principal", None) if request is not None else None
    if principal is not None:
        return principal
    if not auth_required():
        return resolve_access_token(None)
    return resolve_access_token(credentials.credentials if credentials else None)


def get_current_user(request: Request = None, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """The authenticated user. 401 if there is no valid session.

    Every data endpoint depends on this; the middleware in front of them
    rejects sessionless requests earlier still, so reaching a 401 from here
    means the token was present but not valid.
    """
    principal = _principal_for(request, credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to access NetSentinel.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal.user


def get_optional_user(request: Request = None, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """The user if a valid session is present, else None. Never raises.

    Only the public endpoints use this now — /api/auth/session has to be able
    to answer "no" without erroring. Data endpoints must use get_current_user.
    """
    principal = _principal_for(request, credentials)
    return principal.user if principal else None


def get_principal(request: Request = None, credentials: HTTPAuthorizationCredentials = Depends(security)) -> Principal:
    """Like get_current_user but returns the Principal, including org scope."""
    principal = _principal_for(request, credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to access NetSentinel.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal

import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

AGENT_TOKEN_PREFIX = "nsa_"
_TOKEN_CACHE_TTL_S = 60
_token_cache: dict = {}  # token -> (user_id, cached_at)


class AgentIdentity:
    """Who an agent request is acting for. `user_id` is None only for the
    legacy/admin shared AGENT_TOKEN or when auth is disabled locally."""
    def __init__(self, user_id: Optional[str], kind: str):
        self.user_id = user_id
        self.kind = kind  # "user" | "admin" | "open"


def generate_agent_token() -> str:
    return AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def get_or_create_agent_token(user_id: str) -> str:
    """Returns the user's personal agent token, creating one on first use."""
    supabase = get_supabase()
    res = supabase.table("agent_tokens").select("token").eq("user_id", user_id).limit(1).execute()
    if res.data:
        return res.data[0]["token"]
    token = generate_agent_token()
    supabase.table("agent_tokens").insert({"user_id": user_id, "token": token}).execute()
    return token


def rotate_agent_token(user_id: str) -> str:
    """Replaces the user's token; the old one stops working immediately."""
    supabase = get_supabase()
    token = generate_agent_token()
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("agent_tokens").upsert(
        {"user_id": user_id, "token": token, "rotated_at": now}
    ).execute()
    _token_cache.clear()
    return token


def _lookup_user_token(token: str) -> Optional[str]:
    now = time.monotonic()
    cached = _token_cache.get(token)
    if cached and now - cached[1] < _TOKEN_CACHE_TTL_S:
        return cached[0]
    res = get_supabase().table("agent_tokens").select("user_id").eq("token", token).limit(1).execute()
    user_id = res.data[0]["user_id"] if res.data else None
    if user_id:
        _token_cache[token] = (user_id, now)
        try:
            get_supabase().table("agent_tokens").update(
                {"last_used_at": datetime.now(timezone.utc).isoformat()}
            ).eq("token", token).execute()
        except Exception:
            pass
    return user_id


def verify_agent_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> AgentIdentity:
    """Dependency for agent endpoints. Accepts either a per-user token
    (issued on the Getting Started page; identifies the owner) or the
    optional admin-wide AGENT_TOKEN env var (legacy; no owner)."""
    if not is_database_configured():
        return AgentIdentity(None, "open")

    admin_token = os.environ.get("AGENT_TOKEN", "")

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent token")

    token = credentials.credentials

    if admin_token and hmac.compare_digest(token, admin_token):
        return AgentIdentity(None, "admin")

    if token.startswith(AGENT_TOKEN_PREFIX):
        try:
            user_id = _lookup_user_token(token)
        except Exception as e:
            logger.error(f"Agent token lookup failed: {e}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not verify agent token")
        if user_id:
            return AgentIdentity(user_id, "user")

    logger.warning("Agent authentication failed: Invalid token")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
