-- Migration 007: per-account profile, holding the onboarding-tour flag.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- The app now requires login before any use, so the onboarding tour can no
-- longer be triggered by "first visit to the homepage" — there is no
-- pre-login homepage. It fires on first successful sign-in/sign-up instead,
-- and the "have they seen it" flag lives HERE rather than in localStorage so
-- it follows the account across devices and browsers: a user who took the
-- tour on their laptop is not shown it again on their phone.

CREATE TABLE IF NOT EXISTS public.user_profiles (
    user_id                 UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    onboarding_completed_at TIMESTAMPTZ,
    -- Bumped when the tour itself changes materially, so an existing account
    -- can be shown the new one without resetting every other profile field.
    onboarding_version      INTEGER NOT NULL DEFAULT 1,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at           TIMESTAMPTZ,
    login_count             INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Columns added defensively in case an earlier version of this table exists.
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS onboarding_version      INTEGER NOT NULL DEFAULT 1;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS last_login_at           TIMESTAMPTZ;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS login_count             INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.user_profiles ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

-- The backend reads and writes this table with the service-role key, which
-- bypasses RLS. These policies exist so that a leaked publishable key cannot
-- read or alter anyone else's profile.
DROP POLICY IF EXISTS user_profiles_owner_select ON public.user_profiles;
DROP POLICY IF EXISTS user_profiles_owner_upsert ON public.user_profiles;
DROP POLICY IF EXISTS user_profiles_owner_update ON public.user_profiles;

CREATE POLICY user_profiles_owner_select ON public.user_profiles
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY user_profiles_owner_upsert ON public.user_profiles
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY user_profiles_owner_update ON public.user_profiles
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
