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

load_dotenv()

BACKEND_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")

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
        r = httpx.post(f"{BACKEND_URL}/api/agent/register", json=device_data, headers=headers)
        if r.status_code == 422:
            print(f"Validation error details: {r.text}")
        r.raise_for_status()
        device_config = r.json()
        with open(CONFIG_FILE, "w") as f:
            json.dump(device_config, f)
        print(f"Registered new device: {device_config.get('id')}")
        return device_config
    except Exception as e:
        print(f"Failed to register device. Backend might be unreachable: {e}")
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
        print(f"Failed to send telemetry. Saving to buffer. ({e})")
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
    
    if args.register:
        if CONFIG_FILE.exists(): CONFIG_FILE.unlink()
        get_or_create_device()
    elif args.once:
        run_once()
    elif args.start:
        start_agent()
    else:
        parser.print_help()
