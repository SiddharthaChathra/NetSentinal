import os
from supabase import create_client, Client, ClientOptions
from src.logger import logger
from dotenv import load_dotenv

# Hard ceilings on every Supabase round-trip. Without these a paused or
# unreachable project makes API requests hang for the httpx default, which
# is what the frontend then reports as "backend unreachable".
DB_TIMEOUT_S = float(os.environ.get("SUPABASE_TIMEOUT_SECONDS", 8))

# Load environment variables from .env using an absolute path relative to this file
_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_root_dir, ".env")
load_dotenv(dotenv_path=_env_path)

_supabase: Client = None
_access_mode: str = "unconfigured"


def _url() -> str:
    return os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""


def _service_key() -> str:
    """The server-side (secret / service_role) key. Bypasses Row-Level
    Security, which is what a trusted backend needs: every query in this
    codebase scopes by user_id itself. This key must NEVER reach the
    frontend."""
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY") or ""


def _publishable_key() -> str:
    return os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or ""


def get_supabase() -> Client:
    """Returns a singleton Supabase client. Raises ValueError if credentials are not configured."""
    global _supabase, _access_mode
    if _supabase is not None:
        return _supabase

    url = _url()
    key = _service_key()
    mode = "service"
    if not key:
        key = _publishable_key()
        mode = "publishable"
        if key:
            # Publishable key + no user session => auth.uid() is NULL, so every
            # `auth.uid() = user_id` RLS policy denies. User data (history,
            # devices, incidents) silently fails to read/write in this mode.
            logger.warning(
                "SUPABASE_SERVICE_ROLE_KEY is not set; falling back to the publishable key. "
                "Row-Level Security will block all per-user reads and writes from the backend. "
                "Set SUPABASE_SERVICE_ROLE_KEY (Supabase -> Settings -> API Keys -> service_role / secret)."
            )

    if not url or not key:
        logger.warning("SUPABASE_URL or a Supabase key is missing. Supabase integration disabled.")
        raise ValueError("Missing Supabase credentials in environment variables.")

    try:
        _supabase = create_client(url, key, options=ClientOptions(
            postgrest_client_timeout=DB_TIMEOUT_S,
            storage_client_timeout=DB_TIMEOUT_S,
            function_client_timeout=DB_TIMEOUT_S,
        ))
        _access_mode = mode
        logger.info(f"Supabase client initialized ({mode} key).")
        return _supabase
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise


def is_database_configured() -> bool:
    """Check if the application has Supabase credentials configured."""
    return bool(_url() and (_service_key() or _publishable_key()))


def database_access_mode() -> str:
    """'service' (RLS bypassed, per-user data works), 'publishable' (RLS
    blocks per-user data), or 'unconfigured'."""
    if not is_database_configured():
        return "unconfigured"
    return "service" if _service_key() else "publishable"
