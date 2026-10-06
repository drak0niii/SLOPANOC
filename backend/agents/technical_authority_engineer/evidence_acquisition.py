"""Server-owned evidence acquisition decision for the Technical Authority Engineer.

For the evidence a proposal needs (its EvidenceRequirement), the server -- not the model -- decides
HOW it is acquired, in this order:

    1. already satisfied by trusted evidence of this fault           -> EXISTING_EVIDENCE
    2. obtainable from an approved live operational / context source -> TOOL / ITSM / MONITORING query
    3. a fact the operator can state                                  -> OPERATOR_FACT (clarification)
    4. a governed action of THIS run (ProcedureAction / grounded command), with its CURRENT
       authority: authorized, approval required, authorized by current policy, or blocked
       (applicability unresolved / parameter missing / not authorized)
    5. something the operator can observe without any command         -> MANUAL_OBSERVATION
    otherwise, if discovery FAILED -> retrieval / data-source failure (not a gap)
               if discovery was not performed -> DISCOVERY_INCOMPLETE (not a gap)
               if discovery COMPLETED -> GOVERNED ACQUISITION GAP (no fake manual / operator step)

A candidate never grants authority. Authority comes only from this run's ProcedureAction
resolution + Command Authority + operational policy; the decision is recorded as an AUDIT record
(never read back as authority). Commands, procedures and tools are never evidence and never an
operator fact. Deterministic; no model call; no vendor/fault vocabulary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from backend.agents.technical_authority_engineer.evidence_sources import (
    classify_evidence,
    live_sources_for,
    normalize_capability,
    utc_now,
)
from backend.cases.evidence_identity import EvidenceIdentity, stored_identity
from backend.cases.evidence_model import (
    DISCOVERY_COMPLETE_GAPS,
    AcquisitionCandidate,
    AcquisitionGap,
    AcquisitionHint,
    DescriptionRevision,
    DiscoveryAttempt,
    HintType,
    AcquisitionType,
    AuthorityDecisionRecord,
    AuthorityStatus,
    CandidateAvailability,
    CandidateValidation,
    ConsultedSource,
    EvidenceKind,
    EvidenceRequirement,
    EvidenceSourceRef,
    ExecutionActor,
    GapReason,
    RequirementStatus,
)

EVIDENCE_ACQUISITION_KEY = "evidence_acquisition"
"""Server-owned tool-result / execution-record key (never model-supplied)."""
SUPPORTING_EVIDENCE_KEY = "supporting_evidence"
"""Server-owned: canonical ids (display only) of the SELECTED evidence that materially grounds what is presented."""
SUPPORTING_EVIDENCE_IDENTITIES_KEY = "supporting_evidence_identities"
"""Server-owned: the same items as structured identities ({knowledge_id, version_label, section_id});
the only form ever read back (display strings are never parsed)."""

_MAX_DESCRIPTION = 300
_MAX_CONTENT = 4000

# A command / procedure / tool / approval is an acquisition mechanism or authority control, never
# evidence and never an operator fact.
_ACQUISITION_MECHANISM = re.compile(
    r"\b(?:commands?|cmds?|procedures?|mops?|sops?|runbooks?|scripts?|tools?|methods?|approvals?|authori[sz]ations?|cli)\b",
    re.IGNORECASE,
)
_COMMAND_REFERENCE = re.compile(r"\b(?:commands?|cmds?|cli)\b", re.IGNORECASE)
"""A command-less step whose own text refers to a command's output cannot be a manual observation."""
_COMMAND_FOR = re.compile(
    r"\b(?:commands?|cmds?|procedures?|methods?|tools?|way)\b\s+(?:\w+\s+){0,3}?(?:to|for)\s+(?:retrieve|get|obtain|check|show|display|see|view|collect|read|query)?\s*(.+)",
    re.IGNORECASE,
)
_KIND_ALIASES = {
    "operator_fact": EvidenceKind.OPERATOR_FACT,
    "fact": EvidenceKind.OPERATOR_FACT,
    "diagnostic_result": EvidenceKind.DIAGNOSTIC_RESULT,
    "result": EvidenceKind.DIAGNOSTIC_RESULT,
    "observation": EvidenceKind.OBSERVATION,
    "manual_observation": EvidenceKind.OBSERVATION,
    "live_operational_context": EvidenceKind.LIVE_OPERATIONAL_CONTEXT,
    "context": EvidenceKind.LIVE_OPERATIONAL_CONTEXT,
}
_ACQUIRABLE_KINDS = frozenset({EvidenceKind.DIAGNOSTIC_RESULT, EvidenceKind.LIVE_OPERATIONAL_CONTEXT})


class AcquisitionOutcome:
    PRESENT = "present"
    """A usable acquisition (authorized governed action, manual observation, operator fact) or a
    governed action whose CURRENT authority is blocked (handled by its existing path)."""
    SATISFIED = "satisfied"
    """Acquired by the server itself (existing evidence / approved live source)."""
    GAP = "gap"
    """Discovery completed; no legitimate acquisition method."""
    FAILED = "failed"
    """Discovery or a source failed / was not performed: nothing can be concluded."""
    BLOCKED = "blocked"
    """A method exists but is blocked for a non-gap reason (applicability unresolved): the
    existing clarification path answers it."""


@dataclass
class GovernedActionState:
    """What THIS run established about a governed action for the step (never a previous run)."""

    procedure_action_id: Optional[str] = None
    command_template: Optional[str] = None
    canonical_source_id: Optional[str] = None
    source_identity: Optional[EvidenceIdentity] = None
    applicability: Optional[str] = None
    parameters: list[str] = field(default_factory=list)
    authorized_command: Optional[str] = None
    blocking_reason: Optional[GapReason] = None


@dataclass
class DiscoveryState:
    governed_search_performed: bool = False
    searches: list[dict[str, Any]] = field(default_factory=list)
    available: list[tuple[str, Optional[str]]] = field(default_factory=list)
    """(canonical display id, applicability outcome) of every AVAILABLE governed item of this run."""
    available_identities: list[tuple[EvidenceIdentity, Optional[str]]] = field(default_factory=list)
    """(structured identity, applicability outcome) of the same items: the only form compared."""
    selected: list[str] = field(default_factory=list)
    rejected_actions: dict[str, str] = field(default_factory=dict)
    """procedure_action_id -> why THIS run's selected evidence positively no longer supports it."""
    selected_actions: list[str] = field(default_factory=list)
    """Valid diagnostic-read ProcedureActions of THIS run's SELECTED, approved, MATCH evidence that the
    fault has not performed (gap_recovery.viable_governed_alternatives over SELECTED evidence only)."""
    actions_declined: bool = False
    """The specialist was offered `selected_actions` for this need and explicitly chose none."""

    @property
    def search_failed(self) -> bool:
        return any(s.get("status") == "error" for s in self.searches) and not any(s.get("status") == "ok" for s in self.searches)


@dataclass
class AcquisitionDecision:
    requirement: EvidenceRequirement
    outcome: str
    gap: Optional[AcquisitionGap] = None
    acquired: Optional[tuple[EvidenceSourceRef, str]] = None
    response_text: Optional[str] = None
    attempt: Optional[DiscoveryAttempt] = None
    gap_is_new: bool = True
    continuity: Optional[str] = None
    """How the requirement was matched to an existing open one (None = new requirement)."""
    repeated: bool = False
    """The same open gap re-proposed with nothing materially new: reused, no new discovery attempt."""

    def view(self) -> dict[str, Any]:
        selected = self.requirement.selected_acquisition
        return {
            "outcome": self.outcome,
            "requirement": {
                "requirement_id": self.requirement.requirement_id,
                "kind": self.requirement.kind.value,
                "description": self.requirement.description,
                "status": self.requirement.status.value,
                "blocking_reason": self.requirement.blocking_reason.value if self.requirement.blocking_reason else None,
            },
            "candidates": [
                {
                    "acquisition_id": c.acquisition_id,
                    "acquisition_type": c.acquisition_type.value,
                    "availability": c.availability.value,
                    "blocking_reason": c.blocking_reason.value if c.blocking_reason else None,
                    "source": c.source.source_id if c.source else c.canonical_source_id,
                    "procedure_action_id": c.procedure_action_id,
                    "validation": c.validation.value,
                }
                for c in self.requirement.acquisition_candidates
            ],
            "selected_acquisition": selected.acquisition_type.value if selected else None,
            "gap_id": self.gap.gap_id if self.gap else None,
            "gap_reason": self.gap.gap_reason.value if self.gap else None,
            "gap_is_new": self.gap_is_new if self.gap else None,
            "gap_repeated": self.repeated if self.gap else None,
            "discovery_attempts": len(self.gap.all_attempts()) + (1 if not self.gap_is_new and self.attempt is not None else 0) if self.gap else None,
            "requirement_continuity": self.continuity,
            "acquisition_hints": [h.value for h in self.requirement.acquisition_hints],
            "response_text": self.response_text,
        }


def _kind(value: Any) -> Optional[EvidenceKind]:
    return _KIND_ALIASES.get(str(value or "").strip().casefold())


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())[:_MAX_DESCRIPTION]


_OUTPUT_OF = re.compile(r"\b(?:output|outputs|result|results)\s+(?:of|from)\b", re.IGNORECASE)


def is_acquisition_mechanism_item(text: str) -> bool:
    """A missing-information item that asks for a command / procedure / tool / approval itself:
    the system's acquisition or authority problem, never an operator fact. An item asking for the
    OUTPUT / RESULT of a command is evidence, not a mechanism."""
    return bool(_ACQUISITION_MECHANISM.search(text or "")) and not _OUTPUT_OF.search(text or "")


def split_missing_information(items: list[Any]) -> tuple[list[str], list[str]]:
    """(operator-facing evidence items, system acquisition/governance items)."""
    evidence: list[str] = []
    system: list[str] = []
    for item in items or []:
        if not isinstance(item, str) or not item.strip():
            continue
        (system if is_acquisition_mechanism_item(item) else evidence).append(item)
    return evidence, system


def evidence_from_mechanism_item(text: str) -> Optional[str]:
    """'The specific command to retrieve detailed node synchronization status' -> 'detailed node
    synchronization status' (the evidence the mechanism was meant to obtain)."""
    match = _COMMAND_FOR.search(text or "")
    return _clean(match.group(1).rstrip(" .")) if match and match.group(1).strip() else None


# ---------------------------------------------------------------------------------------------
# Server-owned requirement identity (generic: no domain vocabulary)
# ---------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")
_GENERIC_TERMS = frozenset(
    """a an and are as at be by for from in into is it its of on or the to with via
    output outputs showing show shows current currently detail details information info data status state states
    result results check checks checking value values including include includes complete exact specific detailed required
    need needed list report reported any all each their this that these those which what how get obtain retrieve
    provide overall level used use using please also
    node nodes active network system systems device devices element elements site sites entire whole""".split()
)
_MECHANISM_WORDS = frozenset("command commands cmd cmds procedure procedures tool tools method methods script scripts runbook runbooks mop mops sop sops cli".split())
_OUTPUT_OF_MECHANISM = re.compile(
    r"\b(?:output|outputs|result|results)\s+(?:of|from)\s+(?:the\s+|a\s+|an\s+|any\s+)?(?P<subject>.+?)\s+"
    r"(?:check\s+)?(?:commands?|cmds?|procedures?|tools?|scripts?)\b",
    re.IGNORECASE,
)
_SEGMENT_SPLIT = re.compile(r"\s*(?:[,;]|\bincluding\b|\bsuch\s+as\b)\s*", re.IGNORECASE)
_MIN_SHARED_TOKENS = 2
_MIN_OVERLAP = 0.5


def _stem(token: str) -> str:
    """Plural folding only ("sources" -> "source", "alarms" -> "alarm"); conservative on purpose."""
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def semantic_tokens(*texts: Optional[str]) -> list[str]:
    """Content tokens identifying an evidence need: generic words, acquisition-mechanism words and
    command names (backticked) are excluded; simple inflections are folded. Domain-neutral."""
    out: list[str] = []
    for text in texts:
        cleaned = re.sub(r"`[^`]*`", " ", str(text or ""))
        for raw in _TOKEN.findall(cleaned):
            for part in [raw, *re.split(r"[-_]", raw)] if re.search(r"[-_]", raw) else [raw]:
                lowered = part.casefold()
                if lowered in _GENERIC_TERMS or lowered in _MECHANISM_WORDS:
                    continue
                token = _stem(lowered)
                if len(token) < 2 or token in _GENERIC_TERMS or token in _MECHANISM_WORDS:
                    continue
                if token not in out:
                    out.append(token)
    return out


def evidence_description(text: str) -> str:
    """WHAT is needed, never WHICH command produces it: "Output of the X check command, including
    the exact command used" -> "X". Command names, mechanism clauses and parentheticals naming a
    mechanism are removed; text without mechanism wording is returned unchanged."""
    original = _clean(text)
    if not original or not (_ACQUISITION_MECHANISM.search(original) or "`" in original):
        return original
    working = re.sub(r"`[^`]*`", "", original)
    working = re.sub(r"\([^)]*\)", lambda m: "" if _ACQUISITION_MECHANISM.search(m.group(0)) else m.group(0), working)
    kept: list[str] = []
    for segment in _SEGMENT_SPLIT.split(working):
        segment = segment.strip(" .")
        if not segment:
            continue
        if not _ACQUISITION_MECHANISM.search(segment):
            kept.append(segment)
            continue
        match = _OUTPUT_OF_MECHANISM.search(segment)
        if match and match.group("subject").strip():
            kept.append(match.group("subject").strip(" ."))
    result = _clean(", ".join(kept))
    return result or _clean(re.sub(r"`[^`]*`", "", original))


def _semantic_key(capability: Optional[str], tokens: list[str]) -> Optional[str]:
    if capability:
        return f"cap:{capability}"
    return ("desc:" + "_".join(sorted(tokens)[:8])) if tokens else None


def _with_identity(requirement: EvidenceRequirement, ref: Optional[str] = None) -> EvidenceRequirement:
    requirement.semantic_tokens = semantic_tokens(requirement.description, (requirement.capability or "").replace("_", " "))
    requirement.semantic_key = _semantic_key(requirement.capability, requirement.semantic_tokens)
    requirement._proposed_ref = ref
    return requirement


def proposed_ref(requirement: EvidenceRequirement) -> Optional[str]:
    return requirement._proposed_ref


_ACQUIRABLE = frozenset({"diagnostic_result", "live_operational_context"})


def _kinds_compatible(a: EvidenceKind, b: EvidenceKind) -> bool:
    return a is b or {a.value, b.value} <= _ACQUIRABLE


def _overlap(a: list[str], b: list[str]) -> tuple[int, float]:
    shared = set(a) & set(b)
    if not shared:
        return 0, 0.0
    return len(shared), len(shared) / max(1, min(len(set(a)), len(set(b))))


def _requirement_tokens(requirement: EvidenceRequirement) -> list[str]:
    """Server tokens of an existing requirement (derived on the fly for one persisted before
    semantic identity existed -- never written back unless the requirement is reused)."""
    return requirement.semantic_tokens or semantic_tokens(requirement.description, (requirement.capability or "").replace("_", " "))


def match_open_requirement(
    progression: Any, candidate: EvidenceRequirement, *, mechanism_followup: bool = False
) -> tuple[Optional[EvidenceRequirement], Optional[str]]:
    """The OPEN requirement of the same fault that this proposal continues, or None.
    Server-owned continuity, in order:
      1. a referenced requirement -- accepted only if it exists, belongs to this fault, is open, has a
         compatible kind and is semantically compatible (same capability, or enough shared content);
      2. the same normalized capability;
      3. enough shared content tokens (>= 2 shared and >= 50% of the smaller need);
      4. a requirement derived only from "no method to obtain X" (mechanism_followup: no typed content
         of its own), when exactly ONE acquirable need with an open gap is outstanding on the fault.
    Never another fault's requirement; never a satisfied one. Returns (requirement, rule)."""
    if progression is None:
        return None, None
    open_requirements = [r for r in progression.open_requirements(candidate.fault_id) if _kinds_compatible(r.kind, candidate.kind)]
    if not open_requirements:
        return None, None
    tokens = candidate.semantic_tokens

    def _strong(r: EvidenceRequirement) -> bool:
        # Enough shared content: >= 2 tokens (or the whole of a one-token need) and >= 50% of the smaller need.
        existing_tokens = _requirement_tokens(r)
        shared, ratio = _overlap(tokens, existing_tokens)
        needed = min(_MIN_SHARED_TOKENS, len(set(tokens)), len(set(existing_tokens)))
        return shared > 0 and shared >= needed and ratio >= _MIN_OVERLAP

    def _same_capability(r: EvidenceRequirement) -> bool:
        return bool(candidate.capability and r.capability and candidate.capability == r.capability)

    ref = proposed_ref(candidate)
    if ref:
        referenced = next((r for r in open_requirements if r.requirement_id == ref), None)
        if referenced is not None and (_same_capability(referenced) or _strong(referenced) or (not tokens and not candidate.capability)):
            return referenced, "validated_reference"
    for r in open_requirements:
        if _same_capability(r):
            return r, "same_capability"
    scored = [(_overlap(tokens, _requirement_tokens(r))[0], r) for r in open_requirements if _strong(r)]
    if scored:
        return max(scored, key=lambda item: item[0])[1], "shared_content"
    if mechanism_followup:
        acquirable = [r for r in open_requirements if r.kind.value in _ACQUIRABLE and progression.open_gap(r.requirement_id) is not None]
        if len(acquirable) == 1:
            return acquirable[0], "single_open_need_followup"
    return None, None


def refine_requirement(existing: EvidenceRequirement, candidate: EvidenceRequirement, run_id: Optional[str]) -> EvidenceRequirement:
    """Continue the existing requirement with this turn's wording: identity never changes. A new
    wording is kept in history; it replaces the description only when it is a strict refinement
    (covers every existing content token and adds detail). A missing capability is adopted."""
    current = _requirement_tokens(existing)
    if candidate.description and _clean(candidate.description).casefold() != _clean(existing.description).casefold():
        if not any(_clean(r.description).casefold() == _clean(candidate.description).casefold() for r in existing.description_history):
            existing.description_history = [*existing.description_history, DescriptionRevision(description=candidate.description, run_id=run_id)][-10:]
        if set(current) < set(candidate.semantic_tokens):
            existing.description = candidate.description
    if not existing.capability and candidate.capability:
        existing.capability = candidate.capability
    existing.semantic_tokens = list(dict.fromkeys([*current, *candidate.semantic_tokens]))
    existing.semantic_key = existing.semantic_key or _semantic_key(existing.capability, existing.semantic_tokens)
    existing.updated_at = utc_now()
    return existing


# ---------------------------------------------------------------------------------------------
# Operator acquisition hints (untrusted; never authority)
# ---------------------------------------------------------------------------------------------

_HINT_STOPWORDS = frozenset(
    "the a an it that this them those these something anything what which how so to for on in and or then now again "
    "command cmd commands check me you your my our their procedure tool method way".split()
)
_HINT_PATTERNS: list[tuple[re.Pattern, HintType]] = [
    (re.compile(r"`([^`]{2,80})`"), HintType.COMMAND_OR_METHOD),
    (re.compile(r"\b(?:check|query|look\s+(?:in|at)|consult|use)\s+([A-Za-z][\w\-.]{1,40})\s+instead\b", re.IGNORECASE), HintType.TOOL_OR_SOURCE),
    (re.compile(
        r"\b(?:i|we)\s+(?:normally|usually|typically|always|often|sometimes|generally|would)?\s*(?:use|run|type|execute|enter)\s+"
        r"(?:the\s+)?(?:command\s+)?([A-Za-z][\w\-./:=|]*(?:\s+[A-Za-z0-9][\w\-./:=|]*){0,2})",
        re.IGNORECASE,
    ), HintType.COMMAND_OR_METHOD),
    (re.compile(r"\b(?:the\s+)?(?:command|cmd)\s+(?:is|was)\s+([A-Za-z][\w\-./:=|]*(?:\s+[A-Za-z0-9][\w\-./:=|]*){0,2})", re.IGNORECASE), HintType.COMMAND_OR_METHOD),
]
_MAX_HINT_TEXT = 300


def detect_acquisition_hint(text: str) -> Optional[tuple[str, HintType]]:
    """An operator-proposed acquisition method / source in a short conversational message ("I
    normally use X", "the command is X", "check X instead", a backticked command). Untrusted."""
    message = (text or "").strip()
    if not message or len(message) > _MAX_HINT_TEXT or "\n" in message.strip():
        return None
    for pattern, hint_type in _HINT_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        words = [w for w in match.group(1).strip(" .,;:!?").split() if w]
        while words and words[-1].casefold() in _HINT_STOPWORDS:
            words.pop()
        if not words or words[0].casefold() in _HINT_STOPWORDS:
            continue
        return " ".join(words)[:80], hint_type
    return None


def make_hint(requirement: EvidenceRequirement, value: str, hint_type: HintType, run_id: Optional[str]) -> AcquisitionHint:
    return AcquisitionHint(requirement_id=requirement.requirement_id, value=value, hint_type=hint_type, run_id=run_id)


def hint_target(progression: Any, fault_id: str) -> Optional[EvidenceRequirement]:
    """The open acquirable requirement an operator hint refers to: the most recent one with an open
    gap, else the most recent open acquirable one (never another fault's)."""
    if progression is None:
        return None
    open_requirements = [r for r in progression.open_requirements(fault_id) if r.kind.value in _ACQUIRABLE]
    with_gap = [r for r in open_requirements if progression.open_gap(r.requirement_id) is not None]
    return (with_gap or open_requirements or [None])[0]


def requirement_from_step(step: Mapping[str, Any], fault_id: str, *, attempted_command: bool) -> EvidenceRequirement:
    """The step's EvidenceRequirement. Typed fields win; untyped legacy proposals are inferred
    conservatively: a step carrying (or having attempted) a command, or whose own text refers to a
    command's output, needs a DIAGNOSTIC_RESULT; any other command-less step keeps the legacy
    meaning of a manual observation."""
    proposal = step.get("evidence_requirement") if isinstance(step.get("evidence_requirement"), Mapping) else {}
    kind = _kind(proposal.get("kind"))
    acquisition = str(step.get("acquisition") or "").strip().casefold()
    if kind is None:
        if step.get("command") or step.get("procedure_action_id") or attempted_command:
            kind = EvidenceKind.DIAGNOSTIC_RESULT
        elif acquisition == "operator_fact":
            kind = EvidenceKind.OPERATOR_FACT
        elif acquisition in ("none", "governed_action") or _COMMAND_REFERENCE.search(" ".join(str(step.get(f) or "") for f in ("action", "expected_evidence"))):
            kind = EvidenceKind.DIAGNOSTIC_RESULT
        else:
            kind = EvidenceKind.OBSERVATION
    description = evidence_description(_clean(proposal.get("description") or step.get("expected_evidence") or step.get("action")))
    return _with_identity(EvidenceRequirement(
        fault_id=fault_id, kind=kind, description=description or "diagnostic evidence",
        capability=normalize_capability(proposal.get("capability")) or None,
    ), ref=str(proposal.get("requirement_ref") or "").strip() or None)


def requirement_from_proposal(proposal: Mapping[str, Any], fault_id: str) -> Optional[EvidenceRequirement]:
    kind = _kind(proposal.get("kind"))
    raw = _clean(proposal.get("description"))
    if kind is None or not raw or is_acquisition_mechanism_item(raw):
        return None
    description = evidence_description(raw)
    if not description:
        return None
    return _with_identity(
        EvidenceRequirement(fault_id=fault_id, kind=kind, description=description, capability=normalize_capability(proposal.get("capability")) or None),
        ref=str(proposal.get("requirement_ref") or "").strip() or None,
    )


def _existing_evidence(progression: Any, requirement: EvidenceRequirement) -> Optional[EvidenceRequirement]:
    if not requirement.capability or progression is None:
        return None
    for previous in reversed(progression.requirements_for(requirement.fault_id)):
        if previous.status is RequirementStatus.SATISFIED and previous.capability == requirement.capability and previous.satisfied_by:
            return previous
    return None


def _governed_candidate(
    requirement: EvidenceRequirement, action: GovernedActionState, run_id: Optional[str] = None
) -> Optional[AcquisitionCandidate]:
    if not (action.procedure_action_id or action.authorized_command or action.blocking_reason):
        return None
    blocking = None if action.authorized_command else (action.blocking_reason or GapReason.ACTION_NOT_AUTHORIZED)
    if action.authorized_command:
        validation = CandidateValidation.AUTHORIZED_CURRENT_RUN
    elif blocking is GapReason.APPLICABILITY_UNRESOLVED:
        validation = CandidateValidation.BLOCKED_APPLICABILITY
    else:
        validation = CandidateValidation.REJECTED_CURRENT_RUN
    return AcquisitionCandidate(
        requirement_id=requirement.requirement_id,
        acquisition_type=AcquisitionType.GOVERNED_ACTION,
        procedure_action_id=action.procedure_action_id,
        command_template=action.command_template,
        canonical_source_id=action.canonical_source_id,
        source_identity=action.source_identity,
        applicability=action.applicability,
        parameters=list(action.parameters),
        availability=CandidateAvailability.AVAILABLE if action.authorized_command else CandidateAvailability.BLOCKED,
        blocking_reason=blocking,
        execution_actor=ExecutionActor.OPERATOR,
        validation=validation,
        validated_run_id=run_id,
        validation_reason=None if action.authorized_command else (blocking.value if blocking else None),
    )


# ---------------------------------------------------------------------------------------------
# Acquisition continuity: a known governed method is never erased by omission
# ---------------------------------------------------------------------------------------------


def _norm_template(value: Optional[str]) -> str:
    return " ".join(str(value or "").split()).casefold()


def same_governed_identity(a: AcquisitionCandidate, b: AcquisitionCandidate) -> bool:
    """Structural identity of two governed candidates: the deterministic ProcedureAction id when
    both carry one, else the exact structured source identity + normalized template (a canonical
    display string is never compared)."""
    if a.acquisition_type is not AcquisitionType.GOVERNED_ACTION or b.acquisition_type is not AcquisitionType.GOVERNED_ACTION:
        return False
    if a.procedure_action_id and b.procedure_action_id:
        return a.procedure_action_id == b.procedure_action_id
    return bool(
        a.source_identity is not None and a.source_identity == b.source_identity
        and _norm_template(a.command_template) and _norm_template(a.command_template) == _norm_template(b.command_template)
    )


def structural_governed_candidates(requirement: EvidenceRequirement) -> list[AcquisitionCandidate]:
    """The requirement's persisted governed candidates that carry a structural identity."""
    return [
        c for c in requirement.acquisition_candidates
        if c.acquisition_type is AcquisitionType.GOVERNED_ACTION and (c.procedure_action_id or (c.source_identity is not None and c.command_template))
    ]


def _carried_forward(prior: AcquisitionCandidate, discovery: Optional[DiscoveryState], run_id: Optional[str]) -> AcquisitionCandidate:
    """A known governed candidate this run did not re-validate: kept (identity only, zero authority)
    with what THIS run's discovery says about its source."""
    carried = prior.model_copy(deep=True)
    carried.availability = CandidateAvailability.BLOCKED
    carried.validated_run_id = run_id
    outcome = None
    if discovery is not None and prior.source_identity is not None:
        outcome = next((str(o or "").lower() for identity, o in discovery.available_identities if identity == prior.source_identity), None)
    if discovery is not None and prior.procedure_action_id in discovery.rejected_actions:
        carried.validation = CandidateValidation.REJECTED_CURRENT_RUN
        carried.validation_reason = discovery.rejected_actions[prior.procedure_action_id]
    elif discovery is None or not discovery.governed_search_performed or discovery.search_failed:
        carried.validation = CandidateValidation.KNOWN_STRUCTURAL
        carried.validation_reason = "not re-validated in this run (no completed governed discovery)"
        if discovery is not None:
            carried.blocking_reason = GapReason.DISCOVERY_INCOMPLETE
    elif prior.source_identity is None:
        # A legacy record without a structured source identity cannot be located in this run's
        # discovery (its display string is never parsed or compared): identity only, as before.
        carried.validation = CandidateValidation.KNOWN_STRUCTURAL
        carried.validation_reason = "not re-validated in this run (no structured source identity)"
    elif outcome == "match":
        carried.validation = CandidateValidation.AVAILABLE_CURRENT_RUN
        carried.blocking_reason = GapReason.ACTION_NOT_AUTHORIZED
        carried.validation_reason = "source AVAILABLE with applicability MATCH in this run but not selected and resolved"
    elif outcome in ("unknown", "partial_match"):
        carried.validation = CandidateValidation.BLOCKED_APPLICABILITY
        carried.blocking_reason = GapReason.APPLICABILITY_UNRESOLVED
        carried.validation_reason = f"source applicability {outcome} in this run"
    else:
        carried.validation = CandidateValidation.REJECTED_CURRENT_RUN
        carried.validation_reason = (
            f"source applicability {outcome} in this run" if outcome else "source not returned by this run's governed discovery"
        )
    return carried


def merge_governed_candidates(
    prior: list[AcquisitionCandidate],
    current: list[AcquisitionCandidate],
    discovery: Optional[DiscoveryState] = None,
    run_id: Optional[str] = None,
) -> list[AcquisitionCandidate]:
    """This run's candidates plus every previously known governed candidate this run did not
    re-evaluate. A model omission adds nothing and removes nothing: the known structural method is
    kept (never as authority)."""
    merged = list(current)
    for p in prior:
        if any(same_governed_identity(p, c) for c in merged):
            continue
        merged.append(_carried_forward(p, discovery, run_id))
    return merged


def _classify_gap(discovery: DiscoveryState, live_failure: Optional[GapReason]) -> GapReason:
    if live_failure is not None:
        return live_failure
    if discovery.search_failed:
        return GapReason.RETRIEVAL_FAILED
    if not discovery.governed_search_performed:
        return GapReason.DISCOVERY_INCOMPLETE
    if not discovery.available and not discovery.selected:
        return GapReason.NO_RELEVANT_EVIDENCE_FOUND
    outcomes = {str(outcome or "").lower() for _, outcome in discovery.available}
    if "match" in outcomes:
        return GapReason.NO_APPROVED_ACQUISITION_ACTION
    if outcomes & {"unknown", "partial_match"}:
        return GapReason.APPLICABILITY_UNRESOLVED
    return GapReason.NO_APPLICABLE_PROCEDURE


_GAP_TEXT = {
    GapReason.NO_APPROVED_ACQUISITION_ACTION: (
        "No approved governed procedure or available operational capability currently provides a validated method "
        "for obtaining the required evidence: {description}."
    ),
    GapReason.NO_APPLICABLE_PROCEDURE: (
        "No governed procedure applicable to this context provides a validated method for obtaining the required "
        "evidence: {description}."
    ),
    GapReason.NO_RELEVANT_EVIDENCE_FOUND: (
        "Governed knowledge contains no procedure covering the required evidence: {description}."
    ),
}
_FAILURE_TEXT = {
    GapReason.RETRIEVAL_FAILED: "The required evidence ({description}) could not be acquired: governed knowledge retrieval failed in this turn. No operational step is presented; please retry.",
    GapReason.DATA_SOURCE_UNAVAILABLE: "The required evidence ({description}) could not be acquired: the operational data source is currently unavailable. No operational step is presented; please retry.",
    GapReason.TOOL_UNAVAILABLE: "The required evidence ({description}) could not be acquired: the acquisition tool is currently unavailable.",
    GapReason.DISCOVERY_INCOMPLETE: "The required evidence ({description}) has no established acquisition method yet: governed knowledge was not searched for one in this turn. No operational step is presented.",
}


def gap_response(requirement: EvidenceRequirement, reason: GapReason, attempts: int = 1, repeated: bool = False) -> str:
    lines = [_GAP_TEXT[reason].format(description=requirement.description.rstrip("."))]
    if repeated:
        lines.append(
            "This evidence requirement was already found to have no governed acquisition method, and nothing has "
            "changed since (no new result, source or context): the same gap stands and discovery is not repeated."
        )
    elif attempts > 1:
        hints = [h.value for h in requirement.acquisition_hints]
        note = f"This is the same open evidence requirement; discovery has now been attempted {attempts} times."
        if hints:
            note += (
                " Your suggestion (" + ", ".join(hints) + ") was used only to search governed knowledge; it is not an "
                "approved method and cannot be presented or executed unless an approved procedure provides it."
            )
        lines.append(note)
    lines.append(
        "No command is provided, and none is needed from you: this has been recorded as a governed acquisition gap "
        "for knowledge or tool onboarding. It can be escalated to an SME if the investigation cannot continue without it."
    )
    return "\n\n".join(lines)


def failure_response(requirement: EvidenceRequirement, reason: GapReason) -> str:
    return _FAILURE_TEXT.get(reason, _FAILURE_TEXT[GapReason.RETRIEVAL_FAILED]).format(description=requirement.description.rstrip("."))


def acquired_response(requirement: EvidenceRequirement, source: EvidenceSourceRef, content: str) -> str:
    when = f", observed {source.observed_at.isoformat(timespec='seconds')}" if source.observed_at else ""
    return (
        f"Obtained {requirement.description.rstrip('.')} from {source.source_type.value} ({source.source_id}{when}); "
        f"no command needs to be run for it.\n\n{content[:_MAX_CONTENT]}"
    )


def materially_new_context(
    gap: AcquisitionGap, requirement: EvidenceRequirement, discovery: DiscoveryState, progression: Any, run_id: Optional[str]
) -> bool:
    """Whether a re-proposed gapped requirement may be reconsidered: something material changed since
    its latest discovery attempt -- a new TRUSTED result in this fault, a new operator acquisition
    hint, or a governed source (exact identity + applicability) this run consulted that no earlier
    attempt did. A gap gets at most one discovery attempt per run. Deterministic; wording never counts."""
    attempts = gap.all_attempts()
    if run_id and any(a.run_id == run_id for a in attempts):
        return False
    since = max((a.at for a in attempts), default=gap.recorded_at)
    steps = progression.steps_for(gap.fault_id) if progression is not None else []
    if any(s.result is not None and s.result.recorded_at > since for s in steps):
        return True
    if any(h.created_at > since for h in requirement.acquisition_hints):
        return True
    seen = {(c.identity.key, c.applicability) for a in attempts for c in a.consulted}
    return any((identity.key, outcome) not in seen for identity, outcome in discovery.available_identities)


async def decide_acquisition(
    requirement: EvidenceRequirement,
    *,
    progression: Any,
    action: GovernedActionState,
    discovery: DiscoveryState,
    run_id: Optional[str],
    step_id: Optional[str] = None,
    context: Optional[Mapping[str, Any]] = None,
    manual_allowed: bool = True,
) -> AcquisitionDecision:
    """See module docstring. Mutates and returns the requirement inside the decision (not yet
    added to the progression -- the caller persists it with the step it belongs to)."""
    candidates: list[AcquisitionCandidate] = []
    live_failure: Optional[GapReason] = None
    # Governed methods already known for this requirement (structural identity, zero authority):
    # this run may re-validate them or add new ones; omitting them never removes them.
    prior_governed = structural_governed_candidates(requirement)

    def _select(candidate: AcquisitionCandidate) -> None:
        requirement.selected_acquisition_id = candidate.acquisition_id

    def _assign() -> None:
        requirement.acquisition_candidates = merge_governed_candidates(prior_governed, candidates, discovery, run_id)

    previous = _existing_evidence(progression, requirement) if requirement.kind in _ACQUIRABLE_KINDS else None
    if previous is not None:
        candidate = AcquisitionCandidate(
            requirement_id=requirement.requirement_id, acquisition_type=AcquisitionType.EXISTING_EVIDENCE,
            source=previous.satisfied_by[-1], availability=CandidateAvailability.AVAILABLE,
        )
        candidates.append(candidate)

    if requirement.kind in _ACQUIRABLE_KINDS and previous is None:
        for source in live_sources_for(requirement.capability):
            candidate = AcquisitionCandidate(
                requirement_id=requirement.requirement_id, acquisition_type=source.acquisition_type,
                source=source.source_ref(), execution_actor=ExecutionActor.ANOC,
            )
            candidates.append(candidate)
            try:
                result = await source.query(requirement.capability or "", dict(context or {}))
            except Exception:
                result = None
            if result is None or result.status in ("unavailable", "failed"):
                # "unavailable": the connector / tool cannot be reached; "failed" or an exception:
                # the data source answered with an error. Neither is a knowledge gap.
                candidate.availability = CandidateAvailability.UNAVAILABLE
                candidate.blocking_reason = (
                    GapReason.TOOL_UNAVAILABLE if result is not None and result.status == "unavailable" else GapReason.DATA_SOURCE_UNAVAILABLE
                )
                live_failure = live_failure or candidate.blocking_reason
                continue
            if result.status == "ok" and result.content.strip():
                ref = source.source_ref(observed_at=result.observed_at or utc_now(), provenance=result.provenance)
                candidate.source = ref
                _select(candidate)
                _assign()
                requirement.record_authority(AuthorityDecisionRecord(
                    acquisition_id=candidate.acquisition_id, status=AuthorityStatus.NOT_REQUIRED,
                    reason="read-only query of an approved live context source", execution_actor=ExecutionActor.ANOC, run_id=run_id,
                ))
                requirement.status = RequirementStatus.SATISFIED
                requirement.satisfied_by = [ref]
                requirement.satisfied_at = utc_now()
                return AcquisitionDecision(requirement, AcquisitionOutcome.SATISFIED, acquired=(ref, result.content),
                                           response_text=acquired_response(requirement, ref, result.content))
            candidate.availability = CandidateAvailability.UNAVAILABLE
            candidate.blocking_reason = GapReason.NO_RELEVANT_EVIDENCE_FOUND

    if previous is not None:
        _select(candidates[0])
        _assign()
        requirement.status = RequirementStatus.SATISFIED
        requirement.satisfied_by = list(previous.satisfied_by)
        requirement.satisfied_at = utc_now()
        return AcquisitionDecision(requirement, AcquisitionOutcome.SATISFIED, acquired=(previous.satisfied_by[-1], ""))

    if requirement.kind is EvidenceKind.OPERATOR_FACT:
        candidate = AcquisitionCandidate(requirement_id=requirement.requirement_id, acquisition_type=AcquisitionType.OPERATOR_FACT,
                                         execution_actor=ExecutionActor.OPERATOR)
        candidates.append(candidate)
        _select(candidate)
        _assign()
        return AcquisitionDecision(requirement, AcquisitionOutcome.PRESENT)

    governed = _governed_candidate(requirement, action, run_id)
    if governed is not None:
        known = next((p for p in prior_governed if same_governed_identity(p, governed)), None)
        if known is not None:
            governed.acquisition_id = known.acquisition_id  # the same structural method keeps its identity
        candidates.append(governed)
        _select(governed)
        _assign()
        requirement.blocking_reason = governed.blocking_reason
        return AcquisitionDecision(requirement, AcquisitionOutcome.PRESENT)

    if requirement.kind is EvidenceKind.OBSERVATION and manual_allowed:
        candidate = AcquisitionCandidate(requirement_id=requirement.requirement_id, acquisition_type=AcquisitionType.MANUAL_OBSERVATION,
                                         execution_actor=ExecutionActor.OPERATOR)
        candidates.append(candidate)
        _select(candidate)
        _assign()
        return AcquisitionDecision(requirement, AcquisitionOutcome.PRESENT)

    _assign()
    carried = [
        c for c in requirement.acquisition_candidates
        if all(c is not x for x in candidates) and c.validation is not CandidateValidation.REJECTED_CURRENT_RUN
    ]
    if carried and live_failure is None:
        # A governed method for this requirement is already known and this run's discovery does not
        # contradict it: it was simply not (re)selected and resolved here. That is not a governed
        # acquisition gap; it stays blocked until a run re-selects and re-authorizes it.
        _select(carried[0])
        requirement.blocking_reason = (
            GapReason.DISCOVERY_INCOMPLETE if not discovery.governed_search_performed
            else carried[0].blocking_reason or GapReason.ACTION_NOT_AUTHORIZED
        )
        return AcquisitionDecision(requirement, AcquisitionOutcome.BLOCKED)
    if requirement.kind in _ACQUIRABLE_KINDS and discovery.selected_actions and not discovery.actions_declined and live_failure is None:
        # Valid governed diagnostic actions exist in THIS run's SELECTED, approved, MATCH evidence and
        # the specialist did not choose one: a method exists, so this is NOT "no approved acquisition
        # action". No gap is recorded; the requirement waits for an explicit action choice.
        requirement.blocking_reason = GapReason.ACTION_NOT_CHOSEN
        return AcquisitionDecision(requirement, AcquisitionOutcome.BLOCKED)
    existing = progression.open_gap(requirement.requirement_id) if progression is not None else None
    if existing is not None and live_failure is None and not materially_new_context(existing, requirement, discovery, progression, run_id):
        # The SAME open gap re-proposed with nothing materially new (no new trusted result, operator
        # hint, consulted source or applicability context): the branch stays exhausted. Reused as is:
        # no new discovery attempt is recorded and nothing is retried.
        # A gap already recorded in THIS run (e.g. the exhausted branch persisted before the final
        # proposal) is the same decision, not a repeat across turns.
        repeated = not (run_id and any(a.run_id == run_id for a in existing.all_attempts()))
        requirement.blocking_reason = existing.gap_reason
        return AcquisitionDecision(
            requirement, AcquisitionOutcome.GAP, gap=existing, gap_is_new=False, repeated=repeated,
            response_text=gap_response(requirement, existing.gap_reason, attempts=len(existing.all_attempts()), repeated=repeated),
        )
    reason = _classify_gap(discovery, live_failure)
    requirement.blocking_reason = reason
    if reason is GapReason.APPLICABILITY_UNRESOLVED:
        return AcquisitionDecision(requirement, AcquisitionOutcome.BLOCKED)
    if reason not in DISCOVERY_COMPLETE_GAPS:
        return AcquisitionDecision(requirement, AcquisitionOutcome.FAILED, response_text=failure_response(requirement, reason))
    considered = [
        {"acquisition_type": c.acquisition_type.value, "availability": c.availability.value,
         "blocking_reason": c.blocking_reason.value if c.blocking_reason else None,
         "source": c.source.source_id if c.source else None}
        for c in candidates
    ]
    attempt = DiscoveryAttempt(
        run_id=run_id, searches=list(discovery.searches), sources_consulted=[cid for cid, _ in discovery.available],
        consulted=[ConsultedSource(identity=identity, applicability=outcome) for identity, outcome in discovery.available_identities],
        selected_evidence=list(discovery.selected), candidates_considered=considered,
        hints_considered=[h.value for h in requirement.acquisition_hints], outcome=reason.value,
    )
    if existing is not None:
        # ONE open gap per unresolved requirement: this discovery pass is another attempt on it.
        return AcquisitionDecision(
            requirement, AcquisitionOutcome.GAP, gap=existing, attempt=attempt, gap_is_new=False,
            response_text=gap_response(requirement, reason, attempts=len(existing.all_attempts()) + 1),
        )
    gap = AcquisitionGap(
        fault_id=requirement.fault_id, step_id=step_id, requirement_id=requirement.requirement_id,
        requirement_description=requirement.description, gap_reason=reason,
        sources_searched=list(discovery.searches),
        sources_consulted=list(attempt.sources_consulted),
        supporting_evidence=list(discovery.selected),
        candidates_considered=considered,
        discovery_attempts=[attempt],
        run_id=run_id,
    )
    return AcquisitionDecision(requirement, AcquisitionOutcome.GAP, gap=gap, attempt=attempt, response_text=gap_response(requirement, reason))


def authority_decision(
    requirement: EvidenceRequirement, operational_summary: Optional[Mapping[str, Any]], run_id: Optional[str]
) -> Optional[AuthorityDecisionRecord]:
    """Audit of THIS run's authority decision for the selected governed acquisition. The mapping
    reads only what this run's Command Authority and operational policy decided."""
    candidate = requirement.selected_acquisition
    if candidate is None or candidate.acquisition_type is not AcquisitionType.GOVERNED_ACTION:
        return None
    if candidate.availability is not CandidateAvailability.AVAILABLE:
        status = {
            GapReason.APPLICABILITY_UNRESOLVED: AuthorityStatus.BLOCKED_APPLICABILITY,
            GapReason.REQUIRED_PARAMETER_MISSING: AuthorityStatus.BLOCKED_PARAMETERS,
        }.get(candidate.blocking_reason, AuthorityStatus.NOT_AUTHORIZED)
        return AuthorityDecisionRecord(acquisition_id=candidate.acquisition_id, status=status,
                                       reason=candidate.blocking_reason.value if candidate.blocking_reason else None, run_id=run_id)
    mode = str((operational_summary or {}).get("execution_mode") or "advisory")
    policy = str((operational_summary or {}).get("policy_decision") or "")
    if mode == "read_execution":
        status, actor = AuthorityStatus.AUTHORIZED_BY_CURRENT_POLICY, ExecutionActor.ANOC
    elif mode == "approval_required" or policy == "approval_required":
        status, actor = AuthorityStatus.APPROVAL_REQUIRED, ExecutionActor.ANOC
    elif mode == "prohibited":
        status, actor = AuthorityStatus.NOT_AUTHORIZED, None
    else:
        status, actor = AuthorityStatus.AUTHORIZED, ExecutionActor.OPERATOR
    candidate.execution_actor = actor
    return AuthorityDecisionRecord(acquisition_id=candidate.acquisition_id, status=status, execution_actor=actor,
                                   execution_mode=mode, reason=f"command authority + policy of this run ({mode})", run_id=run_id)


def classified_sources(evidence: list[Any]) -> list[EvidenceSourceRef]:
    return [classify_evidence(ev) for ev in evidence or []]


def render_evidence_acquisition_response(execution: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Server-rendered text of a gap / acquisition failure / server-acquired evidence decision, or
    None. Only a record that presents no diagnostic step qualifies (operational guidance always
    keeps the normal gates)."""
    if not isinstance(execution, Mapping):
        return None
    view = execution.get(EVIDENCE_ACQUISITION_KEY)
    if not isinstance(view, Mapping) or view.get("outcome") not in (AcquisitionOutcome.GAP, AcquisitionOutcome.FAILED, AcquisitionOutcome.SATISFIED):
        return None
    text = view.get("response_text")
    if not isinstance(text, str) or not text.strip() or execution.get("diagnostic_step") is not None:
        return None
    return text


def supporting_identities(execution: Optional[Mapping[str, Any]]) -> Optional[set[tuple[str, str, str]]]:
    """(knowledge_id, version_label, section_id) of the evidence that grounds what the specialist
    presented, read from the record's STRUCTURED identities only; None when the record does not
    carry them (every selected item then stays a source). Display strings are never parsed."""
    if not isinstance(execution, Mapping) or not isinstance(execution.get(SUPPORTING_EVIDENCE_IDENTITIES_KEY), list):
        return None
    return {identity.key for identity in (stored_identity(v) for v in execution[SUPPORTING_EVIDENCE_IDENTITIES_KEY]) if identity is not None}
