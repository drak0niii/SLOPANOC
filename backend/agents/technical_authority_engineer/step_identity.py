"""Canonical, server-owned diagnostic step identity (duplicate / loop prevention).

    identity key = sha256(fault_id, action_key, target)

    action_key -- the most structured description available, in this order:
        pa:<knowledge_id>:<command template>      a governed ProcedureAction. Version-independent:
                                                  a new version of the same document is a CONTEXT
                                                  change, not a different action.
        cmd:<normalized command>                  a command without a resolved ProcedureAction
        obs:<objective tokens>|<expected tokens>  a command-less (manual) observation step
    target     -- sorted `name=value` of the VERIFIED parameter bindings ("" when there are none)
    result_key -- normalized rendered command: the observation the step produces, shared by
                  every source that renders the same command
    context    -- source version label + applicability outcome (change detection, not identity)

Normalization is lexical only (whitespace collapsed, casefolded, sorted token sets). No model
similarity judgment is ever authoritative.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Optional

from backend.agents.technical_authority_engineer.turn_request import _content_tokens
from backend.cases.troubleshooting_progression import StepIdentity, TroubleshootingStep

STATE_CHANGING_OPERATION_TYPES = frozenset({"mutating_operational", "configuration_change"})


def normalize_command(command: Optional[str]) -> Optional[str]:
    if not command or not str(command).strip():
        return None
    return re.sub(r"\s+", " ", str(command).strip()).casefold()


def _token_key(text: Optional[str]) -> str:
    return " ".join(sorted(set(_content_tokens(text or ""))))


def canonical_target(bindings: Mapping[str, Any]) -> str:
    pairs = (f"{str(k).strip().casefold()}={str(v).strip().casefold()}" for k, v in bindings.items() if v is not None and str(v).strip())
    return ";".join(sorted(pairs))


def target_values(target: str) -> list[str]:
    return [pair.split("=", 1)[1] for pair in target.split(";") if "=" in pair]


def _digest(fault_id: str, action_key: str, target: str) -> str:
    return "sid-" + hashlib.sha256("\x1f".join((fault_id, action_key, target)).encode("utf-8")).hexdigest()[:20]


def legacy_identity(
    fault_id: str,
    *,
    objective: Optional[str],
    command: Optional[str],
    expected_evidence: Optional[str] = None,
    source_id: Optional[str] = None,
    applicability: Optional[str] = None,
    version_label: Optional[str] = None,
) -> StepIdentity:
    """Identity of a step with no resolved ProcedureAction (model/legacy command or manual check).
    `version_label` comes from the source's structured identity; it is never parsed from `source_id`."""
    normalized = normalize_command(command)
    action_key = f"cmd:{normalized}" if normalized else f"obs:{_token_key(objective)}|{_token_key(expected_evidence)}"
    return StepIdentity(
        key=_digest(fault_id, action_key, ""), fault_id=fault_id, action_key=action_key, target="", result_key=normalized,
        source_id=source_id, version_label=version_label, applicability=applicability,
    )


def candidate_identity(
    fault_id: str,
    step_data: Mapping[str, Any],
    resolution: Any = None,
    applicability: Optional[Mapping[str, Any]] = None,
    versions: Optional[Mapping[str, str]] = None,
) -> StepIdentity:
    """Identity of a proposed step from server-resolved structure (never from model wording when a
    ProcedureAction is resolved). `versions`: this turn's selected source id -> structured version label."""
    applicability = applicability or {}
    action = getattr(resolution, "action", None)
    if action is None:
        source_id = step_data.get("command_source")
        return legacy_identity(
            fault_id, objective=step_data.get("action"), command=step_data.get("command"),
            expected_evidence=step_data.get("expected_evidence"), source_id=source_id,
            applicability=applicability.get(source_id) if source_id else None,
            version_label=(versions or {}).get(source_id) if source_id else None,
        )
    candidate = getattr(resolution, "candidate", None)
    if candidate is not None:
        bindings: Mapping[str, Any] = dict(candidate.bound_parameters)
    else:
        bindings = {b.name: b.value for b in getattr(resolution, "bindings", []) or [] if getattr(b.state, "value", b.state) == "verified" and b.value}
    action_key = f"pa:{action.source.knowledge_id}:{normalize_command(action.command_template)}"
    target = canonical_target(bindings)
    source_id = action.source.canonical_source_id
    return StepIdentity(
        key=_digest(fault_id, action_key, target), fault_id=fault_id, action_key=action_key, target=target,
        result_key=normalize_command(candidate.command if candidate is not None else step_data.get("command")),
        source_id=source_id, version_label=action.source.version_label, applicability=applicability.get(source_id),
        template=action.command_template, operation_type=getattr(action.operation_type, "value", action.operation_type),
        sequence=getattr(action, "sequence", None),
    )


def step_identity(step: TroubleshootingStep) -> StepIdentity:
    """Stored identity, or the legacy identity for steps recorded before identities existed."""
    if step.identity is not None:
        return step.identity
    return legacy_identity(
        step.fault_id, objective=step.objective, command=step.command, expected_evidence=step.expected_evidence,
        source_id=step.command_source_id,
        version_label=step.command_source_identity.version_label if step.command_source_identity is not None else None,
    )


def identity_summary(identity: Optional[StepIdentity]) -> Optional[dict[str, Any]]:
    if identity is None:
        return None
    return {"key": identity.key, "action_key": identity.action_key, "target": identity.target, "result_key": identity.result_key}
