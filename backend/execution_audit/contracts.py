"""Finite content-free execution facts, including explicit consistency class."""
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from backend.observability.schemas import Contract, Identifier, Count, Duration

JOURNAL_EVENTS=(
 'session.created','session.accessed',
 'agent.execution.started','agent.execution.completed','agent.execution.failed',
 'retrieval.started','retrieval.completed','retrieval.failed',
 'evidence.selection.recorded',
 'tool.execution.started','tool.execution.completed','tool.execution.failed',
 'approval.requested','approval.approved','approval.rejected','approval.expired','approval.consumed',
 'authorization.decided','action.authorized','action.dispatch_attempted',
 'action.acknowledged','action.completed','action.failed','action.outcome_unknown')
JournalType=Literal[*JOURNAL_EVENTS]

class Fact(Contract):
    execution_domain: Literal['session','agent','retrieval','evidence','tool','approval','authorization','action']
    consistency_class: Literal['A','B','C','D']
    agent_execution_id: UUID | None=None
    parent_agent_execution_id: UUID | None=None
    retrieval_id: UUID | None=None
    authorization_id: UUID | None=None
    command_execution_id: UUID | None=None
    attempt_id: UUID | None=None
    actor_type: Literal['user','agent','system']='system'
    actor_reference: Identifier | None=None
    safe_target_reference: Identifier | None=None
    provenance_reference: Identifier | None=None
    source_revision: Identifier | None=None
    tool_name: Identifier | None=None
    retrieval_type: Literal['lexical','hybrid'] | None=None
    source_namespace: Literal['governed_knowledge'] | None=None
    candidate_count: Count | None=None
    selected_count: Count | None=None
    duration_ms: Duration | None=Field(default=None,le=86400000)
    outcome: Literal['STARTED','COMPLETED','FAILED','TIMEOUT','CANCELLED','AUTHORIZED','DENIED','DISPATCHING','ACKNOWLEDGED','UNKNOWN','PENDING','APPROVED','REJECTED','EXPIRED','CONSUMED','RECORDED','ACCESSED','CREATED']
    failure_code: Literal['EXECUTION_ERROR','TIMEOUT','CANCELLED','GATEWAY_OUTCOME_UNKNOWN','AUDIT_UNAVAILABLE','DENIED'] | None=None
    decision: Literal['ALLOW','DENY'] | None=None
    reason_code: Literal['no_pending_proposal','proposal_not_approved','proposal_expired','proposal_rejected','proposal_consumed','operation_mismatch','payload_mismatch','proposal_id_mismatch'] | None=None
    policy_version: Literal['teams-write-v1'] | None=None
    supersedes_event_id: UUID | None=None
