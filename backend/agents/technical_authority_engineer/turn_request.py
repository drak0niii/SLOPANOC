"""Server-built current-turn request contract for the Technical Authority Engineer.

INVARIANT: the EXPLICIT CURRENT-TURN REQUEST takes precedence over the historical
troubleshooting objective. History (active investigation, confirmed session facts) may ENRICH
the request -- e.g. supply a vendor the operator stated earlier -- but can never redefine it.

Mechanics (deterministic, no model call, no vendor/fault vocabulary):
- `user_request_text` is always the operator's exact latest message (never caller text).
- A caller-proposed field (subject, operation, target, vendor, technology, intent) is kept only if
  it is stated in the latest message (token match, tolerant of inflection/typos via a shared
  4-character prefix). A value that only exists in earlier turns is discarded and reported.
- `focus` is `switch` when the latest message explicitly introduces a subject/operation/target
  that the active objective does not contain, or when the caller says it switches focus; a caller
  claim of "continue" never overrides an explicit change in the operator's own words.
- `diagnostic_objective` is built from the latest message when it is explicit; only an implicit
  follow-up (e.g. "yes", "done") continues the active objective.
- `request_kind` (classified by the caller against the fault's PENDING CLARIFICATION, see
  clarification_continuity.py): a clarification follow-up ("what details should I provide?") or a
  clarification answer is not a new objective -- it continues the active investigation, and
  caller-proposed subject/operation/target values that are only meta words or the answer values
  themselves are discarded.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from backend.agents.technical_authority_engineer.schemas import RequestField, TurnRequestContract

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
_STOPWORDS = frozenset(
    """a an and are about as at be can could do does for from how i in is it its me my of on or please
    should so that the then this to us we what whats when where which who why will with would you your
    yes no ok okay now next also there here any some just
    done did ran run executed finished completed result results output got see thanks thank tried""".split()
)
_CONVERSATIONAL = frozenset(
    """okie okey okk alright right sure fine cool great good nice thx cheers hmm hm um uh well
    continue continuing proceed proceeding carry keep going go ahead on onward onwards forward move next step steps
    suggest suggestion suggestions recommend recommendation recommendations advise advice propose think idea ideas
    help guide tell let lets""".split()
)
"""Generic conversational / progression words: they name no technical subject, entity, fault,
target, operation or evidence. A message made only of these (and stop words) adds nothing of its
own -- it asks to go on with whatever investigation is active."""
_MIN_PREFIX = 4
_MAX_TEXT = 1000
_OBJECTIVE_FIELDS = ("subject_component", "requested_operation", "explicit_target")
_FOLLOW_UP = "clarification_follow_up"
_ANSWER = "clarification_answer"


def _tokens(text: str) -> list[str]:
    """Lower-cased tokens. A compound identifier (e.g. `unit-2`, `board_3`) also contributes its
    parts, so a specific target relates to the component it names instead of looking unrelated."""
    out: list[str] = []
    for token in _TOKEN.findall(text or ""):
        token = token.lower()
        out.append(token)
        parts = re.split(r"[-_.]", token)
        if len(parts) > 1:
            out.extend(p for p in parts if p)
    return out


def _content_tokens(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOPWORDS and len(t) >= 2]


def is_continuation_only(text: str) -> bool:
    """True when the message introduces no technical subject of its own: every content token is a
    generic conversational / progression word ("what next?", "what do you suggest?", "okay,
    continue", "and now?", "go ahead"). Token-set based, never a phrase list; any subject, entity,
    identifier, alarm or objective word makes it False."""
    return not [t for t in _content_tokens(text) if t not in _CONVERSATIONAL]


_PROGRESSION_CUES = frozenset(
    """next now then continue continuing proceed proceeding carry keep going go ahead onward onwards forward move
    suggest suggestion suggestions recommend recommendation recommendations advise advice propose""".split()
)
"""Generic progression words: a continuation-only message that contains one ASKS TO GO ON ("what
next?", "and now?", "go ahead", "what do you suggest?"); without one it is merely social or meta
("thanks", "ok", "what can you do?")."""


def is_continuation_request(text: str) -> bool:
    """True when the message introduces no subject of its own (`is_continuation_only`) AND asks to go
    on with the active work (a generic progression word). Token-set based, never a phrase list; no
    vendor, technology, fault or command vocabulary."""
    return is_continuation_only(text) and any(t in _PROGRESSION_CUES for t in _tokens(text))


def _token_matches(token: str, candidates: list[str]) -> bool:
    for cand in candidates:
        if token == cand:
            return True
        n = min(len(token), len(cand))
        if n >= _MIN_PREFIX and token[:_MIN_PREFIX] == cand[:_MIN_PREFIX] and (token.startswith(cand[:n]) or cand.startswith(token[:n])):
            return True
    return False


def _stated_in(value: str, text: str) -> bool:
    """Every content token of `value` appears in `text` (exact, or sharing a >=4-char prefix)."""
    value_tokens = _content_tokens(value)
    text_tokens = _tokens(text)
    return bool(value_tokens) and all(_token_matches(t, text_tokens) for t in value_tokens)


def _partly_stated_in(value: str, text: str) -> bool:
    text_tokens = _tokens(text)
    return any(_token_matches(t, text_tokens) for t in _content_tokens(value))


def _field(proposed: Any, name: str) -> Optional[str]:
    raw = proposed.get(name) if isinstance(proposed, Mapping) else getattr(proposed, name, None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def active_investigation_objective(troubleshooting_state: Any) -> Optional[str]:
    """Background only: the investigation's symptom summary plus its most recent check."""
    if troubleshooting_state is None:
        return None
    parts: list[str] = []
    summary = getattr(troubleshooting_state, "symptom_summary", None)
    if summary:
        parts.append(str(summary))
    history = getattr(troubleshooting_state, "diagnostic_history", None) or []
    if history:
        parts.append(f"last check: {history[-1].action}")
    hypothesis = getattr(troubleshooting_state, "working_hypothesis", None)
    if hypothesis:
        parts.append(f"working hypothesis: {hypothesis}")
    return "; ".join(parts) or None


def build_turn_request_contract(
    proposed: Any,
    operator_text: str,
    troubleshooting_state: Any = None,
    confirmed_facts: Optional[Mapping[str, list[str]]] = None,
    *,
    request_kind: str = "operational",
    answer_values: Optional[list[str]] = None,
) -> TurnRequestContract:
    text = (operator_text or "").strip()[:_MAX_TEXT]
    active = active_investigation_objective(troubleshooting_state)
    discarded: list[dict[str, str]] = []
    answer_tokens = _tokens(" ".join(answer_values or []))

    def _current(name: str) -> Optional[RequestField]:
        value = _field(proposed, name)
        if value is None:
            return None
        if name in _OBJECTIVE_FIELDS and request_kind == _FOLLOW_UP:
            discarded.append({"field": name, "value": value, "reason": "clarification follow-up: not an operational objective"})
            return None
        if name in _OBJECTIVE_FIELDS and is_continuation_only(value):
            discarded.append({"field": name, "value": value, "reason": "generic continuation wording: not an operational objective"})
            return None
        value_tokens = _content_tokens(value)
        if name in _OBJECTIVE_FIELDS and request_kind == _ANSWER and value_tokens and all(_token_matches(t, answer_tokens) for t in value_tokens):
            discarded.append({"field": name, "value": value, "reason": "clarification answer value: not an operational objective"})
            return None
        if _stated_in(value, text):
            return RequestField(value=value, source="current_message")
        discarded.append({"field": name, "value": value, "reason": "not stated in the operator's latest message"})
        return None

    subject = _current("subject_component")
    operation = _current("requested_operation")
    target = _current("explicit_target")

    def _qualifier(name: str) -> Optional[RequestField]:
        stated = _current(name)
        if stated is not None:
            return stated
        values = [v for v in (confirmed_facts or {}).get(name, []) if isinstance(v, str) and v.strip()]
        return RequestField(value=", ".join(values), source="session_confirmed") if values else None

    vendor = _qualifier("vendor")
    technology = _qualifier("technology")

    intent = _field(proposed, "intent")
    if intent is not None and not _partly_stated_in(intent, text):
        discarded.append({"field": "intent", "value": intent, "reason": "not grounded in the operator's latest message"})
        intent = None

    explicit_fields = [f.value for f in (subject, operation, target) if f is not None]
    explicit = bool(explicit_fields)
    active_tokens = _tokens(active or "")
    new_content = [t for t in _content_tokens(text) if not _token_matches(t, active_tokens)]
    continues = proposed.get("continues_active_objective") if isinstance(proposed, Mapping) else getattr(proposed, "continues_active_objective", None)

    if not active:
        focus = "new"
    elif request_kind in (_FOLLOW_UP, _ANSWER) and not explicit:
        # Requested information (or a question about it) refers to the pending clarification of the
        # active investigation: never a change of focus.
        focus = "continue"
    elif not explicit and is_continuation_only(text):
        # The message introduces no subject of its own ("what do you suggest?"): it asks to go on with
        # the active investigation. Words absent from the active objective, or a caller's claim of a
        # switch, never turn it into a new objective.
        focus = "continue"
        if continues is False:
            discarded.append({"field": "continues_active_objective", "value": "false",
                              "reason": "the operator's latest message introduces no new subject"})
    elif continues is False:
        focus = "switch"
    elif explicit:
        focus = "continue" if any(_partly_stated_in(v, active) for v in explicit_fields) else "switch"
    else:
        # No structured fields: an operator message whose content words are all absent from the
        # active objective introduces a new subject; a bare acknowledgement continues.
        content = _content_tokens(text)
        focus = "switch" if content and len(new_content) == len(content) else "continue"

    qualifiers = ", ".join(f"{name}: {f.value}" for name, f in (("vendor", vendor), ("technology", technology)) if f is not None)
    if explicit or focus in ("switch", "new"):
        core = " ".join(v for v in (operation.value if operation else None, subject.value if subject else None, target.value if target else None) if v)
        objective = f"{core or intent or text}" + (f" ({qualifiers})" if qualifiers else "")
        objective = f"{objective} -- operator asked: \"{text}\"" if core else objective
    else:
        objective = f"Continue the active investigation ({active}); operator's latest message: \"{text}\""

    return TurnRequestContract(
        user_request_text=text,
        intent=intent,
        subject_component=subject,
        requested_operation=operation,
        explicit_target=target,
        vendor=vendor,
        technology=technology,
        focus=focus,
        request_kind=request_kind,
        explicit_in_current_message=explicit,
        diagnostic_objective=objective,
        active_investigation_objective=active,
        discarded_fields=discarded,
    )
