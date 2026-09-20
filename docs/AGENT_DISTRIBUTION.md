# Distributing the agent

The agent ships as a standalone executable. Adding a machine is: download,
run, type an 8-character code. No Python, no pip, no git, no `.env`.

## The flow

```
website (signed in)                     the machine being added
───────────────────                     ───────────────────────
Devices -> Add a device
  ├─ choose Windows / Linux
  ├─ download the binary  ──────────▶   netsentinel-agent-windows.exe
  └─ POST /api/devices/enroll-code
         shows  DEYM-WF21              run it; it asks for the code
                                          │
                                          ▼
                                       POST /api/agent/enroll {code}
                                          │  (no session; the code IS
                                          │   the credential)
                                          ▼
                                       receives the account's agent token,
                                       saves it per-user, registers, reports
         ◀──────────────────────────── device appears; the wizard notices
```

## Why a code rather than the token

The agent token is long-lived and covers the whole account. Putting it on a
screen for someone to copy onto another machine — or worse, embedding it in
the downloaded binary — means the credential travels, gets screenshotted, and
survives in Downloads folders. An enrolment code:

- is 8 characters, readable off one screen and typeable on another
- expires after 15 minutes and works exactly once
- is useless to anyone who sees it later

Crockford base32 (no I, L, O or U) so the classic transcription slips are
impossible, and the ones people still make — typing `O` for `0` — are mapped
to what they meant rather than rejected. Dashes, spaces and lower case are all
accepted.

Redemption is rate-limited per client (10 attempts / 5 minutes), and
"no such code", "expired" and "already used" return the *same* message, so the
endpoint cannot be used to discover which codes exist.

## Adding several machines

One account, any number of machines. Each machine:

- gets **its own enrolment code** (a code is single-use)
- receives **the same account agent token** — that is what makes them one fleet
- registers under **its own hostname**, which is what makes them separate devices

Two machines sharing a hostname are treated as one device. The wizard says so
at the point where it matters.

## What the binary keeps, and where

| Path | Holds |
|---|---|
| `%LOCALAPPDATA%\NetSentinel` (Windows) | credentials, device id, buffer, logs |
| `~/.local/share/netsentinel` (Linux) | same |
| `~/Library/Application Support/NetSentinel` (macOS) | same |
| `$NETSENTINEL_HOME` | overrides all of the above |

A source checkout that already has `agent_data/` keeps using it, so upgrading
from the old layout does not orphan a registered device.

**`config.json` records the hostname it was created for.** If the directory is
copied to another machine — easy to do with a portable binary — the mismatch
is detected and that machine registers as itself. Without this, the second
machine would report under the first one's device id and the two would
silently collapse into one device with interleaved readings.

## Building

```bash
pip install pyinstaller -r agent/requirements.txt
pyinstaller agent/netsentinel-agent.spec --noconfirm --clean
```

`.github/workflows/build-agent.yml` does this on Windows and Linux. Pushing a
tag (`v1.1.0`) publishes a release with the binaries attached under stable
names, which is what `/releases/latest/download/<name>` resolves to — so the
download links never need updating.

The workflow runs `--version` and `--status` against every binary before
publishing. That is not ceremony: PyInstaller failures are silent and late,
and a binary that dies on a missed import is worse than no binary, because it
gets downloaded and double-clicked before anyone finds out.

## Keeping it running

The agent reports only while its process is alive. `agent/install_autostart.ps1`
(Windows Scheduled Task) and `agent/install_autostart.sh` (systemd user
service) register it to start at login and restart if it stops.

## Verifying a build

```bash
# the binary alone, against a stub server
python scripts/verify_agent_binary.py dist/netsentinel-agent-windows.exe

# the binary against the real API over HTTP, two machines, one account
python scripts/verify_add_device_e2e.py dist/netsentinel-agent-windows.exe
```

The second one runs the actual FastAPI app with an in-memory store standing in
for Supabase; everything above that line is shipping code. It is what caught
the agent following a server-supplied `api_base_url` — which would have moved
a self-hosted deployment's agents onto the hosted service, with a token that
is not valid there. The agent now keeps talking to whatever host it enrolled
against.

## Deploying

1. Supabase → SQL Editor → run `migrations/008_enrollment_codes.sql`
2. Render → Manual Deploy → Deploy latest commit
3. Tag a release (`git tag v1.1.0 && git push --tags`) so the binaries exist
   at the download URLs the site points to
4. Vercel picks up the frontend automatically

Until step 3 the wizard's download links 404 — everything else works.
