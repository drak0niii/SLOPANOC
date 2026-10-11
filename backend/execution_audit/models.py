"""Action intent owner and derived execution/timeline views; journal remains immutable."""
from sqlalchemy import String,Integer,JSON,CheckConstraint,Index,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from backend.observability.models import UTCDateTime
from backend.observability.hybrid.models import HybridBase

class ActionExecutionRecord(HybridBase):
    __tablename__='execution_action_record'
    execution_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    action_id: Mapped[str | None]=mapped_column(String(128))
    authorization_id: Mapped[str]=mapped_column(String(36))
    approval_id: Mapped[str | None]=mapped_column(String(128))
    session_id: Mapped[str | None]=mapped_column(String(128))
    run_id: Mapped[str | None]=mapped_column(String(36))
    turn_id: Mapped[str | None]=mapped_column(String(128))
    action_type: Mapped[str]=mapped_column(String(64))
    safe_target_reference: Mapped[str | None]=mapped_column(String(128))
    state: Mapped[str]=mapped_column(String(16))
    attempt: Mapped[int]=mapped_column(Integer)
    started_at: Mapped[object]=mapped_column(UTCDateTime)
    updated_at: Mapped[object]=mapped_column(UTCDateTime)
    # Exact gateway request ID, when actually allocated; no fabricated ack ID.
    downstream_request_id: Mapped[str | None]=mapped_column(String(36))
    __table_args__=(CheckConstraint("state IN ('AUTHORIZED','DISPATCHING','ACKNOWLEDGED','COMPLETED','FAILED','UNKNOWN') AND attempt >= 1"),
        CheckConstraint("action_type IN ('teams.createChat','teams.sendMessage')"),
        Index('ix_execution_action_run','environment','run_id','started_at','execution_id'))

class AuditTimelineRecord(HybridBase):
    __tablename__='execution_audit_timeline'
    environment: Mapped[str]=mapped_column(String(16),primary_key=True)
    event_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    run_id: Mapped[str | None]=mapped_column(String(36))
    occurred_at: Mapped[object]=mapped_column(UTCDateTime)
    processed_at: Mapped[object]=mapped_column(UTCDateTime)
    payload_hash: Mapped[str]=mapped_column(String(64))
    event: Mapped[dict]=mapped_column(JSON)
    __table_args__=(Index('ix_execution_timeline_run','environment','run_id','occurred_at','event_id'),)

class ToolExecutionRecord(HybridBase):
    """Derived durable lifecycle record for actual agent/retrieval/tool handles."""
    __tablename__='execution_activity_record'
    environment: Mapped[str]=mapped_column(String(16),primary_key=True)
    execution_id: Mapped[str]=mapped_column(String(128),primary_key=True)
    domain: Mapped[str]=mapped_column(String(16))
    run_id: Mapped[str | None]=mapped_column(String(36))
    session_id: Mapped[str | None]=mapped_column(String(128))
    turn_id: Mapped[str | None]=mapped_column(String(128))
    started_at: Mapped[object | None]=mapped_column(UTCDateTime)
    terminal_at: Mapped[object | None]=mapped_column(UTCDateTime)
    outcome: Mapped[str]=mapped_column(String(16))
    terminal_event_id: Mapped[str | None]=mapped_column(String(36))
    __table_args__=(CheckConstraint("domain IN ('agent','retrieval','tool')"),
        Index('ix_execution_activity_run','environment','run_id','execution_id'))
