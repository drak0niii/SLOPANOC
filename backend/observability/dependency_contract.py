"""Finite M5 metadata contract. No client, runtime, evidence or authority imports."""
import math

GATEWAY_OPERATIONS = frozenset({'teams.listChats', 'teams.getMessages', 'teams.getMembers',
    'teams.getHostedContent', 'teams.createChat', 'teams.sendMessage'})
DB_OWNERS = frozenset({'session_db', 'case_db', 'attachment_db', 'knowledge_db'})
STORAGE_ROLES = frozenset({'chat_attachments', 'knowledge_artifacts'})
DEPENDENCIES = DB_OWNERS | STORAGE_ROLES | {'power_automate_gateway', 'secret_manager', 'knowledge', 'other'}
DB_OPERATIONS = frozenset({'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'DDL', 'other', 'CONNECT',
    'CHECKOUT', 'COMMIT', 'ROLLBACK'})
KNOWLEDGE_STAGES = frozenset({'metadata', 'applicability', 'sparse', 'dense', 'fusion'})
OPERATIONS = GATEWAY_OPERATIONS | DB_OPERATIONS | KNOWLEDGE_STAGES | {
    'upload', 'download', 'delete', 'exists', 'access_secret_version'}
SPAN_NAMES = frozenset({'http.client', 'db.client', 'db.connection', 'db.transaction',
    'storage.client', 'secretmanager.client', 'knowledge.retrieval'})
ROUTES = {op: 'power_automate.' + op for op in GATEWAY_OPERATIONS}
ENUM_FIELDS = {
    'slopanoc.dependency': DEPENDENCIES,
    'slopanoc.dependency_operation': OPERATIONS,
    'slopanoc.dependency_kind': frozenset({'external', 'local', 'acquisition', 'transaction'}),
    'slopanoc.error_origin': frozenset({'gateway_http', 'gateway_transport', 'database', 'storage', 'secret_manager', 'knowledge', 'unknown'}),
    'slopanoc.failure_kind': frozenset({'auth', 'not_found', 'rate_limit', 'provider', 'connection',
        'connect_timeout', 'read_timeout', 'timeout', 'pool_timeout', 'query', 'persistence', 'invalid_response', 'unknown'}),
    'slopanoc.retry_visibility': frozenset({'observable', 'unknown'}),
    'slopanoc.transaction_outcome': frozenset({'commit', 'rollback', 'failure', 'commit_requested', 'rollback_requested'}),
    'slopanoc.acquisition_boundary': frozenset({'pool_connect_including_pre_ping'}),
    'http.request.method': frozenset({'POST', 'GET', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OTHER'}),
    'http.route': frozenset(ROUTES.values()) | {'unknown'},
    'slopanoc.http_status_class': frozenset({'1xx', '2xx', '3xx', '4xx', '5xx', 'unknown'}),
    'db.system.name': frozenset({'postgresql', 'sqlite', 'other'}),
    'db.operation.name': DB_OPERATIONS,
}
INT_FIELDS = frozenset({'slopanoc.dependency_attempt', 'slopanoc.dependency_retry_count',
    'slopanoc.bytes', 'slopanoc.page_count'})
FLOAT_FIELDS = frozenset({'slopanoc.retry_after_seconds'})
BOOL_FIELDS = frozenset({'slopanoc.rate_limited', 'slopanoc.pagination'})


def project(attributes):
    safe = {}
    for key, allowed in ENUM_FIELDS.items():
        value = attributes.get(key)
        if type(value) is str and value in allowed:
            safe[key] = value
    for key in INT_FIELDS:
        value = attributes.get(key)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            safe[key] = value
    for key in FLOAT_FIELDS:
        value = attributes.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 3600:
            safe[key] = value
    for key in BOOL_FIELDS:
        value = attributes.get(key)
        if type(value) is bool:
            safe[key] = value
    code = attributes.get('http.response.status_code')
    if type(code) is int and 100 <= code <= 599:
        safe['http.response.status_code'] = code
    return safe
