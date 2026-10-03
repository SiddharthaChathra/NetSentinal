"""Help for using the website: the knowledge base, its retrieval, combined
intent handling in "Ask NetSentinel", and stuck-user nudges.

The knowledge-base tests are the ones that keep the help honest over time:
every entry names the exact UI strings it quotes and the file each lives in,
and the suite fails if any of them disappears. A renamed button or a changed
limit therefore breaks the build instead of leaving help text quietly wrong.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src import ai_data, ai_features, ai_help, llm
from src.ai_grounding import INSUFFICIENT, NO_INSTRUCTIONS, find_ungrounded
from src.api import app
from src.llm import LLMResponse
from tests.conftest import OTHER_USER_ID, TEST_USER_ID
from tests.fake_supabase import FakeSupabase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def device(id, user, name, last_seen=None, created=None, backup=False):
    return {"id": id, "user_id": user, "name": name, "hostname": name, "ip_address": "10.9.8.7",
            "last_seen": last_seen, "created_at": created or ago(days=3), "is_backup_target": backup}


def incident(id, user, dev, title, status="OPEN", started=None, evidence=(), recs=(), cause=""):
    return {"id": id, "user_id": user, "device_id": dev, "title": title, "severity": "critical",
            "status": status, "likely_cause": cause, "confidence": "high", "evidence": list(evidence),
            "recommended_actions": list(recs), "started_at": started or ago(hours=2),
            "acknowledged_at": None, "resolved_at": None}


class FakeLLM:
    def __init__(self):
        self.calls, self.reply = [], "A grounded answer."

    def __call__(self, system_prompt, user_prompt, max_tokens=600):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        text = self.reply(user_prompt) if callable(self.reply) else self.reply
        return LLMResponse(text=text, provider="fake", model="fake-1", latency_ms=1)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for var in ("LLM_PROVIDER", "GROQ_API_KEY", "AI_REQUESTS_PER_MINUTE"):
        monkeypatch.delenv(var, raising=False)
    ai_features.reset_rate_limits()
    llm.reset_breaker()
    yield
    llm._transport = None


def use_db(monkeypatch, tables):
    fake = FakeSupabase(tables)
    monkeypatch.setattr(ai_data, "is_database_configured", lambda: True)
    monkeypatch.setattr(ai_data, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def model(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(ai_features, "call_llm", fake)
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def established_account():
    """Two machines reporting, and a backup target that failed last night."""
    last_night = (NOW - timedelta(days=1)).replace(hour=23, minute=40)
    return {
        "devices": [
            device("d-web", TEST_USER_ID, "web-01", last_seen=ago(seconds=30)),
            device("d-db", TEST_USER_ID, "db-backup-02", last_seen=ago(seconds=40), backup=True),
        ],
        "incidents": [
            incident("i-db", TEST_USER_ID, "d-db", "TCP service connectivity issue", "RESOLVED",
                     started=last_night.isoformat(), cause="TCP service, proxy, or firewall connectivity problem.",
                     evidence=["TCP connection failed to db-backup-02:445."],
                     recs=["Check local or network firewall blocking outbound ports."]),
            incident("i-web", TEST_USER_ID, "d-web", "Unusually high latency", "RESOLVED",
                     started=ago(days=4), evidence=["Average latency is 220ms (>150ms threshold)."]),
        ],
        "enrollment_codes": [], "user_profiles": [],
    }


def brand_new_account():
    return {"devices": [], "incidents": [], "enrollment_codes": [],
            "user_profiles": [{"user_id": TEST_USER_ID, "first_seen_at": ago(hours=2)}]}


# =============================================================================
# The knowledge base itself
# =============================================================================

class TestKnowledgeBase:
    def test_every_anchor_still_exists_in_the_code(self):
        """If this fails, the UI changed and the help entry named here now
        describes something that is no longer there. Update the entry."""
        missing = []
        for e in ai_help.load_kb():
            for a in e["anchors"]:
                with open(os.path.join(ROOT, a["file"]), encoding="utf-8") as fh:
                    if a["text"] not in fh.read():
                        missing.append(f"{e['id']}: {a['file']} no longer contains {a['text']!r}")
        assert not missing, "\n".join(missing)

    def test_no_text_contains_a_line_break_or_control_character(self):
        """A script once wrote ".\\netsentinel-agent-windows.exe" with "\\n" as a
        real line break, so the help showed a broken Windows command and the
        answer check (rightly) rejected the model quoting the correct one."""
        bad = []

        def walk(value, where):
            if isinstance(value, str):
                if any(ord(c) < 32 for c in value):
                    bad.append(f"{where}: {value[:80]!r}")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    walk(v, f"{where}[{i}]")
            elif isinstance(value, dict):
                for k, v in value.items():
                    walk(v, f"{where}.{k}")

        walk(ai_help.load_kb(), "entries")
        assert not bad, "\n".join(bad)

    def test_windows_commands_in_the_help_are_intact(self):
        text = json.dumps(ai_help.load_kb())
        assert ".\\\\netsentinel-agent-windows.exe --install-autostart" in text
        assert ".\\\\netsentinel-agent-windows.exe --start" in text

    def test_entries_are_complete_and_unique(self):
        kb = ai_help.load_kb()
        ids = [e["id"] for e in kb]
        assert len(ids) == len(set(ids))
        for e in kb:
            for field in ("title", "where", "summary", "keywords", "steps", "problems", "anchors"):
                assert e.get(field) is not None, f"{e['id']} lacks {field}"
            assert e["keywords"] and e["anchors"], e["id"]
            assert e["steps"] or e["problems"], e["id"]

    @pytest.mark.parametrize("feature, entry_id", [
        ("signing up / logging in", "sign-up-and-sign-in"),
        ("adding a monitored target", "add-a-device"),
        ("Health Score", "health-score"),
        ("Backup Readiness and NFS/SMB/iSCSI", "backup-readiness"),
        ("installing and connecting the agent", "add-a-device"),
        ("waiting for agent", "waiting-for-the-machine"),
        ("diagnostics feed", "diagnostics-feed"),
        ("Devices/Topology", "topology"),
        ("onboarding tour", "onboarding-tour"),
        ("logout / multi-device login", "sessions-and-sign-out"),
    ])
    def test_every_requested_feature_is_covered(self, feature, entry_id):
        assert ai_help.entry(entry_id), feature

    def test_the_help_does_not_promise_an_install_command_or_discovery(self):
        """The spec assumed both. The app has neither, and the help says so
        rather than describing features that do not exist."""
        add = ai_help.entry("add-a-device")["summary"]
        assert "There is no install command to paste" in add
        assert "does not scan or discover" in ai_help.entry("topology")["summary"]
        assert "no button on the website to replay the tour" in " ".join(ai_help.entry("onboarding-tour")["steps"])


# =============================================================================
# Retrieval
# =============================================================================

class TestHelpRetrieval:
    @pytest.mark.parametrize("question, expected", [
        ("how do I add a target", "add-a-device"),
        ("How do I install the agent on my laptop?", "add-a-device"),
        ("my enrollment code expired", "add-a-device"),
        ("it's stuck waiting for the machine to check in", "waiting-for-the-machine"),
        ("my device shows offline", "device-offline"),
        ("what does NFS readiness mean", "backup-readiness"),
        ("what does the health score mean", "health-score"),
        ("how do I log out", "sessions-and-sign-out"),
        ("I forgot my password", "password-reset"),
        ("can I see the tour again", "onboarding-tour"),
        ("how do I download a PDF report", "reports"),
        ("does topology auto discover my network", "topology"),
        ("why isn't anything showing up", "no-data-showing"),
    ])
    def test_the_right_entry_comes_first(self, question, expected):
        hits = ai_help.retrieve(question)
        assert hits and hits[0]["id"] == expected, [h["id"] for h in hits]

    def test_every_chat_suggestion_chip_is_answerable(self):
        """The chips in the chat panel are read from the component itself. A
        chip that retrieves nothing answers "I don't have instructions for that
        yet" - found when the chips were changed to include "Why isn't my
        agent connecting?", which matched no article."""
        import re
        src = open(os.path.join(ROOT, "frontend/src/components/AskNetSentinelChat.tsx"), encoding="utf-8").read()
        block = re.search(r"const SUGGESTIONS = \[(.*?)\];", src, re.S).group(1)
        chips = re.findall(r'"([^"]+)"', block)
        assert chips
        for chip in chips:
            assert ai_help.retrieve(chip), f"chat chip {chip!r} retrieves no help article"

    @pytest.mark.parametrize("question", [
        "What's the capital of France?",
        "how do I change the dashboard colour theme to purple?",
        "why did db-backup-02 fail last night",
    ])
    def test_unrelated_or_purely_diagnostic_questions_match_no_help(self, question):
        assert ai_help.retrieve(question) == []


# =============================================================================
# The four required cases, through the endpoint
# =============================================================================

class TestRequiredCases:
    def test_1_pure_usage_question_is_answered_from_help(self, monkeypatch, model, client):
        use_db(monkeypatch, established_account())
        body = client.post("/api/ai/ask", json={"question": "how do I add a target"}).json()
        assert body["facts"]["sources"] == ["help"]
        assert body["facts"]["help"][0]["id"] == "add-a-device"
        prompt = model.calls[0]["user"]
        assert "<help>" in prompt and 'click "Add a device"' in prompt
        assert "TCP connection failed" not in prompt  # findings are not part of a how-to answer

    def test_1b_without_a_model_the_help_itself_is_shown(self, monkeypatch, client):
        use_db(monkeypatch, established_account())
        monkeypatch.setenv("LLM_PROVIDER", "disabled")
        body = client.post("/api/ai/ask", json={"question": "how do I add a target"}).json()
        assert body["ai"]["status"] == "unavailable"
        assert "Open Devices in the sidebar and click \"Add a device\"." in body["text"]

    def test_2_pure_diagnostic_question_works_as_before(self, monkeypatch, model, client):
        use_db(monkeypatch, established_account())
        body = client.post("/api/ai/ask", json={"question": "why did db-backup-02 fail last night"}).json()
        assert body["facts"]["sources"] == ["diagnostics"]
        assert [f["id"] for f in body["facts"]["findings"]] == ["i-db"]
        prompt = model.calls[0]["user"]
        assert "TCP connection failed to db-backup-02:445." in prompt
        assert "<help>" not in prompt and "web-01" not in prompt

    def test_3a_mixed_question_on_an_empty_account_uses_help_and_account_facts(self, monkeypatch, model, client):
        use_db(monkeypatch, brand_new_account())
        body = client.post("/api/ai/ask", json={"question": "why isn't anything showing up"}).json()
        assert body["facts"]["sources"] == ["help"]
        assert body["facts"]["help"][0]["id"] == "no-data-showing"
        prompt = model.calls[0]["user"]
        assert "no devices are registered yet" in prompt
        assert "Add a device" in prompt

    def test_3b_mixed_question_with_data_uses_both_sources(self, monkeypatch, model, client):
        tables = established_account()
        tables["devices"][0]["last_seen"] = ago(hours=6)  # web-01 has stopped reporting
        use_db(monkeypatch, tables)
        body = client.post("/api/ai/ask", json={"question": "why isn't anything showing up for web-01"}).json()
        assert set(body["facts"]["sources"]) == {"diagnostics", "help"}
        prompt = model.calls[0]["user"]
        assert "- web-01: OFFLINE" in prompt          # the account fact that explains it
        assert "Unusually high latency" in prompt     # its findings
        assert "<help>" in prompt                     # and what to do about it
        assert "device-offline" in [h["id"] for h in body["facts"]["help"]]
        assert "--start" in prompt                    # including how to restart it

    @pytest.mark.parametrize("question", [
        "how do I change the dashboard colour theme to purple?",
        "What's the capital of France?",
    ])
    def test_4_no_answer_anywhere_is_said_honestly_without_the_model(self, monkeypatch, model, client, question):
        use_db(monkeypatch, established_account())
        body = client.post("/api/ai/ask", json={"question": question}).json()
        assert model.calls == []
        assert body["facts"]["sources"] == []
        assert body["text"].startswith(NO_INSTRUCTIONS)


class TestAccountStateOnlyWhereItMatters:
    """Live regression (llama3.1:8b): with a device offline, account facts and
    the offline article were attached to every help answer, and the model led
    "how do I replay the tour?" with "your devices are OFFLINE"."""

    def _offline(self):
        t = established_account()
        t["devices"][0]["last_seen"] = ago(hours=6)
        return t

    @pytest.mark.parametrize("question, entry_id", [
        ("how can I replay the onboarding tour?", "onboarding-tour"),
        ("what does NFS readiness mean on the backup page?", "backup-readiness"),
        ("what's the install command for the agent on ubuntu?", "add-a-device"),
    ])
    def test_unrelated_how_tos_get_no_account_state(self, monkeypatch, model, client, question, entry_id):
        use_db(monkeypatch, self._offline())
        body = client.post("/api/ai/ask", json={"question": question}).json()
        assert [h["id"] for h in body["facts"]["help"]] == [entry_id]
        assert "OFFLINE" not in model.calls[0]["user"].split("<help>")[0]
        assert "account" not in body["facts"]

    def test_state_dependent_questions_still_get_it(self, monkeypatch, model, client):
        use_db(monkeypatch, self._offline())
        body = client.post("/api/ai/ask", json={"question": "why isn't anything showing up"}).json()
        assert "- web-01: OFFLINE" in model.calls[0]["user"]
        assert "device-offline" in [h["id"] for h in body["facts"]["help"]]


class TestHelpAnswersAreChecked:
    @pytest.mark.parametrize("invented", [
        "Run sudo systemctl restart netsentinel-agent and it will connect.",
        'Click "Settings", then "Agents", then "Add".',
        "Paste `curl -sSL https://get.netsentinel.io | sh` into a terminal.",
        "Run ./netsentinel-agent-linux --repair to fix it.",
    ])
    def test_an_invented_command_or_button_is_withheld(self, monkeypatch, model, client, invented):
        use_db(monkeypatch, established_account())
        model.reply = invented
        body = client.post("/api/ai/ask", json={"question": "how do I add a target"}).json()
        assert body["ai"]["status"] == "rejected", invented
        assert "Open Devices in the sidebar" in body["text"]  # the help, verbatim

    @pytest.mark.parametrize("grounded", [
        'Open Devices and click "Add a device", then choose Windows or Linux.',
        "If you linked it from a terminal, run ./netsentinel-agent-linux --start to keep it reporting.",
        "Click **New code** if the code has expired.",
        'Open Devices in the sidebar and click **“Add a device.”**',  # live false positive
    ])
    def test_a_faithful_answer_passes(self, monkeypatch, model, client, grounded):
        use_db(monkeypatch, established_account())
        model.reply = grounded
        body = client.post("/api/ai/ask", json={"question": "how do I add a target from a terminal"}).json()
        assert body["ai"]["status"] == "ok", grounded

    def test_the_prompt_extends_grounding_without_loosening_it(self):
        from src.ai_grounding import SYSTEM_PROMPT
        assert "<help>" in SYSTEM_PROMPT and "<findings>" in SYSTEM_PROMPT
        assert NO_INSTRUCTIONS in SYSTEM_PROMPT and INSUFFICIENT in SYSTEM_PROMPT
        assert "Never invent a button, page, menu item, setting or command" in SYSTEM_PROMPT


# =============================================================================
# Stuck-user nudges
# =============================================================================

class TestNudges:
    def _nudges(self, monkeypatch, tables):
        use_db(monkeypatch, tables)
        return ai_help.nudges(TEST_USER_ID, now=NOW)

    def test_new_account_with_no_machines(self, monkeypatch):
        n = self._nudges(monkeypatch, brand_new_account())
        assert [x["kind"] for x in n] == ["no_devices"] and n[0]["help_id"] == "add-a-device"

    def test_a_brand_new_account_is_given_time_first(self, monkeypatch):
        tables = brand_new_account()
        tables["user_profiles"][0]["first_seen_at"] = ago(minutes=3)
        assert self._nudges(monkeypatch, tables) == []

    def test_code_created_but_never_entered(self, monkeypatch):
        tables = brand_new_account()
        tables["enrollment_codes"] = [{"user_id": TEST_USER_ID, "code": "X", "created_at": ago(minutes=40),
                                       "expires_at": ago(minutes=25), "used_at": None}]
        n = self._nudges(monkeypatch, tables)
        assert [x["kind"] for x in n] == ["code_never_used"]

    def test_a_code_still_valid_is_left_alone(self, monkeypatch):
        tables = brand_new_account()
        tables["enrollment_codes"] = [{"user_id": TEST_USER_ID, "code": "X", "created_at": ago(minutes=2),
                                       "expires_at": (NOW + timedelta(minutes=13)).isoformat(), "used_at": None}]
        assert self._nudges(monkeypatch, tables) == []

    def test_agent_linked_but_machine_never_appeared(self, monkeypatch):
        tables = brand_new_account()
        tables["enrollment_codes"] = [{"user_id": TEST_USER_ID, "code": "X", "created_at": ago(minutes=20),
                                       "expires_at": ago(minutes=5), "used_at": ago(minutes=18),
                                       "used_by_hostname": "LAB-PC"}]
        n = self._nudges(monkeypatch, tables)
        assert n[0]["kind"] == "linked_not_registered" and "LAB-PC" in n[0]["message"]
        assert n[0]["help_id"] == "waiting-for-the-machine"

    def test_a_second_machine_that_never_linked_is_noticed_despite_the_first_reporting(self, monkeypatch):
        tables = established_account()
        tables["enrollment_codes"] = [{"user_id": TEST_USER_ID, "code": "X", "created_at": ago(minutes=40),
                                       "expires_at": ago(minutes=25), "used_at": None}]
        assert self._nudges(monkeypatch, tables)[0]["kind"] == "code_never_used"

    def test_a_device_that_stopped_reporting(self, monkeypatch):
        tables = established_account()
        tables["devices"][1]["last_seen"] = ago(minutes=30)
        n = self._nudges(monkeypatch, tables)
        assert n[0]["kind"] == "device_offline" and "db-backup-02 is OFFLINE" in n[0]["message"]

    def test_a_healthy_account_gets_nothing(self, monkeypatch):
        assert self._nudges(monkeypatch, established_account()) == []

    def test_advice_is_the_help_content_verbatim(self, monkeypatch):
        tables = established_account()
        tables["devices"][1]["last_seen"] = ago(minutes=30)
        n = self._nudges(monkeypatch, tables)[0]
        entry = ai_help.entry(n["help_id"])
        all_advice = entry["steps"] + [f for p in entry["problems"] for f in p["fixes"]]
        assert n["tips"] and all(t in all_advice for t in n["tips"])
        assert n["tips"][0] == entry["steps"][0]  # the main fix leads

    def test_endpoint_and_gate(self, monkeypatch, client, anon_client):
        use_db(monkeypatch, brand_new_account())
        assert client.get("/api/ai/nudges").json()["nudges"][0]["kind"] == "no_devices"
        assert anon_client.get("/api/ai/nudges").status_code == 401

    def test_a_database_failure_means_no_nudge_not_an_error(self, monkeypatch, client):
        use_db(monkeypatch, brand_new_account()).fail = True
        r = client.get("/api/ai/nudges")
        assert r.status_code == 200 and r.json() == {"nudges": []}


# =============================================================================
# Tenancy for the new data paths
# =============================================================================

class TestTenancy:
    def _two_accounts(self):
        a = established_account()
        a["devices"][1]["last_seen"] = ago(minutes=30)          # A has an offline device
        a["enrollment_codes"] = [{"user_id": TEST_USER_ID, "code": "A", "created_at": ago(minutes=40),
                                  "expires_at": ago(minutes=25), "used_at": ago(minutes=39),
                                  "used_by_hostname": "A-SECRET-HOST"}]
        a["user_profiles"] = [{"user_id": OTHER_USER_ID, "first_seen_at": ago(minutes=1)}]
        return a

    def test_org_b_never_sees_org_a_through_nudges_or_account_facts(self, monkeypatch, model, other_client):
        use_db(monkeypatch, self._two_accounts())
        model.reply = lambda prompt: prompt  # echo: any leaked row would surface
        nudge = other_client.get("/api/ai/nudges").text
        answer = other_client.post("/api/ai/ask", json={"question": "why isn't anything showing up"}).text
        for marker in ("db-backup-02", "web-01", "A-SECRET-HOST", "10.9.8.7"):
            assert marker not in nudge and marker not in answer, marker
        assert "no devices are registered yet" in answer

    def test_every_new_query_is_scoped(self, monkeypatch, model, client):
        fake = use_db(monkeypatch, self._two_accounts())
        scoped = []
        real_table = fake.table

        def spy(name):
            q = real_table(name)
            eq = q.eq
            q.eq = lambda col, val: (scoped.append((name, val)) if col == "user_id" else None) or eq(col, val)
            return q

        monkeypatch.setattr(fake, "table", spy)
        client.get("/api/ai/nudges")
        client.post("/api/ai/ask", json={"question": "why isn't anything showing up"})
        assert len(scoped) == len(fake.queries)
        assert {uid for _, uid in scoped} == {TEST_USER_ID}
