"""POST-6A REPAIR 4/5 -- Deterministic Command Construction and Binding.

THE GAP THIS CLOSES: the only way a command could ever be authorized was
VERBATIM containment in the active governed section. That is a genuine
safety property, but it has a fatal consequence for parameterized
operations: governed procedures state their commands with EXAMPLE
identifiers (`accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1`), so the
ONLY string that can ever pass verbatim grounding is the one carrying the
SOURCE's own example target. For a user whose real unit is `RRU-3`, the
system could therefore only either (a) withhold the command, or (b) --
the dangerous shape this repair forecloses -- have some layer rewrite the
example, which is arbitrary string replacement over governed prose with
no check that the result still means what the source meant.

WHAT THIS IS: an ADDITIONAL, EXPLICIT grounding path, never a relaxation
of the existing one. Verbatim grounding is untouched and remains the path
for a command with no parameters. Template grounding constructs the
command from an APPROVED `ApprovedCommandTemplate` -- a governed command
form whose target is a NAMED PLACEHOLDER, not a filled-in example -- and
the verified parameters of THIS turn's effective request. Nothing is
"replaced": there is no example identifier in a template to survive.

THE CHECK THAT MATTERS (section 4's own "a source example for RRU-9 must
never be authorized for user target RRU-3"): after construction, EVERY
target-bearing argument in the resulting command is re-extracted and
compared, by exact canonical identity, against the verified target
parameters. A constructed command containing any identifier the user did
not verifiably supply is REJECTED -- whether it arrived from a template
that wrongly hard-coded one, from a parameter value that was never
verified, or from anything else. This check is independent of how the
command was built, so it also cannot be bypassed by a future construction
path.

BINDING AND INVALIDATION (repair 5): a constructed command is returned
with a `CommandCandidateBinding` -- the exact operation identity, source/
version identity, target parameters, and payload it was authorized FOR.
`binding_supersedes` compares two bindings; any difference in target,
operation, procedure version, or payload invalidates the earlier
candidate and any approval that was granted for it. Nothing is reused
across a change; re-authorization is always a fresh construction.
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.team_manager.identifier_verification import (
    canonical_identifier,
    extract_identifier_mentions,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance
from backend.agents.team_manager.request_contract import RequestParameter
from backend.knowledge.domain.operation_descriptor import (
    ApprovedCommandTemplate,
    GovernedOperationDescriptor,
    OperationParameterKind,
)

_COMMAND_ARGUMENT_SEPARATORS = ("=", ",", ";", "/")
"""Characters that delimit an identifier inside real command syntax but
not inside natural language. Normalized to spaces before identifier
extraction so `FieldReplaceableUnit=RRU-9` is seen as a target argument.
Deliberately the SAME idea as `command_text_references_target_identifier_
class`'s own `=` split, widened only to the separators real command
syntax uses -- never a command parser, never a regex."""


class CommandConstructionStatus(str, Enum):
    CONSTRUCTED = "constructed"

    NO_APPROVED_TEMPLATE = "no_approved_template"
    """No APPROVED descriptor/template for the selected section. The
    ordinary case for every non-parameterized operation -- the caller
    simply falls back to verbatim grounding."""

    MISSING_PARAMETER = "missing_parameter"
    """A placeholder had no verified value. Fails closed."""

    PARAMETER_NOT_ALLOWED = "parameter_not_allowed"
    """A supplied value is outside the governed allowlist, or is of the
    wrong identifier class for its parameter."""

    UNVERIFIED_TARGET_IN_COMMAND = "unverified_target_in_command"
    """The constructed command contains a unit identifier that is not one
    of this turn's verified targets -- the exact "source example for
    RRU-9 authorized for user target RRU-3" failure."""

    PROHIBITED = "prohibited"
    """The template declares a prohibition that applies."""


class CommandCandidateBinding(BaseModel):
    """POST-6A REPAIR 5 -- exactly what a command candidate was
    authorized FOR. Any difference invalidates it."""

    model_config = ConfigDict(frozen=True)

    operation_id: str
    knowledge_id: Optional[str] = None
    version_label: Optional[str] = None
    section_id: Optional[str] = None
    template_id: Optional[str] = None
    target_parameters: tuple[tuple[str, str], ...] = ()
    """Sorted `(name, value)` pairs of every TARGET_IDENTIFIER/TARGET_TYPE
    parameter the command was built from."""
    payload: str = ""
    """The constructed command text itself."""


class ConstructedCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CommandConstructionStatus
    command: Optional[str] = None
    binding: Optional[CommandCandidateBinding] = None
    detail: str = ""
    """Safe, non-leaking diagnostic -- parameter NAMES and typed reasons
    only, never a rejected command string or a governed document body."""

    @property
    def authorized(self) -> bool:
        return self.status == CommandConstructionStatus.CONSTRUCTED and bool(self.command)


def _verified_values(parameters: Sequence[RequestParameter]) -> dict[str, str]:
    return {param.name: param.value for param in parameters}


def target_identifiers_in_command(command_text: Optional[str]) -> set[str]:
    """Every recognized unit identifier appearing as a command ARGUMENT.

    Normalizes command-syntax separators to whitespace first, so an
    identifier fused onto an argument name (`FieldReplaceableUnit=RRU-9`)
    is recognized -- then reuses the SAME exact, whole-identifier,
    boundary-safe recognizer repair 1 established. Exclusion markers are
    irrelevant here (a command has no prose polarity), so every mention
    counts.
    """
    if not command_text:
        return set()
    normalized = command_text
    for separator in _COMMAND_ARGUMENT_SEPARATORS:
        normalized = normalized.replace(separator, " ")
    return {mention.value for mention in extract_identifier_mentions(normalized)}


def verify_command_targets(
    command_text: Optional[str], verified_parameters: Sequence[RequestParameter]
) -> tuple[bool, tuple[str, ...]]:
    """THE target check, applied to a FINISHED command however it was
    produced (constructed here, or grounded verbatim elsewhere).

    Returns `(ok, unverified_identifiers)`. `ok` is `False` when the
    command carries ANY unit identifier that is not, by exact canonical
    identity, one of this turn's verified target values. A command
    carrying no identifier at all is `ok` -- this function answers "are
    the targets it names the right ones", never "does it need one" (that
    is the governed descriptor's job, repair 2).
    """
    present = target_identifiers_in_command(command_text)
    if not present:
        return True, ()
    allowed = {
        canonical
        for param in verified_parameters
        if (canonical := canonical_identifier(param.value)) is not None
    }
    unverified = sorted(present - allowed)
    return (not unverified), tuple(unverified)


def construct_command_from_template(
    descriptor: Optional[GovernedOperationDescriptor],
    template_id: Optional[str],
    verified_parameters: Sequence[RequestParameter],
    *,
    satisfied_prerequisites: Optional[Mapping[str, bool]] = None,
) -> ConstructedCommand:
    """Deterministically build ONE command from an APPROVED template and
    this turn's own VERIFIED effective-request parameters. No model call,
    no inference, no string replacement over governed prose.

    Fails closed at every step; an unauthorized outcome never returns a
    partially-filled command, and never echoes a rejected value.
    """
    if descriptor is None or not template_id:
        return ConstructedCommand(
            status=CommandConstructionStatus.NO_APPROVED_TEMPLATE, detail="no governed operation descriptor"
        )
    template = descriptor.approved_template(template_id)
    if template is None:
        # Either the descriptor is not APPROVED, or no such template --
        # both are "nothing here may authorize a command", never an error.
        return ConstructedCommand(
            status=CommandConstructionStatus.NO_APPROVED_TEMPLATE,
            detail="no approved command template for this operation",
        )

    prerequisites = satisfied_prerequisites or {}
    unmet = [p for p in (*descriptor.prerequisites, *template.prerequisites) if not prerequisites.get(p, False)]
    if unmet:
        return ConstructedCommand(
            status=CommandConstructionStatus.PROHIBITED,
            detail=f"unmet prerequisites: {sorted(unmet)}",
        )
    if template.prohibitions or descriptor.prohibitions:
        # Prohibitions are recorded governance statements, not
        # machine-evaluable predicates. A template carrying any is never
        # auto-constructed: a human-readable prohibition must be honoured
        # by a person, and silently ignoring it would be worse than
        # withholding the command.
        return ConstructedCommand(
            status=CommandConstructionStatus.PROHIBITED,
            detail="operation or template declares prohibitions requiring human review",
        )

    values = _verified_values(verified_parameters)
    filled = template.template
    used_targets: list[tuple[str, str]] = []
    for name in template.placeholder_names:
        definition = descriptor.parameter(name)
        value = values.get(name)
        if value is None:
            if definition is not None and not definition.required:
                continue
            return ConstructedCommand(
                status=CommandConstructionStatus.MISSING_PARAMETER,
                detail=f"no verified value for parameter {name!r}",
            )
        if definition is not None:
            if definition.allowed_values and value not in definition.allowed_values:
                return ConstructedCommand(
                    status=CommandConstructionStatus.PARAMETER_NOT_ALLOWED,
                    detail=f"value for parameter {name!r} is outside the governed allowlist",
                )
            if definition.kind == OperationParameterKind.TARGET_IDENTIFIER:
                canonical = canonical_identifier(value)
                if canonical is None:
                    return ConstructedCommand(
                        status=CommandConstructionStatus.PARAMETER_NOT_ALLOWED,
                        detail=f"parameter {name!r} is a target identifier but its verified value is not identifier-shaped",
                    )
                if definition.identifier_class and not canonical.startswith(
                    f"{definition.identifier_class.strip().upper()}-"
                ):
                    return ConstructedCommand(
                        status=CommandConstructionStatus.PARAMETER_NOT_ALLOWED,
                        detail=f"parameter {name!r} value is not of the governed identifier class",
                    )
                value = canonical
            if definition.kind in (OperationParameterKind.TARGET_IDENTIFIER, OperationParameterKind.TARGET_TYPE):
                used_targets.append((name, value))
        filled = filled.replace("{" + name + "}", value)

    # THE independent, construction-method-agnostic target check.
    targets_ok, unverified = verify_command_targets(filled, verified_parameters)
    if not targets_ok:
        return ConstructedCommand(
            status=CommandConstructionStatus.UNVERIFIED_TARGET_IN_COMMAND,
            detail=f"constructed command names {len(unverified)} unverified target identifier(s)",
        )

    return ConstructedCommand(
        status=CommandConstructionStatus.CONSTRUCTED,
        command=filled,
        binding=CommandCandidateBinding(
            operation_id=descriptor.operation_id,
            knowledge_id=descriptor.knowledge_id,
            version_label=descriptor.version_label,
            section_id=descriptor.section_id,
            template_id=template.template_id,
            target_parameters=tuple(sorted(used_targets)),
            payload=filled,
        ),
    )


def binding_for_verbatim_command(
    descriptor: Optional[GovernedOperationDescriptor],
    command: str,
    verified_parameters: Sequence[RequestParameter],
) -> CommandCandidateBinding:
    """The repair-5 binding for a command authorized through the EXISTING
    verbatim path -- so invalidation works identically whether a command
    was constructed or grounded verbatim."""
    targets = sorted(
        (param.name, param.value)
        for param in verified_parameters
        if canonical_identifier(param.value) is not None
    )
    return CommandCandidateBinding(
        operation_id=descriptor.operation_id if descriptor is not None else "verbatim",
        knowledge_id=descriptor.knowledge_id if descriptor is not None else None,
        version_label=descriptor.version_label if descriptor is not None else None,
        section_id=descriptor.section_id if descriptor is not None else None,
        template_id=None,
        target_parameters=tuple(targets),
        payload=command,
    )


def binding_supersedes(
    previous: Optional[CommandCandidateBinding], current: Optional[CommandCandidateBinding]
) -> bool:
    """POST-6A REPAIR 5 -- `True` when `previous` must be treated as
    INVALID given `current`: any difference in operation identity, source
    knowledge/version/section, template, target parameters, or payload.

    Fail-closed asymmetry is deliberate: a previous binding with no
    current one to compare against is invalidated (the thing it was bound
    to is gone), while no previous binding at all is nothing to
    invalidate.
    """
    if previous is None:
        return False
    if current is None:
        return True
    return previous != current


class InvalidationReason(str, Enum):
    TARGET_CHANGED = "target_changed"
    OPERATION_CHANGED = "operation_changed"
    PROCEDURE_VERSION_CHANGED = "procedure_version_changed"
    PAYLOAD_CHANGED = "payload_changed"


def invalidation_reasons(
    previous: Optional[CommandCandidateBinding], current: Optional[CommandCandidateBinding]
) -> tuple[InvalidationReason, ...]:
    """Safe, typed diagnostics for WHY a prior candidate/approval was
    invalidated -- closed enum values only, never a target value or a
    command string."""
    if previous is None or not binding_supersedes(previous, current):
        return ()
    if current is None:
        return (InvalidationReason.OPERATION_CHANGED,)
    reasons: list[InvalidationReason] = []
    if previous.target_parameters != current.target_parameters:
        reasons.append(InvalidationReason.TARGET_CHANGED)
    if previous.operation_id != current.operation_id or previous.section_id != current.section_id:
        reasons.append(InvalidationReason.OPERATION_CHANGED)
    if previous.knowledge_id != current.knowledge_id or previous.version_label != current.version_label:
        reasons.append(InvalidationReason.PROCEDURE_VERSION_CHANGED)
    if previous.payload != current.payload or previous.template_id != current.template_id:
        reasons.append(InvalidationReason.PAYLOAD_CHANGED)
    return tuple(reasons)


def target_correction_invalidates(parameters: Sequence[RequestParameter]) -> bool:
    """POST-6A REPAIR 5 -- `True` when THIS turn's own verified context
    corrected a previously-confirmed target value (`corrects_prior_value`,
    stamped deterministically by repair 1's verification). That alone is
    enough to invalidate any command candidate or approval bound to the
    superseded target, without needing the old binding object itself.
    """
    return any(
        param.corrects_prior_value is not None and canonical_identifier(param.value) is not None
        for param in parameters
    )


class AuthorizedCommand(BaseModel):
    """POST-6A REPAIR 6 -- a command that has passed EVERY gate, carried
    as a validated OBJECT rather than as a loose string. This is what a
    response plan's command block references; the renderer emits
    `command` only from one of these."""

    model_config = ConfigDict(frozen=True)

    command: str
    binding: CommandCandidateBinding
    grounding: str = Field(description="How it was authorized: 'verbatim' or 'template'.")


def enforce_verified_command_targets(
    guidance: "TroubleshootingGuidance", verified_parameters: Sequence[RequestParameter]
) -> tuple["TroubleshootingGuidance", bool, tuple[str, ...]]:
    """POST-6A REPAIR 4 -- the target check applied to the EXISTING
    verbatim-grounding path, one layer above `evidence.py`.

    Verbatim grounding proves a command is genuinely, unalteredly present
    in the active governed section. It does NOT prove the command's
    target is the user's target -- and for a parameterized governed
    procedure the verbatim string is, by construction, the SOURCE's own
    EXAMPLE (`...FieldReplaceableUnit=RRU-9...`). Emitting it for a user
    whose verified unit is `RRU-3` is exactly the "a source example for
    RRU-9 must never be authorized for user target RRU-3" failure.

    So: every command still standing after grounding is re-checked
    against this turn's own verified effective-request parameters, and
    any command naming an identifier the user did not supply is STRIPPED
    (never rewritten -- rewriting governed prose is precisely what this
    repair forbids; the correct way to reach a correctly-targeted command
    is `construct_command_from_template`).

    Returns `(guidance, stripped, unverified_identifiers)`. Runs
    unconditionally, so it also covers a command that carries no
    identifier at all -- for which it is a no-op.
    """
    unverified_all: set[str] = set()
    stripped = False

    ok, unverified = verify_command_targets(guidance.command, verified_parameters)
    new_command = guidance.command
    if not ok:
        unverified_all.update(unverified)
        new_command = None
        stripped = True

    new_steps = []
    for step in guidance.full_procedure_steps:
        step_ok, step_unverified = verify_command_targets(step.command, verified_parameters)
        if step_ok:
            new_steps.append(step)
            continue
        unverified_all.update(step_unverified)
        new_steps.append(step.model_copy(update={"command": None}))
        stripped = True

    if not stripped:
        return guidance, False, ()
    return (
        guidance.model_copy(update={"command": new_command, "full_procedure_steps": new_steps}),
        True,
        tuple(sorted(unverified_all)),
    )


_MIN_TEMPLATE_FRAGMENT_CHARS = 8
"""How long an invariant run of an approved template must be before it is
treated as a recognizable command skeleton. Short runs (` 1 1 1`, `all`)
appear in ordinary prose; long ones (`accn FieldReplaceableUnit=`) do
not. Deliberately a length threshold on GOVERNED template text, never a
curated list of command words."""


def command_argument_identifiers(text: Optional[str]) -> set[str]:
    """POST-6A PROMPT 3 -- unit identifiers appearing in COMMAND ARGUMENT
    form, as opposed to ordinary prose mention.

    A plain narrative sentence may legitimately name a unit ("check
    RRU-3's power LED") -- that is description, not instruction. What a
    narrative field must never carry is an identifier bound into command
    syntax (`FieldReplaceableUnit=RRU-3`), because that is an executable
    fragment regardless of whether the surrounding command happens to be
    one the runtime already "knows".

    The discriminator is structural: a whitespace token that BOTH
    contains a command separator AND resolves to a recognized identifier.
    No regex, no command parser, no keyword list.
    """
    if not text:
        return set()
    found: set[str] = set()
    for token in text.split():
        if not any(separator in token for separator in _COMMAND_ARGUMENT_SEPARATORS):
            continue
        normalized = token
        for separator in _COMMAND_ARGUMENT_SEPARATORS:
            normalized = normalized.replace(separator, " ")
        found.update(mention.value for mention in extract_identifier_mentions(normalized))
    return found


def operational_instruction_fragments(
    descriptors: Sequence[Optional[GovernedOperationDescriptor]],
) -> frozenset[str]:
    """The invariant literal runs of every APPROVED command template on
    `descriptors` -- the command SKELETON, with placeholders removed.

    This is what makes the narrative boundary structural rather than a
    blacklist: the fragments are derived from validated operation objects
    (what governance approved as runnable), so a narrative field
    restating a command is caught even when the exact filled string was
    never a value the runtime structurally knew this turn.
    """
    fragments: set[str] = set()
    for descriptor in descriptors:
        if descriptor is None or not descriptor.is_approved:
            continue
        for template in descriptor.command_templates:
            rest = template.template
            run: list[str] = []
            while rest:
                open_at = rest.find("{")
                if open_at < 0:
                    run.append(rest)
                    break
                run.append(rest[:open_at])
                close_at = rest.find("}", open_at + 1)
                if close_at < 0:
                    break
                rest = rest[close_at + 1 :]
                fragments.add("".join(run))
                run = []
            if run:
                fragments.add("".join(run))
    return frozenset(
        fragment.strip() for fragment in fragments if len(fragment.strip()) >= _MIN_TEMPLATE_FRAGMENT_CHARS
    )


def narrative_carries_operational_authority(
    text: Optional[str],
    *,
    descriptors: Sequence[Optional[GovernedOperationDescriptor]] = (),
    unauthorized_command_values: frozenset[str] = frozenset(),
    restricted_target_values: frozenset[str] = frozenset(),
) -> bool:
    """POST-6A PROMPT 3 -- `True` when a narrative field is carrying an
    authority-bearing instruction and must not be rendered.

    THREE INDEPENDENT CHECKS, in decreasing structural strength:

      1. COMMAND-ARGUMENT IDENTIFIERS (`command_argument_identifiers`) --
         an identifier bound into command syntax is executable content,
         full stop. Independent of any known-command inventory.
      2. APPROVED-TEMPLATE SKELETONS (`operational_instruction_fragments`)
         -- derived from validated operation objects, so it catches a
         restated command whose exact filled form the runtime never held.
      3. KNOWN-COMMAND CONTAINMENT -- the pre-existing check, retained
         explicitly as DEFENSE IN DEPTH, never as the primary boundary.
      4. RESTRICTED TARGET VALUES -- the specific unit identifiers a
         withheld command named, populated only when something was
         actually suppressed this turn.

    NONE OF THESE PROVES A SENTENCE CONTAINS NO INSTRUCTION, and this
    function does not claim to. Detection over arbitrary prose cannot:
    "power-cycle the unit and re-seat the fibre" carries no command
    syntax, no template fragment and no identifier. That is exactly why
    the PRIMARY boundary is structural -- instruction-bearing fields are
    not rendered at all when authority was withheld
    (`build_response_plan`'s `operational_narrative_permitted`), and
    command text originates only from validated operation blocks. This
    function is the second line, never the first.

    Authority-bearing instructions are expected to arrive through
    validated COMMAND/OPERATIONAL_STEP blocks instead; this function's
    job is only to ensure the narrative channel cannot become a parallel
    route to the same effect.
    """
    if not text:
        return False
    if command_argument_identifiers(text):
        return True
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for fragment in operational_instruction_fragments(descriptors):
        if fragment in normalized:
            return True
    if any(value and value in normalized for value in unauthorized_command_values):
        return True
    # 4. RESTRICTED TARGET VALUES -- applied when a command for THIS
    #    target was withheld this turn. Naming the specific unit in
    #    prose alongside a suppressed command is how "do X on RRU-3"
    #    reappears in words, and no syntax check can see it. Scoped
    #    deliberately: this set is empty unless something was actually
    #    suppressed, so ordinary prose mentioning a unit is unaffected.
    if restricted_target_values:
        mentions = {m.value for m in extract_identifier_mentions(normalized)}
        if mentions & restricted_target_values:
            return True
    return False
