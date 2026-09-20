"""Session resolution for the login-gated application.

Every request that is not explicitly public must now carry a valid Supabase
access token. Validating one means a round-trip to Supabase's auth server,
which is far too expensive to do on every single call now that *nothing* is
reachable without a session — so tokens are validated once and cached for a
short TTL, and logout evicts the entry immediately.

The tenancy rule is defined here in one place: a principal's `user_id` is the
only scope key the rest of the backend is allowed to filter on.
"""
import os
import time
from typing import Optional

from src.database import get_supabase, is_database_configured
from src.logger import logger

# A validated token is trusted for this long before it is re-checked with
# Supabase. Kept short so a revoked session dies quickly; logout evicts the
# entry outright, so the window only matters for tokens revoked elsewhere
# (e.g. a password change from another device).
SESSION_CACHE_TTL_S = 30
# Failed validations are cached too, so a client looping on a dead token
# cannot turn into a stampede against Supabase.
SESSION_NEGATIVE_TTL_S = 15
_MAX_CACHED_SESSIONS = 2000

# token -> (principal | None, cached_at)
_session_cache: dict = {}

LOCAL_DEV_USER = {"id": "local-dev-user", "role": "admin", "email": "local-dev@netsentinel.local"}


class Principal:
    """Who a request is acting as, and the scope its data must be filtered to.

    `user_id` doubles as the tenant/org id: NetSentinel accounts are
    single-tenant today (one account == one org), so every row is scoped by
    `user_id` and `org_id` is an alias rather than a second column. Anything
    that later introduces shared orgs changes `org_id` here and nothing else.
    """

    __slots__ = ("user_id", "email", "user", "source")

    def __init__(self, user_id: str, email: Optional[str] = None, user=None, source: str = "session"):
        self.user_id = user_id
        self.email = email
        self.user = user if user is not None else {"id": user_id, "email": email}
        self.source = source  # "session" | "local-dev"

    @property
    def org_id(self) -> str:
        return self.user_id

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Principal(user_id={self.user_id!r}, source={self.source!r})"


def auth_required() -> bool:
    """Is the login gate active?

    On by default wherever a database is configured — i.e. always in
    production. It can be forced on with AUTH_REQUIRED=1 (useful for tests and
    for catching a misconfigured deploy), and is off only for a local checkout
    with no Supabase credentials, where there is no user directory to
    authenticate against at all.
    """
    forced = os.environ.get("AUTH_REQUIRED", "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    return is_database_configured()


def docs_exposed() -> bool:
    """Whether /docs, /redoc and /openapi.json bypass the gate.

    Off whenever the gate is on: the schema is a route like any other, and
    Swagger UI cannot send a bearer token from the browser anyway. Set
    EXPOSE_API_DOCS=1 to put them back on the public list.
    """
    if os.environ.get("EXPOSE_API_DOCS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return not auth_required()


def _attr(obj, name):
    """Supabase's client has returned both objects and plain dicts across
    versions; read either without caring which."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _cache_get(token: str):
    entry = _session_cache.get(token)
    if not entry:
        return (False, None)
    principal, cached_at = entry
    ttl = SESSION_CACHE_TTL_S if principal else SESSION_NEGATIVE_TTL_S
    if time.monotonic() - cached_at >= ttl:
        _session_cache.pop(token, None)
        return (False, None)
    return (True, principal)


def _cache_put(token: str, principal: Optional[Principal]):
    if len(_session_cache) >= _MAX_CACHED_SESSIONS:
        # Cheap bounded eviction: drop the oldest half rather than tracking an
        # LRU for what is a short-lived cache anyway.
        for stale in sorted(_session_cache, key=lambda k: _session_cache[k][1])[: _MAX_CACHED_SESSIONS // 2]:
            _session_cache.pop(stale, None)
    _session_cache[token] = (principal, time.monotonic())


def clear_session_cache(token: Optional[str] = None):
    """Evict one token (on logout) or the whole cache (on rotation/tests)."""
    if token is None:
        _session_cache.clear()
    else:
        _session_cache.pop(token, None)


def looks_like_jwt(token: str) -> bool:
    """Could this token even be a Supabase access token?

    A JWT is exactly three dot-separated, non-empty segments. Anything else -
    an `nsa_` agent token, a copied-wrong string, junk from a scanner - cannot
    be one, so it is rejected here instead of paying a round-trip to the auth
    server to be told the same thing. That round-trip was happening on every
    agent heartbeat.
    """
    if not token:
        return False
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def _fetch_user(token: str):
    """The one place that actually asks Supabase who a token belongs to.

    Isolated so the caching, gating and scoping logic around it can be tested
    without a network — and without a test run ever authenticating against the
    live project.
    """
    response = get_supabase().auth.get_user(token)
    return getattr(response, "user", None) if response else None


def resolve_access_token(token: Optional[str]) -> Optional[Principal]:
    """Validate a Supabase access token. Returns a Principal or None.

    Never raises: an unreachable auth server is a failed validation, which the
    caller turns into a 401. Failing closed is the right call for a gate —
    the alternative would let a Supabase outage open the whole app.
    """
    if not auth_required():
        return Principal(LOCAL_DEV_USER["id"], LOCAL_DEV_USER["email"], LOCAL_DEV_USER, source="local-dev")

    if not token or not token.strip():
        return None

    token = token.strip()
    # Cheap structural rejection first: no cache entry, no network, no log
    # line that reads like a failed sign-in.
    if not looks_like_jwt(token):
        return None

    hit, cached = _cache_get(token)
    if hit:
        return cached

    principal = None
    try:
        user = _fetch_user(token)
        uid = _attr(user, "id")
        if uid:
            principal = Principal(str(uid), _attr(user, "email"), user)
    except Exception as e:
        logger.warning(f"Session validation failed: {e}")
        principal = None

    _cache_put(token, principal)
    return principal


def revoke_session(token: Optional[str]) -> dict:
    """Server-side logout.

    Supabase access tokens are stateless JWTs — they cannot be individually
    un-signed before they expire. What *can* be revoked is the session behind
    them: `auth.admin.sign_out(jwt)` invalidates the refresh token, so the
    token cannot be renewed and dies at its (1 hour) expiry. Combined with
    evicting our validation cache, the session stops being usable here
    immediately and stops being renewable at Supabase.

    Returns a small report so the endpoint can tell the caller what actually
    happened rather than claiming success unconditionally.
    """
    result = {"cache_cleared": False, "revoked": False, "detail": None}
    if token:
        clear_session_cache(token)
        result["cache_cleared"] = True

    if not token or not is_database_configured():
        result["detail"] = "no server-side session to revoke"
        return result

    client = get_supabase()
    for attempt in (
        lambda: client.auth.admin.sign_out(token),
        lambda: client.auth.admin.sign_out(token, "global"),
    ):
        try:
            attempt()
            result["revoked"] = True
            result["detail"] = "refresh token revoked at Supabase"
            return result
        except TypeError:
            continue
        except Exception as e:
            result["detail"] = f"revocation call failed: {e}"
            logger.warning(f"Session revocation failed: {e}")
            return result

    result["detail"] = "revocation not supported by this Supabase client"
    return result
