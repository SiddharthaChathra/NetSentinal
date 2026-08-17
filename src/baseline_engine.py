from typing import List, Dict, Any
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

def update_device_baselines(device_id: str, window_hours: int = 24):
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
        
        # Upsert Latency Baseline
        supabase.table("metric_baselines").upsert({
            "device_id": device_id,
            "metric": "latency",
            "window": f"{window_hours}h",
            "average": latency_baseline["average"],
            "median": latency_baseline["median"],
            "p95": latency_baseline["p95"],
            "stddev": latency_baseline["stddev"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="device_id, metric").execute()
        
        # Upsert Loss Baseline
        supabase.table("metric_baselines").upsert({
            "device_id": device_id,
            "metric": "packet_loss",
            "window": f"{window_hours}h",
            "average": loss_baseline["average"],
            "median": loss_baseline["median"],
            "p95": loss_baseline["p95"],
            "stddev": loss_baseline["stddev"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="device_id, metric").execute()
        
        logger.info(f"Updated baselines for device {device_id}")
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
