"""POST-6A REPAIR 1 -- Exact, Boundary-Safe Operational Target Verification.

THE GAP THIS CLOSES: `_verify_and_filter_provided_context` (request_
contract.py) verified a model-proposed `provided_context` value by a raw,
case-insensitive SUBSTRING test against the current turn's own user text
(`value_lower in current_lower`). For an operational TARGET IDENTIFIER
that is unsafe in three independent, separately-demonstrable ways:

  1. PREFIX COLLISION -- `"RRU-9"` is a literal substring of `"RRU-90"`,
     so a user who wrote "restart RRU-90" verified a model claim of
     `unit_id=RRU-9`. A wrong physical unit is the single most dangerous
     failure this system can produce.
  2. POLARITY BLINDNESS -- "it is NOT RRU-9", "any RRU except RRU-9",
     "RRU-3 rather than RRU-9" all literally CONTAIN "RRU-9", so the
     excluded unit verified as the confirmed target.
  3. EXAMPLE CAPTURE -- a user quoting a governed document's own example
     ("the doc says `... FieldReplaceableUnit=RRU-9 ...`, what does that
     mean?") had that EXAMPLE identifier promoted to a verified live
     target -- the exact DEF-0026 family this codebase has fought
     repeatedly, arriving through the user's own text instead of through
     Knowledge.

WHAT THIS IS: one deterministic, offset-preserving recognizer plus a
closed, documented classification of why a mention may NOT be trusted.
No `re`, no NLP, no semantic matching, no fuzzy acceptance -- the SAME
token/boundary discipline `extract_canonical_identifiers` (request_
contract.py) already established, extended only with (a) character spans
and (b) a small, closed exclusion vocabulary.

MATCHING IS EXACT AND WHOLE-IDENTIFIER, NEVER SUBSTRING: a mention is
recognized only as a complete `PREFIX`+digits unit, so `RRU-90` yields
exactly `RRU-90` and never also `RRU-9`. Comparison against a claimed
value is equality of canonical forms, never containment.

BOUNDED, HONESTLY-DOCUMENTED RESIDUAL RISK (the SAME discipline `_ALARM_
STATUS_EVIDENCE_PHRASES` already documents for its own closed phrase
table): the exclusion vocabularies below are CLOSED and small. A negation
or example phrasing they were never given is NOT recognized, and such a
mention is then treated as an ordinary mention. This module never claims
to understand natural language; it claims only that the three concrete
failure shapes above, and any mention it positively recognizes as
excluded or contested, cannot silently become a verified live target.

CONTESTED TARGETS FAIL CLOSED, NOT OPEN: when the current turn mentions
more than one distinct non-excluded identifier, NO identifier is
verified. "Which one did they mean" is exactly the question a machine
must not guess -- the caller turns that into an explicit confirmation
request instead (see `validate_and_persist_request_contract`'s own
`unresolved_target_parameter_names` handling).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

_IDENTIFIER_CLASS_PREFIXES = ("RRU", "AAS")
"""Deliberately the SAME closed unit-class taxonomy `request_contract.py`
already defines -- re-stated here (rather than imported) only because
that module imports THIS one, never the reverse. Kept identical by
`test_post6a_repair_identifier_verification`'s own cross-check; adding a
class requires a deliberate edit in both places."""

_STRIP_CHARS = ".,;:()[]{}\"'?!`"
"""Light leading/trailing punctuation trimming only -- mirrors
`extract_canonical_identifiers`/`evidence._tokenize`'s own established
discipline exactly, never a general tokenizer."""

_NEGATION_MARKERS = frozenset(
    {
        "not",
        "no",
        "never",
        "isn't",
        "isnt",
        "aren't",
        "arent",
        "wasn't",
        "wasnt",
        "don't",
        "dont",
        "doesn't",
        "doesnt",
        "didn't",
        "didnt",
        "won't",
        "wont",
        "nor",
        "neither",
    }
)
"""CLOSED negation vocabulary. A mention whose small preceding window
contains one of these is NEGATED and can never be a verified target."""

_EXCLUSION_MARKERS = frozenset(
    {
        "except",
        "excepting",
        "excluding",
        "excludes",
        "exclude",
        "besides",
        "apart",
        "aside",
        "unless",
        "rather",
        "instead",
        "other",
        "without",
        "avoid",
        "skip",
    }
)
"""CLOSED exclusion vocabulary -- covers "except RRU-9", "other than
RRU-9", "rather than RRU-9", "instead of RRU-9", "apart from RRU-9"."""

_EXAMPLE_MARKERS = frozenset(
    {
        "e.g.",
        "eg",
        "eg.",
        "i.e.",
        "ie",
        "example",
        "examples",
        "such",
        "like",
        "sample",
        "placeholder",
        "template",
        "illustrative",
        "illustration",
        "hypothetical",
        "says",
        "quote",
        "quoted",
        "document",
        "doc",
        "procedure",
    }
)
"""CLOSED example/citation vocabulary -- covers "e.g. RRU-9", "for
example RRU-9", "such as RRU-9", "the document says RRU-9". `says`/
`document`/`doc`/`procedure` are included deliberately: a user restating
what a governed SOURCE says is citing an example, never confirming their
own live target -- the DEF-0026 family, arriving via user text."""

_MARKER_WINDOW = 3
"""How many preceding tokens are inspected for an exclusion marker.
Three covers every phrase in the vocabularies above ("other than X",
"rather than X", "apart from X", "for example X") without reaching back
into an unrelated earlier clause."""

_QUOTE_DELIMITERS = ('"', "'", "`")


class IdentifierMentionStatus(str, Enum):
    """Why one recognized identifier mention may or may not be trusted."""

    ORDINARY = "ordinary"
    """A plain mention with no recognized exclusion marker around it."""

    NEGATED = "negated"
    EXCLUDED = "excluded"
    QUOTED_EXAMPLE = "quoted_example"


_UNTRUSTED_MENTION_STATUSES = frozenset(
    {
        IdentifierMentionStatus.NEGATED,
        IdentifierMentionStatus.EXCLUDED,
        IdentifierMentionStatus.QUOTED_EXAMPLE,
    }
)


class IdentifierVerificationStatus(str, Enum):
    """The outcome of checking ONE claimed identifier value against the
    current turn's own real user text."""

    VERIFIED = "verified"
    """Exactly one non-excluded mention exists, and it is this value."""

    NOT_PRESENT = "not_present"
    """The value does not appear as a whole identifier at all. Ordinary
    and common -- the caller may still verify it through the separate,
    unchanged same-subject session-confirmed carry-forward."""

    EXCLUDED_BY_TEXT = "excluded_by_text"
    """Every mention of this value is negated/excluded/an example. It
    must never be a target, and the caller must ask rather than guess."""

    CONTESTED = "contested"
    """More than one distinct identifier is genuinely in play this turn;
    intent cannot be established safely. The caller must ask."""


class TextSpan(BaseModel):
    """Character offsets of one mention within the text it was found in --
    preserved so a later layer can show/record exactly what evidence
    verified a target, without re-scanning or re-deriving it."""

    model_config = ConfigDict(frozen=True)

    start: int
    end: int


class IdentifierMention(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    """Canonical hyphenated form, e.g. `RRU-9`."""

    span: TextSpan
    status: IdentifierMentionStatus


class IdentifierVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IdentifierVerificationStatus
    value: Optional[str] = None
    """The canonical value, populated only for `VERIFIED`."""

    span: Optional[TextSpan] = None
    """Where in the current-turn text the verifying mention was found."""

    competing_values: tuple[str, ...] = ()
    """For `CONTESTED`: every distinct non-excluded identifier in play,
    sorted -- safe to surface in a confirmation question."""

    @property
    def requires_explicit_confirmation(self) -> bool:
        """`True` when the text POSITIVELY established that this target
        cannot be trusted (as opposed to merely not mentioning it). The
        caller must turn this into an explicit confirmation request
        rather than silently dropping the parameter."""
        return self.status in (
            IdentifierVerificationStatus.EXCLUDED_BY_TEXT,
            IdentifierVerificationStatus.CONTESTED,
        )


def _tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Whitespace tokenization preserving each token's own character
    offsets in the ORIGINAL text. No `re`; a single forward scan."""
    tokens: list[tuple[str, int, int]] = []
    start: Optional[int] = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                tokens.append((text[start:index], start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        tokens.append((text[start:], start, len(text)))
    return tokens


def _canonical_from_fused_token(token: str) -> Optional[str]:
    """`RRU-9`/`RRU9`/`rru-9` -> `RRU-9`; anything else -> `None`.
    Whole-token, digits-only remainder -- this is what makes `RRU-90`
    yield `RRU-90` and never also `RRU-9`."""
    upper = token.upper()
    for prefix in _IDENTIFIER_CLASS_PREFIXES:
        if not upper.startswith(prefix):
            continue
        remainder = upper[len(prefix) :]
        remainder = remainder[1:] if remainder.startswith("-") else remainder
        if remainder and remainder.isdigit():
            return f"{prefix}-{remainder}"
    return None


def _is_inside_quotes(text: str, start: int) -> bool:
    """`True` when the character at `start` sits inside a quoted or
    backticked span opened earlier on the SAME line. Deterministic
    delimiter counting only -- never a parser."""
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    return any(prefix.count(delimiter) % 2 == 1 for delimiter in _QUOTE_DELIMITERS)


def _is_inside_code_fence(text: str, start: int) -> bool:
    """`True` when an odd number of triple-backtick fences precede this
    offset -- i.e. the mention is inside a fenced block, which is quoted
    source material, never a live instruction."""
    return text.count("```", 0, start) % 2 == 1


def _mention_status(text: str, tokens: Sequence[tuple[str, int, int]], index: int, start: int) -> IdentifierMentionStatus:
    if _is_inside_code_fence(text, start) or _is_inside_quotes(text, start):
        return IdentifierMentionStatus.QUOTED_EXAMPLE
    window_start = max(0, index - _MARKER_WINDOW)
    window = [tokens[i][0].strip(_STRIP_CHARS).lower() for i in range(window_start, index)]
    for marker in window:
        if not marker:
            continue
        if marker in _NEGATION_MARKERS:
            return IdentifierMentionStatus.NEGATED
        if marker in _EXCLUSION_MARKERS:
            return IdentifierMentionStatus.EXCLUDED
        if marker in _EXAMPLE_MARKERS:
            return IdentifierMentionStatus.QUOTED_EXAMPLE
    return IdentifierMentionStatus.ORDINARY


def extract_identifier_mentions(text: Optional[str]) -> list[IdentifierMention]:
    """Every recognized operational unit identifier in `text`, with its
    own character span and trust classification, in source order.

    Recognizes the SAME two written forms `extract_canonical_identifiers`
    does -- one fused token (`RRU-9`, `RRU9`) and a bare class prefix
    immediately followed by a purely-numeric token (`RRU 9`) -- and
    nothing else. A number with no adjacent class prefix is never an
    identifier; a prefix with no adjacent number is never an identifier.
    """
    if not text:
        return []

    tokens = _tokens_with_spans(text)
    mentions: list[IdentifierMention] = []
    for index, (raw_token, start, end) in enumerate(tokens):
        stripped = raw_token.strip(_STRIP_CHARS)
        if not stripped:
            continue
        offset = raw_token.find(stripped)
        mention_start = start + (offset if offset >= 0 else 0)
        mention_end = mention_start + len(stripped)

        fused = _canonical_from_fused_token(stripped)
        if fused is not None:
            mentions.append(
                IdentifierMention(
                    value=fused,
                    span=TextSpan(start=mention_start, end=mention_end),
                    status=_mention_status(text, tokens, index, mention_start),
                )
            )
            continue

        upper = stripped.upper()
        if upper in _IDENTIFIER_CLASS_PREFIXES and index + 1 < len(tokens):
            next_raw, _next_start, next_end = tokens[index + 1]
            next_clean = next_raw.strip(_STRIP_CHARS)
            next_clean = next_clean[1:] if next_clean.startswith("-") else next_clean
            if next_clean and next_clean.isdigit():
                mentions.append(
                    IdentifierMention(
                        value=f"{upper}-{next_clean}",
                        span=TextSpan(start=mention_start, end=next_end),
                        status=_mention_status(text, tokens, index, mention_start),
                    )
                )
    return mentions


def canonical_identifier(value: Optional[str]) -> Optional[str]:
    """The canonical form of `value` when it is itself exactly ONE
    identifier, else `None`. Never mutates a non-identifier value (a bare
    `unit_type` of `"RRU"` resolves to `None` here, as it must)."""
    if not value:
        return None
    stripped = value.strip().strip(_STRIP_CHARS)
    if not stripped:
        return None
    fused = _canonical_from_fused_token(stripped)
    if fused is not None:
        return fused
    mentions = extract_identifier_mentions(stripped)
    values = {mention.value for mention in mentions}
    if len(values) == 1:
        (only,) = values
        return only
    return None


def is_identifier_shaped(value: Optional[str]) -> bool:
    """`True` when `value` is itself exactly one recognized operational
    unit identifier -- the discriminator that decides whether the exact
    verification below applies instead of the ordinary substring path."""
    return canonical_identifier(value) is not None


def verify_identifier_against_text(claimed_value: str, text: Optional[str]) -> IdentifierVerification:
    """THE replacement for the substring check, for identifier-shaped
    values only. Deterministic and exact:

      - every mention is recognized WHOLE (`RRU-90` never yields `RRU-9`);
      - negated / excluded / quoted-example mentions are removed from
        consideration entirely;
      - if exactly one distinct identifier survives and it equals the
        claimed value -> `VERIFIED` (with its span);
      - if more than one distinct identifier survives -> `CONTESTED`
        (nothing is verified; the caller must ask);
      - if the claimed value appears ONLY as an excluded mention ->
        `EXCLUDED_BY_TEXT` (the caller must ask);
      - otherwise -> `NOT_PRESENT` (ordinary; the caller may still fall
        back to the unchanged session-confirmed carry-forward).
    """
    canonical_claim = canonical_identifier(claimed_value)
    if canonical_claim is None:
        return IdentifierVerification(status=IdentifierVerificationStatus.NOT_PRESENT)

    mentions = extract_identifier_mentions(text)
    if not mentions:
        return IdentifierVerification(status=IdentifierVerificationStatus.NOT_PRESENT)

    trusted = [m for m in mentions if m.status not in _UNTRUSTED_MENTION_STATUSES]
    trusted_values = {m.value for m in trusted}

    if len(trusted_values) > 1:
        return IdentifierVerification(
            status=IdentifierVerificationStatus.CONTESTED,
            competing_values=tuple(sorted(trusted_values)),
        )

    if canonical_claim in trusted_values:
        match = next(m for m in trusted if m.value == canonical_claim)
        return IdentifierVerification(
            status=IdentifierVerificationStatus.VERIFIED, value=canonical_claim, span=match.span
        )

    if any(m.value == canonical_claim for m in mentions):
        # Present, but every occurrence was negated/excluded/an example.
        return IdentifierVerification(status=IdentifierVerificationStatus.EXCLUDED_BY_TEXT, value=canonical_claim)

    if trusted_values:
        # The user named a DIFFERENT live unit than the model claimed --
        # a conflict, never a near-miss to be resolved in the model's
        # favour.
        return IdentifierVerification(
            status=IdentifierVerificationStatus.CONTESTED,
            competing_values=tuple(sorted(trusted_values | {canonical_claim})),
        )

    return IdentifierVerification(status=IdentifierVerificationStatus.NOT_PRESENT)
