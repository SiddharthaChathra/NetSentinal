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

REPO_ROOT = Path(__file__).parent.parent.absolute()
ENV_FILE = REPO_ROOT / ".env"
load_dotenv(ENV_FILE)

BACKEND_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
# Personal agent token from the website's Getting Started page. It both
# authenticates the agent and tells the server which account this device
# belongs to — the server attaches the device to the token's owner.
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "").strip()
# Legacy/optional: only used when AGENT_TOKEN is an admin-wide token that
# doesn't identify a user.
USER_ID = os.environ.get("NETSENTINEL_USER_ID", "").strip() or None


def _check_configuration():
    """Fail fast with a plain-language explanation instead of a confusing
    'connection refused' when the .env from the setup guide is missing."""
    problems = []
    if not ENV_FILE.exists():
        problems.append(f"No .env file found at {ENV_FILE}")
    if "API_BASE_URL" not in os.environ:
        problems.append("API_BASE_URL is not set (the agent would try http://localhost:8000, which is not your server)")
    if not AGENT_TOKEN:
        problems.append("AGENT_TOKEN is not set (the server would reject the agent, and the device could not be linked to your account)")
    if not problems:
        return
    print("The agent is not configured yet:\n")
    for p in problems:
        print(f"  - {p}")
    print(
        "\nOpen the website while signed in, go to Getting Started, and copy the two lines from\n"
        f"step 3 into a file named .env in this folder:\n    {REPO_ROOT}\n"
        "It should look like:\n"
        "    API_BASE_URL=https://netsentinal.onrender.com\n"
        "    AGENT_TOKEN=nsa_...your personal token from the page...\n"
        "Then run this command again."
    )
    sys.exit(2)

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

# We'll generate/store a consistent device ID in a local file
AGENT_DIR = Path("agent_data")
AGENT_DIR.mkdir(exist_ok=True)
CONFIG_FILE = AGENT_DIR / "config.json"
BUFFER_FILE = AGENT_DIR / "telemetry_buffer.json"

def get_or_create_device():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
            
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

def flush_buffer():
    if not BUFFER_FILE.exists(): return
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    
    try:
        with open(BUFFER_FILE, "r") as f:
            buffer = json.load(f)
            
        remaining = []
        for item in buffer:
            try:
                r = httpx.post(f"{BACKEND_URL}/api/agent/telemetry", json=item, headers=headers)
                r.raise_for_status()
            except:
                remaining.append(item)
                
        if remaining:
            with open(BUFFER_FILE, "w") as f:
                json.dump(remaining, f)
        else:
            BUFFER_FILE.unlink()
    except:
        pass

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
    flush_buffer()
    
    telemetry = collect_telemetry(device["id"])
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    
    try:
        r = httpx.post(f"{BACKEND_URL}/api/agent/telemetry", json=telemetry, headers=headers)
        r.raise_for_status()
        print("Telemetry sent successfully.")
    except Exception as e:
        print(f"Failed to send telemetry. Saving to buffer. ({_explain_http_error(e)})")
        save_to_buffer(telemetry)

def start_agent():
    print(f"Starting NetSentinel Agent (Target: {BACKEND_URL})")
    while True:
        run_once()
        time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run telemetry collection once")
    parser.add_argument("--register", action="store_true", help="Force registration")
    parser.add_argument("--start", action="store_true", help="Start agent loop")
    
    args = parser.parse_args()

    if args.register or args.once or args.start:
        _check_configuration()

    if args.register:
        if CONFIG_FILE.exists(): CONFIG_FILE.unlink()
        get_or_create_device()
    elif args.once:
        run_once()
    elif args.start:
        start_agent()
    else:
        parser.print_help()
