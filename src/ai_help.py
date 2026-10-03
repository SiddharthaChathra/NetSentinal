"""Help for using the website itself: retrieval over src/help_kb.json, and
"stuck user" nudges grounded in the same content.

Same discipline as the diagnostic assistant. Retrieval is plain keyword
matching, only the matched entries reach the model, and if nothing matches the
model is not asked. Nudges are not written by a model at all: the condition is
detected from the account's own rows and the advice is copied verbatim from
the matching help entry.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional

from src import ai_data
from src.backup_readiness import AGENT_STALE_SECONDS
from src.logger import logger

KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "help_kb.json")

# Exact sentence for a how-to question the help does not cover.
from src.ai_grounding import NO_INSTRUCTIONS  # noqa: E402  (one wording everywhere)

# Entries whose right answer depends on the account's current state, so
# account facts are attached to them - and only to them.
STATE_DEPENDENT = {"no-data-showing", "device-offline", "waiting-for-the-machine"}

PHRASE_WEIGHT = 3
WORD_WEIGHT = 2
MIN_SCORE = 2
MAX_ENTRIES = 3

# "How do I...", "where is...": the question is about using the site.
USAGE_CUE = re.compile(
    r"\b(?:how (?:do|can|to|does|should)|where (?:do|is|are|can)|what does|what is|what's|whats|"
    r"what are|can i|is there a way|help me|steps to|walk me through|show me how)\b",
    re.IGNORECASE,
)

_DASHES = re.compile("[‐-―−]")


@lru_cache(maxsize=1)
def load_kb() -> List[dict]:
    with open(KB_PATH, encoding="utf-8") as fh:
        return json.load(fh)["entries"]


def entry(entry_id: str) -> Optional[dict]:
    return next((e for e in load_kb() if e["id"] == entry_id), None)


def _normalise(text: str) -> str:
    text = _DASHES.sub("-", text.lower()).replace("’", "'")
    return " " + re.sub(r"[^a-z0-9.'\- ]+", " ", text) + " "


def score(question: str, e: dict) -> int:
    q = _normalise(question)
    tokens = set(q.split())
    total = 0
    for kw in e["keywords"]:
        kw_n = _normalise(kw).strip()
        if " " in kw_n:
            if f" {kw_n} " in q:
                total += PHRASE_WEIGHT
        elif kw_n in tokens or (kw_n + "s") in tokens:
            total += WORD_WEIGHT
    return total


def retrieve(question: str) -> List[dict]:
    """The help entries relevant to `question`, best first. Entries scoring
    well below the best match are dropped, so one strong hit is not diluted by
    incidental keyword overlaps."""
    scored = sorted(((score(question, e), e) for e in load_kb()), key=lambda se: -se[0])
    scored = [(s, e) for s, e in scored if s >= MIN_SCORE]
    if not scored:
        return []
    best = scored[0][0]
    return [e for s, e in scored if s * 2 >= best][:MAX_ENTRIES]


def is_usage_question(question: str) -> bool:
    return bool(USAGE_CUE.search(question))


def render_entry(e: dict) -> str:
    lines = [f"Help article: {e['title']}", f"  where: {e['where']}", f"  summary: {e['summary']}"]
    if e["steps"]:
        lines.append("  how to:")
        lines.extend(f"    {i}. {s}" for i, s in enumerate(e["steps"], 1))
    for p in e["problems"]:
        lines.append(f"  if {p['symptom']}:")
        lines.extend(f"    - {f}" for f in p["fixes"])
    return "\n".join(lines)


def help_block(entries: List[dict]) -> str:
    return "<help>\n" + "\n\n".join(render_entry(e) for e in entries) + "\n</help>"


def plain_text(entries: List[dict]) -> str:
    """The help shown verbatim when no model is used."""
    parts = []
    for e in entries:
        lines = [f"{e['title']} ({e['where']})", e["summary"]]
        lines.extend(f"{i}. {s}" for i, s in enumerate(e["steps"], 1))
        for p in e["problems"]:
            lines.append(f"If {p['symptom']}: " + " ".join(p["fixes"]))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# --- Account state -----------------------------------------------------------

def _rows(uid: str, table: str, columns: str, order: Optional[str] = None, limit: int = 50) -> List[dict]:
    """One scoped read. Missing tables or an unreachable database read as
    "nothing known", never as an error the user sees - a nudge is optional."""
    uid = ai_data._require_uid(uid)
    if not ai_data.is_database_configured():
        return []
    try:
        query = ai_data.get_supabase().table(table).select(columns).eq("user_id", uid)
        if order:
            query = query.order(order, desc=True)
        return query.limit(limit).execute().data or []
    except Exception as e:
        logger.warning(f"Help lookup on {table} for user {uid} failed: {e}")
        return []


def device_states(uid: str, now: Optional[datetime] = None) -> List[dict]:
    """The account's devices with ONLINE/OFFLINE derived the same way the
    Devices page derives it (no report for AGENT_STALE_SECONDS = offline).
    Names and last report only - never addresses."""
    now = now or datetime.now(timezone.utc)
    out = []
    for d in _rows(uid, "devices", "id,name,hostname,last_seen,created_at,is_backup_target"):
        seen = ai_data.parse_ts(d.get("last_seen"))
        online = seen is not None and (now - seen).total_seconds() <= AGENT_STALE_SECONDS
        out.append({"id": d["id"], "name": d.get("name") or d.get("hostname") or d["id"],
                    "status": "ONLINE" if online else "OFFLINE", "last_seen": d.get("last_seen"),
                    "created_at": d.get("created_at"),
                    "is_backup_target": bool(d.get("is_backup_target"))})
    return out


def account_facts(uid: str, now: Optional[datetime] = None) -> List[str]:
    """Plain facts about the account, for questions like "why isn't anything
    showing up" whose answer depends on what is (not) set up."""
    from src.ai_grounding import utc_label
    devices = device_states(uid, now)
    if not devices:
        return ["Your account (checked by NetSentinel just now): no devices are registered yet."]
    online = [d for d in devices if d["status"] == "ONLINE"]
    lines = [f"Your account (checked by NetSentinel just now): {len(devices)} device(s) registered, "
             f"{len(online)} ONLINE and {len(devices) - len(online)} OFFLINE."]
    for d in devices:
        last = utc_label(d["last_seen"]) or "never"
        lines.append(f"- {d['name']}: {d['status']} (last report {last})")
    targets = sum(1 for d in devices if d["is_backup_target"])
    lines.append(f"Devices tagged as backup targets: {targets}.")
    return lines


# --- Stuck-user nudges ---------------------------------------------------------

NEW_ACCOUNT_GRACE = timedelta(minutes=10)
REGISTRATION_GRACE = timedelta(minutes=2)


def _nudge(kind: str, message: str, help_id: str) -> dict:
    e = entry(help_id)
    # The entry's first step is the main fix ("start the agent again"); the
    # common failure points follow it.
    tips = (e["steps"][:1] + [f for p in e["problems"] for f in p["fixes"]])[:3]
    return {"kind": kind, "message": message, "help_id": help_id, "help_title": e["title"],
            "where": e["where"], "tips": tips}


def nudges(uid: str, now: Optional[datetime] = None) -> List[dict]:
    """At most one suggestion for an account that looks stuck. Each is a
    condition read from the account's own rows, paired with advice copied from
    the help entry for that situation - nothing here is written by a model."""
    now = now or datetime.now(timezone.utc)
    devices = device_states(uid, now)
    codes = _rows(uid, "enrollment_codes", "created_at,expires_at,used_at,used_by_hostname",
                  order="created_at", limit=5)
    latest = codes[0] if codes else None

    if latest:
        created = ai_data.parse_ts(latest.get("created_at"))
        used = ai_data.parse_ts(latest.get("used_at"))
        expires = ai_data.parse_ts(latest.get("expires_at"))
        # A device *created* after the code means the code did its job. Its
        # last report is no evidence: an existing device keeps reporting.
        registered_since = created is not None and any(
            (ai_data.parse_ts(d["created_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= created
            for d in devices)
        if used and not registered_since and now - used >= REGISTRATION_GRACE:
            host = latest.get("used_by_hostname")
            return [_nudge("linked_not_registered",
                           f"The agent on {host or 'your machine'} accepted its code, but the machine has not "
                           "appeared on your account yet.", "waiting-for-the-machine")]
        if not used and expires and expires <= now and not registered_since:
            return [_nudge("code_never_used",
                           "You started adding a machine, but its code was never entered into the agent "
                           "and has now expired.", "add-a-device")]

    offline = [d for d in devices if d["status"] == "OFFLINE"]
    if offline:
        names = ", ".join(d["name"] for d in offline[:3]) + (" and others" if len(offline) > 3 else "")
        return [_nudge("device_offline",
                       f"{names} {'is' if len(offline) == 1 else 'are'} OFFLINE: no agent report for more than "
                       "3 minutes.", "device-offline")]

    if not devices and not codes:
        profile = _rows(uid, "user_profiles", "first_seen_at", limit=1)
        first_seen = ai_data.parse_ts(profile[0].get("first_seen_at")) if profile else None
        if first_seen and now - first_seen >= NEW_ACCOUNT_GRACE:
            return [_nudge("no_devices",
                           "Your account has no machines yet, so there is nothing for NetSentinel to measure.",
                           "add-a-device")]
    return []
