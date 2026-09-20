"""The login gate.

One middleware in front of every request, so access control cannot be
forgotten on a new endpoint: a route is reachable without a session only if
its path is on the public list below. Everything else answers 401 with a
machine-readable body before the handler ever runs.

Deliberately *not* a per-endpoint dependency: a dependency protects the
endpoints someone remembered to decorate, and the failure mode of forgetting
one is a silently public endpoint. Here the failure mode of forgetting is a
locked endpoint, which is loud and safe.
"""
import re
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.logger import logger
from src.session import Principal, auth_required, docs_exposed, resolve_access_token

# Reachable with no session at all.
#   /api/health        — the cold-start probe the frontend fires before
#                        anything else; gating it would make a booting backend
#                        indistinguishable from a rejected one.
#   /api/auth/session  — the "am I logged in" check. Must answer 200 with
#                        authenticated:false rather than 401, or the sign-in
#                        page cannot ask the question.
#   /api/auth/logout   — accepts an already-expired token and still succeeds,
#                        so signing out never strands a user on an error.
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/session",
    "/api/auth/logout",
})

# Agent endpoints authenticate with a per-user agent token (`nsa_…`), not a
# browser session, and enforce it themselves via verify_agent_token. The gate
# steps aside for them rather than demanding a second, different credential.
AGENT_PATH_RE = re.compile(r"^/api/agent(/|$)")

# The legacy static bundle in web/ is a shell with no data in it; the API it
# would call is gated. Left reachable so the container's root URL does not 404.
PUBLIC_PREFIXES = ("/static/",)

DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

# Public paths that still need to know WHO is calling. Everything else public
# is left alone: /api/health does not care, and agent endpoints carry an
# `nsa_` agent token which is not a session at all. Trying to resolve one as a
# session costs a round-trip to Supabase's auth server on every heartbeat and
# logs a validation failure indistinguishable from a real one.
SESSION_AWARE_PUBLIC_PATHS = frozenset({"/api/auth/session", "/api/auth/logout"})


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS or AGENT_PATH_RE.match(path):
        return True
    if path in DOCS_PATHS:
        return docs_exposed()
    if path == "/":
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _unauthenticated(detail: str, code: str) -> JSONResponse:
    """A 401 the frontend can act on without string-matching prose."""
    return JSONResponse(
        status_code=401,
        content={
            "detail": detail,
            "code": code,
            "authenticated": False,
            "login_url": "/auth",
        },
        headers={
            "WWW-Authenticate": 'Bearer realm="netsentinel"',
            "Cache-Control": "no-store",
        },
    )


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Rejects every unauthenticated request to a non-public path with 401.

    On success the resolved Principal is attached to request.state, so the
    endpoint dependencies reuse it instead of validating the same token twice.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # CORS preflight carries no Authorization header by definition.
        if request.method == "OPTIONS":
            return await call_next(request)

        if is_public_path(path):
            # Resolve a session only where the endpoint actually needs one.
            if path in SESSION_AWARE_PUBLIC_PATHS:
                token = _bearer_token(request)
                request.state.access_token = token
                request.state.principal = resolve_access_token(token) if token else None
            else:
                request.state.access_token = None
                request.state.principal = None
            return await call_next(request)

        if not auth_required():
            request.state.access_token = None
            request.state.principal = resolve_access_token(None)
            return await call_next(request)

        token = _bearer_token(request)
        if not token:
            return _unauthenticated("Sign in to access NetSentinel.", "not_authenticated")

        principal = resolve_access_token(token)
        if principal is None:
            logger.info(f"Rejected request to {path}: invalid or expired session")
            return _unauthenticated("Your session has expired. Please sign in again.", "session_expired")

        request.state.access_token = token
        request.state.principal = principal
        return await call_next(request)
