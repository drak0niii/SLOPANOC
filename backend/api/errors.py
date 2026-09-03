"""The API-level error contract -- reuses `backend.gateway.safe_error`
directly rather than inventing a second error taxonomy ("Preserve
existing SafeError philosophy").

Every error response this API ever returns, regardless of cause
(unknown session, malformed request body, a tool/gateway failure
propagating up from `team_manager`'s turn, or a genuinely unexpected
exception), has exactly the same JSON shape as `SafeError.to_dict()`:
`errorCode`/`userMessage`/`retryable`/`correlationId`. Never a stack
trace, never raw exception text, never a Power Automate URL or other
secret -- the same guarantee `backend/gateway/safe_error.py` already
makes for the Teams tool layer.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.gateway.safe_error import SafeError, SafeErrorException, internal_error, validation_error

# HTTP status per `ErrorCode` -- the one place this mapping is defined.
_STATUS_BY_ERROR_CODE: dict[str, int] = {
    "validation_error": 400,
    "authentication_error": 401,
    "authorization_error": 403,
    "not_found": 404,
    "rate_limited": 429,
    "connector_unavailable": 503,
    "knowledge_insufficient": 422,
    "run_failure": 502,
    "upload_failure": 502,
    "action_failure": 409,
    "internal_error": 500,
}


def status_for(safe_error: SafeError) -> int:
    return _STATUS_BY_ERROR_CODE.get(safe_error.error_code, 500)


def safe_error_response(safe_error: SafeError) -> JSONResponse:
    return JSONResponse(status_code=status_for(safe_error), content=safe_error.to_dict())


async def handle_safe_error(request: Request, exc: SafeErrorException) -> JSONResponse:
    """Registered for `SafeErrorException` -- the same exception type the
    Teams tool layer already raises. Anything that already produced a
    `SafeError` (a tool failure, a validation error raised deliberately by
    API code) passes straight through unchanged.
    """
    return safe_error_response(exc.safe_error)


async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI/Pydantic's own request-shape validation (missing/wrong-typed
    fields) -- translated into the same SafeError shape so the frontend
    only ever has to handle one error contract, never framework-specific
    JSON. The underlying Pydantic error detail can safely be echoed (it
    only ever describes the request shape itself, never secrets).
    """
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', 'invalid value')}" for e in exc.errors()
    )
    return safe_error_response(validation_error(f"The request was invalid: {detail}").safe_error)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler for anything not already a `SafeErrorException`
    -- an actual bug, an unhandled library exception, etc. Never includes
    `str(exc)` or any traceback detail in the response; that is exactly
    the leak this handler exists to prevent (see
    backend/gateway/safe_error.py's `SafeErrorException` docstring for the
    same reasoning applied to the Teams tool layer).
    """
    return safe_error_response(internal_error().safe_error)
