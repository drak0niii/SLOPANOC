"""Controlled execution adapters -- DIAGNOSTIC READS ONLY in this tranche.

Boundary rules:
- An adapter receives only an `AuthorizedReadAction`: a process-signed token minted by
  `issue_authorized_read_action`, which requires a genuine `AuthorityAttestation` (Command
  Authority result) AND a PolicyDecision that permits read execution. A free-form string, a
  hand-built or edited action, or a state-changing operation type is rejected before any adapter
  code runs.
- Adapters are resolved only from an explicit registry keyed by execution context. No registered
  adapter -> EXECUTION_UNAVAILABLE. There is no local/shell fallback and no default adapter; this
  module never imports `subprocess`, `os.system` or any shell facility.
- No adapter capable of a state change exists. `ExecutionAdapterRegistry` refuses any operation
  type other than READ_ONLY_DIAGNOSTIC.
- Adapters must execute `action.command` exactly as authorized; they may not rewrite it.
- Execution output is OBSERVED EVIDENCE, never governed knowledge and never command authority.
"""
from __future__ import annotations

import abc
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.technical_authority_engineer.schemas import CommandOperationType
from backend.operations.context import OperationalActionContext, TargetIdentity
from backend.operations.policy import AuthorityAttestation, AuthorityStatus, PolicyDecision
from backend.operations.signing import sign, verify

_READ_ACTION_KIND = "authorized_read_action.v1"
_MAX_CAPTURED_OUTPUT = 20_000


class ExecutionBoundaryError(Exception):
    """An execution request that did not come from the trusted authorization path."""


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "execution_unavailable"


class ExecutionContext(BaseModel):
    execution_context: str
    session_id: Optional[str] = None
    requested_by: str
    check_id: Optional[str] = None


class AuthorizedReadAction(BaseModel):
    """The only input an execution adapter accepts. Signed; never persisted; never model-built."""

    control_id: str
    check_id: Optional[str] = None
    procedure_action_id: str
    command: str
    source_id: str
    operation_type: str
    target: TargetIdentity
    binding_hash: str
    execution_context: str
    signature: str

    def _fields(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"signature"})

    def is_genuine(self) -> bool:
        return verify(_READ_ACTION_KIND, self._fields(), self.signature)


def issue_authorized_read_action(
    context: OperationalActionContext,
    attestation: AuthorityAttestation,
    decision: PolicyDecision,
    execution_context: str,
) -> AuthorizedReadAction:
    if not isinstance(attestation, AuthorityAttestation) or not attestation.is_genuine():
        raise ExecutionBoundaryError("command authority attestation missing or not genuine")
    if attestation.authority_status is not AuthorityStatus.AUTHORIZED:
        raise ExecutionBoundaryError("command is not authorized")
    if attestation.binding_hash != context.binding_hash() or attestation.command != context.command:
        raise ExecutionBoundaryError("attestation does not match the action context")
    if not isinstance(decision, PolicyDecision) or not decision.permits_read_execution:
        raise ExecutionBoundaryError("policy does not permit read execution")
    if attestation.operation_type != CommandOperationType.READ_ONLY_DIAGNOSTIC.value:
        raise ExecutionBoundaryError("only read-only diagnostics can be executed")
    fields = {
        "control_id": context.control_id,
        "check_id": context.check_id,
        "procedure_action_id": context.procedure_action_id,
        "command": context.command,
        "source_id": context.source.canonical_source_id,
        "operation_type": attestation.operation_type,
        "target": context.target.model_dump(mode="json"),
        "binding_hash": context.binding_hash(),
        "execution_context": execution_context,
    }
    return AuthorizedReadAction(**fields, signature=sign(_READ_ACTION_KIND, fields))


class ExecutionResult(BaseModel):
    execution_id: str
    status: ExecutionStatus
    adapter_type: Optional[str] = None
    execution_context: Optional[str] = None
    control_id: Optional[str] = None
    check_id: Optional[str] = None
    procedure_action_id: Optional[str] = None
    command: Optional[str] = None
    target: Optional[TargetIdentity] = None
    started_at: datetime
    completed_at: datetime
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    observed_evidence: Optional[str] = None
    requested_by: Optional[str] = None
    detail: Optional[str] = None


class AdapterOutput(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class ExecutionAdapter(abc.ABC):
    """Generic read-execution adapter. Subclasses implement `_execute_read` for ONE explicitly
    configured execution context (e.g. a sandbox or a read-only management API). They receive the
    already-authorized action and must not reinterpret or modify `action.command`."""

    adapter_type: str = "abstract"

    async def execute(self, action: AuthorizedReadAction, context: ExecutionContext) -> AdapterOutput:
        _assert_trusted(action)
        if action.execution_context != context.execution_context:
            raise ExecutionBoundaryError("action was authorized for a different execution context")
        return await self._execute_read(action, context)

    @abc.abstractmethod
    async def _execute_read(self, action: AuthorizedReadAction, context: ExecutionContext) -> AdapterOutput:
        ...


def _assert_trusted(action: Any) -> None:
    if not isinstance(action, AuthorizedReadAction):
        raise ExecutionBoundaryError("execution adapters accept only an AuthorizedReadAction, never free-form commands")
    if not action.is_genuine():
        raise ExecutionBoundaryError("authorized read action signature invalid (forged or modified)")
    if action.operation_type != CommandOperationType.READ_ONLY_DIAGNOSTIC.value:
        raise ExecutionBoundaryError("state-changing execution is not available")


class ExecutionAdapterRegistry:
    """Explicit execution_context -> adapter registry. Empty by default: production read
    execution exists only where an operator registers an adapter for a configured context."""

    def __init__(self) -> None:
        self._adapters: dict[str, ExecutionAdapter] = {}

    def register(self, execution_context: str, adapter: ExecutionAdapter) -> None:
        if not execution_context or not isinstance(adapter, ExecutionAdapter):
            raise ValueError("an explicit execution context and an ExecutionAdapter are required")
        self._adapters[execution_context] = adapter

    def unregister(self, execution_context: str) -> None:
        self._adapters.pop(execution_context, None)

    def get(self, execution_context: Optional[str]) -> Optional[ExecutionAdapter]:
        return self._adapters.get(execution_context) if execution_context else None

    def is_available(self, execution_context: Optional[str]) -> bool:
        return self.get(execution_context) is not None

    async def execute(self, action: AuthorizedReadAction, context: ExecutionContext) -> ExecutionResult:
        _assert_trusted(action)
        started = datetime.now(timezone.utc)
        base = dict(
            execution_id=f"exec-{uuid.uuid4().hex[:12]}",
            execution_context=context.execution_context,
            control_id=action.control_id,
            check_id=action.check_id,
            procedure_action_id=action.procedure_action_id,
            command=action.command,
            target=action.target,
            requested_by=context.requested_by,
            started_at=started,
        )
        adapter = self.get(context.execution_context)
        if adapter is None:
            return ExecutionResult(
                **base, status=ExecutionStatus.UNAVAILABLE, completed_at=datetime.now(timezone.utc), detail="EXECUTION_UNAVAILABLE"
            )
        try:
            output = await adapter.execute(action, context)
        except ExecutionBoundaryError:
            raise
        except Exception as exc:  # adapter failure: recorded, never retried, never escalated to a write
            return ExecutionResult(
                **base,
                status=ExecutionStatus.FAILED,
                adapter_type=adapter.adapter_type,
                completed_at=datetime.now(timezone.utc),
                detail=f"adapter_error:{type(exc).__name__}",
            )
        stdout = (output.stdout or "")[:_MAX_CAPTURED_OUTPUT]
        return ExecutionResult(
            **base,
            status=ExecutionStatus.SUCCEEDED if output.exit_code == 0 else ExecutionStatus.FAILED,
            adapter_type=adapter.adapter_type,
            completed_at=datetime.now(timezone.utc),
            stdout=stdout,
            stderr=(output.stderr or "")[:_MAX_CAPTURED_OUTPUT],
            exit_code=output.exit_code,
            observed_evidence=stdout,
        )


_default_registry = ExecutionAdapterRegistry()


def get_execution_adapter_registry() -> ExecutionAdapterRegistry:
    return _default_registry
