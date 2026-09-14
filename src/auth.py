from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.database import get_supabase, is_database_configured
from src.logger import logger
import os

security = HTTPBearer(auto_error=False)

def _validate_token(credentials: HTTPAuthorizationCredentials):
    """Internal helper: validates a Supabase JWT and returns the user object."""
    token = credentials.credentials
    supabase = get_supabase()
    user_response = supabase.auth.get_user(token)
    if not user_response or not user_response.user:
        raise ValueError("Invalid token")
    return user_response.user

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency to get the current authenticated user from Supabase.
    Raises 401 if no valid token is present. Use for strictly protected endpoints."""
    if not is_database_configured():
        return {"id": "local-dev-user", "role": "admin"}
        
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    try:
        return _validate_token(credentials)
    except Exception as e:
        logger.warning(f"Auth failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency that returns the user if a valid token is present, or None for guests.
    Never raises 401 — designed for the progressive/freemium auth model."""
    if not is_database_configured():
        return {"id": "local-dev-user", "role": "admin"}
    
    if not credentials:
        return None  # Guest user — no token provided
    
    try:
        return _validate_token(credentials)
    except Exception as e:
        logger.warning(f"Optional auth token invalid: {str(e)}")
        return None  # Treat bad tokens as guest too

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
