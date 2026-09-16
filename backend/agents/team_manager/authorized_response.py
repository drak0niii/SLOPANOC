"""CONTROL-PLANE-SEQ-05 -- Authorized Response Context.

THE GAP THIS CLOSES: every existing safety layer up to and including the
final, post-specialist `derive_execution_decision` call (request_execution_
policy.py, UNCHANGED by this module) answers "what is the runtime ALLOWED
to do this turn" -- but nothing deterministically carries that answer
forward into ONE small, typed, current-turn object the final response-
producing/validating code can consult without re-deriving policy itself.
This module is that one object, and the ONE deterministic constructor that
produces it -- never a second policy engine, never a redefinition of
`RequestExecutionDecision`'s own authority.

WHAT THIS IS NOT: `AuthorizedResponseContext` grants nothing on its own --
it is a faithful, narrowed PROJECTION of an already-computed
`RequestExecutionDecision`, plus a small, explicitly-computed command
inventory (see `extract_known_commands_from_guidance`, below, and this
turn's own `grounded_command_candidate`). It is never persisted (current-
turn-only, exactly like `WorkEnvelope`), never written to by any tool or
model, and never itself re-derives `may_emit_command`/`may_emit_
operational_steps`/`may_execute_action` -- those are copied VERBATIM from
the decision that already computed them.
"""
from __future__ import annotations

from typing import Optional, Sequence

from pydantic import BaseModel, Field

from backend.agents.incident_manager.schemas import TroubleshootingGuidance
from backend.agents.team_manager.request_execution_policy import RequestExecutionDecision, RequestExecutionStatus


def extract_known_commands_from_guidance(guidance: Optional[TroubleshootingGuidance]) -> set[str]:
    """Deterministic, structural-only extraction of every exact command
    value a `TroubleshootingGuidance` object currently carries -- section
    6's own "known structured operational command values" list, applied to
    the ONE existing schema this codebase already uses for command-bearing
    content. Never scrapes free text (`interpretation`/`next_action`/
    `action`/`evidence_requested` are never inspected here -- those are
    exactly the fields DEF-0045 concerns a LEAKED copy of a known command
    reaching, never a SOURCE of a new one) -- only the two dedicated,
    typed `command` fields the schema itself defines
    (`TroubleshootingGuidance.command` for `NEXT_STEP` mode,
    `TroubleshootingStep.command` for each `FULL_PROCEDURE` step).

    `guidance=None` (no troubleshooting_guidance this turn at all) returns
    an empty set -- there is nothing structurally known to protect or
    authorize.
    """
    if guidance is None:
        return set()
    commands: set[str] = set()
    if guidance.command:
        commands.add(guidance.command)
    for step in guidance.full_procedure_steps:
        if step.command:
            commands.add(step.command)
    return commands


class AuthorizedResponseContext(BaseModel):
    """The smallest immutable, current-turn-only record of what the FINAL
    response layer is allowed to use -- see this module's own docstring
    for the full "projection, never a second policy engine" rationale.

    `authorized_commands` -- the CLOSED allowlist of exact command strings
    that may legitimately appear in the final rendered response. Always
    empty when `may_emit_command` is `False` (enforced by the ONE
    constructor below, `build_authorized_response_context`, regardless of
    what a caller passes in -- section 4's own explicit, non-negotiable
    rule).

    `known_commands` -- every exact command value this turn structurally
    knew about at all (grounded candidate, pre-policy guidance/step
    commands), authorized or not. `prohibited_commands` (below) is the
    derived set the final-output validator actually checks free text
    against: everything KNOWN, minus everything AUTHORIZED.
    """

    status: str
    request_class: Optional[str] = None
    requested_output: Optional[str] = None
    may_emit_command: bool = False
    may_emit_operational_steps: bool = False
    may_execute_action: bool = False
    clarification_required: bool = False
    authorized_commands: frozenset[str] = Field(default_factory=frozenset)
    known_commands: frozenset[str] = Field(default_factory=frozenset)
    reason: str = ""

    @property
    def prohibited_commands(self) -> frozenset[str]:
        """The exact set `final_output_validator.validate_final_output`
        checks the rendered response against -- every structurally-known
        command this turn that is NOT on the closed authorized allowlist.
        Empty whenever nothing is known (the overwhelming majority of
        non-operational turns -- section 18's own "lightweight for genuine
        general conversation" requirement), or when every known command is
        also authorized (the fully-permitted EXACT_COMMAND case).
        """
        return frozenset(self.known_commands - self.authorized_commands)


def build_authorized_response_context(
    decision: RequestExecutionDecision,
    *,
    authorized_commands: Sequence[str] = (),
    known_commands: Sequence[str] = (),
) -> AuthorizedResponseContext:
    """THE ONE deterministic constructor for `AuthorizedResponseContext` --
    never call the model, never inspect raw text, never re-derive policy.
    `authorized_commands` is unconditionally forced to empty whenever
    `decision.may_emit_command` is `False` -- section 4's own explicit
    rule, enforced HERE, structurally, regardless of what a caller
    (mistakenly or otherwise) supplies -- the caller-facing invariant this
    function exists to guarantee is that `may_emit_command=False` ALWAYS
    means `authorized_commands` is empty, with no code path around it.
    """
    effective_authorized = frozenset(c for c in authorized_commands if c) if decision.may_emit_command else frozenset()
    return AuthorizedResponseContext(
        status=decision.status,
        request_class=decision.request_class,
        requested_output=decision.requested_output,
        may_emit_command=decision.may_emit_command,
        may_emit_operational_steps=decision.may_emit_operational_steps,
        may_execute_action=decision.may_execute_action,
        clarification_required=decision.status
        in (RequestExecutionStatus.NEEDS_INFORMATION, RequestExecutionStatus.AMBIGUOUS),
        authorized_commands=effective_authorized,
        known_commands=frozenset(c for c in known_commands if c),
        reason=decision.reason,
    )
