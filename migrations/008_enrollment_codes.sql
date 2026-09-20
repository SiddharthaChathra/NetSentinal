-- Migration 008: short-lived enrolment codes for adding a device.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- The agent now ships as a standalone executable, so a user adding their
-- second or third machine has no repo and no .env to paste a token into.
-- Instead the site shows an 8-character code, the agent asks for it on first
-- run, and exchanges it for the account's real agent token.
--
-- Why a code and not the token itself:
--   * it is short enough to read off a screen and type on another machine
--   * it expires (15 minutes) and is single-use, so a code seen over someone's
--     shoulder or left in a screenshot is worthless within the hour
--   * the long-lived credential never has to be displayed, copied between
--     machines, or embedded in a downloadable binary

CREATE TABLE IF NOT EXISTS public.enrollment_codes (
    code        TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    -- Which machine claimed it, recorded after the fact so a user can see
    -- what a code was spent on.
    used_by_hostname TEXT
);

ALTER TABLE public.enrollment_codes ADD COLUMN IF NOT EXISTS used_at          TIMESTAMPTZ;
ALTER TABLE public.enrollment_codes ADD COLUMN IF NOT EXISTS used_by_hostname TEXT;

CREATE INDEX IF NOT EXISTS enrollment_codes_user_id_idx    ON public.enrollment_codes (user_id);
CREATE INDEX IF NOT EXISTS enrollment_codes_expires_at_idx ON public.enrollment_codes (expires_at);

ALTER TABLE public.enrollment_codes ENABLE ROW LEVEL SECURITY;

-- No policies at all: exactly like agent_tokens, this table is touched only
-- by the backend with the service-role key. A leaked publishable key must not
-- be able to read a pending code, because redeeming one yields an agent token.
