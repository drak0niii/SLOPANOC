"""Deterministic Teams chat-name resolution (interaction-capability
extension).

Centralized so every Teams capability that needs to resolve a chat by
name -- reads (`teams_list_chats`) today, any future one tomorrow -- uses
the SAME matching logic (instruction: "Do not implement one matching
algorithm for sendMessage, another for summarize, another for
getMembers."). Pure, deterministic, stdlib-only (`difflib`) -- never LLM
judgment, never a new ML/search dependency for this.

ALGORITHM (documented, not hidden in a magic number):
  1. Both the requested name and each candidate's title are normalized
     (stripped, casefolded, internal whitespace collapsed).
  2. An EXACT normalized match short-circuits everything else -- if
     exactly one chat's normalized title equals the normalized request,
     that is the match, full stop, no similarity scoring involved at all.
  3. If no exact match, every OTHER chat is scored by a combination of:
     - a whole-string similarity ratio (`difflib.SequenceMatcher`),
     - token (word) overlap (Jaccard similarity over the split words),
     - a small bonus when one normalized string is a prefix of the other
       (the common "X" vs "X Test"/"X Group" real-world case).
  4. Candidates scoring at or above `SIMILARITY_THRESHOLD` are kept,
     sorted by score descending (ties broken by original chat order --
     stable sort), and capped at `MAX_SIMILAR_CANDIDATES`.

Never auto-selects a similar (non-exact) candidate -- that is a decision
this module has no opinion on; it only ever classifies and ranks. Whether
a non-exact result requires user disambiguation is the caller's decision
(see list_chats.py).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from backend.tools.teams.schemas import ChatSummary

# Documented, tunable in one place. 0.45 was chosen so that a real-world
# case like "SLOPANOC Gateway Group" vs "SLOPANOC Gateway Group Test"
# (near-total token/substring overlap) scores well above threshold, while
# two chats that only share a single common word ("SLOPANOC") do not.
SIMILARITY_THRESHOLD = 0.45
MAX_SIMILAR_CANDIDATES = 3


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _similarity_score(requested_normalized: str, candidate_normalized: str) -> float:
    if not requested_normalized or not candidate_normalized:
        return 0.0

    seq_ratio = difflib.SequenceMatcher(None, requested_normalized, candidate_normalized).ratio()

    requested_tokens = set(requested_normalized.split())
    candidate_tokens = set(candidate_normalized.split())
    union = requested_tokens | candidate_tokens
    token_overlap = len(requested_tokens & candidate_tokens) / len(union) if union else 0.0

    prefix_bonus = (
        0.1
        if candidate_normalized.startswith(requested_normalized)
        or requested_normalized.startswith(candidate_normalized)
        else 0.0
    )

    return min(1.0, 0.6 * seq_ratio + 0.4 * token_overlap + prefix_bonus)


class ChatResolutionOutcome:
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass
class ChatResolution:
    outcome: str
    matched_chat: ChatSummary | None = None
    # Populated only for `AMBIGUOUS` -- multiple chats share the exact
    # same normalized title (a real Teams edge case, distinct from "no
    # exact match, but similar titles exist" below).
    exact_duplicates: list[ChatSummary] = field(default_factory=list)
    # Populated only for `NOT_FOUND` when at least one candidate scores
    # at/above SIMILARITY_THRESHOLD -- ordered by score, descending,
    # capped at MAX_SIMILAR_CANDIDATES. Never populated alongside
    # `matched_chat` or `exact_duplicates`.
    similar_candidates: list[ChatSummary] = field(default_factory=list)


def resolve_chat(requested_name: str, chats: list[ChatSummary]) -> ChatResolution:
    """Steps 1-5 of the deterministic resolution order:
    normalize -> exact match -> (else) score candidates -> cap at 3.

    Never invents a chat: every returned `ChatSummary` is one the caller
    (ultimately, `teams.listChats`) actually returned.
    """
    normalized_requested = _normalize(requested_name)

    exact_matches = [chat for chat in chats if _normalize(chat.title) == normalized_requested]
    if len(exact_matches) == 1:
        return ChatResolution(outcome=ChatResolutionOutcome.MATCHED, matched_chat=exact_matches[0])
    if len(exact_matches) > 1:
        return ChatResolution(outcome=ChatResolutionOutcome.AMBIGUOUS, exact_duplicates=exact_matches)

    scored = [(chat, _similarity_score(normalized_requested, _normalize(chat.title))) for chat in chats]
    above_threshold = [pair for pair in scored if pair[1] >= SIMILARITY_THRESHOLD]
    # Stable sort by score descending -- ties keep their original
    # (gateway-returned) relative order, never an arbitrary re-ordering.
    above_threshold.sort(key=lambda pair: pair[1], reverse=True)
    top_candidates = [chat for chat, _score in above_threshold[:MAX_SIMILAR_CANDIDATES]]

    return ChatResolution(outcome=ChatResolutionOutcome.NOT_FOUND, similar_candidates=top_candidates)
