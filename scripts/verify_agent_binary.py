"""End-to-end check of a built agent binary against a stub server.

Why this exists: PyInstaller failures are silent and late. The binary builds
fine, the user downloads it, double-clicks it, and it dies on an import that
was never followed at analysis time — `collector` reaching into `src.*` is
exactly that shape. `--version` proves the bootloader works; only actually
enrolling and sending telemetry proves the whole graph does.

The stub server stands in for the real backend so this runs offline and
touches no account.

Usage:
    python scripts/verify_agent_binary.py <path-to-binary>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

RECEIVED = {"enroll": [], "register": [], "heartbeat": [], "telemetry": []}
TOKEN = "nsa_stub_token_for_verification"
CODE = "ABCD2345"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the output readable

    def _read(self):
        length = int(self.headers.get("content-length", 0) or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            return self._send(200, {"status": "healthy"})
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        body = self._read()
        auth = self.headers.get("authorization", "")

        if self.path == "/api/agent/enroll":
            RECEIVED["enroll"].append(body)
            if body.get("code", "").replace("-", "").upper() != CODE:
                return self._send(400, {"detail": "That code is not valid."})
            return self._send(200, {"token": TOKEN, "api_base_url": f"http://127.0.0.1:{PORT}"})

        # Everything past enrolment must present the token it was given.
        if auth != f"Bearer {TOKEN}":
            return self._send(401, {"detail": "Missing or bad agent token"})

        if self.path == "/api/agent/register":
            RECEIVED["register"].append(body)
            device = dict(body)
            device["id"] = "stub-device-0001"
            return self._send(200, device)
        if self.path == "/api/agent/heartbeat":
            RECEIVED["heartbeat"].append(body)
            return self._send(200, {"status": "received"})
        if self.path == "/api/agent/telemetry":
            RECEIVED["telemetry"].append(body)
            return self._send(200, {"status": "ingested"})
        self._send(404, {"detail": "not found"})


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")


def run(binary, args, home, extra_env=None, stdin=None, timeout=180):
    env = dict(os.environ)
    env["NETSENTINEL_HOME"] = home
    env["API_BASE_URL"] = f"http://127.0.0.1:{PORT}"
    env.pop("AGENT_TOKEN", None)
    env.update(extra_env or {})
    return subprocess.run([binary, *args], capture_output=True, text=True,
                          env=env, input=stdin, timeout=timeout)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/verify_agent_binary.py <path-to-binary>")
    binary = os.path.abspath(sys.argv[1])
    if not os.path.exists(binary):
        sys.exit(f"no such binary: {binary}")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Verifying {binary}\nStub server on 127.0.0.1:{PORT}\n")

    home_a = tempfile.mkdtemp(prefix="nsagent-a-")
    home_b = tempfile.mkdtemp(prefix="nsagent-b-")
    try:
        # 1. Starts at all.
        r = run(binary, ["--version"], home_a)
        check("binary starts and reports a version", r.returncode == 0 and "NetSentinel agent" in r.stdout,
              r.stdout.strip() or r.stderr.strip()[:120])

        # 2. Refuses to run before enrolment, with instructions rather than a traceback.
        r = run(binary, ["--once"], home_a)
        check("unenrolled agent explains itself instead of crashing",
              r.returncode == 2 and "not linked" in r.stdout.lower() and "Traceback" not in r.stderr)

        # 3. A wrong code is rejected and nothing is stored.
        r = run(binary, ["--enroll", "WRONGCOD"], home_a)
        check("a bad code is rejected cleanly",
              r.returncode == 1 and "not valid" in r.stdout.lower() and "Traceback" not in r.stderr)
        check("a rejected enrolment stores no credentials",
              not os.path.exists(os.path.join(home_a, "credentials.json")))

        # 4. The real thing: enrol, which must also register the device.
        r = run(binary, ["--enroll", CODE], home_a)
        check("enrolment succeeds with a valid code",
              r.returncode == 0 and "Linked" in r.stdout, r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[:160])
        check("the token is saved for next time",
              os.path.exists(os.path.join(home_a, "credentials.json")))
        check("enrolment registered the device", len(RECEIVED["register"]) == 1,
              f"{len(RECEIVED['register'])} registration(s)")

        # 5. Typed with dashes and lower case, as a human would.
        home_c = tempfile.mkdtemp(prefix="nsagent-c-")
        r = run(binary, ["--enroll", "abcd-2345"], home_c)
        check("a code typed with dashes and lower case is accepted",
              r.returncode == 0 and "Linked" in r.stdout)
        shutil.rmtree(home_c, ignore_errors=True)

        # 6. THE one that PyInstaller breaks: collecting telemetry runs
        #    collector.py, which imports src.gateway_monitor and friends.
        before = len(RECEIVED["telemetry"])
        r = run(binary, ["--once"], home_a)
        sent = len(RECEIVED["telemetry"]) - before
        check("the frozen binary collects and sends real telemetry", sent == 1,
              (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")
        if sent:
            row = RECEIVED["telemetry"][-1]
            check("telemetry carries the fields the backend requires",
                  all(k in row for k in ("device_id", "timestamp", "latency_ms", "packet_loss",
                                         "gateway_reachable", "internet_reachable", "dns_healthy")),
                  ", ".join(sorted(row)[:6]) + " ...")
            check("telemetry includes the agent-checked backup ports and gateway",
                  "backup_ports" in row and "gateway_ip" in row)

        # 7. It remembers, rather than re-enrolling every run.
        before = len(RECEIVED["enroll"])
        run(binary, ["--once"], home_a)
        check("a second run reuses the stored credentials", len(RECEIVED["enroll"]) == before)

        # 8. The multi-device guard: copying one machine's data directory to
        #    another machine must NOT make it report as the first one.
        shutil.copytree(home_a, home_b, dirs_exist_ok=True)
        config_path = os.path.join(home_b, "config.json")
        with open(config_path) as f:
            cfg = json.load(f)
        cfg["hostname"] = "SOME-OTHER-MACHINE"
        with open(config_path, "w") as f:
            json.dump(cfg, f)
        before = len(RECEIVED["register"])
        r = run(binary, ["--once"], home_b)
        check("a config copied from another machine is discarded, not reused",
              len(RECEIVED["register"]) == before + 1 and "separate device" in r.stdout,
              "re-registered as itself" if len(RECEIVED["register"]) == before + 1 else "REUSED the foreign id")

        # 9. Status reflects reality once enrolled.
        r = run(binary, ["--status"], home_a)
        check("status reports the linked state", "Linked        : yes" in r.stdout)
    finally:
        server.shutdown()
        shutil.rmtree(home_a, ignore_errors=True)
        shutil.rmtree(home_b, ignore_errors=True)

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)
