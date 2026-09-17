"""POST-6A -- One shared evidence-service contract for both specialists.

THE GAP THIS CLOSES: Incident Manager and Troubleshooting Manager each
reached for governed evidence through their own path, so the same turn
could run narrowing and retrieval more than once, and each caller had to
re-derive for itself what "no evidence" meant. Worse, "no evidence"
collapsed four genuinely different situations into one silence:

  - the USER has not told us enough yet (missing context),
  - the corpus genuinely contains nothing applicable,
  - retrieval INFRASTRUCTURE could not answer (embedding provider down,
    vector extension absent, database unavailable),
  - POLICY forbade it.

The third is the dangerous one: reporting an infrastructure outage as
"I don't have information about that" invites the engineer to supply
more detail that will not help, and hides a real fault. This module
makes the distinction typed and mandatory.

WHAT THIS IS: a thin COMPOSITION over the existing, unmodified services
-- `narrow_knowledge` (6A.4), `hybrid_retrieve` (6A.5),
`KnowledgeRetrievalService` (5.1G) and `KnowledgeProvenanceService`
(5.1H). It owns no retrieval logic, no ranking, no applicability
evaluation and no provenance validation of its own; it decides only
WHICH of those to call, in what order, and how to describe the outcome.

SPECIALIST RESPONSIBILITIES ARE UNCHANGED: this service returns evidence.
It never interprets it, never chooses a next step, and never produces
user-facing text -- those remain Incident Manager's and Troubleshooting
Manager's own jobs respectively.

NO DUPLICATE RETRIEVAL WITHIN A TURN: results are cached per
`(run_id, request fingerprint)`. A second specialist asking for the same
evidence in the same turn gets the first one's already-validated result,
marked `reused=True`, rather than issuing a second set of queries. The
cache is per-run and in-process only -- never persisted, never shared
across turns, and cleared by `discard_turn_evidence`, mirroring
`troubleshooting_guidance_context.py`'s own run-scoped store exactly.

VERSION IDENTITY IS PRESERVED END TO END: `permitted_version_keys` flows
from narrowing into every retrieval query and is re-asserted against the
final evidence set. Authorization never widens from an approved version
to every version sharing its knowledge_id.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.context.domain.enums import ContextDimension
from backend.context.domain.models import ContextValue
from backend.knowledge.narrowing.contracts import KnowledgeNarrowingResult, NarrowingPolicy
from backend.knowledge.narrowing.service import narrow_knowledge
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem
from backend.knowledge.repository.contracts import KnowledgeRepository

_logger = logging.getLogger(__name__)

__all__ = [
    "EvidenceAvailability",
    "EvidenceRequest",
    "SharedEvidenceResult",
    "SharedEvidenceService",
    "discard_turn_evidence",
    "applicability_fingerprint",
    "applicability_fingerprint",
    "get_run_availability",
    "is_version_authorized",
    "record_run_version_authority",
    "record_run_availability",
]


class EvidenceAvailability(str, Enum):
    """The closed outcome vocabulary every evidence consumer must branch
    on. These are NOT interchangeable, and the distinction is the point
    of this enum: three of them mean "no evidence", and each calls for a
    completely different user-facing response."""

    AVAILABLE = "available"
    """Evidence was retrieved and validated."""

    MISSING_USER_CONTEXT = "missing_user_context"
    """Narrowing could not proceed because required operational context
    (vendor, technology, fault, target, ...) is genuinely not known yet.
    Asking the user is the correct response."""

    NO_APPLICABLE_EVIDENCE = "no_applicable_evidence"
    """Retrieval ran correctly and completely; the governed corpus simply
    contains nothing applicable. Asking the user for more detail may
    help, but the honest statement is that nothing applicable exists."""

    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    """An INFRASTRUCTURE failure prevented a complete answer. NEVER
    reported to the user as missing information from them -- the fault is
    ours, more detail from them cannot fix it, and disguising it hides a
    real outage."""

    POLICY_BLOCKED = "policy_blocked"
    """Narrowing positively excluded everything (lifecycle, expiry,
    AI-approval, applicability mismatch, access restriction). Evidence
    may exist but this request may not see it."""


class RetrievalDegradation(str, Enum):
    """What was lost, when retrieval succeeded only partially. Present on
    an `AVAILABLE` result too -- partial success is still success, but the
    consumer must be able to say so rather than implying completeness."""

    NONE = "none"
    SEMANTIC_CHANNEL_UNAVAILABLE = "semantic_channel_unavailable"
    """Exact/lexical results are authorized and usable; the semantic
    channel did not run (embedding provider failed, or no vector
    extension). Results may be less complete, never less authorized."""

    PROVENANCE_REVALIDATION_FAILED = "provenance_revalidation_failed"
    """Retrieval produced candidates that could not be revalidated
    against the governed repository. Fails closed -- nothing is returned
    as evidence -- but is reported as infrastructure/data drift, not as
    'nothing applicable'."""


class EvidenceRequest(BaseModel):
    """One request for governed evidence, from either specialist."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    query_text: str
    as_of: datetime
    limit: int = 10
    requested_by: str = Field(default="unspecified", description="Specialist name -- diagnostics only, never authority.")
    user_id: str = Field(
        default="",
        description=(
            "The trusted request identity this retrieval is performed FOR. Part of the cache key: two users "
            "must never share a cached evidence result, because what each is permitted to see can differ."
        ),
    )
    access_scope: tuple[str, ...] = Field(
        default=(),
        description=(
            "Any additional trusted access/tenancy scope this retrieval is bound to. Part of the cache key for "
            "the same reason as `user_id`. Empty today (there is no tenancy model yet) -- present so a future "
            "scope cannot be added without the cache key following it."
        ),
    )
    applicability_fingerprint: str = Field(
        default="",
        description=(
            "Deterministic digest of the narrowing/applicability context this retrieval was performed under. "
            "Two otherwise-identical queries evaluated against DIFFERENT known facts are different retrievals "
            "and must not share a result."
        ),
    )

    def fingerprint(self) -> str:
        """Identity for the per-turn cache.

        INCLUDES EVERY RETRIEVAL-RELEVANT INPUT AND THE TRUSTED SCOPE:
        query text, cutoff, limit, applicability context, user identity
        and access scope. A cache that keys on less than this can serve
        one user's authorized evidence to another, or serve results
        computed under different known facts -- both are authorization
        failures, not performance details.

        `requested_by` is the ONE deliberate exclusion: it is the
        specialist's own name, carries no authority, and excluding it is
        exactly what lets the second specialist reuse the first one's
        already-validated evidence within the same turn.
        """
        basis = "\x1f".join(
            (
                self.query_text,
                self.as_of.isoformat(),
                str(self.limit),
                self.user_id,
                "\x1e".join(self.access_scope),
                self.applicability_fingerprint,
            )
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class SharedEvidenceResult(BaseModel):
    """What both specialists consume. Trusted `KnowledgeEvidenceItem`s
    only -- never a raw retrieval candidate, never model-visible prose."""

    model_config = ConfigDict(frozen=True)

    availability: EvidenceAvailability
    degradation: RetrievalDegradation = RetrievalDegradation.NONE
    items: tuple[KnowledgeEvidenceItem, ...] = ()
    retrieval_items: tuple[Any, ...] = Field(
        default=(),
        description=(
            "The matching `KnowledgeRetrievalItem`s, in the same order/identity as `items`. Trusted, "
            "backend-only, never model-facing -- carried so the existing `knowledge_search` adapter can build "
            "its agent payload by correlating retrieval's own `applicability_outcome`/`relevance_score` with "
            "provenance's validated content, exactly as it always has, instead of a second retrieval."
        ),
    )
    evidence_set: Optional[Any] = Field(
        default=None,
        description=(
            "The `KnowledgeEvidenceSet` exactly as `KnowledgeProvenanceService` built it. Passed through "
            "rather than rebuilt: provenance is the only thing permitted to construct evidence types, and a "
            "reconstruction here would be a second, unvalidated construction of trusted state."
        ),
    )
    permitted_version_keys: tuple[tuple[str, str], ...] = ()
    """The versions narrowing POSITIVELY authorized. Evidence from these
    may ground an operational output."""

    indeterminate_version_keys: tuple[tuple[str, str], ...] = ()
    """The versions whose applicability could NOT be proven from the
    context known so far. Retrieved deliberately -- they are the material
    a clarification or an explicitly-qualified reference is built from --
    but they AUTHORIZE NOTHING. An operational instruction grounded in
    knowledge we cannot show applies is exactly the failure this split
    exists to prevent, so the two sets are never unioned."""
    reused: bool = False
    """True when this turn had already retrieved exactly this evidence
    and the result was served from the per-turn cache."""
    detail: str = ""
    """Safe diagnostic -- typed reasons and counts only, never governed
    content and never a credential/URL."""

    @property
    def authorized_items(self) -> tuple[KnowledgeEvidenceItem, ...]:
        """Evidence from a POSITIVELY permitted version -- the only
        evidence that may ground a command, an operational step or an
        action."""
        permitted = set(self.permitted_version_keys)
        return tuple(
            item
            for item in self.items
            if (item.reference.knowledge_id, item.reference.version_label) in permitted
        )

    @property
    def indeterminate_items(self) -> tuple[KnowledgeEvidenceItem, ...]:
        """Evidence whose applicability is unproven. Usable for
        clarification and explicitly-qualified reference where policy
        permits; never for operational authority."""
        indeterminate = set(self.indeterminate_version_keys)
        return tuple(
            item
            for item in self.items
            if (item.reference.knowledge_id, item.reference.version_label) in indeterminate
        )

    @property
    def has_authorized_evidence(self) -> bool:
        return bool(self.authorized_items)

    @property
    def is_available(self) -> bool:
        return self.availability == EvidenceAvailability.AVAILABLE and bool(self.items)

    @property
    def is_infrastructure_failure(self) -> bool:
        """The ONE question a user-facing layer must ask before phrasing
        an apology: is this our fault or a genuine information gap?"""
        return self.availability == EvidenceAvailability.RETRIEVAL_UNAVAILABLE


_lock = threading.Lock()
_turn_cache: dict[str, dict[str, SharedEvidenceResult]] = {}


def discard_turn_evidence(run_id: Optional[str]) -> None:
    """Clear this run's cached evidence. Called unconditionally from the
    turn's own cleanup, exactly like every other run-scoped store."""
    if not run_id:
        return
    with _lock:
        _turn_cache.pop(run_id, None)
        _run_availability.pop(run_id, None)
        _run_version_authority.pop(run_id, None)


_run_availability: dict[str, "SharedEvidenceResult"] = {}


def record_run_availability(run_id: str, result: SharedEvidenceResult) -> None:
    """Record this run's WORST evidence outcome, so the orchestration and
    presentation layers can tell an infrastructure failure from an
    ordinary "nothing applicable" WITHOUT re-deriving it or inspecting
    prose.

    "Worst" is deliberate: if any evidence acquisition this turn failed
    for infrastructure reasons, that is the honest thing to tell the
    user, even if another acquisition happened to succeed. A degraded
    success never overwrites a recorded failure.
    """
    if not run_id:
        return
    with _lock:
        existing = _run_availability.get(run_id)
        if existing is not None and existing.availability == EvidenceAvailability.RETRIEVAL_UNAVAILABLE:
            return
        if existing is not None and result.availability == EvidenceAvailability.AVAILABLE:
            # Keep the more informative non-AVAILABLE record.
            if existing.availability != EvidenceAvailability.AVAILABLE:
                return
        _run_availability[run_id] = result


def get_run_availability(run_id: Optional[str]) -> Optional[SharedEvidenceResult]:
    """Read-only accessor for the orchestration layer. `None` means no
    evidence was acquired this run at all -- which is NOT a failure and
    must never be reported as one."""
    if not run_id:
        return None
    with _lock:
        return _run_availability.get(run_id)


_run_version_authority: dict[str, tuple[set, set]] = {}


def record_run_version_authority(run_id: str, result: SharedEvidenceResult) -> None:
    """Accumulate, per run, which governed versions were AUTHORIZED and
    which were merely retrievable-but-unproven.

    `chat_service.py` consults this before letting a governed operation
    descriptor confer any authority: a descriptor sitting on a section
    whose version could not be proven applicable must not authorize a
    command, no matter how correct the descriptor itself is."""
    if not run_id:
        return
    with _lock:
        authorized, indeterminate = _run_version_authority.setdefault(run_id, (set(), set()))
        authorized.update(result.permitted_version_keys)
        indeterminate.update(result.indeterminate_version_keys)


def is_version_authorized(run_id: Optional[str], knowledge_id: Optional[str], version_label: Optional[str]) -> bool:
    """`True` only when this run POSITIVELY authorized that exact version.

    Fail-closed in both directions that matter: an unknown run, an
    unretrieved version, or a version that only ever appeared in the
    indeterminate set all return `False`. "We never checked" is not
    "authorized"."""
    if not run_id or not knowledge_id or not version_label:
        return False
    with _lock:
        entry = _run_version_authority.get(run_id)
    if entry is None:
        return False
    authorized, _indeterminate = entry
    return (knowledge_id, version_label) in authorized


def _cached(run_id: str, fingerprint: str) -> Optional[SharedEvidenceResult]:
    with _lock:
        return _turn_cache.get(run_id, {}).get(fingerprint)


def _store(run_id: str, fingerprint: str, result: SharedEvidenceResult) -> None:
    with _lock:
        _turn_cache.setdefault(run_id, {})[fingerprint] = result


class SharedEvidenceService:
    """The one evidence entry point both specialists call.

    Dependencies are injected as the EXISTING service objects -- this
    class constructs none of them and replaces none of them. A dependency
    left as `None` simply makes the corresponding channel unavailable,
    reported as a typed degradation rather than an exception.
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        retrieval_service: Any,
        provenance_service: Any,
        narrowing_policy: Optional[NarrowingPolicy] = None,
    ) -> None:
        self._repository = repository
        self._retrieval = retrieval_service
        self._provenance = provenance_service
        self._policy = narrowing_policy

    async def acquire(
        self,
        request: EvidenceRequest,
        context_state: Optional[dict[ContextDimension, ContextValue]] = None,
        *,
        applicability_context: Any = None,
    ) -> SharedEvidenceResult:
        """Narrow -> retrieve -> revalidate, once per turn per request
        shape. Every failure mode resolves to a TYPED availability, never
        to an exception and never to an empty list that means four
        different things."""
        fingerprint = request.fingerprint()
        cached = _cached(request.run_id, fingerprint)
        if cached is not None:
            _logger.info(
                "shared_evidence: reusing this turn's already-validated evidence requested_by=%s run_id=%s",
                request.requested_by,
                request.run_id,
            )
            return cached.model_copy(update={"reused": True})

        result = await self._acquire_uncached(request, context_state or {}, applicability_context)
        _store(request.run_id, fingerprint, result)
        record_run_availability(request.run_id, result)
        record_run_version_authority(request.run_id, result)
        return result

    async def _acquire_uncached(
        self,
        request: EvidenceRequest,
        context_state: dict[ContextDimension, ContextValue],
        applicability_context: Any = None,
    ) -> SharedEvidenceResult:
        try:
            narrowing: KnowledgeNarrowingResult = await narrow_knowledge(
                self._repository, context_state, as_of=request.as_of, policy=self._policy
            )
        except Exception:
            _logger.warning("shared_evidence: narrowing failed", exc_info=True)
            return SharedEvidenceResult(
                availability=EvidenceAvailability.RETRIEVAL_UNAVAILABLE,
                detail="knowledge narrowing could not be completed",
            )

        # POST-6A -- PERMITTED **plus** INDETERMINATE.
        #
        # Narrowing's three buckets are not two. `excluded` is a positive
        # applicability/lifecycle/expiry MISMATCH and stays excluded --
        # that is the access restriction being preserved. `indeterminate`
        # means applicability could not be PROVEN from the context known
        # so far, which is the ordinary state before the engineer has told
        # us the vendor/technology. Treating it as excluded would hide the
        # entire governed corpus from every early turn -- a far worse
        # failure than the over-broad authorization this repair closes.
        #
        # The uncertainty is not discarded: retrieval continues to carry
        # `applicability_outcome` (PARTIAL_MATCH/UNKNOWN) on every item,
        # exactly as it always has, so a caller can still tell proven from
        # unproven applicability.
        permitted_keys = tuple(tuple(key) for key in narrowing.permitted_version_keys)
        indeterminate_keys = tuple(tuple(key) for key in narrowing.indeterminate_version_keys)
        # RETRIEVABLE is the union; AUTHORIZED is only `permitted_keys`.
        # Both sets are carried forward separately and are never merged
        # again -- see `SharedEvidenceResult.permitted_version_keys` /
        # `indeterminate_version_keys` for why.
        version_keys = tuple(dict.fromkeys(permitted_keys + indeterminate_keys))
        if not version_keys:
            # Nothing permitted. WHY matters: an excluded corpus is a
            # policy outcome; an indeterminate one is usually missing user
            # context; an empty one is genuinely nothing applicable.
            if narrowing.excluded_knowledge_ids:
                return SharedEvidenceResult(
                    availability=EvidenceAvailability.POLICY_BLOCKED,
                    detail=f"all candidates excluded: {sorted(set(narrowing.exclusion_reason_counts))}",
                )
            if narrowing.indeterminate_knowledge_ids:
                return SharedEvidenceResult(
                    availability=EvidenceAvailability.MISSING_USER_CONTEXT,
                    detail="applicability could not be determined from the context known so far",
                )
            return SharedEvidenceResult(
                availability=EvidenceAvailability.NO_APPLICABLE_EVIDENCE,
                detail="no governed knowledge is applicable",
            )

        try:
            retrieval_result = await self._retrieve(request, version_keys, applicability_context)
        except Exception:
            _logger.warning("shared_evidence: retrieval failed", exc_info=True)
            return SharedEvidenceResult(
                availability=EvidenceAvailability.RETRIEVAL_UNAVAILABLE,
                permitted_version_keys=permitted_keys,
                indeterminate_version_keys=indeterminate_keys,
                detail="knowledge retrieval could not be completed",
            )

        if not retrieval_result.items:
            return SharedEvidenceResult(
                availability=EvidenceAvailability.NO_APPLICABLE_EVIDENCE,
                permitted_version_keys=permitted_keys,
                indeterminate_version_keys=indeterminate_keys,
                detail="retrieval completed with no matching sections",
            )

        try:
            evidence_set = await self._provenance.build_evidence_set(retrieval_result)
        except Exception:
            _logger.warning("shared_evidence: provenance revalidation failed", exc_info=True)
            return SharedEvidenceResult(
                availability=EvidenceAvailability.RETRIEVAL_UNAVAILABLE,
                degradation=RetrievalDegradation.PROVENANCE_REVALIDATION_FAILED,
                permitted_version_keys=permitted_keys,
                indeterminate_version_keys=indeterminate_keys,
                detail="retrieved evidence could not be revalidated against governed knowledge",
            )

        # FINAL VERSION RE-ASSERTION. Retrieval is already constrained to
        # the permitted versions INSIDE its own query, so this is a
        # verification, not a filter: if anything outside the authorized
        # set reached here, the authorization chain itself is broken and
        # the honest outcome is to fail closed rather than to quietly
        # drop rows and return the rest as though nothing happened.
        permitted = set(version_keys)
        unauthorized = [
            item
            for item in evidence_set.items
            if (item.reference.knowledge_id, item.reference.version_label) not in permitted
        ]
        if unauthorized:
            _logger.error(
                "shared_evidence: %d evidence item(s) outside the authorized version set reached "
                "revalidation -- failing closed",
                len(unauthorized),
            )
            return SharedEvidenceResult(
                availability=EvidenceAvailability.RETRIEVAL_UNAVAILABLE,
                permitted_version_keys=permitted_keys,
                indeterminate_version_keys=indeterminate_keys,
                detail="retrieved evidence did not match the authorized version set",
            )
        items = tuple(evidence_set.items)
        if not items:
            return SharedEvidenceResult(
                availability=EvidenceAvailability.NO_APPLICABLE_EVIDENCE,
                permitted_version_keys=permitted_keys,
                indeterminate_version_keys=indeterminate_keys,
                detail="no retrieved evidence belonged to a retrievable version",
            )

        retrieval_by_identity = {
            (item.knowledge_id, item.version_label, item.section.section_id): item
            for item in retrieval_result.items
        }
        matched_retrieval = tuple(
            retrieval_by_identity[key]
            for item in items
            if (
                key := (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
            )
            in retrieval_by_identity
        )
        return SharedEvidenceResult(
            availability=EvidenceAvailability.AVAILABLE,
            items=items,
            retrieval_items=matched_retrieval,
            evidence_set=evidence_set,
            permitted_version_keys=permitted_keys,
            indeterminate_version_keys=indeterminate_keys,
            detail=(
                f"{len(items)} validated evidence item(s); "
                f"{len([i for i in items if (i.reference.knowledge_id, i.reference.version_label) in set(indeterminate_keys)])} "
                "of unproven applicability"
            ),
        )

    async def _retrieve(
        self,
        request: EvidenceRequest,
        version_keys: Sequence[tuple[str, str]],
        applicability_context: Any,
    ):
        """Delegates to the EXISTING `KnowledgeRetrievalService`, with the
        permitted version set carried INSIDE the query.

        `KnowledgeRetrievalQuery.permitted_version_keys` is applied by
        `retrieve()` before scoring, ranking and the limit -- so a
        disallowed version never becomes a candidate at all. That is the
        authorization guarantee; a Python filter over the ranked result
        would only be a presentation filter, and would let a disallowed
        version displace a permitted one out of the limit before anyone
        looked at it.

        The applicability context is passed through unchanged so
        retrieval's own applicability evaluation and access restrictions
        continue to apply exactly as before.
        """
        from backend.knowledge.domain.applicability import ApplicabilityContext
        from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery

        return await self._retrieval.retrieve(
            KnowledgeRetrievalQuery(
                query_text=request.query_text,
                applicability_context=applicability_context
                if applicability_context is not None
                else ApplicabilityContext(),
                as_of=request.as_of,
                limit=request.limit,
                permitted_version_keys=[tuple(key) for key in version_keys],
            )
        )


def applicability_fingerprint(applicability_context: Any, context_state: Any) -> str:
    """Deterministic digest of the narrowing/applicability inputs a
    retrieval was performed under.

    Part of the cache key, so two otherwise-identical queries evaluated
    against DIFFERENT known facts are never served each other's results.
    Order-independent by construction (both sides are sorted), so the
    same facts always produce the same digest regardless of how they were
    assembled.
    """
    parts: list[str] = []
    dimensions = getattr(applicability_context, "dimensions", None)
    if isinstance(dimensions, dict):
        for key in sorted(dimensions):
            values = dimensions[key]
            rendered = (
                ",".join(sorted(str(v) for v in values))
                if isinstance(values, (list, tuple, set))
                else str(values)
            )
            parts.append(f"a:{key}={rendered}")
    if isinstance(context_state, dict):
        for dimension in sorted(context_state, key=lambda d: getattr(d, "value", str(d))):
            value = context_state[dimension]
            accepted = getattr(value, "accepted", ())
            rendered = ",".join(sorted(str(getattr(a, "canonical_value", a)) for a in accepted))
            state = getattr(getattr(value, "state", None), "value", "")
            parts.append(f"c:{getattr(dimension, 'value', dimension)}={state}:{rendered}")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
