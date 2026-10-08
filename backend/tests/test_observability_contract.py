"""M0 architecture contract tests: offline, no SDK/runtime integration."""
import ast
import importlib
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal
import pytest
from pydantic import ValidationError
from backend.observability import schemas as s
from backend.observability.stages import Stage, RunStatus, TERMINAL_STATUSES, validate_transition, PUBLIC_PROGRESS
from backend.observability.errors import ErrorCode
from backend.observability.attributes import Attribute, RELEASE_ATTRIBUTES

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)

def event(**overrides):
    return dict(event_id='event-1', trace_id='a'*32, span_id='b'*16, run_id='run-1',
                turn_id='turn-1', session_id='session-1', timestamp=NOW,
                stage='request.received', status='RUNNING', **overrides)

def active(**overrides):
    return dict(run_id='run-1', trace_id='a'*32, turn_id='turn-1', session_id='session-1',
                status='RUNNING', current_stage='request.received', started_at=NOW,
                stage_started_at=NOW, last_progress_at=NOW, **overrides)

def test_complete_documented_vocabularies_and_unique_values():
    doc = (ROOT/'docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md').read_text()
    stages = re.findall(r'^([a-z_]+(?:\.[a-z_]+)*)$', doc.split('## Stage vocabulary')[1].split('## Status vocabulary')[0], re.M)
    statuses = re.findall(r'^[A-Z]+$', doc.split('## Status vocabulary')[1].split('## Trace event schema')[0], re.M)
    errors = re.findall(r'^- `([A-Z_]+)`', (ROOT/'docs/Telemetry/04_RELIABILITY_TIMEOUTS_ERRORS.md').read_text().split('## Error taxonomy')[1].split('## Retry policy')[0], re.M)
    for enum, expected in ((Stage, stages), (RunStatus, statuses), (ErrorCode, errors)):
        assert {v.value for v in enum} == set(expected)
        assert len(enum.__members__) == len(expected) == len(set(expected))

@pytest.mark.parametrize('patch', [dict(stage='imaginary'), dict(status='running'),
    dict(error_code='raw_exception'), dict(duration_ms=-1), dict(duration_ms=float('nan')),
    dict(timestamp=datetime(2026,10,7)), dict(trace_id='0'*32), dict(span_id='0'*16),
    dict(telemetry_schema_version=2), dict(prompt='private'),
    dict(stage='turn.completed',status='RUNNING'),dict(stage='run.stalled',status='FAILED')])
def test_event_rejects_invalid_contract(patch):
    with pytest.raises(ValidationError):
        s.TraceEvent(**(event() | patch))

def test_event_span_and_log_shapes():
    value = s.TraceEvent(**event(metadata={'message_count': 4}))
    assert value.model_dump(mode='json')['timestamp'].endswith('Z')
    s.SpanRecord(**event(), operation='slopanoc.turn', started_at=NOW, completed_at=NOW)
    with pytest.raises(ValidationError):
        s.SpanRecord(**event(), operation='slopanoc.turn', started_at=NOW, completed_at=NOW-timedelta(seconds=1))
    s.LogRecord(timestamp=NOW, severity='INFO', service='slopanoc', environment='test',
                event_name=Stage.REQUEST_RECEIVED, message=Stage.REQUEST_RECEIVED)
    with pytest.raises(ValidationError):
        s.LogRecord(timestamp=NOW, severity='INFO', service='slopanoc', environment='test',
                    event_name=Stage.REQUEST_RECEIVED, message='Bearer secret')

def test_terminal_policy_and_active_state():
    for old in RunStatus:
        for new in RunStatus:
            permitted = (old not in TERMINAL_STATUSES and new in TERMINAL_STATUSES) or (old,new) in {
                (RunStatus.PENDING,RunStatus.RUNNING), (RunStatus.RUNNING,RunStatus.STALLED), (RunStatus.STALLED,RunStatus.RUNNING)}
            if permitted:
                validate_transition(old,new)
            else:
                with pytest.raises(ValueError): validate_transition(old,new)
    s.ActiveRun(**active())
    for status in TERMINAL_STATUSES:
        with pytest.raises(ValidationError): s.ActiveRun(**(active() | {'status':status}))
        s.ActiveRun(**(active() | {'status':status,'terminal_at':NOW}))
    with pytest.raises(ValidationError): s.ActiveRun(**active(terminal_at=NOW))
    with pytest.raises(ValidationError): s.ActiveRun(**(active() | {'stage_started_at':NOW-timedelta(seconds=1)}))

def test_usage_financial_semantics():
    data = dict(trace_id='a'*32, span_id='b'*16, run_id='run-1', turn_id='turn-1',
        session_id='session-1', usage_event_id='usage-1',call_id='call-1',attempt=0,
        timestamp=NOW,provider='gcp',service='vertex',model='gemini',operation='generate',
        agent='team_manager',unit='tokens',usage_estimated=False,native_currency='USD',environment='test')
    value = s.UsageEvent(**data)
    assert value.input_tokens is None and value.estimated_cost is None and value.billed_cost is None
    assert value.currency == 'EUR' and not s.LEDGER_SAMPLED
    estimate = s.UsageEvent(**data, estimated_cost=Decimal('0.001'), price_version='v1')
    assert estimate.estimated_cost == Decimal('0.001') and estimate.billed_cost is None
    for patch in [dict(estimated_cost='0.1'),dict(billed_cost='-0.1'),dict(event_kind='reversal'),
                  dict(currency='USD'),dict(cost_schema_version=2),dict(input_tokens=True),dict(prompt='secret')]:
        with pytest.raises(ValidationError): s.UsageEvent(**(data | patch))
    adjustment=s.UsageEvent(**(data | dict(usage_event_id='usage-2',event_kind='reversal',
        original_usage_event_id='usage-1',correction_reason='late_usage',correction_actor='reconciler',billed_cost='-0.1')))
    assert adjustment.billed_cost == Decimal('-0.1')
    assert (s.TELEMETRY_SCHEMA_VERSION,s.COST_SCHEMA_VERSION,s.PRICING_SCHEMA_VERSION)==(1,1,1)
    assert s.BILLING_PRIMARY_SOURCES==('gcp_detailed_billing_export','gcp_pricing_export')
    assert s.BILLING_SUPPLEMENTARY_SOURCE=='focus'

def test_release_attributes_and_resource_scope():
    doc=(ROOT/'docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md').read_text()
    documented=set(re.findall(r'- `(slopanoc\.[a-z_]+|service.version)`',doc.split('## Release/configuration correlation attributes')[1].split('## M0 code ownership')[0]))
    assert documented <= {a.value for a in RELEASE_ATTRIBUTES}
    assert {Attribute.ENVIRONMENT,Attribute.REGION,Attribute.MODEL} <= RELEASE_ATTRIBUTES
    assert len(Attribute.__members__)==len(Attribute)
    s.ResourceMetadata(attributes={Attribute.SERVICE_NAME:'slopanoc'})
    with pytest.raises(ValidationError): s.ResourceMetadata(attributes={Attribute.RUN_ID:'run-1'})

def test_settings_and_rbac_contract():
    assert list(s.SETTINGS_SECTION_NAMES.values())==['Overview','Tracing','Reliability','SLOs','FinOps','Data & Retention','Integrations','Access','Diagnostics']
    assert len(s.SettingsSection)==9 and s.SETTINGS_CATEGORY_NAME=='Observability & FinOps'
    assert {role.value for role in s.Role}=={'User','Operator','Developer / SRE','FinOps','Admin','Auditor'}
    assert s.ROLE_PERMISSIONS[s.Role.FINOPS]==frozenset({s.Permission.FINANCIAL_DATA})
    for owner in s.ConfigurationOwner:
        value=s.EffectiveConfiguration(setting='otel_enabled',value=False,owner=owner,config_version='v1')
        assert value.read_only
        with pytest.raises(ValidationError): s.EffectiveConfiguration(setting='otel_enabled',value=False,owner=owner,config_version='v1',read_only=False)
    with pytest.raises(ValidationError): s.EffectiveConfiguration(setting='authorization',value=False,owner='environment_policy',config_version='v1')
    for stage,(code,_) in PUBLIC_PROGRESS.items():
        assert stage in Stage
        assert code in {v.value for v in importlib.import_module('backend.api.streaming_events').Stage}

def test_package_fresh_import_has_no_runtime_or_network_side_effects():
    code='''
import importlib, logging, socket, sys
before=list(logging.getLogger().handlers)
modules_before=set(sys.modules)
def denied(*args, **kwargs): raise AssertionError('Network on contract import')
socket.socket=denied
for name in ('stages','errors','attributes','redaction','config','schemas'):
    importlib.import_module('backend.observability.'+name)
assert before==list(logging.getLogger().handlers)
assert not any(name.startswith(('backend.api','backend.agents','backend.tools','sqlalchemy','google','opentelemetry')) for name in set(sys.modules)-modules_before)
print('INERT_IMPORT_OK')
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'INERT_IMPORT_OK' in result.stdout
    for path in (ROOT/'backend/observability').glob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert not (node.module or '').startswith(('backend.api','backend.agents','backend.tools','backend.config'))
