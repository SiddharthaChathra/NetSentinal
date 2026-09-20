"""Test-wide auth setup.

Two things every test in this suite now depends on:

1. **The app has no anonymous mode.** Since login became mandatory, a request
   with no session reaches no endpoint, so the default for every existing test
   is now "a signed-in account" rather than "a guest". TestClient therefore
   sends a session token unless a test deliberately drops it (see the
   `anon_client` fixture).

2. **Tests must never authenticate against the live Supabase project.** The
   repo has a real .env, so `is_database_configured()` was True during test
   runs and only the old guest path kept the suite off the network. That was
   luck, not design. Here the Supabase environment is cleared for the whole
   session and the one function that talks to the auth server is stubbed, so
   the gate is exercised end to end with no network and no writes to
   production.
"""
import os

import pytest
from fastapi.testclient import TestClient

# Import src.database FIRST: it calls load_dotenv() at import time, which would
# otherwise re-populate the very variables cleared below (load_dotenv does not
# override variables that are already set, but it happily fills in ones that
# have been deleted). Importing it here means the .env load happens before the
# scrub, not after it.
import src.database  # noqa: E402,F401

# Cleared so is_database_configured() is False for the whole run no matter what
# the developer's .env holds.
SUPABASE_ENV_VARS = (
    "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY",
    "SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
)


def scrub_supabase_env():
    for var in SUPABASE_ENV_VARS:
        os.environ.pop(var, None)


scrub_supabase_env()

# ...but the login gate stays ON, so the suite tests the shipped behaviour
# rather than the local-dev bypass.
os.environ["AUTH_REQUIRED"] = "1"

from src import session as _session  # noqa: E402  (must follow the env scrub)

TEST_TOKEN = "test-session-token"
TEST_USER_ID = "test-user-0001"
TEST_EMAIL = "tester@netsentinel.local"

# A second account, for asserting that one tenant cannot see another's data.
OTHER_TOKEN = "other-session-token"
OTHER_USER_ID = "test-user-0002"

_KNOWN_SESSIONS = {
    TEST_TOKEN: {"id": TEST_USER_ID, "email": TEST_EMAIL},
    OTHER_TOKEN: {"id": OTHER_USER_ID, "email": "other@netsentinel.local"},
}


def _stub_fetch_user(token):
    """Stands in for the Supabase auth round-trip. Unknown tokens are invalid,
    which is what an expired or forged one looks like in production."""
    user = _KNOWN_SESSIONS.get(token)
    if user is None:
        raise ValueError("invalid token")
    return user


@pytest.fixture(autouse=True)
def stub_session_auth(monkeypatch):
    monkeypatch.setattr(_session, "_fetch_user", _stub_fetch_user)
    _session.clear_session_cache()
    yield
    _session.clear_session_cache()


@pytest.fixture(autouse=True)
def reset_global_caches():
    """Drop every process-global cache between tests.

    src.database keeps a singleton client, and src.api caches the database
    probe and schema probe for 60 s. A test that patches the Supabase
    environment can leave a client built against its fake URL behind, which
    the next test then reuses — an order-dependent failure that was latent
    until signed-in code paths started running in the suite.
    """
    import src.api as _api
    import src.database as _database

    def _clear():
        # Re-scrubbed per test, not just once at import: agent/agent.py calls
        # load_dotenv() at *its* import time, so the first test that imports
        # the agent quietly refills these from the developer's .env and every
        # later test starts believing a database is configured.
        scrub_supabase_env()
        _database._supabase = None
        _database._access_mode = "unconfigured"
        _api._db_probe_cache.update(at=0.0, status="unconfigured")
        _api._schema_cache.update(at=0.0, result=None)
        _api._last_runs.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def authenticated_test_client(monkeypatch):
    """Makes every `TestClient(app)` in the suite a signed-in client.

    Done here rather than by editing 200-odd call sites: the tests are about
    diagnostics, reports and readiness, and "is there a session" is now simply
    ambient. Tests that care about the gate itself use `anon_client` or pass
    their own Authorization header, both of which still win over this default.
    """
    original_init = TestClient.__init__

    def init_with_session(self, app, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {TEST_TOKEN}")
        original_init(self, app, *args, headers=headers, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", init_with_session)


@pytest.fixture
def anon_client():
    """A client with no session at all — a brand-new visitor."""
    from src.api import app

    client = TestClient(app)
    client.headers.pop("Authorization", None)
    return client


@pytest.fixture
def other_client():
    """A client signed in as a different account."""
    from src.api import app

    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {OTHER_TOKEN}"
    return client
