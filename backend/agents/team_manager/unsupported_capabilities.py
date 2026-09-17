"""POST-6A -- Explicitly unavailable operational capabilities.

THE GAP THIS CLOSES: ITSM, alarm/fault, topology/inventory, KPI, change
and handover integrations do not exist. `ContextOrigin` already reserves
enum values for them (so the vocabulary need not change when they are
built), and `ContextDimension` has `ALARM`/`FAULT`/`SERVICE_IMPACT`
dimensions -- which is exactly the shape that invites a reasoning layer
to believe it can consult them. Nothing deterministic said "these are not
connected", so the only thing standing between a user and a fabricated
ticket number or a made-up alarm list was prompt wording.

WHAT THIS IS: a closed registry of capabilities that are NOT implemented,
and the one deterministic sentence the runtime says about each. Never a
stub connector, never a placeholder that returns empty results (an empty
alarm list is indistinguishable from "no alarms", which is a lie), and
never an apology that implies the data exists but is temporarily
unreachable.

WHY A REGISTRY RATHER THAN A PROMPT RULE: a prompt rule is advice. This
is checked in code and produces fixed, Python-authored text, so a
capability cannot become "supported" because a model sounded confident.
Adding a real integration means deleting its entry here -- a deliberate,
visible edit -- never leaving the entry and hoping the runtime notices.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

__all__ = [
    "UnsupportedCapability",
    "UNSUPPORTED_CAPABILITY_TEXT",
    "unsupported_capability_text",
]


class UnsupportedCapability(str, Enum):
    """Operational integrations named in the roadmap and NOT built. Each
    is genuinely absent -- there is no client, no credential, no schema
    and no stub anywhere in this backend."""

    ITSM = "itsm"
    ALARM = "alarm"
    TOPOLOGY = "topology"
    INVENTORY = "inventory"
    KPI = "kpi"
    CHANGE = "change"
    HANDOVER = "handover"
    REMOTE_EXECUTION = "remote_execution"
    """The assistant cannot run anything on a network element. Listed
    alongside the integrations deliberately: "I ran the command and it
    returned X" is the same class of fabrication as inventing a ticket,
    and is the more dangerous one because it implies operational state
    changed."""


UNSUPPORTED_CAPABILITY_TEXT: dict[UnsupportedCapability, str] = {
    UnsupportedCapability.ITSM: (
        "I'm not connected to an ITSM system, so I can't look up, create, or update tickets. "
        "If you have the ticket details, tell me and I'll work from what you give me."
    ),
    UnsupportedCapability.ALARM: (
        "I'm not connected to an alarm or fault management system, so I can't see current alarms. "
        "If you paste or describe what the alarm says, I can work from that."
    ),
    UnsupportedCapability.TOPOLOGY: (
        "I'm not connected to a topology system, so I can't look up how elements are connected."
    ),
    UnsupportedCapability.INVENTORY: (
        "I'm not connected to an inventory system, so I can't look up what hardware or software is deployed."
    ),
    UnsupportedCapability.KPI: (
        "I'm not connected to a KPI or observability system, so I can't see performance counters or trends."
    ),
    UnsupportedCapability.CHANGE: (
        "I'm not connected to a change management system, so I can't see recent or planned changes."
    ),
    UnsupportedCapability.HANDOVER: (
        "I'm not connected to a shift handover system, so I can't see handover notes."
    ),
    UnsupportedCapability.REMOTE_EXECUTION: (
        "I can't run commands on network elements myself -- I can only tell you what the approved "
        "procedure says to run. You'll need to run it and tell me what it returned."
    ),
}
"""Fixed, deterministic, Python-authored. Each one states plainly that
the capability is ABSENT (not failing, not slow, not restricted) and,
where it helps, names the one thing the user CAN do instead. None of them
implies a retry would work."""


def unsupported_capability_text(capability: UnsupportedCapability) -> str:
    return UNSUPPORTED_CAPABILITY_TEXT[capability]


def capability_for_context_origin(origin_value: str) -> Optional[UnsupportedCapability]:
    """Maps a `ContextOrigin` value to its unsupported capability, if it
    is one. Used so a context assertion can never be recorded as having
    come from an integration that does not exist -- the origin enum
    reserves those values for the future, which must not be mistaken for
    them being reachable now."""
    try:
        return UnsupportedCapability(origin_value)
    except ValueError:
        return None
