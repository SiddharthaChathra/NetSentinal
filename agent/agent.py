import time
import argparse
import httpx
import os
import platform
import socket
import json
from dotenv import load_dotenv
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent and agent directory to sys.path to support direct execution
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))
sys.path.insert(0, str(Path(__file__).parent.absolute()))

from collector import collect_telemetry
from agent_paths import (
    buffer_file, config_file, credentials_file, data_dir, env_file, is_frozen, repo_root,
)

AGENT_VERSION = "1.1.0"

# The hosted service. A source checkout can still point somewhere else with
# API_BASE_URL in .env; a downloaded binary has no .env and needs a default
# that actually works.
DEFAULT_BACKEND_URL = "https://netsentinal.onrender.com"

REPO_ROOT = repo_root()
ENV_FILE = env_file()
if ENV_FILE:
    load_dotenv(ENV_FILE)


def _load_saved_credentials() -> dict:
    """Credentials written by enrolment, if this machine has enrolled."""
    path = credentials_file()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_credentials(token: str, api_base_url: str):
    path = credentials_file()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"agent_token": token, "api_base_url": api_base_url}, f)
        # The token is a credential; do not leave it world-readable in a
        # shared home directory. Best effort - no-op on Windows.
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"Warning: could not save credentials to {path}: {e}")


_saved = _load_saved_credentials()

# Precedence: explicit environment > what enrolment saved > the default.
# Environment first so a developer pointing at a local backend is never
# overridden by a stale enrolment, and so existing .env installs keep working.
BACKEND_URL = (
    os.environ.get("API_BASE_URL", "").strip()
    or _saved.get("api_base_url", "").strip()
    or DEFAULT_BACKEND_URL
)
# Personal agent token. It both authenticates the agent and tells the server
# which account this device belongs to - the server attaches the device to
# the token's owner, which is why one token covers every machine you own.
AGENT_TOKEN = (
    os.environ.get("AGENT_TOKEN", "").strip()
    or _saved.get("agent_token", "").strip()
)
# Legacy/optional: only used when AGENT_TOKEN is an admin-wide token that
# doesn't identify a user.
USER_ID = os.environ.get("NETSENTINEL_USER_ID", "").strip() or None


def _check_configuration():
    """Fail fast with a plain-language explanation instead of a confusing
    'connection refused' when the agent has not been set up yet."""
    if AGENT_TOKEN:
        return

    print("This machine is not linked to a NetSentinel account yet.\n")
    print("  1. Sign in at the NetSentinel website")
    print("  2. Go to Devices and click 'Add a device'")
    print("  3. Run this agent with --enroll and type in the code it shows you\n")
    print(f"     {_self_invocation()} --enroll\n")
    if ENV_FILE is None and not is_frozen():
        print("(Running from a source checkout? You can also put API_BASE_URL and")
        print(f" AGENT_TOKEN in a .env file at {REPO_ROOT}.)")
    sys.exit(2)


def _self_invocation() -> str:
    """How to re-run this agent, phrased for how it was actually started."""
    if is_frozen():
        return Path(sys.executable).name
    return "python agent/agent.py"


def enroll(code: str = "") -> bool:
    """Link this machine to an account using a code from the website.

    The code is exchanged for the account's agent token, which is stored in
    the per-user data directory. One code per machine; the same account can
    enrol as many machines as it likes - that is what makes multi-device work
    without anyone copying a credential between computers.
    """
    # Declared up front: Python requires `global` before the name's first use
    # in the function, and BACKEND_URL is read below when building the URL.
    global AGENT_TOKEN, BACKEND_URL

    print("Link this machine to your NetSentinel account.")
    print("Get a code from the website: sign in, open Devices, click 'Add a device'.\n")

    code = (code or "").strip()
    if not code:
        try:
            code = input("Enrolment code: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return False
    if not code:
        print("No code entered.")
        return False

    try:
        r = httpx.post(
            f"{BACKEND_URL}/api/agent/enroll",
            json={"code": code, "hostname": socket.gethostname()},
            timeout=60,
        )
    except Exception as e:
        print(f"Could not reach {BACKEND_URL}: {e}")
        print("Check your internet connection and try again.")
        return False

    if r.status_code == 400:
        try:
            print(r.json().get("detail", "That code was not accepted."))
        except Exception:
            print("That code was not accepted.")
        return False
    if r.status_code >= 500:
        print("The NetSentinel service is waking up or unavailable. Try again in a minute.")
        return False
    try:
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        print(f"Enrolment failed: {_explain_http_error(e)}")
        return False

    token = body.get("token")
    if not token:
        print("The server did not return a token. Please generate a new code and try again.")
        return False

    # Deliberately saving the URL we just enrolled AGAINST, not the one the
    # response suggests. The server returns its own public address as a hint,
    # but a reply must never move an agent to a different host: a self-hosted
    # deployment would otherwise hand its own agents to the hosted service,
    # and the token we were just given is not valid there.
    _save_credentials(token, BACKEND_URL)

    # Applied immediately so --enroll can register in the same run, rather
    # than making the user start the agent a second time.
    AGENT_TOKEN = token

    print(f"\nLinked. Credentials saved to {credentials_file()}")
    return True


def _explain_http_error(e: Exception) -> str:
    """Turn an httpx HTTPStatusError into the server's own explanation
    (its JSON `detail`), which is far more useful than the status code."""
    resp = getattr(e, "response", None)
    if resp is None:
        return str(e)
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in detail)
    return f"HTTP {resp.status_code}: {detail or resp.text[:200] or resp.reason_phrase}"


def _get_local_ip() -> str:
    """Get the real local IP using a UDP socket trick (same as system_info.py)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Device identity and buffered telemetry live in a per-user directory, not
# next to the executable: a downloaded binary can be run from anywhere, and
# Downloads/ is not somewhere state should accumulate.
AGENT_DIR = data_dir()
CONFIG_FILE = config_file()
BUFFER_FILE = buffer_file()

def _load_device_config():
    """The cached device identity, but only if it belongs to THIS machine.

    The guard matters now the agent is a portable executable. Copying the data
    directory to a second machine used to make that machine report under the
    first one's device id - two machines silently collapsed into one device,
    with interleaved readings. A hostname mismatch means the config came from
    somewhere else, so it is discarded and this machine registers as itself.
    """
    if not CONFIG_FILE.exists():
        return None
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except Exception:
        return None
    if not isinstance(config, dict) or not config.get("id"):
        return None

    recorded = config.get("hostname")
    current = socket.gethostname()
    if recorded and recorded != current:
        print(f"This configuration belongs to '{recorded}' but this machine is '{current}'.")
        print("Registering as a separate device so the two do not report as one.")
        BUFFER_FILE.unlink(missing_ok=True)
        return None
    return config


def get_or_create_device():
    cached = _load_device_config()
    if cached:
        return cached

    # Need to register
    now_str = datetime.now(timezone.utc).isoformat()
    device_data = {
        "user_id": USER_ID,
        "name": socket.gethostname(),
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "ip_address": _get_local_ip(),
        "agent_version": "1.0.0",
        "status": "ONLINE",
        "last_seen": now_str,
        "created_at": now_str,
        "updated_at": now_str
    }
    
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    
    try:
        r = httpx.post(f"{BACKEND_URL}/api/agent/register", json=device_data, headers=headers, timeout=60)
        if r.status_code == 401:
            print("The server rejected the agent token (401). Open the website while signed in, go to "
                  "Getting Started, and copy the current AGENT_TOKEN line into .env — tokens change if "
                  "you generated a new one on the page.")
            return None
        if r.status_code == 422:
            print(f"Validation error details: {r.text}")
        r.raise_for_status()
        device_config = r.json()
        with open(CONFIG_FILE, "w") as f:
            json.dump(device_config, f)
        print(f"Registered new device: {device_config.get('id')}")
        return device_config
    except Exception as e:
        print(f"Failed to register device: {_explain_http_error(e)}")
        return None

def send_heartbeat(device_id: str):
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "agent_version": "1.0.0"}
    try:
        httpx.post(f"{BACKEND_URL}/api/agent/heartbeat", json=payload, headers=headers)
    except:
        pass

MAX_BUFFER_RETRIES = 5


def flush_buffer(current_device_id: str):
    """Replays reports that failed to send earlier. Reports that can never
    succeed are dropped instead of being retried forever: anything from a
    previous device identity (a re-register creates a new device id), anything
    the server rejects as bad data (4xx), and anything that has already
    failed MAX_BUFFER_RETRIES times."""
    if not BUFFER_FILE.exists(): return
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}

    try:
        with open(BUFFER_FILE, "r") as f:
            buffer = json.load(f)
    except Exception:
        BUFFER_FILE.unlink(missing_ok=True)
        return

    remaining, dropped, reason = [], 0, None
    for item in buffer:
        if item.get("device_id") != current_device_id:
            dropped += 1
            reason = reason or "belongs to a previous device registration"
            continue
        try:
            r = httpx.post(f"{BACKEND_URL}/api/agent/telemetry", json=item, headers=headers, timeout=30)
            if r.status_code < 300:
                continue
            if 400 <= r.status_code < 500:
                dropped += 1
                reason = reason or _explain_http_error(httpx.HTTPStatusError("", request=r.request, response=r))
                continue
            raise httpx.HTTPStatusError("", request=r.request, response=r)
        except Exception as e:
            item["_retries"] = item.get("_retries", 0) + 1
            if item["_retries"] > MAX_BUFFER_RETRIES:
                dropped += 1
                reason = reason or _explain_http_error(e)
            else:
                remaining.append(item)

    if dropped:
        print(f"Dropped {dropped} buffered report(s) that cannot be delivered ({reason}).")
    if remaining:
        with open(BUFFER_FILE, "w") as f:
            json.dump(remaining, f)
    else:
        BUFFER_FILE.unlink(missing_ok=True)

def save_to_buffer(telemetry: dict):
    buffer = []
    if BUFFER_FILE.exists():
        try:
            with open(BUFFER_FILE, "r") as f:
                buffer = json.load(f)
        except:
            pass
    # Limit buffer size
    buffer.append(telemetry)
    if len(buffer) > 100: buffer = buffer[-100:]
    with open(BUFFER_FILE, "w") as f:
        json.dump(buffer, f)

def run_once():
    device = get_or_create_device()
    if not device: return
    
    send_heartbeat(device["id"])
    flush_buffer(device["id"])
    
    telemetry = collect_telemetry(device["id"])
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    
    try:
        r = httpx.post(f"{BACKEND_URL}/api/agent/telemetry", json=telemetry, headers=headers)
        r.raise_for_status()
        print("Telemetry sent successfully.")
    except Exception as e:
        resp = getattr(e, "response", None)
        if resp is not None and 400 <= resp.status_code < 500:
            # The server rejected the data itself; retrying identical data can't help.
            print(f"Telemetry rejected by server, not buffering: {_explain_http_error(e)}")
        else:
            print(f"Failed to send telemetry. Saving to buffer. ({_explain_http_error(e)})")
            save_to_buffer(telemetry)

def start_agent():
    print(f"NetSentinel agent {AGENT_VERSION} - reporting {socket.gethostname()} to {BACKEND_URL}")
    print(f"Data directory: {AGENT_DIR}")
    while True:
        run_once()
        time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog=_self_invocation(),
        description="NetSentinel agent - reports this machine's network health to your account.",
    )
    parser.add_argument("--enroll", nargs="?", const="", metavar="CODE",
                        help="Link this machine to your account using a code from the website")
    parser.add_argument("--once", action="store_true", help="Run telemetry collection once")
    parser.add_argument("--register", action="store_true", help="Force registration")
    parser.add_argument("--start", action="store_true", help="Start agent loop")
    parser.add_argument("--status", action="store_true", help="Show what this machine is linked to")
    parser.add_argument("--version", action="version", version=f"NetSentinel agent {AGENT_VERSION}")

    args = parser.parse_args()

    # Double-clicked with no arguments: a downloaded binary has no terminal
    # to print help into, so do what a new user actually wants - enrol, then
    # start reporting.
    if is_frozen() and len(sys.argv) == 1:
        if not AGENT_TOKEN and not enroll():
            input("\nPress Enter to close...")
            sys.exit(1)
        get_or_create_device()
        start_agent()
        sys.exit(0)

    if args.enroll is not None:
        if not enroll(args.enroll):
            sys.exit(1)
        get_or_create_device()
        print("\nThis machine now appears on the Devices page.")
        print(f"Keep it reporting with:  {_self_invocation()} --start")
        sys.exit(0)

    if args.status:
        device = _load_device_config()
        print(f"Agent version : {AGENT_VERSION}")
        print(f"Server        : {BACKEND_URL}")
        print(f"Data directory: {AGENT_DIR}")
        print(f"Linked        : {'yes' if AGENT_TOKEN else 'no - run --enroll'}")
        print(f"Hostname      : {socket.gethostname()}")
        print(f"Device id     : {device.get('id') if device else 'not registered on this machine'}")
        sys.exit(0)

    if args.register or args.once or args.start:
        _check_configuration()

    if args.register:
        # A new registration means a new device id; buffered reports for the
        # old one can never be delivered.
        if CONFIG_FILE.exists(): CONFIG_FILE.unlink()
        BUFFER_FILE.unlink(missing_ok=True)
        get_or_create_device()
    elif args.once:
        run_once()
    elif args.start:
        start_agent()
    else:
        parser.print_help()
