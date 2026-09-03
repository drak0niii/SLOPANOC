"""Synthetic Teams conversation fixtures for semantic-classification
quality work (decisions/actions/proposals/open questions/risks).

Named with a leading underscore (not `test_*`) so pytest never tries to
collect it as a test module itself -- mirrors `_fakes.py`.

Generic participant names only ("User A"/"User B"/"User C") -- no real
employee names or private conversation content, per instruction. Content
is intentionally simple and unambiguous about which category it
illustrates, for use as live-Gemini acceptance fixtures later (see
docs/... "final acceptance" -- not implemented in this pass); the
automated suite here only checks that these fixtures are well-formed and
survive the deterministic retrieval pipeline unchanged, per the test
strategy in this task ("do not attempt to unit-test Gemini's semantic
judgment itself").

Each fixture is a list of raw Teams message dicts in the same wire shape
`teams_get_messages`/`_parse_messages` already expect (see
backend/tools/teams/get_messages.py): `id`, `createdDateTime`,
`senderName`, `contentType`, `content`.
"""
from __future__ import annotations

from typing import Any


def _msg(msg_id: str, sender: str, content: str, sent_at: str) -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": sender,
        "contentType": "html",
        "content": content,
    }


# A. CONFIRMED DECISION -- proposal, explicit agreement, confirmed direction.
DECISION_CONFIRMED = [
    _msg("a1", "User A", "<p>Should we move the deployment to Friday?</p>", "2026-08-25T09:00:00Z"),
    _msg("a2", "User B", "<p>I think Friday works better for the team.</p>", "2026-08-25T09:01:00Z"),
    _msg("a3", "User A", "<p>Agreed, let's go with Friday. Confirmed.</p>", "2026-08-25T09:02:00Z"),
]

# B. PROPOSAL ONLY -- suggested approach, discussion, no acceptance.
PROPOSAL_ONLY = [
    _msg("b1", "User A", "<p>What if we tried caching the results instead?</p>", "2026-08-25T10:00:00Z"),
    _msg("b2", "User B", "<p>That could work, worth considering.</p>", "2026-08-25T10:01:00Z"),
    _msg("b3", "User A", "<p>Let's keep discussing before deciding.</p>", "2026-08-25T10:02:00Z"),
]

# C. ACTION WITH OWNER -- work clearly assigned to a participant.
ACTION_WITH_OWNER = [
    _msg(
        "c1",
        "User A",
        "<p>User B, can you update the deployment script by Friday?</p>",
        "2026-08-25T11:00:00Z",
    ),
    _msg("c2", "User B", "<p>Sure, I will handle that.</p>", "2026-08-25T11:01:00Z"),
]

# D. ACTION WITHOUT OWNER -- work identified but no owner assigned.
ACTION_WITHOUT_OWNER = [
    _msg(
        "d1",
        "User A",
        "<p>Someone needs to update the documentation before release.</p>",
        "2026-08-25T12:00:00Z",
    ),
    _msg("d2", "User B", "<p>Good point, that still needs to happen.</p>", "2026-08-25T12:01:00Z"),
]

# E. OPEN QUESTION -- a question asked and never resolved.
OPEN_QUESTION = [
    _msg(
        "e1",
        "User A",
        "<p>Do we know which environment this should run in?</p>",
        "2026-08-25T13:00:00Z",
    ),
    _msg("e2", "User B", "<p>Not yet, we still need to figure that out.</p>", "2026-08-25T13:01:00Z"),
]

# F. RISK + MITIGATION -- a stated risk, and a separately discussed mitigation.
RISK_WITH_MITIGATION = [
    _msg("f1", "User A", "<p>Required approval may delay deployment.</p>", "2026-08-25T14:00:00Z"),
    _msg(
        "f2",
        "User B",
        "<p>Let's submit the approval request early to reduce that risk.</p>",
        "2026-08-25T14:01:00Z",
    ),
]

# G. MIXED CONVERSATION -- one confirmed decision, two actions, one
# proposal, one open question, one risk, all in the same chat.
MIXED_CONVERSATION = [
    _msg(
        "g1",
        "User A",
        "<p>We agreed to move the deployment to Friday. Confirmed.</p>",
        "2026-08-25T15:00:00Z",
    ),
    _msg(
        "g2",
        "User A",
        "<p>User B, please update the deployment script by Friday.</p>",
        "2026-08-25T15:01:00Z",
    ),
    _msg(
        "g3",
        "User B",
        "<p>Will do. I will also notify the support team.</p>",
        "2026-08-25T15:02:00Z",
    ),
    _msg(
        "g4",
        "User C",
        "<p>What if we also added a rollback step?</p>",
        "2026-08-25T15:03:00Z",
    ),
    _msg(
        "g5",
        "User A",
        "<p>Do we know if the rollback step is required for this release?</p>",
        "2026-08-25T15:04:00Z",
    ),
    _msg(
        "g6",
        "User B",
        "<p>Required approval may delay the deployment if we wait too long.</p>",
        "2026-08-25T15:05:00Z",
    ),
]

# H. SOCIAL / INCIDENTAL CONVERSATION -- casual questions, a joke,
# reactions, no substantive unresolved issue and nothing material. Used to
# check the model does not over-classify incidental banter into formal
# categories (materiality gate).
SOCIAL_INCIDENTAL_CONVERSATION = [
    _msg("h1", "User A", "<p>Good morning everyone!</p>", "2026-08-26T09:00:00Z"),
    _msg("h2", "User B", "<p>Morning! Anyone want coffee?</p>", "2026-08-26T09:01:00Z"),
    _msg("h3", "User C", "<p>Haha sure, I'm always in for coffee \U0001F600</p>", "2026-08-26T09:02:00Z"),
    _msg("h4", "User A", "<p>lol classic</p>", "2026-08-26T09:03:00Z"),
    _msg("h5", "User B", "<p>\U0001F44D</p>", "2026-08-26T09:04:00Z"),
    _msg("h6", "User C", "<p>Anyone catch the game last night?</p>", "2026-08-26T09:05:00Z"),
]

# I. MIXED SUBSTANTIVE + SOCIAL -- a real project decision/action/proposal
# interspersed with incidental social chatter, in the same chat. Used to
# check substantive items are retained while incidental content around
# them is not promoted into formal categories.
MIXED_SUBSTANTIVE_AND_SOCIAL = [
    _msg("i1", "User A", "<p>Good morning team!</p>", "2026-08-27T09:00:00Z"),
    _msg("i2", "User B", "<p>Morning! Coffee first, then work \U0001F600</p>", "2026-08-27T09:01:00Z"),
    _msg(
        "i3",
        "User A",
        "<p>We agreed to move the deployment to Friday. Confirmed.</p>",
        "2026-08-27T09:02:00Z",
    ),
    _msg("i4", "User C", "<p>haha nice, Friday deploys are the best</p>", "2026-08-27T09:03:00Z"),
    _msg(
        "i5",
        "User A",
        "<p>User B, can you update the deployment script by Friday?</p>",
        "2026-08-27T09:04:00Z",
    ),
    _msg("i6", "User B", "<p>Sure, I will handle that.</p>", "2026-08-27T09:05:00Z"),
    _msg("i7", "User C", "<p>Anyone want to grab lunch later?</p>", "2026-08-27T09:06:00Z"),
]

# J. CASUAL CONCERN WITHOUT MATERIAL IMPACT -- a passing remark that could
# superficially resemble a risk, but is not framed as materially affecting
# delivery/execution/schedule/quality/security/compliance/cost. Used to
# check the model does not manufacture a risk from mere inconvenience.
CASUAL_CONCERN_WITHOUT_MATERIAL_IMPACT = [
    _msg(
        "j1",
        "User A",
        "<p>The office coffee machine is broken again, kind of annoying.</p>",
        "2026-08-28T09:00:00Z",
    ),
    _msg("j2", "User B", "<p>Haha yeah, hope they fix it soon.</p>", "2026-08-28T09:01:00Z"),
]

ALL_FIXTURES: dict[str, list[dict[str, Any]]] = {
    "decision_confirmed": DECISION_CONFIRMED,
    "proposal_only": PROPOSAL_ONLY,
    "action_with_owner": ACTION_WITH_OWNER,
    "action_without_owner": ACTION_WITHOUT_OWNER,
    "open_question": OPEN_QUESTION,
    "risk_with_mitigation": RISK_WITH_MITIGATION,
    "mixed_conversation": MIXED_CONVERSATION,
    "social_incidental_conversation": SOCIAL_INCIDENTAL_CONVERSATION,
    "mixed_substantive_and_social": MIXED_SUBSTANTIVE_AND_SOCIAL,
    "casual_concern_without_material_impact": CASUAL_CONCERN_WITHOUT_MATERIAL_IMPACT,
}
