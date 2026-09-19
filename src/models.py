from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid

# --- Existing Core Diagnostics Models ---

class DiagnosticFinding(BaseModel):
    severity: str
    title: str
    likely_cause: str
    evidence: List[str]
    recommended_checks: List[str]
    confidence: str

class DiagnosticResult(BaseModel):
    timestamp: str
    health_score: int
    status: str
    system: Dict[str, Any]
    interfaces: List[Dict[str, Any]]
    gateway: Dict[str, Any]
    internet: Dict[str, Any]
    dns: List[Dict[str, Any]]
    tcp: List[Dict[str, Any]]
    routes: Dict[str, Any]
    stability: Dict[str, Any]
    diagnostics: List[DiagnosticFinding]
    duration_ms: int
    is_demo: bool = False

class HistoryEntry(BaseModel):
    id: int
    timestamp: str
    score: int
    status: str
    gateway_status: str
    internet_status: str
    dns_status: str
    tcp_status: str
    latency: float
    packet_loss: float
    is_demo: bool

# --- New Platform Models ---

class Device(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    name: str
    hostname: str
    platform: str
    architecture: str
    ip_address: str
    agent_version: str
    status: str
    is_backup_target: bool = False
    last_seen: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

class DiagnosticRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    device_id: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    health_score: int
    status: str
    mode: str

class Telemetry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    device_id: str
    timestamp: datetime
    latency_ms: float
    packet_loss: float
    gateway_reachable: bool
    internet_reachable: bool
    dns_healthy: bool
    tcp_healthy: bool
    interface_errors: int
    interface_drops: int
    # Backup-protocol ports as checked by the agent on its own host
    # ({port, service, open}); None from agents that predate this field.
    backup_ports: Optional[List[Dict[str, Any]]] = None

class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    device_id: str
    title: str
    severity: str
    status: str
    likely_cause: str
    confidence: str
    evidence: List[str]
    recommended_actions: List[str]
    started_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    device_id: str
    type: str
    threshold: float
    current_value: float
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None

class Baseline(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    device_id: str
    metric: str
    window: str
    average: float
    median: float
    p95: float
    stddev: float
    updated_at: datetime

# --- Backup Readiness Module ---
#
# These models are serialized camelCase (via Field aliases) to match the
# shared data contract agreed with the frontend, which is intentionally
# different from the snake_case legacy endpoints above (/api/devices,
# /api/history, etc.). Do not change field names/aliases here without
# flagging it — the frontend is built directly against this shape.

class BackupPortStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    port: int
    service: str
    open: bool

class BackupReadinessDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: int
    verdict: str  # ready | at-risk | not-ready
    sla_window_hours: float = Field(alias="slaWindowHours")
    estimated_transfer_hours: float = Field(alias="estimatedTransferHours")
    will_meet_sla: bool = Field(alias="willMeetSla")

class BackupTargetStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    is_backup_target: bool = Field(alias="isBackupTarget")
    reachability: str  # up | degraded | down
    dns_resolved: bool = Field(alias="dnsResolved")
    latency_ms: float = Field(alias="latencyMs")
    packet_loss_pct: float = Field(alias="packetLossPct")
    ports: List[BackupPortStatus]
    backup_readiness: BackupReadinessDetail = Field(alias="backupReadiness")

class BackupDiagnostic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    severity: str  # info | warning | critical
    category: str  # gateway | routing | dns | firewall | backup-protocol | throughput
    message: str
    recommendation: str
    affected_target_id: str = Field(alias="affectedTargetId")
    timestamp: str

class SimulatedScenario(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: str  # dns-flap | port-blocked | throughput-drop
    status: str  # pass | fail

class BackupReadinessReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    health_score: int = Field(alias="healthScore")
    backup_readiness_score: int = Field(alias="backupReadinessScore")
    targets: List[BackupTargetStatus]
    diagnostics: List[BackupDiagnostic]
    simulated_scenarios: List[SimulatedScenario] = Field(default_factory=list, alias="simulatedScenarios")
