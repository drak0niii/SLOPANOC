"""Phase 6A.9: `run_troubleshooting_assessment` -- the ONE direct/
internal invocation surface for `troubleshooting_manager` (§72). Not a
new public HTTP route (§104) and not wired into `team_manager` (6A.10's
own scope) -- exercised directly by tests and by this milestone's own
controlled real-model validation.

ORCHESTRATION, IN ORDER:
  1. Resolve a Skill deterministically (`skill_resolution.py`) -- NEVER a
     model call merely to choose the only valid Skill (§62).
  2. Query bounded, owner-scoped historical Experience
     (`experience_support.py`), filtered by the ALREADY-resolved Skill
     (§64) -- optional, may legitimately be empty (§79).
  3. Assemble the deterministic `TroubleshootingIntelligencePackage`
     (`backend.troubleshooting_intelligence.assembly`) -- pure, no model
     call.
  4. If no Skill was SELECTED (missing/not-ready/not-applicable/
     unregistered methodology), return a SAFE, DETERMINISTIC,
     PYTHON-AUTHORED result WITHOUT ever invoking the model (§27/§61/§91:
     "no silent fallback to general model knowledge").
  5. Otherwise, invoke `troubleshooting_manager` exactly once via a
     throwaway `InMemorySessionService`/`Runner` turn (mirrors this
     codebase's own established one-shot-agent pattern --
     `provenance_compliance.py`'s compliance retry,
     `governed_knowledge_completion.py`'s remediation call,
     `gemini_image_interpreter.py`'s interpretation call), parse the
     model's own `output_schema`-validated JSON, and run deterministic
     grounding validation (§54-56) before ever trusting the result.
  6. On EITHER a malformed/unparseable model response OR a grounding
     validation failure, replace the ENTIRE response with a safe,
     Python-authored BLOCKED result -- never partially accept an
     ungrounded answer (§55).

ONE-SHOT ONLY (§70/§71): there is no loop here of any kind -- the model
is invoked at most once per call to this function, and this function
performs no retry/self-correction of its own (unlike `provenance_
compliance.py`'s own bounded one-retry mechanism, which exists for a
DIFFERENT, already-frozen Incident Manager compliance concern this
module does not touch or reuse).
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ValidationError

from backend.agents.troubleshooting_manager.experience_support import query_experience_support
from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerRequest, TroubleshootingManagerResponse, TroubleshootingResponseStatus
from backend.agents.troubleshooting_manager.skill_resolution import resolve_skill_for_troubleshooting
from backend.skills.contracts import RequirementStatus, SkillReadinessResult
from backend.troubleshooting_intelligence.assembly import assemble_troubleshooting_intelligence
from backend.troubleshooting_intelligence.contracts import (
    SkillSelectionOutcome,
    TroubleshootingIntelligenceInput,
    TroubleshootingIntelligencePackage,
)
from backend.troubleshooting_intelligence.grounding import (
    TroubleshootingGroundingError,
    validate_evidence_references,
    validate_experience_references,
    validate_skill_reference,
)
from backend.troubleshooting_intelligence.rendering import render_troubleshooting_intelligence_as_text

__all__ = ["TroubleshootingManagerResult", "run_troubleshooting_assessment"]

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = "troubleshooting_manager::direct-invocation"
_USER_ID = "troubleshooting-manager-direct-invocation"


class TroubleshootingManagerResult(BaseModel):
    """The full, auditable result of one `run_troubleshooting_assessment`
    call -- always carries the assembled Intelligence Package (§94: an
    audit manifest is reconstructable from `intelligence_package
    .content_fingerprint` plus its own selected evidence/experience/skill
    identities) alongside the final, validated response."""

    response: TroubleshootingManagerResponse
    intelligence_package: TroubleshootingIntelligencePackage
    model_invoked: bool
    grounding_error: Optional[str] = None


def _detail_from_readiness(readiness: Optional[SkillReadinessResult]) -> str:
    if readiness is None:
        return "no applicable troubleshooting methodology is currently ready."
    reasons: list[str] = []
    for result in readiness.context_results:
        if result.status == RequirementStatus.MISSING:
            reasons.append(f"{result.dimension.value}: {result.reason}")
    if readiness.case_context_result == RequirementStatus.MISSING:
        reasons.append("case context is required but not linked")
    if readiness.evidence_result.status == RequirementStatus.MISSING:
        reasons.append(readiness.evidence_result.reason or "selected evidence requirement not met")
    for result in readiness.capability_results:
        if result.status == RequirementStatus.MISSING:
            reasons.append(f"capability {result.capability} is not available")
    if not reasons:
        return "required inputs for the applicable troubleshooting methodology are not yet available."
    return "missing required input(s) for the applicable troubleshooting methodology: " + "; ".join(reasons)


def _deterministic_unready_response(outcome: SkillSelectionOutcome, readiness) -> TroubleshootingManagerResponse:
    if outcome == SkillSelectionOutcome.NONE_REGISTERED:
        return TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.BLOCKED, detail="no production troubleshooting methodology is registered.")
    if outcome == SkillSelectionOutcome.NONE_APPLICABLE:
        return TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.BLOCKED, detail="no registered troubleshooting methodology applies to the current operational context.")
    # NONE_READY
    return TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.NEEDS_INFORMATION, detail=_detail_from_readiness(readiness))


def _merged_final_text(content: Optional[types.Content]) -> Optional[str]:
    """Mirrors `provenance_compliance.py`'s/`governed_knowledge_
    completion.py`'s own identical "final, non-thought text" extraction,
    reused by convention rather than cross-imported (each of this
    codebase's one-shot-agent modules keeps its own tiny copy)."""
    if content is None or content.parts is None:
        return None
    merged = "\n".join(p.text for p in content.parts if p.text and not p.thought)
    return merged.strip() or None


async def _invoke_troubleshooting_manager_model(package: TroubleshootingIntelligencePackage) -> Optional[TroubleshootingManagerResponse]:
    """Runs `troubleshooting_manager` exactly once against a throwaway,
    never-persisted session. Returns `None` (never raises for an ordinary
    malformed/empty response) when the model produced no usable
    schema-valid output -- the caller treats that identically to a
    grounding failure (a safe, deterministic BLOCKED result)."""
    from backend.agents.troubleshooting_manager.agent import troubleshooting_manager

    rendered = render_troubleshooting_intelligence_as_text(package)
    content = types.Content(role="user", parts=[types.Part.from_text(text=rendered)])

    session_service = InMemorySessionService()
    session_id = f"troubleshooting-manager::{package.content_fingerprint}"
    runner = Runner(app_name=_APP_NAME, agent=troubleshooting_manager, session_service=session_service, memory_service=InMemoryMemoryService())
    try:
        await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
        last_content: Optional[types.Content] = None
        async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=content):
            if event.content:
                last_content = event.content
    finally:
        await runner.close()
        try:
            existing = await session_service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
            if existing is not None:
                await session_service.delete_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
        except Exception:
            _logger.warning("troubleshooting_manager.runtime: failed to delete internal direct-invocation session")

    merged_text = _merged_final_text(last_content)
    if merged_text is None:
        return None
    try:
        return TroubleshootingManagerResponse.model_validate_json(merged_text)
    except ValidationError:
        _logger.warning("troubleshooting_manager.runtime: model response failed schema validation")
        return None


def _validate_grounding(package: TroubleshootingIntelligencePackage, response: TroubleshootingManagerResponse) -> None:
    validate_evidence_references(package, [item.evidence_id for item in response.evidence_references_used])
    validate_experience_references(package, [item.experience_id for item in response.experience_references_used])
    validate_skill_reference(package, response.skill_id, response.skill_version)


async def run_troubleshooting_assessment(
    request: TroubleshootingManagerRequest,
    *,
    skill_registry=None,
    experience_service=None,
) -> TroubleshootingManagerResult:
    """`skill_registry`/`experience_service` are OPTIONAL test-only
    dependency-injection seams (mirroring `resolve_skill_for_
    troubleshooting`'s/`query_experience_support`'s own existing
    `registry`/`service` parameters exactly) -- the real runtime default
    (both `None`) resolves the real on-disk production Skill registry and
    the real process-wide `ExperienceMemoryService`. Never used to
    substitute the model itself, which is always the real
    `troubleshooting_manager` agent."""
    resolution = resolve_skill_for_troubleshooting(
        request.context_package,
        registry=skill_registry,
        available_capabilities=set(request.available_capabilities) if request.available_capabilities is not None else None,
    )

    skill_id = resolution.skill.skill_id if resolution.skill is not None else None
    skill_version = resolution.skill.version if resolution.skill is not None else None
    experience_records, experience_metadata = await query_experience_support(
        owner_id=request.owner_id,
        skill_id=skill_id,
        skill_version=skill_version,
        case_id=request.context_package.case_id,
        service=experience_service,
    )

    intelligence_input = TroubleshootingIntelligenceInput(
        owner_id=request.owner_id,
        objective=request.objective,
        context_package=request.context_package,
        skill=resolution.skill,
        skill_fingerprint=resolution.fingerprint,
        skill_selection_outcome=resolution.outcome,
        skill_readiness=resolution.readiness,
        skill_applicability=resolution.applicability,
        experience_records=experience_records,
        experience_query_metadata=experience_metadata,
    )
    package = assemble_troubleshooting_intelligence(intelligence_input)

    if resolution.outcome != SkillSelectionOutcome.SELECTED:
        response = _deterministic_unready_response(resolution.outcome, resolution.readiness)
        return TroubleshootingManagerResult(response=response, intelligence_package=package, model_invoked=False)

    _perf_logger.info("perf stage=troubleshooting_manager_invocation_start intelligence_fingerprint=%s", package.content_fingerprint)
    raw_response = await _invoke_troubleshooting_manager_model(package)
    if raw_response is None:
        safe = TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.BLOCKED, detail="the troubleshooting assessment could not be produced or validated for this request.")
        return TroubleshootingManagerResult(response=safe, intelligence_package=package, model_invoked=True, grounding_error="model produced no schema-valid response")

    try:
        _validate_grounding(package, raw_response)
    except TroubleshootingGroundingError as exc:
        _logger.warning("troubleshooting_manager.runtime: grounding validation failed: %s", exc)
        safe = TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.BLOCKED, detail="the troubleshooting assessment referenced evidence, experience, or a methodology that could not be validated.")
        return TroubleshootingManagerResult(response=safe, intelligence_package=package, model_invoked=True, grounding_error=str(exc))

    _perf_logger.info("perf stage=troubleshooting_manager_invocation_succeeded intelligence_fingerprint=%s", package.content_fingerprint)
    return TroubleshootingManagerResult(response=raw_response, intelligence_package=package, model_invoked=True)
