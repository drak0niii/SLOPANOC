"""M15 policy boundaries and independent durable archive evidence."""
import json
import logging
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import pytest
from backend.observability.privacy_policy import Surface, PRODUCERS, attributes, field_policy
from backend.observability.redaction import DataClassification
from backend.observability.archive import FilesystemArchive, ArchiveError
from backend.observability.archive_metadata import ArchiveMetadata
from backend.observability.policy_models import ArchiveReceipt
from backend.observability.policy_metrics import record
from backend.observability.runtime import Runtime
from backend.observability.logging import ProductionLogging
from backend.tests.test_observability_runtime import config
from backend.tests.test_m15_archive import request, ROOT, TID, MARKERS
from backend.tests._m7_storage import storage

@pytest.mark.parametrize('producer',sorted(PRODUCERS))
@pytest.mark.parametrize('surface',list(Surface))
def test_unknown_prohibited_payload_denied_on_every_surface(producer,surface):
    values=dict(zip(('prompt','response','messages','command','sql','authorization','password','secret','reasoning','url.full'),MARKERS))
    values['unknown_custom_field']='PRIVATE_UNKNOWN'
    assert attributes(values,producer=producer,surface=surface,environment='production')=={}
    assert field_policy('unknown_custom_field').classification==DataClassification.PROHIBITED

def test_partial_retry_expiry_and_reconstruction_do_not_refresh_retention(tmp_path):
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    archive=FilesystemArchive(tmp_path,'test',clock=lambda:now[0],staging_seconds=5)
    whole=request();first,=archive.submit(whole.SerializeToString())
    before=list(tmp_path.rglob('*.manifest.json'))[0].read_bytes()
    child=request();del child.resource_spans[0].scope_spans[0].spans[0]
    now[0]+=timedelta(seconds=3)
    retry,=archive.submit(child.SerializeToString())
    assert retry.state=='COMPLETE' and retry.duplicate and retry.digest==first.digest
    assert list(tmp_path.rglob('*.manifest.json'))[0].read_bytes()==before
    other=FilesystemArchive(tmp_path/'partial','test',clock=lambda:now[0],staging_seconds=5)
    assert other.submit(child.SerializeToString())[0].state=='PARTIAL'
    now[0]+=timedelta(seconds=6)
    other.submit(request('HEALTHY').SerializeToString())
    assert not list((tmp_path/'partial').rglob('*.chunk'))
    assert not list((tmp_path/'partial').rglob('*.manifest.json'))

def test_w3c_external_parent_preserves_the_owned_population_boundary(tmp_path):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from backend.tests.test_m15_archive import principal
    archive=FilesystemArchive(tmp_path,'test')
    value=request();root=value.resource_spans[0].scope_spans[0].spans[0]
    root.parent_span_id=bytes.fromhex('5'*16)
    commit,=archive.submit(value.SerializeToString())
    assert commit.state=='COMPLETE'
    decoded=ExportTraceServiceRequest();decoded.ParseFromString(archive.reconstruct(principal(),TID,commit.digest))
    archived=next(s for r in decoded.resource_spans for scope in r.scope_spans for s in scope.spans if s.span_id.hex()==ROOT)
    assert archived.parent_span_id.hex()=='5'*16

def test_overlapping_production_logging_leases_survive_out_of_order_release():
    original=logging.Logger.callHandlers
    already_owned=ProductionLogging._shared is not None
    first=ProductionLogging(config().model_copy(update={'otel_environment':'production'}))
    second=ProductionLogging(config().model_copy(update={'otel_environment':'production'}))
    try:
        first.close()
        if not already_owned:assert logging.Logger.callHandlers is not original
        assert ProductionLogging._shared is not None
        second.close()
        assert logging.Logger.callHandlers is original
    finally:first.close();second.close()

@pytest.mark.asyncio
async def test_ack_is_not_confirmation_and_unsampled_loss_remains_visible(tmp_path):
    db,_=await storage(tmp_path/'metadata.sqlite')
    now=datetime.now(timezone.utc)
    archive=FilesystemArchive(tmp_path/'archive','development')
    try:
        async with db.sessions() as session,session.begin():
            for tid in (TID,'4'*32):
                session.add(ArchiveReceipt(environment='development',trace_id=tid,trace_class='FAILED',
                    expected_spans=2,population_digest=sha256(','.join(sorted([ROOT,format(3,'016x')])).encode()).hexdigest(),
                    terminal_at=now,population_closed=True,policy_version='archive-v1'))
        metadata=ArchiveMetadata(db.sessions,'development')
        args={'since':now-timedelta(seconds=1),'until':now+timedelta(seconds=1)}
        assert (await metadata.health(**args))['completeness'] is None
        assert (await metadata.health(**args,coverage_confirmed=True))['completeness']==0
        commit,=archive.submit(request(environment='development').SerializeToString())
        assert (await metadata.health(**args,coverage_confirmed=True))['confirmed']==0
        assert await metadata.confirm(archive,TID,commit.digest)
        assert await metadata.confirm(archive,TID,commit.digest)
        health=await metadata.health(**args,coverage_confirmed=True)
        assert (health['eligible'],health['confirmed'],health['completeness'])==(2,1,.5)
        assert (await metadata.health(**args))['completeness'] is None
    finally:await db.close()

def test_production_logging_sanitizes_custom_and_late_handlers_before_emit():
    class Poison:
        def __str__(self):raise AssertionError('unsafe stringify')
        def __repr__(self):raise AssertionError('unsafe repr')
    class Handler(logging.Handler):
        def __init__(self):super().__init__();self.records=[]
        def emit(self,value):self.records.append(dict(value.__dict__))
    owner=ProductionLogging(config().model_copy(update={'otel_environment':'production'}))
    logger=logging.getLogger('google.adk.m15_late');handler=Handler()
    try:
        logger.addHandler(handler)
        logger.error(Poison(),Poison(),extra={'prompt':'PRIVATE_PROMPT'},exc_info=(ValueError,ValueError('PRIVATE_SQL'),None))
        value=handler.records[-1]
        assert value['args']==() and value['exc_info'] is None and 'prompt' not in value
        assert json.loads(value['msg'])['event_name']=='telemetry.legacy_log'
        assert 'PRIVATE_' not in json.dumps(value,default=lambda _: 'fixed')
    finally:logger.removeHandler(handler);owner.close()

def test_policy_metric_exemplars_and_dimensions_are_minimized():
    runtime=Runtime(config())
    try:
        record(runtime,'slopanoc.archive.eligible')
        with pytest.raises(ValueError):record(runtime,'slopanoc.archive.unknown')
        with pytest.raises(ValueError):record(runtime,'slopanoc.archive.eligible',float('nan'))
        instrument=runtime.policy_instruments['slopanoc.archive.eligible']
        instrument.add(1,{'environment':'development','operation':'projection','status':'RUNNING','window':'event','run_id':'PRIVATE_RUN'})
        runtime.meter_provider.force_flush(timeout_millis=1000)
        output=runtime.exporters['metric'].records[-1].to_json()
        assert 'slopanoc.archive.eligible' in output and 'PRIVATE_RUN' not in output
        assert '"run_id"' not in output and '"trace_id"' not in output and '"span_id"' not in output
    finally:runtime.close()

def test_contract_inventory_resource_shapes_and_invalid_sampling_are_closed():
    from backend.observability.privacy_policy import contract_registry
    from backend.observability.resource_policy import project
    from backend.observability.sampling_policy import collector_environment
    inventory=contract_registry()
    assert len(inventory)>500
    assert all(p.producers and p.validator and p.environment_scope for p in inventory.values())
    financial=[p for p in inventory.values() if '.finops.' in p.field]
    assert financial and all(p.classification==DataClassification.RESTRICTED and p.capability=='financial_data' for p in financial)
    assert project({'service.name':'slopanoc','service.version':'PRIVATE_PROMPT','cloud.region':'PRIVATE_TEAMS','slopanoc.release_id':'PRIVATE_SECRET'})=={'service.name':'slopanoc'}
    for invalid in ('invalid',float('nan'),float('inf'),0,-1,101):
        assert collector_environment({'HEALTHY_TRACE_PERCENT':invalid})=={'HEALTHY_TRACE_PERCENT':'100','SLOPANOC_SAMPLING_POLICY_STATE':'DEGRADED_SAFE_FALLBACK'}

def test_staging_capacity_rejection_does_not_allocate_empty_traces(tmp_path):
    archive=FilesystemArchive(tmp_path,'test',max_pending=2)
    child=request();del child.resource_spans[0].scope_spans[0].spans[0]
    for n in (1,2):
        child.resource_spans[0].scope_spans[0].spans[0].trace_id=bytes.fromhex(format(n,'032x'))
        assert archive.submit(child.SerializeToString())[0].state=='PARTIAL'
    child.resource_spans[0].scope_spans[0].spans[0].trace_id=bytes.fromhex(format(3,'032x'))
    with pytest.raises(ArchiveError):archive.submit(child.SerializeToString())
    assert len(list((tmp_path/'.staging'/'test').iterdir()))==2
    assert not (tmp_path/'test'/format(3,'032x')).exists()

def test_durable_diagnostic_deployment_metadata_cannot_bypass_resource_policy():
    from backend.observability.api_models import TechnicalView
    value=TechnicalView(producer_instance_id='00000000-0000-4000-8000-000000000001',state_version=1,
        service_version='PRIVATE_PROMPT',release_id='PRIVATE_SECRET',revision='PRIVATE_COMMAND',region='PRIVATE_TEAMS')
    assert 'PRIVATE_' not in value.model_dump_json()

@pytest.mark.asyncio
async def test_local_confirmation_worker_keeps_missing_receipts_unknown(tmp_path):
    db,_=await storage(tmp_path/'reconciliation.sqlite');now=datetime.now(timezone.utc)
    archive=FilesystemArchive(tmp_path/'archive','development')
    try:
        async with db.sessions() as session,session.begin():
            for tid in (TID,'4'*32):
                session.add(ArchiveReceipt(environment='development',trace_id=tid,trace_class='FAILED',expected_spans=2,
                    population_digest=sha256(','.join(sorted([ROOT,format(3,'016x')])).encode()).hexdigest(),terminal_at=now,population_closed=True,policy_version='archive-v1'))
        archive.submit(request(environment='development').SerializeToString())
        result=await ArchiveMetadata(db.sessions,'development').reconcile(archive)
        assert result=={'scanned':2,'confirmed':1,'pending_or_unknown':1}
    finally:await db.close()

def test_disabled_sdk_production_logging_lease_is_released():
    original=logging.Logger.callHandlers
    runtime=Runtime(config().model_copy(update={'observability_enabled':False,'otel_enabled':False,'otel_environment':'production'}))
    try:
        runtime.start_logging()
        assert ProductionLogging._shared is not None
    finally:runtime.close()
    assert logging.Logger.callHandlers is original

def test_central_retention_inventory_covers_every_actual_table():
    from backend.observability.retention_registry import table_registry
    from backend.observability.retention_worker import financial_tables
    from backend.observability.models import Base
    registry=table_registry()
    assert set(Base.metadata.tables)|set(financial_tables())==set(registry)
    assert all(registry[name].policy=='financial_history' for name in financial_tables())
