import { supabase } from "./supabase";

export const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "https://netsentinal.onrender.com";

// The backend runs on Render's free tier, which spins the container down
// after ~15 min idle and takes 30-60s to cold-start on the next request.
// Everything below is tuned so a normal cold start never surfaces as a
// user-facing "unreachable" error: the health probe retries with backoff
// for well over a minute before giving up, data requests get a real timeout
// (previously none — they could hang forever) plus a retry for idempotent
// GETs, and an early warming ping is fired from the layout.
// Health probe keeps retrying until this much wall-clock time has passed
// (a refused connection fails instantly, a cold Render proxy holds the
// socket open — the budget covers both), never fewer than MIN attempts.
const HEALTH_TOTAL_BUDGET_MS = 90_000;
const HEALTH_MIN_ATTEMPTS = 3;
const HEALTH_MAX_ATTEMPTS = 15;
const HEALTH_TIMEOUTS_MS = [8_000, 10_000, 15_000, 20_000, 25_000, 30_000];
const HEALTH_BACKOFF_MS = [1_000, 2_000, 4_000, 6_000, 8_000];
const FETCH_DEFAULT_TIMEOUT_MS = 45_000;
const GET_RETRY_ATTEMPTS = 3;
const GET_RETRY_BACKOFF_MS = [1_500, 4_000];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const isTransient = (e: unknown) =>
  e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError" || e.name === "TypeError");

export interface BackendHealth {
  ok: boolean;
  status?: string;
  database?: string;
  version?: string;
  error?: string;
  attempts: number;
}

/**
 * Probes /api/health with retry + exponential backoff so a cold-starting
 * backend is waited for, not reported as down. Resolves ok:true on the
 * first success; only resolves ok:false once the time budget is spent.
 * `onAttempt` fires before each try so a caller can show progress.
 */
export const checkBackendHealth = async (opts: {
  onAttempt?: (attempt: number, maxAttempts: number) => void;
  budgetMs?: number;
} = {}): Promise<BackendHealth> => {
  const budgetMs = opts.budgetMs ?? HEALTH_TOTAL_BUDGET_MS;
  const started = Date.now();
  let lastError = "Backend is unreachable";
  let attempt = 0;

  while (true) {
    attempt++;
    opts.onAttempt?.(attempt, HEALTH_MAX_ATTEMPTS);
    const timeout = HEALTH_TIMEOUTS_MS[Math.min(attempt - 1, HEALTH_TIMEOUTS_MS.length - 1)];
    try {
      const res = await fetch(`${BACKEND_URL}/api/health`, {
        signal: AbortSignal.timeout(timeout),
        cache: "no-store",
      });
      if (res.ok) {
        const data = await res.json();
        return { ok: true, status: data.status, database: data.database, version: data.version, attempts: attempt };
      }
      lastError = `Backend returned HTTP ${res.status}`;
    } catch (e: unknown) {
      lastError =
        e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError")
          ? "Backend did not respond in time"
          : "Backend server is not reachable";
    }

    const elapsed = Date.now() - started;
    const exhausted = attempt >= HEALTH_MAX_ATTEMPTS || (attempt >= HEALTH_MIN_ATTEMPTS && elapsed >= budgetMs);
    if (exhausted) {
      return {
        ok: false,
        error: `${lastError} after ${attempt} attempts over ${Math.round(elapsed / 1000)}s (${BACKEND_URL})`,
        attempts: attempt,
      };
    }
    await sleep(HEALTH_BACKOFF_MS[Math.min(attempt - 1, HEALTH_BACKOFF_MS.length - 1)]);
  }
};

// --- Shared readiness gate ---------------------------------------------------
//
// Exactly one health-probe loop runs per page load. Every data request
// awaits it before firing, so pages no longer race the cold-starting
// backend, burn their retries, and paint fallback values that never
// refresh. If the probe ultimately fails, requests proceed anyway so
// pages can degrade gracefully instead of hanging.

type BackendStatus = "connecting" | "connected" | "disconnected";
type StatusListener = (status: BackendStatus, info: { attempt?: number; maxAttempts?: number; health?: BackendHealth }) => void;

let readyPromise: Promise<BackendHealth> | null = null;
let currentStatus: BackendStatus = "connecting";
const listeners = new Set<StatusListener>();

const emit = (status: BackendStatus, info: Parameters<StatusListener>[1] = {}) => {
  currentStatus = status;
  listeners.forEach((l) => {
    try { l(status, info); } catch { /* ignore listener errors */ }
  });
};

export const getBackendStatus = () => currentStatus;

/** Subscribe to connecting/connected/disconnected transitions. Returns an unsubscribe fn. */
export const subscribeBackendStatus = (listener: StatusListener) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

/**
 * Returns the shared readiness probe, starting it on first call. Pass
 * `force` to re-run after a failure (e.g. a user-initiated retry).
 */
export const ensureBackendReady = (force = false): Promise<BackendHealth> => {
  if (readyPromise && !force) return readyPromise;
  emit("connecting", { attempt: 1 });
  readyPromise = checkBackendHealth({
    onAttempt: (attempt, maxAttempts) => emit("connecting", { attempt, maxAttempts }),
  }).then((health) => {
    emit(health.ok ? "connected" : "disconnected", { health });
    return health;
  });
  return readyPromise;
};

/** Fire-and-forget ping to start a cold backend booting as early as possible. */
export const warmBackend = () => {
  try {
    fetch(`${BACKEND_URL}/api/health`, { signal: AbortSignal.timeout(60_000), cache: "no-store", keepalive: true }).catch(() => {});
  } catch {
    /* ignore */
  }
};

// --- Session expiry while the app is open -----------------------------------
//
// A session can end without the user doing anything here: the token expires,
// or it is revoked from another device. The backend then answers 401 to every
// request, and without this the user would sit on a dashboard quietly failing
// to load anything. Instead the first such 401 notifies the app, which signs
// out locally and sends them to the login page with their current location
// remembered.

type UnauthorizedListener = (reason: string) => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();
let unauthorizedNotified = false;

export const subscribeUnauthorized = (listener: UnauthorizedListener) => {
  unauthorizedListeners.add(listener);
  return () => { unauthorizedListeners.delete(listener); };
};

/** Called after a successful sign-in so a later expiry can fire again. */
export const resetUnauthorizedLatch = () => { unauthorizedNotified = false; };

const notifyUnauthorized = (reason: string) => {
  // Latched: a page firing six parallel requests must produce one redirect,
  // not six.
  if (unauthorizedNotified) return;
  unauthorizedNotified = true;
  unauthorizedListeners.forEach((l) => { try { l(reason); } catch { /* ignore */ } });
};

// --- Session ----------------------------------------------------------------
//
// The app requires a login before any use, so there is no guest identity any
// more: the per-browser X-Guest-Session id that used to key an anonymous
// visitor's diagnostic run is gone, and the backend keys runs by account.

export interface SessionInfo {
  authenticated: boolean;
  user: { id: string; email: string | null } | null;
  org: { id: string; scope: string } | null;
  onboarding: { completed: boolean; should_show_tour: boolean; version: number } | null;
  /** True when the backend could not be reached at all — distinct from
   *  "reached it, and you are signed out". The UI must not treat the two the
   *  same: one is a cold start, the other is a real redirect to sign-in. */
  unreachable?: boolean;
}

const SIGNED_OUT: SessionInfo = { authenticated: false, user: null, org: null, onboarding: null };

/**
 * The "am I logged in" check, called on app load before anything is painted.
 *
 * It goes through the same readiness gate as every other request, so on a
 * cold start it waits for the backend to wake instead of reporting a failure.
 * A backend that never answers resolves to `unreachable: true` rather than
 * throwing — the caller decides what to show, and "signed out" is never
 * inferred from a network failure (that would bounce a signed-in user to the
 * login page every time Render is slow to wake).
 */
export const checkSession = async (): Promise<SessionInfo> => {
  try {
    const res = await fetchWithAuth("/api/auth/session", { timeoutMs: 20_000 });
    if (!res.ok) return { ...SIGNED_OUT, unreachable: res.status >= 500 };
    return (await res.json()) as SessionInfo;
  } catch {
    return { ...SIGNED_OUT, unreachable: true };
  }
};

/** Ends the session server-side (revokes the refresh token, clears the
 *  backend's validation cache). Never throws: a failure here must not trap
 *  the user in a half-signed-out state — the local session is cleared either
 *  way and they land on the sign-in page. */
export const logoutBackend = async (): Promise<void> => {
  try {
    await fetchWithAuth("/api/auth/logout", { method: "POST", timeoutMs: 10_000, retry: false });
  } catch {
    /* best effort — the local sign-out below is what the user sees */
  }
};

/** Marks the onboarding tour as seen for the ACCOUNT, not just this browser. */
export const completeOnboarding = async (): Promise<void> => {
  try {
    await fetchWithAuth("/api/auth/onboarding/complete", { method: "POST", timeoutMs: 10_000, retry: false });
  } catch {
    /* the local flag still suppresses it on this device */
  }
};

export interface FetchOptions extends RequestInit {
  /** Per-request timeout. Defaults to FETCH_DEFAULT_TIMEOUT_MS; pass 0 to disable. */
  timeoutMs?: number;
  /** Retry transient failures (network error / timeout / 502-504). Defaults to true for GET, false otherwise. */
  retry?: boolean;
  /** Skip waiting for the shared readiness probe. Defaults to false. */
  skipReadyGate?: boolean;
}

export const fetchWithAuth = async (url: string, options: FetchOptions = {}) => {
  const { timeoutMs = FETCH_DEFAULT_TIMEOUT_MS, retry, skipReadyGate = false, ...init } = options;
  const method = (init.method || "GET").toUpperCase();
  let shouldRetry = retry ?? method === "GET";

  if (!skipReadyGate) {
    const health = await ensureBackendReady();
    // The probe already spent its budget confirming the backend is down —
    // don't stack more retries on top; fail fast so pages can degrade.
    if (!health.ok) shouldRetry = false;
  }
  const attempts = shouldRetry ? GET_RETRY_ATTEMPTS : 1;

  const headers = new Headers(init.headers);
  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      headers.set("Authorization", `Bearer ${session.access_token}`);
    }
  } catch (e) {
    // No token means the backend will answer 401 and the gate will send the
    // user to sign in — which is the correct outcome, so don't swallow it.
    console.warn("fetchWithAuth: could not read the session:", e);
  }

  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const signal =
        init.signal ?? (timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined);
      const res = await fetch(`${BACKEND_URL}${url}`, { ...init, headers, signal });
      if (shouldRetry && attempt < attempts && res.status >= 502 && res.status <= 504) {
        await sleep(GET_RETRY_BACKOFF_MS[Math.min(attempt - 1, GET_RETRY_BACKOFF_MS.length - 1)]);
        continue;
      }
      // The gate refused us. /api/auth/session is exempt because a "no" there
      // is the answer to a question, not a session that just died.
      if (res.status === 401 && url !== "/api/auth/session") {
        notifyUnauthorized("Your session has ended. Please sign in again.");
      }
      return res;
    } catch (e) {
      lastError = e;
      if (!shouldRetry || attempt >= attempts || !isTransient(e)) throw e;
      await sleep(GET_RETRY_BACKOFF_MS[Math.min(attempt - 1, GET_RETRY_BACKOFF_MS.length - 1)]);
    }
  }
  throw lastError;
};
