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

def verify_agent_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency to verify an agent token for telemetry ingestion."""
    if not is_database_configured():
        return True
        
    expected_token = os.environ.get("AGENT_TOKEN")
    if not expected_token:
        # Allow bypass if AGENT_TOKEN is not configured in .env
        return True
        
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent token")
        
    token = credentials.credentials
    
    if token != expected_token:
        logger.warning("Agent authentication failed: Invalid token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
        
    return True
