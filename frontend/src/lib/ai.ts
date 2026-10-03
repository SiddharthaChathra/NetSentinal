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

const UNAVAILABLE = "The assistant is temporarily unavailable. Your diagnostic data is unaffected.";

// The AI routes answer 200 whenever the assistant itself is the problem, so
// a failure here is either the caller's (a record not on this account, a bad
// request) or the server's. FastAPI answers an unknown route with the bare
// detail "Not Found" - that is a backend without the AI routes deployed, not
// a missing record, and must not read as "not found on your account".
function describeFailure(status: number, detail: string): string {
  if (status === 404 && detail && detail !== "Not Found") return "That record was not found on your account.";
  if (status === 400 || status === 422) return detail || "That request could not be processed.";
  return UNAVAILABLE;
}

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
    throw new AiRequestError(UNAVAILABLE);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {}
    throw new AiRequestError(describeFailure(res.status, detail));
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

// A "looks like you're stuck" suggestion, detected from the account's own
// data and worded from the help content - never written by a model.
export interface Nudge {
  kind: string;
  message: string;
  help_id: string;
  help_title: string;
  where: string;
  tips: string[];
}

// Optional by nature: any failure means "no suggestion", never an error.
export async function fetchNudges(): Promise<Nudge[]> {
  try {
    const res = await fetchWithAuth("/api/ai/nudges", { timeoutMs: 20_000, retry: false });
    if (!res.ok) return [];
    const body = await res.json();
    return Array.isArray(body.nudges) ? body.nudges : [];
  } catch {
    return [];
  }
}
