"""6A.4 / P11-M04: REAL CORPUS NARROWING VALIDATION.

Runs the full 6A.4 two-gate narrowing pipeline against the real,
already-cleared TELCO/RAN validation corpus (the same corpus A5/6A.3
already use), governed through the existing, unmodified `materialize_
candidate`/`approve_version` pipeline (6A.3's own `ingest_and_structure_
local_files`), then narrowed with a representative TELCO Context profile.

Same discipline as the existing real-corpus test files: SKIPS (never
fails, never fabricates a result) if the real files are unavailable.
Structural counts only -- no real document content is ever asserted,
printed, or logged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeVersion
from backend.knowledge.governance.service import approve_version, materialize_candidate
from backend.knowledge.narrowing.service import narrow_corpus
from backend.knowledge_ingestion.local_file_adapter import ingest_and_structure_local_files

_DOCUMENT1 = Path(r"C:\Users\eosiocn\Downloads\Document1.docx")
_ROGERS_4G = Path(r"C:\Users\eosiocn\Downloads\Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx")
_ROGERS_4G5G = Path(
    r"C:\Users\eosiocn\Downloads\MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx"
)
_ALL_PATHS = [_DOCUMENT1, _ROGERS_4G, _ROGERS_4G5G]
_missing = [p for p in _ALL_PATHS if not p.is_file()]
pytestmark = pytest.mark.skipif(bool(_missing), reason=f"real validation corpus not available on this machine: {_missing}")

_AS_OF = datetime(2026, 9, 11, tzinfo=timezone.utc)
_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

_KNOWLEDGE_IDS = {
    "Document1.docx": "6A4-VALIDATION-DOCUMENT1",
    "Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx": "6A4-VALIDATION-ROGERS-4G",
    "MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx": "6A4-VALIDATION-ROGERS-4G5G",
}


@pytest.fixture(scope="module")
def governed_real_corpus():
    import asyncio

    async def _build():
        outcomes = await ingest_and_structure_local_files(_ALL_PATHS)
        objects = []
        for outcome in outcomes:
            if outcome.structured is None:
                continue
            file_name = outcome.result.report.file_name
            knowledge_id = _KNOWLEDGE_IDS.get(file_name, f"6A4-VALIDATION-{file_name}")
            candidate = materialize_candidate(
                outcome.structured,
                knowledge_id=knowledge_id,
                document_type=KnowledgeDocumentType.MOP,
                version=KnowledgeVersion(label="1.0", effective_from=_EFFECTIVE_FROM),
                governed_at=_EFFECTIVE_FROM,
            )
            approved = approve_version(candidate, transitioned_at=_EFFECTIVE_FROM)
            assert approved.lifecycle_status == LifecycleStatus.APPROVED
            objects.append(approved)
        return objects

    return asyncio.run(_build())


def _representative_context():
    assertions = [
        ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a2", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="4G", canonical_value="4G", origin=ContextOrigin.USER),
    ]
    return compute_context_state(assertions)


def test_real_corpus_narrowing_structural_counts(governed_real_corpus) -> None:
    """Reports REAL, OBSERVED counts (§39/§16 of the 6A.4 corrective
    pass) -- never fabricated. UPDATED by the 6A.4 corrective pass
    ("Unspecified Applicability Must Fail Closed"): the real A5/6A.3
    corpus was never governed with applicability dimensions populated, so
    the CORRECTED, honest expected finding is that every object now
    resolves INDETERMINATE (reason APPLICABILITY_UNSPECIFIED) rather than
    permitted -- absence of applicability information is not evidence of
    applicability. Before this corrective pass, this same real corpus
    incorrectly showed `permitted=3, indeterminate=0` (see the 6A.4
    closure report's own real-corpus section) -- that was the exact
    defect this pass fixes, reproduced here against real data, not just
    synthetic fixtures.
    """
    result = narrow_corpus(governed_real_corpus, _representative_context(), as_of=_AS_OF)
    assert result.input_count == len(governed_real_corpus)
    assert result.input_count >= 1
    # Every real object is version-eligible (freshly approved, effective,
    # no supersession declared in this validation run).
    assert len(result.excluded_knowledge_ids) == 0
    assert len(result.permitted_knowledge_ids) == 0
    assert sorted(result.indeterminate_knowledge_ids) == sorted(o.knowledge_id for o in governed_real_corpus)
    for item in result.items:
        assert item.final_bucket == "indeterminate"
        assert item.applicability is not None
        from backend.knowledge.narrowing.contracts import NarrowingReasonCode

        assert NarrowingReasonCode.APPLICABILITY_UNSPECIFIED in item.applicability.reason_codes


def test_real_corpus_has_no_declared_telco_applicability_dimensions(governed_real_corpus) -> None:
    """Honest structural finding, not a defect: the real corpus's own
    `Applicability.dimensions` and asset-metadata-derived dimensions are
    both empty for every object -- narrowing therefore currently has
    nothing to narrow ON for this specific corpus, until a future
    ingestion/governance step populates real TELCO applicability
    metadata for these documents.
    """
    from backend.knowledge.narrowing.applicability_gate import resolve_canonical_dimensions

    for obj in governed_real_corpus:
        merged, explicit_any, conflicts = resolve_canonical_dimensions(obj)
        assert merged == {}
        assert explicit_any == set()
        assert conflicts == set()


def test_real_corpus_version_authority_gate_passes_for_freshly_approved_objects(governed_real_corpus) -> None:
    from backend.knowledge.narrowing.eligibility import evaluate_eligibility_for_corpus

    decisions = evaluate_eligibility_for_corpus(governed_real_corpus, _AS_OF)
    assert all(d.eligible for d in decisions)
    assert all(d.ai_approved is None for d in decisions)  # real corpus has no AI-Approved metadata yet
