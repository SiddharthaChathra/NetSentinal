"""The five AI features: incident summary, KB article, digest, "ask
NetSentinel", and trend narration.

Every feature has the same shape, and the order is the point:

1. Read the account's own findings (src.ai_data - scoped by user_id).
2. Compute everything deterministically: which findings, what is new or
   recurring, which patterns repeat. The model never decides any of that.
3. Render that result as plain text. This is the fallback, and it is a
   complete answer on its own.
4. Only then ask the model to *phrase* it, and check what comes back
   (src.ai_grounding.find_ungrounded) before showing it.

If the model is down, rate-limited, disabled or makes something up, the
caller gets step 3 with a one-line note. The response is a 200 either way;
an AI failure never becomes an error on screen.
"""
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src import ai_data
from src.ai_grounding import (
    INSUFFICIENT, SYSTEM_PROMPT, build_user_prompt, find_ungrounded, findings_block, tidy, utc_label,
)
from src.llm import LLMUnavailable, call_llm
from src.logger import logger

NOTE_UNAVAILABLE = ("AI summarization is temporarily unavailable. "
                    "Showing the diagnostic engine's findings directly.")
NOTE_REJECTED = ("The AI-written version was withheld because it mentioned details that are not in "
                 "your diagnostic data. Showing the diagnostic engine's findings directly.")
NOTE_LIMITED = ("You've reached the AI request limit for the moment. "
                "Showing the diagnostic engine's findings directly.")

MAX_SUMMARY_INCIDENTS = 20
MAX_QUESTION_CHARS = 500


class NotFound(LookupError):
    """A requested record does not exist *for this account*. Deliberately the
    same answer whether it exists for someone else or not at all."""


# --- Per-account request limit -----------------------------------------------
#
# Groq's free tier is shared by every account on the deployment. Without a
# per-account ceiling one user refreshing a page could spend the whole
# deployment's quota and degrade everyone else to the fallback.

_rate_lock = threading.Lock()
_recent_calls: Dict[str, List[float]] = defaultdict(list)


def _per_minute() -> int:
    try:
        return max(1, int(os.environ.get("AI_REQUESTS_PER_MINUTE", "12")))
    except ValueError:
        return 12


def _take_quota(uid: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        calls = [t for t in _recent_calls[uid] if now - t < 60.0]
        if len(calls) >= _per_minute():
            _recent_calls[uid] = calls
            return False
        calls.append(now)
        _recent_calls[uid] = calls
        return True


def reset_rate_limits():
    with _rate_lock:
        _recent_calls.clear()


# --- The one path to the model -----------------------------------------------

def _respond(feature: str, uid: str, facts: dict, fallback_text: str,
             context: Optional[str] = None, task: Optional[str] = None,
             skip_note: Optional[str] = None, max_tokens: int = 600) -> dict:
    """Ask the model to phrase `context`, or explain why the fallback is shown.

    `context=None` means there is nothing to phrase (no findings, nothing
    retrieved) - the model is not called, so it cannot fill an empty context
    with something plausible.
    """
    ai = {"used": False, "status": "skipped", "provider": None, "model": None, "note": skip_note}
    text = fallback_text

    if context is not None:
        if not _take_quota(uid):
            ai.update(status="rate_limited", note=NOTE_LIMITED)
        else:
            try:
                reply = call_llm(SYSTEM_PROMPT, build_user_prompt(context, task), max_tokens=max_tokens)
            except LLMUnavailable as e:
                ai.update(status="unavailable", provider=e.provider, note=NOTE_UNAVAILABLE)
            else:
                ai.update(provider=reply.provider, model=reply.model)
                reply.text = tidy(reply.text)
                problems = find_ungrounded(reply.text, context)
                if problems:
                    logger.warning(f"AI {feature} answer withheld as ungrounded: {problems}")
                    ai.update(status="rejected", note=NOTE_REJECTED, ungrounded=problems)
                else:
                    ai.update(used=True, status="ok", note=None)
                    text = reply.text

    return {"feature": feature, "text": text, "ai": ai, "facts": facts}


# --- Shared rendering --------------------------------------------------------

def _status_phrase(f: dict) -> str:
    status = (f.get("status") or "").upper()
    if status == "RESOLVED":
        when = utc_label(f.get("resolved_at"))
        return f"resolved {when}" if when else "resolved"
    if status == "ACKNOWLEDGED":
        return "acknowledged, not yet resolved"
    return "still open"


def _plain_finding(f: dict) -> str:
    parts = [f"{(f.get('severity') or 'unknown').capitalize()}: {f.get('category')} on {f.get('target')}"
             f" (first seen {utc_label(f.get('started_at')) or 'at an unknown time'}; {_status_phrase(f)})."]
    if f.get("message"):
        parts.append(str(f["message"]).rstrip(".") + ".")
    if f.get("evidence"):
        parts.append("Evidence: " + "; ".join(str(e).rstrip(".") for e in f["evidence"]) + ".")
    if f.get("recommendation"):
        parts.append("Recommended next steps: " + "; ".join(str(r).rstrip(".") for r in f["recommendation"]) + ".")
    return " ".join(parts)


# --- 1. Incident summary -----------------------------------------------------

def incident_summary(uid: str, incident_ids: List[str]) -> dict:
    ids = list(dict.fromkeys(i for i in incident_ids if i))
    if not ids:
        raise ValueError("incident_ids must contain at least one id")
    if len(ids) > MAX_SUMMARY_INCIDENTS:
        raise ValueError(f"at most {MAX_SUMMARY_INCIDENTS} incidents can be summarized at once")

    findings = ai_data.findings_for(uid, ids=ids)
    if {f["id"] for f in findings} != set(ids):
        raise NotFound("one or more incidents were not found")

    fallback = "\n\n".join(_plain_finding(f) for f in findings)
    task = ("Task: write a short support-ticket style summary of the findings above for a "
            "non-technical reader, in one or two paragraphs. Cover which device is affected, what "
            "was observed, how serious it is, whether it is still open, and the recommended next "
            "steps. Use only the findings. If no recommendation is recorded, say so rather than "
            "suggesting one. Do not add conclusions of your own - such as whether action is still "
            "needed, how serious the impact is, or what a term means - beyond what the findings state.")
    return _respond("incident_summary", uid, {"findings": findings}, fallback,
                    context=findings_block(findings), task=task)


# --- 2. Knowledge-base article -----------------------------------------------

NO_STEPS = "NetSentinel's diagnostic engine did not record resolution steps for this finding."


def _kb_markdown(title: str, problem: str, cause: str, evidence: List[str], steps: List[str]) -> str:
    lines = [f"# {title}", "", "## Problem", problem, "", "## Likely cause", cause or "Not recorded.", ""]
    if evidence:
        lines += ["## How NetSentinel detects it", *[f"- {e}" for e in evidence], ""]
    lines += ["## Resolution steps"]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)] if steps else [NO_STEPS]
    return "\n".join(lines)


def kb_article(uid: str, incident_id: str) -> dict:
    """Only the problem description is written by the model. The cause,
    detection evidence and resolution steps are the rule engine's own fields,
    inserted verbatim - so the steps a reader follows can never be invented."""
    findings = ai_data.findings_for(uid, ids=[incident_id])
    if not findings:
        raise NotFound("incident not found")
    f = findings[0]

    article = {
        "title": f["category"],
        "likely_cause": f["message"],
        "detection_evidence": f["evidence"],
        "resolution_steps": f["recommendation"],
        "source_incident_id": f["id"],
    }
    fallback_problem = f"NetSentinel's diagnostic engine reports: {f['category']}."
    # A reusable article is not about one device on one day, so neither goes in.
    generic = {**f, "target": "not applicable (reusable article)", "started_at": None,
               "resolved_at": None, "status": "not applicable"}
    task = ("Task: write two or three sentences for the 'Problem' section of a short knowledge-base "
            "article about the finding above: what is going wrong, in plain English, using only its "
            "category, message and evidence. Do not name a device or a date, do not add causes "
            "beyond the message, and do not include resolution steps; those are added separately.")
    result = _respond("kb_article", uid, {"article": article}, fallback_problem,
                      context=findings_block([generic]), task=task, max_tokens=300)

    article["problem"] = result["text"]
    article["markdown"] = _kb_markdown(article["title"], article["problem"], f["message"],
                                       f["evidence"], f["recommendation"])
    result["text"] = article["markdown"]
    return result


# --- 3. Daily / weekly digest ------------------------------------------------

PERIODS = {"daily": timedelta(hours=24), "weekly": timedelta(days=7)}
_WINDOW_LABEL = {"daily": "the last 24 hours", "weekly": "the last 7 days"}


def digest(uid: str, period: str = "daily", now: Optional[datetime] = None) -> dict:
    if period not in PERIODS:
        raise ValueError("period must be 'daily' or 'weekly'")
    now = now or datetime.now(timezone.utc)
    span = PERIODS[period]
    start, prev_start = now - span, now - 2 * span

    devices = ai_data.fetch_devices(uid)
    rows = {r["id"]: r for r in ai_data.fetch_incidents(uid, since=prev_start)}
    for r in ai_data.fetch_incidents(uid, statuses=["OPEN", "ACKNOWLEDGED"]):
        rows.setdefault(r["id"], r)
    findings = [ai_data.normalize(r, devices) for r in rows.values()]

    def started(f):
        return ai_data.parse_ts(f["started_at"])

    def resolved_in_window(f):
        ended = ai_data.parse_ts(f["resolved_at"])
        return (f["status"] or "").upper() == "RESOLVED" and ended is not None and ended >= start

    def key(f):
        return (f["target"], f["category"])

    current = [f for f in findings if started(f) and started(f) >= start]
    previous = [f for f in findings if started(f) and prev_start <= started(f) < start]
    cur_counts, prev_counts = Counter(map(key, current)), Counter(map(key, previous))
    severity_of = {key(f): f["severity"] for f in sorted(current, key=lambda f: started(f))}

    new = [{"target": t, "category": c, "severity": severity_of[(t, c)], "count": n}
           for (t, c), n in cur_counts.items() if (t, c) not in prev_counts]
    recurring = [{"target": t, "category": c, "count_this_period": n,
                  "count_previous_period": prev_counts.get((t, c), 0)}
                 for (t, c), n in cur_counts.items() if (t, c) in prev_counts or n >= 2]
    resolved = [{"target": f["target"], "category": f["category"], "resolved_at": f["resolved_at"]}
                for f in findings if resolved_in_window(f)]
    still_open = [{"target": f["target"], "category": f["category"], "severity": f["severity"],
                   "since": f["started_at"]}
                  for f in findings if (f["status"] or "").upper() in ("OPEN", "ACKNOWLEDGED")]
    severity_totals = Counter((f["severity"] or "unknown").lower() for f in current)

    facts = {
        "period": period, "window_start": start.isoformat(), "window_end": now.isoformat(),
        "total_this_period": len(current), "total_previous_period": len(previous),
        "by_severity": dict(severity_totals),
        "new": new, "recurring": recurring, "resolved": resolved, "still_open": still_open,
    }

    window = _WINDOW_LABEL[period]
    lines = [f"Period: {window} (from {utc_label(start)} to {utc_label(now)}).",
             f"Findings recorded in this period: {len(current)}. In the period before it: {len(previous)}."]
    if severity_totals:
        lines.append("By severity this period: " + ", ".join(f"{s} {n}" for s, n in sorted(severity_totals.items())) + ".")
    lines.append("New this period (not seen in the period before): " + (
        "; ".join(f"{n['category']} on {n['target']} ({n['severity']}, {n['count']} occurrence(s))" for n in new)
        or "none") + ".")
    lines.append("Recurring: " + (
        "; ".join(f"{r['category']} on {r['target']} ({r['count_this_period']} occurrence(s) this period, "
                  f"{r['count_previous_period']} in the period before)" for r in recurring) or "none") + ".")
    lines.append("Resolved during this period: " + (
        "; ".join(f"{r['category']} on {r['target']} at {utc_label(r['resolved_at'])}" for r in resolved)
        or "none") + ".")
    lines.append("Still open: " + (
        "; ".join(f"{o['category']} on {o['target']} ({o['severity']}, since {utc_label(o['since'])})"
                  for o in still_open) or "none") + ".")
    fallback = "\n".join(lines)

    if not current and not previous and not still_open:
        return _respond("digest", uid, facts,
                        f"No diagnostic findings were recorded in {window}, and nothing is open.")

    task = (f"Task: write a short digest (three to six sentences) of {window} from the facts above: "
            "what is new, what recurred, what was resolved and what is still open. Use only these "
            "facts. Do not explain causes or give advice.")
    return _respond("digest", uid, facts, fallback, context=findings_block([], preamble=lines), task=task)


# --- 4. Ask NetSentinel (retrieval, then answer) -------------------------------

_FIELD_REQUESTS = {
    "an IP address": (re.compile(r"\bip\b|\bip[- ]?address|\bipv[46]\b", re.I),
                      lambda ctx: bool(re.search(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", ctx))),
    "a MAC address": (re.compile(r"\bmac\b|\bmac[- ]?address|hardware address", re.I),
                      lambda ctx: bool(re.search(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", ctx))),
}


def _not_found_text(r: dict) -> str:
    days = r["scope"]["window_days"]
    if r["reason"] == "unknown_target":
        names = ", ".join(f"'{u}'" for u in r.get("unknown", []))
        return (f"I don't have any information about {names}. There is no device by that name on "
                f"your account, and none of your diagnostic findings mention it.")
    if r["reason"] == "no_relevant_terms":
        return ("I couldn't find anything in your diagnostic history that matches that question, so "
                "I can't answer it. I can answer questions about your devices' diagnostic findings, "
                "for example \"Any DNS problems this week?\" or \"What happened to <device name>?\"")
    where = f" on {', '.join(r['scope']['devices'])}" if r["scope"]["devices"] else ""
    about = f" about {', '.join(r['scope']['topics'])}" if r["scope"]["topics"] else ""
    return f"No diagnostic findings{about} were recorded{where} in the last {days} day(s)."


def _status_tally(findings: List[dict]) -> List[str]:
    groups: Dict[tuple, Counter] = defaultdict(Counter)
    for f in findings:
        status = (f.get("status") or "").upper()
        groups[(f["category"], f["target"])]["open" if status in ("OPEN", "ACKNOWLEDGED") else "resolved"] += 1
    lines = ["Current status of each problem (computed by NetSentinel):"]
    for (category, target), c in groups.items():
        state = "STILL OPEN" if c["open"] else "all resolved"
        lines.append(f"- {category} on {target}: {state} ({c['open']} open, {c['resolved']} resolved)")
    return lines


def ask(uid: str, question: str, now: Optional[datetime] = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("question must not be empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")

    r = ai_data.retrieve(uid, question, now=now)
    facts = {"question": question, "scope": r["scope"], "findings": r["findings"],
             "retrieval": r["reason"] or "matched"}
    if not r["findings"]:
        # Nothing to ground an answer in: say so, and never ask the model.
        return _respond("ask", uid, facts, _not_found_text(r))

    scope = r["scope"]
    preamble = [f"Retrieved for this question: findings from the last {scope['window_days']} day(s)"
                + (f", device(s): {', '.join(scope['devices'])}" if scope["devices"] else "")
                + (f", topic(s): {', '.join(scope['topics'])}" if scope["topics"] else "") + "."]
    if r.get("total_matched", 0) > len(r["findings"]):
        preamble.append(f"Showing the {len(r['findings'])} most recent of {r['total_matched']} matching findings.")
    # Status per problem, counted here rather than left to the model: given
    # five DNS findings with four resolved, a live model answered "the DNS
    # problems have been resolved" while one was still open.
    preamble.extend(_status_tally(r["findings"]))
    context = findings_block(r["findings"], preamble=preamble)
    fallback = "Here are the diagnostic findings that match your question:\n\n" + \
        "\n\n".join(_plain_finding(f) for f in r["findings"])

    # A question for a value the findings do not carry is answered here, not
    # by the model: it is the classic bait, and the honest answer is fixed.
    for label, (asks_for, present) in _FIELD_REQUESTS.items():
        if asks_for.search(question) and not present(context):
            covered = "; ".join(dict.fromkeys(f"{f['category']} on {f['target']}" for f in r["findings"]))
            text = (f"{INSUFFICIENT} The diagnostic findings I have don't include {label}. "
                    f"They cover: {covered}.")
            return _respond("ask", uid, facts, text,
                            skip_note=f"The diagnostic data does not record {label}.")

    task = (f"Question from the user: {question}\n\n"
            f"Answer the question using only the findings above. If they do not contain the answer, "
            f"say \"{INSUFFICIENT}\"")
    return _respond("ask", uid, facts, fallback, context=context, task=task)


# --- 5. Trend narration --------------------------------------------------------

_TIME_BLOCKS = [(0, 6, "between 00:00 and 06:00 UTC"), (6, 12, "between 06:00 and 12:00 UTC"),
                (12, 18, "between 12:00 and 18:00 UTC"), (18, 24, "between 18:00 and 24:00 UTC")]
MIN_FOR_TIME_PATTERN = 3
TIME_PATTERN_SHARE = 0.6


def detect_patterns(findings: List[dict], window_start: datetime, window_end: datetime,
                    target: str, window_days: int) -> List[dict]:
    """Repeated patterns in one device's findings, found by counting - the
    model is never asked to spot one."""
    midpoint = window_start + (window_end - window_start) / 2
    by_category = defaultdict(list)
    for f in findings:
        ts = ai_data.parse_ts(f["started_at"])
        if ts:
            by_category[f["category"]].append((ts, f))

    patterns = []
    for category, items in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda x: x[0])
        n = len(items)
        if n < 2:
            continue
        times = [t for t, _ in items]
        patterns.append({
            "type": "recurring", "category": category, "count": n,
            "first": times[0].isoformat(), "last": times[-1].isoformat(),
            "statement": (f"'{category}' occurred {n} times on {target} in the last {window_days} days "
                          f"(first {utc_label(times[0])}, most recent {utc_label(times[-1])})."),
        })

        if n >= MIN_FOR_TIME_PATTERN:
            blocks = Counter(next(label for lo, hi, label in _TIME_BLOCKS if lo <= t.hour < hi) for t in times)
            label, hits = blocks.most_common(1)[0]
            if hits / n >= TIME_PATTERN_SHARE:
                pct = round(100 * hits / n)
                patterns.append({
                    "type": "time_of_day", "category": category, "block": label, "count": hits,
                    "of": n, "share_pct": pct,
                    "statement": f"{hits} of those {n} occurrences ({pct}%) started {label}.",
                })

        if n >= 4:
            early = sum(1 for t in times if t < midpoint)
            late = n - early
            if late >= 2 * max(early, 1) and late > early:
                direction = "more frequent"
            elif early >= 2 * max(late, 1) and early > late:
                direction = "less frequent"
            else:
                direction = None
            if direction:
                patterns.append({
                    "type": "frequency", "category": category, "direction": direction,
                    "first_half": early, "second_half": late,
                    "statement": (f"It became {direction} over the window: {early} occurrence(s) in the "
                                  f"first half and {late} in the second half."),
                })

        durations = []
        for t, f in items:
            ended = ai_data.parse_ts(f.get("resolved_at"))
            if ended and ended > t:
                durations.append((ended - t).total_seconds() / 60)
        if len(durations) >= 2:
            avg = round(sum(durations) / len(durations))
            patterns.append({
                "type": "duration", "category": category, "average_minutes": avg, "resolved": len(durations),
                "statement": f"The {len(durations)} resolved occurrences lasted {avg} minutes on average.",
            })

        still_open = sum(1 for _, f in items if (f.get("status") or "").upper() != "RESOLVED")
        if still_open:
            patterns.append({
                "type": "open", "category": category, "count": still_open,
                "statement": f"{still_open} occurrence(s) of '{category}' are not resolved yet.",
            })
    return patterns


def trends(uid: str, device_id: str, days: int = 30, now: Optional[datetime] = None) -> dict:
    if not 1 <= days <= ai_data.MAX_WINDOW_DAYS:
        raise ValueError(f"days must be between 1 and {ai_data.MAX_WINDOW_DAYS}")
    devices = ai_data.fetch_devices(uid)
    if device_id not in devices:
        raise NotFound("device not found")
    device = devices[device_id]
    target = device.get("name") or device.get("hostname") or device_id

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    findings = [ai_data.normalize(r, devices)
                for r in ai_data.fetch_incidents(uid, since=start, device_id=device_id)]
    patterns = detect_patterns(findings, start, now, target, days)
    facts = {"device": {"id": device_id, "name": target}, "window_days": days,
             "total_findings": len(findings), "patterns": patterns}

    if not findings:
        return _respond("trends", uid, facts,
                        f"No diagnostic findings were recorded for {target} in the last {days} days.")
    if not patterns:
        return _respond("trends", uid, facts,
                        f"No repeated pattern was found for {target} in the last {days} days: "
                        f"each of its {len(findings)} finding(s) occurred only once.")

    statements = [p["statement"] for p in patterns]
    task = ("Task: rephrase the detected patterns above as one short paragraph for a non-technical "
            "reader. They were detected by NetSentinel's own analysis. Do not add patterns, causes, "
            "predictions or advice, and keep every number and time exactly as given, including the "
            "word UTC after every time.")
    context = findings_block([], preamble=[f"Device: {target}. Window: the last {days} days.",
                                           "Detected patterns:", *[f"- {s}" for s in statements]])
    return _respond("trends", uid, facts, " ".join(statements), context=context, task=task)
