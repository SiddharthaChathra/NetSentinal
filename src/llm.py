"""The one door every AI feature goes through to reach a language model.

Feature code calls `call_llm(system_prompt, user_prompt)` and nothing else. It
never imports a provider SDK, never knows which provider is configured, and
never sees a provider-specific error: every failure - unreachable, timed out,
rate-limited, misconfigured, empty answer - surfaces as `LLMUnavailable`, which
the features turn into the rule engine's own output plus a short note.

Which provider answers is configuration, not code:

    LLM_PROVIDER=ollama    local development - http://localhost:11434
    LLM_PROVIDER=groq      production (Render) - Groq's free-tier API
    LLM_PROVIDER=disabled  no AI at all; every feature shows the raw findings

Unset, it is `groq` when GROQ_API_KEY is present and `ollama` otherwise, so a
laptop with Ollama running needs no configuration and a Render service needs
only the key. The environment is read on every call, never cached at import,
so switching provider is a config change and a restart - never a code change.

Both providers are reached over plain HTTPS with httpx (already a dependency)
rather than their SDKs: two small JSON calls do not justify two more packages,
and it keeps the request shape visible and testable.
"""
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from src.logger import logger

PROVIDERS = ("ollama", "groq", "disabled")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_GROQ_URL = "https://api.groq.com/openai/v1"
# Groq retires models (llama-3.1-8b-instant went in 2026); GROQ_MODEL
# overrides this without a deploy, and /api/ai/status?probe=1 reports a
# configured model that no longer exists.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"

OLLAMA_NUM_CTX = 8192

# An 8B model on a laptop CPU can take the better part of a minute for a
# paragraph; Groq answers in a second or two, so a long wait there means it
# is not going to answer at all.
DEFAULT_TIMEOUT_S = {"ollama": 120.0, "groq": 20.0}

# After a failure the provider is not asked again for this long, so an outage
# costs one slow request rather than one per page view. A 429 uses the
# provider's own Retry-After instead when it sends one.
FAILURE_COOLDOWN_S = 30.0
MAX_COOLDOWN_S = 300.0

# Tests swap this for an httpx.MockTransport; production leaves it None.
_transport: Optional[httpx.BaseTransport] = None

_breaker_lock = threading.Lock()
_breaker: dict = {}  # provider -> (open_until_monotonic, reason)


class LLMUnavailable(Exception):
    """The configured model cannot answer right now. Never fatal: callers fall
    back to the rule engine's structured output."""

    def __init__(self, reason: str, provider: Optional[str] = None,
                 rate_limited: bool = False, retry_after: Optional[float] = None):
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.rate_limited = rate_limited
        self.retry_after = retry_after


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: int


def provider_name() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return "groq" if os.environ.get("GROQ_API_KEY", "").strip() else "ollama"


def provider_config() -> dict:
    """What is configured, with no secrets - safe to return from an endpoint."""
    provider = provider_name()
    if provider == "ollama":
        return {"provider": provider,
                "model": os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
                "base_url": os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL).rstrip("/")}
    if provider == "groq":
        return {"provider": provider,
                "model": os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
                "base_url": os.environ.get("GROQ_BASE_URL", DEFAULT_GROQ_URL).rstrip("/"),
                "api_key_set": bool(os.environ.get("GROQ_API_KEY", "").strip())}
    return {"provider": provider, "model": None, "base_url": None}


def _timeout(provider: str) -> float:
    raw = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_S[provider]
    except ValueError:
        return DEFAULT_TIMEOUT_S[provider]


def _client(provider: str) -> httpx.Client:
    return httpx.Client(timeout=_timeout(provider), transport=_transport)


def reset_breaker():
    with _breaker_lock:
        _breaker.clear()


def _check_breaker(provider: str):
    with _breaker_lock:
        entry = _breaker.get(provider)
        if not entry:
            return
        open_until, reason = entry
        if time.monotonic() < open_until:
            raise LLMUnavailable(f"{reason} (retrying shortly)", provider=provider)
        _breaker.pop(provider, None)


def _trip_breaker(provider: str, err: LLMUnavailable):
    cooldown = err.retry_after if err.retry_after else FAILURE_COOLDOWN_S
    cooldown = max(1.0, min(cooldown, MAX_COOLDOWN_S))
    with _breaker_lock:
        _breaker[provider] = (time.monotonic() + cooldown, err.reason)


def _retry_after(response: httpx.Response) -> Optional[float]:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


def _call_ollama(cfg: dict, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "stream": False,
        # Temperature 0: these are summaries of fixed facts, and sampling
        # variety is exactly where invented detail comes from.
        # num_ctx: Ollama defaults to 4096 tokens and, on overflow, silently
        # drops the OLDEST tokens - the grounding system prompt. 25 retrieved
        # findings come close to that, so the window is set explicitly.
        "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": OLLAMA_NUM_CTX},
    }
    with _client("ollama") as client:
        r = client.post(f"{cfg['base_url']}/api/chat", json=body)
    if r.status_code == 404:
        raise LLMUnavailable(f"model {cfg['model']} is not pulled (run: ollama pull {cfg['model']})", "ollama")
    if r.status_code >= 400:
        raise LLMUnavailable(f"Ollama answered HTTP {r.status_code}", "ollama")
    return ((r.json().get("message") or {}).get("content") or "")


def _reasoning_options(model: str) -> dict:
    """Groq's current small models reason before answering. Left at their
    defaults they can spend the whole token budget thinking and return an
    empty answer; this keeps the thinking short and out of the content."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if model.startswith("qwen/"):
        return {"reasoning_format": "hidden"}
    return {}


def _call_groq(cfg: dict, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set", "groq")
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    body.update(_reasoning_options(cfg["model"]))
    with _client("groq") as client:
        r = client.post(f"{cfg['base_url']}/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 429:
        raise LLMUnavailable("Groq rate limit reached", "groq", rate_limited=True,
                             retry_after=_retry_after(r))
    if r.status_code in (401, 403):
        raise LLMUnavailable("Groq rejected the API key", "groq")
    if r.status_code >= 400:
        raise LLMUnavailable(f"Groq answered HTTP {r.status_code}", "groq",
                             retry_after=_retry_after(r))
    choices = r.json().get("choices") or [{}]
    return ((choices[0].get("message") or {}).get("content") or "")


_CALLERS = {"ollama": _call_ollama, "groq": _call_groq}


def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 600) -> LLMResponse:
    """Ask the configured model. Returns an LLMResponse or raises LLMUnavailable.

    Never raises anything else: a malformed body, a refused connection or a
    timeout are all "the model is unavailable" as far as a feature is concerned.
    """
    provider = provider_name()
    if provider == "disabled":
        raise LLMUnavailable("AI is disabled by configuration (LLM_PROVIDER=disabled)", provider)
    if provider not in _CALLERS:
        raise LLMUnavailable(f"unknown LLM_PROVIDER {provider!r}; expected one of {', '.join(PROVIDERS)}",
                             provider)

    _check_breaker(provider)
    cfg = provider_config()
    started = time.monotonic()
    try:
        try:
            text = _CALLERS[provider](cfg, system_prompt, user_prompt, max_tokens)
        except LLMUnavailable:
            raise
        except httpx.TimeoutException:
            raise LLMUnavailable(f"{provider} did not answer within {_timeout(provider):.0f}s", provider)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"{provider} is unreachable ({type(e).__name__})", provider)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
            raise LLMUnavailable(f"{provider} returned an unreadable response ({type(e).__name__})", provider)
        text = (text or "").strip()
        if not text:
            raise LLMUnavailable(f"{provider} returned an empty answer", provider)
    except LLMUnavailable as e:
        logger.warning(f"LLM unavailable: {e.reason}")
        _trip_breaker(provider, e)
        raise

    return LLMResponse(text=text, provider=provider, model=cfg["model"],
                       latency_ms=int((time.monotonic() - started) * 1000))


def probe() -> dict:
    """Is the configured provider answering? A cheap listing call, not a
    completion, so it costs no tokens against the free tier."""
    cfg = provider_config()
    provider = cfg["provider"]
    if provider not in _CALLERS:
        return {**cfg, "reachable": False, "detail": "no model provider configured"}
    try:
        with _client(provider) as client:
            if provider == "ollama":
                r = client.get(f"{cfg['base_url']}/api/tags", timeout=5.0)
                ok = r.status_code == 200
                models = [m.get("name") for m in (r.json().get("models") or [])] if ok else []
                if ok and cfg["model"] not in models and f"{cfg['model']}:latest" not in models:
                    return {**cfg, "reachable": False, "detail": f"model {cfg['model']} is not pulled"}
            else:
                key = os.environ.get("GROQ_API_KEY", "").strip()
                if not key:
                    return {**cfg, "reachable": False, "detail": "GROQ_API_KEY is not set"}
                r = client.get(f"{cfg['base_url']}/models", headers={"Authorization": f"Bearer {key}"},
                               timeout=5.0)
                ok = r.status_code == 200
                models = [m.get("id") for m in (r.json().get("data") or [])] if ok else []
                if ok and cfg["model"] not in models:
                    return {**cfg, "reachable": False,
                            "detail": f"model {cfg['model']} is not offered by Groq (set GROQ_MODEL)"}
        return {**cfg, "reachable": ok, "detail": None if ok else f"HTTP {r.status_code}"}
    except (httpx.HTTPError, ValueError) as e:
        return {**cfg, "reachable": False, "detail": f"unreachable ({type(e).__name__})"}
