-- Migration 006: per-target backup protocols.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- Backup Readiness checked every target against all four protocols
-- (NFS, SMB, iSCSI, Replication) and penalised each closed port. A Windows
-- laptop serving SMB shares was therefore marked "at-risk" for not running
-- NFS/iSCSI. This column records which protocols a target is meant to
-- serve; only those are checked and scored. NULL = all four (unchanged
-- behaviour for existing targets).

ALTER TABLE public.devices ADD COLUMN IF NOT EXISTS backup_protocols TEXT[];
