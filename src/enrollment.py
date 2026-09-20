"""Enrolment codes: how a new machine gets an agent token without anyone
copying a long-lived credential around.

The flow:
    1. a signed-in user clicks "Add a device"; the site calls
       POST /api/devices/enroll-code and shows an 8-character code
    2. they run the agent on the new machine, which asks for the code
    3. the agent calls POST /api/agent/enroll with it and receives the
       account's agent token, which it stores locally

The code is the only thing that crosses between screen and keyboard, and it
is worthless fifteen minutes later.
"""
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.database import get_supabase, is_database_configured
from src.logger import logger

TABLE = "enrollment_codes"

# Crockford base32: excludes I, L, O and U. The first three are the classic
# transcription errors when a code is read off one screen and typed on another
# machine (often from a phone photo); U is excluded so a random code cannot
# spell something unfortunate. Crucially the excluded letters have unambiguous
# targets, which is what makes the forgiving normalisation below correct
# rather than guesswork: a typed O can only have meant 0.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

# What people type -> what they meant.
_CONFUSABLES = {"O": "0", "I": "1", "L": "1", "U": "V"}
CODE_TTL_SECONDS = 15 * 60

# 32^8 is ~1.1e12 codes, but only the handful currently live are valid, and a
# guess must land inside a 15-minute window. The rate limit below is what
# actually makes brute force hopeless; the entropy is the backstop.
_MAX_ATTEMPTS = 10
_ATTEMPT_WINDOW_S = 300
_attempts: dict = {}

_CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")


class EnrollmentError(Exception):
    """Redemption failed. The message is safe to show a user."""


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    """ABCD-EFGH — easier to read aloud and to type without losing your place."""
    mid = CODE_LENGTH // 2
    return f"{code[:mid]}-{code[mid:]}"


def normalise_code(raw: str) -> Optional[str]:
    """Accept what a human actually types: lower case, dashes, spaces.

    Returns None if it could not be a code at all, so a malformed attempt is
    rejected before it costs a database round-trip.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-_]", "", raw).upper()
    for typed, meant in _CONFUSABLES.items():
        cleaned = cleaned.replace(typed, meant)
    if not _CODE_RE.match(cleaned):
        return None
    return cleaned


def _rate_limited(client_key: str) -> bool:
    """Per-client attempt budget for redemption.

    Without this an 8-character code is guessable by a script; with it an
    attacker gets 10 tries per five minutes against codes that live for
    fifteen. Kept in memory deliberately — it needs to be cheap, and the
    consequence of it resetting on redeploy is ten extra guesses.
    """
    now = time.monotonic()
    hits = [t for t in _attempts.get(client_key, []) if now - t < _ATTEMPT_WINDOW_S]
    if len(hits) >= _MAX_ATTEMPTS:
        _attempts[client_key] = hits
        return True
    hits.append(now)
    _attempts[client_key] = hits
    if len(_attempts) > 5000:  # bound memory
        for key in list(_attempts)[:2500]:
            _attempts.pop(key, None)
    return False


def reset_rate_limit(client_key: Optional[str] = None):
    if client_key is None:
        _attempts.clear()
    else:
        _attempts.pop(client_key, None)


def create_code(user_id: str) -> dict:
    """Issues a fresh code for an account. Codes are additive: generating a
    new one does not invalidate an earlier unused one, so a user setting up
    three machines at once is not fighting their own clicks."""
    if not user_id:
        raise EnrollmentError("Sign in to add a device.")
    if not is_database_configured():
        raise EnrollmentError("No database configured; enrolment codes are unavailable.")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=CODE_TTL_SECONDS)
    code = generate_code()
    try:
        get_supabase().table(TABLE).insert({
            "code": code,
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"Could not create enrolment code for {user_id}: {e}")
        raise EnrollmentError("Could not create an enrolment code. Please try again.")

    return {
        "code": code,
        "formatted_code": format_code(code),
        "expires_at": expires.isoformat(),
        "expires_in_seconds": CODE_TTL_SECONDS,
    }


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def redeem_code(raw_code: str, client_key: str, hostname: Optional[str] = None) -> str:
    """Exchange a code for the owning account's agent token.

    Returns the token. Raises EnrollmentError with a message safe to show the
    person at the keyboard — deliberately the SAME message for "no such code",
    "expired" and "already used", so the endpoint cannot be used to discover
    which codes exist.
    """
    from src.auth import get_or_create_agent_token

    if _rate_limited(client_key):
        raise EnrollmentError("Too many attempts. Wait a few minutes and generate a new code.")

    generic = "That code is not valid. Codes expire after 15 minutes and can only be used once — generate a new one."

    code = normalise_code(raw_code)
    if not code:
        raise EnrollmentError(generic)
    if not is_database_configured():
        raise EnrollmentError("No database configured; enrolment codes are unavailable.")

    try:
        res = get_supabase().table(TABLE).select("*").eq("code", code).limit(1).execute()
    except Exception as e:
        logger.error(f"Enrolment lookup failed: {e}")
        raise EnrollmentError("Could not verify that code right now. Please try again.")

    rows = getattr(res, "data", None)
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if not row:
        raise EnrollmentError(generic)

    if row.get("used_at"):
        raise EnrollmentError(generic)

    expires = _parse_ts(row.get("expires_at"))
    if expires is None or datetime.now(timezone.utc) > expires:
        raise EnrollmentError(generic)

    user_id = row.get("user_id")
    if not user_id:
        raise EnrollmentError(generic)

    # Mark it spent BEFORE handing out the token: if the update fails we must
    # not have already given away a credential against a code we then leave
    # redeemable.
    try:
        get_supabase().table(TABLE).update({
            "used_at": datetime.now(timezone.utc).isoformat(),
            "used_by_hostname": (hostname or "")[:255] or None,
        }).eq("code", code).is_("used_at", "null").execute()
    except Exception as e:
        logger.error(f"Could not mark enrolment code used: {e}")
        raise EnrollmentError("Could not complete enrolment. Please generate a new code.")

    try:
        return get_or_create_agent_token(user_id)
    except Exception as e:
        logger.error(f"Could not issue agent token during enrolment: {e}")
        raise EnrollmentError("Could not issue an agent token. Please try again.")


def codes_are_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")
