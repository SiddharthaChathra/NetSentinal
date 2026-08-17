import { supabase } from "./supabase";

export const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

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
