-- Migration 005: agents report the default gateway they detected.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- The dashboard's Topology page previously showed "Unknown" for the gateway
-- unless the visitor had run an on-demand diagnostic in the same browser
-- session. The agent already detects its host's default gateway (and picks
-- the lowest-metric route when there are several adapters); this column
-- stores it so the dashboard can read it back.

ALTER TABLE public.telemetry ADD COLUMN IF NOT EXISTS gateway_ip TEXT;
