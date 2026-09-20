# Refreshing the README screenshots

Every capture in `screenshots/` comes from a local instance seeded with
synthetic data, so a public README never carries a real hostname, LAN address
or account.

```bash
# 1. the API, with a three-machine fleet already reporting
python scripts/demo_server.py

# 2. the UI, pointed at it
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Then drive a browser at `http://localhost:3000`, and before the first capture
seed a session so the route guard lets you through — the API runs with
`AUTH_REQUIRED=0`, so the token is never checked:

```js
const ref = "awsnemrstsjsydwcjfcw";           // the Supabase project ref
const now = Math.floor(Date.now() / 1000);
localStorage.setItem(`sb-${ref}-auth-token`, JSON.stringify({
  access_token: "local.screenshot.session", token_type: "bearer",
  expires_in: 3600, expires_at: now + 3600, refresh_token: "local-refresh",
  user: { id: "local-dev-user", aud: "authenticated", role: "authenticated",
          email: "ops@netsentinel.demo", app_metadata: {}, user_metadata: {},
          created_at: new Date().toISOString() },
}));
localStorage.setItem("netsentinel_onboarding_done", "true");
localStorage.setItem("netsentinel_needs_setup:local-dev-user", "false");

// the Next dev-tools badge must not end up in a README image
const s = document.createElement("style");
s.textContent = `nextjs-portal,[data-nextjs-toast],#__next-build-watcher{display:none!important}`;
document.head.appendChild(s);
```

Capture at **1440×900**, one file per page:

| File | Page | Note |
|---|---|---|
| `auth.png` | `/auth` | signed out — clear the session first |
| `dashboard.png` | `/` | click **Why is my network slow?** first |
| `devices.png` | `/devices` | |
| `add-device.png` | `/devices` | with the Add a device wizard open |
| `backup.png` | `/backup` | |
| `topology.png` | `/topology` | |
| `history.png` | `/history` | |
| `incidents.png` | `/incidents` | expand the open incident |
| `report.png` | `/report` | |
| `getting-started.png` | `/getting-started` | |
