"""Context Engineering Layer for SLOPANOC.

Orchestrates multi-source context assembly across:
- Knowledge Context (Generic KM MOPs, SOPs, RCAs)
- Case Context & Persistent Troubleshooting State
- Operational Context (Teams text transcripts & visual media)
- Experience Memory (historical resolution patterns)
"""
from backend.context.assembly import (
    ContextDomain,
    ContextItem,
    AssembledContext,
    ContextEngineeringBroker,
)

__all__ = [
    "ContextDomain",
    "ContextItem",
    "AssembledContext",
    "ContextEngineeringBroker",
]
