"""Where the agent keeps its configuration and state.

This exists because the agent now ships as a standalone executable as well as
a checkout of the repo, and the two need different answers.

Under PyInstaller, `__file__` points inside a temporary extraction directory
(`sys._MEIPASS`) that is deleted when the process exits, so the old
`Path(__file__).parent.parent / ".env"` resolved to a path that vanishes, and
`Path("agent_data")` resolved relative to whatever directory the user happened
to double-click from. Both are replaced here by a stable per-user location.

Resolution order for the data directory:
  1. NETSENTINEL_HOME, if set (tests, and anyone who wants it elsewhere)
  2. the repo's agent_data/ when running from a source checkout that already
     has one, so existing installs keep their device id and do not re-register
  3. the per-user application directory
"""
import os
import sys
from pathlib import Path

APP_NAME = "NetSentinel"


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """The directory the user actually launched, not the temp extraction dir."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def repo_root() -> Path | None:
    """The source checkout this agent lives in, or None when frozen."""
    if is_frozen():
        return None
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Per-user, per-machine application directory.

    Windows: %LOCALAPPDATA%\\NetSentinel
    macOS:   ~/Library/Application Support/NetSentinel
    Linux:   $XDG_DATA_HOME/netsentinel, else ~/.local/share/netsentinel
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base or Path.home() / "AppData" / "Local") / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / APP_NAME.lower() if base else Path.home() / ".local" / "share" / APP_NAME.lower()


def data_dir() -> Path:
    """The directory holding config.json and the telemetry buffer.

    An existing repo checkout keeps using its agent_data/ folder so upgrading
    does not orphan a registered device; anything else uses the per-user
    directory. Created on demand.
    """
    override = os.environ.get("NETSENTINEL_HOME", "").strip()
    if override:
        path = Path(override)
    else:
        root = repo_root()
        legacy = root / "agent_data" if root else None
        path = legacy if legacy and legacy.is_dir() else user_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "config.json"


def buffer_file() -> Path:
    return data_dir() / "telemetry_buffer.json"


def credentials_file() -> Path:
    """Where the agent token is stored once enrolled.

    Deliberately not the .env in a repo folder: a frozen binary has no repo,
    and a credential next to a file people copy around is a credential that
    gets copied around.
    """
    return data_dir() / "credentials.json"


def env_file() -> Path | None:
    """The .env of a source checkout, for installs that predate enrolment."""
    root = repo_root()
    if root is None:
        return None
    candidate = root / ".env"
    return candidate if candidate.exists() else None
