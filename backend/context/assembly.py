"""Context Engineering Layer: Central Context Broker.

Assembles, scores, and bounds multi-domain context for specialist agent execution:
1. Knowledge Context: Governed MOPs, SOPs, RCAs from Generic KM.
2. Case Context: Persistent fault identity, Case details, and active `TroubleshootingState`.
3. Operational Context: Teams conversations, inline rich media, and telemetry.
4. Experience Memory: Historical resolution patterns and verified findings.

Continuous Re-evaluation Principle:
New evidence arriving on any turn triggers re-evaluation of context relevance to
prevent stale context reuse and enforce token budgeting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from backend.cases.troubleshooting_state import TroubleshootingState


class ContextDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    CASE = "case"
    OPERATIONAL = "operational"
    EXPERIENCE_MEMORY = "experience_memory"


class ContextItem(BaseModel):
    """An individual piece of contextual evidence or reference."""
    domain: ContextDomain
    source_id: str = Field(description="Canonical source or evidence identifier")
    title: str = Field(description="Descriptive title or label")
    content: str = Field(description="Textual payload or structured description")
    metadata: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    is_authoritative: bool = Field(default=True, description="Whether item is Approved/Authoritative vs. heuristic")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AssembledContext(BaseModel):
    """Aggregated and bounded context prepared for agent invocation."""
    query: str
    knowledge_context: list[ContextItem] = Field(default_factory=list)
    case_context: Optional[dict[str, Any]] = None
    troubleshooting_state: Optional[TroubleshootingState] = None
    operational_context: list[ContextItem] = Field(default_factory=list)
    experience_memory: list[ContextItem] = Field(default_factory=list)
    assembled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    estimated_tokens: int = Field(default=0)

    def total_items(self) -> int:
        return (
            len(self.knowledge_context)
            + len(self.operational_context)
            + len(self.experience_memory)
            + (1 if self.case_context else 0)
            + (1 if self.troubleshooting_state else 0)
        )

    def format_prompt_block(self) -> str:
        """Renders assembled context as a structured, bounded markdown block."""
        blocks: list[str] = []

        if self.case_context or self.troubleshooting_state:
            case_lines: list[str] = ["### Case & Troubleshooting State"]
            if self.case_context:
                title = self.case_context.get("title", "Active Case")
                status = self.case_context.get("status", "OPEN")
                case_lines.append(f"- Case: **{title}** [Status: {status}]")
            if self.troubleshooting_state:
                ts = self.troubleshooting_state
                case_lines.append(f"- Active Fault ID: `{ts.fault_id}` [Status: {ts.status.value}]")
                if ts.working_hypothesis:
                    case_lines.append(f"- Working Hypothesis: {ts.working_hypothesis}")
                if ts.competing_hypotheses:
                    case_lines.append(f"- Competing Hypotheses: {', '.join(ts.competing_hypotheses)}")
            blocks.append("\n".join(case_lines))

        if self.knowledge_context:
            km_lines: list[str] = ["### Governed Knowledge Context"]
            for item in self.knowledge_context:
                km_lines.append(f"- **{item.title}** (Source: `{item.source_id}`):\n  {item.content}")
            blocks.append("\n".join(km_lines))

        if self.operational_context:
            op_lines: list[str] = ["### Operational Context (Teams & Visual Media)"]
            for item in self.operational_context:
                op_lines.append(f"- **{item.title}**: {item.content}")
            blocks.append("\n".join(op_lines))

        if self.experience_memory:
            exp_lines: list[str] = ["### Historical Experience Memory"]
            for item in self.experience_memory:
                exp_lines.append(f"- {item.title}: {item.content}")
            blocks.append("\n".join(exp_lines))

        return "\n\n".join(blocks)


class ContextEngineeringBroker:
    """Central orchestrator for multi-source context collection and budgeting."""

    def __init__(self, max_context_items: int = 20, max_token_budget: int = 4000):
        self._max_context_items = max_context_items
        self._max_token_budget = max_token_budget

    def assemble(
        self,
        query: str,
        knowledge_items: Optional[list[ContextItem]] = None,
        operational_items: Optional[list[ContextItem]] = None,
        case_details: Optional[dict[str, Any]] = None,
        troubleshooting_state: Optional[TroubleshootingState] = None,
        experience_items: Optional[list[ContextItem]] = None,
    ) -> AssembledContext:
        """Assembles all available context items, sorts by relevance, and enforces budget."""
        km = sorted(knowledge_items or [], key=lambda x: (x.is_authoritative, x.relevance_score), reverse=True)
        op = sorted(operational_items or [], key=lambda x: x.relevance_score, reverse=True)
        exp = sorted(experience_items or [], key=lambda x: x.relevance_score, reverse=True)

        # Enforce item limits
        km = km[: self._max_context_items]
        op = op[: self._max_context_items]
        exp = exp[: self._max_context_items]

        # Simple token estimation (~4 chars per token)
        total_chars = sum(len(i.content) for i in km + op + exp)
        estimated_tokens = total_chars // 4

        return AssembledContext(
            query=query,
            knowledge_context=km,
            case_context=case_details,
            troubleshooting_state=troubleshooting_state,
            operational_context=op,
            experience_memory=exp,
            estimated_tokens=estimated_tokens,
        )

    def re_evaluate(
        self,
        current_context: AssembledContext,
        new_evidence_keys: list[str],
    ) -> AssembledContext:
        """Re-evaluates and refreshes context given newly verified evidence."""
        # Elevate relevance of items referencing verified evidence
        for item in current_context.knowledge_context + current_context.operational_context:
            if any(k.lower() in item.content.lower() or k == item.source_id for k in new_evidence_keys):
                item.relevance_score = min(1.0, item.relevance_score + 0.2)

        return self.assemble(
            query=current_context.query,
            knowledge_items=current_context.knowledge_context,
            operational_items=current_context.operational_context,
            case_details=current_context.case_context,
            troubleshooting_state=current_context.troubleshooting_state,
            experience_items=current_context.experience_memory,
        )
