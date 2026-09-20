from typing import Dict, Any, Optional
from datetime import datetime, timezone
from src.database import get_supabase, is_database_configured
from src.logger import logger
from src.models import Alert

def evaluate_alerts(device_id: str, telemetry: Dict[str, Any], user_id: Optional[str] = None):
    """
    Evaluates basic static threshold alert rules.

    `user_id` is the device owner from the agent token. Alerts are per-account
    data and the table is scoped by user_id, so writing one without an owner
    leaves an orphan row no account can see.
    """
    if not is_database_configured():
        return
        
    try:
        supabase = get_supabase()
        
        # Hardcoded simple rules for this version (could be fetched from DB)
        rules = [
            {"type": "latency_threshold", "threshold": 200.0, "metric": "latency_ms"},
            {"type": "packet_loss_threshold", "threshold": 10.0, "metric": "packet_loss"},
        ]
        
        new_alerts = []
        for rule in rules:
            val = telemetry.get(rule["metric"], 0.0)
            if val > rule["threshold"]:
                new_alerts.append(Alert(
                    user_id=user_id,
                    device_id=device_id,
                    type=rule["type"],
                    threshold=rule["threshold"],
                    current_value=val,
                    status="ACTIVE",
                    created_at=datetime.now(timezone.utc)
                ))
                
        for alert in new_alerts:
            supabase.table("alerts").insert(alert.model_dump(mode='json')).execute()
            logger.info(f"Triggered alert {alert.type} for device {device_id}")
            
    except Exception as e:
        logger.error(f"Failed to evaluate alerts: {e}")
