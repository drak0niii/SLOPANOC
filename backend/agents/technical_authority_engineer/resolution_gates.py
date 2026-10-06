"""Diagnosis -> Remediation -> Verified Resolution gates (server-owned, deterministic).

    DIAGNOSIS -> REMEDIATION_CANDIDATE -> REMEDIATION -> ACTION_EXECUTED
              -> POST_ACTION_VERIFICATION_REQUIRED -> RESOLVED / REASSESS / ESCALATION_REQUIRED

Remediation gate (a state-changing candidate). The TAE may explain why the evidence supports a
remediation; only server state can pass the gate:
    diagnostic_evidence   a COMPLETED non-state-changing step of THIS fault thread carries a trusted
                          result (operator message / controlled execution), and no earlier
                          remediation still awaits its post-action verification
    target_isolated       every target of the governed action (parameterized slot or literal instance)
                          is a trusted target fact of THIS fault (the target gate passed); a literal
                          "fixed by procedure" target alone never isolates anything. Whether the evidence
                          makes the remediation technically appropriate is the agent's judgement
    governed_remediation  a state-changing ProcedureAction from a SELECTED governed section whose
                          applicability outcome is MATCH
    prerequisites         every MANDATORY_PREREQUISITE relationship of the governed action (derived
                          at extraction time, with provenance; see procedure_actions.py) is
                          COMPLETED on this thread for the same target. RECOMMENDED_BEFORE and
                          UNKNOWN relationships are advisory only: document order is never enforced
The existing controls then still run unchanged: resolution -> Command Authority -> target
confirmation -> policy -> HITL.

Resolution gate. COMMAND SUCCEEDED != INCIDENT RESOLVED. The remediation's own output never
verifies anything. Resolution requires explicit criteria, accepted only when grounded:
    must_exclude  values observed in a trusted PRE-action observation (the original condition)
    must_include  values stated by the SELECTED governed procedure (expected state)
    check         a governed READ action whose output is re-observed after the action
    scope         a literal selecting the output lines (e.g. the target identifier)
Criteria freeze when the action is executed. Each is evaluated on the latest trusted
post-action observation of its check: all MET -> RESOLVED; any NOT_MET -> REASSESS (or
ESCALATION_REQUIRED once the policy's `max_failed_verifications` is reached); otherwise the fault
stays POST_ACTION_VERIFICATION_REQUIRED. No criteria -> never resolved automatically.

Escalation gate. The agent decides WHETHER escalation is technically appropriate and PROPOSES it;
this gate only checks hard requirements under the server-owned TroubleshootingProgressionPolicy
(policy permits it, fault unresolved, recorded evidence or an explicit knowledge gap) and records
the transition. It never judges escalation against other diagnostics. Separately, the policy's
`max_failed_verifications` escalates on its own (a configured policy, not technical reasoning).

Hypotheses: SUPPORTED / WEAKENED / REJECTED need a trusted observation bound THIS turn;
CONFIRMED only from met verification criteria of the remediation that tested the hypothesis.

No vendor vocabulary, no model call.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from backend.agents.technical_authority_engineer.step_identity import (
    STATE_CHANGING_OPERATION_TYPES,
    normalize_command,
    step_identity,
)
from backend.agents.technical_authority_engineer.turn_request import _content_tokens
from backend.cases.troubleshooting_progression import (
    CriterionKind,
    CriterionStatus,
    Hypothesis,
    HypothesisState,
    RemediationRecord,
    RemediationState,
    ResolutionState,
    ResultSource,
    StepIdentity,
    StepPurpose,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
    VerificationCriterion,
)


TRUSTED_SOURCES = frozenset({ResultSource.OPERATOR_MESSAGE, ResultSource.EXECUTION_ADAPTER})
GATE_CONDITIONS = ("diagnostic_evidence", "target_isolated", "governed_remediation", "prerequisites")
_MAX_TOKEN_CHARS = 80


def is_state_changing(identity: Optional[StepIdentity]) -> bool:
    return bool(identity and identity.operation_type in STATE_CHANGING_OPERATION_TYPES)


def _trusted(step: TroubleshootingStep) -> bool:
    return step.result is not None and step.result.source in TRUSTED_SOURCES


def contains(text: str, token: str) -> bool:
    return bool(token) and re.search(rf"(?<![\w]){re.escape(token.casefold())}(?![\w])", (text or "").casefold()) is not None


def scoped_lines(text: str, scope: Optional[str]) -> list[str]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return [line for line in lines if contains(line, scope)] if scope else lines


def _target_map(target: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in target.split(";") if "=" in pair)


def _action_key(action: Any) -> str:
    return f"pa:{action.source.knowledge_id}:{normalize_command(action.command_template)}"


action_key = _action_key


def section_actions(evidence_sections: Mapping[str, tuple[str, Optional[str]]], evidence: Iterable[Any]) -> dict[str, Any]:
    """Governed ProcedureActions of this turn's SELECTED sections (same deterministic extraction
    as the catalog), keyed by action_id."""
    from backend.agents.technical_authority_engineer import procedure_actions as pa

    actions: dict[str, Any] = {}
    for ev in evidence or ():
        get = ev.get if isinstance(ev, Mapping) else (lambda k, _e=ev: getattr(_e, k, None))
        if str(get("source_id") or "") not in evidence_sections:
            continue
        for action in pa.actions_for_evidence(ev, get("metadata") or {})[0]:
            actions[action.action_id] = action
    return actions


# ---- remediation gate ------------------------------------------------------------------------------
def remediation_gate(
    progression: TroubleshootingProgression,
    fault_id: str,
    identity: StepIdentity,
    resolution: Any,
    sections: Mapping[str, tuple[str, Optional[str]]],
    actions: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Evaluate each gate condition from server state. Returns {condition: {"met": bool, "detail": str}}."""
    steps = progression.steps_for(fault_id)
    fault = progression.faults.get(fault_id)
    gate: dict[str, dict[str, Any]] = {}

    diagnostics = [s for s in steps if s.status is StepStatus.COMPLETED and _trusted(s) and not is_state_changing(step_identity(s))]
    awaiting = fault.active_remediation if fault else None
    if awaiting is not None and awaiting.state is RemediationState.VERIFICATION_REQUIRED:
        gate["diagnostic_evidence"] = {"met": False, "detail": f"remediation {awaiting.remediation_id} still awaits post-action verification"}
    elif diagnostics:
        gate["diagnostic_evidence"] = {"met": True, "detail": f"observations {[s.result.result_id for s in diagnostics]}"}
    else:
        gate["diagnostic_evidence"] = {"met": False, "detail": "no completed diagnostic observation on this fault thread"}

    bindings = list(getattr(resolution, "bindings", []) or [])
    unresolved = [b.name for b in bindings if getattr(b.state, "value", b.state) != "verified"]
    validation = getattr(resolution, "target_validation", None)
    if validation is not None and not validation.passed:
        gate["target_isolated"] = {"met": False, "detail": f"no validated current-case target: {validation.reason}"}
    elif getattr(resolution, "candidate", None) is None or unresolved:
        gate["target_isolated"] = {"met": False, "detail": f"target parameters not verified: {unresolved or 'no resolved candidate'}"}
    elif validation is None or not validation.passed or not validation.targets:
        # A literal "fixed by procedure" target is NOT isolated: every target of a state change must
        # be a trusted target fact of THIS fault (procedure_actions.validate_action_targets).
        gate["target_isolated"] = {
            "met": False,
            "detail": f"no validated current-case target: {validation.reason if validation is not None else 'target gate not evaluated'}",
        }
    else:
        identities = [f"{d.key}={d.value}" if d.key else str(d.value) for d in validation.targets]
        gate["target_isolated"] = {"met": True, "detail": f"current-case target(s) {identities} (facts {[f for d in validation.targets for f in d.fact_ids]})"}

    action = getattr(resolution, "action", None)
    source_id = action.source.canonical_source_id if action is not None else None
    _, applicability = sections.get(source_id or "", ("", None))
    if action is None or getattr(action.action_type, "value", action.action_type) != "state_change":
        gate["governed_remediation"] = {"met": False, "detail": "no governed state-changing ProcedureAction was resolved"}
    elif source_id not in sections:
        gate["governed_remediation"] = {"met": False, "detail": f"{source_id} is not SELECTED evidence this turn"}
    elif applicability != "match":
        gate["governed_remediation"] = {"met": False, "detail": f"applicability of {source_id} is {applicability or 'unknown'}, not match"}
    else:
        gate["governed_remediation"] = {"met": True, "detail": f"{action.action_id} from {source_id}"}

    if action is None or source_id not in sections:
        gate["prerequisites"] = {"met": False, "detail": "no governed procedure to read prerequisites from"}
    else:
        gate["prerequisites"] = structured_prerequisites(action, identity, steps, actions)
    return gate


def structured_prerequisites(action: Any, identity: StepIdentity, steps: list[TroubleshootingStep], actions: Mapping[str, Any]) -> dict[str, Any]:
    """Consume the action's STRUCTURED relationships (derived at extraction time, with provenance);
    no governed prose is parsed here. ONLY a MANDATORY_PREREQUISITE blocks, and only while the
    required action is not completed for the same target. RECOMMENDED_BEFORE and UNKNOWN are
    reported as advisory context for the agent: document order is never a server-enforced sequence."""
    from backend.agents.technical_authority_engineer.procedure_actions import RelationshipType

    target = _target_map(identity.target)
    missing: list[str] = []
    relationships = []
    for relationship in action.relationships:
        if relationship.relationship_type is RelationshipType.POST_ACTION_VERIFICATION:
            continue
        mandatory = relationship.relationship_type is RelationshipType.MANDATORY_PREREQUISITE
        relationships.append({"action_id": relationship.related_action_id, "type": relationship.relationship_type.value,
                              "enforced": mandatory, "rule": relationship.rule, "line": relationship.line,
                              "source_locator": relationship.source_locator})
        if not mandatory:
            continue
        required = actions.get(relationship.related_action_id)
        if required is None:
            missing.append(f"{relationship.related_action_id} (not in the selected evidence)")
            continue
        shared = {p.name.casefold() for p in required.parameters} & set(target)
        done = any(
            s.status is StepStatus.COMPLETED and _trusted(s) and step_identity(s).action_key == _action_key(required)
            and all(_target_map(step_identity(s).target).get(name) == target[name] for name in shared)
            for s in steps
        )
        if not done:
            missing.append(f"{required.command_template} (governed line {relationship.line}: {relationship.rule})")
    if missing:
        return {"met": False, "detail": "mandatory prerequisite not completed: " + "; ".join(missing), "relationships": relationships}
    enforced = [r for r in relationships if r["enforced"]]
    detail = "mandatory prerequisites completed" if enforced else "no mandatory prerequisite declared"
    if len(relationships) > len(enforced):
        detail += " (recommended/unknown relationships are advisory, not enforced)"
    return {"met": True, "detail": detail, "relationships": relationships}


def gate_passed(gate: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(gate.get(c, {}).get("met") for c in GATE_CONDITIONS)


def unmet(gate: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [f"{c}: {gate[c]['detail']}" for c in GATE_CONDITIONS if c in gate and not gate[c]["met"]]


# ---- escalation gate --------------------------------------------------------------------------------------


def escalation_gate(
    progression: TroubleshootingProgression,
    fault_id: str,
    policy: Any,
    *,
    knowledge_gap: bool = False,
) -> dict[str, Any]:
    """Hard escalation requirements only -- NOT a judgement of whether escalating is technically
    better than another diagnostic (that is the agent's call):
        failed_verifications_reached  the policy's own threshold of failed post-action verifications
        policy                        the policy permits agent escalation proposals
        unresolved                    the fault is not already resolved / escalated
        recorded evidence             at least one validated observation or failed verification on
                                      this fault, or an explicit knowledge gap (governed search
                                      performed, nothing applicable selected)
    Returns {"passed", "rule", "reasons", "evidence_step_ids", "evidence_result_ids"}."""
    fault = progression.faults[fault_id]
    steps = progression.steps_for(fault_id)
    failed_verifications = [r for r in fault.remediations if r.state is RemediationState.VERIFICATION_FAILED]
    threshold = policy.max_failed_verifications
    if threshold is not None and len(failed_verifications) >= threshold:
        return {"passed": True, "rule": "failed_verifications_reached", "reasons": [f"{len(failed_verifications)} failed post-action verifications"],
                "evidence_step_ids": [r.step_id for r in failed_verifications], "evidence_result_ids": []}
    if not policy.allow_escalation_proposals:
        return {"passed": False, "rule": None, "reasons": ["the escalation policy does not accept agent escalation proposals"], "evidence_step_ids": []}
    if fault.resolution is not ResolutionState.UNRESOLVED:
        return {"passed": False, "rule": None, "reasons": [f"the fault is already {fault.resolution.value}"], "evidence_step_ids": []}
    observed = [s for s in steps if _trusted(s) and s.status in (StepStatus.COMPLETED, StepStatus.FAILED)]
    if policy.escalation_requires_recorded_evidence and not (observed or failed_verifications or knowledge_gap):
        return {"passed": False, "rule": None,
                "reasons": ["no recorded evidence supports escalation yet (no validated observation, failed verification or explicit knowledge gap)"],
                "evidence_step_ids": []}
    rule = "evidence_backed_proposal" if observed or failed_verifications else "no_applicable_governed_procedure"
    return {
        "passed": True, "rule": rule,
        "reasons": ["the agent's escalation proposal is backed by recorded evidence" if rule == "evidence_backed_proposal"
                    else "a governed search was performed and no applicable governed procedure was selected"],
        "evidence_step_ids": [s.step_id for s in observed] + [r.step_id for r in failed_verifications],
        "evidence_result_ids": [s.result.result_id for s in observed],
    }


# ---- verification criteria ---------------------------------------------------------------------------
def _clean(values: Any) -> list[str]:
    out = []
    for value in values or []:
        token = str(value).strip()
        if token and len(token) <= _MAX_TOKEN_CHARS and token not in out:
            out.append(token)
    return out


def ground_criteria(
    proposals: Iterable[Any],
    *,
    baseline: list[TroubleshootingStep],
    sections: Mapping[str, tuple[str, Optional[str]]],
    actions: Mapping[str, Any],
    target: str,
) -> tuple[list[VerificationCriterion], list[dict[str, str]]]:
    accepted: list[VerificationCriterion] = []
    rejected: list[dict[str, str]] = []
    target_values = set(_target_map(target).values())
    for raw in proposals or []:
        proposal = raw if isinstance(raw, Mapping) else getattr(raw, "model_dump", lambda: {})()
        description = str(proposal.get("description") or "")[:500]

        def reject(reason: str) -> None:
            rejected.append({"description": description, "reason": reason})

        try:
            kind = CriterionKind(str(proposal.get("kind")))
        except ValueError:
            reject("unknown criterion kind")
            continue
        include, exclude = _clean(proposal.get("must_include")), _clean(proposal.get("must_exclude"))
        scope = str(proposal.get("scope") or "").strip() or None
        if not include and not exclude:
            reject("no observable expectation")
            continue
        check_key = None
        check_id = proposal.get("check_procedure_action_id")
        if check_id:
            action = actions.get(str(check_id))
            if action is None:
                reject("check is not a governed action of the selected evidence")
                continue
            if action.operation_type.value != "read_only_diagnostic":
                reject("verification check must be a read-only diagnostic")
                continue
            check_key = _action_key(action)
        if scope and scope.casefold() not in target_values and not any(scoped_lines(s.result.text, scope) for s in baseline):
            reject(f"scope '{scope}' was never observed")
            continue
        grounding: list[str] = []
        ungrounded = None
        for token in exclude:
            hit = next((s for s in baseline if any(contains(line, token) for line in scoped_lines(s.result.text, scope))), None)
            if hit is None:
                ungrounded = f"'{token}' was not observed before the action"
                break
            grounding.append(f"baseline:{hit.result.result_id}")
        for token in include if ungrounded is None else []:
            source = next((sid for sid, (content, _) in sections.items() if contains(content, token)), None)
            if source is None:
                ungrounded = f"'{token}' is not stated by the selected governed procedure"
                break
            grounding.append(f"governed:{source}")
        if ungrounded:
            reject(ungrounded)
            continue
        accepted.append(
            VerificationCriterion(
                kind=kind, description=description, check_action_key=check_key, scope=scope,
                must_include=include, must_exclude=exclude, grounding=sorted(set(grounding)),
            )
        )
    return accepted, rejected


def evaluate_criteria(record: RemediationRecord, observations: list[TroubleshootingStep]) -> None:
    """Deterministic: latest trusted post-action observation of each criterion's check decides it."""
    for criterion in record.criteria:
        applicable = [s for s in observations if criterion.check_action_key is None or step_identity(s).action_key == criterion.check_action_key]
        if not applicable:
            continue
        latest = applicable[-1]
        echoed = normalize_command(latest.command)
        lines = [line for line in scoped_lines(latest.result.text, criterion.scope) if normalize_command(line) != echoed]  # skip the echoed command
        if criterion.scope and not lines:
            criterion.status, criterion.detail = CriterionStatus.PENDING, f"'{criterion.scope}' absent from the latest verification output"
            criterion.evidence_ids = [latest.result.result_id]
            continue
        text = "\n".join(lines)
        still_present = [t for t in criterion.must_exclude if contains(text, t)]
        missing = [t for t in criterion.must_include if not contains(text, t)]
        criterion.status = CriterionStatus.NOT_MET if still_present or missing else CriterionStatus.MET
        criterion.evidence_ids = [latest.result.result_id]
        criterion.detail = (
            "; ".join([*(f"'{t}' still observed" for t in still_present), *(f"'{t}' not observed" for t in missing)]) or "criterion satisfied"
        )


def verification_outcome(record: RemediationRecord) -> Optional[CriterionStatus]:
    """MET (all met), NOT_MET (any not met), or None (no criteria / some still pending)."""
    if not record.criteria:
        return None
    statuses = {c.status for c in record.criteria}
    if CriterionStatus.NOT_MET in statuses:
        return CriterionStatus.NOT_MET
    if statuses == {CriterionStatus.MET}:
        return CriterionStatus.MET
    return None


def post_action_observations(progression: TroubleshootingProgression, record: RemediationRecord) -> list[TroubleshootingStep]:
    remediation = progression.step(record.step_id)
    after = remediation.sequence if remediation else 0
    return [
        s for s in progression.steps_for(record.fault_id)
        if s.sequence > after and s.purpose is StepPurpose.VERIFICATION and s.status is StepStatus.COMPLETED and _trusted(s)
    ]


def pre_action_observations(progression: TroubleshootingProgression, fault_id: str) -> list[TroubleshootingStep]:
    return [s for s in progression.steps_for(fault_id) if s.status is StepStatus.COMPLETED and _trusted(s) and not is_state_changing(step_identity(s))]


# ---- hypotheses ------------------------------------------------------------------------------------------
def hypothesis_key(statement: str) -> str:
    return " ".join(sorted(set(_content_tokens(statement or ""))))


def find_or_add_hypothesis(progression: TroubleshootingProgression, fault_id: str, statement: str) -> Optional[Hypothesis]:
    key = hypothesis_key(statement)
    if not key:
        return None
    existing = next((h for h in progression.hypotheses_for(fault_id) if hypothesis_key(h.statement) == key), None)
    return existing or progression.add_hypothesis(Hypothesis(fault_id=fault_id, statement=statement.strip()[:500]))


def apply_hypothesis_updates(
    progression: TroubleshootingProgression, fault_id: str, proposals: Iterable[Any], evidence_ids: list[str], step_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """Apply proposed hypothesis states with THIS turn's trusted observations as evidence.
    CONFIRMED is never applied from a proposal."""
    applied: list[dict[str, Any]] = []
    for raw in proposals or []:
        proposal = raw if isinstance(raw, Mapping) else getattr(raw, "model_dump", lambda: {})()
        statement = str(proposal.get("statement") or "")
        requested = str(proposal.get("proposed_state") or "active")
        hypothesis = find_or_add_hypothesis(progression, fault_id, statement)
        if hypothesis is None:
            continue
        entry = {"hypothesis_id": hypothesis.hypothesis_id, "statement": hypothesis.statement, "proposed_state": requested}
        if requested == HypothesisState.ACTIVE.value:
            entry.update(status="recorded", state=hypothesis.state.value)
        elif not evidence_ids:
            entry.update(status="rejected", state=hypothesis.state.value, reason="no trusted observation was recorded this turn")
        elif requested == HypothesisState.CONFIRMED.value:
            if hypothesis.state is not HypothesisState.SUPPORTED:
                progression.transition_hypothesis(hypothesis.hypothesis_id, HypothesisState.SUPPORTED, evidence_ids, step_id,
                                                  reason="confirmation requires met verification criteria; recorded as supported")
            entry.update(status="downgraded", state=HypothesisState.SUPPORTED.value, reason="CONFIRMED requires met verification criteria")
        else:
            target = HypothesisState(requested)
            if hypothesis.state is HypothesisState.CONFIRMED:
                entry.update(status="rejected", state=hypothesis.state.value, reason="a confirmed hypothesis changes only through verification")
            else:
                if hypothesis.state is not target:
                    progression.transition_hypothesis(hypothesis.hypothesis_id, target, evidence_ids, step_id, reason=str(proposal.get("rationale") or "")[:300])
                entry.update(status="applied", state=target.value, evidence_ids=list(evidence_ids))
        applied.append(entry)
    return applied
