import sqlite3
import os
from src.logger import logger
from src.models import DiagnosticResult

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "netsentinel.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        score INTEGER,
        status TEXT,
        gateway_status TEXT,
        internet_status TEXT,
        dns_status TEXT,
        tcp_status TEXT,
        latency REAL,
        packet_loss REAL,
        is_demo BOOLEAN
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized.")

def save_diagnostic_run(result: DiagnosticResult):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Enforce limit of 1000 records (delete oldest if at limit)
        cursor.execute("SELECT COUNT(*) FROM history")
        count = cursor.fetchone()[0]
        if count >= 1000:
            cursor.execute("DELETE FROM history WHERE id IN (SELECT id FROM history ORDER BY id ASC LIMIT ?)", (count - 999,))
            
        gw_status = "PASS" if result.gateway.get("reachable") else "FAIL"
        int_status = "PASS" if result.internet.get("reachable") else "FAIL"
        
        dns_pass = any(d.get("success", False) for d in result.dns) if result.dns else False
        dns_status = "PASS" if dns_pass else "FAIL"
        if not result.dns: dns_status = "UNAVAILABLE"
        
        tcp_pass = any(t.get("success", False) for t in result.tcp) if result.tcp else False
        tcp_status = "PASS" if tcp_pass else "FAIL"
        if not result.tcp: tcp_status = "UNAVAILABLE"
        
        cursor.execute('''
        INSERT INTO history 
        (timestamp, score, status, gateway_status, internet_status, dns_status, tcp_status, latency, packet_loss, is_demo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result.timestamp,
            result.health_score,
            result.status,
            gw_status,
            int_status,
            dns_status,
            tcp_status,
            result.internet.get("latency_ms", 0.0),
            result.internet.get("packet_loss", 100.0),
            result.is_demo
        ))
        
        conn.commit()
        conn.close()
        logger.info("Diagnostic run saved to history.")
    except Exception as e:
        logger.error(f"Failed to save diagnostic run to history: {e}")

def get_history(limit=100):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch history: {e}")
        return []

def save_diagnostic_run_supabase(result: DiagnosticResult, user_id: str):
    from src.database import get_supabase, is_database_configured
    if not is_database_configured():
        return
    try:
        supabase = get_supabase()
        
        gw_status = "PASS" if result.gateway.get("reachable") else "FAIL"
        int_status = "PASS" if result.internet.get("reachable") else "FAIL"
        
        dns_pass = any(d.get("success", False) for d in result.dns) if result.dns else False
        dns_status = "PASS" if dns_pass else "FAIL"
        if not result.dns: dns_status = "UNAVAILABLE"
        
        tcp_pass = any(t.get("success", False) for t in result.tcp) if result.tcp else False
        tcp_status = "PASS" if tcp_pass else "FAIL"
        if not result.tcp: tcp_status = "UNAVAILABLE"
        
        entry = {
            "user_id": user_id,
            "timestamp": result.timestamp,
            "score": result.health_score,
            "status": result.status,
            "gateway_status": gw_status,
            "internet_status": int_status,
            "dns_status": dns_status,
            "tcp_status": tcp_status,
            "latency": result.internet.get("latency_ms", 0.0),
            "packet_loss": result.internet.get("packet_loss", 100.0),
            "is_demo": result.is_demo
        }
        supabase.table("history").insert(entry).execute()
        logger.info(f"Diagnostic run saved to Supabase history for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to save diagnostic run to Supabase: {e}")

def get_history_supabase(user_id: str, limit: int = 100):
    from src.database import get_supabase, is_database_configured
    if not is_database_configured():
        return []
    try:
        supabase = get_supabase()
        res = supabase.table("history")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("id", desc=True)\
            .limit(limit)\
            .execute()
        return res.data if res.data else []
    except Exception as e:
        logger.error(f"Failed to fetch history from Supabase: {e}")
        return []

# Initialize on module load
init_db()
