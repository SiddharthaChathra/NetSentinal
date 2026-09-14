-- Migration 002: per-user agent tokens.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- Each account gets its own agent token, generated on the website's Getting
-- Started page. The agent authenticates with it, and the backend attaches
-- the registered device to the token's owner — so users never need a shared
-- secret from an administrator.
--
-- RLS is enabled with NO policies: browsers (publishable key) can never read
-- this table. Only the backend, using the service-role key, can.

CREATE TABLE IF NOT EXISTS public.agent_tokens (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    rotated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE
);

ALTER TABLE public.agent_tokens ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS agent_tokens_token_idx ON public.agent_tokens (token);
