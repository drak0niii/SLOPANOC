"""POST-6A REPAIR 6 -- Typed Authorized Response Plan.

THE GAP THIS CLOSES: the PRIMARY output-authority mechanism was a
blacklist. `final_output_validator.validate_final_output` took whatever
text had been assembled and searched it for known-bad command strings.
A blacklist can only ever reject what it already knows about, so it has
two structural holes it can never close:

  - a command the runtime never structurally "knew" (never appeared in a
    typed `command` field this turn) is invisible to it, so free text
    remains a parallel, unpoliced route to an executable instruction;
  - it is a scan over model prose, which means safety depends on
    recognizing text rather than on controlling what gets emitted.

WHAT THIS IS: the inversion. The final response is CONSTRUCTED from a
typed plan rather than scanned after the fact. A plan is a sequence of
typed blocks; a `COMMAND` block does not carry a string at all -- it
references an `AuthorizedCommand` object that has already passed
grounding, policy, and the repair-4 target check. `render_response_plan`
is the only thing that turns a plan into text, and it emits command text
from nowhere except those validated objects.

FREE TEXT IS STRUCTURALLY NOT A ROUTE. Narrative blocks exist (an answer
still needs prose), but:
  - narrative content is taken only from the typed, already-grounded
    guidance fields, never from the model's own free-form `summary`;
  - a plan built while `may_emit_command` is `False` cannot contain a
    COMMAND block at all -- `build_response_plan` refuses to create one;
  - every narrative block is passed through `_reject_executable_leak`,
    which drops a block that restates any command value the plan did not
    authorize.

DEFENSE IN DEPTH IS RETAINED, NOT REPLACED: `validate_final_output`'s
known-command check still runs afterwards, unchanged, on the rendered
result. It is now the SECOND line rather than the first. Keeping it costs
nothing and covers the case where a plan block's own text was built from
something this module did not anticipate.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
)
from backend.agents.team_manager.command_construction import (
    AuthorizedCommand,
    narrative_carries_operational_authority,
)
from backend.knowledge.domain.operation_descriptor import GovernedOperationDescriptor


class ResponseBlockKind(str, Enum):
    NARRATIVE = "narrative"
    """Plain descriptive prose. Never carries an executable instruction."""

    COMMAND = "command"
    """Renders exactly one `AuthorizedCommand`. Never free text."""

    OPERATIONAL_STEP = "operational_step"
    """One numbered procedure step: narrative action text plus, at most,
    one referenced `AuthorizedCommand`."""

    CLARIFICATION = "clarification"
    """A deterministic, Python-authored question. Never model text."""


class ResponseBlock(BaseModel):
    """One renderable unit. A block either carries text (NARRATIVE/
    CLARIFICATION/the action half of OPERATIONAL_STEP) or references a
    validated command object -- never a command as a loose string."""

    model_config = ConfigDict(frozen=True)

    kind: ResponseBlockKind
    text: str = ""
    command: Optional[AuthorizedCommand] = None

    def model_post_init(self, _context: object) -> None:  # noqa: D401
        if self.kind == ResponseBlockKind.COMMAND and self.command is None:
            raise ValueError("a COMMAND block must reference a validated AuthorizedCommand")
        if self.kind in (ResponseBlockKind.NARRATIVE, ResponseBlockKind.CLARIFICATION) and self.command is not None:
            raise ValueError("only COMMAND/OPERATIONAL_STEP blocks may reference a command")


class AuthorizedResponsePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocks: tuple[ResponseBlock, ...] = ()
    dropped_narrative_blocks: int = Field(
        default=0,
        description=(
            "How many narrative blocks were refused because they restated an unauthorized command. Safe "
            "diagnostic only -- the refused text is never retained."
        ),
    )

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def authorized_command_values(self) -> frozenset[str]:
        return frozenset(
            block.command.command for block in self.blocks if block.command is not None
        )


def _reject_executable_leak(
    text: str,
    unauthorized_values: frozenset[str],
    descriptors: Sequence[Optional[GovernedOperationDescriptor]] = (),
    restricted_target_values: frozenset[str] = frozenset(),
) -> bool:
    """`True` when this narrative block must be dropped because it is
    carrying an authority-bearing instruction.

    POST-6A PROMPT 3: this no longer rests on known-command containment.
    It delegates to `narrative_carries_operational_authority`
    (command_construction.py), whose primary checks are STRUCTURAL --
    identifiers bound into command-argument syntax, and the invariant
    skeletons of APPROVED command templates (derived from validated
    operation objects). Known-command containment survives inside that
    function as defense in depth only.

    WHY THIS MATTERS: `action`, `interpretation`, `next_action` and
    `evidence_requested` are typed FIELDS, but their CONTENT is free
    text. A typed string is not an authority boundary, and a blacklist of
    values the runtime happened to know this turn can only ever reject
    what it has already seen. Authority-bearing instructions must come
    from validated operation blocks; this is what stops the narrative
    channel becoming a parallel route to the same effect.
    """
    return narrative_carries_operational_authority(
        text,
        descriptors=descriptors,
        unauthorized_command_values=unauthorized_values,
        restricted_target_values=restricted_target_values,
    )


def build_response_plan(
    guidance: Optional[TroubleshootingGuidance],
    *,
    may_emit_command: bool,
    authorized_commands: Sequence[AuthorizedCommand] = (),
    unauthorized_command_values: frozenset[str] = frozenset(),
    clarification_text: Optional[str] = None,
    descriptors: Sequence[Optional[GovernedOperationDescriptor]] = (),
    operational_narrative_permitted: bool = True,
    restricted_target_values: frozenset[str] = frozenset(),
) -> AuthorizedResponsePlan:
    """Build the typed plan for THIS turn from already-validated inputs.

    `guidance` must already have been through grounding (`evidence.py`),
    policy (`enforce_execution_decision_on_guidance`), and the repair-4
    target check -- this function performs no authorization of its own; it
    only refuses to render what was not authorized.

    `authorized_commands` is the closed set of validated command objects
    that may appear. A command value present in the guidance but NOT in
    this set is never rendered: the plan simply contains no block for it.
    """
    authorized_by_value = {c.command: c for c in authorized_commands}
    blocks: list[ResponseBlock] = []
    dropped = 0

    # POST-6A -- INSTRUCTION FIELDS ARE NOT A SECOND COMMAND CHANNEL.
    #
    # `next_action` and `step.action` are INSTRUCTION fields: their whole
    # purpose is to tell the engineer to do something. `interpretation`
    # and `evidence_requested` are EXPLANATION/REQUEST fields.
    #
    # Command-syntax detection and template-skeleton matching are real
    # checks, but they are defense in depth -- neither can prove an
    # arbitrary sentence contains no operational instruction, because
    # "power-cycle the unit and then re-seat the fibre" contains no
    # command syntax at all. So when this turn is operationally shaped
    # and command authority was NOT granted, the instruction fields are
    # not rendered at all: authority-bearing direction may come only from
    # a validated COMMAND/OPERATIONAL_STEP block built from an approved
    # operation. Explanation and the request for evidence are preserved,
    # which is what keeps the answer useful rather than blank.
    def _instruction_narrative(text: Optional[str]) -> None:
        if not operational_narrative_permitted:
            nonlocal dropped
            if text:
                dropped += 1
            return
        _narrative(text)

    def _narrative(text: Optional[str]) -> None:
        nonlocal dropped
        if not text:
            return
        if _reject_executable_leak(
            text, unauthorized_command_values, descriptors, restricted_target_values
        ):
            dropped += 1
            return
        blocks.append(ResponseBlock(kind=ResponseBlockKind.NARRATIVE, text=text))

    if guidance is not None:
        _narrative(guidance.interpretation)
        if guidance.interaction_mode == TroubleshootingInteractionMode.FULL_PROCEDURE:
            for step in guidance.full_procedure_steps:
                step_command = (
                    authorized_by_value.get(step.command)
                    if (may_emit_command and step.command)
                    else None
                )
                if step.action and (
                    not operational_narrative_permitted
                    or _reject_executable_leak(
                        step.action, unauthorized_command_values, descriptors, restricted_target_values
                    )
                ):
                    dropped += 1
                    continue
                blocks.append(
                    ResponseBlock(
                        kind=ResponseBlockKind.OPERATIONAL_STEP,
                        text=step.action or "",
                        command=step_command,
                    )
                )
        else:
            _instruction_narrative(guidance.next_action)
            if may_emit_command and guidance.command:
                authorized = authorized_by_value.get(guidance.command)
                if authorized is not None:
                    blocks.append(ResponseBlock(kind=ResponseBlockKind.COMMAND, command=authorized))
            _narrative(guidance.evidence_requested)

    if clarification_text:
        blocks.append(ResponseBlock(kind=ResponseBlockKind.CLARIFICATION, text=clarification_text))

    return AuthorizedResponsePlan(blocks=tuple(blocks), dropped_narrative_blocks=dropped)


def render_response_plan(plan: AuthorizedResponsePlan) -> str:
    """The ONE deterministic renderer. Produces byte-identical output for
    identical input; never reorders, paraphrases, or synthesizes. Command
    text is emitted from `block.command.command` and from nowhere else.

    Formatting deliberately matches `render_troubleshooting_guidance`'s
    own established conventions (blank-line separated blocks, `Run:` and
    numbered steps) so this change is invisible to the user.
    """
    rendered: list[str] = []
    step_number = 0
    for block in plan.blocks:
        if block.kind == ResponseBlockKind.OPERATIONAL_STEP:
            step_number += 1
            text = f"{step_number}. {block.text}" if block.text else f"{step_number}."
            if block.command is not None:
                text += f"\n\nRun:\n\n{block.command.command}"
            rendered.append(text)
        elif block.kind == ResponseBlockKind.COMMAND and block.command is not None:
            rendered.append(f"Run:\n\n{block.command.command}")
        elif block.text:
            rendered.append(block.text)
    return "\n\n".join(rendered)
