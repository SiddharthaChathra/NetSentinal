-- Migration 003: add the per-user ownership column the backend expects.
-- Safe to run more than once. Apply in Supabase -> SQL Editor -> Run.
--
-- The live project was created from an older schema: devices, incidents,
-- alerts, metric_baselines and diagnostic_runs have NO user_id column, so
-- every user-scoped query the backend makes ("column user_id does not
-- exist") has been failing since day one. Agent registration returned 503
-- for the same reason.
--
-- Also (re)asserts Row-Level Security with owner-only policies on every
-- user table. The backend uses the service-role key (bypasses RLS), so
-- these policies only lock down direct access with the publishable key.

-- 1. Ownership columns ------------------------------------------------------
ALTER TABLE public.devices          ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.incidents        ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.alerts           ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.metric_baselines ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.diagnostic_runs  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS devices_user_id_idx          ON public.devices (user_id);
CREATE INDEX IF NOT EXISTS incidents_user_id_idx        ON public.incidents (user_id);
CREATE INDEX IF NOT EXISTS alerts_user_id_idx           ON public.alerts (user_id);
CREATE INDEX IF NOT EXISTS metric_baselines_user_id_idx ON public.metric_baselines (user_id);
CREATE INDEX IF NOT EXISTS diagnostic_runs_user_id_idx  ON public.diagnostic_runs (user_id);

-- 2. Row-Level Security ------------------------------------------------------
ALTER TABLE public.devices          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.incidents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alerts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.metric_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.diagnostic_runs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.telemetry        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.history          ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
    pname TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['devices', 'incidents', 'alerts', 'metric_baselines', 'diagnostic_runs', 'history']
    LOOP
        pname := 'Users can access their own ' || t;
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public' AND tablename = t AND policyname = pname
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL USING (auth.uid() = user_id)',
                pname, t
            );
        END IF;
    END LOOP;

    -- Telemetry has no user_id; readable by the owner of its device.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'telemetry'
          AND policyname = 'Users can read telemetry of their own devices'
    ) THEN
        CREATE POLICY "Users can read telemetry of their own devices"
            ON public.telemetry FOR SELECT
            USING (EXISTS (
                SELECT 1 FROM public.devices
                WHERE public.devices.id = public.telemetry.device_id
                  AND public.devices.user_id = auth.uid()
            ));
    END IF;
END $$;
