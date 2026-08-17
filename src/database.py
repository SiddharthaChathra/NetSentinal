import os
from supabase import create_client, Client
from src.logger import logger
from dotenv import load_dotenv

# Load environment variables from .env using an absolute path relative to this file
_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_root_dir, ".env")
load_dotenv(dotenv_path=_env_path)

_supabase: Client = None

def get_supabase() -> Client:
    """Returns a singleton Supabase client. Raises ValueError if credentials are not configured."""
    global _supabase
    if _supabase is not None:
        return _supabase
        
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    
    if not url or not key:
        logger.warning("SUPABASE_URL or SUPABASE_PUBLISHABLE_KEY is missing. Supabase integration disabled.")
        raise ValueError("Missing Supabase credentials in environment variables.")
        
    try:
        _supabase = create_client(url, key)
        logger.info("Supabase client initialized successfully.")
        return _supabase
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise

def is_database_configured() -> bool:
    """Check if the application has Supabase credentials configured."""
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    return bool(url and key)
