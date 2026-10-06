"""Server-owned operational action context: the exact thing a confirmation or an approval is
bound to. Built only from a Tranche 2 ProcedureAction resolution (governed template + trusted
parameter bindings + SELECTED source identity) -- never from model output.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.operations.signing import stable_hash, text_hash


class TargetIdentity(BaseModel):
    """Generic target identity. `target_type` is the governed placeholder name (or a generic
    composite/scope label) -- no telecom-specific type is hardcoded anywhere in authority logic."""

    target_type: str
    canonical_identifier: str
    display_value: str
    attributes: dict[str, str] = Field(default_factory=dict)


def validated_target_identity(target_validation: Optional[dict[str, Any]]) -> Optional[TargetIdentity]:
    """The target identity of a state change, taken ONLY from the server target gate's validated
    `key=value` targets (with their current-case fact provenance) -- never from the command text."""
    targets = [t for t in (target_validation or {}).get("targets") or [] if isinstance(t, dict) and t.get("value")]
    if not targets or not (target_validation or {}).get("passed"):
        return None
    written = [f"{t['key']}={t['value']}" if t.get("key") else str(t["value"]) for t in targets]
    attributes = {
        "validated_targets": ";".join(written),
        "fact_ids": ",".join(sorted({f for t in targets for f in t.get("fact_ids") or []})),
    }
    if len(targets) == 1:
        target = targets[0]
        return TargetIdentity(
            target_type=str(target.get("key") or target.get("parameter") or "target"), canonical_identifier=str(target["value"]),
            display_value=written[0], attributes=attributes,
        )
    canonical = ";".join(sorted(written))
    return TargetIdentity(target_type="composite", canonical_identifier=canonical, display_value=canonical, attributes=attributes)


def target_identity_for(parameters: dict[str, str], command: str) -> TargetIdentity:
    """Deterministic target identity for a rendered action.

    The identity is DERIVED from verified parameter bindings, but a derived identity is only a
    proposal: it becomes a confirmed target solely through an explicit operator confirmation
    (targets.py). Parameter verified != target confirmed.
    """
    if len(parameters) == 1:
        (name, value), = parameters.items()
        return TargetIdentity(target_type=name, canonical_identifier=value, display_value=value, attributes=dict(parameters))
    if parameters:
        canonical = ";".join(f"{k}={parameters[k]}" for k in sorted(parameters))
        return TargetIdentity(target_type="composite", canonical_identifier=canonical, display_value=canonical, attributes=dict(parameters))
    return TargetIdentity(
        target_type="procedure_scope",
        canonical_identifier=command,
        display_value="As defined by the governed procedure (no explicit target parameter)",
    )


class ActionSourceIdentity(BaseModel):
    canonical_source_id: str
    knowledge_id: str
    version_label: str
    section_id: str
    source_locator: Optional[str] = None
    title: Optional[str] = None
    heading: Optional[str] = None
    content_hash: str


class OperationalActionContext(BaseModel):
    """Everything an operational decision is bound to. `binding_hash()` changes if the action,
    command, template, parameters, target, operation type, or governed source (version or exact
    section text) change -- so a confirmation/approval for one context can never apply to another."""

    control_id: str
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    check_id: Optional[str] = None
    procedure_action_id: str
    intent: str
    action_type: str
    operation_type: str
    command_template: str
    command: str
    parameters: dict[str, str] = Field(default_factory=dict)
    target: TargetIdentity
    source: ActionSourceIdentity
    description: str = ""
    reason: str = ""
    restrictions: list[str] = Field(default_factory=list)
    fault_id: Optional[str] = None
    target_validation: Optional[dict[str, Any]] = Field(
        default=None,
        description="State changes: the server target gate's decision (validated key=value targets + current-case fact ids).",
    )
    governed_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="State changes: verbatim governed preconditions / post-action requirements / block conditions "
        "(with provenance) the confirmation and approval cover.",
    )
    condition_scope: Optional[dict[str, Any]] = Field(
        default=None, description="State changes: the governed condition(s) the action is scoped to (any one must hold)."
    )

    def binding(self) -> dict[str, Any]:
        binding = self._base_binding()
        # Added only when present, so contexts persisted before target validation keep their hash.
        if self.fault_id:
            binding["fault_id"] = self.fault_id
        if self.target_validation:
            binding["target_validation"] = {
                "passed": self.target_validation.get("passed"),
                "targets": [
                    {"key": t.get("key"), "value": t.get("value"), "fact_ids": sorted(t.get("fact_ids") or [])}
                    for t in self.target_validation.get("targets") or [] if isinstance(t, dict)
                ],
            }
        if self.governed_conditions:
            binding["governed_conditions"] = [
                {"kind": c.get("kind"), "source": c.get("source"), "line": c.get("line"), "text": c.get("text")}
                for c in self.governed_conditions
            ]
        if self.condition_scope:
            binding["condition_scope"] = [
                {"dimension": a.get("dimension"), "value": a.get("value"), "source": a.get("source")}
                for a in self.condition_scope.get("alternatives") or []
            ]
        return binding

    def _base_binding(self) -> dict[str, Any]:
        return {
            "procedure_action_id": self.procedure_action_id,
            "operation_type": self.operation_type,
            "command_template": self.command_template,
            "command": self.command,
            "parameters": dict(sorted(self.parameters.items())),
            "target": {"type": self.target.target_type, "id": self.target.canonical_identifier},
            "source": {
                "id": self.source.canonical_source_id,
                "version": self.source.version_label,
                "content_hash": self.source.content_hash,
            },
            "check_id": self.check_id,
            "session_id": self.session_id,
        }

    def binding_hash(self) -> str:
        return stable_hash(self.binding())

    def command_hash(self) -> str:
        return text_hash(self.command)
