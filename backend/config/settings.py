"""Deterministic backend configuration.

The Power Automate gateway URL is a secret (it typically embeds a SAS
signature). Per docs/AGENT_CONTRACT.md #13 and
docs/TEAMS_TOOL_CONTRACT.md #1:

  - it is never hardcoded anywhere in this codebase
  - it is resolved from environment/config only, at call time
  - it is never logged, and never returned from any function that could
    end up in an agent prompt, a tool result, or a frontend-facing payload

Resolution order for the gateway URL:
  1. `SLOPANOC_POWER_AUTOMATE_GATEWAY_URL` env var -- local development.
  2. `SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE` env var, naming a GCP Secret
     Manager secret version resource (e.g.
     "projects/<project>/secrets/<name>/versions/latest") -- deployment.

Callers must not `print`/`log` the return value of
`resolve_power_automate_gateway_url()`, or any exception raised while
resolving it.

SESSION PERSISTENCE (Phase 4C, backend/api/session_service.py):
`SLOPANOC_SESSION_BACKEND` selects the ADK `BaseSessionService`
implementation the API's session-service factory constructs -- "memory"
(explicit opt-in, non-persistent, same-process only -- what the automated
test suite uses throughout) or "database" (the default -- ADK's own
`DatabaseSessionService`, persistent, restart-safe). `resolve_database_url`
mirrors `resolve_power_automate_gateway_url`'s exact resolution order and
Secret-Manager fallback for the same reason: a production PostgreSQL/Cloud
SQL connection string is itself a credential-bearing secret. It is never
hardcoded, never logged, and never returned from anything that could reach
a frontend response, an agent prompt, or a log line.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Mapping, Optional

_GATEWAY_URL_ENV_VAR = "SLOPANOC_POWER_AUTOMATE_GATEWAY_URL"
_GATEWAY_SECRET_ENV_VAR = "SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE"
_MODEL_ENV_VAR = "SLOPANOC_MODEL"
_TIMEOUT_ENV_VAR = "SLOPANOC_POWER_AUTOMATE_TIMEOUT_SECONDS"
_ACTION_PROPOSAL_EXPIRY_ENV_VAR = "SLOPANOC_ACTION_PROPOSAL_EXPIRY_SECONDS"
_SESSION_BACKEND_ENV_VAR = "SLOPANOC_SESSION_BACKEND"
_CASE_CONTEXT_MAX_ITEMS_ENV_VAR = "SLOPANOC_CASE_CONTEXT_MAX_ITEMS"
_CASE_CONTEXT_MAX_CHARACTERS_ENV_VAR = "SLOPANOC_CASE_CONTEXT_MAX_CHARACTERS"
_DATABASE_URL_ENV_VAR = "SLOPANOC_DATABASE_URL"
_DATABASE_SECRET_ENV_VAR = "SLOPANOC_DATABASE_SECRET_RESOURCE"
_KNOWLEDGE_DATABASE_URL_ENV_VAR = "SLOPANOC_KNOWLEDGE_DATABASE_URL"
_KNOWLEDGE_DATABASE_SECRET_ENV_VAR = "SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE"
_MODEL_WARMUP_ENABLED_ENV_VAR = "SLOPANOC_MODEL_WARMUP_ENABLED"
_MODEL_WARMUP_TIMEOUT_ENV_VAR = "SLOPANOC_MODEL_WARMUP_TIMEOUT_SECONDS"
_CHAT_ATTACHMENTS_BUCKET_ENV_VAR = "SLOPANOC_CHAT_ATTACHMENTS_BUCKET"

# Matches google.adk.agents.llm_agent.LlmAgent.DEFAULT_MODEL in the
# installed ADK (1.33.0) -- not an independently invented default.
_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_TIMEOUT_SECONDS = 10.0
# Deterministic Teams write-action approval framework (backend/approval/):
# how long a created ActionProposal stays approvable before the policy
# gate must treat it as expired, per instruction.
_DEFAULT_ACTION_PROPOSAL_EXPIRY_SECONDS = 600.0

# P4COLD (model warm-up at startup): enabled by default -- the live-measured
# cost of NOT warming up (a first real user request paying ~5-22s of cold
# Vertex/Gemini client init instead of the process's own startup window) is
# materially worse for the product experience than the small, one-time,
# fixed cost of one minimal warm-up request per process start, and nothing
# about this application's existing test/development architecture requires
# it to be off by default (tests disable it explicitly via config/dependency
# injection -- see model_warmup.py's own module docstring -- never by the
# production code special-casing pytest).
_DEFAULT_MODEL_WARMUP_ENABLED = True
# Live evidence (P4COLD investigation): cold Vertex/Gemini calls observed
# ranging ~5s-~22s. A short (e.g. 2-5s) timeout would routinely abort a
# genuinely-cold real warm-up attempt before it could ever complete,
# defeating the whole point of this pass -- this default is comfortably
# above the worst observed cold duration while still bounding startup.
_DEFAULT_MODEL_WARMUP_TIMEOUT_SECONDS = 30.0

# "database" (persistent, ADK DatabaseSessionService) is the default --
# instruction: "the default local-development path should preferably use
# SQLite persistence rather than in-memory". "memory" is an explicit,
# named opt-out (InMemorySessionService) -- used throughout this backend's
# own automated test suite for speed/isolation, never the runtime default.
_DEFAULT_SESSION_BACKEND = "database"
_VALID_SESSION_BACKENDS = frozenset({"memory", "database"})
# `aiosqlite` (async SQLite DBAPI for SQLAlchemy) is a verified installed
# dependency of this environment -- a local `.db` file next to wherever
# the process is run from, requiring zero setup for local development.
_DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./slopanoc_sessions.db"

# Phase 5.1J (first Generic KM reference consumer): a SEPARATE local
# SQLite database from the ADK session store above -- the Generic
# Knowledge Management repository (backend/knowledge/repository/) has its
# own table set and its own lifecycle, unrelated to session persistence.
# Mirrors `_DEFAULT_DATABASE_URL`'s exact "local file, zero setup"
# reasoning; a genuinely dedicated setting, not reused ad hoc, because a
# production deployment may reasonably want to scale/replace governed
# knowledge storage independently of chat session storage.
#
# POST-5.1 A2: `resolve_knowledge_database_url()` now supports the same
# `*_SECRET_RESOURCE` Secret-Manager fallback `resolve_database_url()`
# already has -- a production deployment can point Knowledge at the same
# Cloud SQL PostgreSQL database as sessions/cases via its own explicit
# secret resource, without the two configuration domains ever silently
# reusing one another's setting.
_DEFAULT_KNOWLEDGE_DATABASE_URL = "sqlite+aiosqlite:///./slopanoc_knowledge.db"

# Phase 4D (backend/cases/snapshot.py): the model-facing
# `CaseContextSnapshot` is always bounded -- never the full ledger. These
# are deliberately modest defaults; the point is a fixed, configurable
# budget existing at all (instruction: "Use settings rather than
# scattering fixed values through prompts."), not a specific number.
_DEFAULT_CASE_CONTEXT_MAX_ITEMS = 12
_DEFAULT_CASE_CONTEXT_MAX_CHARACTERS = 4000


class ConfigurationError(RuntimeError):
    """Required backend configuration is missing.

    Never include a resolved secret value in this error's message -- at
    the point this is raised, no value has been resolved yet, so there is
    nothing to leak by construction. Keep it that way in any future edit.
    """


class Settings:
    """Deterministic, non-caching-of-secrets configuration accessor.

    `env` defaults to the live `os.environ` mapping (not a snapshot), so
    that tests can use `monkeypatch.setenv`/`delenv` against a
    module-level cached `Settings` instance (see `get_settings`) and have
    it observed immediately.
    """

    def __init__(self, env: Optional[Mapping[str, str]] = None) -> None:
        self._env: Mapping[str, str] = env if env is not None else os.environ

    @property
    def gemini_model(self) -> str:
        return self._env.get(_MODEL_ENV_VAR, _DEFAULT_MODEL)

    @property
    def request_timeout_seconds(self) -> float:
        raw = self._env.get(_TIMEOUT_ENV_VAR)
        return float(raw) if raw else _DEFAULT_TIMEOUT_SECONDS

    @property
    def action_proposal_expiry_seconds(self) -> float:
        raw = self._env.get(_ACTION_PROPOSAL_EXPIRY_ENV_VAR)
        return float(raw) if raw else _DEFAULT_ACTION_PROPOSAL_EXPIRY_SECONDS

    @property
    def session_backend(self) -> str:
        """"memory" or "database" -- see this module's docstring."""
        raw = self._env.get(_SESSION_BACKEND_ENV_VAR, _DEFAULT_SESSION_BACKEND).strip().lower()
        if raw not in _VALID_SESSION_BACKENDS:
            raise ConfigurationError(
                f"{_SESSION_BACKEND_ENV_VAR}={raw!r} is not a supported session "
                f"backend (expected one of {sorted(_VALID_SESSION_BACKENDS)})."
            )
        return raw

    @property
    def model_warmup_enabled(self) -> bool:
        """P4COLD switch -- `SLOPANOC_MODEL_WARMUP_ENABLED` (default
        enabled). Accepts the same permissive boolean vocabulary already
        used elsewhere in this codebase's own env-var parsing (`"1"`/
        `"true"`/`"yes"`/`"on"`, case-insensitive) rather than inventing a
        stricter one.
        """
        raw = self._env.get(_MODEL_WARMUP_ENABLED_ENV_VAR)
        if raw is None:
            return _DEFAULT_MODEL_WARMUP_ENABLED
        return raw.strip().lower() in ("1", "true", "yes", "on")

    @property
    def model_warmup_timeout_seconds(self) -> float:
        """P4COLD -- `SLOPANOC_MODEL_WARMUP_TIMEOUT_SECONDS` (default
        `_DEFAULT_MODEL_WARMUP_TIMEOUT_SECONDS`, see this module's own
        comment above for why). Validated positive, mirroring `session_
        backend`'s own "reject an invalid configured value at read time,
        never silently substitute a default for a value that WAS set but
        is nonsensical" discipline.
        """
        raw = self._env.get(_MODEL_WARMUP_TIMEOUT_ENV_VAR)
        if not raw:
            return _DEFAULT_MODEL_WARMUP_TIMEOUT_SECONDS
        try:
            value = float(raw)
        except ValueError:
            raise ConfigurationError(
                f"{_MODEL_WARMUP_TIMEOUT_ENV_VAR}={raw!r} is not a valid number of seconds."
            ) from None
        if value <= 0:
            raise ConfigurationError(f"{_MODEL_WARMUP_TIMEOUT_ENV_VAR} must be a positive number of seconds.")
        return value

    def resolve_database_url(self) -> str:
        """Resolve the session-persistence database URL (a SQLAlchemy async
        URL, e.g. `sqlite+aiosqlite:///...` or `postgresql+asyncpg://...`).
        Never log the result -- a production URL embeds credentials, exactly
        like the Power Automate gateway URL (see `resolve_power_automate_gateway_url`,
        whose resolution order/reasoning this mirrors).
        """
        direct = self._env.get(_DATABASE_URL_ENV_VAR)
        if direct:
            return direct

        secret_resource = self._env.get(_DATABASE_SECRET_ENV_VAR)
        if secret_resource:
            return _fetch_secret_from_secret_manager(secret_resource)

        return _DEFAULT_DATABASE_URL

    def resolve_knowledge_database_url(self) -> str:
        """Resolve the Generic Knowledge Management repository's own
        database URL (Phase 5.1J, extended POST-5.1 A2). Mirrors
        `resolve_database_url()`'s exact resolution order and
        Secret-Manager fallback -- a production PostgreSQL/Cloud SQL
        connection string is itself a credential-bearing secret, exactly
        like the session/Case database URL. Never log the result.

        Resolution order:
          1. `SLOPANOC_KNOWLEDGE_DATABASE_URL` -- local development / tests.
          2. `SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE` -- a GCP Secret
             Manager secret version resource, for deployment.
          3. The local-file SQLite default.

        Deliberately a SEPARATE setting from `resolve_database_url()`,
        never falling back to it -- Knowledge and sessions/Case remain two
        explicit configuration domains (this module's own docstring,
        "Keep the two configuration domains explicit"), even though both
        may be configured to point at the same Cloud SQL PostgreSQL
        database in a real deployment.
        """
        direct = self._env.get(_KNOWLEDGE_DATABASE_URL_ENV_VAR)
        if direct:
            return direct

        secret_resource = self._env.get(_KNOWLEDGE_DATABASE_SECRET_ENV_VAR)
        if secret_resource:
            return _fetch_secret_from_secret_manager(secret_resource)

        return _DEFAULT_KNOWLEDGE_DATABASE_URL

    @property
    def case_context_max_items(self) -> int:
        raw = self._env.get(_CASE_CONTEXT_MAX_ITEMS_ENV_VAR)
        return int(raw) if raw else _DEFAULT_CASE_CONTEXT_MAX_ITEMS

    @property
    def case_context_max_characters(self) -> int:
        raw = self._env.get(_CASE_CONTEXT_MAX_CHARACTERS_ENV_VAR)
        return int(raw) if raw else _DEFAULT_CASE_CONTEXT_MAX_CHARACTERS

    @property
    def chat_attachments_bucket(self) -> Optional[str]:
        """POST-5.1 B1: the private GCS bucket for durable chat
        attachment binaries. No default -- unlike the database URLs,
        there is no safe/sensible local zero-setup fallback for a GCS
        bucket name. Returns `None` when unset; callers (`backend.
        attachments.storage`) must treat that as "attachment storage is
        unavailable" and fail clearly ONLY when actually invoked -- this
        property being unset must never fail application startup or
        break ordinary text chat, since no B1 code path is wired into a
        live request yet.
        """
        raw = self._env.get(_CHAT_ATTACHMENTS_BUCKET_ENV_VAR)
        return raw.strip() if raw and raw.strip() else None

    def resolve_power_automate_gateway_url(self) -> str:
        """Resolve the Power Automate gateway URL. Never log the result."""
        direct = self._env.get(_GATEWAY_URL_ENV_VAR)
        if direct:
            return direct

        secret_resource = self._env.get(_GATEWAY_SECRET_ENV_VAR)
        if secret_resource:
            return _fetch_secret_from_secret_manager(secret_resource)

        raise ConfigurationError(
            "Power Automate gateway URL is not configured. Set "
            f"{_GATEWAY_URL_ENV_VAR} for local development, or "
            f"{_GATEWAY_SECRET_ENV_VAR} to a GCP Secret Manager secret "
            "version resource name for deployment."
        )


@lru_cache(maxsize=32)
def _cached_secret_value(secret_resource: str) -> str:
    """Performance pass (pre-4H latency investigation): the actual fetch,
    cached for the LIFETIME OF THIS PROCESS, keyed by `secret_resource`.

    WHY THIS IS SAFE, unlike caching a value directly on `Settings`
    (which this module's own docstring/class docstring explicitly
    forbids): `Settings` is deliberately non-caching so that
    `os.environ` changes -- e.g. a test's `monkeypatch.setenv`/`delenv`
    -- are observed immediately; that guarantee is about WHICH secret
    resource NAME/env var is currently configured, and is completely
    preserved here (every call still re-reads `self._env` fresh in
    `resolve_power_automate_gateway_url`/`resolve_database_url` -- only
    the expensive network FETCH for a given, already-resolved resource
    NAME is memoized). A real deployment's secret resource name does not
    change mid-process, and Secret Manager access is exactly the kind of
    "long-lived safe resource" this backend should reuse rather than
    re-fetching over the network on every single Teams/database
    operation -- previously, EVERY `PowerAutomateClient._call` (i.e.
    every `teams.listChats`/`getMessages`/`getMembers`/`createChat`/
    `sendMessage`) re-resolved the gateway URL from scratch, meaning a
    Secret-Manager-configured deployment paid a full network round trip
    (plus `SecretManagerServiceClient()` construction) before every
    single Teams operation could even begin.

    NEVER exercised by the automated test suite: `conftest.py`'s autouse
    `power_automate_gateway_url` fixture always sets the direct env-var
    path and clears `SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE`, so this
    function is only ever reached in a real Secret-Manager deployment --
    caching it introduces no test-isolation risk (see
    test_settings_secret_caching.py).
    """
    from google.cloud import secretmanager  # noqa: PLC0415

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_resource)
    return response.payload.data.decode("utf-8")


def _fetch_secret_from_secret_manager(secret_resource: str) -> str:
    """Fetch a secret payload from GCP Secret Manager -- see
    `_cached_secret_value` for the process-lifetime caching this now
    delegates to. Kept as a separate, thin function so callers/tests can
    still refer to "resolving a secret" without needing to know about the
    cache specifically.
    """
    return _cached_secret_value(secret_resource)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide Settings singleton.

    Safe to cache: `Settings` holds a reference to `os.environ` (or an
    injected mapping), it never snapshots or stores a resolved secret
    value on itself.
    """
    return Settings()


@lru_cache(maxsize=8)
def get_shared_llm(model_name: str):
    """Latency pass: a process-lifetime-cached ADK `BaseLlm` instance for
    `model_name`, shared by every agent that uses this exact model string.

    WHY THIS EXISTS, VERIFIED AGAINST THE INSTALLED ADK (1.33.0) SOURCE:
    `google.adk.agents.llm_agent.LlmAgent.canonical_model` -- the property
    ADK's own flow code reads before EVERY model call -- is a plain
    (uncached) `@property`. When `Agent(model=...)` is given a bare model
    STRING (this backend's own previous construction:
    `Agent(model=_settings.gemini_model, ...)`), `canonical_model` calls
    `LLMRegistry.new_llm(self.model)` FRESH, on every single access --
    i.e. a brand-new `Gemini` (or whatever `BaseLlm` subclass the model
    string resolves to) instance is constructed for every model call,
    with no reuse. `Gemini.api_client` (the actual `google.genai.Client`)
    IS a `@cached_property`, but that caching is scoped to the `Gemini`
    INSTANCE -- worthless when a fresh instance is built every time.

    `LlmAgent.model`'s own field type is `Union[str, BaseLlm]`
    (`LlmAgent.model_fields["model"]`, verified directly) -- passing an
    ALREADY-CONSTRUCTED `BaseLlm` instance instead of a string is fully
    supported, documented usage (see `Gemini`'s own class docstring
    example: `Agent(model=GlobalGemini(model="gemini-3-pro-preview"))`).
    In that case, `canonical_model`'s first branch (`if isinstance(self
    .model, BaseLlm): return self.model`) returns the SAME instance every
    time, so its `api_client` -- and whatever underlying HTTP transport/
    credential resolution `google.genai.Client` performs -- is built ONCE
    per process and reused for every subsequent model call, instead of
    being rebuilt from scratch before every single Team Manager/Incident
    Manager model invocation.

    Uses `LLMRegistry.new_llm` -- the EXACT SAME resolution `canonical_
    model` itself uses -- rather than hardcoding a `Gemini(...)`
    construction, so this stays correct for whatever `SLOPANOC_MODEL` is
    actually configured to (never assumes every configured model string
    names a Gemini model).

    SAFE TO SHARE ACROSS CONCURRENT REQUESTS: `team_manager`/`incident_
    manager` (the `Agent` objects using this) are ALREADY process-wide,
    cross-request singletons in this codebase's own existing design (see
    their own `agent.py` modules) -- sharing their underlying model
    client is consistent with that existing sharing model, not a new
    category of shared mutable state. Constructing a `BaseLlm` instance
    itself performs no network/credential I/O (verified: `Gemini.__init__`
    only stores config; `api_client` is lazily built on first real use) --
    so this cache never does eager work at import time, and is exercised
    lazily on this process's own first real model call.
    """
    from google.adk.models.registry import LLMRegistry

    return LLMRegistry.new_llm(model_name)
