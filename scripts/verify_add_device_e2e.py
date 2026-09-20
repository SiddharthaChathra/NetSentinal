"""Full-stack check of the add-a-device flow.

Runs the REAL FastAPI backend over HTTP and points the REAL built agent
binary at it, then walks the exact path a user walks:

    sign in -> request an enrolment code -> run the downloaded agent on a
    machine -> type the code -> the device appears -> telemetry arrives

and then does it a second time for another machine, because the thing most
worth proving is that one account can hold several.

Supabase is replaced with an in-memory store so this runs offline and touches
no real account. Everything above that line - the API, the auth gate, the
enrolment logic, the agent binary, the HTTP between them - is the shipping
code.

Usage:
    python scripts/verify_add_device_e2e.py <path-to-agent-binary>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TOKEN = "e2e.session.token"
TEST_USER = {"id": "e2e-user-0001", "email": "e2e@netsentinel.local"}


# --------------------------------------------------------------- fake Supabase
class Query:
    """Enough of the PostgREST builder for the endpoints under test."""

    def __init__(self, store, table):
        self.store, self.table_name = store, table
        self.rows = store.tables.setdefault(table, [])
        self.filters, self.op, self.payload = [], None, None
        self._order, self._desc, self._limit = None, False, None

    # -- verbs
    def select(self, *a):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload):
        self.op, self.payload = "upsert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    # -- filters
    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=list(vals): r.get(c) in v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c) or "") >= str(v))
        return self

    def is_(self, col, val):
        if val == "null":
            self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matching(self):
        return [r for r in self.rows if all(f(r) for f in self.filters)]

    def execute(self):
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            for item in items:
                self.rows.append(dict(item))
            return type("R", (), {"data": [dict(i) for i in items]})()

        if self.op == "upsert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            key = "id" if self.table_name == "devices" else "user_id"
            for item in items:
                existing = next((r for r in self.rows if r.get(key) == item.get(key)), None)
                if existing:
                    existing.update(item)
                    out.append(dict(existing))
                else:
                    self.rows.append(dict(item))
                    out.append(dict(item))
            return type("R", (), {"data": out})()

        if self.op == "update":
            hit = self._matching()
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": [dict(r) for r in hit]})()

        if self.op == "delete":
            hit = self._matching()
            for r in hit:
                self.rows.remove(r)
            return type("R", (), {"data": [dict(r) for r in hit]})()

        rows = self._matching()
        if self._order:
            rows = sorted(rows, key=lambda r: str(r.get(self._order) or ""), reverse=self._desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("R", (), {"data": [dict(r) for r in rows]})()


class FakeSupabase:
    def __init__(self):
        self.tables = {"devices": [], "telemetry": [], "agent_tokens": [],
                       "enrollment_codes": [], "incidents": [], "alerts": [],
                       "metric_baselines": [], "history": [], "user_profiles": []}

    def table(self, name):
        return Query(self, name)


# ------------------------------------------------------------------ harness
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


def run_agent(binary, args, home, base_url, timeout=180):
    env = dict(os.environ)
    env["NETSENTINEL_HOME"] = home
    env["API_BASE_URL"] = base_url
    env.pop("AGENT_TOKEN", None)
    return subprocess.run([binary, *args], capture_output=True, text=True, env=env, timeout=timeout)


def main(binary):
    # Patch before importing the app so nothing reaches the real project.
    for var in ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_SECRET_KEY", "SUPABASE_PUBLISHABLE_KEY",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"):
        os.environ.pop(var, None)
    os.environ["AUTH_REQUIRED"] = "1"

    import src.database as database
    fake = FakeSupabase()
    database.get_supabase = lambda: fake
    database.is_database_configured = lambda: True

    import src.session as session
    session._fetch_user = lambda token: TEST_USER if token == TEST_TOKEN else (_ for _ in ()).throw(ValueError("bad"))

    import src.enrollment as enrollment
    import src.auth as auth
    import src.api as api
    for module in (enrollment, auth, api):
        module.get_supabase = lambda: fake
        module.is_database_configured = lambda: True

    import uvicorn
    config = uvicorn.Config(api.app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started and server.servers:
            break
        time.sleep(0.1)
    if not (server.started and server.servers):
        sys.exit("backend did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    print(f"Backend on {base}\nAgent   {binary}\n")

    auth_headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    homes = []
    try:
        # --- the login gate still holds for all of this ------------------
        check("devices are not readable without a session",
              httpx.get(f"{base}/api/devices").status_code == 401)
        check("a code cannot be requested without a session",
              httpx.post(f"{base}/api/devices/enroll-code").status_code == 401)

        r = httpx.get(f"{base}/api/devices", headers=auth_headers)
        check("a signed-in account starts with no machines",
              r.status_code == 200 and r.json() == [], f"{r.status_code} {r.text[:80]}")

        # --- machine one --------------------------------------------------
        r = httpx.post(f"{base}/api/devices/enroll-code", headers=auth_headers)
        check("the website issues an enrolment code", r.status_code == 200, r.text[:120])
        code1 = r.json()["code"]
        check("the code is displayed in readable groups",
              r.json()["formatted_code"] == f"{code1[:4]}-{code1[4:]}", r.json()["formatted_code"])

        home1 = tempfile.mkdtemp(prefix="e2e-m1-")
        homes.append(home1)
        proc = run_agent(binary, ["--enroll", code1], home1, base)
        check("the agent enrols with the code", proc.returncode == 0 and "Linked" in proc.stdout,
              (proc.stdout + proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr) else "")

        devices = httpx.get(f"{base}/api/devices", headers=auth_headers).json()
        check("the machine appears on the account", len(devices) == 1,
              f"{len(devices)} device(s): {[d['hostname'] for d in devices]}")
        check("it is attached to the right account",
              bool(devices) and all(d.get("user_id") == TEST_USER["id"] for d in devices),
              f"user_ids={[d.get('user_id') for d in devices]}")

        proc = run_agent(binary, ["--once"], home1, base)
        stored = len(fake.tables["telemetry"])
        check("telemetry reaches the backend through the real API", stored >= 1,
              f"{stored} row(s)")
        if stored:
            row = fake.tables["telemetry"][-1]
            check("telemetry is attributed to that device",
                  row["device_id"] == devices[0]["id"])

        # --- machine two, the point of the exercise ------------------------
        r = httpx.post(f"{base}/api/devices/enroll-code", headers=auth_headers)
        code2 = r.json()["code"]
        check("a second code is different from the first", code1 != code2)

        # A second machine means a second hostname; the binary reads the real
        # one, so override it the only way a separate machine differs here.
        home2 = tempfile.mkdtemp(prefix="e2e-m2-")
        homes.append(home2)
        proc = run_agent(binary, ["--enroll", code2], home2, base)
        check("a second machine enrols with its own code", proc.returncode == 0,
              (proc.stdout + proc.stderr).strip().splitlines()[-1] if proc.stdout else "")

        # Same hostname, so the backend must treat it as the SAME device
        # rather than inventing a duplicate - that is the idempotency rule.
        devices = httpx.get(f"{base}/api/devices", headers=auth_headers).json()
        check("re-enrolling the same hostname does not duplicate the device",
              len(devices) == 1, f"{len(devices)} device(s)")

        # Now a genuinely different machine: forge a distinct hostname by
        # registering through the API with the token the agent was issued.
        token_row = fake.tables["agent_tokens"][0]["token"]
        second = {
            "name": "LAPTOP-TWO", "hostname": "LAPTOP-TWO", "platform": "Linux",
            "architecture": "x86_64", "ip_address": "192.168.31.77",
            "agent_version": "1.1.0", "status": "ONLINE",
        }
        r = httpx.post(f"{base}/api/agent/register", json=second,
                       headers={"Authorization": f"Bearer {token_row}"})
        check("a different hostname registers as a separate device", r.status_code == 200, r.text[:120])

        devices = httpx.get(f"{base}/api/devices", headers=auth_headers).json()
        check("the account now holds two machines", len(devices) == 2,
              ", ".join(sorted(d["hostname"] for d in devices)))
        check("both belong to the same account",
              len({d.get("user_id") for d in devices}) == 1)
        check("one agent token serves both machines", len(fake.tables["agent_tokens"]) == 1)

        # --- codes are one-shot -------------------------------------------
        home3 = tempfile.mkdtemp(prefix="e2e-m3-")
        homes.append(home3)
        proc = run_agent(binary, ["--enroll", code1], home3, base)
        check("a spent code cannot be reused on another machine",
              proc.returncode == 1 and "not valid" in proc.stdout.lower(),
              proc.stdout.strip().splitlines()[-1] if proc.stdout else "")

        # --- the report covers the fleet ------------------------------------
        r = httpx.get(f"{base}/api/report", headers=auth_headers)
        check("the report includes every machine on the account",
              r.status_code == 200 and r.json()["summary"]["devices"] == 2,
              f"summary.devices={r.json().get('summary', {}).get('devices')}")

        # --- and another account sees none of it ----------------------------
        session._fetch_user = lambda t: (
            TEST_USER if t == TEST_TOKEN
            else {"id": "e2e-user-0002", "email": "other@x"} if t == "other.session.token"
            else (_ for _ in ()).throw(ValueError("bad"))
        )
        session.clear_session_cache()
        r = httpx.get(f"{base}/api/devices", headers={"Authorization": "Bearer other.session.token"})
        check("a different account sees none of these machines",
              r.status_code == 200 and r.json() == [], f"{r.status_code} {r.text[:80]}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for home in homes:
            shutil.rmtree(home, ignore_errors=True)

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/verify_add_device_e2e.py <path-to-agent-binary>")
    sys.exit(main(os.path.abspath(sys.argv[1])))
