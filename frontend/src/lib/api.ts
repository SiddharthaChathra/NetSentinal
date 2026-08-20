import { supabase } from "./supabase";

export const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "https://netsentinal.onrender.com";

/**
 * Checks if the backend API is reachable by hitting the /api/health endpoint.
 * Returns { ok, status, data } or { ok: false, error } if unreachable.
 */
export const checkBackendHealth = async (): Promise<{
  ok: boolean;
  status?: string;
  database?: string;
  version?: string;
  error?: string;
}> => {
  try {
    const res = await fetch(`${BACKEND_URL}/api/health`, {
      signal: AbortSignal.timeout(5000),
    });
    if (res.ok) {
      const data = await res.json();
      return { ok: true, status: data.status, database: data.database, version: data.version };
    }
    return { ok: false, error: `Backend returned HTTP ${res.status}` };
  } catch (e: any) {
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return { ok: false, error: "Backend request timed out" };
    }
    return { ok: false, error: "Backend server is not reachable. Make sure the backend is running on " + BACKEND_URL };
  }
};

export const fetchWithAuth = async (url: string, options: RequestInit = {}) => {
  const headers = new Headers(options.headers);

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      headers.set("Authorization", `Bearer ${session.access_token}`);
    }
  } catch (e) {
    console.warn("fetchWithAuth: Could not get session, proceeding without auth:", e);
  }

  return fetch(`${BACKEND_URL}${url}`, {
    ...options,
    headers,
  });
};
