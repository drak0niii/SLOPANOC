"""ANOC UserContext adapter over the shared server-verified identity boundary.

All existing session/approval/command ownership continues to use UserContext.
LOCAL/DEV identity is accepted only through explicit shared server policy; no
request body, browser roles or capabilities can supply authority. Production
requires a real verifier and otherwise denies access.
"""
from dataclasses import dataclass
from fastapi import Depends
from backend.authentication import DEV_USER_HEADER_NAME
from backend.observability.authorization import Principal, verified_principal, denied


@dataclass(frozen=True)
class UserContext:
    user_id: str


async def resolve_user_context(principal=Depends(verified_principal)) -> UserContext:
    if not isinstance(principal, Principal) or not principal.valid():
        raise denied()
    return UserContext(user_id=principal.subject)
