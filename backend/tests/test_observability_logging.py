import json
import logging
from datetime import datetime, timezone
import pytest
from opentelemetry.trace import Status, StatusCode, Link
from backend.observability.runtime import Runtime
from backend.observability.tracing import span, SCOPE
from backend.observability.logging import emit, SafeJsonFormatter, ProductionLogging
from backend.observability.schemas import OperationalEvent, LogRecord
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_exporters import SENTINELS


def test_json_correlation_safe_metadata_and_no_trace_outside_span():
    r=Runtime(config())
    try:
        with span(r,OperationalEvent.INITIALIZED):
            record=emit(r,OperationalEvent.INITIALIZED,severity='WARNING',run_id='run-1',
                metadata={'message_count':2,'prompt':SENTINELS[2],'messages':SENTINELS[3]})
            assert record.trace_id and record.span_id
        assert emit(r,OperationalEvent.INITIALIZED).trace_id is None
        assert r.flush(1)
        captured=r.exporters['log'].snapshot()[0].log_record.body
        assert set(captured)>={'timestamp','severity','service','environment','message','event_name',
                              'error_code','trace_id','span_id','run_id'}
        assert captured['metadata']=={'message_count':2}
        assert captured['run_id']=='run-1'
        assert not any(s in json.dumps(captured) for s in SENTINELS)
    finally: r.close()


def test_foreign_adk_spans_and_logs_filtered_before_queue():
    r=Runtime(config())
    try:
        tracer=r.tracer_provider.get_tracer('google.adk')
        with tracer.start_as_current_span('model.request',attributes={'prompt':SENTINELS[2]}):
            r.logger_provider.get_logger('google.adk').emit(body={'prompt':SENTINELS[2]})
        r.flush(1)
        assert not r.exporters['trace'].snapshot()
        assert not r.exporters['log'].snapshot()
        assert r.health.snapshot()['trace']['accepted']==0
        assert r.health.snapshot()['log']['accepted']==0
        assert r.health.snapshot()['trace']['filtered']==1
    finally:r.close()


def test_owned_sdk_bypass_raw_events_links_status_and_log_body_denied():
    r=Runtime(config())
    try:
        with span(r,OperationalEvent.INITIALIZED) as parent:
            with r.tracer.start_as_current_span('telemetry.initialized',
                links=[Link(parent.get_span_context(),attributes={'prompt':SENTINELS[2]})]) as child:
                child.set_attribute('authorization',SENTINELS[4])
                child.set_attribute('slopanoc.stage',SENTINELS[3])
                child.add_event(SENTINELS[2],{'content':SENTINELS[3]})
                child.set_status(Status(StatusCode.ERROR,SENTINELS[0]))
                child.record_exception(RuntimeError(SENTINELS[1]))
        r.logger.emit(body={'prompt':SENTINELS[2]})
        r.logger.emit(body={'event_name':'telemetry.initialized','message':'telemetry.initialized',
            'timestamp':datetime.now(timezone.utc).isoformat(),'service':'slopanoc','environment':'test',
            'severity':'INFO','metadata':{'prompt':SENTINELS[2]}})
        r.flush(1)
        assert not r.exporters['log'].snapshot()
        for sample in r.exporters['trace'].snapshot():
            assert not sample.events and not sample.links and sample.status.description is None
            assert not any(s in str(sample.attributes) for s in SENTINELS)
        assert r.health.snapshot()['log']['accepted']==0
    finally:r.close()


@pytest.mark.parametrize('severity,level',[('DEBUG',10),('INFO',20),('WARNING',30),('ERROR',40),('CRITICAL',50)])
def test_legacy_formatter_never_evaluates_message_or_exception(severity,level):
    class Poison:
        def __str__(self): raise AssertionError('Raw log evaluated')
    formatter=SafeJsonFormatter(config())
    record=logging.LogRecord('backend.fake',level,__file__,1,Poison(),(),None)
    record.exc_info=(RuntimeError,RuntimeError(SENTINELS[1]),None)
    record.prompt=SENTINELS[2]
    value=json.loads(formatter.format(record))
    assert value['severity']==severity
    assert value['message']=='telemetry.legacy_log'
    assert not any(s in json.dumps(value) for s in SENTINELS)


def test_production_handler_formatting_restored_without_duplicate_handlers():
    c=config().model_copy(update={'otel_environment':'production'})
    logger=logging.getLogger('backend.m1_test')
    handler=logging.StreamHandler();old=logging.Formatter('%(message)s')
    handler.setFormatter(old);logger.addHandler(handler)
    before=tuple(logger.handlers)
    owner=ProductionLogging(c)
    try:
        assert tuple(logger.handlers)==before
        assert isinstance(handler.formatter,SafeJsonFormatter)
    finally:
        owner.close();logger.removeHandler(handler)
    assert handler.formatter is old


def test_foreign_metrics_and_sensitive_labels_do_not_export():
    r=Runtime(config())
    try:
        r.meter_provider.get_meter('google.adk').create_counter('foreign').add(1,{'prompt':SENTINELS[2]})
        r.meter_provider.get_meter(SCOPE).create_counter('unregistered').add(1,{'run_id':'run-1'})
        assert r.meter_provider.force_flush(timeout_millis=1000)
        text=r.exporters['metric'].records[-1].to_json()
        assert 'foreign' not in text and 'unregistered' not in text and 'run_id' not in text
        assert not any(s in text for s in SENTINELS)
    finally:r.close()


def test_owned_log_cannot_forge_resource_identity():
    r=Runtime(config())
    try:
        data=LogRecord(timestamp=datetime.now(timezone.utc), severity='INFO',service='FAKE_PROMPT_BODY',
            environment='development',event_name=OperationalEvent.INITIALIZED,
            message=OperationalEvent.INITIALIZED).model_dump(mode='json')
        r.logger.emit(body=data)
        r.flush(1)
        assert not r.exporters['log'].snapshot()
        assert r.health.snapshot()['log']['filtered']==1
    finally:r.close()


def test_reserved_metric_with_unregistered_value_is_filtered():
    from opentelemetry.metrics import Observation
    r=Runtime(config())
    try:
        r.meter_provider.get_meter(SCOPE,'adversarial').create_observable_gauge('slopanoc.telemetry.accepted',
            callbacks=[lambda options:[Observation(1,{'environment':'FAKE_PROMPT_BODY','operation':'trace_export'})]])
        r.meter_provider.force_flush(timeout_millis=1000)
        assert r.health.snapshot()['metric']['filtered']>0
        assert 'FAKE_PROMPT_BODY' not in r.exporters['metric'].records[-1].to_json()
    finally:r.close()


def test_instrumentation_scope_attributes_version_and_tracestate_are_redacted():
    from opentelemetry.trace import SpanContext, TraceFlags, TraceState, NonRecordingSpan, set_span_in_context
    r=Runtime(config())
    try:
        tracer=r.tracer_provider.get_tracer(SCOPE,'FAKE_SECRET_123',schema_url='FAKE_PROMPT_BODY',
            attributes={'prompt':'FAKE_TEAMS_TEXT'})
        parent=SpanContext(trace_id=1,span_id=2,is_remote=True,trace_flags=TraceFlags(1),
            trace_state=TraceState([('vendor','FAKE_SECRET_123')]))
        with tracer.start_as_current_span('telemetry.initialized',context=set_span_in_context(NonRecordingSpan(parent))):pass
        r.flush(1)
        captured=r.exporters['trace'].snapshot()[0]
        assert captured.instrumentation_scope.version=='1'
        assert not captured.instrumentation_scope.attributes
        assert not captured.context.trace_state and not captured.parent.trace_state
        assert 'FAKE' not in str(captured.instrumentation_scope)
    finally:r.close()


def test_metric_description_unit_and_scope_metadata_cannot_carry_content():
    from opentelemetry.metrics import Observation
    r=Runtime(config())
    try:
        meter=r.meter_provider.get_meter(SCOPE,'FAKE_SECRET_123',schema_url='FAKE_PROMPT_BODY',
            attributes={'content':'FAKE_TEAMS_TEXT'})
        meter.create_observable_gauge('slopanoc.telemetry.accepted',unit='FAKE_TEAMS_TEXT',
            description='FAKE_PROMPT_BODY',callbacks=[lambda options:[Observation(1,
                {'environment':'development','operation':'trace_export'})]])
        r.meter_provider.force_flush(timeout_millis=1000)
        text=r.exporters['metric'].records[-1].to_json()
        assert not any(s in text for s in SENTINELS)
        assert 'FAKE' not in text
    finally:r.close()
