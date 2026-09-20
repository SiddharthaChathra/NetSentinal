"use client";

import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from "react";
import { User, Session } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";
import {
  checkSession, logoutBackend, completeOnboarding as completeOnboardingApi,
  subscribeUnauthorized, resetUnauthorizedLatch, SessionInfo,
} from "@/lib/api";

interface AuthContextType {
  user: User | null;
  session: Session | null;
  /** True until the LOCAL session has been read. It resolves in milliseconds
   *  (Supabase reads it from storage, no network), which is what lets the app
   *  decide between the dashboard and the sign-in page without either one
   *  flashing first. */
  loading: boolean;
  /** The backend's view of the session: org scope and onboarding state. Null
   *  until /api/auth/session answers, which on a cold start can take a while —
   *  the app must not block the dashboard on it. */
  backendSession: SessionInfo | null;
  /** True when the backend could not be reached. NOT the same as signed out:
   *  a cold start must never look like a sign-out. */
  backendUnreachable: boolean;
  shouldShowTour: boolean;
  markTourSeen: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  session: null,
  loading: true,
  backendSession: null,
  backendUnreachable: false,
  shouldShowTour: false,
  markTourSeen: async () => {},
  signOut: async () => {},
});

// Same-device cache for the per-account onboarding flag. The server is
// authoritative; this only stops the tour flashing while the backend wakes up.
const TOUR_SEEN_KEY = "netsentinel_onboarding_done";

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [backendSession, setBackendSession] = useState<SessionInfo | null>(null);
  const [backendUnreachable, setBackendUnreachable] = useState(false);
  const [shouldShowTour, setShouldShowTour] = useState(false);

  useEffect(() => {
    const initializeAuth = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        setSession(session);
        setUser(session?.user || null);
      } catch (error) {
        console.error("Error fetching session:", error);
      } finally {
        setLoading(false);
      }
    };

    initializeAuth();

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setSession(session);
        setUser(session?.user || null);
        setLoading(false);
      }
    );

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  // Confirm the session with the backend once there is a local one. This is
  // what turns "I have a token in localStorage" into "the server agrees, and
  // here is my org scope and onboarding state".
  useEffect(() => {
    let cancelled = false;
    if (!session?.access_token) {
      setBackendSession(null);
      setShouldShowTour(false);
      return;
    }
    (async () => {
      const info = await checkSession();
      if (cancelled) return;
      setBackendSession(info);
      setBackendUnreachable(!!info.unreachable);

      // The backend was reachable and says this session is not valid — it
      // expired, or it was revoked from another device. Drop the local
      // session so the gate sends them to sign in, rather than leaving them
      // on a dashboard where every request quietly 401s.
      //
      // `unreachable` is deliberately excluded: a cold start must never look
      // like a sign-out.
      if (!info.authenticated && !info.unreachable) {
        await supabase.auth.signOut();
        setUser(null);
        setSession(null);
        return;
      }
      resetUnauthorizedLatch();

      // The tour fires on the first successful sign-in for the ACCOUNT. The
      // server owns that flag so a second device does not replay it; the
      // local key is only consulted to avoid a flash before the answer lands.
      let seenLocally = false;
      try { seenLocally = localStorage.getItem(TOUR_SEEN_KEY) === "true"; } catch { /* private mode */ }
      const serverSaysShow = info.onboarding?.should_show_tour ?? false;
      if (serverSaysShow && !seenLocally) setShouldShowTour(true);
      if (!serverSaysShow) {
        setShouldShowTour(false);
        try { localStorage.setItem(TOUR_SEEN_KEY, "true"); } catch { /* ignore */ }
      }
    })();
    return () => { cancelled = true; };
  }, [session?.access_token]);

  // A 401 from any data request means the same thing: the session is over.
  useEffect(() => {
    return subscribeUnauthorized(() => {
      void (async () => {
        try { await supabase.auth.signOut(); } catch { /* already gone */ }
        setUser(null);
        setSession(null);
        setBackendSession(null);
      })();
    });
  }, []);

  const markTourSeen = useCallback(async () => {
    setShouldShowTour(false);
    try { localStorage.setItem(TOUR_SEEN_KEY, "true"); } catch { /* ignore */ }
    await completeOnboardingApi();
  }, []);

  const signOut = useCallback(async () => {
    // Order matters: revoke server-side FIRST, while the token is still valid
    // and can be sent. Doing it after supabase.auth.signOut() would leave the
    // refresh token live at Supabase with nothing able to revoke it.
    await logoutBackend();

    // Clear everything this browser cached for the account, so the next
    // person to sign in on a shared machine cannot see any of it.
    try {
      localStorage.removeItem("netsentinel_history");
      // Written by the old guest mode. Nothing writes them now, but browsers
      // that used the app before the login gate still hold them.
      localStorage.removeItem("netsentinel_guest_devices");
      localStorage.removeItem("netsentinel_guest_incidents");
      localStorage.removeItem(TOUR_SEEN_KEY);
    } catch (e) {
      console.error("Failed to clear localStorage on sign out:", e);
    }

    await supabase.auth.signOut();
    setUser(null);
    setSession(null);
    setBackendSession(null);
    setShouldShowTour(false);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, session, loading, backendSession, backendUnreachable, shouldShowTour, markTourSeen, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
