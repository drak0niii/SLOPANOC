"""Versioned, surface-specific telemetry policy. Never an authority/evidence input."""
from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from .redaction import DataClassification, COUNT_FIELDS
from .attributes import HIGH_CARDINALITY_FIELDS

VERSION = 'privacy-v1'
ENVIRONMENTS = frozenset({'local', 'development', 'test', 'staging', 'production'})

class Surface(str, Enum):
    TRACE = 'trace'
    EVENT = 'event'
    LOG = 'log'
    METRIC = 'metric'
    DATABASE = 'database'
    API = 'api'
    UI = 'ui'
    ARCHIVE = 'archive'

@dataclass(frozen=True)
class FieldPolicy:
    field: str
    classification: DataClassification
    producers: frozenset[str]
    surfaces: frozenset[Surface]
    validator: str
    capability: str | None = None
    environment_scope: str = 'verified_server_environment'

PRODUCERS = frozenset({'turn','model','agent','tool','dependency','knowledge','reliability',
    'diagnostic','slo','accounting','billing','reconciliation','unit_economics','control'})
CORRELATION = frozenset(Surface) - {Surface.METRIC}
REGISTRY = {key: FieldPolicy(key, DataClassification.SAFE, PRODUCERS, frozenset(Surface),
    'nonnegative_int64') for key in COUNT_FIELDS}
REGISTRY.update({key: FieldPolicy(key, DataClassification.RESTRICTED, PRODUCERS,
    CORRELATION, 'typed_server_identifier', 'technical_diagnostics')
    for key in HIGH_CARDINALITY_FIELDS if key not in {'user_id','teams_chat_id','chat_id'}})

# These entries reuse the actual finite typed producer validators. Declaring an
# attribute in the general vocabulary alone does not enable it on a sink.
from .dependency_contract import ENUM_FIELDS as DEPENDENCY_ENUMS, INT_FIELDS, FLOAT_FIELDS, BOOL_FIELDS
from .reliability_contract import ENUM_FIELDS as RELIABILITY_ENUMS, NUMERIC_FIELDS
TRACE_FIELDS=frozenset(DEPENDENCY_ENUMS)|INT_FIELDS|FLOAT_FIELDS|BOOL_FIELDS|frozenset(RELIABILITY_ENUMS)|NUMERIC_FIELDS|frozenset({
    'slopanoc.stage','slopanoc.error_code','slopanoc.status','slopanoc.turn_id_origin','slopanoc.config_version',
    'slopanoc.agent','slopanoc.model_operation','gen_ai.provider.name','gen_ai.operation.name',
    'slopanoc.workload','slopanoc.usage_availability','slopanoc.ttft_availability','slopanoc.ttft_boundary',
    'error.type','gen_ai.request.model','gen_ai.response.model','slopanoc.model_version',
    'slopanoc.logical_call_id','slopanoc.attempt_id','slopanoc.attempt','slopanoc.retry_count',
    'gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.cache_read.input_tokens',
    'slopanoc.total_tokens','slopanoc.candidate_tokens','slopanoc.thought_tokens','slopanoc.tool_input_tokens',
    'slopanoc.billable_characters','slopanoc.duration_ms','slopanoc.ttft_ms','slopanoc.streaming',
    'gen_ai.response.finish_reasons','slopanoc.tool','slopanoc.tool_category','slopanoc.execution_role',
    'slopanoc.execution_purpose','slopanoc.result_category','slopanoc.execution_disposition',
    'slopanoc.result_count','slopanoc.timeout_observed','http.response.status_code',
    'slopanoc.trace_class','slopanoc.redaction_policy','slopanoc.sampling_policy','slopanoc.archive_policy',
    'slopanoc.archive.expected_span_ids','slopanoc.archive.population_closed',
    'slopanoc.run_id','slopanoc.turn_id','slopanoc.session_id','slopanoc.thread_id','slopanoc.fault_id'})
TRACE_CORRELATION=frozenset({'slopanoc.run_id','slopanoc.turn_id','slopanoc.session_id','slopanoc.thread_id',
    'slopanoc.fault_id','slopanoc.logical_call_id','slopanoc.attempt_id','slopanoc.archive.expected_span_ids'})
for field in TRACE_FIELDS:
    REGISTRY[field]=FieldPolicy(field,DataClassification.RESTRICTED if field in TRACE_CORRELATION else DataClassification.SAFE,
        PRODUCERS,frozenset({Surface.TRACE,Surface.EVENT,Surface.ARCHIVE,Surface.DATABASE}),
        'canonical_typed_span_projection','technical_diagnostics' if field in TRACE_CORRELATION else None)
# Resource fields have a separate canonical semantic projection; declaring one
# here never promotes it to a datapoint label or bypasses ResourceMetadata.
from .resource_policy import PATTERNS as RESOURCE_PATTERNS
for field in RESOURCE_PATTERNS:
    REGISTRY[field]=FieldPolicy(field,DataClassification.SAFE,PRODUCERS,frozenset(Surface),
        'canonical_resource_shape:'+RESOURCE_PATTERNS[field])

# Qualified legacy diagnostic fields describe their actual allowlist projection;
# technical correlation is pseudonymous and excluded from logs/metrics.
from . import diagnostic_privacy as LEGACY_POLICY
for field in LEGACY_POLICY.IDENTITIES | LEGACY_POLICY.IDENTITY_ARRAYS:
    key='legacy_diagnostic.'+field
    REGISTRY[key]=FieldPolicy(key,DataClassification.RESTRICTED,frozenset({'diagnostic'}),
        frozenset({Surface.DATABASE,Surface.API,Surface.UI}),'sha256_pseudonymous_identifier','technical_diagnostics')
for field in LEGACY_POLICY.COUNTS | LEGACY_POLICY.BOOLEANS | LEGACY_POLICY.ENUM_FIELDS | LEGACY_POLICY.CODE_ARRAYS:
    key='legacy_diagnostic.'+field
    if key not in REGISTRY:
        REGISTRY[key]=FieldPolicy(key,DataClassification.SAFE,frozenset({'diagnostic'}),
            frozenset({Surface.DATABASE,Surface.API,Surface.UI,Surface.LOG}),'legacy_finite_code_boolean_or_count')

REGISTRY['subject_digest']=FieldPolicy('subject_digest',DataClassification.RESTRICTED,frozenset({'slo'}),
    frozenset({Surface.DATABASE}),'authenticated_owner_sha256','own_progress')
for field in ('amount','currency','quantity','source_generation','pricing_version','reconciliation_generation',
    'denominator_snapshot','forecast_generation','anomaly_generation'):
    REGISTRY[field]=FieldPolicy(field,DataClassification.RESTRICTED,
        frozenset({'accounting','billing','reconciliation','unit_economics','control'}),
        frozenset({Surface.DATABASE,Surface.API,Surface.UI}),'immutable_financial_contract','financial_data')

def field_policy(field):
    return REGISTRY.get(field, FieldPolicy(field, DataClassification.PROHIBITED,
        frozenset(), frozenset(), 'deny'))

def attributes(values, *, producer, surface, environment):
    """Known typed projections first; unknown producers/environments fail closed.

    Correlation fields are not promoted to metric/log labels. API and database
    contracts keep their own typed DTOs; this function never bypasses their RBAC.
    """
    if producer not in PRODUCERS or environment not in ENVIRONMENTS or not isinstance(values, Mapping):
        return {}
    from .redaction import safe_span_attributes, sanitize_metadata
    if surface in (Surface.METRIC, Surface.LOG, Surface.API, Surface.UI):
        return sanitize_metadata(values)
    if surface not in (Surface.TRACE, Surface.EVENT, Surface.ARCHIVE, Surface.DATABASE):
        return {}
    projected=safe_span_attributes(values)
    return {key:value for key,value in projected.items()
        if producer in field_policy(key).producers and surface in field_policy(key).surfaces}

def exception(error, *, code, subsystem, operation):
    """Never accesses error args, str/repr, traceback, URL or SQL attributes."""
    from .errors import ErrorCode
    from .reliability_contract import Category
    from .tracing import Operation
    try:
        code = ErrorCode(code).value
        subsystem = Category(subsystem).value
        operation = Operation(operation).value
    except (ValueError, TypeError):
        return {}
    family = ('timeout' if isinstance(error, TimeoutError) else
        'connection' if isinstance(error, ConnectionError) else
        'validation' if isinstance(error, (ValueError, TypeError)) else 'other')
    return {'error_code': code, 'exception_family': family,
        'subsystem': subsystem, 'operation': operation, 'status_category': 'failed'}

CONTRACT_MODULES={
    'turn':('backend.observability.schemas',),
    'diagnostic':('backend.observability.projection','backend.observability.api_models','backend.observability.policy_views'),
    'slo':('backend.observability.slo_rollups',),
    'accounting':('backend.observability.finops.contracts','backend.observability.finops.api_models'),
    'billing':('backend.observability.finops.billing_api_models',),
    'reconciliation':('backend.observability.finops.reconciliation_api_models',),
    'unit_economics':('backend.observability.finops.unit_economics_contracts','backend.observability.finops.unit_economics_api_models'),
    'control':('backend.observability.finops.control_api_models',),
}

def contract_registry():
    """Inventory actual strict M0–M14 DTO validators without widening any DTO.

    Names are contract-qualified: an accounting `quantity` has a different
    capability/surface policy from a trace attribute. These entries describe the
    existing server-enforced typed boundaries; they never authorize a business
    operation or promote arbitrary extracted/provider data to safe metadata.
    """
    from importlib import import_module
    from .schemas import Contract
    result={}
    financial={'accounting','billing','reconciliation','unit_economics','control'}
    prohibited={'prompt','response','content','command','command_template','rendered_command','reasoning','sql','url','authorization','password','token','secret'}
    for producer,names in CONTRACT_MODULES.items():
        for name in names:
            module=import_module(name)
            for cls in vars(module).values():
                if not isinstance(cls,type) or not issubclass(cls,Contract) or cls.__module__!=name:continue
                for field in cls.model_fields:
                    key=name+'.'+cls.__name__+'.'+field
                    deny=field in prohibited
                    restricted=producer in financial or field in HIGH_CARDINALITY_FIELDS or field.endswith('_id') or field in {'subject_digest','technical','digest','capabilities','environments'}
                    classification=DataClassification.PROHIBITED if deny else DataClassification.RESTRICTED if restricted else DataClassification.SAFE
                    surfaces=frozenset() if deny else frozenset({Surface.DATABASE,Surface.API,Surface.UI})
                    capability='financial_data' if producer in financial else 'technical_diagnostics' if restricted else None
                    if cls.__name__ in {'Correlation','TraceEvent','SpanRecord'}:surfaces=frozenset({Surface.TRACE,Surface.EVENT,Surface.ARCHIVE,Surface.DATABASE}) if not deny else frozenset()
                    if field=='subject_digest':surfaces=frozenset({Surface.DATABASE})
                    if cls.__name__=='LogRecord':surfaces=frozenset({Surface.LOG}) if not deny else frozenset()
                    result[key]=FieldPolicy(key,classification,frozenset({producer}),surfaces,key,capability)
    return result
