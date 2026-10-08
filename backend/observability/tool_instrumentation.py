"""One logical invocation boundary; closed result adapters never export payloads."""
from contextlib import contextmanager
from .agent_instrumentation import Execution, active_execution, runtime_for_execution, attached_scope, finish_scope
from .model_context import ModelAgent, ModelPurpose
from .model_instrumentation import guarded
from .stages import RunStatus
from .errors import ErrorCode

TOOLS = {
    'incident_manager':'internal_coordination', 'technical_authority_engineer':'internal_coordination',
    'problem_manager':'internal_coordination', 'automated_operations_engineer':'internal_coordination',
    'record_case_analysis':'incident_case', 'record_conversation_target':'internal_coordination',
    'record_source_requirements':'governance', 'teams_list_chats':'teams',
    'teams_get_messages':'teams', 'get_resolved_chat_messages':'teams',
    'teams_get_hosted_content':'teams', 'teams_get_all_hosted_content':'teams', 'teams_get_members':'teams',
    'get_current_time_context':'utility', 'teams_propose_create_chat':'approval',
    'teams_propose_send_message':'approval', 'teams_create_chat':'command_action',
    'teams_send_message':'command_action', 'knowledge_search':'knowledge',
    'knowledge_select_evidence':'governance', 'procedure_action_catalog':'troubleshooting',
    # ADK injects this REAL tool when output_schema and tools are combined.
    'set_model_response':'internal_coordination',
}
CATEGORIES = frozenset(TOOLS.values()) | {'other'}
RESULTS = frozenset({'unknown','completed','ok','no_result','empty','selection_needed','proposed',
    'executed','policy_blocked','error','invalid','accepted','explicit_empty','recorded','partial',
    'source_gap','approval_rejected','command_rejected'})
SIDE_EFFECTING = frozenset({'record_case_analysis','record_conversation_target','record_source_requirements',
    'teams_propose_create_chat','teams_propose_send_message','teams_create_chat','teams_send_message',
    'knowledge_select_evidence','procedure_action_catalog','technical_authority_engineer','incident_manager','set_model_response'})


def classify(scope, result):
    """Project ONLY known scalar status/code and known list lengths, not text."""
    if type(result) is not dict:
        return
    error = result.get('error')
    if type(error) is dict:
        code = error.get('errorCode')
        if code == 'authorization_error' and scope.tool in ('teams_create_chat','teams_send_message'):
            scope.result_category, scope.error = 'approval_rejected', ErrorCode.APPROVAL_REJECTED
        elif code in ('validation_error','not_found'):
            scope.result_category = 'invalid'
        else:
            scope.result_category, scope.status, scope.error = 'error', RunStatus.FAILED, ErrorCode.TOOL_ERROR
        return
    outcome = result.get('outcome', result.get('status'))
    if type(outcome) is str and outcome in RESULTS:
        scope.result_category = outcome
        if outcome == 'error':
            scope.status, scope.error = RunStatus.FAILED, ErrorCode.TOOL_ERROR
        elif outcome == 'source_gap':
            scope.error = ErrorCode.SOURCE_GAP
        elif outcome == 'approval_rejected':
            scope.error = ErrorCode.APPROVAL_REJECTED
        elif outcome == 'command_rejected':
            scope.error = ErrorCode.COMMAND_AUTHORITY_REJECTED
    fields = {'knowledge_search':'items','knowledge_select_evidence':'selected',
        'teams_get_messages':'messages','get_resolved_chat_messages':'messages',
        'teams_get_members':'members','teams_list_chats':'chats','procedure_action_catalog':'actions',
        'incident_manager':'evidence'}
    value = result.get(fields.get(scope.tool, ''))
    if type(value) is list:
        scope.result_count = len(value)
        if scope.result_category == 'unknown':
            scope.result_category = 'completed' if value else 'empty'
        if scope.tool == 'knowledge_select_evidence' and not value:
            scope.result_category = 'explicit_empty'
    if scope.tool in ('record_source_requirements','record_conversation_target','record_case_analysis') and scope.result_category == 'unknown':
        scope.result_category = 'recorded'


def start_tool(name, agent=None):
    scope = active_execution()
    owner = agent or (scope.agent if scope else ModelAgent.SYSTEM)
    name = name if name in TOOLS else 'other'
    return Execution('tool', owner, scope.purpose if scope else ModelPurpose.ORCHESTRATION,
        tool=name, category=TOOLS.get(name,'other'), role=scope.role if scope else 'system',
        workload=scope.workload if scope else None)


@contextmanager
def tool_scope(name, agent=None):
    """Direct-call adapter. The caller invokes business exactly once and sets result."""
    scope = guarded(runtime_for_execution(), start_tool, name, agent)
    error = None
    try:
        if scope:
            scope.executed = True
            with attached_scope(scope):
                yield scope
        else:
            yield None
    except BaseException as exc:
        error = exc
        raise
    finally:
        if scope:
            finish_scope(scope, error)


def observe_result(scope, result):
    if scope is not None:
        guarded(scope.runtime, classify, scope, result)
    return result


def approval_activity(requested=False, rejected=False):
    """Safe service lifecycle event; no fake tool or human-wait span."""
    from functools import wraps
    import time
    def decorate(fn):
        @wraps(fn)
        def observed(*args, **kwargs):
            started = time.monotonic()
            result = fn(*args, **kwargs)  # exactly once, same exception behavior
            def emit_event():
                from .turn_trace import current_turn
                turn = current_turn()
                if turn is None:
                    return
                scope = active_execution()
                target = scope.span if scope else turn.model_parent()
                category = 'proposed' if requested else 'accepted' if not rejected and getattr(result,'success',False) else 'policy_blocked'
                target.add_event('approval.requested' if requested else 'approval.completed', attributes={
                    'slopanoc.result_category':category,'slopanoc.duration_ms':max(0,(time.monotonic()-started)*1000)})
            guarded(runtime_for_execution(), emit_event)
            return result
        return observed
    return decorate


def governance_decision(stage, accepted):
    def record():
        from .turn_trace import current_turn
        if current_turn() is None or stage not in ('procedure_action.resolved','command_authority.completed'):
            return
        scope = active_execution()
        target = scope.span if scope else current_turn().model_parent()
        attrs = {'slopanoc.result_category':'accepted' if accepted else 'policy_blocked'}
        if not accepted and stage == 'command_authority.completed':
            attrs['slopanoc.error_code'] = 'COMMAND_AUTHORITY_REJECTED'
        target.add_event(stage, attributes=attrs)
    guarded(runtime_for_execution(), record)
