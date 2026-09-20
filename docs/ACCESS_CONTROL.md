# Access control

NetSentinel requires a login before any use. This document records how that is
enforced, the decisions that had more than one defensible answer, and what has
to be deployed for it to work.

## The boundary

The security boundary is **`AuthGateMiddleware` in `src/auth_middleware.py`**,
which runs in front of every request. A path is reachable without a session
only if it is on the public list; everything else answers `401` with a
machine-readable body before the handler runs:

```json
{ "detail": "Sign in to access NetSentinel.", "code": "not_authenticated",
  "authenticated": false, "login_url": "/auth" }
```

`code` is `not_authenticated` when no token was sent and `session_expired`
when one was sent and rejected, so the frontend can tell "please sign in" from
"your session ended" without matching prose.

This is middleware rather than a per-endpoint dependency on purpose. A
dependency protects the endpoints someone remembered to decorate, and the
failure mode of forgetting one is a silently public endpoint. Here the failure
mode of forgetting is a locked endpoint — loud, and safe.

### What is public, and why

| Path | Why |
|---|---|
| `/api/health` | The cold-start probe. Gating it would make a booting backend indistinguishable from a rejected one. Reports no account data. |
| `/api/auth/session` | The "am I logged in" check. Must answer `200` with `authenticated: false`, or the sign-in page cannot ask the question. |
| `/api/auth/logout` | Accepts an already-expired token and still succeeds, so a stale session never strands a user on an error. |
| `/api/agent/*` | Agents authenticate with their own per-user `nsa_` token via `verify_agent_token`. The gate steps aside rather than demanding a second, different credential. |

`/docs`, `/redoc` and `/openapi.json` are **gated** while the login gate is
on — the schema is a route like any other, and Swagger UI cannot send a bearer
token from the browser anyway. Set `EXPOSE_API_DOCS=1` to put them back.

### When the gate is on

`auth_required()` in `src/session.py`:

- `AUTH_REQUIRED=1` forces it on, `AUTH_REQUIRED=0` forces it off;
- otherwise it is on whenever a database is configured — i.e. always in
  production.

Off only for a local checkout with no Supabase credentials, where there is no
user directory to authenticate against. Startup logs which mode is active.

### Failing closed

If Supabase cannot be reached, token validation fails and the request is
refused. Failing *open* during an auth outage would unlock the whole app.

## Session validation cost

Nothing is reachable without a token, so every request would otherwise pay a
round-trip to Supabase's auth server. Validated tokens are cached for 30 s
(failures for 15 s, which also stops a client looping on a dead token from
stampeding the auth server). Logout evicts the entry immediately, so the
window only applies to sessions revoked elsewhere.

## Logout

Supabase access tokens are stateless JWTs and cannot be un-signed before they
expire. `POST /api/auth/logout` therefore does three things:

1. revokes the refresh token at Supabase (`auth.admin.sign_out`), so the
   session cannot be renewed and dies at the access token's expiry;
2. evicts this backend's validation cache, so the token stops being accepted
   here immediately;
3. drops the account's cached diagnostic run, so the next person to sign in on
   a shared browser cannot see the previous user's scan.

The response reports whether the Supabase revocation actually succeeded rather
than claiming success unconditionally. The frontend calls this **before**
`supabase.auth.signOut()` — afterwards there would be no valid token left to
send.

## Frontend routing

`frontend/src/components/AuthGate.tsx` wraps the whole app.

It is client-side because the Supabase session lives in `localStorage`, which
Next.js edge middleware cannot read — it only sees cookies. That is acceptable
because this component is a *routing* convenience, not the security boundary:
someone who defeats it sees an empty shell and no data, because every request
it would have made is refused by the backend.

**No flash in either direction.** `loading` resolves from storage with no
network round-trip, so it settles in milliseconds, and until it does neither
the login form nor the dashboard renders — a splash does.

**Deep links** are carried in `?redirect=`, mirrored into `sessionStorage` so
they also survive the email-confirmation round trip (which returns on a URL we
do not control). `safeRedirect()` in `frontend/src/lib/redirect.ts` accepts
only same-origin paths, so `?redirect=https://evil.example` cannot turn
sign-in into an open redirect.

**Sessions that end mid-use** (expiry, or revoked from another device) surface
as a `401` from any data request. `fetchWithAuth` notifies the app once —
latched, so six parallel requests cause one redirect, not six — and the user is
signed out locally and sent to the login page.

## Onboarding trigger

The tour used to fire on the first visit to the pre-login homepage. There is no
pre-login homepage any more, so it fires on **first successful sign-in or
sign-up**.

**The flag is per account, server-side** — `user_profiles.onboarding_completed_at`
— not per browser. A user who takes the tour on their laptop is not shown it
again when they sign in on their phone. `localStorage` is kept only as a
same-device cache so the tour does not flash while the backend answers; the
server is authoritative, and clearing browser storage does not bring it back.

The trade-off, accepted deliberately: the tour is genuinely once per account,
so a colleague sharing an account never sees it. `ONBOARDING_VERSION` in
`src/user_profile.py` is the escape hatch — bumping it re-runs the tour for
every account whose stored version is older.

The profile row is created by `/api/auth/session` on the first authenticated
request an account ever makes, which is what makes "first login" a server-side
fact rather than a client-side guess.

Everything in `src/user_profile.py` fails soft: if migration 007 has not been
applied, users still sign in normally and simply get the tour treated as unseen.

## Where a user lands

`/api/auth/session` also answers "is this account set up yet", from the
cheapest question that settles it: does the account own at least one
registered device (`select id … limit 1`, indexed on `user_id`).

- **No registered agent** → land on `/getting-started`. An empty dashboard
  tells a new user nothing.
- **Agent registered** → land on `/`, and the "Getting Started" item drops out
  of the sidebar. The page stays reachable, and the Devices page embeds the
  same guide for adding a second machine.

The rule lives on the server (`landing` in the session response) so it is
decided in one place. A deep link the user was blocked from always wins over
it.

`has_devices` is `null` when the backend could not find out, and that is
treated as *unknown*, never as "no": being wrongly told to install an agent
you already run is worse than not being prompted. The nav item is likewise
kept while the answer is unknown.

Only the dashboard redirects. Every other page stays reachable before setup,
so someone who wants to look around first is never trapped. The answer is
cached per account in `localStorage` so a reload makes the decision
immediately instead of painting the dashboard and bouncing; the first login
awaits the check before navigating, so it does not flash either.

## Tenant scoping

An account is a tenant. `Principal.user_id` is the only scope key any query may
filter on, and it comes from the validated token — never from a query
parameter, header or request body. `/api/auth/session` returns the resolved
scope so the frontend can assert it.

Three gaps were found and closed while gating the app:

- **Agent ingest was not ownership-checked.** A per-user agent token could post
  telemetry or a heartbeat for *any* `device_id`, including another account's.
  `_assert_agent_owns_device()` now rejects that with `404` (not `403` — a
  `403` would confirm the device exists).
- **Incidents and alerts were written with no `user_id`**, while
  `/api/incidents` filters by `user_id` — so engine-generated incidents were
  invisible to the account they concerned. The owner is now propagated from the
  agent token through `evaluate_alerts` and `evaluate_and_create_incidents`.
- **Guest run keying is gone.** Diagnostic runs were keyed by an
  `X-Guest-Session` header; they are now keyed by account, and that header is
  ignored.

## Deploying this change

1. **Supabase → SQL Editor** — run `migrations/007_user_profiles.sql`.
   (Skipping it does not break sign-in; the tour just replays until it is run.)
2. **Render → Manual Deploy → Deploy latest commit.**
3. Vercel auto-deploys the frontend.

No new environment variables are required. `AUTH_REQUIRED` and
`EXPOSE_API_DOCS` are both optional overrides.

## Verifying

```bash
python -m pytest tests/test_auth_gate.py -q      # the gate, session check, logout, scoping
node --experimental-strip-types --no-warnings scripts/verify_auth_coldstart.mjs
```

The second script takes about four minutes: it waits out real cold-start
timings against the actual `src/lib/api.ts` logic, because the thing being
checked is the timing.
