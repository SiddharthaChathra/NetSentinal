from typing import List, Dict, Any
from datetime import datetime, timezone
from src.database import get_supabase, is_database_configured
from src.logger import logger
from src.models import Incident

def evaluate_and_create_incidents(device_id: str, anomalies: List[Dict[str, Any]], diagnostics: List[Any]):
    """
    Evaluates anomalies and diagnostic findings to create or deduplicate incidents.
    """
    if not is_database_configured():
        return
        
    try:
        supabase = get_supabase()
        
        # 1. Fetch Open Incidents for this device to deduplicate
        res = supabase.table("incidents").select("*").eq("device_id", device_id).eq("status", "OPEN").execute()
        open_incidents = {i["title"]: i for i in res.data} if res.data else {}
        
        new_incidents = []
        
        # 2. Process Anomalies
        for anomaly in anomalies:
            title = anomaly["title"]
            if title not in open_incidents:
                new_incidents.append(Incident(
                    device_id=device_id,
                    title=title,
                    severity=anomaly["severity"],
                    status="OPEN",
                    likely_cause=anomaly.get("reason", "Anomaly detected"),
                    confidence="HIGH" if anomaly["severity"] == "critical" else "MEDIUM",
                    evidence=[anomaly.get("reason", "")],
                    recommended_actions=[],
                    started_at=datetime.now(timezone.utc)
                ))
                
        # 3. Process Diagnostic Findings (Dependency-aware correlation output)
        for diag in diagnostics:
            # diag is a DiagnosticFinding object
            if hasattr(diag, 'model_dump'):
                diag_dict = diag.model_dump()
            else:
                diag_dict = diag
                
            title = diag_dict["title"]
            if title not in open_incidents:
                new_incidents.append(Incident(
                    device_id=device_id,
                    title=title,
                    severity=diag_dict["severity"],
                    status="OPEN",
                    likely_cause=diag_dict["likely_cause"],
                    confidence=diag_dict["confidence"],
                    evidence=diag_dict["evidence"],
                    recommended_actions=diag_dict["recommended_checks"],
                    started_at=datetime.now(timezone.utc)
                ))
                
        # 4. Insert new incidents
        for inc in new_incidents:
            supabase.table("incidents").insert(inc.model_dump(mode='json')).execute()
            logger.info(f"Created new incident: {inc.title} for device {device_id}")
            
    except Exception as e:
        logger.error(f"Failed to evaluate incidents: {e}")
