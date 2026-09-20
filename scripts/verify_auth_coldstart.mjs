/**
 * Verifies the two things the login gate can break that unit tests cannot see:
 *
 *   1. COLD START ON THE NEW FIRST-LOAD PATH. The sign-in page is now the
 *      first thing every visitor loads, which makes it the most-hit
 *      cold-start path in the app. The retry/warm-up logic in src/lib/api.ts
 *      must keep a booting Render container from surfacing as a scary
 *      "Service Unreachable" banner — and a slow backend must never be
 *      mistaken for "signed out", which would log people out of a working
 *      session.
 *
 *   2. REDIRECT-AFTER-LOGIN SAFETY. `?redirect=` is attacker-controllable, so
 *      it must only ever send users to a same-origin path.
 *
 * Runs the REAL modules (node strips the TypeScript); the supabase client is
 * stubbed so nothing touches the network or needs credentials.
 *
 * Usage, from the repo root:
 *   node --experimental-strip-types --no-warnings scripts/verify_auth_coldstart.mjs
 *
 * Takes ~4 minutes: it waits out real cold-start timings rather than faking
 * the clock, because the thing being checked IS the timing.
 */
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

const FRONTEND = path.resolve(import.meta.dirname, "..", "frontend", "src", "lib");
const apiUrl = pathToFileURL(path.join(FRONTEND, "api.ts")).href;
const redirectUrl = pathToFileURL(path.join(FRONTEND, "redirect.ts")).href;

// --- stub ./supabase so api.ts can be imported in isolation -----------------
const STUB =
  "export const supabase = { auth: { getSession: async () => ({ data: { session: globalThis.__SESSION__ || null } }) } };";
register(
  "data:text/javascript," +
    encodeURIComponent(`
    export async function resolve(s, c, n) {
      return s === "./supabase" ? { url: "stub:supabase", shortCircuit: true } : n(s, c);
    }
    export async function load(u, c, n) {
      return u === "stub:supabase"
        ? { format: "module", shortCircuit: true, source: ${JSON.stringify(STUB)} }
        : n(u, c);
    }
  `),
  import.meta.url,
);

// AbortSignal.timeout() timers are unref'd in Node, so a promise waiting only
// on one does not keep the process alive. This heartbeat does.
const keepAlive = setInterval(() => {}, 1000);

const results = [];
const check = (name, passed, detail) => {
  results.push({ name, passed });
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};

/**
 * Models Render free-tier behaviour: while the container boots, the proxy
 * HOLDS the socket open rather than refusing, so the client sees a timeout.
 * After `bootMs` the service answers 200.
 */
function coldBackend({ bootMs, holdSocket = true }) {
  const startedAt = Date.now();
  let requests = 0;
  return {
    get requests() { return requests; },
    fetch(_url, opts = {}) {
      requests++;
      if (Date.now() - startedAt >= bootMs) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ status: "healthy", database: "connected", version: "2.0.0" }),
        });
      }
      if (!holdSocket) {
        const e = new Error("fetch failed");
        e.name = "TypeError";
        return Promise.reject(e);
      }
      return new Promise((_resolve, reject) => {
        const signal = opts.signal;
        if (!signal) return;
        const fail = () => {
          const e = new Error("aborted due to timeout");
          e.name = "TimeoutError";
          reject(e);
        };
        if (signal.aborted) fail();
        else signal.addEventListener("abort", fail, { once: true });
      });
    },
  };
}

async function coldStartScenario(label, { bootMs, holdSocket }) {
  // A fresh module instance per scenario: the readiness gate is a
  // module-level singleton, which is part of what is being tested.
  const api = await import(`${apiUrl}?v=${label}`);
  globalThis.__SESSION__ = null;
  const backend = coldBackend({ bootMs, holdSocket });
  globalThis.fetch = (u, o) => backend.fetch(u, o);

  const states = [];
  api.subscribeBackendStatus((s) => { if (states[states.length - 1] !== s) states.push(s); });

  const t0 = Date.now();
  const health = await api.ensureBackendReady();
  return { health, elapsed: Date.now() - t0, states, requests: backend.requests };
}

console.log("NetSentinel — login gate: cold start + redirect safety\n");
console.log("1. Cold start on the sign-in page (the new first load)\n");

for (const [label, bootMs] of [["cold-45s", 45_000], ["cold-60s", 60_000]]) {
  const { health, elapsed, states, requests } = await coldStartScenario(label, { bootMs });
  check(
    `${bootMs / 1000}s cold start resolves as connected, not unreachable`,
    health.ok === true,
    `ok=${health.ok} after ${(elapsed / 1000).toFixed(1)}s, ${requests} attempts`,
  );
  check(
    `${bootMs / 1000}s cold start never shows the "unreachable" banner`,
    !states.includes("disconnected"),
    `states: ${states.join(" -> ")}`,
  );
}

{
  // Connection refused (container down rather than booting) fails instantly,
  // so the retry budget has to be wall-clock based, not attempt-count based.
  const { health, states, requests } = await coldStartScenario("refused", { bootMs: 30_000, holdSocket: false });
  check("instantly-refused connections are retried, not reported as down",
    health.ok === true, `ok=${health.ok} after ${requests} attempts`);
  check("no scary banner on the refused-then-recovered path",
    !states.includes("disconnected"), `states: ${states.join(" -> ")}`);
}

console.log("\n2. The session check itself\n");

{
  // The critical distinction: a backend that never answers must read as
  // "unreachable", NOT as "signed out". Getting this wrong signs people out
  // every time Render is slow to wake.
  const api = await import(`${apiUrl}?v=never`);
  globalThis.__SESSION__ = { access_token: "a-valid-token" };
  globalThis.fetch = (_u, opts = {}) =>
    new Promise((_r, reject) => {
      opts.signal?.addEventListener("abort", () => {
        const e = new Error("timeout");
        e.name = "TimeoutError";
        reject(e);
      }, { once: true });
    });

  const info = await api.checkSession();
  check("a backend that never answers reads as unreachable, not signed out",
    info.unreachable === true && info.authenticated === false,
    `unreachable=${info.unreachable} authenticated=${info.authenticated}`);
  check("checkSession resolves rather than throwing at the sign-in page",
    typeof info === "object" && info !== null);
}

{
  const api = await import(`${apiUrl}?v=warm`);
  globalThis.__SESSION__ = { access_token: "a-valid-token" };
  globalThis.fetch = async (url) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes("/api/auth/session")
        ? {
            authenticated: true,
            user: { id: "u1", email: "a@b.c" },
            org: { id: "u1", scope: "account" },
            onboarding: { completed: false, should_show_tour: true, version: 1 },
          }
        : { status: "healthy" },
  });
  const t0 = Date.now();
  const info = await api.checkSession();
  const elapsed = Date.now() - t0;
  check("warm session check is fast (it is on every visit's critical path)",
    info.authenticated === true && elapsed < 500, `${elapsed}ms`);
  check("the session check carries org scope and the onboarding flag",
    info.org?.id === "u1" && info.onboarding?.should_show_tour === true);
}

console.log("\n3. Redirect-after-login safety\n");

{
  const { safeRedirect, DEFAULT_LANDING } = await import(redirectUrl);

  const allowed = [
    ["/devices", "/devices"],
    ["/backup?target=dev-1", "/backup?target=dev-1"],
    ["/topology", "/topology"],
  ];
  for (const [input, expected] of allowed) {
    check(`deep link "${input}" is preserved`, safeRedirect(input) === expected, `-> ${safeRedirect(input)}`);
  }

  const rejected = [
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "http://evil.example/path",
    "javascript:alert(1)",
    "/auth",
    "/auth?redirect=/devices",
    "",
    null,
    undefined,
  ];
  for (const input of rejected) {
    check(`open-redirect attempt ${JSON.stringify(input)} falls back to ${DEFAULT_LANDING}`,
      safeRedirect(input) === DEFAULT_LANDING, `-> ${safeRedirect(input)}`);
  }
}

clearInterval(keepAlive);
const failed = results.filter((r) => !r.passed);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
