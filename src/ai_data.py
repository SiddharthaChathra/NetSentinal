"""Everything the AI layer reads from the database, scoped to one account.

This module is the AI layer's tenant boundary. Every function takes the
caller's `user_id` - which the endpoints take from the validated session and
from nowhere else - as a required argument and filters on it in the query
itself. There is no un-scoped variant and no fallback to one: a database error
returns nothing, never everything, exactly as /api/devices and /api/incidents
behave.

What is selected is also deliberate. Devices are read as id/name/hostname
only. An IP address never enters an AI context, so a model asked for one has
nothing to quote and the grounding check catches any it invents.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.database import get_supabase, is_database_configured
from src.logger import logger

MAX_INCIDENTS = 500

_DEVICE_COLUMNS = "id,name,hostname"
_INCIDENT_COLUMNS = ("id,device_id,title,severity,status,likely_cause,confidence,"
                     "evidence,recommended_actions,started_at,acknowledged_at,resolved_at")


def _require_uid(uid: Optional[str]) -> str:
    if not uid:
        # Not a 401 - the gate already guarantees a principal. This is a
        # programming error, and it must never degrade to an un-scoped read.
        raise ValueError("an account id is required to read AI context")
    return uid


def fetch_devices(uid: str) -> Dict[str, dict]:
    """The account's devices by id, without address fields."""
    uid = _require_uid(uid)
    if not is_database_configured():
        return {}
    try:
        res = get_supabase().table("devices").select(_DEVICE_COLUMNS).eq("user_id", uid).execute()
        return {d["id"]: d for d in (res.data or [])}
    except Exception as e:
        logger.error(f"AI device query for user {uid} failed: {e}")
        return {}


def fetch_incidents(uid: str, ids: Optional[List[str]] = None, since: Optional[datetime] = None,
                    device_id: Optional[str] = None, statuses: Optional[List[str]] = None,
                    limit: int = MAX_INCIDENTS) -> List[dict]:
    """The account's incidents, newest first. Raises on a database error so a
    caller can tell "nothing recorded" from "could not look"."""
    uid = _require_uid(uid)
    if not is_database_configured():
        return []
    query = get_supabase().table("incidents").select(_INCIDENT_COLUMNS).eq("user_id", uid)
    if ids is not None:
        query = query.in_("id", list(ids))
    if since is not None:
        query = query.gte("started_at", since.isoformat())
    if device_id is not None:
        query = query.eq("device_id", device_id)
    if statuses is not None:
        query = query.in_("status", list(statuses))
    res = query.order("started_at", desc=True).limit(limit).execute()
    return res.data or []


def parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def normalize(incident: dict, devices: Dict[str, dict]) -> dict:
    """An incident row as a finding: the shape every AI context is built from."""
    device = devices.get(incident.get("device_id")) or {}
    return {
        "id": incident.get("id"),
        "target": device.get("name") or device.get("hostname") or "unknown device",
        "target_id": incident.get("device_id"),
        "severity": incident.get("severity"),
        "category": incident.get("title"),
        "message": incident.get("likely_cause"),
        "evidence": [e for e in (incident.get("evidence") or []) if e],
        "recommendation": [r for r in (incident.get("recommended_actions") or []) if r],
        "status": incident.get("status"),
        "started_at": incident.get("started_at"),
        "resolved_at": incident.get("resolved_at"),
    }


def findings_for(uid: str, **filters) -> List[dict]:
    devices = fetch_devices(uid)
    return [normalize(i, devices) for i in fetch_incidents(uid, **filters)]


# --- Retrieval for "ask NetSentinel" -----------------------------------------
#
# Plain keyword retrieval, deliberately: the question is matched against the
# account's own device names and stored findings, and only what matches is
# handed to the model. If nothing matches, the model is never called.

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 90
MAX_CONTEXT_FINDINGS = 25

TOPICS = {
    "dns": ("dns", "resolve", "resolving", "resolution", "domain", "domains", "nameserver"),
    "latency": ("latency", "slow", "slowness", "lag", "laggy", "delay", "ping", "sluggish"),
    "packet loss": ("loss", "dropping", "dropped", "drops", "unstable", "instability"),
    "gateway": ("gateway", "router"),
    "internet": ("internet", "upstream", "isp", "outage", "offline", "connectivity", "online"),
    "tcp": ("tcp", "port", "ports", "firewall", "proxy"),
    "interface": ("interface", "adapter", "wifi", "wi-fi", "ethernet", "cable", "nic"),
}
# Which words in a finding's text indicate each topic.
TOPIC_MARKERS = {
    "dns": ("dns",),
    "latency": ("latency",),
    "packet loss": ("packet loss", "loss"),
    "gateway": ("gateway",),
    "internet": ("internet", "upstream", "isp"),
    "tcp": ("tcp", "port", "firewall", "proxy"),
    "interface": ("interface",),
}

GENERIC = {
    "issue", "issues", "problem", "problems", "incident", "incidents", "error", "errors", "wrong",
    "happening", "happened", "happen", "status", "health", "healthy", "anything", "everything",
    "network", "summary", "summarize", "findings", "finding", "alerts", "alert", "recent", "latest",
    "going", "broken", "failing", "failures", "failure", "fine", "ok", "okay", "devices", "machines",
}

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "my", "our", "your", "of", "on", "in",
    "at", "to", "for", "with", "and", "or", "any", "there", "what", "whats", "what's", "which", "who",
    "when", "why", "how", "did", "do", "does", "has", "have", "had", "it", "its", "this", "that", "these",
    "those", "me", "i", "we", "you", "can", "could", "should", "would", "tell", "show", "give", "about",
    "from", "since", "last", "past", "today", "yesterday", "week", "weeks", "month", "months", "day",
    "days", "hour", "hours", "this", "please", "all", "been", "get", "got", "so", "far", "ago", "lately",
    "s", "much", "many", "often", "times", "again", "still", "now", "currently", "right", "up", "down",
}

TARGET_NOUNS = {"device", "target", "host", "machine", "server", "computer", "pc", "laptop", "node"}

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._'-]*[a-z0-9]|[a-z0-9]")


def parse_window_days(question: str) -> int:
    q = question.lower()
    m = re.search(r"(?:last|past)\s+(\d{1,3})\s+(hour|day|week|month)s?", q)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"hour": n / 24, "day": n, "week": n * 7, "month": n * 30}[unit]
        return max(1, min(MAX_WINDOW_DAYS, int(round(days)) or 1))
    if "today" in q or re.search(r"(?:last|past)\s+(?:24 hours|day)\b", q):
        return 1
    if "yesterday" in q:
        return 2
    if re.search(r"\b(?:this|last|past)\s+week\b", q):
        return 7
    if re.search(r"\b(?:this|last|past)\s+month\b", q):
        return 30
    return DEFAULT_WINDOW_DAYS


def _finding_text(f: dict) -> str:
    return " ".join([str(f.get("category") or ""), str(f.get("message") or ""),
                     *[str(e) for e in f.get("evidence") or []]]).lower()


def analyze_question(question: str, devices: Dict[str, dict]) -> dict:
    """Which of the account's devices, which topics, and which unrecognised
    identifiers the question mentions."""
    q = question.lower()
    tokens = [t.strip("'") for t in _TOKEN.findall(q)]
    token_set = set(tokens)

    matched = {}
    for dev_id, d in devices.items():
        for label in (d.get("name"), d.get("hostname"), dev_id):
            label = (label or "").strip().lower()
            if not label:
                continue
            # Whole-word match, so a device called "pc" does not match "pcs".
            if re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", q):
                matched[dev_id] = d
                break

    known_labels = {(l or "").lower() for d in devices.values()
                    for l in (d.get("name"), d.get("hostname"), d.get("id"))}
    known_words = {w for l in known_labels for w in _TOKEN.findall(l)}

    topics = {name for name, words in TOPICS.items()
              if any(w in token_set for w in words) or (name == "packet loss" and "packet loss" in q)}

    # Identifier-looking words (db-01, nas.lan, server_7) and the word after
    # "device"/"target" are candidate device names. Unrecognised ones are what
    # make "what happened to server-zeta?" answerable with "no such device"
    # rather than with everything on the account.
    candidates = [t for t in tokens if re.search(r"[0-9._-]", t)
                  and not re.fullmatch(r"\d+(?:\.\d+)?(?:h|d|ms|%)?", t)]
    for i, t in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if t in TARGET_NOUNS and nxt not in STOPWORDS and nxt not in GENERIC and nxt not in TARGET_NOUNS \
                and not any(nxt in words for words in TOPICS.values()):
            candidates.append(nxt)
    unknown = [c for c in dict.fromkeys(candidates)
               if c not in known_labels and c not in known_words]

    generic = bool(token_set & GENERIC)
    return {"devices": matched, "topics": topics, "unknown_identifiers": unknown, "generic": generic}


def retrieve(uid: str, question: str, now: Optional[datetime] = None) -> dict:
    """Find the account's findings that are relevant to `question`.

    Returns {"findings", "scope", "reason"}; `findings` empty with a `reason`
    means there is nothing to ground an answer in and the model must not be
    asked.
    """
    now = now or datetime.now(timezone.utc)
    days = parse_window_days(question)
    since = now - timedelta(days=days)
    devices = fetch_devices(uid)
    a = analyze_question(question, devices)
    scope = {"window_days": days, "devices": sorted(d.get("name") or d["id"] for d in a["devices"].values()),
             "topics": sorted(a["topics"])}

    incidents = fetch_incidents(uid, since=since)
    findings = [normalize(i, devices) for i in incidents]

    # An unrecognised identifier may still be a value quoted in a finding (a
    # gateway address, a failing domain) rather than a device name.
    text_hits = []
    if a["unknown_identifiers"]:
        text_hits = [f for f in findings if any(u in _finding_text(f) for u in a["unknown_identifiers"])]
        if not text_hits and not a["devices"]:
            return {"findings": [], "scope": scope, "reason": "unknown_target",
                    "unknown": a["unknown_identifiers"]}

    if text_hits and not a["devices"]:
        findings = text_hits
    if a["devices"]:
        findings = [f for f in findings if f["target_id"] in a["devices"]]
    if a["topics"]:
        findings = [f for f in findings
                    if any(m in _finding_text(f) for t in a["topics"] for m in TOPIC_MARKERS[t])]
    if not (a["devices"] or a["topics"] or a["generic"] or text_hits):
        return {"findings": [], "scope": scope, "reason": "no_relevant_terms"}
    if not findings:
        return {"findings": [], "scope": scope, "reason": "no_matching_findings"}
    return {"findings": findings[:MAX_CONTEXT_FINDINGS], "scope": scope, "reason": None,
            "total_matched": len(findings)}
