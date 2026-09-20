"""Per-account profile: onboarding state and login bookkeeping.

The onboarding tour used to fire on the first visit to the pre-login
homepage. There is no pre-login homepage any more, so it fires on the first
successful sign-in or sign-up instead.

WHERE THE FLAG LIVES, AND WHY
    Per *account*, server-side (user_profiles.onboarding_completed_at) — not
    per browser. A user who takes the tour on their laptop and then signs in
    on their phone is not shown it a second time. localStorage is used only
    as a same-device cache to stop the tour flashing while the server answers;
    the server is authoritative, and clearing browser storage does not bring
    the tour back.

    The trade-off: the tour is genuinely once per account, so a colleague
    sharing an account never sees it. That is the documented behaviour, and
    `onboarding_version` is the escape hatch — bumping ONBOARDING_VERSION
    re-runs the tour for everyone whose stored version is older.

Every function here fails soft. The profile table is convenience data; if
migration 007 has not been applied yet, the app must still work — the user
just gets the tour treated as "not completed" rather than a 500.
"""
from datetime import datetime, timezone
from typing import Optional

from src.database import get_supabase, is_database_configured
from src.logger import logger

TABLE = "user_profiles"
ONBOARDING_VERSION = 1

_missing_table_logged = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unavailable(action: str, e: Exception):
    global _missing_table_logged
    msg = str(e)
    if "user_profiles" in msg and not _missing_table_logged:
        _missing_table_logged = True
        logger.warning(
            f"Could not {action}: the {TABLE} table is missing. "
            "Apply migrations/007_user_profiles.sql. Onboarding state will not persist until then."
        )
    else:
        logger.warning(f"Could not {action}: {e}")


def get_profile(user_id: str) -> Optional[dict]:
    if not user_id or not is_database_configured():
        return None
    try:
        res = get_supabase().table(TABLE).select("*").eq("user_id", user_id).limit(1).execute()
        return (res.data or [None])[0]
    except Exception as e:
        _unavailable("read user profile", e)
        return None


def onboarding_state(user_id: str, profile: Optional[dict] = None) -> dict:
    """{'completed': bool, 'should_show_tour': bool, 'version': int}

    `should_show_tour` is the single answer the frontend acts on, so the
    "when does the tour run" rule is decided in one place rather than
    re-implemented in the client.
    """
    if profile is None:
        profile = get_profile(user_id)

    if not profile:
        # No row yet == brand-new account (or the table is unavailable, in
        # which case showing the tour is the harmless direction to fail).
        return {"completed": False, "should_show_tour": True, "version": ONBOARDING_VERSION, "completed_at": None}

    completed_at = profile.get("onboarding_completed_at")
    seen_version = profile.get("onboarding_version") or 0
    completed = bool(completed_at) and seen_version >= ONBOARDING_VERSION
    return {
        "completed": completed,
        "should_show_tour": not completed,
        "version": ONBOARDING_VERSION,
        "completed_at": completed_at,
    }


def record_login(user_id: str) -> dict:
    """Called by /api/auth/session on a signed-in request. Creates the profile
    row on first sight — which is what makes "first successful login" a
    server-side fact rather than a client-side guess — and returns the
    onboarding state as it was *before* this login was recorded.
    """
    if not user_id or not is_database_configured():
        return onboarding_state(user_id, None)

    profile = get_profile(user_id)
    state = onboarding_state(user_id, profile)

    try:
        sb = get_supabase()
        if profile:
            sb.table(TABLE).update({
                "last_login_at": _now(),
                "login_count": (profile.get("login_count") or 0) + 1,
                "updated_at": _now(),
            }).eq("user_id", user_id).execute()
        else:
            sb.table(TABLE).insert({
                "user_id": user_id,
                "first_seen_at": _now(),
                "last_login_at": _now(),
                "login_count": 1,
                "onboarding_version": 0,  # 0 = never completed any tour version
            }).execute()
    except Exception as e:
        _unavailable("record login", e)

    return state


def complete_onboarding(user_id: str) -> dict:
    """Marks the tour as seen for this account, on every device."""
    if not user_id:
        return {"completed": False, "should_show_tour": False, "version": ONBOARDING_VERSION, "completed_at": None}
    if not is_database_configured():
        return {"completed": True, "should_show_tour": False, "version": ONBOARDING_VERSION, "completed_at": _now()}

    stamp = _now()
    try:
        get_supabase().table(TABLE).upsert({
            "user_id": user_id,
            "onboarding_completed_at": stamp,
            "onboarding_version": ONBOARDING_VERSION,
            "updated_at": stamp,
        }).execute()
    except Exception as e:
        _unavailable("save onboarding completion", e)
        return {"completed": False, "should_show_tour": True, "version": ONBOARDING_VERSION, "completed_at": None, "persisted": False}

    return {"completed": True, "should_show_tour": False, "version": ONBOARDING_VERSION, "completed_at": stamp, "persisted": True}


def reset_onboarding(user_id: str) -> dict:
    """Lets a user replay the tour from the UI."""
    if not user_id or not is_database_configured():
        return {"completed": False, "should_show_tour": True, "version": ONBOARDING_VERSION, "completed_at": None}
    try:
        get_supabase().table(TABLE).upsert({
            "user_id": user_id,
            "onboarding_completed_at": None,
            "onboarding_version": 0,
            "updated_at": _now(),
        }).execute()
    except Exception as e:
        _unavailable("reset onboarding", e)
    return {"completed": False, "should_show_tour": True, "version": ONBOARDING_VERSION, "completed_at": None}
