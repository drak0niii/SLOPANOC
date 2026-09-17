"""POST-6A REPAIR 3 -- Governed Operation Descriptor.

THE GAP THIS CLOSES: every operational-safety decision in this system --
"does this operation need a physical target?", "which parameters are
required?", "is this a read or a state change?", "what must be true
first?", "what is forbidden?" -- was answered by INFERENCE over free text
(scanning a command string for an `RRU-*`/`AAS-*` token; reading a
model-set `operational_effect`) rather than by reading positive, governed
metadata. Inference over text cannot distinguish "this operation is
genuinely system-wide" from "we happen not to recognize its target
syntax", so absence of evidence became evidence of safety.

WHAT THIS IS: the SMALLEST descriptor that lets a deterministic layer
answer those questions POSITIVELY, attached to the governed content it
describes. It is an additive, optional field on `KnowledgeSection`
(models.py) -- no new repository, no new pipeline, no parallel knowledge
store, and no migration (the repository persists the whole
`KnowledgeObject` as one JSON payload, so an optional field round-trips
as-is).

AUTHORITY IS NEVER INFERRED, NEVER SELF-ASSERTED, AND NEVER GRANTED BY A
CALLER'S OWN CLAIM. A descriptor carries its own `authority`, and only
`APPROVED` may authorize anything. `APPROVED` is reachable ONLY through
`backend.knowledge.governance.operation_approval.approve_section_
operation`, which requires a typed `OperationApprovalRecord` checked
against the exact source version AND a fingerprint of the exact
descriptor content -- so an approval cannot cross versions and cannot
survive an edit to what it approved. (POST-6A PROMPT 3 removed the former
public `approve_descriptor(..., governance_transition_approved=True)`:
a boolean argument supplied by the caller is not a governance decision.)
`from_model_extraction` exists precisely so a model-proposed descriptor
has a safe, explicit way in, and it ALWAYS produces `CANDIDATE`,
regardless of what the extraction claimed. There is deliberately no code
path that promotes a descriptor automatically, and none that promotes the
existing corpus in bulk.

UNKNOWN SCOPE IS NOT PERMISSIVE. `OperationTargetScope.UNKNOWN` is the
default and means exactly what it says: nothing has been established. A
consumer must treat it as unresolved, never as "no target needed" -- see
`required_parameter_names_for_scope`, and `request_contract
.required_target_parameter_gaps`'s own use of it.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.knowledge.domain._shared import require_non_blank


class OperationDescriptorAuthority(str, Enum):
    """Whether this descriptor may be used to authorize anything.

    Mirrors `LifecycleStatus`'s own CANDIDATE/APPROVED discipline
    deliberately -- a descriptor is governed content, subject to the same
    rule as every other piece of governed content: ingestion (or model
    extraction) produces CANDIDATE; only a human-gated governance
    transition creates APPROVED.
    """

    CANDIDATE = "candidate"
    APPROVED = "approved"


class OperationTargetScope(str, Enum):
    """What kind of target this operation acts on. POSITIVE metadata --
    never derived by scanning a command string."""

    UNKNOWN = "unknown"
    """Nothing has been established. Never treated as target-independent."""

    TARGET_INDEPENDENT = "target_independent"
    """Genuinely acts on no specific unit (e.g. a system-wide alarm
    listing). Positively asserted by governance, never inferred from the
    absence of a recognized identifier token."""

    SINGLE_TARGET = "single_target"
    """Acts on exactly one identified unit."""

    MULTI_TARGET = "multi_target"
    """Acts on a named set of units."""


class OperationParameterKind(str, Enum):
    TARGET_IDENTIFIER = "target_identifier"
    """Identifies WHICH physical unit is acted on (e.g. `unit_id`). The
    highest-risk parameter class: every occurrence of one of these in a
    constructed command is separately re-checked against the verified
    effective request."""

    TARGET_TYPE = "target_type"
    """The unit CLASS (e.g. `unit_type`)."""

    VALUE = "value"
    """An ordinary, non-target operational value."""


class OperationEffect(str, Enum):
    """Governed classification of what running this operation does.
    Deliberately parallel to `TroubleshootingOperationalEffect` (the
    MODEL's own self-classification) but sourced from governance instead
    -- when both exist, this one is authoritative."""

    READ_ONLY = "read_only"
    STATE_CHANGE = "state_change"
    UNKNOWN = "unknown"


class OperationParameterDefinition(BaseModel):
    """One parameter an approved command template needs."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: OperationParameterKind = OperationParameterKind.VALUE
    required: bool = True
    identifier_class: Optional[str] = Field(
        default=None,
        description=(
            "For a TARGET_IDENTIFIER parameter, the unit class its value must belong to (e.g. 'RRU'). Used to "
            "check that a supplied value is of the right class, never to guess one."
        ),
    )
    allowed_values: tuple[str, ...] = Field(
        default=(),
        description="Closed allowlist of permitted values, when governance defines one. Empty means unconstrained.",
    )

    @field_validator("name")
    @classmethod
    def _non_blank_name(cls, value: str) -> str:
        return require_non_blank(value, "name")


class ApprovedCommandTemplate(BaseModel):
    """One exact, governance-approved command form, with its parameters
    expressed as named placeholders rather than baked-in example values.

    A template is what makes deterministic construction possible WITHOUT
    string-replacing a source example: `accn FieldReplaceableUnit={unit_id}
    restartunit 1 1 1` has no example identifier in it to accidentally
    survive, and no substitution ever rewrites governed prose.
    """

    model_config = ConfigDict(frozen=True)

    template_id: str
    template: str = Field(description="The command form, with `{parameter_name}` placeholders. Never a filled example.")
    effect: OperationEffect = OperationEffect.UNKNOWN
    prerequisites: tuple[str, ...] = ()
    prohibitions: tuple[str, ...] = ()

    @field_validator("template_id", "template")
    @classmethod
    def _non_blank(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return require_non_blank(value, info.field_name)

    @property
    def placeholder_names(self) -> tuple[str, ...]:
        """Every `{name}` placeholder in this template, in first-
        appearance order. Deterministic single scan -- no `re`, no format
        parsing, no nesting, and an unterminated `{` yields nothing
        rather than a partial name."""
        names: list[str] = []
        rest = self.template
        while True:
            open_at = rest.find("{")
            if open_at < 0:
                break
            close_at = rest.find("}", open_at + 1)
            if close_at < 0:
                break
            name = rest[open_at + 1 : close_at].strip()
            if name and name not in names:
                names.append(name)
            rest = rest[close_at + 1 :]
        return tuple(names)


class GovernedOperationDescriptor(BaseModel):
    """The governed description of ONE operation a knowledge section
    defines. Attached to that section (`KnowledgeSection.operation`), so
    its source/version identity is never separately asserted or spoofable
    -- `bind_to_source` stamps it from the owning object, and a mismatch
    against the section actually selected this turn is a hard rejection
    downstream.
    """

    model_config = ConfigDict(frozen=True)

    operation_id: str
    authority: OperationDescriptorAuthority = OperationDescriptorAuthority.CANDIDATE
    target_scope: OperationTargetScope = OperationTargetScope.UNKNOWN
    effect: OperationEffect = OperationEffect.UNKNOWN
    parameters: tuple[OperationParameterDefinition, ...] = ()
    command_templates: tuple[ApprovedCommandTemplate, ...] = ()
    prerequisites: tuple[str, ...] = ()
    prohibitions: tuple[str, ...] = ()

    # --- source/version identity ------------------------------------
    knowledge_id: Optional[str] = None
    version_label: Optional[str] = None
    section_id: Optional[str] = None

    @field_validator("operation_id")
    @classmethod
    def _non_blank_operation_id(cls, value: str) -> str:
        return require_non_blank(value, "operation_id")

    @model_validator(mode="after")
    def _templates_declare_their_parameters(self) -> "GovernedOperationDescriptor":
        """Every placeholder a template uses must have a parameter
        definition. A template referring to an undeclared parameter could
        never be safely filled, so it is rejected at construction rather
        than failing unpredictably at command-construction time."""
        declared = {param.name for param in self.parameters}
        for template in self.command_templates:
            missing = [name for name in template.placeholder_names if name not in declared]
            if missing:
                raise ValueError(
                    f"command template {template.template_id!r} uses undeclared parameters: {sorted(missing)}"
                )
        return self

    @property
    def is_approved(self) -> bool:
        return self.authority == OperationDescriptorAuthority.APPROVED

    @property
    def required_parameter_names(self) -> tuple[str, ...]:
        return tuple(param.name for param in self.parameters if param.required)

    @property
    def target_identifier_parameters(self) -> tuple[OperationParameterDefinition, ...]:
        return tuple(p for p in self.parameters if p.kind == OperationParameterKind.TARGET_IDENTIFIER)

    def parameter(self, name: str) -> Optional[OperationParameterDefinition]:
        for param in self.parameters:
            if param.name == name:
                return param
        return None

    def approved_template(self, template_id: str) -> Optional[ApprovedCommandTemplate]:
        """A template, by id -- returned ONLY when this descriptor itself
        is APPROVED. A CANDIDATE descriptor's templates are visible for
        review but can never be reached through this accessor, which is
        the one command construction uses."""
        if not self.is_approved:
            return None
        for template in self.command_templates:
            if template.template_id == template_id:
                return template
        return None

    def bind_to_source(
        self, *, knowledge_id: str, version_label: Optional[str], section_id: str
    ) -> "GovernedOperationDescriptor":
        """Stamps this descriptor's own source/version identity from the
        governed object that actually owns it. Never self-asserted by the
        descriptor's author."""
        return self.model_copy(
            update={"knowledge_id": knowledge_id, "version_label": version_label, "section_id": section_id}
        )

    def matches_source(self, *, knowledge_id: str, version_label: Optional[str], section_id: str) -> bool:
        """Exact identity equality against the evidence actually selected
        this turn. `None` on a stored field means "never bound" and never
        matches -- absence is not a wildcard."""
        return (
            self.knowledge_id is not None
            and self.section_id is not None
            and self.knowledge_id == knowledge_id
            and self.section_id == section_id
            and self.version_label == version_label
        )


def from_model_extraction(descriptor: GovernedOperationDescriptor) -> GovernedOperationDescriptor:
    """THE one supported way a model-proposed descriptor enters the
    system. Always returns a `CANDIDATE`, whatever the extraction
    claimed -- there is deliberately no argument, flag, or alternate
    entry point that produces `APPROVED` from extracted metadata."""
    return descriptor.model_copy(update={"authority": OperationDescriptorAuthority.CANDIDATE})


def _approved_copy(descriptor: GovernedOperationDescriptor) -> GovernedOperationDescriptor:
    """INTERNAL. The ONE place an `APPROVED` descriptor is constructed.

    Deliberately private and deliberately judgment-free: the decision
    that promotion is legitimate belongs entirely to `backend.knowledge
    .governance.operation_approval.approve_section_operation`, which
    checks a typed `OperationApprovalRecord` against the exact source
    version AND the exact descriptor content before calling anything
    here. Nothing else in the codebase may promote a descriptor.

    POST-6A PROMPT 3: this REPLACES the former public
    `approve_descriptor(descriptor, governance_transition_approved=True)`.
    That signature made approval a caller ASSERTION -- any code able to
    pass `True` could mint authority, with no binding to the version or
    the content being approved. A boolean argument is not a governance
    decision.
    """
    return descriptor.model_copy(update={"authority": OperationDescriptorAuthority.APPROVED})


def required_parameter_names_for_scope(descriptor: Optional[GovernedOperationDescriptor]) -> Optional[tuple[str, ...]]:
    """What a deterministic policy layer may conclude about required
    target parameters from governed metadata alone.

    Returns:
      - `()` -- positively established that no target parameter is needed
        (an APPROVED, `TARGET_INDEPENDENT` descriptor).
      - a tuple of names -- the APPROVED descriptor's own required
        parameters.
      - `None` -- NOTHING is established: no descriptor, a CANDIDATE one,
        or `UNKNOWN` scope. The caller must treat this as unresolved and
        fall back to its own conservative rule; it must never read `None`
        as "no parameters needed".
    """
    if descriptor is None or not descriptor.is_approved:
        return None
    if descriptor.target_scope == OperationTargetScope.UNKNOWN:
        return None
    if descriptor.target_scope == OperationTargetScope.TARGET_INDEPENDENT:
        return ()
    return descriptor.required_parameter_names
