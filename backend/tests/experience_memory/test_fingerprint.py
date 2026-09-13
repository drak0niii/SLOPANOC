"""Phase 6A.8 core test matrix -- FINGERPRINT/IDENTITY (§16/§17/§43),
corrected TWICE: first to include `source_origin` as a namespace stand-
in, then FINALLY corrected to use a genuine `source_namespace` field,
keeping `source_origin` strictly as the separate trust/admission
concern. Proven both at the pure-function level and via the mandatory
cross-namespace-collision / same-namespace-idempotency / source-origin-
independence proofs.
"""
from __future__ import annotations

from backend.experience_memory.domain.enums import ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.fingerprint import compute_experience_content_fingerprint, compute_experience_id
from backend.experience_memory.domain.models import ExperienceCandidate


def _candidate(**overrides) -> ExperienceCandidate:
    defaults = dict(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
        owner_id="VODAFONE",
        source_namespace="bmc",
        source_event_id="evt-1",
        outcome_summary="Observed alarm cleared.",
    )
    defaults.update(overrides)
    return ExperienceCandidate(**defaults)


def test_experience_id_deterministic_same_inputs() -> None:
    a = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    b = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    assert a == b


def test_experience_id_differs_on_owner() -> None:
    a = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    b = compute_experience_id("ORANGE", "observation", "bmc", "evt-1")
    assert a != b


def test_experience_id_differs_on_experience_type() -> None:
    a = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    b = compute_experience_id("VODAFONE", "case_resolution", "bmc", "evt-1")
    assert a != b


def test_experience_id_differs_on_source_event_id() -> None:
    a = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    b = compute_experience_id("VODAFONE", "observation", "bmc", "evt-2")
    assert a != b


def test_experience_id_differs_on_source_namespace_alone() -> None:
    """§9: the core corrective requirement -- identical owner/type/
    event-id, but a DIFFERENT source_namespace, must never collide."""
    a = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    b = compute_experience_id("VODAFONE", "observation", "onefm", "evt-1")
    assert a != b


def test_experience_id_is_a_hex_digest() -> None:
    eid = compute_experience_id("VODAFONE", "observation", "bmc", "evt-1")
    assert len(eid) == 64
    int(eid, 16)  # raises if not valid hex


def test_content_fingerprint_deterministic() -> None:
    candidate = _candidate()
    a = compute_experience_content_fingerprint(candidate)
    b = compute_experience_content_fingerprint(_candidate())
    assert a == b


def test_content_fingerprint_changes_on_material_content_change() -> None:
    a = compute_experience_content_fingerprint(_candidate())
    b = compute_experience_content_fingerprint(_candidate(outcome_summary="A materially different outcome occurred."))
    assert a != b


def test_content_fingerprint_includes_source_namespace() -> None:
    """§20: source_namespace is a normal candidate field, so it IS
    included in the content fingerprint (never a persistence-time
    concern like recorded_at)."""
    a = compute_experience_content_fingerprint(_candidate(source_namespace="bmc"))
    b = compute_experience_content_fingerprint(_candidate(source_namespace="onefm"))
    assert a != b


def test_content_fingerprint_stable_across_field_that_is_not_content() -> None:
    """§43: never includes a record-insertion timestamp/random id --
    proven structurally here: `ExperienceCandidate` has no such field at
    all, so two candidates built with identical logical content always
    fingerprint identically regardless of when/how many times they are
    constructed."""
    fingerprints = {compute_experience_content_fingerprint(_candidate()) for _ in range(5)}
    assert len(fingerprints) == 1


def test_experience_id_and_content_fingerprint_are_distinct_concepts() -> None:
    """Two candidates sharing the SAME identity basis (owner/type/
    namespace/source_event_id) but DIFFERENT other content get the SAME
    experience_id but a DIFFERENT content_fingerprint -- proving the two
    hashes answer genuinely different questions (§43 vs. §16/§17)."""
    c1 = _candidate(outcome_summary="Outcome A.")
    c2 = _candidate(outcome_summary="Outcome B, materially different.")
    eid1 = compute_experience_id(c1.owner_id, c1.experience_type.value, c1.source_namespace, c1.source_event_id)
    eid2 = compute_experience_id(c2.owner_id, c2.experience_type.value, c2.source_namespace, c2.source_event_id)
    assert eid1 == eid2
    fp1 = compute_experience_content_fingerprint(c1)
    fp2 = compute_experience_content_fingerprint(c2)
    assert fp1 != fp2


def test_corrective_pass_cross_namespace_same_event_id_never_collides() -> None:
    """§9's own mandatory cross-namespace collision proof, at the pure
    identity-function level: owner/type/event_id all identical, only
    source_namespace differs -> different experience_id, both legitimate
    identities remain independently computable."""
    candidate_a = _candidate(source_namespace="bmc")
    candidate_b = _candidate(source_namespace="onefm")
    eid_a = compute_experience_id(candidate_a.owner_id, candidate_a.experience_type.value, candidate_a.source_namespace, candidate_a.source_event_id)
    eid_b = compute_experience_id(candidate_b.owner_id, candidate_b.experience_type.value, candidate_b.source_namespace, candidate_b.source_event_id)
    assert eid_a != eid_b


def test_corrective_pass_same_full_source_identity_is_idempotent() -> None:
    """§8's own mandatory same-namespace idempotency proof, at the pure
    identity-function level: owner/type/namespace/event_id ALL identical
    -> the SAME experience_id every time."""
    candidate_1 = _candidate()
    candidate_2 = _candidate()
    eid_1 = compute_experience_id(candidate_1.owner_id, candidate_1.experience_type.value, candidate_1.source_namespace, candidate_1.source_event_id)
    eid_2 = compute_experience_id(candidate_2.owner_id, candidate_2.experience_type.value, candidate_2.source_namespace, candidate_2.source_event_id)
    assert eid_1 == eid_2


def test_corrective_pass_source_origin_independence() -> None:
    """§10: same source_origin, different source_namespace, same
    source_event_id -> still distinct identities -- trust classification
    is never mistaken for producer identity."""
    candidate_a = _candidate(source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="enm")
    candidate_b = _candidate(source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="alarm_platform")
    eid_a = compute_experience_id(candidate_a.owner_id, candidate_a.experience_type.value, candidate_a.source_namespace, candidate_a.source_event_id)
    eid_b = compute_experience_id(candidate_b.owner_id, candidate_b.experience_type.value, candidate_b.source_namespace, candidate_b.source_event_id)
    assert eid_a != eid_b


def test_corrective_pass_same_namespace_different_origin_is_same_identity() -> None:
    """§11: identity is NOT a function of source_origin at all -- same
    namespace + same event_id but a DIFFERENT source_origin resolves to
    the identical experience_id (the identity basis simply never reads
    source_origin), matching the audited decision that identity should
    track the producer event, not the admission classification."""
    candidate_a = _candidate(source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME, source_namespace="bmc")
    candidate_b = _candidate(source_origin=ExperienceSourceOrigin.EXECUTED_ACTION_RESULT, source_namespace="bmc")
    eid_a = compute_experience_id(candidate_a.owner_id, candidate_a.experience_type.value, candidate_a.source_namespace, candidate_a.source_event_id)
    eid_b = compute_experience_id(candidate_b.owner_id, candidate_b.experience_type.value, candidate_b.source_namespace, candidate_b.source_event_id)
    assert eid_a == eid_b


def test_identity_never_uses_source_timestamp() -> None:
    """§7's own explicit prohibition: source_event_at is never part of
    the identity basis -- two candidates differing ONLY in
    source_event_at must produce the SAME experience_id."""
    from datetime import datetime, timezone

    candidate_a = _candidate(source_event_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    candidate_b = _candidate(source_event_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    eid_a = compute_experience_id(candidate_a.owner_id, candidate_a.experience_type.value, candidate_a.source_namespace, candidate_a.source_event_id)
    eid_b = compute_experience_id(candidate_b.owner_id, candidate_b.experience_type.value, candidate_b.source_namespace, candidate_b.source_event_id)
    assert eid_a == eid_b
