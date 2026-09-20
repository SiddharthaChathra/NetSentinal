from typing import List, Dict, Any, Optional
import statistics
from datetime import datetime, timezone
from src.database import get_supabase, is_database_configured
from src.logger import logger

def calculate_statistical_baseline(data_points: List[float]) -> Dict[str, float]:
    """Calculate average, median, p95, and stddev from a list of floats."""
    if not data_points:
        return {"average": 0.0, "median": 0.0, "p95": 0.0, "stddev": 0.0}
        
    n = len(data_points)
    avg = statistics.mean(data_points)
    median = statistics.median(data_points)
    
    # Calculate P95
    sorted_data = sorted(data_points)
    p95_idx = int(0.95 * n)
    p95 = sorted_data[p95_idx] if p95_idx < n else sorted_data[-1]
    
    # Calculate Stddev
    stddev = statistics.stdev(data_points) if n > 1 else 0.0
    
    return {
        "average": round(avg, 2),
        "median": round(median, 2),
        "p95": round(p95, 2),
        "stddev": round(stddev, 2)
    }

# Recomputing over 1000 telemetry rows on every 60-second report, for every
# device, would cost far more than it is worth. Once every 15 minutes per
# device keeps the baseline current enough to judge an anomaly against.
_BASELINE_MIN_INTERVAL_S = 900
_last_baseline_update: dict = {}


def maybe_update_baselines(device_id: str, min_interval_s: int = _BASELINE_MIN_INTERVAL_S,
                           user_id: Optional[str] = None) -> bool:
    """Recompute this device's baselines if they are stale. Returns whether
    it ran. Never raises: a missing baseline degrades detection, it must not
    fail an agent's telemetry post."""
    import time
    now = time.monotonic()
    last = _last_baseline_update.get(device_id)
    if last is not None and now - last < min_interval_s:
        return False
    _last_baseline_update[device_id] = now
    try:
        update_device_baselines(device_id, user_id=user_id)
        return True
    except Exception as e:
        logger.warning(f"Baseline update failed for {device_id}: {e}")
        return False


def baseline_row_id(device_id: str, metric: str) -> str:
    """A deterministic primary key, one row per (device, metric).

    metric_baselines has `id TEXT PRIMARY KEY` and no unique constraint on
    (device_id, metric), so the previous `on_conflict="device_id, metric"`
    could not work - Postgres answered 42P10, "no unique or exclusion
    constraint matching the ON CONFLICT specification" - and even with that
    fixed the insert would have failed on a null primary key, because no id
    was supplied.

    Deriving the id from the pair makes the primary key itself the conflict
    target, so a plain upsert does the right thing and a device can never
    accumulate duplicate baselines for one metric. No migration needed.
    """
    return f"{device_id}:{metric}"


def update_device_baselines(device_id: str, window_hours: int = 24, user_id: Optional[str] = None):
    """Fetch recent telemetry for a device and update its baselines in the database."""
    if not is_database_configured():
        return
        
    try:
        supabase = get_supabase()
        # Fetch recent telemetry
        # Note: In a real system you'd filter by timestamp >= NOW() - window_hours
        # For simplicity, we just fetch the last 1000 records for the device.
        res = supabase.table("telemetry").select("latency_ms, packet_loss").eq("device_id", device_id).order("timestamp", desc=True).limit(1000).execute()
        
        if not res.data:
            return
            
        latencies = [r["latency_ms"] for r in res.data if r.get("latency_ms") is not None]
        losses = [r["packet_loss"] for r in res.data if r.get("packet_loss") is not None]
        
        # Calculate
        latency_baseline = calculate_statistical_baseline(latencies)
        loss_baseline = calculate_statistical_baseline(losses)
        
        now = datetime.now(timezone.utc).isoformat()

        def row(metric: str, stats: Dict[str, float]) -> Dict[str, Any]:
            entry = {
                "id": baseline_row_id(device_id, metric),
                "device_id": device_id,
                "metric": metric,
                "window": f"{window_hours}h",
                "average": stats["average"],
                "median": stats["median"],
                "p95": stats["p95"],
                "stddev": stats["stddev"],
                "updated_at": now,
            }
            # Baselines are per-account data like everything else; leaving
            # user_id null would make the row invisible to its owner under RLS.
            if user_id:
                entry["user_id"] = user_id
            return entry

        # Conflict target is the primary key, which is what `id` now encodes.
        supabase.table("metric_baselines").upsert(
            [row("latency", latency_baseline), row("packet_loss", loss_baseline)]
        ).execute()

        logger.info(f"Updated baselines for device {device_id} from {len(latencies)} samples")
    except Exception as e:
        logger.error(f"Failed to update baselines: {e}")

def get_baseline(device_id: str, metric: str) -> Dict[str, float]:
    """Retrieve a specific baseline for a device. Returns defaults if none exists."""
    if not is_database_configured():
         return {"average": 0.0, "median": 0.0, "p95": 0.0, "stddev": 0.0}
         
    try:
        res = get_supabase().table("metric_baselines").select("*").eq("device_id", device_id).eq("metric", metric).execute()
        if res.data:
            b = res.data[0]
            return {
                "average": b.get("average", 0.0),
                "median": b.get("median", 0.0),
                "p95": b.get("p95", 0.0),
                "stddev": b.get("stddev", 0.0)
            }
    except Exception as e:
        logger.warning(f"Error fetching baseline for {device_id}/{metric}: {e}")
        
    return {"average": 0.0, "median": 0.0, "p95": 0.0, "stddev": 0.0}
