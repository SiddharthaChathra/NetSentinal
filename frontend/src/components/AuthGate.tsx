"use client";

import { useEffect, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { Shield } from "lucide-react";
import {
  isPublicRoute, safeRedirect, rememberDestination, takeRememberedDestination,
  DEFAULT_LANDING, SETUP_ROUTE,
} from "@/lib/redirect";

// Re-exported so existing imports from this component keep working.
export { safeRedirect, rememberDestination, takeRememberedDestination, DEFAULT_LANDING, SETUP_ROUTE };

/**
 * Route protection for the whole app.
 *
 * WHY THIS IS CLIENT-SIDE
 *   The Supabase session lives in localStorage (the default for
 *   @supabase/supabase-js), which Next.js middleware running at the edge
 *   cannot read — it only sees cookies. So a server-side redirect is not
 *   available without moving the whole app to cookie-based sessions.
 *
 *   That is fine, because the gate here is a *routing* convenience, not the
 *   security boundary: the security boundary is AuthGateMiddleware on the
 *   backend, which 401s every request without a valid token. Someone who
 *   defeats this component sees an empty shell and no data.
 *
 * WHY THERE IS NO FLASH
 *   `loading` is resolved from storage without a network round-trip, so it
 *   settles in milliseconds. Until it does, neither the login form nor the
 *   dashboard renders — a splash does. A returning user therefore goes
 *   straight to the dashboard, and a signed-out one straight to sign-in,
 *   without either seeing the other first.
 */

function Splash({ label }: { label: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#060a13]" role="status" aria-live="polite">
      <div className="flex flex-col items-center gap-4">
        <div className="relative">
          <Shield className="w-10 h-10 text-cyan-400" />
          <span className="absolute inset-0 rounded-full border-2 border-cyan-400/30 border-t-cyan-400 animate-spin" />
        </div>
        <p className="text-sm text-slate-400">{label}</p>
      </div>
    </div>
  );
}

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, needsSetup } = useAuth();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();

  const onPublicRoute = isPublicRoute(pathname);

  // Arriving from a password-recovery email, Supabase creates a temporary
  // session so a new password can be set. That user IS signed in but must
  // stay on /auth, so the "already signed in, go to the dashboard" rule is
  // suspended for exactly that case.
  const isPasswordReset = searchParams.get("reset") === "1";

  const currentDestination = useMemo(() => {
    const query = searchParams.toString();
    return query ? `${pathname}?${query}` : pathname;
  }, [pathname, searchParams]);

  useEffect(() => {
    if (loading) return;

    if (!user && !onPublicRoute) {
      // Blocked from a deep link: remember exactly where they were going,
      // including its query string, and hand it to the sign-in page.
      rememberDestination(currentDestination);
      router.replace(`/auth?redirect=${encodeURIComponent(currentDestination)}`);
      return;
    }

    if (user && onPublicRoute && !isPasswordReset) {
      // Already signed in and looking at the login form (back button, or the
      // root URL with a live session): send them on, to their original
      // destination if they had one.
      const requested = searchParams.get("redirect") || takeRememberedDestination();
      const target = requested ? safeRedirect(requested) : (needsSetup ? SETUP_ROUTE : DEFAULT_LANDING);
      router.replace(target);
      return;
    }

    // An account with no agent registered yet has nothing on the dashboard.
    // Send it to the setup guide instead of an empty Overview — but only from
    // the dashboard itself: every other page stays reachable, so someone who
    // wants to look around first is never trapped.
    //
    // `needsSetup` is undefined until we know, and undefined must not trigger
    // this: a user who already runs an agent should never be bounced to the
    // install instructions.
    if (user && needsSetup === true && pathname === DEFAULT_LANDING) {
      router.replace(SETUP_ROUTE);
    }
  }, [loading, user, needsSetup, onPublicRoute, isPasswordReset, pathname, currentDestination, router, searchParams]);

  if (loading) return <Splash label="Checking your session…" />;

  // Render nothing but the splash while the redirect above is in flight, so
  // the login form never flashes for a signed-in user and no protected page
  // ever paints for a signed-out one.
  if (!user && !onPublicRoute) return <Splash label="Redirecting to sign in…" />;
  if (user && onPublicRoute && !isPasswordReset) return <Splash label="Signing you in…" />;
  // Covers the redirect above so the empty dashboard does not flash first.
  if (user && needsSetup === true && pathname === DEFAULT_LANDING) {
    return <Splash label="Opening your setup guide…" />;
  }

  return <>{children}</>;
}
