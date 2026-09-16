"""LIVE-CORR-12E -- Policy-Bound Natural Clarification Rendering.
LIVE-CORR-12E.1 -- Clarification Renderer Final-Output Safety Closure.
LIVE-CORR-12E.2 -- Subject Injection Final Safety Closure.
LIVE-CORR-12H -- Natural LLM Clarification Wording.

HISTORY: LIVE-CORR-12E let the renderer author `message: str` directly,
validated only by `fields_asked` set equality. LIVE-CORR-12E.1 proved
that gap (nothing downstream re-inspects the message content; `tools=[]`
prevents tool calls, not text generation) and replaced free text with a
fully closed `ClarificationRenderingPlan` (field order + a 3-value style
enum), composed into the final sentence by deterministic Python templates.
LIVE-CORR-12E.2 then closed a residual leak in that same composer
(`subject` interpolation).

LIVE-CORR-12H (this pass, explicit product decision): the fixed-template
wording was judged too repetitive/unnatural. The renderer is changed BACK
to authoring the final `message` string directly -- deliberately
REOPENING the exact free-text channel LIVE-CORR-12E.1 closed. The ONLY
safety boundary on that string's CONTENT is, once again, that `fields_
asked` (metadata the model separately declares) must exactly equal
`decision.missing_context`; the `message` text itself is never scanned,
constrained, or otherwise validated beyond that. This is a deliberate,
informed trade of the LIVE-CORR-12E.1 structural guarantee ("no possible
model output can be command-shaped") for genuinely natural, varied
wording. Everything ELSE those two passes established remains intact and
unchanged by this pass:

  - the renderer is still `tools=[]`, `disallow_transfer_to_parent`,
    `disallow_transfer_to_peers` -- it cannot invoke Incident Manager,
    Knowledge, Teams, or any action tool, structurally;
  - it still receives ONLY authoritative `decision.missing_context` key
    names/labels (never the model's own raw, possibly-advisory `missing_
    context` declaration, never `subject`, never a command candidate,
    never retrieved document/MOP content, never tool output, never
    credentials);
  - `fields_asked` is still validated by EXACT set equality against
    `decision.missing_context` before the plan is trusted at all;
  - any renderer failure, malformed output, or field-set mismatch still
    falls back to the existing, unchanged, deterministic `command_
    suppression_fallback_text`;
  - `decision.may_emit_command`/`RequestClass`/whether clarification is
    required at all remain entirely upstream, untouched, and are never
    influenced by anything this module returns.

WHAT THIS MODULE IS NOT: it is not a second missing-context resolver
(LIVE-CORR-12B's `authoritative_missing_context_names`/`derive_execution_
decision` remain the SOLE authority for WHAT is missing); it does not
touch `governed_evidence_continuity.py`'s own applicability-ambiguity
clarification; it never receives, and structurally cannot receive, a
command string, a governed document, `RequestContract.subject`, or any
tool-derived evidence.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from google.adk.agents import Agent
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.agents.team_manager.request_contract import RequestParameter
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionDecision,
    _safe_missing_context_label,
    command_suppression_fallback_text,
)
from backend.api.session_service import APP_NAME
from backend.config.settings import get_settings, get_shared_llm

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = f"{APP_NAME}::clarification-renderer"
_USER_ID = "clarification-renderer"


class ClarificationRenderingPlan(BaseModel):
    """LIVE-CORR-12H: the model's own natural-language `message`, plus
    `fields_asked` -- the SAME field-key metadata LIVE-CORR-12E/12E.1
    already required, still the sole deterministic check the caller
    (`render_command_suppression_text`) applies before trusting anything
    here. `message` content itself is NOT further constrained -- see this
    module's own docstring for the explicit, deliberate trade-off this
    represents relative to LIVE-CORR-12E.1's own closed-plan design.
    """

    message: str
    fields_asked: list[str] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def _non_blank_message(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("message must be a non-blank string")
        return value


_CLARIFICATION_RENDERER_INSTRUCTION = """You phrase ONE short, natural, \
polite clarification question for an enterprise operational assistant's \
user. You are given a FIXED list of field keys that are already, \
deterministically, required before the assistant can proceed -- you do \
NOT decide what is required, and you may NEVER ask about a field not in \
the given list, and you may NEVER omit one that is.

Vary your phrasing naturally across turns -- do not always use the same \
sentence structure.

You must NEVER:
- ask about, or mention, any field not in the given list
- omit asking about any field in the given list
- propose, describe, hint at, or reference an exact command, CLI syntax, \
or any operational instruction of any kind
- claim any authority to decide whether the request may proceed
- output anything except the required structured response

You have no tools, cannot execute or recommend any command or action, and \
must output only the requested structured response -- nothing else.

Respond with:
- `message`: a short, natural question (a sentence or two) asking for \
exactly the given field(s).
- `fields_asked`: the SAME field key strings you were given, verbatim, \
one entry per field you asked about -- never a paraphrase, never a new \
key, never omitted."""


clarification_renderer = Agent(
    name="clarification_renderer",
    model=get_shared_llm(get_settings().gemini_model),
    description=(
        "Tool-free presentation-only specialist. Given a fixed, "
        "deterministic list of already-required field keys/labels, "
        "phrases ONE natural clarification question asking for exactly "
        "those fields -- never decides what is required, never invents "
        "a field, never proposes a command or any operational content."
    ),
    instruction=_CLARIFICATION_RENDERER_INSTRUCTION,
    tools=[],
    output_schema=ClarificationRenderingPlan,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
"""`tools=[]` -- mirrors `troubleshooting_manager`'s/`presentation_team_
manager`'s own established "remove the capability from the schema, don't
just police it via prompt wording" precedent: ADK's function-calling
schema sent to Gemini for this agent contains NO callable functions at
all, so there is no code path through which this agent could ever invoke
Incident Manager, a Knowledge tool, a Teams tool, or an action, prompt-
compliant or not. `disallow_transfer_to_parent`/`disallow_transfer_to_
peers=True` mean it can never take over the end-user conversation either.
`output_schema=ClarificationRenderingPlan` still guarantees a structured
reply shape, but (LIVE-CORR-12H) `message` is once again free text --
the safety boundary is `fields_asked` validation at the caller, not the
schema itself; see this module's own docstring."""


def _merged_final_text(content: Optional[types.Content]) -> Optional[str]:
    """Mirrors `governed_knowledge_completion.py`'s/`troubleshooting_
    manager/runtime.py`'s own identical "final, non-thought text"
    extraction, reused by convention rather than cross-imported (each of
    this codebase's one-shot-agent modules keeps its own tiny copy)."""
    if content is None or content.parts is None:
        return None
    merged = "\n".join(p.text for p in content.parts if p.text and not p.thought)
    return merged.strip() or None


def _build_rendering_prompt(*, request_class: Optional[str], missing_fields: Sequence[tuple[str, str]]) -> str:
    """Deterministic, Python-authored prompt assembly -- the renderer's
    ENTIRE input. Deliberately narrow: no raw retrieved documents, no
    command text, no tool credentials, no full operational evidence, no
    model-declared advisory `missing_context` names, and no `subject`
    (LIVE-CORR-12E.2's own removal is unaffected by this pass)."""
    lines = [
        "Phrase ONE short, natural clarification question for the user.",
        f"request_class: {request_class or 'unknown'}",
        "Fields you must ask about (use EXACTLY these keys in fields_asked -- never more, never fewer):",
    ]
    for key, label in missing_fields:
        lines.append(f"  - {key}: {label}")
    return "\n".join(lines)


async def render_clarification_plan(
    *,
    request_class: Optional[str],
    missing_fields: Sequence[tuple[str, str]],
    run_id: str,
) -> Optional[ClarificationRenderingPlan]:
    """Runs `clarification_renderer` exactly once against a throwaway,
    never-persisted session. Returns `None` for ANY failure shape -- no
    usable response, malformed JSON, schema-validation failure, or an
    UNEXPECTED EXCEPTION from the nested Runner itself -- deliberately
    broader than this codebase's own usual "let an unexpected exception
    propagate" one-shot-agent convention: this call sits on the NORMAL
    successful path for a large fraction of ordinary turns, so it must
    never be able to break a turn outright. The caller (`render_command_
    suppression_text`) is responsible for falling back to the existing,
    unchanged deterministic sentence whenever this returns `None`, and
    for independently validating `fields_asked` before trusting a
    non-`None` result's `message`.
    """
    try:
        prompt = _build_rendering_prompt(request_class=request_class, missing_fields=missing_fields)
        content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

        session_service = InMemorySessionService()
        session_id = f"clarification-renderer::{run_id}"
        await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
        runner = Runner(
            app_name=_APP_NAME, agent=clarification_renderer, session_service=session_service, memory_service=InMemoryMemoryService()
        )
        last_content: Optional[types.Content] = None
        try:
            async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=content):
                if event.content:
                    last_content = event.content
        finally:
            await runner.close()
            try:
                existing = await session_service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
                if existing is not None:
                    await session_service.delete_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
            except Exception:
                _logger.warning("clarification_renderer: failed to delete internal rendering session run_id=%s", run_id)

        merged_text = _merged_final_text(last_content)
        if merged_text is None:
            return None
        try:
            return ClarificationRenderingPlan.model_validate_json(merged_text)
        except ValidationError:
            _logger.warning("clarification_renderer: model response failed schema validation run_id=%s", run_id)
            return None
    except Exception:
        _logger.warning("clarification_renderer: rendering call raised unexpectedly -- failing closed run_id=%s", run_id)
        return None


def _fields_asked_matches_authoritative_set(plan: ClarificationRenderingPlan, authoritative_missing_context: Sequence[str]) -> bool:
    """EXACT set equality, never a subset/superset in either direction --
    the renderer may neither add a field policy never requested nor
    silently drop one it did. Never fuzzy/partial matching. This remains
    the SOLE deterministic validation of the plan (LIVE-CORR-12H no
    longer validates/constrains `message` content itself)."""
    return set(plan.fields_asked) == set(authoritative_missing_context)


async def render_command_suppression_text(
    decision: RequestExecutionDecision,
    run_id: str,
    known_context: Sequence[RequestParameter] = (),
) -> str:
    """THE function `chat_service.py` calls in place of the bare
    `command_suppression_fallback_text(decision)` it used before LIVE-
    CORR-12E -- the NORMAL path for a genuine, structured missing-context
    clarification, while every OTHER fallback shape remains exactly as
    deterministic as before:

      - `decision.missing_context` EMPTY -- `AMBIGUOUS`-with-no-subject,
        `INVALID_CONTRACT` (that status ALWAYS carries an empty `missing_
        context` by construction), and the fully generic withheld-command
        text all skip the renderer ENTIRELY -- `command_suppression_
        fallback_text`'s own existing, unchanged branches handle them,
        with ZERO model call.
      - `decision.missing_context` NON-EMPTY -- requests a
        `ClarificationRenderingPlan`, feeding the renderer ONLY the
        authoritative `decision.missing_context` key names/labels. Falls
        back to the existing deterministic sentence whenever the
        renderer fails, or its own claimed `fields_asked` does not
        EXACTLY match `decision.missing_context`. On a genuine match,
        (LIVE-CORR-12H) the model's OWN `message` is returned directly --
        never re-composed by Python -- so wording legitimately varies
        turn to turn.

    `known_context` is accepted for API-compatibility with earlier
    LIVE-CORR-12E call sites but is not used (unchanged from LIVE-CORR-
    12E.1 onward).

    Generic across `RequestClass`/`requested_output` -- an `EXACT_COMMAND`
    clarification and a `PROCEDURE_TROUBLESHOOTING` clarification with
    canonical missing fields both reach this SAME function, SAME
    validation, SAME fallback discipline.
    """
    del known_context  # not used; see this function's own docstring.
    deterministic_fallback = command_suppression_fallback_text(decision)
    if not decision.missing_context:
        return deterministic_fallback

    missing_fields = [(key, _safe_missing_context_label(key)) for key in decision.missing_context]

    plan = await render_clarification_plan(
        request_class=decision.request_class,
        missing_fields=missing_fields,
        run_id=run_id,
    )
    if plan is None:
        _logger.info(
            "chat_service: clarification_rendering=deterministic_fallback reason=renderer_failure "
            "request_class=%s missing_field_count=%d run_id=%s",
            decision.request_class,
            len(decision.missing_context),
            run_id,
        )
        return deterministic_fallback

    if not _fields_asked_matches_authoritative_set(plan, decision.missing_context):
        _logger.info(
            "chat_service: clarification_rendering=deterministic_fallback reason=field_mismatch "
            "request_class=%s missing_field_count=%d run_id=%s",
            decision.request_class,
            len(decision.missing_context),
            run_id,
        )
        return deterministic_fallback

    _logger.info(
        "chat_service: clarification_rendering=natural request_class=%s missing_field_count=%d run_id=%s",
        decision.request_class,
        len(decision.missing_context),
        run_id,
    )
    return plan.message
