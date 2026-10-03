import { fetchWithAuth } from "./api";

// Every /api/ai/* route answers in this shape. `text` is always displayable:
// when the model was not used (down, rate-limited, disabled, or its answer
// failed the grounding check) it is the rule engine's own output, and
// `ai.note` says why in one sentence.
export interface AiResult {
  feature: string;
  text: string;
  ai: {
    used: boolean;
    status: "ok" | "unavailable" | "rejected" | "rate_limited" | "skipped";
    provider: string | null;
    model: string | null;
    note: string | null;
  };
  facts: Record<string, unknown>;
}

// A local 8B model can take most of a minute; Groq answers in a second or two.
const AI_TIMEOUT_MS = 150_000;

export class AiRequestError extends Error {}

async function call(url: string, init: RequestInit = {}): Promise<AiResult> {
  let res: Response;
  try {
    res = await fetchWithAuth(url, {
      ...init,
      timeoutMs: AI_TIMEOUT_MS,
      retry: false,
      headers: init.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new AiRequestError("The assistant could not be reached. Nothing else on this page is affected.");
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {}
    throw new AiRequestError(
      res.status === 404 ? "That record was not found on your account." : detail || `The assistant answered HTTP ${res.status}.`
    );
  }
  return res.json();
}

export const summarizeIncidents = (incidentIds: string[]) =>
  call("/api/ai/incident-summary", { method: "POST", body: JSON.stringify({ incident_ids: incidentIds }) });

export const kbArticle = (incidentId: string) =>
  call("/api/ai/kb-article", { method: "POST", body: JSON.stringify({ incident_id: incidentId }) });

export const digest = (period: "daily" | "weekly") => call(`/api/ai/digest?period=${period}`);

export const ask = (question: string) =>
  call("/api/ai/ask", { method: "POST", body: JSON.stringify({ question }) });

export const trends = (deviceId: string, days = 30) =>
  call(`/api/ai/trends/${encodeURIComponent(deviceId)}?days=${days}`);
