"""Phase 6A.9 core test matrix -- `run_troubleshooting_assessment`'s own
orchestration: deterministic no-model-call paths, owner-mismatch fail-
closed, and post-model grounding enforcement (hallucinated evidence/
experience/skill references, malformed/absent model output) -- all via
a monkeypatched model invocation so these tests never require a real
Gemini/Vertex call.
"""
from __future__ import annotations

import pytest

import backend.agents.troubleshooting_manager.runtime as runtime_module
from backend.agents.troubleshooting_manager.runtime import run_troubleshooting_assessment
from backend.agents.troubleshooting_manager.schemas import (
    EvidenceReferenceUsed,
    ExperienceReferenceUsed,
    TroubleshootingManagerRequest,
    TroubleshootingManagerResponse,
    TroubleshootingResponseStatus,
)
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService
from backend.skills.registry import SkillRegistry
from backend.troubleshooting_intelligence.assembly import TroubleshootingOwnerMismatchError

from ._fixtures import context_package_fault_unknown, context_package_with_fault_known, evidence_candidate


@pytest.mark.asyncio
async def test_none_registered_never_invokes_model() -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)
    result = await run_troubleshooting_assessment(request, skill_registry=SkillRegistry([]), experience_service=ExperienceMemoryService())
    assert result.model_invoked is False
    assert result.response.status == TroubleshootingResponseStatus.BLOCKED


@pytest.mark.asyncio
async def test_none_ready_never_invokes_model_and_explains_why() -> None:
    package = context_package_fault_unknown(evidence_items=[evidence_candidate()])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)
    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.model_invoked is False
    assert result.response.status == TroubleshootingResponseStatus.NEEDS_INFORMATION
    assert result.response.detail is not None and "fault" in result.response.detail


@pytest.mark.asyncio
async def test_owner_mismatch_fails_closed() -> None:
    package = context_package_with_fault_known(owner_id="OWNER-A", evidence_items=[evidence_candidate()])
    request = TroubleshootingManagerRequest(owner_id="OWNER-B", context_package=package)
    with pytest.raises(TroubleshootingOwnerMismatchError):
        await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())


@pytest.mark.asyncio
async def test_selected_path_invokes_model_and_passes_through_grounded_response(monkeypatch) -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package, objective="what next?")

    good_response = TroubleshootingManagerResponse(
        status=TroubleshootingResponseStatus.ADVISORY_READY,
        assessment="Fault is known; evidence reviewed.",
        evidence_references_used=[EvidenceReferenceUsed(evidence_id="ev-1")],
        skill_id="telco.troubleshooting_assessment",
        skill_version="1.0.0",
    )

    async def _fake_invoke(package_):
        return good_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.model_invoked is True
    assert result.response.status == TroubleshootingResponseStatus.ADVISORY_READY
    assert result.grounding_error is None


@pytest.mark.asyncio
async def test_hallucinated_evidence_reference_fails_closed(monkeypatch) -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)

    bad_response = TroubleshootingManagerResponse(
        status=TroubleshootingResponseStatus.ADVISORY_READY,
        assessment="fabricated",
        evidence_references_used=[EvidenceReferenceUsed(evidence_id="ev-DOES-NOT-EXIST")],
    )

    async def _fake_invoke(package_):
        return bad_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.response.status == TroubleshootingResponseStatus.BLOCKED
    assert result.grounding_error is not None and "evidence" in result.grounding_error
    assert result.response.assessment is None, "a response that failed grounding must never be partially trusted"


@pytest.mark.asyncio
async def test_hallucinated_experience_reference_fails_closed(monkeypatch) -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)

    bad_response = TroubleshootingManagerResponse(
        status=TroubleshootingResponseStatus.ADVISORY_READY,
        experience_references_used=[ExperienceReferenceUsed(experience_id="exp-DOES-NOT-EXIST")],
    )

    async def _fake_invoke(package_):
        return bad_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.response.status == TroubleshootingResponseStatus.BLOCKED
    assert result.grounding_error is not None and "experience" in result.grounding_error


@pytest.mark.asyncio
async def test_hallucinated_skill_reference_fails_closed(monkeypatch) -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)

    bad_response = TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.ADVISORY_READY, skill_id="telco.troubleshooting_assessment", skill_version="99.0.0")

    async def _fake_invoke(package_):
        return bad_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.response.status == TroubleshootingResponseStatus.BLOCKED
    assert result.grounding_error is not None and "skill" in result.grounding_error


@pytest.mark.asyncio
async def test_model_producing_nothing_usable_fails_closed(monkeypatch) -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)

    async def _fake_invoke(package_):
        return None

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.model_invoked is True
    assert result.response.status == TroubleshootingResponseStatus.BLOCKED


@pytest.mark.asyncio
async def test_intelligence_package_available_regardless_of_status() -> None:
    """§94: the Intelligence Package's own fingerprint/selected evidence/
    Skill identity must be reconstructable from the result even for a
    deterministic, no-model-call BLOCKED/NEEDS_INFORMATION outcome."""
    package = context_package_fault_unknown(evidence_items=[])
    request = TroubleshootingManagerRequest(owner_id="OWNER-1", context_package=package)
    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())
    assert result.intelligence_package.content_fingerprint != ""
    assert result.intelligence_package.owner_id == "OWNER-1"
