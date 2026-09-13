"""Shared, minimal synthetic fixture builders for 6A.9 Troubleshooting
Intelligence Assembly tests -- mirrors `backend/tests/context_
engineering/test_telco_integration.py`'s own helper style."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context.domain.models import ContextValue
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackage, ContextPackageInput, RequestContext
from backend.experience_memory.domain.enums import ExperienceLifecycle, ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceRecord
from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, EvidenceIndexRecord, EvidenceSelectionResult, HybridRetrievalCandidate, RetrievalChannel
from backend.skills.contracts import ContextRequirement, EvidenceRequirement, MethodologyStep, SkillApplicability, SkillDefinition, SkillLifecycle

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_evidence_item(evidence_id: str, *, knowledge_id: str = "KO-1", section_id: str = "sec-1", is_derived: bool = False, text: str = "evidence text") -> HybridRetrievalCandidate:
    record = EvidenceIndexRecord(
        evidence_id=evidence_id,
        knowledge_id=knowledge_id,
        version_label="v1",
        section_id=section_id,
        is_derived=is_derived,
        indexable_text=text,
        content_hash="deadbeef",
    )
    return HybridRetrievalCandidate(
        record=record,
        channel_hits=[ChannelHit(evidence_id=evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)],
        fusion_score=1.0,
        rerank_score=1.0,
    )


def make_evidence_selection(items: Optional[list[HybridRetrievalCandidate]] = None) -> EvidenceSelectionResult:
    return EvidenceSelectionResult(query_text="q", selected=items or [], selection_reason="top_k_within_budget")


def make_context_package(
    owner_id: str = "OWNER-1",
    *,
    telco_state: Optional[dict] = None,
    evidence_selection: Optional[EvidenceSelectionResult] = None,
    case_id: Optional[str] = None,
    request: Optional[RequestContext] = None,
) -> ContextPackage:
    telco_state = telco_state if telco_state is not None else {}
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION,
        owner_id=owner_id,
        case_id=case_id,
        request=request or RequestContext(),
        telco_context_state=telco_state,
        evidence_selection=evidence_selection or make_evidence_selection(),
    )
    return assemble_context_package(input_)


def make_skill(
    skill_id: str = "test.synthetic_skill",
    *,
    version: str = "1.0.0",
    lifecycle: SkillLifecycle = SkillLifecycle.ACTIVE,
    context_requirements: Optional[list[ContextRequirement]] = None,
    evidence_requirement: Optional[EvidenceRequirement] = None,
    applicability: Optional[SkillApplicability] = None,
    methodology: Optional[list[MethodologyStep]] = None,
) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        version=version,
        lifecycle=lifecycle,
        name="Synthetic Test Skill",
        description="A synthetic skill for tests only.",
        objective="Exercise Troubleshooting Intelligence Assembly in isolation.",
        applicability=applicability or SkillApplicability(),
        context_requirements=context_requirements or [],
        evidence_requirement=evidence_requirement or EvidenceRequirement(),
        methodology=methodology or [],
    )


def make_experience_record(
    experience_id: str = "exp-1",
    *,
    owner_id: str = "OWNER-1",
    experience_type: ExperienceType = ExperienceType.OBSERVATION,
    source_origin: ExperienceSourceOrigin = ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
    source_namespace: str = "bmc",
    outcome_summary: str = "Observed: alarm cleared after restart.",
    observed_facts: Optional[list[str]] = None,
    skill_id: Optional[str] = None,
    skill_version: Optional[str] = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=experience_id,
        experience_schema_version="1.0",
        experience_type=experience_type,
        lifecycle=ExperienceLifecycle.ACTIVE,
        owner_id=owner_id,
        source_origin=source_origin,
        source_namespace=source_namespace,
        source_event_id=f"evt-{experience_id}",
        outcome_summary=outcome_summary,
        observed_facts=observed_facts or [],
        skill_id=skill_id,
        skill_version=skill_version,
        content_fingerprint="fingerprint-placeholder",
        recorded_at=NOW,
    )
