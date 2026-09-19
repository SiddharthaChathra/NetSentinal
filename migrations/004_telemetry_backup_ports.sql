-- Migration 004: agents report their own backup-protocol port state.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- The server cannot reach an agent-managed host (private LAN, no public
-- DNS), so Backup Readiness for such devices is derived from what the agent
-- reports about itself. This column carries the agent's NFS/SMB/iSCSI/
-- replication port check: [{"port": 2049, "service": "NFS", "open": true}, ...]

ALTER TABLE public.telemetry ADD COLUMN IF NOT EXISTS backup_ports JSONB;

-- Latest-telemetry-per-device lookups.
CREATE INDEX IF NOT EXISTS telemetry_device_id_timestamp_idx
    ON public.telemetry (device_id, timestamp DESC);
