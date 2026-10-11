"""Shared human identity boundary. Explicit local policy; no cloud/ADK imports.

Development verification means validation against trusted local server policy,
not proof of a production human login. IAP is intentionally unimplemented.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.observability.authorization import Principal, denied
from backend.observability.schemas import Permission, Role

DEFAULT_DEV_USER_ID = 'api-user'  # historical ANOC default, shared without ADK
DEV_USER_HEADER_NAME = 'X-SLOPANOC-DEV-USER'
LOCAL_ENVIRONMENTS = frozenset({'local', 'development'})


class VerifiedIdentityProvider(Protocol):
    async def authenticate(self, request: Request) -> Principal | None: ...


class DevelopmentGrant(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    role: Role
    capabilities: frozenset[Permission]
    environments: frozenset[str]


class DevelopmentPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    default_subject: str = Field(min_length=1, max_length=128)
    users: dict[str, DevelopmentGrant] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def valid_grants(self):
        if self.default_subject not in self.users:
            raise ValueError('Default development subject must have an explicit grant')
        for subject, grant in self.users.items():
            principal = Principal(subject, grant.role, grant.capabilities, grant.environments, True)
            if not principal.valid() or not grant.environments <= LOCAL_ENVIRONMENTS:
                raise ValueError('Invalid development identity grant')
        return self


@dataclass(frozen=True)
class DevelopmentVerifiedIdentityProvider:
    environment: str
    policy: DevelopmentPolicy

    async def authenticate(self, request: Request) -> Principal | None:
        if self.environment not in LOCAL_ENVIRONMENTS:
            return None
        subject = request.headers.get(DEV_USER_HEADER_NAME, '').strip() or self.policy.default_subject
        grant = self.policy.users.get(subject)
        if grant is None:
            return None
        if self.environment not in grant.environments:
            raise denied('authorization_error', 'Application environment access is not permitted.')
        return Principal(subject, grant.role, grant.capabilities, grant.environments, True)


class IAPVerifiedIdentityProvider:
    """Inert future integration point. No header constitutes verified IAP identity.

    A future implementation must verify the signed X-Goog-IAP-JWT-Assertion
    (signature/issuer/audience/time) and obtain grants from server-owned policy.
    Plain email/user headers are never sufficient. See AUTHENTICATION.md.
    """
    async def authenticate(self, request: Request) -> Principal | None:
        return None


class DeniedIdentityProvider:
    async def authenticate(self, request: Request) -> Principal | None:
        return None


def identity_provider(environment: str, env=None) -> VerifiedIdentityProvider:
    env = os.environ if env is None else env
    mode = env.get('SLOPANOC_AUTH_MODE', 'disabled')
    if mode == 'disabled':
        return DeniedIdentityProvider()
    if mode == 'iap':
        return IAPVerifiedIdentityProvider()  # deny-only until real verification exists
    if (mode != 'development' or environment not in LOCAL_ENVIRONMENTS
            or env.get('K_SERVICE') or env.get('GAE_ENV')):
        raise ValueError('Invalid authentication mode for application environment')
    try:
        path = Path(env['SLOPANOC_DEV_AUTH_POLICY_FILE'])
        if path.is_symlink() or not path.is_file():
            raise ValueError('Invalid local policy')
        # Bound reads even if the file changes between stat and open.
        with path.open('rb') as stream:
            payload = stream.read(65537)
        if len(payload) > 65536:
            raise ValueError('Local policy too large')
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate policy key')
                result[key] = value
            return result
        policy = DevelopmentPolicy.model_validate(json.loads(payload, object_pairs_hook=unique_keys))
    except (KeyError, OSError, ValueError, TypeError):
        # Never return paths, grant values or identity details in an error.
        raise ValueError('Invalid development authentication policy') from None
    return DevelopmentVerifiedIdentityProvider(environment, policy)


def validate_identity_configuration(environment: str) -> None:
    """Startup gate before business/reader lifecycle work; never enables a provider."""
    identity_provider(environment)


async def resolve_application_identity(request: Request) -> Principal | None:
    environment = getattr(request.app.state, 'authentication_environment', None)
    if environment is None:
        environment = os.environ.get('SLOPANOC_OTEL_ENVIRONMENT', 'development')
    try:
        provider = identity_provider(environment)
    except ValueError:
        raise denied() from None
    return await provider.authenticate(request)
