"""Default-denied identity seam. No header/environment can create a verified principal."""
from dataclasses import dataclass
import re
from fastapi import Depends
from backend.gateway.safe_error import SafeError, SafeErrorException
from .schemas import Role, Permission, ROLE_PERMISSIONS


def denied(code='authentication_error', message='Diagnostic access is unavailable.'):
    return SafeErrorException(SafeError(error_code=code,user_message=message))


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    capabilities: frozenset[Permission]
    environments: frozenset[str]
    verified: bool = False

    def valid(self):
        return (self.verified is True and isinstance(self.subject,str) and
            re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',self.subject) is not None and
            isinstance(self.role,Role) and isinstance(self.capabilities,frozenset) and
            self.capabilities <= ROLE_PERMISSIONS[self.role] and
            all(isinstance(p,Permission) for p in self.capabilities) and
            isinstance(self.environments,frozenset) and 1<=len(self.environments)<=16 and
            all(isinstance(e,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',e) for e in self.environments))


async def verified_principal_provider():
    """Future verified server integration goes here. Development identity is ignored."""
    return None


async def verified_principal(value=Depends(verified_principal_provider)):
    if not isinstance(value,Principal) or not value.valid():
        raise denied()
    return value


def require(capability):
    async def dependency(principal=Depends(verified_principal)):
        if capability not in principal.capabilities:
            raise denied('authorization_error','Diagnostic access is not permitted.')
        return principal
    return dependency
