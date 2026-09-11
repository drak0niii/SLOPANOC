"""HTTP-layer orchestration for the authenticated Teams Source visual-
evidence content endpoint (`GET /api/sessions/{session_id}/sources/
{source_id}/images/{image_id}`) -- Teams Visual Evidence milestone.

AUTHORIZATION BOUNDARY: unlike `teams_get_hosted_content` (the ADK tool,
authorized by the TRANSIENT per-turn `known_hosted_content_ids` registry
established during the original live turn), this endpoint authorizes its
request from a DURABLE, server-persisted binding
(`turn_source_references.resolve_visual_evidence_binding`) -- the SAME
mechanism that already makes `SourceReferenceDTO`/`KnowledgeSourceReference
DTO` survive a hard refresh, saved-chat reopen, or backend restart. This is
deliberately a DIFFERENT authorization boundary for a DIFFERENT caller,
not a weakening of the original one -- see that function's own docstring.

FLOW (mirrors `attachment_service.get_attachment_content`'s own shape):

  1. `session_service.get_session(session_id, user_id)` -- resolves
     ownership; raises a generic `not_found` for an unknown OR wrong-owner
     session (anti-enumeration, unchanged existing behavior, never
     special-cased here).
  2. `resolve_visual_evidence_binding(session.state, source_id, image_id)`
     -- resolves the opaque `(source_id, image_id)` pair to the real,
     internal `(chat_id, message_id, hosted_content_id)` triple, or
     `None` for any unknown/mismatched/malformed combination -- a `None`
     here becomes the SAME generic `not_found` as step 1, never a
     different error shape that would let a caller distinguish "wrong
     session" from "wrong source" from "wrong image" (no existence
     enumeration).
  3. `fetch_and_validate_hosted_content(chat_id, message_id,
     hosted_content_id)` -- the SAME shared fetch+validate path
     `teams_get_hosted_content` uses (get_hosted_content.py), applying
     IDENTICAL Base64 decoding/MIME-allowlist/real-image-format
     validation -- never a second, independently-maintained validator.
     Re-validates the REAL, CURRENT gateway response every time (never
     trusts the mime_type/size_bytes recorded at the original turn) -- if
     the underlying Teams hosted content has since become unavailable,
     this fails the SAME generic way, and the caller (the frontend's
     `PersistedImageAttachment`-style component) renders "Image
     unavailable" rather than crashing or fabricating a placeholder.

Never exposes `chat_id`/`message_id`/`hosted_content_id`/any Power
Automate or Graph URL -- not even in an error message (every failure path
collapses to the same generic, content-free `not_found`).

PowerAutomateClient remains the only HTTP gateway client -- this module
never constructs its own gateway request; `fetch_and_validate_hosted_
content` already owns that.
"""
from __future__ import annotations

from backend.api.session_service import ApiSessionService
from backend.api.turn_source_references import resolve_visual_evidence_binding
from backend.gateway.safe_error import not_found
from backend.tools.teams.get_hosted_content import fetch_and_validate_hosted_content


async def get_source_image_content(
    session_service: ApiSessionService,
    user_id: str,
    session_id: str,
    source_id: str,
    image_id: str,
) -> tuple[bytes, str]:
    """Returns `(data, content_type)` for a real, already-delivered-to-
    Gemini Teams visual evidence image, or raises a generic `SafeError
    Exception` (`not_found`) for any failure -- unknown/foreign session,
    unknown source, unknown image, an image that belongs to a different
    source/session, or a Teams-side retrieval/validation failure. Every
    one of those cases is intentionally indistinguishable from the
    outside (anti-enumeration, mirrors every other session-scoped route in
    this API).
    """
    session = await session_service.get_session(session_id, user_id)

    binding = resolve_visual_evidence_binding(session.state, source_id, image_id)
    if binding is None:
        raise not_found("No visual evidence image was found with that id.")

    fetched = fetch_and_validate_hosted_content(binding.chat_id, binding.message_id, binding.hosted_content_id)
    if isinstance(fetched, dict):
        # A structured SafeError dict from fetch_and_validate_hosted_
        # content (e.g. the Teams content is no longer available, or a
        # gateway failure) -- collapsed to the SAME generic not_found as
        # every other failure path here, never the underlying detail.
        raise not_found("That Teams image is no longer available.")

    return fetched.data, fetched.content_type
